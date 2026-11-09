# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Local data made available to an independent, scope-limited agent."""
from __future__ import annotations

import pytest

from tests.test_assistant import ENGS_KEY, OIL_KEY, _step_store


def _full_context(keys, t0=None, t1=None, **identity):
    from core.assistant_analysis import AnalysisContext

    return AnalysisContext('private-measurement.blf', tuple(keys), t0, t1,
                           '全程', **identity)


def test_snapshot_keeps_samples_after_the_measurement_changes_or_closes():
    from core.assistant_analysis import capture_snapshot

    store = _step_store()
    snapshot = capture_snapshot(store, _full_context([ENGS_KEY], 0.4, 0.55))
    store.get_series(ENGS_KEY).values[4] = 99999
    store.close()

    series = snapshot.get_series(ENGS_KEY)
    assert series.numpy_values().tolist() == [320.0, 950.0]
    assert series.numpy_timestamps().tolist() == pytest.approx([0.4, 0.5])
    assert not series.numpy_values().flags.writeable
    assert snapshot.get_series(OIL_KEY) is None
    assert snapshot.context.snapshot_id
    assert snapshot.context.source_id


def test_snapshot_resolves_only_the_first_twelve_selected_keys_and_reports_limits():
    from core.assistant_analysis import MAX_ANALYSIS_KEYS, capture_snapshot

    class NoMetadata:
        def __getattr__(self, name):
            raise AssertionError(f'Unexpected store access: {name}')

    keys = [f'key-{i}' for i in range(MAX_ANALYSIS_KEYS + 1)]
    calls = []

    def resolve(key):
        calls.append(key)
        return None

    snapshot = capture_snapshot(NoMetadata(), _full_context(keys, 0.0, 1.0),
                                resolver=resolve)
    assert calls == keys[:12]
    assert snapshot.context.keys == tuple(keys[:12])
    assert snapshot.context.t0 == 0.0 and snapshot.context.t1 == 1.0
    assert snapshot.statuses['key-0'] == 'missing'
    assert any('13' in note and '12' in note and '选择' in note
               for note in snapshot.limitations)


# ── Deterministic analyzer on the snapshot ────────────────────────────────

def _snapshot(keys=(ENGS_KEY, OIL_KEY), t0=None, t1=None):
    from core.assistant_analysis import capture_snapshot
    return capture_snapshot(_step_store(), _full_context(list(keys), t0, t1))


def test_analyze_snapshot_reports_step_and_constant():
    from core.assistant_analysis import analyze

    result = analyze(_snapshot())
    texts = [f.text for f in result.findings]
    assert any('最大单步' in t and '630' in t for t in texts), texts
    assert any('恒定' in t and 'OilPressure' in t for t in texts), texts
    assert result.evidence_rows
    assert all(e.snapshot_id == result.context.snapshot_id for e in result.evidence_rows)


def test_analyze_snapshot_marks_missing_and_empty_signals():
    from core.assistant_analysis import analyze

    result = analyze(_snapshot(keys=(ENGS_KEY, 'CH1::Nope::Nope')))
    kinds = {f.text for f in result.findings if 'Nope' in f.text or '不在' in f.text}
    assert any('不在' in t for t in kinds), [f.text for f in result.findings]


# ── AnalysisTools: the only data path an agent gets ───────────────────────

def _tools(keys=(ENGS_KEY, OIL_KEY), t0=None, t1=None):
    from core.assistant_tools import AnalysisTools
    return AnalysisTools(_snapshot(keys, t0, t1))


def test_specs_expose_only_the_approved_read_only_tools():
    specs = _tools().specs()
    assert [s['name'] for s in specs] == ['osvanta_list_signals',
                                          'osvanta_analyze',
                                          'osvanta_read_samples',
                                          'osvanta_compare_events']
    for spec in specs:
        assert spec['type'] == 'function'
        assert spec['description']
        assert spec['inputSchema'].get('additionalProperties') is False


