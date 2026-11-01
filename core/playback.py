"""Offline playback time and sample validity; never sends CAN traffic."""
from __future__ import annotations

from bisect import bisect_right
from datetime import datetime, timedelta, timezone
import math
import time

import numpy as np


class PlaybackClock:
    """Advance from elapsed monotonic time, not the number of GUI timer ticks."""

    def __init__(self, now=time.monotonic):
        self.now = now
        self.start = self.end = self.position = 0.0
        self.speed = 1.0
        self.playing = False
        self._anchor = self.now()
        self._position_anchor = 0.0

    def load(self, start: float, end: float) -> None:
        if not all(math.isfinite(t) for t in (start, end)) or end < start:
            raise ValueError('Invalid playback range')
        self.playing = False
        self.start, self.end = float(start), float(end)
        self.seek(start)

    def seek(self, position: float) -> None:
        if not math.isfinite(position):
            raise ValueError('Invalid playback position')
        self.position = min(self.end, max(self.start, float(position)))
        self._position_anchor, self._anchor = self.position, self.now()

    def tick(self) -> float:
        if self.playing:
            self.position = min(self.end, self._position_anchor +
                                (self.now() - self._anchor) * self.speed)
            if self.position >= self.end:
                self.playing = False
        return self.position

    def play(self) -> None:
        if self.end <= self.start:
            return
        if self.position >= self.end:
            self.seek(self.start)
        self.seek(self.position)
        self.playing = True

    def pause(self) -> None:
        self.tick()
        self.playing = False

    def set_speed(self, speed: float) -> None:
        if not math.isfinite(speed) or speed <= 0:
            raise ValueError('Playback speed must be positive')
        self.tick()
        self.speed = float(speed)
        self.seek(self.position)


def sample_at(timestamps, position: float, timeout: float = 0.0):
    """Last sample at/before position and its age. Never read a future sample.

    A None index means missing/stale. A non-None age still identifies how
    long a stale value has gone without a new sample.
    """
    index = bisect_right(timestamps, position) - 1
    if index < 0:
        return None, None
    age = position - float(timestamps[index])
    return (None if timeout > 0 and age > timeout else index), age


def data_gaps(timestamps, timeout: float):
    """Intervals after a value expires and before the next real sample."""
    if timeout <= 0 or len(timestamps) < 2:
        return []
    ts = np.asarray(timestamps, dtype=np.float64)
    gaps = []
    for start in range(0, len(ts) - 1, 262144):
        stop = min(len(ts), start + 262145)
        indices = np.flatnonzero(np.diff(ts[start:stop]) > timeout) + start
        gaps.extend((float(ts[i] + timeout), float(ts[i + 1])) for i in indices)
    return gaps


def merge_gaps(gaps):
    merged = []
    for start, end in sorted(gaps):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def recording_time(epoch: float | None, position: float, mode: str = 'utc'):
    """UTC BLF header -> local, or retain a cloud export's wall-clock fields.

    This changes display only. Relative timestamps, raw frames and exports
    are left untouched. 'wall' is an explicit choice, never a blanket -8 h.
    """
    if epoch is None:
        return None
    stamp = datetime.fromtimestamp(epoch, timezone.utc) + timedelta(seconds=position)
    if mode == 'wall':
        return stamp.replace(tzinfo=None)
    return stamp.astimezone()
