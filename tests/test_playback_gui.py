import numpy as np
import pytest

from core.signal_store import SignalSeries, SignalStore


def series(name='Temperature', times=(0, 1, 10, 11)):
    return SignalSeries(channel=1, message_name='Telemetry', message_id=0x638,
                        signal_name=name, unit='', timestamps=np.array(times, dtype=float),
                        values=np.array([20, 21, 30, 31], dtype=float), raw_values=[])


def cell(panel, col=2, key='temp'):
    return panel.table.item(panel._row_lookup[key], col).text()


def test_player_seeks_pauses_changes_speed_and_finishes(qapp):
    from gui.playback_bar import PlaybackBar
    bar = PlaybackBar()
    now = [0.0]
    bar.clock.now = lambda: now[0]
    bar.load((0, 20), 1793278800.18, True)
    emitted = []
    bar.positionChanged.connect(emitted.append)
    bar.toggle()
    now[0] = 1
    bar._tick()
    assert emitted[-1] == 1
    bar.speed.setCurrentIndex(bar.speed.findData(2.0))
    now[0] = 2
    bar._tick()
    assert emitted[-1] == 3
    bar.slider.setValue(500_000)
    assert bar.clock.position == 10
    assert not bar.clock.playing
    bar.toggle()
    now[0] = 20
    bar._tick()
    assert emitted[-1] == 20
    assert not bar.timer.isActive()
    assert bar.play_button.text() == 'Play'
    bar.clear()
    assert not bar.isEnabled()
    bar.close()


@pytest.mark.parametrize('mode', ['normal', 'stacked', 'multi_axis'])
def test_stale_readouts_gap_curves_and_layout_rebuilds(panel, qapp, mode):
    s = series()
    original = s.values.copy()
    panel.add_series('temp', s)
    panel.set_cursor1_enabled(True)
    panel.set_gap_timeout(2)
    if mode == 'stacked':
        panel.set_stacked(True)
    elif mode == 'multi_axis':
        panel.set_multi_axis(True)
    qapp.processEvents()
    panel.playback_at(0.9)
    assert cell(panel) == '20.000'  # nearest-sample lookup would show a future 21
    panel.playback_at(9)
    assert cell(panel) == 'Stale (8.0 s)'
    assert 'NO FRESH DATA' in panel.freshness_summary(9)[0]
    assert len(panel._gap_regions) == 1
    assert tuple(panel._gap_regions[0][1].getRegion()) == (3, 10)
    assert not panel._items['temp'].curve.opts['connect'][1]
    panel.playback_at(10)
    assert cell(panel) == '30.000'
    assert 'fresh' in panel.freshness_summary(10)[0]
    panel.set_cursor2_enabled(True)
    panel.move_cursor2(9)
    assert cell(panel, 3) == 'Stale (8.0 s)'
    from gui.plot_widget import _DELTA_COL
    assert cell(panel, _DELTA_COL) == ''
    assert np.array_equal(s.values, original)
    panel.set_gap_timeout(0)
    assert not panel._gap_regions


def test_before_first_sample_shows_when_playback_starts(panel, qapp):
    s = series('Late signal', times=(0.773, 1.0, 1.1))
    panel.add_series('late', s)
    panel.set_cursor1_enabled(True)
    panel.playback_at(0.5)

    assert cell(panel, key='late') == '未开始（首个采样 0.773 s）'
    text, detail = panel.freshness_summary(0.5)
    assert 'NO FRESH DATA: 1/1' in text
    assert 'Late signal: 未开始（首个采样 0.773 s）' in detail


def test_status_bar_reports_the_total_number_of_plotted_signals(qapp, monkeypatch, tmp_path):
    from gui.main_window import MainWindow

    monkeypatch.setenv('OSVANTA_DATA_DIR', str(tmp_path))
    window = MainWindow('Playback count test', 'test')
    try:
        store = SignalStore()
        store._series_by_key['one'] = series('One')
        store._series_by_key['two'] = series('Two', (0, 1, 2, 3))
        window.store = store

        window.add_signals_to_plot(['one'])
        assert window.status_state_label.text() == 'State: Plotted 1 signal(s)'
        window.add_signals_to_plot(['two'])
        assert window.status_state_label.text() == 'State: Plotted 2 signal(s)'
        window.add_signals_to_plot(['two'])
        assert window.status_state_label.text() == 'State: Plotted 2 signal(s)'
        window.clear_plot()
        assert window.status_state_label.text() == 'State: Plotted 0 signal(s)'
    finally:
        window.close()
        qapp.processEvents()


def test_fresh_message_does_not_hide_another_signals_gap(panel):
    panel.add_series('temp', series())
    panel.add_series('other', series('Other', (0, 3, 6, 9)))
    panel.set_gap_timeout(2)
    text, detail = panel.freshness_summary(9)
    assert '1/2' in text
    assert 'Temperature' in detail and 'Other' not in detail


