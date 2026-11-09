# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Qt-free, local analysis of a bounded, detached measurement snapshot."""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping
from uuid import uuid4
from weakref import WeakKeyDictionary

import numpy as np

MAX_ANALYSIS_KEYS = 12
_SOURCE_IDS: WeakKeyDictionary = WeakKeyDictionary()


@dataclass(frozen=True)
class AnalysisContext:
    measurement: str
    keys: tuple[str, ...]
    t0: float | None
    t1: float | None
    range_label: str
    source_id: str = ''
    snapshot_id: str = ''


@dataclass(frozen=True)
class Evidence:
    key: str
    t0: float
    t1: float
    text: str
    source_id: str = ''
    snapshot_id: str = ''
    evidence_id: str = ''


@dataclass(frozen=True)
class Finding:
    kind: str
    text: str
    evidence: Evidence | None = None


@dataclass(frozen=True)
class AnalysisResult:
    context: AnalysisContext
    findings: tuple[Finding, ...] = ()
    cancelled: bool = False

    @property
    def evidence_rows(self) -> tuple[Evidence, ...]:
        seen = set()
        rows = []
        for finding in self.findings:
            ev = finding.evidence
            if ev is not None:
                mark = (ev.key, ev.t0, ev.t1, ev.text)
                if mark not in seen:
                    seen.add(mark)
                    rows.append(ev)
        return tuple(rows)


@dataclass(frozen=True)
class SnapshotSeries:
    """Only copied data; even re-enabling the numpy write flag is forbidden."""

    key: str
    timestamps: np.ndarray
    values: np.ndarray
    unit: str = ''
    has_labels: bool = False

    def numpy_timestamps(self) -> np.ndarray:
        return self.timestamps

    def numpy_values(self) -> np.ndarray:
        return self.values


@dataclass(frozen=True)
class AnalysisSnapshot:
    context: AnalysisContext
    _series: Mapping[str, SnapshotSeries | None]
    statuses: Mapping[str, str]
    limitations: tuple[str, ...] = ()
    cancelled: bool = False

    def get_series(self, key: str) -> SnapshotSeries | None:
        return self._series.get(key)


def measurement_source_id(store) -> str:
    try:
        if store not in _SOURCE_IDS:
            _SOURCE_IDS[store] = 'source-' + uuid4().hex
        return _SOURCE_IDS[store]
    except TypeError:  # Objects without weak-reference support need no registry.
        return 'source-' + uuid4().hex


def _readonly(values) -> np.ndarray:
    return np.frombuffer(np.asarray(values, dtype=np.float64).tobytes(),
                         dtype=np.float64)


def capture_snapshot(store, context, *, resolver=None, cancel=None) -> AnalysisSnapshot:
    """Copy selected samples only, without retaining the store or resolver."""
    keys = tuple(context.keys)
    limitations = ()
    if len(keys) > MAX_ANALYSIS_KEYS:
        limitations = (f'上下文含 {len(keys)} 条信号，仅捕获原选前 '
                       f'{MAX_ANALYSIS_KEYS} 条；请缩小选择后重新捕获。',)
        keys = keys[:MAX_ANALYSIS_KEYS]
    source_id = measurement_source_id(store)
    captured_id = getattr(context, 'source_id', '')
    if captured_id and captured_id != source_id:
        raise ValueError('采集范围所属的测量已切换，请重新捕获。')
    ctx = AnalysisContext(
        context.measurement, keys, context.t0, context.t1,
        context.range_label, source_id,
        getattr(context, 'snapshot_id', '') or 'snapshot-' + uuid4().hex,
    )
    copied = dict.fromkeys(ctx.keys)
    statuses = dict.fromkeys(ctx.keys, 'not_captured')
    cancelled = False
    for key in ctx.keys:
        if cancel is not None and cancel():
            cancelled = True
            break
        series = resolver(key) if resolver is not None else (
            store.get_series(key) if store is not None else None)
        if series is None:
            statuses[key] = 'missing'
            continue
        ts, vs = series.numpy_timestamps(), series.numpy_values()
        mask = np.isfinite(ts)
        if ctx.t0 is not None:
            mask &= ts >= ctx.t0
        if ctx.t1 is not None:
            mask &= ts <= ctx.t1
        copied[key] = SnapshotSeries(
            key, _readonly(ts[mask]), _readonly(vs[mask]),
            series.unit or '', bool(series.has_labels),
        )
        statuses[key] = 'available' if mask.any() else 'empty'
    return AnalysisSnapshot(ctx, MappingProxyType(copied), MappingProxyType(statuses),
                            limitations, cancelled)


