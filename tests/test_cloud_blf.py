import csv
from pathlib import Path

import cantools
from cantools.database.can import Message, Signal
import numpy as np
import pytest

from core.cloud_blf import convert_csv, read_metadata, sha256
from core.blf_reader import BLFReaderService
from core.bus_types import BusType


def database(tmp_path, name='matrix', signals=None, frame_id=7):
    signals = signals or [Signal('Position', 0, 8), Signal('Reset', 8, 2)]
    db = cantools.database.Database(messages=[Message(frame_id, name, 16 if any(s.start >= 64 for s in signals) else 2, signals, strict=False)], strict=False)
    path = tmp_path / (name + '.dbc')
    path.write_text(db.as_dbc_string(), encoding='utf-8')
    return path


def source(tmp_path, headers, rows):
    path = tmp_path / 'cloud.csv'
    with path.open('w', encoding='gb18030', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['采集时间'] + headers)
        for index, values in enumerate(rows):
            writer.writerow([f'2026/10/29 13:00:{index:02d}.180'] + values)
    return path


def settings(path, network='28', bus='LIN', channel=2):
    return dict(matrices=[dict(path=str(path), network=network, bus=bus, channel=channel)], time_basis='wall')


def load(path):
    from gui.load_worker import LoadWorker
    done, failed = [], []
    worker = LoadWorker(path)
    worker.finished.connect(done.append)
    worker.failed.connect(failed.append)
    worker.run()
    assert done, failed
    return done[0]


def test_full_and_partial_reopen_without_csv_or_databases(tmp_path, monkeypatch):
    monkeypatch.setenv('OSVANTA_DATA_DIR', str(tmp_path / 'cache'))
    dbc = database(tmp_path)
    csv_path = source(tmp_path, ['Position(null-007-28)/1000ms', 'Reset(null-007-28)/1000ms'], [[20, 1], ['', 0], [30, 2]])
    output = convert_csv(csv_path, settings(dbc))
    manifest, events = read_metadata(output)
    assert manifest['frame_count'] == 2
    assert len(events) == 3
    csv_path.unlink()
    dbc.unlink()
    store = load(output)
    try:
        assert store.total_frames == 2
        position = store.get_series('LIN2::matrix::Position')
        reset = store.get_series('LIN2::matrix::Reset')
        np.testing.assert_array_equal(position.values, [20, np.nan, 30])
        np.testing.assert_array_equal(reset.values, [1, 0, 2])
        np.testing.assert_allclose(position.timestamps, [0, 1, 2], atol=1e-6)
        assert store.total_samples == 6  # raw decode + metadata must not double count
        assert store.cloud_manifest['time_mode'] == 'wall'
    finally:
        store.close()


def test_partial_only_has_no_fabricated_bus_frame(tmp_path, monkeypatch):
    monkeypatch.setenv('OSVANTA_DATA_DIR', str(tmp_path / 'cache'))
    dbc = database(tmp_path)
    path = source(tmp_path, ['Position(null-007-28)/1000ms'], [[0], [50]])
    output = convert_csv(path, settings(dbc))
    assert list(BLFReaderService(output)) == []
    store = load(output)
    try:
        np.testing.assert_array_equal(store.get_series('LIN2::matrix::Position').values, [0, 50])
        assert np.isnan(store.get_series('LIN2::matrix::Reset').values).all()
    finally:
        store.close()


def test_duplicate_ids_on_different_networks_stay_separate(tmp_path, monkeypatch):
    monkeypatch.setenv('OSVANTA_DATA_DIR', str(tmp_path / 'cache'))
    one = database(tmp_path, 'can_signals')
    two = database(tmp_path, 'lin_signals')
    path = source(tmp_path, ['Position(null-007-10)/1000ms', 'Reset(null-007-10)/1000ms',
                            'Position(null-007-28)/1000ms', 'Reset(null-007-28)/1000ms'], [[11, 1, 22, 2]])
    config = settings(one, '10', 'CAN', 1)
    config['matrices'] += settings(two)['matrices']
    output = convert_csv(path, config)
    store = load(output)
    try:
        assert store.get_series('CH1::can_signals::Position').values[0] == 11
        assert store.get_series('LIN2::lin_signals::Position').values[0] == 22
        assert store.channels == {(BusType.CAN, 1), (BusType.LIN, 2)}
    finally:
        store.close()


def test_ambiguous_matrix_and_changed_version_are_rejected(tmp_path):
    dbc = database(tmp_path)
    path = source(tmp_path, ['Position(null-007-28)/1000ms'], [[3]])
    config = settings(dbc)
    config['matrices'].append(dict(config['matrices'][0], channel=3))
    output = tmp_path / 'output.blf'
    with pytest.raises(ValueError, match='Ambiguous'):
        convert_csv(path, config, output)
    assert not output.exists()
    config = settings(dbc)
    config['matrices'][0]['sha256_locked'] = sha256(dbc)
    dbc.write_text(dbc.read_text() + '\n')
    with pytest.raises(ValueError, match='Matrix changed'):
        convert_csv(path, config, output)
    assert not output.exists()


