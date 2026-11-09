"""Window ownership, local signal lists and per-window axes on one timeline."""
from copy import deepcopy

import pyqtgraph as pg
from PySide6.QtCore import Qt, QRectF, QMimeData, QPoint, QPointF, QTimer
from PySide6.QtGui import QActionGroup, QColor, QBrush, QDrag, QPainter, QPen
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QTreeWidget, QTreeWidgetItem,
                              QGraphicsProxyWidget, QGraphicsWidget, QMenu, QInputDialog,
                              QAbstractItemView, QHeaderView)

from gui.signal_tree import SignalTreeWidget
from gui.edge_tab import EdgeTab
from gui.time_axis import TimePan
from gui import observation_style


MIN_WINDOW_HEIGHT = 120
RULER_HEIGHT = 30


class TimelineRuler(QWidget):
    """A fixed overlay on the right; the left list continues to the footer."""
    def __init__(self, panel):
        super().__init__(panel._stacked_scroll.viewport())
        self.panel = panel
        self._pan = None
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        self.setToolTip('按住鼠标左键左右拖动刻度尺，所有窗口同步平移时间轴；滚轮缩放。')
        self.setFixedHeight(RULER_HEIGHT)
        self.hide()

    def paintEvent(self, event):
        from gui.plot_widget import _cursor_color_on
        panel = self.panel
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(panel.background_color()))
        if not panel._stacked_plots:
            return
        view = panel._stacked_plots[0].vb
        rect = view.sceneBoundingRect()
        left = self.mapFromGlobal(panel.glw.viewport().mapToGlobal(QPoint(round(rect.left()), 0))).x()
        right = left + rect.width()
        low, high = view.viewRange()[0]
        if high <= low or right <= left:
            return
        axis = panel._stacked_plots[0].getAxis('bottom')
        painter.setPen(QPen(QColor(_cursor_color_on(panel.background_color())), 1))
        painter.drawLine(QPoint(round(left), 1), QPoint(round(right), 1))
        for level, (spacing, values) in enumerate(axis.tickValues(low, high, right-left)):
            labels = axis.tickStrings(values, 1, spacing)
            for value, label in zip(values, labels):
                x = left + (value-low) * (right-left) / (high-low)
                painter.drawLine(QPoint(round(x), 1), QPoint(round(x), 9 if level == 0 else 5))
                if level == 0:
                    label_width = painter.fontMetrics().horizontalAdvance(label) + 4
                    label_left = max(left, min(x-label_width/2, min(right, self.width()-2)-label_width))
                    painter.drawText(QRectF(label_left, 10, label_width, 17), Qt.AlignmentFlag.AlignHCenter, label)
        painter.drawText(QRectF(2, 10, max(0, left-8), 17), Qt.AlignmentFlag.AlignRight, 't / s')

    def wheelEvent(self, event):
        self.panel._driving_vb().scaleBy(x=.8 if event.angleDelta().y() > 0 else 1.25)
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            view = self.panel._driving_vb()
            self._pan = TimePan(view, event.globalPosition().x(),
                                view.sceneBoundingRect().width(), self.panel.begin_time_pan)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._pan is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self._pan.move(event.globalPosition().x())
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._pan is not None:
            self._pan.move(event.globalPosition().x())
            self._pan = None
            self.setCursor(Qt.CursorShape.SizeHorCursor)
            event.accept()
        else:
            super().mouseReleaseEvent(event)