# ── Deterministic local analysis over a captured snapshot ─────────────────
# The same measured facts as the in-app analyzer, but reading only the
# detached copy — never the live store — so an agent cannot widen its own
# view of the measurement.

def _fmt(value: float) -> str:
    return f'{value:.6g}'


def analyze(snapshot: AnalysisSnapshot, context: AnalysisContext | None = None,
            cancel=None) -> AnalysisResult:
    """Read-only analysis of *context* (default: the snapshot's own context)
    over the snapshot's frozen samples. ``cancel`` is polled between signals."""
    ctx = context if context is not None else snapshot.context
    findings: list[Finding] = []
    if not ctx.keys:
        findings.append(Finding(
            '缺失', '快照未包含任何信号；请重新捕获上下文。'))
        return AnalysisResult(ctx, tuple(findings))
    cancelled = False
    for key in ctx.keys:
        if cancel is not None and cancel():
            cancelled = True
            break
        findings.extend(_analyze_key(snapshot, key, ctx))
    return AnalysisResult(ctx, tuple(findings), cancelled=cancelled)


def _analyze_key(snapshot: AnalysisSnapshot, key: str,
                 ctx: AnalysisContext) -> list[Finding]:
    name = key.rsplit('::', 1)[-1]
    status = snapshot.statuses.get(key, 'missing')
    series = snapshot.get_series(key)
    if series is None or status == 'missing':
        return [Finding('缺失',
                        f'{key} 不在本次捕获的信号中（可能来自其他日志或已删除）。')]
    ts, vs = series.numpy_timestamps(), series.numpy_values()
    if not len(ts) or status == 'empty':
        return [Finding('缺失', f'{name} 在捕获区间内无采样。')]
    t0, t1 = ctx.t0, ctx.t1
    if t0 is None or t1 is None:
        t0, t1 = float(ts[0]), float(ts[-1])
    window = (ts >= t0) & (ts <= t1) & np.isfinite(vs)
    if not window.any():
        return [Finding('缺失', f'{name} 在 {_fmt(t0)}–{_fmt(t1)} s 内无有效数值采样。',
                        Evidence(key, t0, t1, f'{name} 无采样区间',
                                 ctx.source_id, ctx.snapshot_id))]
    tw, vw = ts[window], vs[window]
    unit = series.unit or ''
    span_ev = Evidence(key, float(tw[0]), float(tw[-1]), f'{name} 统计区间',
                       ctx.source_id, ctx.snapshot_id)
    minimum = float(np.min(vw)); maximum = float(np.max(vw))
    mean = float(np.mean(vw)); std = float(np.std(vw))
    findings = [Finding(
        '现象',
        f'{name}：{len(vw)} 个有效样本，最小 {minimum:.6g} ~ 最大 {maximum:.6g} '
        f'{unit}，均值 {mean:.6g}，标准差 {std:.6g}。',
        span_ev)]
    if len(vw) >= 2 and std == 0.0:
        findings[0] = Finding(
            '现象',
            f'{name}：区间内恒定于 {mean:.6g} {unit}（{len(vw)} 个样本）。',
            span_ev)
    elif len(vw) >= 2:
        deltas = np.diff(vw)
        idx = int(np.argmax(np.abs(deltas)))
        step = float(deltas[idx])
        if step != 0.0:
            t_before, t_after = float(tw[idx]), float(tw[idx + 1])
            pad = (t_after - t_before) or max((t1 - t0) * 0.01, 1e-3)
            findings.append(Finding(
                '现象',
                f'{name}：最大单步变化 {step:+.6g} {unit}'
                f'（{float(vw[idx]):.6g} → {float(vw[idx + 1]):.6g}）'
                f'@ {t_after:.6g} s。',
                Evidence(key, max(t0, t_before - pad), min(t1, t_after + pad),
                         f'{name} 单步变化', ctx.source_id, ctx.snapshot_id)))
    if len(tw) >= 3:
        dts = np.diff(tw)
        if (dts > 0).all():
            median = float(np.median(dts))
            largest = float(dts.max())
            if median > 0.0 and largest > 3.0 * median:
                gi = int(np.argmax(dts))
                findings.append(Finding(
                    '现象',
                    f'{name}：采样间隔异常，最大 {largest:.6g} s'
                    f'（约 {largest / median:.1f}× 中位 {median:.6g} s）'
                    f'@ {float(tw[gi + 1]):.6g} s。',
                    Evidence(key, float(tw[gi]), float(tw[gi + 1]),
                             f'{name} 采样间隔', ctx.source_id, ctx.snapshot_id)))
    return findings
