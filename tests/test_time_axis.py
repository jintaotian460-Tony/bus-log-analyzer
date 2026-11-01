import numpy as np
import pytest

from core.signal_store import SignalSeries
from gui.time_axis import TimeAxis
from gui.plot_widget import PlotPanel
from gui.main_window import MainWindow
from PySide6.QtCore import Qt, QPointF, QEvent
from PySide6.QtGui import QMouseEvent
from gui.time_axis import TimePan


def test_time_labels_keep_seconds_and_milliseconds(qapp):
    axis = TimeAxis()
    assert axis.tickStrings([8, 9], 1, 1) == ['8s', '9s']
    assert axis.tickStrings([13, 13.01, 13.02], 1, .01) == ['13.00s', '13.01s', '13.02s']
    assert axis.tickStrings([-.00000001], 1, .01) == ['0.00s']
    assert axis.tickStrings([1.001], 1, .001) == ['1.001s']


def test_ticks_are_bounded_when_zoomed_out_and_ten_ms_when_zoomed_in(qapp):
    axis = TimeAxis(.01)
    levels = axis.tickValues(13, 14, 1000)
    assert levels[1][0] == .01
    combined = sorted(levels[0][1] + levels[1][1])
    assert np.diff(combined) == pytest.approx(np.full(len(combined)-1, .01))
    assert len(combined) == len(set(combined))
    levels = axis.tickValues(0, 86400 * 30, 1000)
    assert sum(len(values) for _, values in levels) < 1000
    assert levels[1][0] > .01


@pytest.mark.parametrize('mode', ['normal', 'stacked', 'multi_axis', 'multistack', 'windows'])
def test_ten_ms_zoom_shows_only_original_samples_in_every_layout(qapp, mode):
    panel = PlotPanel()
    panel.resize(1100, 600)
    panel.show()
    if mode == 'stacked':
        panel.set_stacked(True)
    elif mode == 'multi_axis':
        panel.set_multi_axis(True)
    elif mode == 'multistack':
        panel.set_multistack(True)
    elif mode == 'windows':
        panel.set_windows(True)
    timestamps = np.arange(5001, dtype=np.float64) / 100
    values = np.sin(timestamps)
    series = SignalSeries(channel=1, message_name='A', message_id=1, signal_name='Speed',
                          unit='rpm', timestamps=timestamps, values=values)
    panel.add_series(series.key, series)
    panel.set_show_points(True)
    qapp.processEvents()
    panel._update_adaptive_points()
    assert '稀疏显示' in panel.sampling_label.text()
    panel.zoom_to_time_ticks(.01)
    qapp.processEvents()
    panel._update_adaptive_points()
    vb = panel._driving_vb()
    low, high = vb.viewRange()[0]
    axis = (panel._stacked_plots[-1] if panel._stacked_mode else panel.plot.plotItem).getAxis('bottom')
    assert isinstance(axis, TimeAxis)
    assert axis.tickValues(low, high, vb.width())[1][0] == .01
    shown_x, shown_y = panel._items[series.key].scatter.getData()
    wanted = (timestamps >= low) & (timestamps <= high)
    assert np.array_equal(shown_x, timestamps[wanted])
    assert np.array_equal(shown_y, values[wanted])
    assert '全量' in panel.sampling_label.text()
    assert panel._sample_tooltip(panel._items[series.key], 1).endswith('10.000 ms')
    assert np.array_equal(series.timestamps, timestamps)
    panel.close()
    qapp.processEvents()


def test_samples_are_visible_by_default_and_ten_ms_has_a_named_control(qapp):
    window = MainWindow('Time details', 'test')
    assert window.btn_points.isChecked()
    assert window.plot_panel._show_points
    assert window.btn_points.text() == '采样点'
    assert window.btn_time_10ms.text() == '10 ms'
    window.close()
    qapp.processEvents()


def test_sample_tooltip_reports_the_real_adjacent_interval(qapp):
    panel = PlotPanel()
    try:
        timestamps = np.array([0.0, 0.011, 0.023], dtype=np.float64)
        series = SignalSeries(channel=1, message_name='A', message_id=1,
                              signal_name='Speed', unit='rpm',
                              timestamps=timestamps, values=np.arange(3.0))
        panel.add_series(series.key, series)
        assert panel._sample_tooltip(panel._items[series.key], 1).endswith(
            '距上一原始采样点 Δt = 11.000 ms')
    finally:
        panel.close()
        qapp.processEvents()