class HeightGrip(EdgeTab):
    """Resize the two neighbours, keeping their combined height unchanged."""
    def __init__(self, panel, index):
        super().__init__(Qt.Orientation.Horizontal, panel.glw.viewport())
        self.panel, self.index = panel, index
        self.setToolTip('拖动调整相邻窗口高度')
        self.dragStarted.connect(self.begin_resize)
        self.dragged.connect(self.resize_pair)

    def begin_resize(self):
        self.panel._push_undo()
        self.heights = list(self.panel._window_heights)

    def resize_pair(self, delta):
        panel, index = self.panel, self.index
        if not hasattr(self, 'heights') or index+1 >= len(panel._window_specs):
            return
        first, second = self.heights[index:index+2]
        delta = max(MIN_WINDOW_HEIGHT-first, min(second-MIN_WINDOW_HEIGHT, round(delta)))
        heights = list(self.heights)
        heights[index], heights[index+1] = first+delta, second-delta
        # Heights double as proportional weights in the template schema.
        scale = min(1., 1600/max(heights))
        for window, height in zip(panel._window_specs, heights):
            window['height'] = max(MIN_WINDOW_HEIGHT, round(height*scale))
        arrange(panel)


class WindowSignals(QTreeWidget):
    def __init__(self, panel, window):
        super().__init__()
        self.panel, self.window = panel, window
        self.setColumnCount(3)
        self.setHeaderLabels(['信号', 'C1', 'C2'])
        self.setToolTip('按住信号名称拖到另一窗口的列表或绘图区，可移动信号；Ctrl/Shift 多选后可一起拖动。Ctrl+Z 撤销。右键管理窗口及调整信号样式。')
        self.header().setStretchLastSection(False)
        self.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2):
            self.header().setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
            self.setColumnWidth(column, 82)
        self.setRootIsDecorated(False)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.menu)
        self.header().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.header().customContextMenuRequested.connect(self.header_menu)
        self.itemSelectionChanged.connect(self.select)
        self.itemChanged.connect(self.changed)
        self.rows = {}

    def select(self):
        self.panel._window_active_id = self.window['id']
        self.panel._restore_selection([item.data(0, Qt.ItemDataRole.UserRole) for item in self.selectedItems()])
        activate(self.panel, self.window['id'])

    def changed(self, item, column):
        if column != 0:
            return
        key = item.data(0, Qt.ItemDataRole.UserRole)
        if key in self.panel._items:
            self.panel._items[key].visible = item.checkState(0) == Qt.CheckState.Checked
            QTimer.singleShot(0, self.panel, self.panel.redraw)

    def header_menu(self, position):
        panel, window_id = self.panel, self.window['id']
        global_position = self.header().mapToGlobal(position)
        activate(panel, window_id)
        QTimer.singleShot(0, panel, lambda: show_window_menu(panel, window_id, global_position))

    def menu(self, position):
        panel, window_id = self.panel, self.window['id']
        global_position = self.viewport().mapToGlobal(position)
        activate(panel, window_id)
        item = self.itemAt(position)
        if item is None:
            QTimer.singleShot(0, panel, lambda: show_window_menu(panel, window_id, global_position))
            return
        item.setSelected(True)
        self.select()
        keys = panel.selected_keys()
        key = item.data(0, Qt.ItemDataRole.UserRole)
        if panel._items[key].unavailable_reason:
            def open_menu():
                menu = QMenu(panel)
                add_window_actions(panel, menu, window_id)
                menu.addSeparator()
                menu.addAction('匹配到本日志信号…', lambda: panel.signalMatchRequested.emit(key))
                menu.addAction('移除此曲线', lambda: panel.remove_series(key))
                menu.exec(global_position)
                menu.deleteLater()
            QTimer.singleShot(0, panel, open_menu)
            return
        QTimer.singleShot(0, panel, lambda: panel._show_signal_menu(
            keys, global_position, window_id=window_id))

    def startDrag(self, actions):
        mime = QMimeData()
        keys = [str(item.data(0, Qt.ItemDataRole.UserRole)) for item in self.selectedItems()]
        if not keys:
            return
        mime.setData(SignalTreeWidget.MIME_TYPE, '\n'.join(keys).encode('utf-8'))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.MoveAction)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(SignalTreeWidget.MIME_TYPE):
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        if not event.mimeData().hasFormat(SignalTreeWidget.MIME_TYPE):
            event.ignore()
            return
        keys = self.panel._decode_signal_mime(event.mimeData(), SignalTreeWidget.MIME_TYPE)
        if queue_drop(self.panel, keys, self.window['id']):
            event.acceptProposedAction()
        else:
            event.ignore()


