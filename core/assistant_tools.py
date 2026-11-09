# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""The only data path an analysis agent gets: read-only tools over one
frozen snapshot. Every number in a tool reply was computed locally; every
evidence id was issued here. Anything outside the captured signal keys and
time window is refused, and the measurement's file name or path never
appears in a reply.
"""
from __future__ import annotations

from dataclasses import replace
import json
import math

import numpy as np

from core.assistant_analysis import AnalysisContext, Evidence, analyze
from core.project_agent import KINDS, MAX_LOOKUPS, BoundProject, ReferenceEvidence, lookup

#: Hard ceiling on tool calls per snapshot, against a runaway agent.
MAX_CALLS = 120
#: One reply must stay well under the protocol message limit.
MAX_OUTPUT_CHARS = 200_000
#: Raw samples handed out per osvanta_read_samples call.
MAX_SAMPLES = 200

MAX_TARGET_EVENTS = 8
EVENT_ROLES = ('target', 'command', 'feedback', 'status')

_SPEC_COMMON = {'type': 'function'}


def _schema(properties, required):
    return {'type': 'object', 'properties': properties,
            'required': required, 'additionalProperties': False}


class AnalysisTools:
    MAX_CALLS = MAX_CALLS

    def __init__(self, snapshot, project: BoundProject | None = None):
        self._snapshot = snapshot
        self._project = project
        self._lookups = 0
        self._references: dict[str, ReferenceEvidence] = {}
        self._calls = 0
        self._evidence: dict[str, Evidence] = {}
        self._evidence_by_mark: dict[tuple, str] = {}

    @property
    def snapshot(self):
        return self._snapshot

    @property
    def context(self) -> AnalysisContext:
        return self._snapshot.context

    @property
    def project_context(self) -> dict | None:
        return self._project.model_context() if self._project is not None else None

    # ── Tool surface ────────────────────────────────────────────────────

    def specs(self) -> list[dict]:
        specs = [
            {**_SPEC_COMMON, 'name': 'osvanta_list_signals',
             'description': '列出本次分析范围内已捕获的信号（名称、单位、可用状态、'
                            '样本数）。不能列出范围之外的信号。',
             'inputSchema': _schema(
                 {'query': {'type': 'string', 'description': '可选，按名称或键过滤'}},
                 [])},
            {**_SPEC_COMMON, 'name': 'osvanta_analyze',
             'description': '对范围内信号做本地确定性分析：样本统计、最大单步变化、'
                            '恒定区间、采样间隔异常。返回发现与证据 id。数值均由本地'
                            '程序计算。',
             'inputSchema': _schema(
                 {'keys': {'type': 'array', 'items': {'type': 'string'},
                           'description': '可选，默认全部范围内信号'},
                  'start': {'type': 'number', 'description': '可选，窗口起点（秒）'},
                  'end': {'type': 'number', 'description': '可选，窗口终点（秒）'}},
                 [])},
            {**_SPEC_COMMON, 'name': 'osvanta_read_samples',
             'description': '读取范围内某信号的时间/数值样本（原始数据，最多 '
                            f'{MAX_SAMPLES} 点）。非有限值会被跳过并计数。',
             'inputSchema': _schema(
                 {'key': {'type': 'string'},
                  'start': {'type': 'number', 'description': '可选，窗口起点（秒）'},
                  'end': {'type': 'number', 'description': '可选，窗口终点（秒）'},
                  'limit': {'type': 'integer', 'minimum': 1,
                            'maximum': MAX_SAMPLES}},
                 ['key'])},
            {**_SPEC_COMMON, 'name': 'osvanta_compare_events',
             'description': '按目标变化分段，对照指令、反馈和状态的首个实测变化及延迟。'
                            '仅使用快照内样本，不插值；无基准、无覆盖、未见变化分别报告。',
             'inputSchema': _schema(
                 {**{role: {'type': 'string', 'description': '捕获范围内的信号键'}
                     for role in EVENT_ROLES},
                  'start': {'type': 'number', 'description': '可选，窗口起点（秒）'},
                  'end': {'type': 'number', 'description': '可选，窗口终点（秒）'},
                  **{f'{role}_min_step': {
                      'type': 'number', 'minimum': 0,
                      'description': '可选，以该信号自身单位指定最小变化量；默认任意非零变化',
                  } for role in EVENT_ROLES if role != 'status'}},
                 ['target', 'feedback'])},
        ]
        if self._project is not None:
            specs.append({
                **_SPEC_COMMON, 'name': 'osvanta_lookup_reference',
                'description': '在当前项目 Agent 绑定的资料中查找信号、C 标识符或需求关键词。'
                               '返回短命中和可引用出处。版本与项目级资料的适用范围分别标注。',
                'inputSchema': _schema({
                    'kind': {'type': 'string', 'enum': list(KINDS)},
                    'query': {'type': 'string', 'minLength': 2, 'maxLength': 80},
                }, ['kind', 'query']),
            })
        return specs

    def call(self, name: str, arguments) -> dict:
        if name == 'osvanta_lookup_reference':
            return self._lookup_reference(arguments)
        if name not in ('osvanta_list_signals', 'osvanta_analyze',
                        'osvanta_read_samples', 'osvanta_compare_events'):
            raise ValueError(f'未知工具：{name}')
        self._calls += 1
        if self._calls > MAX_CALLS:
            raise ValueError('工具调用次数超过上限，请基于已有结果作答。')
        args = self._arguments(name, arguments)
        evidence = None
        if name == 'osvanta_list_signals':
            result = self._list(args)
        elif name == 'osvanta_analyze':
            result, evidence = self._analyze(args)
        elif name == 'osvanta_compare_events':
            result, evidence = self._compare_events(args)
        else:
            result = self._read_samples(args)
        staged = {}
        if evidence is not None:
            result['evidence'] = []
            for ev, owner in evidence:
                mark = (ev.key, ev.t0, ev.t1, ev.text)
                issued = staged.get(mark)
                if issued is None:
                    evidence_id = self._evidence_by_mark.get(mark)
                    if evidence_id is not None:
                        issued = self._evidence[evidence_id]
                    else:
                        evidence_id = f'ev-{len(self._evidence) + len(staged) + 1}'
                        issued = Evidence(ev.key, ev.t0, ev.t1, ev.text,
                                          ev.source_id, ev.snapshot_id, evidence_id)
                        staged[mark] = issued
                if owner is not None:
                    owner['evidence_id'] = issued.evidence_id
                result['evidence'].append({
                    'id': issued.evidence_id, 'key': issued.key,
                    'name': issued.key.rsplit('::', 1)[-1],
                    't0': issued.t0, 't1': issued.t1, 'text': issued.text,
                })
        encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
        if len(encoded) > MAX_OUTPUT_CHARS:
            raise ValueError('工具输出超过上限；请缩小时间范围或样本数。')
        for mark, issued in staged.items():
            self._evidence[issued.evidence_id] = issued
            self._evidence_by_mark[mark] = issued.evidence_id
        return result

    def _lookup_reference(self, arguments) -> dict:
        if self._project is None:
            raise ValueError('本轮未授权项目资料。')
        if not isinstance(arguments, dict) or set(arguments) != {'kind', 'query'}:
            raise ValueError('资料查询只接受类型和关键词。')
        self._lookups += 1
        if self._lookups > MAX_LOOKUPS:
            raise ValueError('资料查询次数超过本轮上限。')
        hits, unavailable = lookup(self._project, arguments['kind'], arguments['query'])
        rows = [replace(hit, evidence_id=f'src-{len(self._references) + i}')
                for i, hit in enumerate(hits, 1)]
        result = {
            'scope': self.project_context,
            'matches': [{'id': row.evidence_id, 'citation': row.citation,
                         'excerpt': row.excerpt} for row in rows],
            'unavailable_sources': unavailable,
        }
        if len(json.dumps(result, ensure_ascii=False, allow_nan=False)) > MAX_OUTPUT_CHARS:
            raise ValueError('资料查询结果超过本轮上限。')
        self._references.update((row.evidence_id, row) for row in rows)
        return result

    def resolve_evidence(self, ids) -> tuple[Evidence | ReferenceEvidence, ...]:
        if not isinstance(ids, list) or len(ids) > 100 \
                or any(not isinstance(item, str) for item in ids):
            raise ValueError('证据引用格式无效。')
        unknown = [item for item in ids
                   if item not in self._evidence and item not in self._references]
        if unknown:
            raise ValueError(f'未发行过的证据 id：{unknown[0]!r}。'
                             '只能引用本会话工具返回的证据。')
        return tuple(self._evidence[item] if item in self._evidence else self._references[item]
                     for item in ids)

    # ── Validation ──────────────────────────────────────────────────────

    @staticmethod
    def _arguments(name: str, arguments) -> dict:
        if not isinstance(arguments, dict):
            raise ValueError('工具参数必须是对象。')
        step_names = {'target_min_step', 'command_min_step', 'feedback_min_step'}
        allowed = {'osvanta_list_signals': {'query'},
                   'osvanta_analyze': {'keys', 'start', 'end'},
                   'osvanta_read_samples': {'key', 'start', 'end', 'limit'},
                   'osvanta_compare_events': set(EVENT_ROLES) | step_names | {'start', 'end'}}[name]
        required = {'osvanta_read_samples': {'key'},
                    'osvanta_compare_events': {'target', 'feedback'}}.get(name, set())
        missing = required - arguments.keys()
        if missing:
            raise ValueError(f'缺少必填参数：{sorted(missing)[0]}。')
        unknown = set(arguments) - allowed
        if unknown:
            raise ValueError(f'不接受参数：{sorted(unknown)[0]}。')
        checked: dict = {}
        for key, value in arguments.items():
            if key in ('start', 'end') or key in step_names:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError(f'{key} 必须是数字。')
                try:
                    value = float(value)
                except OverflowError as exc:
                    raise ValueError(f'{key} 不是有限数值。') from exc
                if not math.isfinite(value) or (key in step_names and value < 0):
                    raise ValueError(f'{key} 超出数值范围。')
            elif key == 'query':
                if not isinstance(value, str) or len(value) > 200:
                    raise ValueError('query 必须是不超过 200 字的字符串。')
            elif key == 'key' or key in EVENT_ROLES:
                if not isinstance(value, str) or not value:
                    raise ValueError(f'{key} 必须是非空信号键。')
            elif key == 'limit':
                if isinstance(value, bool) or not isinstance(value, int) \
                        or not 1 <= value <= MAX_SAMPLES:
                    raise ValueError(f'limit 必须是 1..{MAX_SAMPLES} 的整数。')
            elif key == 'keys':
                if (not isinstance(value, list) or len(value) > 12
                        or any(not isinstance(item, str) or not item
                               for item in value)):
                    raise ValueError('keys 必须是不超过 12 个非空字符串的列表。')
            checked[key] = value
        return checked

    def _window(self, args) -> tuple[float | None, float | None]:
        """Fill omitted endpoints, then reject any out-of-scope interval."""
        ctx = self.context
        lo, hi = self._data_bounds() if ctx.t0 is None or ctx.t1 is None else (None, None)
        lo = ctx.t0 if ctx.t0 is not None else lo
        hi = ctx.t1 if ctx.t1 is not None else hi
        start, end = args.get('start', lo), args.get('end', hi)
        for name, value in (('start', start), ('end', end)):
            if name in args and (lo is None or hi is None or not lo <= value <= hi):
                raise ValueError(f'{name} 超出捕获范围；请重新捕获更大范围。')
        if start is not None and end is not None and start > end:
            raise ValueError('start 不能大于 end。')
        return start, end

    def _data_bounds(self):
        lo = hi = None
        for key in self.context.keys:
            series = self._snapshot.get_series(key)
            if series is None or not len(series.timestamps):
                continue
            ts = series.numpy_timestamps()
            first, last = float(ts[0]), float(ts[-1])
            lo = first if lo is None else min(lo, first)
            hi = last if hi is None else max(hi, last)
        return lo, hi

    # ── Tools ───────────────────────────────────────────────────────────

    def _list(self, args) -> dict:
        query = args.get('query', '').lower()
        signals = []
        for key in self.context.keys:
            name = key.rsplit('::', 1)[-1]
            if query and query not in name.lower() and query not in key.lower():
                continue
            series = self._snapshot.get_series(key)
            status = self._snapshot.statuses.get(key, 'missing')
            if series is None or status == 'missing':
                status = 'missing'
                samples = 0
            else:
                samples = int(len(series.timestamps))
            signals.append({'key': key, 'name': name,
                            'unit': series.unit if series is not None else '',
                            'status': status, 'samples': samples})
        return {'signals': signals,
                'limitations': list(self._snapshot.limitations)}

    def _analyze(self, args) -> dict:
        keys = tuple(args.get('keys') or self.context.keys)
        scope = set(self.context.keys)
        outside = [key for key in keys if key not in scope]
        if outside:
            raise ValueError(f'{outside[0]} 不在本次分析范围内；'
                             '请让用户重新捕获该信号。')
        start, end = self._window(args)
        ctx = AnalysisContext(self.context.measurement, keys, start, end,
                              self.context.range_label,
                              self.context.source_id, self.context.snapshot_id)
        result = analyze(self._snapshot, ctx)
        return ({'findings': [f'{f.kind}：{f.text}' for f in result.findings],
                 'cancelled': result.cancelled},
                [(ev, None) for ev in result.evidence_rows])

    def _event_series(self, key, start, end):
        series = self._snapshot.get_series(key)
        if series is None:
            return None
        ts, vs = series.timestamps, series.values
        valid = np.isfinite(vs)
        if start is not None:
            valid &= ts >= start
        if end is not None:
            valid &= ts <= end
        ts, vs = ts[valid], vs[valid]
        if np.any(ts[1:] < ts[:-1]):
            raise ValueError('信号时间戳未排序，不能计算事件顺序。')
        # At a repeated timestamp the last recorded value is the visible state.
        if len(ts):
            keep = np.r_[ts[1:] != ts[:-1], True]
            ts, vs = ts[keep], vs[keep]
        return ts, vs, series.unit

    def _compare_events(self, args):
        keys = {role: args[role] for role in EVENT_ROLES if role in args}
        if any(key not in self.context.keys for key in keys.values()):
            raise ValueError('事件对照只能使用本次捕获的信号。')
        if len(set(keys.values())) != len(keys):
            raise ValueError('目标、指令、反馈和状态应使用不同信号。')
        start, end = self._window(args)
        series = {role: self._event_series(key, start, end) for role, key in keys.items()}
        events = []
        evidence = []
        result = {
            'window': {'start': start, 'end': end},
            'signals': keys,
            'events': events,
            'missing_roles': [role for role in ('command', 'status') if role not in keys],
            'coverage': {
                role: ('unavailable' if data is None else
                       {'valid_samples': len(data[0]), 'unit': data[2]})
                for role, data in series.items()
            },
        }
        target = series['target']
        if target is None or len(target[0]) < 2:
            result['target_status'] = 'insufficient_samples'
            return result, evidence
        ts, vs, unit = target
        delta = np.diff(vs)
        steps = np.flatnonzero((delta != 0) & (np.abs(delta) >= args.get('target_min_step', 0))) + 1
        result['target_status'] = 'changed' if len(steps) else 'no_change_observed'
        result['truncated'] = len(steps) > MAX_TARGET_EVENTS
        for index, sample in enumerate(steps[:MAX_TARGET_EVENTS]):
            at = float(ts[sample])
            next_at = float(ts[steps[index + 1]]) if index + 1 < len(steps) else None
            target_change = {'at_s': at, 'before': float(vs[sample - 1]),
                             'after': float(vs[sample]), 'unit': unit}
            event = {'target': target_change, 'before_next_target_s': next_at}
            evidence.append((self._event_evidence(
                keys['target'], float(ts[sample - 1]), at,
                f'目标在 {at:.6g} s：{vs[sample - 1]:.6g} → {vs[sample]:.6g} {unit}'),
                target_change))
            for role in ('command', 'feedback', 'status'):
                if role in keys:
                    event[role] = self._compare_role(
                        role, keys[role], series[role], at, next_at,
                        args.get(f'{role}_min_step', 0), evidence)
            events.append(event)
        return result, evidence

    def _event_evidence(self, key, t0, t1, text):
        return Evidence(key, t0, t1, text, self.context.source_id, self.context.snapshot_id)

    def _compare_role(self, role, key, data, at, next_at, minimum, evidence):
        if data is None or not len(data[0]):
            return {'status': 'no_valid_samples'}
        ts, vs, unit = data
        first = int(np.searchsorted(ts, at, side='left'))
        stop = len(ts) if next_at is None else int(np.searchsorted(ts, next_at, side='left'))
        if first == 0:
            return {'status': 'no_pre_event_baseline'}
        baseline = first - 1
        row = {'baseline_at_s': float(ts[baseline]), 'baseline': float(vs[baseline]),
               'unit': unit, 'post_event_samples': stop - first}
        if role == 'status':
            row['state_before_target'] = float(vs[baseline])
        label = {'command': '指令', 'feedback': '反馈', 'status': '状态'}[role]
        if stop <= first:
            row['status'] = 'no_post_event_samples'
            return row
        segment = vs[baseline:stop]
        changes = np.diff(segment)
        observed = np.flatnonzero((changes != 0) & (np.abs(changes) >= minimum))
        if not len(observed):
            last = float(ts[stop - 1])
            row.update(status='no_change_observed', last_sample_at_s=last)
            threshold = f'达到 {minimum:.6g} {unit} 的' if minimum else ''
            note = (f'{label}在 {at:.6g}–{last:.6g} s 有 {stop - first} 个有效样本，'
                    f'未见{threshold}变化；目标变化前为 {vs[baseline]:.6g} {unit}')
            evidence.append((self._event_evidence(key, float(ts[baseline]), last, note), row))
            return row
        index = baseline + int(observed[0]) + 1
        changed_at = float(ts[index])
        delay = changed_at - at
        row.update(status='changed', at_s=changed_at, delay_s=delay,
                   before=float(vs[index - 1]), after=float(vs[index]))
        note = (f'{label}在 {changed_at:.6g} s：{vs[index - 1]:.6g} → {vs[index]:.6g} {unit}'
                f'，与目标变化相隔 {delay:.6g} s（采样时刻）')
        evidence.append((self._event_evidence(key, float(ts[index - 1]), changed_at, note), row))
        return row

    def _read_samples(self, args) -> dict:
        key = args['key']
        series = self._snapshot.get_series(key)
        if series is None or self._snapshot.statuses.get(key, 'missing') == 'missing':
            raise ValueError(f'{key} 不在本次分析范围内；请让用户重新捕获该信号。')
        start, end = self._window(args)
        ts = series.numpy_timestamps()
        vs = series.numpy_values()
        mask = (ts >= (start if start is not None else -math.inf)) \
            & (ts <= (end if end is not None else math.inf))
        finite_idx = np.flatnonzero(mask & np.isfinite(vs))
        skipped = int(np.count_nonzero(mask)) - len(finite_idx)
        limit = args.get('limit', 100)
        taken = finite_idx[:limit]
        samples = [[float(ts[i]), float(vs[i])] for i in taken]
        return {'samples': samples, 'returned': len(samples),
                'truncated': len(finite_idx) > len(taken),
                'skipped_nonfinite': skipped,
                'note': ('枚举/文本标签未包含在快照中；数值为原始编码值。'
                         if series.has_labels else '')}