def test_pan_uses_the_press_range_and_returns_to_its_origin(qapp):
    panel = PlotPanel()
    try:
        view = panel.plot.plotItem.vb
        view.setRange(xRange=(10, 20), yRange=(-5, 5), padding=0)
        calls = []
        pan = TimePan(view, 200, 1000, lambda: calls.append(True))
        pan.move(300)
        assert view.viewRange()[0] == pytest.approx([9, 19])
        pan.move(400)
        assert view.viewRange()[0] == pytest.approx([8, 18])
        pan.move(200)
        assert view.viewRange()[0] == pytest.approx([10, 20])
        assert view.viewRange()[1] == pytest.approx([-5, 5])
        assert calls == [True]
    finally:
        panel.close()


@pytest.mark.parametrize('mode', ['normal', 'stacked', 'multi_axis', 'multistack', 'windows'])
def test_real_left_ruler_drag_pans_time_only(qapp, mode):
    from PySide6.QtWidgets import QApplication
    panel = PlotPanel()
    panel.resize(1200, 700)
    panel.show()
    try:
        for index, unit in enumerate(('V', 'rpm')):
            signal = SignalSeries(channel=1, message_name='A', message_id=1,
                                  signal_name=str(index), unit=unit,
                                  timestamps=np.arange(101, dtype=float),
                                  values=np.arange(101, dtype=float)*(index+1))
            if index == 0 and mode != 'normal':
                getattr(panel, f'set_{mode}')(True)
            if index and mode == 'windows':
                panel.new_window()
            panel.add_series(signal.key, signal)
        panel.set_cursor1_enabled(True)
        panel.set_cursor2_enabled(True)
        panel.move_cursor1(45)
        panel.move_cursor2(55)
        panel.set_rect_zoom(True)
        for _ in range(5):
            qapp.processEvents()
        panel._driving_vb().setXRange(30, 70, padding=0)
        for _ in range(5):
            qapp.processEvents()
        views = {item.view_box or panel.plot.plotItem.vb for item in panel._items.values()}
        if panel._stacked_mode:
            views = {plot.vb for plot in panel._stacked_plots}
        before_y = {view: list(view.viewRange()[1]) for view in views}
        calls = []
        panel.timePanStarted.connect(lambda: calls.append(True))
        view = panel._driving_vb()
        width = view.sceneBoundingRect().width()
        if mode == 'windows':
            target = panel.window_ruler
            start = QPointF(target.width()/2, 18)
        else:
            plot = panel._stacked_plots[-1] if panel._stacked_mode else panel.plot.plotItem
            axis = plot.getAxis('bottom')
            graph = panel.glw if panel._stacked_mode else panel.plot
            target = graph.viewport()
            # Grid-enabled axes include the whole plot in boundingRect();
            # press the actual tick/label strip below the data viewport.
            point = axis.mapToScene(QPointF(axis.size().width()/2, 12))
            start = QPointF(graph.mapFromScene(point))
        finish = start + QPointF(80, 8)
        def send(kind, point, button, buttons):
            global_point = QPointF(target.mapToGlobal(point.toPoint()))
            event = QMouseEvent(kind, point, global_point, button, buttons, Qt.KeyboardModifier.NoModifier)
            QApplication.sendEvent(target, event)
            qapp.processEvents()
        send(QEvent.Type.MouseButtonPress, start, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
        # Two moves exercise pyqtgraph's native drag-start routing too.
        send(QEvent.Type.MouseMove, start+QPointF(25, 3), Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton)
        send(QEvent.Type.MouseMove, finish, Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton)
        send(QEvent.Type.MouseButtonRelease, finish, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)
        expected = [30-80*40/width, 70-80*40/width]
        assert view.viewRange()[0] == pytest.approx(expected, abs=.12)
        for other in views:
            assert other.viewRange()[0] == pytest.approx(expected, abs=.12)
            assert other.viewRange()[1] == pytest.approx(before_y[other])
        assert panel.v_line.value() == pytest.approx(45)
        assert panel.v_line2.value() == pytest.approx(55)
        assert calls == [True]
    finally:
        panel.close()
        qapp.processEvents()


def test_ruler_pan_releases_playback_follow_view(qapp):
    window = MainWindow('Manual pan', 'test')
    try:
        window.playback.follow.setChecked(True)
        view = window.plot_panel.plot.plotItem.vb
        pan = TimePan(view, 0, 100, window.plot_panel.begin_time_pan)
        pan.move(20)
        assert not window.playback.follow.isChecked()
    finally:
        window.close()
        qapp.processEvents()