def move_signals_to_window(panel, keys, target):
    """Move existing signal objects together, in one redraw and undo step."""
    if not panel._windows_mode or target not in {window['id'] for window in panel._window_specs}:
        return False
    moving = [key for key in dict.fromkeys(keys) if key in panel._items and panel._items[key].window_id != target]
    if not moving:
        return False
    panel._push_undo()
    for key in moving:
        panel._items[key].window_id = target
    activate(panel, target)
    panel.redraw()
    return True


def queue_drop(panel, keys, target):
    """Finish the Qt drop before rebuilding the proxy that owns its source."""
    keys = tuple(dict.fromkeys(key for key in keys if key))
    if not keys or not panel._windows_mode or target not in {window['id'] for window in panel._window_specs}:
        return False
    revision = panel._rebuild_seq
    def apply():
        if panel._rebuild_seq != revision or not panel._windows_mode:
            return
        if target not in {window['id'] for window in panel._window_specs}:
            return
        existing = [key for key in keys if key in panel._items]
        new = [key for key in keys if key not in panel._items]
        activate(panel, target)
        move_signals_to_window(panel, existing, target)
        if new:
            panel.signalDropped.emit(new)
    QTimer.singleShot(0, panel, apply)
    return True


def initialise(panel):
    panel._windows_mode = False
    panel._window_specs = []
    panel._window_active_id = 0
    panel._window_widgets = {}
    panel._window_extra_axes = []
    panel._window_resize_hooks = []
    panel._window_time_hooks = []
    panel._window_time_syncing = False
    panel._window_time_revision = 0
    panel._window_history = {}
    panel._window_y_restore = {}
    panel._window_grips = []
    panel._window_heights = []
    panel._window_arranging = False
    panel._window_ruler_spacer = None


def ensure(panel):
    if not panel._window_specs:
        panel._window_specs = [dict(id=0, name='窗口 1', axis_mode='shared', height=220)]
    valid = {window['id'] for window in panel._window_specs}
    if panel._window_active_id not in valid:
        panel._window_active_id = panel._window_specs[0]['id']
    for item in panel._items.values():
        if item.window_id not in valid:
            item.window_id = panel._window_active_id


def activate(panel, window_id):
    panel._window_active_id = window_id


def add_window(panel):
    panel._push_undo()
    identifier = max([window['id'] for window in panel._window_specs], default=-1) + 1
    panel._window_specs.append(dict(id=identifier, name=f'窗口 {identifier+1}', axis_mode='shared', height=220))
    panel._window_active_id = identifier
    panel._rebuild_curves(preserve_selection=True)


def close_window(panel, window):
    panel._push_undo()
    for key in [key for key, item in panel._items.items() if item.window_id == window['id']]:
        panel._items.pop(key)
    panel._window_specs.remove(window)
    ensure(panel)
    panel._current_key = next(iter(panel._items), None)
    panel.redraw()


def move_window(panel, window, direction):
    index = panel._window_specs.index(window)
    target = index + direction
    if 0 <= target < len(panel._window_specs):
        panel._push_undo()
        panel._window_specs[index], panel._window_specs[target] = panel._window_specs[target], panel._window_specs[index]
        panel.redraw()


def change_axis_mode(panel, window_id, mode):
    if not panel._windows_mode or mode not in ('shared', 'individual'):
        return False
    window = next((spec for spec in panel._window_specs if spec['id'] == window_id), None)
    if window is None or window['axis_mode'] == mode:
        return False
    panel._push_undo()
    window['axis_mode'] = mode
    panel._window_y_restore = {}

    def redraw():
        if not panel._windows_mode or not any(spec['id'] == window_id for spec in panel._window_specs):
            return
        panel._rebuild_curves(preserve_selection=True, preserve_y_range=False)
        panel.fit_vertical()

    # The rebuild removes the proxy that opened the menu; let its event return first.
    QTimer.singleShot(0, panel, redraw)
    return True