def test_list_signals_never_leaks_the_measurement_name_or_path():
    import json
    result = _tools().call('osvanta_list_signals', {})
    encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
    assert 'EngSpeed' in encoded and 'rpm' in encoded
    assert 'private-measurement' not in encoded
    assert 'blf' not in encoded
    assert result['signals'][0]['status'] == 'available'


def test_list_signals_query_filters_and_rejects_unknown_arguments():
    tools = _tools()
    assert tools.call('osvanta_list_signals', {'query': 'oil'})['signals'][0]['key'] == OIL_KEY
    with pytest.raises(ValueError):
        tools.call('osvanta_list_signals', {'query': 'x', 'extra': 1})
    with pytest.raises(ValueError):
        tools.call('osvanta_list_signals', {'query': 5})


def test_analyze_registers_evidence_and_resolves_only_issued_ids():
    tools = _tools()
    result = tools.call('osvanta_analyze', {})
    assert result['findings'] and result['evidence']
    evidence = result['evidence'][0]
    assert evidence['id'] and evidence['key'] and 't0' in evidence
    resolved = tools.resolve_evidence([evidence['id']])
    assert resolved[0].evidence_id == evidence['id']
    assert resolved[0].snapshot_id == tools.context.snapshot_id
    with pytest.raises(ValueError):
        tools.resolve_evidence(['invented-id'])
    with pytest.raises(ValueError):
        tools.call('osvanta_analyze', {'keys': ['CH1::Nope::Nope']})


def test_analyze_range_must_stay_inside_the_captured_window():
    tools = _tools((ENGS_KEY,), 0.3, 0.7)
    result = tools.call('osvanta_analyze', {'start': 0.35, 'end': 0.65})
    assert result['findings']
    with pytest.raises(ValueError):
        tools.call('osvanta_analyze', {'start': 0.0, 'end': 0.65})


def test_read_samples_is_limited_and_validated():
    tools = _tools((ENGS_KEY,), 0.0, 1.0)
    result = tools.call('osvanta_read_samples',
                        {'key': ENGS_KEY, 'limit': 3})
    assert len(result['samples']) == 3
    assert result['truncated'] is True
    with pytest.raises(ValueError):
        tools.call('osvanta_read_samples', {'key': ENGS_KEY, 'limit': 0})
    with pytest.raises(ValueError):
        tools.call('osvanta_read_samples', {'key': ENGS_KEY, 'limit': 201})
    with pytest.raises(ValueError):
        tools.call('osvanta_read_samples', {'key': 'CH1::Nope::Nope'})
    with pytest.raises(ValueError):
        tools.call('osvanta_read_samples', {'key': ENGS_KEY, 'start': float('nan')})
    with pytest.raises(ValueError):
        tools.call('osvanta_read_samples', {'key': ENGS_KEY, 'start': True})


def test_tool_call_budget_blocks_a_runaway_agent():
    tools = _tools((ENGS_KEY,))
    for _ in range(tools.MAX_CALLS):
        tools.call('osvanta_list_signals', {})
    with pytest.raises(ValueError):
        tools.call('osvanta_list_signals', {})



ACTUATOR_KEYS = {
    role: f'CH1::{name}Msg::{name}'
    for role, name in (
        ('target', 'DoorTarget'), ('command', 'DoorCommand'),
        ('feedback', 'DoorPosition'), ('status', 'DoorFault'),
    )
}


def _actuator_store(*, feedback=True):
    import numpy as np
    from core.signal_store import SignalStore

    store = SignalStore()
    signals = {
        'DoorTarget': ([0, 1, 3], [20, 70, 40], '%'),
        'DoorCommand': ([0.25, 1.25, 2, 3.125], [20, 70, 70, 40], '%'),
        'DoorPosition': ([0.5, 1.5, 2.5, 3.5], [20, 20, 20, 40], '%'),
        'DoorFault': ([0.75, 1.125, 2.75, 3.25], [0, 1, 1, 0], ''),
    }
    if not feedback:
        del signals['DoorPosition']
    for index, (name, (times, values, unit)) in enumerate(signals.items()):
        store.add_series_bulk(
            channel=1, message_name=f'{name}Msg', message_id=0x440 + index,
            signal_name=name, unit=unit, timestamps=np.asarray(times, dtype=float),
            values=np.asarray(values, dtype=float), raw_values=[], has_labels=False,
        )
    return store


