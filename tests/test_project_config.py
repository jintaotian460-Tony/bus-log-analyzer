import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from core.project_config import load_project, normalise_project, save_project


def test_profile_is_reusable_relative_and_does_not_save_a_recording_or_play_position(tmp_path):
    dbc = tmp_path / 'calibration' / 'layout.dbc'
    source = tmp_path / 'signals.csv'
    project = {'name': 'Another project', 'csv_sources': [
        {'name': 'Sensor', 'path': str(source)},
        {'name': 'Companion', 'path': '{measurement_stem}.csv'}]}
    channels = {'channels': {'CAN:2': str(dbc)}}
    path = tmp_path / 'profile.json'
    data = save_project(path, project, channels, {'time_mode': 'utc', 'gap_timeout': 3, 'position': 400})
    assert 'measurement_path' not in data and data['playback']['position'] == 0
    assert not Path(data['channel_config']['channels']['CAN:2']).is_absolute()
    loaded = load_project(path)
    assert loaded['channel_config']['channels']['CAN:2'] == str(dbc)
    assert loaded['project']['csv_sources'][0]['path'] == str(source)
    assert loaded['project']['csv_sources'][1]['path'] == '{measurement_stem}.csv'
    assert project['csv_sources'][0]['path'] == str(source)  # exporting did not rewrite live settings


def test_bad_project_schema_and_types_fail_before_application(tmp_path):
    path = tmp_path / 'project.json'
    for bad in ([], {'kind': 'other'}, {'kind': 'osvanta.project', 'schema_version': 99}):
        path.write_text(json.dumps(bad))
        with pytest.raises(ValueError):
            load_project(path)
    for bad in ([], {'csv_sources': 'not a list'}, {'csv_sources': [{}]}):
        with pytest.raises(ValueError):
            normalise_project(bad)


def test_source_dialog_reads_real_columns_and_preserves_selection(qapp, tmp_path):
    from gui.project_settings import CSVSourceDialog
    path = tmp_path / 'recording.csv'
    path.write_text('time,Sensor_A,Sensor_B,Other\n0,1,2,3\n')
    source = {'path': '{measurement_stem}.csv', 'name': 'Sensors', 'time_column': 'time',
              'prefix': 'Sensor_', 'time_basis': 'relative'}
    dialog = CSVSourceDialog(source, str(tmp_path / 'recording.blf'))
    value = dialog.value()
    assert [c['column'] for c in value['columns']] == ['Sensor_A', 'Sensor_B']
    assert value['time_column'] == 'time' and value['time_basis'] == 'relative'
    dialog.close()


def test_project_switch_unloads_previous_recording_and_does_not_decode_with_new_dbc(qapp, monkeypatch, tmp_path):
    from core.signal_store import SignalStore
    from gui.main_window import MainWindow
    from PySide6.QtWidgets import QFileDialog
    monkeypatch.setenv('OSVANTA_DATA_DIR', str(tmp_path))
    window = MainWindow('Project test', 'test')
    old = SignalStore()
    window.store = old
    window.measurement_path = window.blf_path = 'old-project.blf'
    window.load_data = Mock()
    path = tmp_path / 'new-project.json'
    save_project(path, {'name': 'New project'}, {'channels': {'CAN:2': str(tmp_path / 'new.dbc')}},
                 {'time_mode': 'wall', 'gap_timeout': 4})
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **k: (str(path), ''))
    window.open_project()
    assert window.store is None and window.measurement_path is None
    assert window.project['name'] == 'New project'
    assert window.playback.project_label.text() == 'New project'
    assert not window.load_data.called and not window.playback.isEnabled()
    assert window.playback.gap_timeout.value() == 4
    window.close()


def test_bad_session_project_keeps_current_project(qapp, monkeypatch, tmp_path):
    from gui.main_window import MainWindow
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setenv('OSVANTA_DATA_DIR', str(tmp_path))
    window = MainWindow('Project test', 'test')
    window.project = {'name': 'Current', 'csv_sources': []}
    monkeypatch.setattr(QMessageBox, 'critical', Mock())
    path = tmp_path / 'session.json'
    path.write_text(json.dumps({'project': {'csv_sources': [{}]}}))
    window.open_configuration_path(str(path))
    assert window.project['name'] == 'Current'
    assert QMessageBox.critical.called
    window.close()
