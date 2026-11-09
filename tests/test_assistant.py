# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""Tests for the analysis assistant (gui/assistant.py) and its wiring into
MainWindow: the immutable context snapshot, the read-only local analyzer,
the dock panel's interactions, and evidence navigation back to the plot.

The first version is deliberately local-only: no model is contacted and no
data leaves the machine, and the panel says so.
"""
from __future__ import annotations

import dataclasses

import numpy as np
import pytest

ENGS_KEY = 'CH1::EngSpeedMsg::EngSpeed'
OIL_KEY = 'CH1::OilPressureMsg::OilPressure'
GAP_KEY = 'CH1::GapSigMsg::GapSig'
MISSING_KEY = 'CH1::NopeMsg::Nope'


def _store_with(signals: dict[str, tuple[np.ndarray, np.ndarray]]):
    from core.signal_store import SignalStore

    store = SignalStore()
    for i, (name, (ts, vals)) in enumerate(signals.items()):
        store.add_series_bulk(
            channel=1,
            message_name=f'{name}Msg',
            message_id=0x400 + i,
            signal_name=name,
            unit='rpm' if name == 'EngSpeed' else 'bar',
            timestamps=np.asarray(ts, dtype=np.float64),
            values=np.asarray(vals, dtype=np.float64),
            raw_values=[],
            has_labels=False,
        )
    return store


def _step_store():
    # 0.1 s cadence, one +630 step at t=0.5.
    ts = np.arange(11) * 0.1
    vals = np.array([320.0] * 5 + [950.0] * 6)
    return _store_with({
        'EngSpeed': (ts, vals),
        'OilPressure': (ts, np.full(11, 3.0)),
    })


def _full_context(keys, t0=None, t1=None, label='全程'):
    from gui.assistant import AnalysisContext
    return AnalysisContext(measurement='m.blf', keys=tuple(keys),
                           t0=t0, t1=t1, range_label=label)


def _texts(result):
    return [f.text for f in result.findings]


# ── Context snapshot ───────────────────────────────────────────────────────

def test_context_is_immutable():
    from gui.assistant import AnalysisContext

    ctx = AnalysisContext(measurement='m', keys=('a',), t0=0.0, t1=1.0,
                          range_label='全程')
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.t0 = 5.0


def test_evidence_is_immutable():
    from gui.assistant import Evidence

    ev = Evidence(key='k', t0=0.0, t1=1.0, text='t')
    with pytest.raises(dataclasses.FrozenInstanceError):
        ev.key = 'other'


# ── Local analyzer (pure, no Qt) ───────────────────────────────────────────

def test_analyze_reports_stats_and_largest_step():
    from gui.assistant import analyze

    result = analyze(_step_store(), _full_context([ENGS_KEY, OIL_KEY]))
    texts = _texts(result)
    assert any('EngSpeed' in t and '最大单步' in t and '630' in t
               for t in texts), texts
    assert any('最小' in t and '320' in t and '950' in t for t in texts), texts
    assert any('OilPressure' in t and '恒定' in t and '3' in t
               for t in texts), texts
    evs = result.evidence_rows
    assert evs, 'expected evidence rows'
    step_ev = [e for e in evs if e.key == ENGS_KEY]
    assert step_ev


def test_analyze_step_evidence_points_at_the_step_time():
    from gui.assistant import analyze

    result = analyze(_step_store(), _full_context([ENGS_KEY]))
    step = [e for e in result.evidence_rows
            if '单步' in e.text or 'EngSpeed' in e.text]
    assert step
    # Some evidence covers the jump at t=0.5 (evidence windows may add pad).
    assert any(e.t0 <= 0.5 <= e.t1 for e in step), [(e.t0, e.t1) for e in step]


def test_analyze_flags_missing_signal():
    from gui.assistant import analyze

    result = analyze(_step_store(), _full_context([MISSING_KEY]))
    assert any(f.kind == '缺失' and MISSING_KEY in f.text
               for f in result.findings), _texts(result)


def test_analyze_empty_keys_gives_guidance():
    from gui.assistant import analyze

    result = analyze(_step_store(), _full_context([]))
    assert any('未选择' in t for t in _texts(result))
    assert not result.evidence_rows


def test_analyze_reports_sampling_gap():
    from gui.assistant import analyze

    ts = np.array([0.0, 0.1, 0.2, 0.3, 1.3, 1.4])
    store = _store_with({'GapSig': (ts, np.arange(6, dtype=float))})
    result = analyze(store, _full_context([GAP_KEY]))
    assert any('采样间隔' in t and 'GapSig' in t for t in _texts(result)), \
        _texts(result)


def test_analyze_window_with_no_samples_is_reported_not_crashed():
    from gui.assistant import analyze

    result = analyze(_step_store(),
                     _full_context([ENGS_KEY], t0=50.0, t1=60.0, label='双光标之间'))
    assert any(f.kind == '缺失' and '无采样' in f.text
               for f in result.findings), _texts(result)


def test_analyze_reports_held_value_when_window_has_no_samples():
    from gui.assistant import analyze

    # The last sample before the window (t=1.0) holds 950 into 50–60 s.
    result = analyze(_step_store(),
                     _full_context([ENGS_KEY], t0=50.0, t1=60.0))
    assert any('950' in t for t in _texts(result)), _texts(result)


def test_analyze_cancel_stops_between_signals():
    from gui.assistant import analyze

    result = analyze(_step_store(), _full_context([ENGS_KEY, OIL_KEY]),
                     cancel=lambda: True)
    assert result.cancelled


def test_analyze_without_store_reports_missing():
    from gui.assistant import analyze

    result = analyze(None, _full_context([ENGS_KEY]))
    assert any(f.kind == '缺失' for f in result.findings)


def test_analyze_caps_signal_count():
    from gui.assistant import analyze, MAX_ANALYSIS_KEYS

    keys = [f'CH1::S{i}Msg::S{i}' for i in range(MAX_ANALYSIS_KEYS + 3)]
    result = analyze(_step_store(), _full_context(keys))
    assert any('仅分析前' in t for t in _texts(result))


# ── Assistant panel (Qt) ───────────────────────────────────────────────────

def test_panel_renders_context_result_and_privacy_note(qapp):
    from gui.assistant import AssistantPanel

    panel = AssistantPanel()
    panel.set_context(_full_context([ENGS_KEY]))
    assert panel.context() is not None
    assert 'm.blf' in panel.context_label.text()

    panel.start_analysis(_step_store(), synchronous=True)
    assert not panel.is_running()
    assert '完成' in panel.status_label.text()
    assert panel.evidence_list.count() >= 1
    assert AssistantPanel.PRIVACY_NOTE in panel.privacy_label.text()
    panel.shutdown()


def test_panel_evidence_activation_emits_evidence(qapp):
    from gui.assistant import AssistantPanel

    panel = AssistantPanel()
    panel.set_context(_full_context([ENGS_KEY]))
    panel.start_analysis(_step_store(), synchronous=True)
    seen = []
    panel.evidenceActivated.connect(seen.append)
    item = panel.evidence_list.item(0)
    panel.evidence_list.itemActivated.emit(item)
    assert seen and seen[0].key
    panel.shutdown()


def test_panel_capture_button_requests_recapture(qapp):
    from gui.assistant import AssistantPanel

    panel = AssistantPanel()
    seen = []
    panel.recaptureRequested.connect(lambda: seen.append(True))
    panel.capture_btn.click()
    assert seen
    panel.shutdown()


def test_panel_run_without_context_gives_hint(qapp):
    from gui.assistant import AssistantPanel

    panel = AssistantPanel()
    panel.run_btn.click()
    assert '捕获' in panel.status_label.text()
    panel.shutdown()


def test_panel_async_analysis_completes(qapp):
    from gui.assistant import AssistantPanel

    panel = AssistantPanel()
    panel.set_context(_full_context([ENGS_KEY]))
    panel.start_analysis(_step_store())
    import time
    deadline = time.monotonic() + 10.0
    while panel.is_running() and time.monotonic() < deadline:
        qapp.processEvents()
    assert not panel.is_running()
    assert '完成' in panel.status_label.text()
    panel.shutdown()


# ── MainWindow wiring ──────────────────────────────────────────────────────

def _make_window(qapp, monkeypatch, tmp_path):
    from gui.main_window import MainWindow

    monkeypatch.setattr(MainWindow, '_settings_folder',
                        staticmethod(lambda: tmp_path))
    return MainWindow('Osvanta Bus Log Analyzer', '00.00.99')


@pytest.fixture()
def window(qapp, monkeypatch, tmp_path):
    w = _make_window(qapp, monkeypatch, tmp_path)
    yield w
    w.deleteLater()
    qapp.processEvents()


def test_main_window_has_hidden_assistant_dock_and_toggle(window):
    assert window.assistant_dock.isHidden()
    assert window._act_assistant.text() == 'Assistant'
    assert window.assistant_dock.windowTitle() == '分析助手'
    window._act_assistant.trigger()
    assert not window.assistant_dock.isHidden()


def test_recapture_builds_context_from_window_state(window):
    window.store = _step_store()
    window.measurement_path = 'some/m.blf'
    window.add_signals_to_plot([ENGS_KEY])

    window._assistant_recapture()
    ctx = window.assistant_panel.context()
    assert ctx is not None
    assert ctx.measurement == 'm.blf'
    assert ENGS_KEY in ctx.keys
    window.assistant_panel.shutdown()


def test_evidence_navigation_zooms_the_plot(window):
    window.store = _step_store()
    window.add_signals_to_plot([ENGS_KEY])

    from core.assistant_analysis import Evidence, measurement_source_id

    window._assistant_locate_evidence(Evidence(
        ENGS_KEY, 0.4, 0.6, '步进', source_id=measurement_source_id(window.store)))
    x0, x1 = window.plot_panel._visible_x_range()
    # Data spans 0..1 s, the evidence window is 0.4–0.6 s with padding:
    # centred on 0.5, clamped to the data bounds.
    assert x0 == pytest.approx(0.0, abs=1e-6)
    assert x1 == pytest.approx(1.0, abs=1e-6)
    window.assistant_panel.shutdown()
