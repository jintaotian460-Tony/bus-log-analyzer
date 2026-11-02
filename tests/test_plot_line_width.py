from copy import deepcopy

import numpy as np
import pytest
from PySide6.QtCore import Qt

from core.plot_style import LINE_WIDTHS, normalise_line_width, selected_line_width
from core.signal_store import SignalSeries, SignalStore
from core.analysis_templates import normalise_template
from gui import analysis_templates
from gui.main_window import MainWindow
from gui.plot_widget import PlotPanel


def series(name='A'):
    return SignalSeries(channel=1, message_name='Message', message_id=1,
                        signal_name=name, unit='V', timestamps=np.arange(101, dtype=float)/10,
                        values=np.sin(np.arange(101, dtype=float)))


@pytest.mark.parametrize('mode', ['normal', 'stacked', 'multi_axis', 'multistack', 'windows'])
def test_explicit_width_survives_points_selection_and_rebuild(qapp, mode):
    panel = PlotPanel()
    panel.resize(1000, 600)
    panel.show()
    try:
        first, second = series('A'), series('B')
        panel.add_series(first.key, first)
        if mode != 'normal':
            getattr(panel, f'set_{mode}')(True)
        if mode == 'windows':
            panel.new_window()
        panel.add_series(second.key, second)
        qapp.processEvents()
        for width in LINE_WIDTHS:
            panel.set_line_width(width)
            panel._restore_selection([first.key])
            for show in (True, False):
                panel.set_show_points(show)
                for key, item in panel._items.items():
                    # the explicit-width branch runs ahead of point mode, so
                    # selection thickens the curve in both point states
                    expected = (selected_line_width(width) if key == first.key
                                else width)
                    assert item.curve.opts['pen'].widthF() == expected, key
            assert np.shares_memory(panel._items[first.key].curve.xData, first.timestamps)
        panel.set_series_line_style(first.key, 'dash')
        panel.set_hide_lines(False)
        panel.redraw()
        assert all(item.curve.opts['pen'].widthF() ==
                   (selected_line_width(LINE_WIDTHS[-1]) if key == first.key
                    else LINE_WIDTHS[-1])
                   for key, item in panel._items.items())
        panel.set_show_points(True)
        panel.set_hide_lines(True)
        panel.set_line_width(1)
        assert panel._items[first.key].curve.opts['pen'].style() == Qt.PenStyle.NoPen
        panel.set_hide_lines(False)
        assert panel._items[first.key].curve.opts['pen'].widthF() == selected_line_width(1)
        panel.set_line_width(None)
        panel._restore_selection([])
        assert panel._items[first.key].curve.opts['pen'].widthF() == panel._POINTS_LINE_WIDTH
    finally:
        panel.close()
        qapp.processEvents()


def test_width_change_only_restyles_existing_buffers(qapp, monkeypatch):
    panel = PlotPanel()
    try:
        signal = series()
        panel.add_series(signal.key, signal)
        item = panel._items[signal.key]
        before = item.curve.xData, item.curve.yData
        def unexpected(*args, **kwargs):
            pytest.fail('Changing line width must not reload the signal buffers')
        monkeypatch.setattr(item.curve, 'setData', unexpected)
        panel.set_line_width(1)
        assert item.curve.xData is before[0] and item.curve.yData is before[1]
        assert item.curve.opts['pen'].widthF() == 1
    finally:
        panel.close()


def test_toolbar_overflow_configuration_and_template_keep_the_width(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args, **kwargs: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, 'question', lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    window = MainWindow('Line widths', 'test')
    monkeypatch.setattr(window, 'load_data', lambda: None)
    window._temporary_plot_config_path = tmp_path/'temporary.json'
    try:
        store = SignalStore()
        a = series()
        store.add_series_bulk(channel=1, message_name='Message', message_id=1,
                              signal_name='A', unit='V', timestamps=a.timestamps,
                              values=a.values, raw_values=[], has_labels=False)
        window.measurement_path = window.blf_path = 'current.csv'
        window._on_worker_finished(store)
        window.add_signal_to_plot(a.key)
        action = next(action for action in window.line_width_actions if action.data() == 1)
        action.trigger()
        assert window.plot_panel.line_width() == 1
        assert window.btn_line_width.text() == '线宽 1 px'
        config = window._configuration_dict()
        assert config['plot_line_width'] == 1
        template = analysis_templates.capture(window)
        window._set_line_width(4)
        assert analysis_templates.apply(window, template)
        assert window.plot_panel.line_width() == 1
        window._set_line_width(3)
        window._apply_configuration(config, str(tmp_path/'session.json'))
        assert window.plot_panel.line_width() == 1
        window._reset_for_new_measurement()
        assert window.plot_panel.line_width() == 1
        row = window.plot_button_row
        row.resize(100, row.sizeHint().height())
        row._fit()
        assert window.btn_line_width in row.overflowed_buttons()
        row._fill_menu()
        submenu = next(action.menu() for action in row.more_menu.actions()
                       if action.text().startswith('线宽'))
        assert submenu is window.btn_line_width.menu()
        next(action for action in submenu.actions() if action.data() == 2).trigger()
        assert window.plot_panel.line_width() == 2
        row._fill_menu()
        assert window.btn_line_width.menu().actions()
        invalid = deepcopy(template)
        invalid['presentation']['plot_line_width'] = float('nan')
        with pytest.raises(ValueError, match='line width'):
            normalise_template(invalid)
        # widths saved on the old 0.5/1.5 steps load onto the nearest step:
        # a whole template library was once rejected over this
        legacy = deepcopy(template)
        legacy['presentation']['plot_line_width'] = 0.5
        assert normalise_template(legacy)
        config.pop('plot_line_width')
        window._apply_configuration(config, str(tmp_path/'old.json'))
        assert window.plot_panel.line_width() is None
    finally:
        window.close()
        qapp.processEvents()


@pytest.mark.parametrize('value', [None, True, -1, 0, float('nan'), float('inf'), 'bad', [], {}])
def test_invalid_width_falls_back_to_automatic(value):
    assert normalise_line_width(value) is None


def test_legacy_saved_widths_load_onto_the_nearest_step():
    assert normalise_line_width(.5) == 1.0
    assert normalise_line_width(1.5) == 2.0


def test_selecting_a_row_highlights_its_curve_at_every_width(qapp):
    """The user-facing point of an explicit width: picking a row in the
    table must be visible on the plot — the selected curve thickens."""
    panel = PlotPanel()
    try:
        first, second = series('A'), series('B')
        panel.add_series(first.key, first)
        panel.add_series(second.key, second)
        qapp.processEvents()

        # automatic mode highlights by its own pair
        panel._restore_selection([second.key])
        assert panel._items[first.key].curve.opts['pen'].widthF() == panel._LINE_WIDTH
        assert panel._items[second.key].curve.opts['pen'].widthF() == panel._SELECTED_LINE_WIDTH

        # and so does every explicit width
        for width in LINE_WIDTHS:
            panel.set_line_width(width)
            assert panel._items[first.key].curve.opts['pen'].widthF() == width
            assert panel._items[second.key].curve.opts['pen'].widthF() == selected_line_width(width)

        # clearing the selection puts both back to the exact width
        panel._restore_selection([])
        for item in panel._items.values():
            assert item.curve.opts['pen'].widthF() == LINE_WIDTHS[-1]
    finally:
        panel.close()
        qapp.processEvents()