def add_axis_mode_menu(panel, menu, window_id):
    window = next((spec for spec in panel._window_specs if spec['id'] == window_id), None)
    if not panel._windows_mode or window is None:
        return
    submenu = menu.addMenu('此窗口 Y 轴')
    submenu.setObjectName('windowAxisModeMenu')
    group = QActionGroup(submenu)
    group.setExclusive(True)
    for label, mode in (('共轴', 'shared'), ('独立轴', 'individual')):
        action = submenu.addAction(label)
        action.setData(mode)
        action.setCheckable(True)
        action.setChecked(window['axis_mode'] == mode)
        group.addAction(action)
        action.triggered.connect(
            lambda _checked=False, target=window_id, value=mode:
            change_axis_mode(panel, target, value))


def add_window_actions(panel, menu, window_id):
    window = next((spec for spec in panel._window_specs if spec['id'] == window_id), None)
    if not panel._windows_mode or window is None:
        return
    name = menu.addAction(window['name'])
    name.setEnabled(False)
    menu.addSeparator()
    target = menu.addAction('设为添加信号的目标窗口')
    target.setCheckable(True)
    target.setChecked(window_id == panel._window_active_id)
    target.triggered.connect(lambda: activate(panel, window_id))
    add_axis_mode_menu(panel, menu, window_id)

    def rename():
        name, ok = QInputDialog.getText(panel, '窗口名称', '名称', text=window['name'])
        if ok and name.strip():
            window['name'] = name.strip()

    menu.addAction('重命名窗口…', rename)
    index = panel._window_specs.index(window)
    for label, direction in (('上移窗口', -1), ('下移窗口', 1)):
        action = menu.addAction(label)
        action.setEnabled(0 <= index + direction < len(panel._window_specs))
        action.triggered.connect(
            lambda _checked=False, step=direction:
            QTimer.singleShot(0, panel, lambda: move_window(panel, window, step)))
    close = menu.addAction('关闭此窗口及其曲线')
    close.triggered.connect(lambda: QTimer.singleShot(0, panel, lambda: close_window(panel, window)))


def show_window_menu(panel, window_id, global_position):
    if not panel._windows_mode or not any(spec['id'] == window_id for spec in panel._window_specs):
        return
    menu = QMenu(panel)
    add_window_actions(panel, menu, window_id)
    menu.exec(global_position)
    menu.deleteLater()