def test_missing_packed_chunk_does_not_decode_zero_filled_signals(tmp_path, monkeypatch):
    monkeypatch.setenv('OSVANTA_DATA_DIR', str(tmp_path / 'cache'))
    raw = database(tmp_path, 'packed', [Signal('Climate1TestData1', 0, 64), Signal('Climate1TestData2', 64, 64)])
    decoded = database(tmp_path, 'decoded', [Signal('InFirst', 0, 8), Signal('AcrossChunks', 60, 8), Signal('InSecond', 80, 8)])
    config = settings(raw, '10', 'CAN', 1)
    config['telemetry_database'] = str(decoded)
    path = source(tmp_path, ['Climate1TestData1(null-007-10)/1000ms', 'Climate1TestData2(null-007-10)/1000ms'], [[15, 7], [0, '']])
    output = convert_csv(path, config)
    store = load(output)
    try:
        np.testing.assert_array_equal(store.get_series('CH1::decoded::InFirst').values, [15, 0])
        assert np.isnan(store.get_series('CH1::decoded::AcrossChunks').values[-1])
        assert np.isnan(store.get_series('CH1::decoded::InSecond').values[-1])
        assert store.total_frames == 1
    finally:
        store.close()


def test_gbk_ldf_wide_scalar_keeps_layout(tmp_path, sample_ldf_path):
    from core.readers.db_format import ldf_to_dbc_string
    text = sample_ldf_path.read_text(encoding='utf-8')
    text = '/* 中文矩阵 */\n' + text.replace('MirrorAngle: 8,', 'MirrorAngle: 64,').replace('DoorNode, 1 {', 'DoorNode, 8 {')
    path = tmp_path / 'wide.ldf'
    path.write_text(text, encoding='gb18030')
    db = cantools.database.load_string(ldf_to_dbc_string(str(path)), database_format='dbc', strict=False)
    assert db.get_message_by_name('DoorStatus').signals[0].length == 64
    assert str(db.get_message_by_name('DoorCmd').get_signal_by_name('LockState').choices[1]) == 'Locked'


def test_missing_readout_and_delta(panel):
    from core.signal_store import SignalSeries
    from gui.plot_widget import _DELTA_COL
    series = SignalSeries(channel=1, message_name='M', message_id=7, signal_name='P', unit='',
                          timestamps=np.array([0., 1., 2.]), values=np.array([10., np.nan, 20.]), raw_values=[])
    panel.add_series('p', series)
    panel.set_cursor1_enabled(True)
    panel.set_cursor2_enabled(True)
    panel.playback_at(1.5)
    panel.move_cursor2(2)
    row = panel._row_lookup['p']
    assert panel.table.item(row, 2).text() == '未采集'
    assert panel.table.item(row, _DELTA_COL).text() == ''
    assert 'NO FRESH DATA' in panel.freshness_summary(1.5)[0]
    panel.set_gap_timeout(2)
    assert any(tuple(region.getRegion()) == (1., 2.) for _,region in panel._gap_regions)


def test_unknown_column_keeps_known_values_and_reports_omission(tmp_path, monkeypatch):
    monkeypatch.setenv('OSVANTA_DATA_DIR', str(tmp_path / 'cache'))
    dbc = database(tmp_path)
    path = source(tmp_path, ['Position(null-007-28)/1000ms', 'UnknownSignal(null-007-28)/1000ms'], [[0, 33]])
    store = load(convert_csv(path, settings(dbc)))
    try:
        assert store.get_series('LIN2::matrix::Position').values[0] == 0
        assert store.cloud_manifest['unmatched'][0]['signals'] == ['UnknownSignal']
        assert store.load_warnings
    finally:
        store.close()


def test_unicode_decoder_contract_survives_embedded_blf(tmp_path, monkeypatch):
    monkeypatch.setenv('OSVANTA_DATA_DIR', str(tmp_path / 'cache'))
    dbc = database(tmp_path, signals=[Signal('Position', 0, 8, unit='℃'), Signal('Reset', 8, 2)])
    text = cantools.database.Database(messages=[Message(7,'matrix',2,[Signal('Position',0,8,unit='℃'),Signal('Reset',8,2)])]).as_dbc_string()
    dbc.write_text(text, encoding='utf-8-sig')
    path = source(tmp_path, ['Position(null-007-28)/1000ms', 'Reset(null-007-28)/1000ms'], [[20, 0]])
    store = load(convert_csv(path, settings(dbc)))
    try:
        assert store.get_series('LIN2::matrix::Position').unit == '℃'
    finally:
        store.close()


def test_text_only_signal_is_not_marked_uncollected(panel):
    from core.signal_store import SignalSeries
    series = SignalSeries(channel=1, message_name='M', message_id=7, signal_name='Text', unit='',
                          timestamps=np.array([0.,1.]), values=np.array([np.nan,np.nan]), raw_values=['Off','On'], has_labels=True)
    panel.add_series('text',series)
    panel.playback_at(0.5)
    assert panel.table.item(panel._row_lookup['text'],2).text() == 'Off'
    assert 'NO FRESH DATA' not in panel.freshness_summary(0.5)[0]
