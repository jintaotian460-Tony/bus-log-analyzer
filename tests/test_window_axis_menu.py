# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""Window axis modes are reached by right-click, not permanent header controls."""
from __future__ import annotations

import sys

import numpy as np
import pytest
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QMenu

from core.signal_store import SignalSeries
from gui.plot_widget import PlotPanel
from gui import plot_windows


def settle(qapp):
    for _ in range(6):
        qapp.processEvents()
        QTest.qWait(10)


@pytest.fixture(autouse=True)
def qt_callback_errors(qapp, monkeypatch):
    errors = []
    monkeypatch.setattr(sys, 'excepthook', lambda _type, error, _traceback: errors.append(error))
    yield
    settle(qapp)
    assert not errors, errors


@pytest.fixture
def windows(qapp):
    panel = PlotPanel()
    panel.resize(1350, 850)
    panel.show()
    panel.set_windows(True)
    for name, unit, factor in (('A', 'V', 1), ('B', 'rpm', 100)):
        timestamps = np.arange(101, dtype=float) / 10
        signal = SignalSeries(
            channel=1, message_name='Message', message_id=1,
            signal_name=name, unit=unit, timestamps=timestamps,
            values=np.arange(101, dtype=float) * factor)
        panel.add_series(signal.key, signal)
    panel.new_window()
    settle(qapp)
    panel.discard_undo_history()
    try:
        yield panel
    finally:
        panel.close()
        settle(qapp)


def open_tree_menu(qapp, tree, position):
    """Open the signal list's menu through the handler a right-click reaches.

    The lists live inside QGraphicsProxyWidgets: Qt synthesises the
    context-menu event only for presses that arrive through the embedding
    scene, which a directly posted event bypasses. menu() is the slot the
    customContextMenuRequested signal is connected to.
    """
    tree.menu(position)
    qapp.processEvents()


def open_title_menu(qapp, title, position):
    """A title's menu the same way: its own signal, its connected handler."""
    title.customContextMenuRequested.emit(position)
    qapp.processEvents()


def window_axis_menu(panel):
    """The panel's visible window menu, wherever the event loop shows it."""
    return next((menu for menu in panel.findChildren(QMenu)
                 if menu.isVisible()
                 and any(action.text() == '此窗口 Y 轴' for action in menu.actions())), None)


def choose_axis_mode(panel, qapp, request, mode, *, current, while_open=None):
    """Drive the popup from a timer while QMenu.exec() owns its nested loop."""
    outcome, errors = [], []
    opened = []
    timer = QTimer()
    timer.setInterval(10)
    deadline = QTimer()
    deadline.setSingleShot(True)

    def close_menus():
        for menu in list(panel.findChildren(QMenu)):
            menu.close()

    def fail(error):
        timer.stop()
        errors.append(error)
        close_menus()

    def drive():
        try:
            if outcome:
                if window_axis_menu(panel) is None:
                    timer.stop()
                return
            menu = window_axis_menu(panel)
            if menu is None:
                return
            if not opened:
                opened.append(menu)
                submenu = next(action.menu() for action in menu.actions()
                               if action.text() == '此窗口 Y 轴')
                actions = {action.text(): action for action in submenu.actions()}
                assert actions[current].isChecked()
                assert not actions['共轴' if current == '独立轴' else '独立轴'].isChecked()
                if while_open is not None:
                    while_open()
                entry = next(action for action in menu.actions() if action.menu() is submenu)
                QTest.mouseClick(menu, Qt.MouseButton.LeftButton,
                                 pos=menu.actionGeometry(entry).center())
                return
            submenu = next(action.menu() for action in opened[0].actions()
                           if action.text() == '此窗口 Y 轴')
            if not submenu.isVisible():
                return
            target = next(action for action in submenu.actions() if action.text() == mode)
            QTest.mouseClick(submenu, Qt.MouseButton.LeftButton,
                             pos=submenu.actionGeometry(target).center())
            outcome.append(mode)
        except Exception as error:
            fail(error)

    timer.timeout.connect(drive)
    deadline.timeout.connect(lambda: fail(AssertionError('Window Y-axis menu did not finish')))
    timer.start()
    deadline.start(3000)
    try:
        request()
        while timer.isActive():
            QTest.qWait(10)
        settle(qapp)
    finally:
        timer.stop()
        deadline.stop()
        close_menus()
    if errors:
        raise errors[0]
    assert outcome == [mode]


