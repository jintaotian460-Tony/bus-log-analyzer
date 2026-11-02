import time

import numpy as np
import pytest
from PySide6.QtWidgets import QInputDialog

from core.bus_types import BusType
from core.signal_inheritance import identity, match_signals, recording_family
from core.signal_store import SignalStore
from gui import signal_inheritance
from gui.main_window import MainWindow
from core.calculated_signals import CalculatedSignalDefinition


def store(*specs, value=10):
    result = SignalStore()
    for channel, message, name, unit in specs:
        result.add_series_bulk(channel=channel, message_name=message, message_id=1,
                               signal_name=name, unit=unit,
                               timestamps=np.array([2., 3.]), values=np.array([value, value + 1.]),
                               raw_values=[], has_labels=False)
    return result


def entry_for(source):
    key = source.all_keys()[0]
    return {'identity': identity(key, source.get_series(key))}


@pytest.mark.parametrize('spec,matched,reason', [
    ((1, 'A', 'Speed', 'rpm'), True, ''),
    ((1, 'Renamed', 'Speed', 'rpm'), True, ''),
    ((1, 'A', 'Speed', 'V'), True, ''),
    ((2, 'A', 'Speed', 'rpm'), True, ''),
    (((BusType.LIN, 1), 'A', 'Speed', 'rpm'), True, ''),
    ((None, 'XcpEventChannel_100ms', 'Speed', ''), True, ''),
    ((1, 'A', 'speed', 'rpm'), False, '本日志未匹配'),
    ((1, 'A', 'Speed ', 'rpm'), False, '本日志未匹配'),
    ((1, 'A', ' Speed', 'rpm'), False, '本日志未匹配'),
    ((1, 'A', 'Speed_1', 'rpm'), False, '本日志未匹配'),
    ((1, 'A', 'Other', 'rpm'), False, '本日志未匹配'),
])
def test_matches_require_only_the_strictly_identical_signal_name(spec, matched, reason):
    old = store((1, 'A', 'Speed', 'rpm'))
    new = store(spec)
    key, state = match_signals([entry_for(old)], new)[0]
    assert bool(key) == matched
    assert reason in state


def test_duplicate_names_choose_the_existing_key_or_first_recorded_channel():
    old = store((1, 'Old', 'Speed', 'rpm'))
    new = store((1, 'B', 'Speed', 'rpm'), (1, 'C', 'Speed', 'rpm'))
    assert match_signals([entry_for(old)], new) == [('CH1::B::Speed', '')]
    old = store((1, 'C', 'Speed', 'V'))
    assert match_signals([entry_for(old)], new) == [('CH1::C::Speed', '')]
    target = store((1, 'B', 'Speed', 'rpm'))
    entries = [entry_for(old), entry_for(store((1, 'Other', 'Speed', 'rpm')))]
    assert match_signals(entries, target) == [('CH1::B::Speed', '')] * 2


@pytest.mark.parametrize('old_unit,new_unit', [('', '℃'), ('℃', ''), ('rpm', 'V')])
def test_unit_metadata_never_blocks_an_identical_name(old_unit, new_unit):
    old = store((None, 'Merged', 'Temperature', old_unit))
    new = store((None, 'XcpEventChannel_100ms', 'Temperature', new_unit))
    assert match_signals([entry_for(old)], new) == [
        ('CH?::XcpEventChannel_100ms::Temperature', '')]


def test_even_an_exact_key_cannot_bypass_the_signal_name_check():
    old = store((1, 'A', 'Speed', 'rpm'))
    entry = entry_for(old)
    entry['identity']['name'] = 'speed'
    assert match_signals([entry], old) == [(None, '本日志未匹配')]


def test_missing_legacy_metadata_does_not_block_a_name_match():
    old = store((1, 'A', 'Speed', 'rpm'))
    entry = entry_for(old)
    entry['identity']['unit'] = None
    entry['identity']['source'] = ''
    new = store((2, 'B', 'Speed', 'V'))
    assert match_signals([entry], new) == [('CH2::B::Speed', '')]


@pytest.fixture
def window(qapp, tmp_path):
    win = MainWindow('Osvanta', 'test')
    win._temporary_plot_config_path = tmp_path / 'temporary.json'
    yield win
    win.close()
    qapp.processEvents()


def load_direct(win, data, config=None):
    win._reset_for_new_measurement()
    win._temporary_plot_handoff = config
    if config:
        win._arm_temporary_plot_handoff()
    win._on_worker_finished(data)