def test_playback_settings_roundtrip_and_reload_resets_to_saved_position(qapp):
    from gui.playback_bar import PlaybackBar
    bar = PlaybackBar()
    config = {'time_mode': 'wall', 'speed': 5.0, 'gap_timeout': 2.0,
              'follow_view': False, 'position': 7.25}
    bar.apply_config(config)
    bar.load((0, 20), 1793278800.18, True)
    assert bar.to_config() == config
    assert '2026-10-29 13:00:07.430' in bar.time_label.text()
    bar.toggle()
    bar.clear()
    assert not bar.timer.isActive()
    bar.load((0, 4), 1793278800.18, False)
    assert bar.clock.position == 0
    assert bar.time_mode.currentData() == 'utc'
    assert bar.gap_timeout.value() == 0
    bar.close()


def test_observation_header_tracks_cursors_without_transport_controls(qapp, monkeypatch, tmp_path):
    from gui.main_window import MainWindow
    monkeypatch.setenv('OSVANTA_DATA_DIR', str(tmp_path))
    window = MainWindow('Playback test', 'test')
    store = SignalStore()
    store.base_ts = 1793278800.18
    store._series_by_key['temp'] = series()
    window.store = store
    window.plot_panel.add_series('temp', store.get_series('temp'))
    window.playback.apply_config({'gap_timeout': 2})
    window._prepare_playback()
    window.show()
    qapp.processEvents()
    assert window.playback.transport.isHidden()
    assert not window.playback.play_button.isEnabled()
    assert not window.playback.follow.isVisible()
    assert window.playback.time_mode.isVisible()
    window.playback.time_mode.setCurrentIndex(1)
    window.playback.seek(9)
    assert window.btn_cursor1.isChecked()
    assert window.plot_panel.v_line.value() == 9
    assert cell(window.plot_panel) == 'Stale (8.0 s)'
    assert 'NO FRESH DATA' in window.playback.gap_label.text()
    assert window._configuration_dict()['playback']['time_mode'] == 'wall'
    now = [0.0]
    window.playback.clock.now = lambda: now[0]
    window.playback.toggle()
    now[0] = 1
    assert not window.playback.clock.playing and not window.playback.timer.isActive()
    window.plot_panel.move_cursor1(10)
    assert window.playback.clock.position == 10
    assert cell(window.plot_panel) == '30.000'
    assert '2026-10-29 13:00:10.180' in window.playback.time_label.text()
    window.playback.apply_config({'follow_view': True, 'speed': 5, 'position': 3})
    assert not window.playback.follow.isChecked()
    window._prepare_playback()
    assert window.playback.clock.position == 3
    assert window.playback.transport.isHidden()
    window._release_store()
    assert not window.playback.timer.isActive()
    window.close()


def test_pause_publishes_final_time_without_undoing_a_manual_cursor_seek(qapp):
    from gui.playback_bar import PlaybackBar
    bar = PlaybackBar()
    now = [0.0]
    bar.clock.now = lambda: now[0]
    bar.load((0,20))
    updates = []
    bar.positionChanged.connect(updates.append)
    bar.toggle()
    now[0] = 0.5
    bar._tick()
    now[0] = 0.515
    bar.pause()
    assert updates[-1] == bar.clock.position == 0.515
    previous = len(updates)
    bar.follow_cursor(8)
    assert bar.clock.position == 8
    assert len(updates) == previous
    bar.close()


def test_recording_clock_keeps_saved_seek_and_project_preferences_between_logs(qapp):
    from gui.playback_bar import PlaybackBar
    bar = PlaybackBar()
    bar.apply_config({'position':5, 'gap_timeout':2})
    bar.use_recording_time_mode('wall')
    bar.load((0,10),1793278800.18, keep_preferences=True)
    assert bar.clock.position == 5
    assert bar.time_mode.currentData() == 'wall'
    bar.clear()
    bar.load((0,20),1793278800.18, keep_preferences=True)
    assert bar.clock.position == 0
    assert bar.time_mode.currentData() == 'wall'
    assert bar.gap_timeout.value() == 2
    bar.close()


def test_explicit_configuration_startup_restores_player_without_session_prompt(qapp, monkeypatch, tmp_path):
    import json
    from unittest.mock import Mock
    from gui.main_window import MainWindow
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setenv('OSVANTA_DATA_DIR', str(tmp_path))
    window = MainWindow('Playback test', 'test')
    window._apply_configuration = Mock()
    config = {'measurement_path': 'recording.blf', 'playback': {'time_mode': 'wall'}}
    path = tmp_path / 'session.json'
    path.write_text(json.dumps(config), encoding='utf-8')
    window.open_configuration_path(str(path))
    window._apply_configuration.assert_called_once_with(config, str(path))
    assert window._session_offered
    window._apply_configuration.reset_mock()
    monkeypatch.setattr(QMessageBox, 'critical', Mock())
    path.write_text('[]', encoding='utf-8')
    window.open_configuration_path(str(path))
    assert not window._apply_configuration.called
    assert QMessageBox.critical.called
    window.close()
