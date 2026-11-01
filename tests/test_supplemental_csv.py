from datetime import datetime, timezone

import numpy as np
import pytest

from core.signal_store import SignalStore
from core.supplemental_csv import SupplementalCSVError, attach_csv_sources, read_source


def write_csv(tmp_path, text, encoding='utf-8'):
    path = tmp_path / 'recording.csv'
    path.write_text(text, encoding=encoding)
    return path


def spec(path, **kwargs):
    return dict(path=str(path), name='Actuator', time_column='time', columns=['Position', 'Reset'],
                time_basis='wall', **kwargs)


def test_gbk_reversed_sparse_samples_keep_zero_and_own_timestamps(tmp_path):
    path = write_csv(tmp_path, '\t采集时间\t,Position(description)/1000ms,Reset\n'
                     '\t2026/10/29 13:00:02.180\t,30,\n'
                     '2026/10/29 13:00:01.180,,1\n'
                     '2026/10/29 13:00:00.180,0,0\n', 'gbk')
    source = spec(path)
    source['time_column'] = '采集时间'
    series, report = read_source(source, tmp_path / 'recording.blf', 1793278800.18)
    assert report['signals'] == 2 and report['samples'] == 4
    assert report['encoding'] == 'gb18030'
    np.testing.assert_array_equal(series[0]['timestamps'], [0, 2])
    np.testing.assert_array_equal(series[0]['values'], [0, 30])
    np.testing.assert_array_equal(series[1]['timestamps'], [0, 1])
    np.testing.assert_array_equal(series[1]['values'], [0, 1])


def test_explicit_utc_offset_aligns_against_real_utc_recording(tmp_path):
    path = write_csv(tmp_path, 'time,Position,Reset\n2026-10-29 13:00:00.180,12,0\n')
    origin = datetime(2026, 10, 29, 5, tzinfo=timezone.utc).timestamp()
    source = spec(path, utc_offset_hours=8, offset_seconds=0.02)
    source['time_basis'] = 'absolute'
    signals, _ = read_source(source, 'recording.blf', origin)
    assert signals[0]['timestamps'][0] == pytest.approx(0.2)
    source['time_basis'] = 'wall'
    signals, _ = read_source(source, 'recording.blf', origin)
    assert signals[0]['timestamps'][0] == pytest.approx(28800.2)


def test_missing_source_column_does_not_partially_attach_good_source(tmp_path):
    path = write_csv(tmp_path, 'time,Position,Reset\n2026-10-29 13:00:00.180,12,0\n')
    store = SignalStore()
    store.base_ts = 1793278800.18
    good = spec(path)
    bad = dict(good, name='Other', columns=['Absent'])
    with pytest.raises(SupplementalCSVError, match='Absent'):
        attach_csv_sources(store, [good, bad], 'recording.blf')
    assert store.all_keys() == [] and store.total_samples == 0


def test_duplicates_bad_timestamps_and_packed_integers_fail_explicitly(tmp_path):
    path = write_csv(tmp_path, 'time,Position,Reset\nbroken,12,0\n')
    with pytest.raises(SupplementalCSVError, match='row 2'):
        read_source(spec(path), 'recording.blf', 1793278800.18)
    path.write_text('time,Position,Reset\n2026-10-29 13:00:00,9007199254740993,0\n')
    with pytest.raises(SupplementalCSVError, match='precision'):
        read_source(spec(path), 'recording.blf', 1793278800.18)
    path.write_text('time,Position,Position\n2026-10-29 13:00:00,1,2\n')
    with pytest.raises(SupplementalCSVError, match='duplicate'):
        read_source(spec(path), 'recording.blf', 1793278800.18)


def test_csv_has_no_synthetic_bus_frames_and_retains_labels(tmp_path):
    path = write_csv(tmp_path, 'time,Position,Reset\n0,0,Init\n1,NaN,Normal\n2,5,\n')
    source = spec(path)
    source['time_basis'] = 'relative'
    store = SignalStore()
    store.total_frames = 7
    store.decoded_frames = 6
    attach_csv_sources(store, [source], 'recording.blf')
    assert len(store.all_keys()) == 2 and store.total_samples == 4
    assert store.total_frames == 7 and store.decoded_frames == 6
    assert store.channels == set() and store.raw_frame_store is None
    assert store.csv_source_reports[0]['invalid_values'] == 1
    reset = next(store.get_series(k) for k in store.all_keys() if k.endswith('::Reset'))
    assert reset.display_value_at(0) == 'Init'


def test_nonoverlapping_times_refuse_wrong_clock_instead_of_silent_shift(tmp_path):
    path = write_csv(tmp_path, 'time,Position,Reset\n28800,1,0\n28801,2,0\n')
    source = spec(path)
    source['time_basis'] = 'relative'
    store = SignalStore()
    store.add_series_bulk(None, 'Main', 0, 'MainSignal', '', np.array([0., 1.]),
                          np.array([1., 2.]), [], False)
    with pytest.raises(SupplementalCSVError, match='do not overlap'):
        attach_csv_sources(store, [source], 'recording.blf')
    assert len(store.all_keys()) == 1


def test_load_worker_adds_csv_without_changing_blf_trace(tmp_path, blf_path, sample_dbc_path):
    from gui.load_worker import LoadWorker
    path = write_csv(tmp_path, 'time,Position,Reset\n0,0,0\n1,2,1\n10,3,0\n')
    source = spec(path)
    source['time_basis'] = 'relative'
    baseline, attached, failures = [], [], []
    for sources, result in [(None, baseline), ([source], attached)]:
        worker = LoadWorker(blf_path, sample_dbc_path, csv_sources=sources)
        worker.finished.connect(result.append)
        worker.failed.connect(failures.append)
        worker.run()
    try:
        assert not failures
        old, new = baseline[0], attached[0]
        assert len(new.all_keys()) == len(old.all_keys()) + 2
        assert new.total_frames == old.total_frames and new.decoded_frames == old.decoded_frames
        assert new.total_samples == old.total_samples + 6
        assert new.channels == old.channels
        np.testing.assert_array_equal(new.raw_frame_store.timestamps, old.raw_frame_store.timestamps)
        assert len(new.raw_frame_store) == len(old.raw_frame_store)
        assert new.supplemental_time_bounds == (0, 10)
    finally:
        for store in baseline + attached:
            store.raw_frame_store.close()
            store.close()
