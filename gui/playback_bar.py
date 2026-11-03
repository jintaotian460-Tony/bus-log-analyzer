"""Recording status shared by the plot, its cursor readouts and the Trace."""
from __future__ import annotations

import math

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QHBoxLayout, QLabel, QPushButton,
    QSlider, QVBoxLayout, QWidget,
)

from core.playback import PlaybackClock, recording_time


class PlaybackBar(QWidget):
    positionChanged = Signal(float)
    gapTimeoutChanged = Signal(float)

    def __init__(self, parent=None, *, transport_controls=True):
        super().__init__(parent)
        self.transport_controls = bool(transport_controls)
        self.clock = PlaybackClock()
        self.epoch = None
        self._updating = False
        self._configured = False
        self._pending_position = 0.0
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self._tick)

        self.play_button = QPushButton('Play')
        self.project_label = QLabel('离线观察')
        self.project_label.setMaximumWidth(180)
        self.project_label.setMinimumWidth(1)
        self.restart_button = QPushButton('Restart')
        self.speed = QComboBox()
        for value in (0.25, 0.5, 1, 2, 5, 10, 50):
            self.speed.addItem(f'{value:g}×', float(value))
        self.speed.setCurrentIndex(2)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 1_000_000)
        self.slider.setMinimumWidth(80)
        self.slider.setToolTip('Seek through the recording. Dragging pauses playback.')
        self.position = QDoubleSpinBox()
        self.position.setDecimals(3)
        self.position.setSuffix(' s')
        self.position.setKeyboardTracking(False)
        self.position.setToolTip('Playback position in seconds; Enter to seek')
        self.follow = QCheckBox('Follow view')
        self.follow.setChecked(self.transport_controls)

        # Keep the reusable clock/controller compatible with old configurations.
        # The observation interface exposes only the status row and cursor input.
        self.transport = QWidget(self)
        row = QHBoxLayout(self.transport)
        row.setContentsMargins(0, 0, 0, 0)
        for widget in (self.play_button, self.restart_button, self.speed, self.slider,
                       self.position, self.follow):
            row.addWidget(widget, 1 if widget is self.slider else 0)
        self.transport.setVisible(self.transport_controls)
        self.transport.setEnabled(self.transport_controls)
        self.time_mode = QComboBox()
        self.time_mode.addItem('BLF UTC → local', 'utc')
        self.time_mode.addItem('Cloud local wall time', 'wall')
        self.time_mode.setToolTip(
            'Standard BLF: UTC header displayed in the system timezone.\n'
            'Cloud local wall time: use when the converter wrote local clock fields\n'
            'directly to the BLF header. This avoids applying the timezone twice.\n'
            'Display only; frame timing and the source file are not changed.')
        self.time_label = QLabel('No decoded measurement')
        self.time_label.setMinimumWidth(1)
        self.time_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        # Saved sessions still use the gap setting; its editor lives in Project settings.
        self.gap_timeout = QDoubleSpinBox(self)
        self.gap_timeout.hide()
        self.gap_timeout.setRange(0, 3600)
        self.gap_timeout.setDecimals(2)
        self.gap_timeout.setSuffix(' s')
        self.gap_timeout.setSpecialValueText('Off')
        self.gap_timeout.setKeyboardTracking(False)
        self.gap_timeout.setToolTip(
            'Mark samples stale after this many seconds without an update.\n'
            'Curves break across longer gaps; zero disables gap marking.')
        details = QHBoxLayout()
        details.setSpacing(8)
        details.addWidget(self.project_label)
        details.addWidget(self.time_mode)
        details.addWidget(self.time_label)
        details.addStretch(1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(0)
        layout.addWidget(self.transport)
        layout.addLayout(details)

        self.play_button.clicked.connect(self.toggle)
        self.restart_button.clicked.connect(self.restart)
        self.speed.currentIndexChanged.connect(self._speed_changed)
        self.slider.sliderPressed.connect(self.pause)
        self.slider.valueChanged.connect(self._slider_seek)
        self.position.valueChanged.connect(self._spin_seek)
        self.time_mode.currentIndexChanged.connect(self._refresh)
        self.gap_timeout.valueChanged.connect(self.gapTimeoutChanged.emit)
        self.clear()

    def clear(self):
        self.pause(publish=False)
        self.clock.load(0, 0)
        self.epoch = None
        self._updating = True
        try:
            self.position.setRange(0, 0)
            self.slider.setValue(0)
        finally:
            self._updating = False
        self.setEnabled(False)
        self.time_label.setText('No decoded measurement')
        self.time_mode.setVisible(self.transport_controls)

    def load(self, bounds, epoch=None, telemetry=False, *, keep_preferences=False):
        self.pause(publish=False)
        start, end = bounds
        if start is None or end is None:
            self.clear()
            return
        self.clock.load(start, end)
        self.epoch = epoch
        self._updating = True
        try:
            self.position.setRange(start, end)
        finally:
            self._updating = False
        self.time_mode.setEnabled(epoch is not None)
        self.time_mode.setVisible(self.transport_controls or epoch is not None)
        self.setEnabled(True)
        self.play_button.setEnabled(end > start)
        recording_mode = getattr(self, '_recording_mode', None)
        if not self._configured and not keep_preferences and recording_mode is None:
            self.time_mode.setCurrentIndex(0)
            self.gap_timeout.setValue(2.0 if telemetry else 0.0)
        if recording_mode is not None:
            self.time_mode.setCurrentIndex(max(0, self.time_mode.findData(recording_mode)))
            self._recording_mode = None
        self.clock.seek(self._pending_position if self._configured else start)
        self._configured = False
        self._refresh()

    def pause(self, *, publish=True):
        self.clock.pause()
        self.timer.stop()
        self.play_button.setText('Play')
        if publish:
            self._publish()

    def toggle(self):
        if not self.transport_controls:
            return
        if self.clock.playing:
            self.pause()
        else:
            self.clock.play()
            if self.clock.playing:
                self.timer.start()
                self.play_button.setText('Pause')
        self._publish()

    def restart(self):
        self.pause()
        self.seek(self.clock.start)

    def seek(self, value):
        self.clock.seek(float(value))
        self._publish()

    def follow_cursor(self, value):
        if self._updating:
            return
        self.pause(publish=False)
        self.clock.seek(float(value))
        self._refresh()

    def _slider_seek(self, value):
        if not self._updating:
            self.pause()
            span = self.clock.end - self.clock.start
            self.seek(self.clock.start + span * value / self.slider.maximum())

    def _spin_seek(self, value):
        if not self._updating:
            self.pause()
            self.seek(value)

    def _speed_changed(self):
        self.clock.set_speed(self.speed.currentData())
        self._publish()

    def _tick(self):
        self.clock.tick()
        if not self.clock.playing:
            self.pause()
        self._publish()

    def _publish(self):
        self._updating = True
        try:
            self._refresh()
            self.positionChanged.emit(self.clock.position)
        finally:
            self._updating = False

    def _refresh(self):
        was_updating, self._updating = self._updating, True
        try:
            value, span = self.clock.position, self.clock.end - self.clock.start
            self.position.setValue(value)
            self.slider.setValue(round((value - self.clock.start) / span *
                                       self.slider.maximum()) if span > 0 else 0)
            stamp = recording_time(self.epoch, value, self.time_mode.currentData())
            text = stamp.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3] if stamp else 'Relative time'
            self.time_label.setText(f'{text}  /  {self.clock.end:.3f} s')
        finally:
            self._updating = was_updating

    def to_config(self):
        return {'time_mode': self.time_mode.currentData(),
                'speed': self.speed.currentData(),
                'gap_timeout': self.gap_timeout.value(),
                'follow_view': self.follow.isChecked(),
                'position': self.clock.position}

    def use_recording_time_mode(self, mode):
        """Read the file clock contract without discarding a pending seek."""
        self._recording_mode = mode
        self.time_mode.setCurrentIndex(max(0, self.time_mode.findData(mode)))

    def apply_config(self, config):
        self.pause()
        config = config if isinstance(config, dict) else {}
        self.time_mode.setCurrentIndex(max(0, self.time_mode.findData(config.get('time_mode', 'utc'))))
        self.speed.setCurrentIndex(max(0, self.speed.findData(config.get('speed', 1.0))))
        try:
            gap = float(config.get('gap_timeout', 0))
            position = float(config.get('position', 0))
            self.gap_timeout.setValue(gap if math.isfinite(gap) else 0)
            self._pending_position = position if math.isfinite(position) else 0
        except (TypeError, ValueError):
            self.gap_timeout.setValue(0)
            self._pending_position = 0
        self.follow.setChecked(self.transport_controls and bool(config.get('follow_view', True)))
        self._configured = True