@pytest.mark.parametrize('mode', ['stacked', 'multi_axis', 'multistack', 'normal'])
def test_missing_rows_restore_on_later_file_with_new_values_and_styles(window, mode):
    old = store((1, 'A', 'Speed', 'rpm'), (1, 'A', 'OnlySelected', 'V'), value=10)
    load_direct(window, old)
    window.btn_stacked.setChecked(False)
    if mode != 'normal':
        getattr(window, 'btn_' + mode).setChecked(True)
    key = 'CH1::A::Speed'
    selected = 'CH1::A::OnlySelected'
    window.add_signal_to_plot(key)
    item = window.plot_panel._items[key]
    item.color = '#123456'
    item.group = 'Powertrain'
    item.axis_visible = False
    item.own_axis = True
    item.multistack_id = 3
    item.line_style = 'dash'
    window.signal_tree.select_signal_keys([key, selected])
    config = window._capture_temporary_plot_configuration()
    load_direct(window, store((1, 'B', 'Unrelated', 'V')), config)
    ghost = window.plot_panel._items[key]
    assert ghost.unavailable_reason == '本日志未匹配'
    assert len(ghost.series.timestamps) == len(ghost.series.values) == 0
    assert window.store.get_series(key) is None
    assert set(window.signal_tree.selected_signal_keys()) == {key, selected}
    row = window.plot_panel._row_lookup[key]
    window.plot_panel.move_cursor1(3)
    assert window.plot_panel.table.item(row, 2).text() == '本日志未匹配'
    assert window.plot_panel.table.item(row, 1).foreground().color().name() == '#888888'
    config = window._capture_temporary_plot_configuration()
    new = store((1, 'A', 'Speed', 'rpm'), (1, 'A', 'OnlySelected', 'V'), value=90)
    load_direct(window, new, config)
    restored = window.plot_panel._items[key]
    assert not restored.unavailable_reason
    assert restored.series is new.get_series(key)
    assert list(restored.series.values) == [90, 91]
    for field in signal_inheritance.STYLE_FIELDS:
        assert getattr(restored, field) == getattr(ghost, field)
    assert window.plot_panel.plotted_keys() == [key]
    assert set(window.signal_tree.selected_signal_keys()) == {key, selected}


def test_unique_group_rename_preserves_order_and_selection(window):
    old = store((1, 'A', 'Speed', 'rpm'), (1, 'A', 'Voltage', 'V'))
    load_direct(window, old)
    window.add_signals_to_plot(['CH1::A::Voltage', 'CH1::A::Speed'])
    window.signal_tree.select_signal_keys(['CH1::A::Speed'])
    config = window._capture_temporary_plot_configuration()
    new = store((1, 'B', 'Speed', 'rpm'), (1, 'B', 'Voltage', 'V'), value=80)
    load_direct(window, new, config)
    assert window.plot_panel.plotted_keys() == ['CH1::B::Voltage', 'CH1::B::Speed']
    assert window.signal_tree.selected_signal_keys() == ['CH1::B::Speed']
    assert all(p.series is new.get_series(k) for k, p in window.plot_panel._items.items())


def test_selection_only_is_inherited_without_plotting(window):
    load_direct(window, store((1, 'A', 'Speed', 'rpm')))
    window.signal_tree.select_signal_keys(['CH1::A::Speed'])
    config = window._capture_temporary_plot_configuration()
    assert config
    load_direct(window, store((1, 'B', 'Speed', 'rpm')), config)
    assert window.signal_tree.selected_signal_keys() == ['CH1::B::Speed']
    assert window.plot_panel.plotted_keys() == []


def test_manual_confirmation_can_bind_an_explicitly_chosen_different_name(window, monkeypatch):
    load_direct(window, store((1, 'A', 'Speed', 'rpm')))
    key = 'CH1::A::Speed'
    window.add_signal_to_plot(key)
    window.signal_tree.select_signal_keys([key])
    config = window._capture_temporary_plot_configuration()
    new = store((2, 'B', 'RenamedSpeed', 'V'), value=75)
    load_direct(window, new, config)
    assert window.plot_panel._items[key].unavailable_reason == '本日志未匹配'
    monkeypatch.setattr(QInputDialog, 'getItem', lambda *args: (args[3][0], True))
    signal_inheritance.confirm_mapping(window, key)
    target = 'CH2::B::RenamedSpeed'
    assert window.plot_panel.plotted_keys() == [target]
    assert window.signal_tree.selected_signal_keys() == [target]
    assert window.plot_panel._items[target].series is new.get_series(target)
    assert window.signal_tree._unavailable_signals == {}


