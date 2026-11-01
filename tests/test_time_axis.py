import numpy as np
import pytest

from core.signal_store import SignalSeries
from gui.time_axis import TimeAxis
from gui.plot_widget import PlotPanel
from gui.main_window import MainWindow


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
