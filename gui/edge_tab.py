# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""A one-pixel divider with a transparent, wider mouse target.

The divider overlays the boundary instead of reserving an empty strip. Drag
coordinates are global so resizing the widget under the mouse cannot feed back
into the next movement. A single click does nothing; double-click is separate
from dragging and can be connected to a panel's visibility toggle.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPalette, QPen
from PySide6.QtWidgets import QApplication, QWidget


def _mix(base: QColor, toward: QColor, amount: float) -> QColor:
    """Colour interpolation also used by the plot panel's theme controls."""
    return QColor(
        round(base.red() + (toward.red()-base.red())*amount),
        round(base.green() + (toward.green()-base.green())*amount),
        round(base.blue() + (toward.blue()-base.blue())*amount),
    )


class EdgeTab(QWidget):
    THICKNESS = 7  # mouse target, not the painted line's width
    dragStarted = Signal()
    dragged = Signal(float)
    dragFinished = Signal()
    doubleClicked = Signal()

    def __init__(self, orientation: Qt.Orientation, parent: QWidget | None = None):
        super().__init__(parent)
        self._orientation = orientation
        self._origin = None
        self._dragging = False
        self._line_offset = None
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)
        horizontal = orientation == Qt.Orientation.Horizontal
        self.setCursor(Qt.CursorShape.SizeVerCursor if horizontal else Qt.CursorShape.SizeHorCursor)
        self.resize(80 if horizontal else self.THICKNESS,
                    self.THICKNESS if horizontal else 80)

    def set_line_offset(self, offset=None):
        """Keep the line on the exact edge even if its mouse target is clipped."""
        self._line_offset = offset
        self.update()

    def _coordinate(self, event):
        position = event.globalPosition()
        return position.y() if self._orientation == Qt.Orientation.Horizontal else position.x()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._origin = self._coordinate(event)
            self._dragging = False
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._origin is not None and event.buttons() & Qt.MouseButton.LeftButton:
            delta = self._coordinate(event) - self._origin
            if not self._dragging and abs(delta) >= QApplication.startDragDistance():
                self._dragging = True
                self.dragStarted.emit()
            if self._dragging:
                self.dragged.emit(delta)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            dragged = self._dragging
            self._origin = None
            self._dragging = False
            if dragged:
                self.dragFinished.emit()
            self.update()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._origin = None
            self._dragging = False
            self.doubleClicked.emit()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()

    def paintEvent(self, event):
        palette = self.palette()
        active = self.underMouse() or self._dragging
        # Midlight, not Mid: against a dark plot background a Mid line was
        # one invisible pixel, and a divider nobody sees is a feature nobody
        # finds — however well it works once found.
        color = palette.color(QPalette.ColorRole.Highlight if active
                              else QPalette.ColorRole.Midlight)
        painter = QPainter(self)
        painter.setPen(QPen(QColor(color), 2 if active else 1))
        if self._orientation == Qt.Orientation.Horizontal:
            y = round(self._line_offset if self._line_offset is not None else self.height() // 2)
            painter.drawLine(0, y, self.width()-1, y)
            # Three dashes at the centre: the classic drag-handle shape that
            # says "this divider moves" without any chrome around it.
            grip_pen = QPen(QColor(color), 3)
            grip_pen.setCapStyle(Qt.PenCapStyle.FlatCap)   # exactly 8-px dashes
            painter.setPen(grip_pen)
            centre = self.width() // 2
            for start, stop in ((centre-26, centre-18), (centre-4, centre+4),
                                (centre+18, centre+26)):
                painter.drawLine(start, y, stop, y)
        else:
            x = round(self._line_offset if self._line_offset is not None else self.width() // 2)
            painter.drawLine(x, 0, x, self.height()-1)
            grip_pen = QPen(QColor(color), 3)
            grip_pen.setCapStyle(Qt.PenCapStyle.FlatCap)   # exactly 8-px dashes
            painter.setPen(grip_pen)
            centre = self.height() // 2
            for start, stop in ((centre-26, centre-18), (centre-4, centre+4),
                                (centre+18, centre+26)):
                painter.drawLine(x, start, x, stop)

