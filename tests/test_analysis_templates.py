from copy import deepcopy
import json

import numpy as np
import pytest

from core.signal_store import SignalStore
from core.project_config import save_project, load_project
from core.analysis_templates import normalise_template, write_library
from gui.main_window import MainWindow
from gui import analysis_templates
from core.calculated_signals import CalculatedSignalDefinition
import time


def make_store(group='A', names=('Target', 'Feedback'), value=10):
    store = SignalStore()
    for name in names:
        store.add_series_bulk(channel=1, message_name=group, message_id=1, signal_name=name, unit='%',
                              timestamps=np.arange(101, dtype=float)/10,
                              values=np.arange(101, dtype=float)+value, raw_values=[], has_labels=False)
    return store


@pytest.fixture
def window(qapp, tmp_path):
    widget = MainWindow('Templates', 'test')
    widget._temporary_plot_config_path = tmp_path / 'temporary.json'
    widget.measurement_path = widget.blf_path = 'current.csv'
    widget._on_worker_finished(make_store())
    yield widget
    widget.close()
    qapp.processEvents()


def configure(window):
    window.btn_windows.setChecked(True)
    window.add_signal_to_plot('CH1::A::Target')
    window.plot_panel.new_window()
    window.add_signal_to_plot('CH1::A::Feedback')
    window.plot_panel._window_specs[1]['axis_mode'] = 'individual'
    window.plot_panel.redraw()
    window.plot_panel._items['CH1::A::Target'].color = '#123456'
    return analysis_templates.capture(window)


def test_apply_replaces_curves_and_keeps_store_time_and_cursors(window, qapp):
    template = configure(window)
    panel = window.plot_panel
    window.btn_cursor1.setChecked(True)
    window.btn_cursor2.setChecked(True)
    panel._stacked_plots[0].setXRange(3, 6, padding=0)
    panel.move_cursor1(4)
    panel.move_cursor2(5)
    qapp.processEvents()
    before = list(panel._driving_vb().viewRange()[0])
    store = window.store
    window.btn_windows.setChecked(False)
    panel.clear_all()
    window.add_signal_to_plot('CH1::A::Feedback')
    panel.plot.setXRange(*before, padding=0)
    panel.move_cursor1(4)
    panel.move_cursor2(5)
    assert analysis_templates.apply(window, template)
    qapp.processEvents()
    assert window.store is store
    assert window.measurement_path == 'current.csv'
    assert panel.plotted_keys() == ['CH1::A::Target', 'CH1::A::Feedback']
    assert panel._driving_vb().viewRange()[0] == pytest.approx(before, abs=.05)
    assert panel.v_line.value() == pytest.approx(4)
    assert panel.v_line2.value() == pytest.approx(5)
    assert panel._stacked_row_keys == [['CH1::A::Target'], ['CH1::A::Feedback']]
    assert panel._window_specs[1]['axis_mode'] == 'individual'
    assert panel._items['CH1::A::Target'].color == '#123456'


def test_missing_template_signal_stays_in_its_window_and_recovers(window, qapp):
    template = configure(window)
    window._reset_for_new_measurement()
    window._on_worker_finished(make_store(names=('Target',), value=90))
    analysis_templates.apply(window, template)
    assert window.plot_panel._stacked_row_keys == [['CH1::A::Target'], ['CH1::A::Feedback']]
    ghost = window.plot_panel._items['CH1::A::Feedback']
    assert ghost.unavailable_reason == '本日志未匹配'
    assert len(ghost.series.values) == 0
    handoff = window._capture_temporary_plot_configuration()
    window._reset_for_new_measurement()
    window._temporary_plot_handoff = handoff
    window._arm_temporary_plot_handoff()
    new = make_store(group='B', value=100)
    window._on_worker_finished(new)
    assert window.plot_panel._stacked_row_keys == [['CH1::B::Target'], ['CH1::B::Feedback']]
    assert window.plot_panel._items['CH1::B::Feedback'].series is new.get_series('CH1::B::Feedback')


def test_template_library_persists_only_in_its_project(window, tmp_path):
    template = configure(window)
    first, second = tmp_path/'first.json', tmp_path/'second.json'
    save_project(first, {'name': 'E702'}, {'channels': {}}, {})
    save_project(second, {'name': 'P717'}, {'channels': {}}, {})
    write_library(first, {'电机排查': template}, expected={})
    assert '电机排查' in load_project(first)['project']['analysis_templates']
    assert not load_project(second)['project'].get('analysis_templates')
    analysis_templates.bind_profile(window, first)
    window.template_menu.persist({'改名': template})
    assert list(load_project(first)['project']['analysis_templates']) == ['改名']
    window.template_menu.persist({})
    assert not load_project(first)['project']['analysis_templates']


