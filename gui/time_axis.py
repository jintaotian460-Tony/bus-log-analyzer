"""Seconds on the common recording timeline, with readable minor ticks."""
import math

import pyqtgraph as pg
from PySide6.QtCore import QPointF, Qt
from gui.observation_style import ObservationGrid


class TimePan:
    """Translate a time range from the original press, without changing its span."""
    def __init__(self, view, start_x, width, on_start=None):
        self.view, self.start_x, self.width = view, start_x, width
        self.bounds = tuple(view.viewRange()[0])
        self.on_start = on_start
        self.started = False

    def move(self, x):
        if self.width <= 0 or (x == self.start_x and not self.started):
            return
        if not self.started:
            if self.on_start is not None:
                self.on_start()
            self.started = True
        low, high = self.bounds
        shift = (x-self.start_x) * (high-low) / self.width
        self.view.setXRange(low-shift, high-shift, padding=0)


class TimeAxis(ObservationGrid, pg.AxisItem):
    def __init__(self, preferred_minor=None, color='#ffffff', on_pan=None):
        super().__init__('bottom')
        self._on_pan = on_pan
        self._pan = None
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        self.setToolTip('按住鼠标左键左右拖动刻度尺，平移时间轴；滚轮缩放。')
        self.preferred_minor = preferred_minor
        self.setPen(pg.mkPen(color, width=1))
        self.setTextPen(pg.mkPen(color))
        self.enableAutoSIPrefix(False)
        self.setStyle(tickLength=8, maxTickLevel=1, maxTextLevel=0,
                      tickTextOffset=5, autoExpandTextSpace=True)

    def mouseDragEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseDragEvent(event)
            return
        view = self.linkedView()
        if view is None:
            event.ignore()
            return
        if event.isStart():
            self._pan = TimePan(view, event.buttonDownScenePos().x(),
                                view.sceneBoundingRect().width(), self._on_pan)
        if self._pan is not None:
            self._pan.move(event.scenePos().x())
        if event.isFinish():
            self._pan = None
        event.accept()

    def drawPicture(self, painter, axisSpec, tickSpecs, textSpecs):
        super().drawPicture(painter, axisSpec, tickSpecs, textSpecs)
        if not self.style['showValues']:
            return
        # AxisItem replaces its short ticks with grid lines when showGrid is
        # enabled. Draw the ruler separately so both remain visible.
        pen, start, end = axisSpec
        width = end.x() - start.x()
        low, high = self.range
        if width <= 0 or high <= low:
            return
        major = self.tickValues(low, high, width)[0][1]
        major_x = [start.x() + (value-low) * width / (high-low) for value in major]
        painter.save()
        painter.setPen(pen)
        painter.drawLine(start, end)
        for _grid_pen, point, _other in tickSpecs:
            x = point.x()
            length = 8 if any(abs(x-position) < .75 for position in major_x) else 4
            painter.drawLine(QPointF(x, start.y()), QPointF(x, start.y()+length))
        painter.restore()

    def tickValues(self, minVal, maxVal, size):
        low, high = sorted((float(minVal), float(maxVal)))
        span = high - low
        if not math.isfinite(span) or span <= 0 or size <= 0:
            return []
        minor = self.preferred_minor
        pixels = size * minor / span if minor else 0
        if minor and 6 <= pixels <= 60:
            divisions = 5 if pixels >= 18 else 10
            major = minor * divisions
        else:
            target = span / max(1, int(size / 90))
            base = 10 ** math.floor(math.log10(target))
            major = next(step * base for step in (1, 2, 5, 10)
                         if step * base >= target * (1 - 1e-10))
            divisions = 5
            minor = major / divisions
        first = math.ceil(low / minor - 1e-9)
        last = math.floor(high / minor + 1e-9)
        major_ticks, minor_ticks = [], []
        for index in range(first, min(last + 1, first + 1000)):
            value = index * minor
            (major_ticks if index % divisions == 0 else minor_ticks).append(value)
        return [(major, major_ticks), (minor, minor_ticks)]

    def tickStrings(self, values, scale, spacing):
        decimals = max(0, min(6, math.ceil(-math.log10(spacing) - 1e-9))) if spacing > 0 else 3
        threshold = .5 * 10 ** -decimals
        return [f'{0.0 if abs(value) < threshold else value:.{decimals}f}s' for value in values]
