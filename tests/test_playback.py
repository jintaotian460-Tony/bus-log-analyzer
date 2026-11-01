from datetime import datetime, timezone

import pytest

from core.playback import PlaybackClock, data_gaps, merge_gaps, recording_time, sample_at


def test_elapsed_time_speed_pause_seek_and_end():
    now = [0.0]
    clock = PlaybackClock(lambda: now[0])
    clock.load(0, 20)
    clock.play()
    now[0] = 1.37  # a delayed GUI tick must not slow down the recording
    assert clock.tick() == pytest.approx(1.37)
    clock.set_speed(2)
    now[0] += 3
    assert clock.tick() == pytest.approx(7.37)
    clock.pause()
    now[0] += 50
    assert clock.tick() == pytest.approx(7.37)
    clock.seek(12)
    clock.play()
    now[0] += 10
    assert clock.tick() == 20
    assert not clock.playing
    clock.play()
    assert clock.position == 0
    clock.seek(5)
    now[0] += 1
    assert clock.tick() == 7


def test_invalid_clock_inputs_do_not_start_playback():
    clock = PlaybackClock()
    for speed in (0, -1, float('nan'), float('inf')):
        with pytest.raises(ValueError):
            clock.set_speed(speed)
    with pytest.raises(ValueError):
        clock.load(4, 2)
    with pytest.raises(ValueError):
        clock.seek(float('nan'))
    clock.load(2, 2)
    clock.play()
    assert not clock.playing


def test_samples_never_come_from_the_future_and_expire_individually():
    times = [1, 2, 10]
    assert sample_at(times, 0, 2) == (None, None)
    assert sample_at(times, 1.9, 2)[0] == 0
    assert sample_at(times, 4, 2) == (1, 2)
    assert sample_at(times, 4.01, 2)[0] is None
    assert sample_at(times, 9.99, 2)[0] is None
    assert sample_at(times, 10, 2) == (2, 0)
    assert sample_at(times, 100, 0) == (2, 90)
    assert sample_at([], 5, 2) == (None, None)


def test_gap_intervals_begin_after_expiry_and_merge_overlays():
    assert data_gaps([0, 1, 10, 11], 2) == [(3, 10)]
    assert data_gaps([0, 1, 10], 0) == []
    assert merge_gaps([(4, 8), (3, 5), (8, 10), (20, 21)]) == [(3, 10), (20, 21)]


def test_cloud_wall_clock_is_explicit_and_leaves_relative_timing_unchanged():
    epoch = datetime(2026, 10, 29, 13, 0, 0, 180000, timezone.utc).timestamp()
    wall = recording_time(epoch, 6, 'wall')
    standard = recording_time(epoch, 6, 'utc')
    assert wall == datetime(2026, 10, 29, 13, 0, 6, 180000)
    assert standard.timestamp() == pytest.approx(epoch + 6)
    assert standard.tzinfo is not None
    assert recording_time(None, 100) is None
