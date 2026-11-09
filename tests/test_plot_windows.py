import numpy as np
import pytest
from PySide6.QtCore import QPointF

from core.signal_store import SignalSeries
from gui.plot_widget import PlotPanel
from gui import plot_windows


def series(name, unit='V', value=1):
    return SignalSeries(channel=1, message_name='Message', message_id=1, signal_name=name,
                        unit=unit, timestamps=np.arange(101, dtype=float)/10,
                        values=np.arange(101, dtype=float)*value)


@pytest.fixture
def windows(qapp):
    panel = PlotPanel()
    panel.resize(1350, 650)
    panel.show()
    panel.set_windows(True)
    yield panel
    panel.close()
    qapp.processEvents()


def test_list_divider_drags_all_signal_lists_and_tracks_the_ruler(windows, qapp):
    from PySide6.QtCore import QPoint
    from tests.test_edge_tab import drag

    panel = windows
    grip = panel.window_list_grip
    settle(qapp)
    assert grip.isVisible()
    empty_list = panel._window_widgets[0][1]
    initial = empty_list.width()
    drag(grip, dx=-72)
    settle(qapp)
    assert empty_list.width() == initial - 72

    for index in range(3):
        if index:
            panel.new_window()
        signal = series(f'WidthTest_{index}')
        panel.add_series(signal.key, signal)
    settle(qapp)
    trees = [tree for _widget, tree in panel._window_widgets.values()]
    assert {tree.width() for tree in trees} == {initial - 72}
    drag(grip, dx=104)
    settle(qapp)
    assert {tree.width() for tree in trees} == {initial + 32}
    assert all(tree.topLevelItemCount() == 1 for tree in trees)
    edge = trees[0].mapToGlobal(QPoint(trees[0].width(), 0)).x()
    assert abs(grip.mapToGlobal(QPoint(grip.THICKNESS//2, 0)).x() - edge) <= 1
    assert abs(panel.window_ruler.mapToGlobal(QPoint()).x() - edge) <= 1

    drag(grip, dx=-3000)
    settle(qapp)
    assert {tree.width() for tree in trees} == {grip.MIN_WIDTH}
    panel.set_stacked(True)
    settle(qapp)
    assert grip.isHidden()
    panel.set_windows(True)
    settle(qapp)
    assert {tree.width() for _widget, tree in panel._window_widgets.values()} == {grip.MIN_WIDTH}


@pytest.mark.parametrize(('fraction', 'delta', 'factor'), [(.2, 120, .8), (.75, -120, 1.25)])
def test_timeline_wheel_anchors_time_under_the_pointer(windows, qapp, fraction, delta, factor):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QWheelEvent
    from tests.test_edge_tab import drag

    panel = windows
    for index in range(2):
        if index:
            panel.new_window()
        signal = series(f'ZoomTest_{index}', value=index + 1)
        panel.add_series(signal.key, signal)
    settle(qapp)
    drag(panel.window_list_grip, dx=72)
    settle(qapp)

    ruler = panel.window_ruler
    graph = panel.glw
    view = panel._driving_vb()
    view.setXRange(20, 100, padding=0)
    settle(qapp)
    y_ranges = [plot.vb.viewRange()[1] for plot in panel._stacked_plots]
    anchor = 20 + 80 * fraction

    def pointer_x():
        scene = view.mapViewToScene(QPointF(anchor, 0))
        viewport = graph.viewportTransform().map(scene)
        return ruler.mapFromGlobal(graph.viewport().mapToGlobal(viewport)).x()

    position = QPointF(pointer_x(), 18)
    assert ruler.rect().contains(position.toPoint())
    event = QWheelEvent(position, ruler.mapToGlobal(position), QPoint(), QPoint(0, delta),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    qapp.sendEvent(ruler, event)
    settle(qapp)

    assert event.isAccepted()
    assert pointer_x() == pytest.approx(position.x(), abs=.01)
    bounds = [anchor + (edge - anchor) * factor for edge in (20, 100)]
    for plot, y_range in zip(panel._stacked_plots, y_ranges):
        x, y = plot.vb.viewRange()
        assert x == pytest.approx(bounds)
        assert y == pytest.approx(y_range)


def test_each_window_has_its_own_list_and_one_source_of_data(windows, qapp):
    panel = windows
    assert panel.table_panel.isHidden()
    a, b = series('A'), series('B')
    panel.add_series(a.key, a)
    panel.new_window()
    panel.add_series(b.key, b)
    qapp.processEvents()
    assert len(panel._stacked_plots) == 2
    assert list(panel._window_widgets[0][1].rows) == [a.key]
    assert list(panel._window_widgets[1][1].rows) == [b.key]
    assert panel._items[a.key].series is a
    assert panel._items[b.key].series is b
    assert panel.window_ruler.isVisible()
    assert all(not plot.getAxis('bottom').style['showValues'] for plot in panel._stacked_plots)
    plot_windows.activate(panel, 1)
    panel.add_series(a.key, a)
    assert panel._stacked_row_keys == [[], [a.key, b.key]]


@pytest.mark.parametrize('count', [1, 3, 6])
def test_only_the_top_window_reserves_space_for_signal_column_headings(windows, qapp, count):
    from PySide6.QtCore import QPoint
    panel = windows
    signals = [series(f'Signal_{index}') for index in range(count)]
    for index, signal in enumerate(signals):
        if index:
            panel.new_window()
        panel.add_series(signal.key, signal)
    panel.set_cursor1_enabled(True)
    panel.set_cursor2_enabled(True)
    panel.move_cursor1(3)
    panel.move_cursor2(4)
    settle(qapp)
    for index, spec in enumerate(panel.window_layout()):
        tree = panel._window_widgets[spec['id']][1]
        assert tree.isHeaderHidden() == (index > 0)
        assert tree.header().isVisible() == (index == 0)
        viewport_top = tree.viewport().mapTo(tree, QPoint()).y()
        assert viewport_top == (tree.header().height() if index == 0 else 0)
        item = tree.rows[signals[index].key]
        assert tree.visualItemRect(item).top() == 0
        assert item.text(1) == '30.000' and item.text(2) == '40.000'
        assert panel._items[signals[index].key].series is signals[index]


def test_single_signal_header_follows_window_order_close_undo_and_restore(windows, qapp):
    panel = windows
    for index in range(3):
        if index:
            panel.new_window()
        signal = series(f'Signal_{index}')
        panel.add_series(signal.key, signal)
    settle(qapp)
    originals = {key: item.series for key, item in panel._items.items()}

    def check_top(identifier):
        settle(qapp)
        visible = [spec['id'] for spec in panel.window_layout()
                   if panel._window_widgets[spec['id']][1].header().isVisible()]
        assert visible == [identifier]

    check_top(0)
    plot_windows.move_window(panel, panel._window_specs[0], 1)
    check_top(1)
    saved = panel.window_layout()
    plot_windows.close_window(panel, panel._window_specs[0])
    check_top(0)
    panel.undo()
    check_top(1)
    panel.apply_window_layout(saved)
    panel.resize(1100, 700)
    check_top(1)
    assert all(panel._items[key].series is value for key, value in originals.items())
    panel.set_stacked(True)
    panel.set_windows(True)
    check_top(1)


def test_x_range_cursors_and_readouts_follow_in_both_directions(windows, qapp):
    panel = windows
    a, b = series('A'), series('B')
    panel.add_series(a.key, a)
    panel.new_window()
    panel.add_series(b.key, b)
    panel.set_cursor1_enabled(True)
    panel.set_cursor2_enabled(True)
    qapp.processEvents()
    panel._stacked_plots[1].setXRange(2, 4, padding=0)
    qapp.processEvents()
    assert panel._stacked_plots[0].vb.viewRange()[0] == pytest.approx([2, 4], abs=.03)
    panel._stacked_c1_lines[1].setPos(3)
    panel._stacked_c2_lines[0].setPos(3.5)
    assert panel.v_line.value() == pytest.approx(3)
    assert panel._stacked_c1_lines[0].value() == pytest.approx(3)
    assert panel._stacked_c2_lines[1].value() == pytest.approx(3.5)
    assert panel._window_widgets[0][1].rows[a.key].text(1) == '30.000'
    assert panel._window_widgets[1][1].rows[b.key].text(2) == '35.000'


@pytest.mark.parametrize('column', [1, 2])
def test_cursor_move_repaints_every_embedded_signal_list(windows, qapp, column):
    from PySide6.QtWidgets import QStyledItemDelegate

    class PaintedReadout(QStyledItemDelegate):
        value = None

        def paint(self, painter, option, index):
            if index.column() == column:
                self.value = index.data()
            super().paint(painter, option, index)

    panel = windows
    for index in range(5):
        if index:
            panel.new_window()
        signal = series(f'Readout_{index}', value=index+1)
        panel.add_series(signal.key, signal)
    panel.set_cursor1_enabled(True)
    panel.set_cursor2_enabled(True)
    move = panel.move_cursor1 if column == 1 else panel.move_cursor2
    lines = panel._stacked_c1_lines if column == 1 else panel._stacked_c2_lines
    move(2)
    settle(qapp)

    delegates = []
    for _, tree in panel._window_widgets.values():
        delegate = PaintedReadout(tree)
        tree.setItemDelegate(delegate)
        delegates.append(delegate)
    panel.glw.viewport().update()
    settle(qapp)
    assert [delegate.value for delegate in delegates] == [f'{20*i:.3f}' for i in range(1, 6)]

    move(5)
    settle(qapp)
    assert [delegate.value for delegate in delegates] == [f'{50*i:.3f}' for i in range(1, 6)]
    lines[3].setPos(8)
    settle(qapp)
    assert [delegate.value for delegate in delegates] == [f'{80*i:.3f}' for i in range(1, 6)]


@pytest.mark.parametrize('column', [1, 2])
def test_cursor_toggle_hides_its_readouts_without_another_move(windows, qapp, column):
    panel = windows
    a, b = series('A'), series('B')
    panel.add_series(a.key, a)
    panel.new_window()
    panel.add_series(b.key, b)
    panel.set_cursor1_enabled(True)
    panel.set_cursor2_enabled(True)
    settle(qapp)
    toggle = panel.set_cursor1_enabled if column == 1 else panel.set_cursor2_enabled
    toggle(False)
    settle(qapp)
    for _, tree in panel._window_widgets.values():
        assert tree.isColumnHidden(column)
        assert not tree.isColumnHidden(3-column)


def test_whole_window_axes_switch_and_y_ranges_are_independent(windows, qapp):
    panel = windows
    a, b = series('A', 'V', 1), series('B', 'rpm', 100)
    panel.add_series(a.key, a)
    panel.add_series(b.key, b)
    assert panel._items[a.key].view_box is panel._items[b.key].view_box
    plot_windows.change_axis_mode(panel, 0, 'individual')
    qapp.processEvents()
    assert panel._window_specs[0]['axis_mode'] == 'individual'
    assert panel._items[a.key].view_box is not panel._items[b.key].view_box
    panel.fit_to_window()
    assert panel._items[b.key].view_box.viewRange()[1][1] > 1000
    assert panel._items[a.key].view_box.viewRange()[1][1] < 200
    panel._stacked_plots[0].setXRange(1, 2, padding=0)
    panel.fit_vertical()
    assert panel._items[a.key].view_box.viewRange()[1][1] < 30


def test_legends_use_color_samples_and_keep_names_and_cursor_values_readable(windows, qapp):
    from PySide6.QtCore import Qt
    from gui import observation_style
    panel = windows
    signal = series('Long_signal_name_that_used_to_fill_the_vertical_axis')
    panel.add_series(signal.key, signal, color='#ffff00')
    panel.set_cursor1_enabled(True)
    panel.set_cursor2_enabled(True)
    panel.move_cursor1(3)
    panel.move_cursor2(4)
    settle(qapp)
    tree = panel._window_widgets[0][1]
    item = tree.rows[signal.key]
    assert item.text(0) == signal.signal_name
    assert item.foreground(0).color().name() == observation_style.TEXT
    assert not item.icon(0).isNull()
    assert item.text(1) == '30.000' and item.text(2) == '40.000'
    assert item.textAlignment(1) & Qt.AlignmentFlag.AlignRight
    assert '3.000000' in tree.headerItem().toolTip(1)
    assert panel._items[signal.key].axis.labelText == 'V'
    assert signal.signal_name in panel._items[signal.key].axis.toolTip()
    old_icon = item.icon(0).cacheKey()
    panel.set_series_color(signal.key, '#ff0000')
    assert item.icon(0).cacheKey() != old_icon
    curve = panel._items[signal.key].curve
    data = curve.xData, curve.yData
    old_icon = item.icon(0).cacheKey()
    panel.set_series_line_style(signal.key, 'dash')
    assert item.icon(0).cacheKey() != old_icon
    assert curve.xData is data[0] and curve.yData is data[1]
    item.setCheckState(0, Qt.CheckState.Unchecked)
    settle(qapp)
    assert not panel._items[signal.key].curve.isVisible()
    assert panel._window_widgets[0][1].rows[signal.key].foreground(0).color().name() == observation_style.MUTED
    assert panel._items[signal.key].series is signal


def test_window_axis_labels_follow_units_and_background_without_changing_data(windows, qapp):
    from gui.observation_style import AXIS_WIDTH
    panel = windows
    a, b = series('A', 'V'), series('B', 'rpm', 100)
    panel.add_series(a.key, a)
    panel.add_series(b.key, b)
    assert panel._items[a.key].axis.labelText == 'V / rpm'
    panel._window_specs[0]['axis_mode'] = 'individual'
    panel.redraw()
    settle(qapp)
    assert panel._items[a.key].axis.labelText == 'V'
    assert panel._items[b.key].axis.labelText == 'rpm'
    assert panel._items[b.key].axis.width() == AXIS_WIDTH
    panel.set_background_color('#ffffff')
    settle(qapp)
    for plot in panel._stacked_plots:
        assert plot.getAxis('bottom').observation_background == '#ffffff'
        assert plot.getAxis('bottom').textPen().color().name() == '#000000'
    assert panel._items[a.key].series is a and panel._items[b.key].series is b


def test_window_order_height_close_and_undo(windows, qapp):
    panel = windows
    a, b = series('A'), series('B')
    panel.add_series(a.key, a)
    panel.new_window()
    panel.add_series(b.key, b)
    second = panel._window_specs[1]
    second['height'] = 350
    plot_windows.move_window(panel, second, -1)
    assert panel._stacked_row_keys == [[b.key], [a.key]]
    assert panel.window_layout()[0]['height'] == 350
    plot_windows.close_window(panel, second)
    assert panel.plotted_keys() == [a.key]
    panel.undo()
    assert panel.plotted_keys() == [a.key, b.key]
    assert len(panel._window_specs) == 2
    assert panel._window_specs[0]['id'] == second['id']


def test_switching_to_legacy_modes_releases_extra_views_and_proxy_widgets(windows, qapp):
    panel = windows
    a, b = series('A'), series('B')
    panel.add_series(a.key, a)
    panel.add_series(b.key, b)
    panel._window_specs[0]['axis_mode'] = 'individual'
    panel.redraw()
    assert len(panel._window_extra_axes) == 1
    panel.set_stacked(True)
    qapp.processEvents()
    assert not panel._windows_mode
    assert not panel._window_extra_axes
    assert not panel._window_widgets
    assert len(panel._stacked_plots) == 2


def settle(qapp):
    from PySide6.QtTest import QTest
    for _ in range(4):
        qapp.processEvents()
        QTest.qWait(10)


def assert_tiles(panel):
    from PySide6.QtCore import QPoint
    plots = panel._stacked_plots
    assert plots[0].geometry().top() == pytest.approx(0, abs=1)
    for first, second in zip(plots, plots[1:]):
        assert first.geometry().bottom() == pytest.approx(second.geometry().top(), abs=.01)
    assert plots[-1].geometry().bottom()+plot_windows.RULER_HEIGHT == pytest.approx(panel.glw.viewport().height(), abs=1)
    footer_top = panel._cursor_line.mapToGlobal(QPoint()).y()
    assert panel.window_ruler.mapToGlobal(QPoint(0, panel.window_ruler.height())).y() == footer_top
    if panel._stacked_scroll.verticalScrollBar().value() == panel._stacked_scroll.verticalScrollBar().maximum():
        tree = panel._window_widgets[panel._window_specs[-1]['id']][1]
        assert tree.mapToGlobal(QPoint(0, tree.height())).y() == pytest.approx(footer_top, abs=1)
    assert len(panel._window_grips) == len(plots)-1


@pytest.mark.parametrize('size', [(1350, 650), (1450, 950), (1100, 510)])
def test_windows_tile_to_ruler_after_resize_without_rewriting_ratios(windows, qapp, size):
    panel = windows
    panel.new_window()
    panel.new_window()
    for spec, height in zip(panel._window_specs, (160, 320, 160)):
        spec['height'] = height
    panel.resize(*size)
    settle(qapp)
    assert_tiles(panel)
    assert [spec['height'] for spec in panel._window_specs] == [160, 320, 160]
    assert sum(panel._window_heights)+plot_windows.RULER_HEIGHT == panel._stacked_scroll.viewport().height()
    assert min(panel._window_heights) >= plot_windows.MIN_WINDOW_HEIGHT
    assert panel._stacked_scroll.verticalScrollBar().maximum() == 0
    # The only horizontal space between a list and its graph is the Y axis.
    for index, plot in enumerate(panel._stacked_plots):
        widget = panel._window_widgets[panel._window_specs[index]['id']][0]
        proxy = widget.graphicsProxyWidget()
        assert proxy.geometry().right() == pytest.approx(plot.geometry().left(), abs=.01)


def test_overflow_preserves_readable_rows_and_bottom_ruler(windows, qapp):
    panel = windows
    for _ in range(7):
        panel.new_window()
    settle(qapp)
    assert min(panel._window_heights) >= plot_windows.MIN_WINDOW_HEIGHT
    bar = panel._stacked_scroll.verticalScrollBar()
    assert bar.maximum() > 0
    ruler_y = panel.window_ruler.mapToGlobal(panel.window_ruler.rect().topLeft()).y()
    bar.setValue(bar.maximum())
    settle(qapp)
    last = panel.glw.mapFromScene(panel._stacked_plots[-1].geometry().bottomLeft())
    last = panel._stacked_scroll.viewport().mapFromGlobal(panel.glw.viewport().mapToGlobal(last))
    assert last.y()+plot_windows.RULER_HEIGHT == pytest.approx(panel._stacked_scroll.viewport().height(), abs=1)
    assert panel.window_ruler.mapToGlobal(panel.window_ruler.rect().topLeft()).y() == ruler_y
    assert_tiles(panel)
    # A shrinking stack also releases its old minimum height and scrollbar.
    for spec in list(panel._window_specs[2:]):
        plot_windows.close_window(panel, spec)
    settle(qapp)
    assert panel._stacked_scroll.verticalScrollBar().maximum() == 0
    assert_tiles(panel)


def test_divider_drag_changes_only_neighbours_and_undo_restores(windows, qapp):
    from PySide6.QtCore import QPointF, QEvent, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    panel = windows
    panel.resize(1350, 950)
    a = series('A')
    panel.add_series(a.key, a)
    panel.new_window()
    panel.new_window()
    settle(qapp)
    panel._driving_vb().setXRange(2, 4, padding=0)
    panel.set_cursor1_enabled(True)
    panel.v_line.setValue(3)
    settle(qapp)
    before = list(panel._window_heights)
    saved = [spec['height'] for spec in panel._window_specs]
    grip = panel._window_grips[0]
    QTest.mousePress(grip, Qt.MouseButton.LeftButton)
    local = QPointF(grip.rect().center())
    global_pos = QPointF(grip.mapToGlobal(grip.rect().center()))+QPointF(0, 40)
    QApplication.sendEvent(grip, QMouseEvent(QEvent.Type.MouseMove, local, global_pos,
                           Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    QTest.mouseRelease(grip, Qt.MouseButton.LeftButton)
    settle(qapp)
    assert panel._window_heights == [before[0]+40, before[1]-40, before[2]]
    assert_tiles(panel)
    assert panel._items[a.key].series is a
    assert panel._driving_vb().viewRange()[0] == pytest.approx([2, 4], abs=.02)
    assert panel.v_line.value() == 3
    panel.undo()
    settle(qapp)
    assert [spec['height'] for spec in panel._window_specs] == saved
    assert_tiles(panel)


@pytest.mark.parametrize('direction', [-1, 1])
def test_height_drag_allows_thin_and_scrollable_rows_and_restores_layout(windows, qapp, direction):
    from core.analysis_templates import normalise_windows
    from tests.test_edge_tab import drag

    panel = windows
    for index in range(3):
        if index:
            panel.new_window()
        signal = series(f'HeightTest_{index}')
        panel.add_series(signal.key, signal)
    settle(qapp)
    before = panel._window_heights[:]
    grip = panel._window_grips[0]
    thin, tall = (0, 1) if direction < 0 else (1, 0)

    drag(grip, dy=direction*2000)
    settle(qapp)
    assert panel._window_heights[thin] == grip.THICKNESS
    assert panel._window_heights[tall] == before[tall] + 2000
    assert panel._window_heights[2] == before[2]
    assert panel._stacked_scroll.verticalScrollBar().maximum() > 0
    for index in (thin, tall):
        widget, tree = panel._window_widgets[index]
        plot = panel._stacked_plots[index]
        proxy = widget.graphicsProxyWidget()
        assert proxy.geometry().top() == pytest.approx(plot.geometry().top(), abs=.01)
        assert tree.height() == widget.height() == panel._window_heights[index]
        assert plot.geometry().height() == panel._window_heights[index]
    assert_tiles(panel)

    saved = normalise_windows(panel.window_layout())
    panel.undo()
    settle(qapp)
    assert panel._window_heights == before
    panel.apply_window_layout(saved)
    settle(qapp)
    assert panel._window_heights == [window['height'] for window in saved]

    bar = panel._stacked_scroll.verticalScrollBar()
    grip = panel._window_grips[0]
    bar.setValue(max(0, grip.y() - panel._stacked_scroll.viewport().height()//2))
    settle(qapp)
    assert panel._stacked_scroll.viewport().rect().contains(
        grip.mapTo(panel._stacked_scroll.viewport(), grip.rect().center()))
    drag(grip, dy=-direction*50)
    settle(qapp)
    assert panel._window_heights[thin] == grip.THICKNESS + 50
    assert panel._window_heights[tall] == before[tall] + 1950
    assert_tiles(panel)


def test_proportions_and_extreme_ratios_respect_minimum(qapp):
    assert plot_windows.fitted_heights([160, 320, 160], 801) == [200, 401, 200]
    assert plot_windows.fitted_heights([120, 1600, 120], 500) == [120, 260, 120]
    assert plot_windows.fitted_heights([220]*8, 400) == [120]*8


def test_leaving_overflow_restores_legacy_layout_and_clears_handles(windows, qapp):
    panel = windows
    a = series('A')
    panel.add_series(a.key, a)
    for _ in range(7):
        panel.new_window()
    settle(qapp)
    assert panel._stacked_scroll.verticalScrollBar().maximum() > 0
    panel.set_stacked(True)
    settle(qapp)
    assert not panel._window_grips
    assert panel.glw.minimumHeight() == 0
    assert panel.glw.ci.layout.getContentsMargins() == panel._stacked_layout_margins
    assert not panel.window_ruler.isVisible()


def test_zoom_after_cursor_rebuild_supersedes_deferred_old_viewport(windows, qapp):
    panel = windows
    panel.add_series(series('A').key, series('A'))
    panel.new_window()
    settle(qapp)
    panel.set_cursor2_enabled(True)
    # Deliberately zoom before the queued rebuild restore runs.
    panel._stacked_plots[1].setXRange(2, 4, padding=0)
    settle(qapp)
    assert all(plot.vb.viewRange()[0] == pytest.approx([2, 4], abs=.001)
               for plot in panel._stacked_plots)


def test_independent_axes_resize_never_becomes_a_horizontal_zoom(windows, qapp):
    panel = windows
    for name, unit in [('A', 'V'), ('B', 'rpm')]:
        item = series(name, unit)
        panel.add_series(item.key, item)
    panel.new_window()
    panel._window_specs[0]['axis_mode'] = 'individual'
    panel.redraw()
    settle(qapp)
    panel._stacked_plots[1].setXRange(2, 4, padding=0)
    for width, height in [(1550, 900), (1100, 550), (1350, 650)]:
        panel.resize(width, height)
        settle(qapp)
        views = [p.vb for p in panel._stacked_plots]+[view for _axis, view in panel._window_extra_axes]
        assert all(view.viewRange()[0] == pytest.approx([2, 4], abs=.001) for view in views)


@pytest.mark.parametrize('count', [1, 2, 3])
def test_last_signal_list_reaches_the_cursor_information_bar(windows, qapp, count):
    from PySide6.QtCore import QPoint
    panel = windows
    for _ in range(count-1):
        panel.new_window()
    settle(qapp)
    assert panel.glw.isVisible() and panel.window_ruler.isVisible()
    tree = panel._window_widgets[panel._window_specs[-1]['id']][1]
    list_bottom = tree.mapToGlobal(QPoint(0, tree.height())).y()
    information_top = panel._cursor_line.mapToGlobal(QPoint()).y()
    assert list_bottom == pytest.approx(information_top, abs=1)
    # The actual tree occupies the old black strip, not a background patch.
    ruler_left = panel.window_ruler.mapToGlobal(QPoint()).x()
    list_right = tree.mapToGlobal(QPoint(tree.width(), 0)).x()
    assert ruler_left == pytest.approx(list_right, abs=1)


@pytest.mark.parametrize('background', ['#000000', '#353535'])
def test_old_black_strip_is_part_of_the_real_interactive_tree(windows, qapp, background):
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QColor, QPalette
    from PySide6.QtWidgets import QTreeWidgetItem
    panel = windows
    panel.new_window()
    panel.set_background_color(background)
    settle(qapp)
    tree = panel._window_widgets[panel._window_specs[-1]['id']][1]
    footer = panel._cursor_line.mapToGlobal(QPoint())
    target = QPoint(tree.mapToGlobal(QPoint()).x()+40, footer.y()-25)
    image_point = panel.mapFromGlobal(target)
    assert panel.grab().toImage().pixelColor(image_point) == tree.palette().color(QPalette.ColorRole.Base)
    assert panel.grab().toImage().pixelColor(image_point) != QColor(background)
    for number in range(100):
        tree.addTopLevelItem(QTreeWidgetItem([f'Test signal {number}', '1.000']))
    settle(qapp)
    local = tree.viewport().mapFromGlobal(target)
    assert tree.viewport().rect().contains(local)
    assert tree.itemAt(local) is not None


def test_bottom_alignment_survives_reorder_close_undo_and_table_view(windows, qapp):
    panel = windows
    panel.new_window()
    panel.new_window()
    settle(qapp)
    plot_windows.move_window(panel, panel._window_specs[-1], -1)
    settle(qapp)
    assert_tiles(panel)
    plot_windows.close_window(panel, panel._window_specs[-1])
    settle(qapp)
    assert_tiles(panel)
    panel.undo()
    settle(qapp)
    assert_tiles(panel)
    panel.set_table_view(True)
    settle(qapp)
    assert_tiles(panel)
    panel.set_table_view(False)
    settle(qapp)
    assert_tiles(panel)


def test_window_dividers_are_visible_full_width_drag_targets(windows, qapp):
    """The height grips exist to be found: shown at each boundary, spanning
    the viewport, cursor and tooltip saying what they do."""
    panel = windows
    a, b = series('A'), series('B')
    panel.add_series(a.key, a)
    panel.new_window()
    panel.add_series(b.key, b)
    qapp.processEvents()

    assert len(panel._window_grips) == len(panel._stacked_plots) - 1
    viewport = panel.glw.viewport()
    for index, grip in enumerate(panel._window_grips):
        assert grip.isVisible()
        assert grip.width() == viewport.width()
        bottom = panel._stacked_plots[index].geometry().bottom()
        expected = panel.glw.mapFromScene(QPointF(0, bottom)).y()
        assert grip.y() == pytest.approx(expected - grip.THICKNESS // 2, abs=1)
        assert '高度' in grip.toolTip()