def _actuator_tools(*, feedback=True):
    from core.assistant_analysis import capture_snapshot
    from core.assistant_tools import AnalysisTools

    return AnalysisTools(capture_snapshot(
        _actuator_store(feedback=feedback), _full_context(ACTUATOR_KEYS.values(), 0, 4)))


def test_compare_events_distinguishes_following_the_next_target_from_no_change():
    tools = _actuator_tools()
    result = tools.call('osvanta_compare_events', ACTUATOR_KEYS)
    first, second = result['events']
    assert first['target']['at_s'] == 1.0
    assert first['target']['before'] == 20.0 and first['target']['after'] == 70.0
    assert first['command']['delay_s'] == 0.25
    assert first['feedback']['status'] == 'no_change_observed'
    assert first['feedback']['last_sample_at_s'] == 2.5
    assert first['status']['delay_s'] == 0.125
    assert second['feedback']['delay_s'] == 0.5
    ref = tools.resolve_evidence([first['feedback']['evidence_id']])[0]
    assert (ref.key, ref.t0, ref.t1) == (ACTUATOR_KEYS['feedback'], 0.5, 2.5)


def test_compare_events_distinguishes_missing_data_and_rejects_scope_escape():
    tools = _actuator_tools(feedback=False)
    result = tools.call('osvanta_compare_events', ACTUATOR_KEYS)
    assert result['events'][0]['feedback']['status'] == 'no_valid_samples'
    assert 'evidence_id' not in result['events'][0]['feedback']
    with pytest.raises(ValueError, match='捕获'):
        tools.call('osvanta_compare_events', {**ACTUATOR_KEYS, 'feedback': ENGS_KEY})
    with pytest.raises(ValueError, match='捕获范围'):
        tools.call('osvanta_compare_events', {**ACTUATOR_KEYS, 'start': 100})
    with pytest.raises(ValueError, match='捕获范围'):
        tools.call('osvanta_compare_events', {**ACTUATOR_KEYS, 'end': -100})


def test_compare_events_distinguishes_pre_event_and_post_event_coverage():
    tools = _actuator_tools()
    args = {**ACTUATOR_KEYS, 'end': 1.2}
    event, = tools.call('osvanta_compare_events', args)['events']
    assert event['feedback']['status'] == 'no_post_event_samples'
    # Use a signal whose first captured sample comes after the target event.
    from core.assistant_analysis import capture_snapshot
    from core.assistant_tools import AnalysisTools

    store = _actuator_store()
    series = store.get_series(ACTUATOR_KEYS['feedback'])
    series.timestamps[0] = 1.125
    tools = AnalysisTools(capture_snapshot(store, _full_context(ACTUATOR_KEYS.values(), 0, 4)))
    assert tools.call('osvanta_compare_events', ACTUATOR_KEYS)['events'][0][
        'feedback']['status'] == 'no_pre_event_baseline'


def test_single_point_statistics_and_rejected_tool_results():
    tools = _tools((ENGS_KEY,), 0, 1)
    result = tools.call('osvanta_analyze', {'start': 0.49, 'end': 0.51})
    assert '1 个有效样本' in result['findings'][0]
    for args in ({}, {'key': ENGS_KEY, 'start': 10**400}):
        with pytest.raises(ValueError):
            tools.call('osvanta_read_samples', args)


def test_oversized_event_result_does_not_issue_evidence(monkeypatch):
    from core import assistant_tools

    tools = _actuator_tools()
    monkeypatch.setattr(assistant_tools, 'MAX_OUTPUT_CHARS', 10)
    with pytest.raises(ValueError, match='超过上限'):
        tools.call('osvanta_compare_events', ACTUATOR_KEYS)
    with pytest.raises(ValueError, match='未发行'):
        tools.resolve_evidence(['ev-1'])
