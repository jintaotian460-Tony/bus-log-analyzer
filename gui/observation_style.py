"""Scoped presentation for the offline graphic windows."""
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap


AXIS_WIDTH = 64
LIST_WIDTH = 360
TEXT = '#20252b'
MUTED = '#7b828a'

# These selectors are applied only to the graphic legend and its toolbars.
CONTROLS = '''
QToolButton, QPushButton {
    background: #f3f3f3; color: #20252b; border: 1px solid #bfc3c8;
    border-radius: 1px; padding: 1px 6px; min-height: 18px;
}
QToolButton:hover, QPushButton:hover { background: #e3ebf2; border-color: #8da7bd; }
QToolButton:disabled, QPushButton:disabled { color: #92989e; border-color: #d6d8db; }
QToolButton:checked, QPushButton:checked {
    background: #315f88; color: white; border-color: #315f88;
}
'''
LEGEND = CONTROLS + '''
QWidget#observationLegend { background: #ededed; color: #20252b; }
QToolButton { background: #ededed; border: 1px solid transparent; padding: 0px 3px; }
QToolButton:hover { background: #dae5ef; border-color: #a2b6c9; }
QToolButton#windowTitle { text-align: left; }
QToolButton#windowTitle:checked { background: #cfdeec; color: #142b3d; border-color: #9eb7cd; }
QTreeWidget { background: #f3f3f3; color: #20252b; border: none; outline: none; }
QTreeWidget::item { min-height: 19px; border-bottom: 1px solid transparent; }
QTreeWidget::item:selected { background: #d5e5f4; color: #172b3c; border-bottom-color: #98b5cf; }
QTreeWidget::item:hover:!selected { background: #e6edf3; }
QHeaderView::section { background: #e5e5e5; color: #31363b; padding: 2px 4px;
    border: none; border-bottom: 1px solid #b8bcc1; border-right: 1px solid #c9ccd0; }
'''


def swatch(color, line_style='solid'):
    """The line sample keeps bright signal colours readable on a light list."""
    pixmap = QPixmap(22, 12)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    # A dark backing keeps a white/yellow signal distinguishable too.
    painter.fillRect(1, 2, 20, 8, QColor('#30383d'))
    style = {'dash': Qt.PenStyle.DashLine, 'dot': Qt.PenStyle.DotLine,
             'dashdot': Qt.PenStyle.DashDotLine}.get(line_style, Qt.PenStyle.SolidLine)
    painter.setPen(QPen(QColor(color), 2, style))
    painter.drawLine(3, 6, 19, 6)
    painter.end()
    return QIcon(pixmap)


def dark(background):
    c = QColor(background)
    return .299*c.redF() + .587*c.greenF() + .114*c.blueF() < .5


class ObservationGrid:
    """Keep ruler/text pens separate from dotted measurement grid pens."""
    observation_background = None

    def drawPicture(self, painter, axis_spec, ticks, texts):
        if self.observation_background is not None and self.grid:
            adjusted = []
            for pen, first, last in ticks:
                color = QColor('#00b000' if dark(self.observation_background) else '#478047')
                color.setAlpha(min(180, max(25, round(pen.color().alpha()*1.6))))
                grid_pen = QPen(color, 1, Qt.PenStyle.DotLine)
                grid_pen.setCosmetic(True)
                adjusted.append((grid_pen, first, last))
            ticks = adjusted
        super().drawPicture(painter, axis_spec, ticks, texts)


class ValueAxis(ObservationGrid, pg.AxisItem):
    def resizeEvent(self, event=None):
        super().resizeEvent(event)
        if self.label is not None and self.orientation == 'left':
            # A row reserves space for the widest independent-axis row. Keep
            # this axis's unit beside its own ticks, not under a neighbouring axis.
            self.label.setX(self.size().width()-AXIS_WIDTH-5)


def style_axes(plot, background):
    for direction in ('left', 'bottom'):
        axis = plot.getAxis(direction)
        axis.observation_background = background
        axis.picture = None
        axis.update()