def test_changed_unit_and_source_uses_new_samples_and_keeps_window_style(window):
    old = store((None, 'Merged', 'CFC_tDVT_FL', ''), value=10)
    load_direct(window, old)
    old_key = old.all_keys()[0]
    window.add_signal_to_plot(old_key)
    window.signal_tree.select_signal_keys([old_key])
    item = window.plot_panel._items[old_key]
    item.color = '#123456'
    item.line_style = 'dash'
    old_window = item.window_id
    config = window._capture_temporary_plot_configuration()
    new = store(((BusType.LIN, 2), 'XcpEventChannel_100ms', 'CFC_tDVT_FL', '℃'), value=75)
    load_direct(window, new, config)
    key = new.all_keys()[0]
    item = window.plot_panel._items[key]
    assert window.plot_panel.plotted_keys() == [key]
    assert item.series is new.get_series(key) and item.series.unit == '℃'
    assert list(item.series.values) == [75, 76]
    assert not item.unavailable_reason
    assert item.color == '#123456' and item.line_style == 'dash'
    assert item.window_id == old_window
    assert window.signal_tree.selected_signal_keys() == [key]
    assert window.signal_tree._unavailable_signals == {}


def test_same_named_old_channels_coalesce_without_duplicate_rows_or_selection(window):
    old = store((1, 'A', 'Speed', 'rpm'), (2, 'B', 'Speed', 'V'))
    load_direct(window, old)
    first, second = old.all_keys()
    window.add_signal_to_plot(first)
    first_window = window.plot_panel._items[first].window_id
    window.plot_panel.new_window()
    window.add_signal_to_plot(second)
    window.signal_tree.select_signal_keys([first, second])
    config = window._capture_temporary_plot_configuration()
    new = store((3, 'C', 'Speed', ''), value=90)
    load_direct(window, new, config)
    target = new.all_keys()[0]
    assert window.plot_panel.plotted_keys() == [target]
    assert window.signal_tree.selected_signal_keys() == [target]
    assert window.plot_panel._items[target].window_id == first_window
    assert window.plot_panel._items[target].series is new.get_series(target)
    assert len(window._inherited_signals) == 1
    layout = window.plot_panel.window_layout()
    assert sum(spec['keys'].count(target) for spec in layout) == 1
    handoff = window._capture_temporary_plot_configuration()
    assert len(handoff['inheritance']) == 1


@pytest.mark.parametrize('old,new,expected', [
    ('a.blf', 'b.mf4', False), ('a.MF4', 'b.csv', False),
    ('a.mdf', 'b.MF4', True), ('a.csv', 'b.blf', True),
])
def test_cross_family_open_clears_handoff(window, monkeypatch, tmp_path, old, new, expected):
    load_direct(window, store((1, 'A', 'Speed', 'rpm')))
    window.add_signal_to_plot('CH1::A::Speed')
    window.measurement_path = str(tmp_path / old)
    path = str(tmp_path / new)
    monkeypatch.setattr(window, '_accept_measurement_content', lambda _: True)
    monkeypatch.setattr('gui.main_window.dbc_required_for', lambda _: False)
    monkeypatch.setattr('gui.main_window.has_mixed_mdf_content', lambda _: False)
    window.open_measurement_path(path)
    assert bool(window._temporary_plot_handoff) is expected
    assert window.plot_panel.plotted_keys() == []
    assert window._inherited_signals == []


def test_calculated_curve_recomputes_from_new_group_and_recovers_after_missing(window, qapp):
    def wait():
        deadline = time.monotonic() + 5
        while window._calc_thread is not None and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(.005)
        assert window._calc_thread is None
    load_direct(window, store((1, 'A', 'Speed', 'rpm'), value=10))
    definition = CalculatedSignalDefinition('Doubled', '`CH1::A::Speed` * 2', 'rpm')
    window._queue_calculation(definition, 'create', plot_after=True)
    wait()
    window.add_signal_to_plot('CH1::A::Speed')
    config = window._capture_temporary_plot_configuration()
    load_direct(window, store((1, 'Other', 'Absent', 'V')), config)
    assert not len(window.plot_panel._items[definition.key].series.values)
    assert '计算输入不可用' in window.plot_panel._items[definition.key].unavailable_reason
    config = window._capture_temporary_plot_configuration()
    load_direct(window, store((1, 'B', 'Speed', 'rpm'), value=90), config)
    wait()
    item = window.plot_panel._items[definition.key]
    assert not item.unavailable_reason
    assert list(item.series.values) == [180, 182]
    assert window.plot_panel.plotted_keys() == [definition.key, 'CH1::B::Speed']


def test_restored_geometry_does_not_prompt_but_visibility_still_counts():
    from gui.session_restore import differs
    before = {'panels': {'signals_geometry': 'old position', 'signals_visible': True}}
    assert not differs({'panels': {'signals_geometry': 'adjusted position', 'signals_visible': True}}, before)
    assert differs({'panels': {'signals_geometry': 'old position', 'signals_visible': False}}, before)
