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
from PySide6.QtWidgets import QComboBox, QInputDialog, QLineEdit, QMenu, QToolButton

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


def open_header_menu(qapp, tree):
    assert tree.header().isVisible()
    tree.header().customContextMenuRequested.emit(tree.header().rect().center())
    qapp.processEvents()


def open_window_menu(qapp, tree):
    if tree.isHeaderHidden():
        position = QPoint(20, tree.viewport().height() - 20)
        assert tree.itemAt(position) is None
        open_tree_menu(qapp, tree, position)
    else:
        open_header_menu(qapp, tree)


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


def use_window_menu(panel, qapp, request, interact):
    errors, outcome = [], []
    timer = QTimer()
    timer.setInterval(10)
    deadline = QTimer()
    deadline.setSingleShot(True)

    def close_menus():
        for menu in list(panel.findChildren(QMenu)):
            menu.close()

    def drive():
        menu = window_axis_menu(panel)
        if menu is None:
            return
        timer.stop()
        try:
            interact(menu)
            outcome.append(True)
        except Exception as error:
            errors.append(error)
        finally:
            close_menus()

    def timeout():
        timer.stop()
        errors.append(AssertionError('Window context menu did not open'))
        close_menus()

    timer.timeout.connect(drive)
    deadline.timeout.connect(timeout)
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
    assert outcome == [True]


def click_menu_action(menu, label):
    action = next(action for action in menu.actions() if action.text() == label)
    assert action.isEnabled(), label
    QTest.mouseClick(menu, Qt.MouseButton.LeftButton,
                     pos=menu.actionGeometry(action).center())


@pytest.mark.parametrize('surface,target', [
    ('signal', 0), ('blank', 0), ('empty', 1), ('header', 0),
    ('plot', 0), ('empty_plot', 1), ('unavailable', 0),
])
def test_window_operations_are_available_on_every_context_surface(windows, qapp, surface, target):
    panel = windows
    if surface == 'unavailable':
        panel._items[panel.plotted_keys()[0]].unavailable_reason = '本日志未匹配'
        panel.redraw()
        settle(qapp)
    plot_windows.activate(panel, 1 - target)

    def request():
        tree = panel._window_widgets[target][1]
        if surface in ('plot', 'empty_plot'):
            position = panel.glw.mapFromScene(panel._stacked_plots[target].vb.sceneBoundingRect().center())
            QTest.mouseClick(panel.glw.viewport(), Qt.MouseButton.RightButton, pos=position)
            qapp.processEvents()
        elif surface == 'header':
            open_header_menu(qapp, tree)
        else:
            position = (tree.visualItemRect(tree.topLevelItem(0)).center()
                        if surface in ('signal', 'unavailable')
                        else QPoint(20, tree.viewport().height() - 20))
            open_tree_menu(qapp, tree, position)

    def inspect(menu):
        actions = {action.text(): action for action in menu.actions()}
        name = actions[f'窗口 {target + 1}']
        assert not name.isEnabled()
        assert menu.actionGeometry(name).height() > 0
        assert actions['设为添加信号的目标窗口'].isChecked()
        assert actions['上移窗口'].isEnabled() == (target == 1)
        assert actions['下移窗口'].isEnabled() == (target == 0)
        assert actions['关闭此窗口及其曲线'].isEnabled()
        assert actions['重命名窗口…'].isEnabled()

    use_window_menu(panel, qapp, request, inspect)
    assert panel._window_active_id == target
    assert not panel._undo_stack


def test_menu_window_moves_and_close_preserve_undo_and_signal_ownership(windows, qapp):
    panel = windows
    panel.new_window()
    extra = SignalSeries(
        channel=1, message_name='Message', message_id=1, signal_name='C', unit='V',
        timestamps=np.array([0., 1.]), values=np.array([7., 8.]))
    panel.add_series(extra.key, extra)
    settle(qapp)
    panel.discard_undo_history()
    originals = {key: item.series for key, item in panel._items.items()}
    layout = panel.window_layout()
    layout[-1]['height'] = 350
    panel.apply_window_layout(layout)
    settle(qapp)

    def choose(label):
        tree = panel._window_widgets[2][1]
        use_window_menu(panel, qapp, lambda: open_window_menu(qapp, tree),
                        lambda menu: click_menu_action(menu, label))

    choose('上移窗口')
    assert [spec['id'] for spec in panel.window_layout()] == [0, 2, 1]
    assert panel.window_layout()[1]['height'] == 350
    assert panel._items[extra.key].window_id == 2
    assert len(panel._undo_stack) == 1
    panel.undo()
    settle(qapp)
    assert [spec['id'] for spec in panel.window_layout()] == [0, 1, 2]

    choose('上移窗口')
    choose('下移窗口')
    assert [spec['id'] for spec in panel.window_layout()] == [0, 1, 2]
    panel.undo()
    settle(qapp)
    assert [spec['id'] for spec in panel.window_layout()] == [0, 2, 1]
    assert all(panel._items[key].series is series for key, series in originals.items())

    choose('关闭此窗口及其曲线')
    assert extra.key not in panel.plotted_keys()
    assert [spec['id'] for spec in panel.window_layout()] == [0, 1]
    assert set(panel.plotted_keys()) == set(originals) - {extra.key}
    panel.undo()
    settle(qapp)
    assert panel.window_layout()[1]['id'] == 2
    assert panel.window_layout()[1]['height'] == 350
    assert panel._items[extra.key].window_id == 2
    assert all(panel._items[key].series is series for key, series in originals.items())