def test_signal_right_click_switches_only_its_window_and_keeps_data(windows, qapp):
    panel = windows
    keys = panel.plotted_keys()
    originals = {key: panel._items[key].series for key in keys}
    buffers = {key: (series.timestamps, series.values) for key, series in originals.items()}
    panel.set_cursor1_enabled(True)
    panel.move_cursor1(3.)
    settle(qapp)
    panel._stacked_plots[1].setXRange(2., 5., padding=0)
    settle(qapp)
    plot_windows.activate(panel, 1)
    tree = panel._window_widgets[0][1]
    position = tree.visualItemRect(tree.rows[keys[0]]).center()
    choose_axis_mode(panel, qapp,
                     lambda: open_tree_menu(qapp, tree, position),
                     '独立轴', current='共轴')

    assert [spec['axis_mode'] for spec in panel._window_specs] == ['individual', 'shared']
    assert panel._items[keys[0]].view_box is not panel._items[keys[1]].view_box
    assert all(plot.vb.viewRange()[0] == pytest.approx([2., 5.], abs=.001)
               for plot in panel._stacked_plots)
    assert panel.v_line.value() == 3.
    for key in keys:
        assert panel._items[key].series is originals[key]
        assert panel._items[key].series.timestamps is buffers[key][0]
        assert panel._items[key].series.values is buffers[key][1]
    assert len(panel._undo_stack) == 1
    panel.undo()
    settle(qapp)
    assert [spec['axis_mode'] for spec in panel._window_specs] == ['shared', 'shared']


def test_window_headers_have_no_axis_combobox_after_rebuild_and_restore(windows, qapp):
    panel = windows
    layout = panel.window_layout()
    layout[0]['axis_mode'] = 'individual'
    panel.apply_window_layout(layout)
    panel.resize(1100, 700)
    settle(qapp)
    assert panel.window_layout()[0]['axis_mode'] == 'individual'
    for widget, tree, title in panel._window_widgets.values():
        assert not widget.findChildren(QComboBox)
        assert title.width() > 250
        assert tree.width() == widget.width()


@pytest.mark.parametrize('surface,target', [
    ('blank', 0), ('empty', 1), ('title', 1), ('plot', 1),
])
def test_axis_menu_is_available_without_a_signal_at_the_click(windows, qapp, surface, target):
    panel = windows
    plot_windows.activate(panel, 1 - target)

    def request():
        _widget, tree, title = panel._window_widgets[target]
        if surface == 'title':
            open_title_menu(qapp, title, title.rect().center())
        elif surface == 'plot':
            index = next(i for i, spec in enumerate(panel._window_specs)
                         if spec['id'] == target)
            position = panel.glw.mapFromScene(panel._stacked_plots[index].vb.sceneBoundingRect().center())
            QTest.mouseClick(panel.glw.viewport(), Qt.MouseButton.RightButton, pos=position)
            qapp.processEvents()
        else:
            position = QPoint(20, tree.viewport().height() - 20)
            assert tree.itemAt(position) is None
            open_tree_menu(qapp, tree, position)

    choose_axis_mode(panel, qapp, request, '独立轴', current='共轴')
    assert {spec['id']: spec['axis_mode'] for spec in panel._window_specs} == {
        target: 'individual', 1 - target: 'shared'}
    choose_axis_mode(panel, qapp, request, '共轴', current='独立轴')
    assert all(spec['axis_mode'] == 'shared' for spec in panel._window_specs)


def test_current_mode_is_a_noop_and_menu_target_survives_reordering(windows, qapp):
    panel = windows
    target = panel._window_widgets[0][2]
    choose_axis_mode(panel, qapp,
                     lambda: open_title_menu(qapp, target, target.rect().center()),
                     '共轴', current='共轴')
    assert not panel._undo_stack

    def reorder():
        panel._window_specs.reverse()
        plot_windows.activate(panel, 1)

    choose_axis_mode(panel, qapp,
                     lambda: open_title_menu(qapp, target, target.rect().center()),
                     '独立轴', current='共轴', while_open=reorder)
    assert [(spec['id'], spec['axis_mode']) for spec in panel._window_specs] == [
        (1, 'shared'), (0, 'individual')]


def test_unavailable_signal_menu_retains_matching_and_axis_choices(windows, qapp):
    panel = windows
    key = panel.plotted_keys()[0]
    panel._items[key].unavailable_reason = '本日志未匹配'
    panel.redraw()
    settle(qapp)
    tree = panel._window_widgets[0][1]
    position = tree.visualItemRect(tree.rows[key]).center()

    def inspect():
        menu = window_axis_menu(panel)
        labels = [action.text() for action in menu.actions()]
        assert '匹配到本日志信号…' in labels
        assert '移除此曲线' in labels

    choose_axis_mode(panel, qapp,
                     lambda: open_tree_menu(qapp, tree, position),
                     '独立轴', current='共轴', while_open=inspect)
    assert panel._window_specs[0]['axis_mode'] == 'individual'
