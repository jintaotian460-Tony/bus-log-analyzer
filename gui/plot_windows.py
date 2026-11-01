"""Window ownership, local signal lists and per-window axes on one timeline."""
from copy import deepcopy

import pyqtgraph as pg
from PySide6.QtCore import Qt, QRectF, QMimeData, QPoint, QPointF, QTimer
from PySide6.QtGui import QColor, QBrush, QDrag, QPainter, QPen
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QToolButton,
                              QComboBox, QTreeWidget, QTreeWidgetItem,
                              QGraphicsProxyWidget, QGraphicsWidget, QMenu, QInputDialog,
                              QAbstractItemView)

from gui.signal_tree import SignalTreeWidget
from gui.edge_tab import EdgeTab


MIN_WINDOW_HEIGHT = 120
RULER_HEIGHT = 44


class TimelineRuler(QWidget):
    """A fixed overlay on the right; the left list continues to the footer."""
    def __init__(self, panel):
        super().__init__(panel._stacked_scroll.viewport())
        self.panel = panel
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
                    painter.drawText(QRectF(x-45, 10, 90, 17), Qt.AlignmentFlag.AlignHCenter, label)
        painter.drawText(QRectF(left, 27, right-left, 16), Qt.AlignmentFlag.AlignHCenter, 'Time (seconds)')

    def wheelEvent(self, event):
        self.panel._driving_vb().scaleBy(x=.8 if event.angleDelta().y() > 0 else 1.25)
        event.accept()


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
        self.setToolTip('拖入信号添加到此窗；已绘制的信号会移动到此窗。右键可调整样式或移除。')
        self.setColumnWidth(0, 165)
        self.setColumnWidth(1, 75)
        self.setRootIsDecorated(False)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.menu)
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

    def menu(self, position):
        item = self.itemAt(position)
        if item is None:
            return
        item.setSelected(True)
        self.select()
        keys = self.panel.selected_keys()
        global_position = self.viewport().mapToGlobal(position)
        key = item.data(0, Qt.ItemDataRole.UserRole)
        if self.panel._items[key].unavailable_reason:
            panel = self.panel
            def open_menu():
                menu = QMenu(panel)
                menu.addAction('匹配到本日志信号…', lambda: panel.signalMatchRequested.emit(key))
                menu.addAction('移除此曲线', lambda: panel.remove_series(key))
                menu.exec(global_position)
            QTimer.singleShot(0, panel, open_menu)
            return
        QTimer.singleShot(0, self.panel, lambda: self.panel._show_signal_menu(keys, global_position))

    def startDrag(self, actions):
        mime = QMimeData()
        keys = [str(item.data(0, Qt.ItemDataRole.UserRole)) for item in self.selectedItems()]
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
        activate(self.panel, self.window['id'])
        keys = bytes(event.mimeData().data(SignalTreeWidget.MIME_TYPE)).decode('utf-8').splitlines()
        QTimer.singleShot(0, self.panel, lambda: self.panel.signalDropped.emit(keys))
        event.acceptProposedAction()


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
    for identifier, (widget, _tree, title) in panel._window_widgets.items():
        title.setChecked(identifier == window_id)


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


def make_list(panel, window, keys, row, last=False):
    widget = QWidget()
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    toolbar = QHBoxLayout()
    title = QToolButton()
    title.setText(window['name'])
    title.setCheckable(True)
    title.setChecked(window['id'] == panel._window_active_id)
    title.clicked.connect(lambda: activate(panel, window['id']))
    title.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    def rename(_position):
        name, ok = QInputDialog.getText(widget, '窗口名称', '名称', text=window['name'])
        if ok and name.strip():
            window['name'] = name.strip()
            title.setText(window['name'])
    title.customContextMenuRequested.connect(rename)
    title.setToolTip('点击设为添加信号的目标窗口；右键重命名')
    toolbar.addWidget(title)
    toolbar.addStretch()
    for text, tip, action in [('↑', '上移窗口', lambda: move_window(panel, window, -1)),
                              ('↓', '下移窗口', lambda: move_window(panel, window, 1)),
                              ('×', '关闭此窗口及其曲线', lambda: close_window(panel, window))]:
        button = QToolButton()
        button.setText(text)
        button.setToolTip(tip)
        button.clicked.connect(lambda _checked=False, action=action: QTimer.singleShot(0, panel, action))
        toolbar.addWidget(button)
    layout.addLayout(toolbar)
    axis_mode = QComboBox()
    axis_mode.addItem('全部共轴', 'shared')
    axis_mode.addItem('每条信号独立轴', 'individual')
    axis_mode.setCurrentIndex(1 if window['axis_mode'] == 'individual' else 0)
    def change_mode():
        panel._push_undo()
        window['axis_mode'] = axis_mode.currentData()
        panel._window_y_restore = {}
        def redraw():
            panel._rebuild_curves(preserve_selection=True, preserve_y_range=False)
            panel.fit_vertical()
        # Rebuilding removes this proxy widget. Wait until Qt has returned
        # from the combo/list event before disposing its sender.
        QTimer.singleShot(0, panel, redraw)
    axis_mode.currentIndexChanged.connect(change_mode)
    layout.addWidget(axis_mode)
    tree = WindowSignals(panel, window)
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
        color = '#888888' if plotted.unavailable_reason or not plotted.visible else plotted.color
        item.setForeground(0, QBrush(QColor(color)))
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
    panel._window_widgets[window['id']] = (widget, tree, title)
    panel.glw.ci.layout.setColumnFixedWidth(0, 330)


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
            axis = pg.AxisItem('left')
            axis.setLabel(plotted.series.signal_name, units=plotted.series.unit, color=plotted.color)
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
                    axis.setGeometry(QRectF(rect.left() - (index+1)*85, rect.top(), 85, rect.height()))
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
    for _widget, tree, _title in panel._window_widgets.values():
        tree.setColumnHidden(1, not panel._cursor1_enabled)
        tree.setColumnHidden(2, not panel._cursor2_enabled)
        for key, item in tree.rows.items():
            row = panel._row_lookup.get(key)
            if row is None:
                continue
            for column in (1, 2):
                cell = panel.table.item(row, column+1)
                item.setText(column, cell.text() if cell else '')


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