def test_template_contains_no_recording_samples_paths_or_old_time(window):
    template = configure(window)
    text = json.dumps(template)
    for forbidden in ('measurement_path', 'blf_path', 'channel_config', 'timestamps', 'raw_values', 'current.csv', 'position'):
        assert forbidden not in text
    poisoned = {**template, 'measurement_path': 'private.blf', 'timestamps': [1, 2, 3]}
    assert normalise_template(poisoned) == template


def test_bad_template_rejected_before_current_plot_is_changed(window):
    template = configure(window)
    template['inheritance'][0]['color'] = 'bad color'
    before = window.plot_panel.plotted_keys()
    with pytest.raises(ValueError):
        analysis_templates.apply(window, template)
    assert window.plot_panel.plotted_keys() == before


def test_library_does_not_overwrite_another_instances_edit(window, tmp_path):
    template = configure(window)
    path = tmp_path/'profile.json'
    save_project(path, {'name': 'E702'}, {'channels': {}}, {})
    write_library(path, {'Other window': template})
    before = path.read_bytes()
    with pytest.raises(ValueError, match='其他窗口'):
        write_library(path, {'My window': template}, expected={})
    assert path.read_bytes() == before


def test_template_calculations_use_the_new_recording_after_group_rename(window, qapp):
    def wait():
        end = time.monotonic()+5
        while window._calc_thread is not None and time.monotonic() < end:
            qapp.processEvents()
            time.sleep(.005)
        assert window._calc_thread is None
    configure(window)
    definition = CalculatedSignalDefinition('Doubled target', '`CH1::A::Target` * 2', '%')
    window._queue_calculation(definition, 'create', plot_after=True)
    wait()
    template = analysis_templates.capture(window)
    window._reset_for_new_measurement()
    window._on_worker_finished(make_store(group='B', value=90))
    analysis_templates.apply(window, template)
    wait()
    result = window.plot_panel._items[definition.key]
    assert len(result.series.values), window.log_box.toPlainText()
    assert result.series.values[0] == 180
    assert not window.plot_panel._items[definition.key].unavailable_reason


def test_template_restores_explicit_y_limits_in_windows_mode(window, qapp):
    window.add_signal_to_plot('CH1::A::Target')
    view = window.plot_panel._items['CH1::A::Target'].view_box
    assert view is not None
    view.setYRange(10, 20, padding=0)
    qapp.processEvents()
    template = analysis_templates.capture(window)
    view.setYRange(0, 100, padding=0)
    analysis_templates.apply(window, template)
    qapp.processEvents()
    assert window.plot_panel._items['CH1::A::Target'].view_box.viewRange()[1] == pytest.approx([10, 20])


def test_regular_configuration_roundtrip_preserves_window_ownership(window, qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, 'question', lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    configure(window)
    config = window._configuration_dict()
    window.btn_windows.setChecked(False)
    window._apply_configuration(config, 'same-session.json')
    qapp.processEvents()
    assert window.btn_windows.isChecked()
    assert window.plot_panel._stacked_row_keys == [['CH1::A::Target'], ['CH1::A::Feedback']]
    assert window.plot_panel._window_specs[1]['axis_mode'] == 'individual'


def test_manual_binding_and_missing_signal_moves_keep_window_ownership(window, monkeypatch):
    from gui import signal_inheritance
    from PySide6.QtWidgets import QInputDialog
    template = configure(window)
    window._reset_for_new_measurement()
    window._on_worker_finished(make_store(group='B', names=('Other',), value=90))
    analysis_templates.apply(window, template)
    ghost_key = 'CH1::A::Feedback'
    window.plot_panel._window_active_id = 0
    monkeypatch.setattr(QInputDialog, 'getItem', lambda *args: (args[3][0], True))
    signal_inheritance.confirm_mapping(window, ghost_key)
    assert window.plot_panel._items['CH1::B::Other'].window_id == 1
    assert window.plot_panel._items['CH1::A::Target'].unavailable_reason
    window.plot_panel._window_active_id = 1
    assert window.add_signal_to_plot('CH1::A::Target')
    assert window.plot_panel._items['CH1::A::Target'].window_id == 1
    assert not len(window.plot_panel._items['CH1::A::Target'].series.values)


def test_template_preserves_current_recording_events_and_hidden_cursor(window):
    template = configure(window)
    window.btn_cursor1.setChecked(False)
    window.plot_panel.set_events([(1.0, 'Current file event')])
    window.plot_panel.events_action.blockSignals(True)
    window.plot_panel.events_action.setChecked(True)
    window.plot_panel.events_action.blockSignals(False)
    analysis_templates.apply(window, template)
    assert not window.btn_cursor1.isChecked()
    assert window.plot_panel._events == [(1.0, 'Current file event')]
    assert window.plot_panel.events_action.isChecked()