def test_menu_target_routes_the_next_signal_to_the_clicked_window(windows, qapp):
    panel = windows
    tree = panel._window_widgets[0][1]

    def select_target(menu):
        plot_windows.activate(panel, 1)
        click_menu_action(menu, '设为添加信号的目标窗口')

    use_window_menu(panel, qapp, lambda: open_window_menu(qapp, tree), select_target)
    added = SignalSeries(
        channel=1, message_name='Message', message_id=1, signal_name='C', unit='V',
        timestamps=np.array([0., 1.]), values=np.array([7., 8.]))
    panel.add_series(added.key, added)
    settle(qapp)
    assert added.key in panel._window_widgets[0][1].rows
    assert added.key not in panel._window_widgets[1][1].rows


def test_rename_is_visible_in_the_next_menu_and_saved_layout(windows, qapp):
    panel = windows
    tree = panel._window_widgets[1][1]
    errors, completed = [], []
    timer = QTimer()
    timer.setInterval(10)

    def enter_name():
        dialog = next((dialog for dialog in panel.findChildren(QInputDialog)
                       if dialog.isVisible()), None)
        if dialog is None:
            return
        timer.stop()
        try:
            edit = dialog.findChild(QLineEdit)
            assert edit is not None
            edit.selectAll()
            QTest.keyClicks(edit, 'Comfort')
            QTest.keyClick(edit, Qt.Key.Key_Return)
            completed.append(True)
        except Exception as error:
            errors.append(error)
            dialog.reject()

    def rename(menu):
        timer.start()
        QTimer.singleShot(1500, panel, lambda: [dialog.reject()
                          for dialog in panel.findChildren(QInputDialog) if dialog.isVisible()])
        click_menu_action(menu, '重命名窗口…')

    timer.timeout.connect(enter_name)
    try:
        use_window_menu(panel, qapp, lambda: open_window_menu(qapp, tree), rename)
    finally:
        timer.stop()
    assert not errors, errors
    assert completed == [True]
    assert panel.window_layout()[1]['name'] == 'Comfort'

    def inspect(menu):
        assert 'Comfort' in [action.text() for action in menu.actions()]

    use_window_menu(panel, qapp, lambda: open_window_menu(qapp, tree), inspect)
    panel.apply_window_layout(panel.window_layout())
    settle(qapp)
    tree = panel._window_widgets[1][1]
    use_window_menu(panel, qapp, lambda: open_window_menu(qapp, tree), inspect)


def test_closing_the_last_window_from_its_menu_keeps_a_usable_empty_list(windows, qapp):
    panel = windows
    layout = panel.window_layout()[:1]
    panel.apply_window_layout(layout)
    settle(qapp)
    panel.discard_undo_history()
    keys = panel.plotted_keys()
    tree = panel._window_widgets[0][1]
    use_window_menu(panel, qapp, lambda: open_window_menu(qapp, tree),
                    lambda menu: click_menu_action(menu, '关闭此窗口及其曲线'))
    assert panel.plotted_keys() == []
    assert len(panel.window_layout()) == 1
    tree = panel._window_widgets[panel.window_layout()[0]['id']][1]
    def inspect_empty(menu):
        actions = {action.text(): action for action in menu.actions()}
        assert not actions['上移窗口'].isEnabled()
        assert not actions['下移窗口'].isEnabled()
        assert actions['设为添加信号的目标窗口'].isChecked()

    use_window_menu(panel, qapp, lambda: open_window_menu(qapp, tree), inspect_empty)
    panel.undo()
    settle(qapp)
    assert panel.plotted_keys() == keys


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


def test_signal_lists_start_at_the_top_without_permanent_window_controls(windows, qapp):
    panel = windows
    layout = panel.window_layout()
    layout[0]['axis_mode'] = 'individual'
    panel.apply_window_layout(layout)
    panel.resize(1100, 700)
    settle(qapp)
    assert panel.window_layout()[0]['axis_mode'] == 'individual'
    for widget, tree in panel._window_widgets.values():
        assert not widget.findChildren(QComboBox)
        assert not widget.findChildren(QToolButton)
        assert tree.geometry().topLeft() == QPoint(0, 0)
        assert tree.size() == widget.size()
        assert [tree.headerItem().text(column) for column in range(3)] == ['信号', 'C1', 'C2']


@pytest.mark.parametrize('surface,target', [
    ('blank', 0), ('empty', 1), ('header', 0), ('plot', 1),
])
def test_axis_menu_is_available_without_a_signal_at_the_click(windows, qapp, surface, target):
    panel = windows
    plot_windows.activate(panel, 1 - target)

    def request():
        _widget, tree = panel._window_widgets[target]
        if surface == 'header':
            open_header_menu(qapp, tree)
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
    target = panel._window_widgets[0][1]
    choose_axis_mode(panel, qapp,
                     lambda: open_header_menu(qapp, target),
                     '共轴', current='共轴')
    assert not panel._undo_stack

    def reorder():
        panel._window_specs.reverse()
        plot_windows.activate(panel, 1)

    choose_axis_mode(panel, qapp,
                     lambda: open_header_menu(qapp, target),
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