def make_list(panel, window, keys, row, last=False):
    widget = QWidget()
    widget.setObjectName('observationLegend')
    widget.setStyleSheet(observation_style.LEGEND)
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    tree = WindowSignals(panel, window)
    tree.setHeaderHidden(row != 0)
    tree.setFrameShape(QTreeWidget.Shape.NoFrame)
    tree.setMinimumHeight(0)
    tree.blockSignals(True)
    for key in keys:
        plotted = panel._items[key]
        reason = f' — {plotted.unavailable_reason}' if plotted.unavailable_reason else ''
        item = QTreeWidgetItem([plotted.series.signal_name + reason, '', ''])
        item.setData(0, Qt.ItemDataRole.UserRole, key)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(0, Qt.CheckState.Checked if plotted.visible else Qt.CheckState.Unchecked)
        muted = bool(plotted.unavailable_reason or not plotted.visible)
        item.setForeground(0, QBrush(QColor(observation_style.MUTED if muted else observation_style.TEXT)))
        item.setIcon(0, observation_style.swatch(observation_style.MUTED if muted else plotted.color,
                                               plotted.line_style))
        for column in (1, 2):
            item.setTextAlignment(column, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        item.setToolTip(0, f'{key}\n单位：{plotted.series.unit or "无"}\n{plotted.unavailable_reason}')
        tree.addTopLevelItem(item)
        tree.rows[key] = item
    tree.blockSignals(False)
    layout.addWidget(tree, 1)
    proxy = QGraphicsProxyWidget()
    proxy.setWidget(widget)
    # The last list spans its graph and the ruler's reserved row. It remains a
    # real, interactive tree all the way down to the cursor/file information.
    panel.glw.addItem(proxy, row=row, col=0, rowspan=2 if last else 1)
    panel._window_widgets[window['id']] = (widget, tree)
    panel.glw.ci.layout.setColumnFixedWidth(0, observation_style.LIST_WIDTH)


def finish_rows(panel):
    # Reserve scrolling space below the last graph so no graph samples are
    # hidden under the fixed ruler at the bottom of the right viewport.
    panel._window_ruler_spacer = QGraphicsWidget()
    panel.glw.addItem(panel._window_ruler_spacer, row=len(panel._window_specs), col=1)


def clear_renderers(panel):
    panel.glw.setMinimumHeight(0)
    for grip in panel._window_grips:
        grip.hide()
        grip.deleteLater()
    panel._window_grips.clear()
    panel._window_heights.clear()
    for view, callback in panel._window_time_hooks:
        view.sigXRangeChanged.disconnect(callback)
    panel._window_time_hooks.clear()
    for row in range(len(panel._window_widgets)+1):
        panel.glw.ci.layout.setRowMinimumHeight(row, 0)
        panel.glw.ci.layout.setRowMaximumHeight(row, 16777215)
        panel.glw.ci.layout.setRowPreferredHeight(row, -1)
        panel.glw.ci.layout.setRowStretchFactor(row, 1)
    panel.glw.ci.layout.setColumnMinimumWidth(0, 0)
    panel.glw.ci.layout.setColumnMaximumWidth(0, 16777215)
    panel.glw.ci.layout.setColumnPreferredWidth(0, -1)
    for view, callback in panel._window_resize_hooks:
        view.sigResized.disconnect(callback)
    panel._window_resize_hooks.clear()
    for axis, view in panel._window_extra_axes:
        axis.unlinkFromView()
        view.setXLink(None)
        if axis.scene() is not None:
            axis.scene().removeItem(axis)
        if view.scene() is not None:
            view.scene().removeItem(view)
    panel._window_extra_axes.clear()
    panel._window_widgets.clear()
    panel._window_ruler_spacer = None


def axes_for_window(panel, plot, window, keys, width):
    plot.getAxis('left').setWidth(width)
    visible = [key for key in keys if panel._items[key].visible]
    views = {key: (plot.vb, plot.getAxis('left')) for key in keys}
    if window['axis_mode'] == 'individual':
        extra = []
        for index, key in enumerate(visible[1:], 1):
            plotted = panel._items[key]
            view = pg.ViewBox(enableMenu=False)
            axis = observation_style.ValueAxis('left')
            axis.setWidth(observation_style.AXIS_WIDTH)
            axis.setZValue(20)
            axis.setLabel(plotted.series.unit or '', color=plotted.color)
            axis.setToolTip(f'{plotted.series.signal_name}\n{plotted.series.unit or "无单位"}')
            axis.setPen(pg.mkPen(plotted.color))
            axis.setTextPen(pg.mkPen(plotted.color))
            plot.scene().addItem(view)
            plot.scene().addItem(axis)
            axis.linkToView(view)
            view.enableAutoRange(x=False)
            views[key] = view, axis
            panel._window_extra_axes.append((axis, view))
            extra.append((axis, view, index))
        def geometry():
            rect = plot.vb.sceneBoundingRect()
            bounds = list(plot.vb.viewRange()[0])
            was_syncing = panel._window_time_syncing
            panel._window_time_syncing = True
            try:
                for axis, view, index in extra:
                    # ViewBox resizing emits range changes before its final
                    # geometry is known. Those are not horizontal user zooms.
                    view.setGeometry(rect)
                    view.setXRange(*bounds, padding=0)
                    width = observation_style.AXIS_WIDTH
                    axis.setGeometry(QRectF(rect.left() - (index+1)*width, rect.top(), width, rect.height()))
            finally:
                panel._window_time_syncing = was_syncing
        plot.vb.sigResized.connect(geometry)
        panel._window_resize_hooks.append((plot.vb, geometry))
        geometry()
    return views


def link_times(panel):
    views = [plot.vb for plot in panel._stacked_plots] + [view for _axis, view in panel._window_extra_axes]
    def changed(source, bounds):
        if panel._window_time_syncing:
            return
        panel._window_time_revision += 1
        panel._window_time_syncing = True
        try:
            for view in views:
                if view is not source:
                    view.setXRange(*bounds, padding=0)
        finally:
            panel._window_time_syncing = False
        panel.window_ruler.update()
    for view in views:
        view.enableAutoRange(x=False)
        view.sigXRangeChanged.connect(changed)
        panel._window_time_hooks.append((view, changed))


def fitted_heights(weights, available):
    """Proportional allocation with a lower bound and no rounding remainder."""
    if not weights:
        return []
    total = max(round(available), len(weights)*MIN_WINDOW_HEIGHT)
    free, remaining = set(range(len(weights))), total
    result = [0]*len(weights)
    while free:
        weight_sum = sum(weights[index] for index in free)
        small = [index for index in free
                 if remaining*weights[index]/weight_sum < MIN_WINDOW_HEIGHT]
        if not small:
            exact = {index: remaining*weights[index]/weight_sum for index in free}
            for index in free:
                result[index] = int(exact[index])
            extra = remaining-sum(result[index] for index in free)
            for index in sorted(free, key=lambda i: (-(exact[i]-result[i]), i))[:extra]:
                result[index] += 1
            break
        for index in small:
            result[index] = MIN_WINDOW_HEIGHT
            free.remove(index)
            remaining -= MIN_WINDOW_HEIGHT
    return result


def position_grips(panel):
    if not panel._windows_mode:
        return
    width = panel.glw.viewport().width()
    for index, grip in enumerate(panel._window_grips):
        if index >= len(panel._stacked_plots):
            continue
        bottom = panel._stacked_plots[index].geometry().bottom()
        y = panel.glw.mapFromScene(QPointF(0, bottom)).y()
        grip.setGeometry(0, y-grip.THICKNESS//2, width, grip.THICKNESS)
        grip.raise_()
        grip.show()
    position_ruler(panel)


def position_ruler(panel):
    if not panel._windows_mode or not panel._window_widgets:
        return
    viewport = panel._stacked_scroll.viewport()
    widget = next(iter(panel._window_widgets.values()))[0]
    proxy = widget.graphicsProxyWidget()
    edge = panel.glw.mapFromScene(QPointF(proxy.sceneBoundingRect().right(), 0))
    left = viewport.mapFromGlobal(panel.glw.viewport().mapToGlobal(edge)).x()
    left = max(0, min(viewport.width(), left))
    panel.window_ruler.setGeometry(left, viewport.height()-RULER_HEIGHT,
                                   viewport.width()-left, RULER_HEIGHT)
    panel.window_ruler.raise_()


def arrange(panel):
    if panel._window_arranging:
        return
    panel._window_arranging = True
    try:
        layout = panel.glw.ci.layout
        if not panel._windows_mode:
            panel.glw.setMinimumHeight(0)
            layout.setContentsMargins(*panel._stacked_layout_margins)
            layout.setHorizontalSpacing(panel._stacked_layout_spacing[0])
            layout.setVerticalSpacing(panel._stacked_layout_spacing[1])
            return
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        heights = fitted_heights([window['height'] for window in panel._window_specs],
                                 panel._stacked_scroll.viewport().height()-RULER_HEIGHT)
        panel._window_heights = heights
        panel.glw.setMinimumHeight(sum(heights)+RULER_HEIGHT)
        for row, height in enumerate(heights):
            layout.setRowFixedHeight(row, height)
        layout.setRowFixedHeight(len(heights), RULER_HEIGHT)
        layout.activate()
        while len(panel._window_grips) < max(0, len(heights)-1):
            panel._window_grips.append(HeightGrip(panel, len(panel._window_grips)))
        position_grips(panel)
        panel.window_ruler.update()
    finally:
        panel._window_arranging = False


def refresh_values(panel):
    for _widget, tree in panel._window_widgets.values():
        tree.setColumnHidden(1, not panel._cursor1_enabled)
        tree.setColumnHidden(2, not panel._cursor2_enabled)
        for key, item in tree.rows.items():
            row = panel._row_lookup.get(key)
            if row is None:
                continue
            for column in (1, 2):
                cell = panel.table.item(row, column+1)
                item.setText(column, cell.text() if cell else '')
                item.setToolTip(column, cell.toolTip() if cell else '')
        for column, line in ((1, panel.v_line), (2, panel.v_line2)):
            tree.headerItem().setToolTip(column, f'C{column}  t = {line.value():.6f} s')


def refresh_signal_style(panel, key):
    plotted = panel._items.get(key)
    if plotted is None or plotted.window_id not in panel._window_widgets:
        return
    tree = panel._window_widgets[plotted.window_id][1]
    item = tree.rows.get(key)
    if item is None:
        return
    blocked = tree.blockSignals(True)
    try:
        muted = bool(plotted.unavailable_reason or not plotted.visible)
        item.setForeground(0, QBrush(QColor(observation_style.MUTED if muted else observation_style.TEXT)))
        item.setIcon(0, observation_style.swatch(observation_style.MUTED if muted else plotted.color,
                                               plotted.line_style))
    finally:
        tree.blockSignals(blocked)
    window = next(window for window in panel._window_specs if window['id'] == plotted.window_id)
    if window['axis_mode'] == 'individual' and plotted.visible and plotted.axis is not None:
        plotted.axis.setLabel(plotted.series.unit or '', color=plotted.color)
        plotted.axis.setTextPen(pg.mkPen(plotted.color))
        plotted.axis.setPen(pg.mkPen(plotted.color))


def snapshot(panel):
    ensure(panel)
    windows = deepcopy(panel._window_specs)
    for window in windows:
        keys = [key for key, item in panel._items.items() if item.window_id == window['id']]
        window['keys'] = keys
        ranges = {}
        for key in keys:
            view = panel._items[key].view_box
            if view is not None:
                ranges[key] = list(view.viewRange()[1])
        window['y_ranges'] = ranges
    return windows


def apply_layout(panel, windows, mapping=None):
    mapping = mapping or {}
    panel._window_specs = [{k: deepcopy(window[k]) for k in ('id', 'name', 'axis_mode', 'height')} for window in windows]
    panel._window_y_restore = {}
    for window in windows:
        for old_key in window['keys']:
            key = mapping.get(old_key, old_key)
            if key in panel._items:
                panel._items[key].window_id = window['id']
                if old_key in window.get('y_ranges', {}):
                    panel._window_y_restore[key] = window['y_ranges'][old_key]
    ensure(panel)


def restore_y(panel):
    for key, bounds in panel._window_y_restore.items():
        item = panel._items.get(key)
        if item is not None and item.view_box is not None:
            item.view_box.setYRange(*bounds, padding=0)


def fit_individual(panel, visible_only=False):
    from gui.plot_widget import _value_range, _window_range
    for window in panel._window_specs:
        if window['axis_mode'] != 'individual':
            continue
        for item in panel._items.values():
            if item.window_id != window['id'] or not item.visible or item.view_box is None:
                continue
            series = item.series
            limits = (_window_range(series.timestamps, series.values, *item.view_box.viewRange()[0])
                      if visible_only else _value_range(series.values))
            if limits:
                item.view_box.setYRange(*limits, padding=.05)
