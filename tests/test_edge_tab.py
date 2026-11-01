# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""Panel boundaries must resize, collapse and reopen without blank gutters."""
from unittest.mock import Mock

import pytest
from PySide6.QtCore import Qt, QEvent, QPointF
from PySide6.QtGui import QMouseEvent, QPalette, QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from gui.edge_tab import EdgeTab


def settle(qapp):
    for _ in range(4):
        qapp.processEvents()
        QTest.qWait(10)


def drag(widget, dx=0, dy=0):
    local = QPointF(widget.rect().center())
    global_pos = QPointF(widget.mapToGlobal(widget.rect().center()))
    QTest.mousePress(widget, Qt.MouseButton.LeftButton)
    QApplication.sendEvent(widget, QMouseEvent(QEvent.Type.MouseMove, local,
                           global_pos+QPointF(dx, dy), Qt.MouseButton.NoButton,
                           Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    QTest.mouseRelease(widget, Qt.MouseButton.LeftButton)


@pytest.mark.parametrize('background', ['#f3f3f3', '#202020'])
@pytest.mark.parametrize('orientation', [Qt.Orientation.Horizontal, Qt.Orientation.Vertical])
def test_line_is_one_pixel_and_mouse_target_is_transparent(qapp, background, orientation):
    parent = QWidget()
    parent.resize(180, 180)
    palette = parent.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor(background))
    palette.setColor(QPalette.ColorRole.Mid, QColor('#8899aa'))
    parent.setPalette(palette)
    parent.setAutoFillBackground(True)
    tab = EdgeTab(orientation, parent)
    tab.move(20, 20)
    parent.show()
    settle(qapp)
    image = parent.grab().toImage()
    if orientation == Qt.Orientation.Horizontal:
        pixels = [image.pixelColor(30, 20+i).name() for i in range(tab.height())]
    else:
        pixels = [image.pixelColor(20+i, 30).name() for i in range(tab.width())]
    assert pixels.count(background) == EdgeTab.THICKNESS-1
    assert pixels.count('#8899aa') == 1
    parent.close()


@pytest.fixture
def window(qapp, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QMessageBox
    from gui.main_window import MainWindow
    for name in ('warning', 'critical', 'information'):
        monkeypatch.setattr(QMessageBox, name, Mock(return_value=QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(MainWindow, '_settings_folder', staticmethod(lambda: tmp_path))
    w = MainWindow('Osvanta', 'test')
    w.resize(1500, 1000)
    w.show()
    w.plot_panel.set_windows(True)
    w.plot_panel.new_window()
    settle(qapp)
    yield w
    w.close()
    settle(qapp)


@pytest.mark.parametrize('name', ['left', 'bottom'])
def test_click_does_not_collapse_drag_does_not_toggle_and_double_click_reopens(qapp, window, name):
    tab, dock = getattr(window, name+'_edge_btn'), getattr(window, name+'_dock')
    QTest.mouseClick(tab, Qt.MouseButton.LeftButton)
    settle(qapp)
    assert dock.isVisible()
    drag(tab, dx=45 if name == 'left' else 0, dy=-45 if name == 'bottom' else 0)
    settle(qapp)
    assert dock.isVisible()
    extent = dock.width() if name == 'left' else dock.height()
    QTest.mouseDClick(tab, Qt.MouseButton.LeftButton)
    settle(qapp)
    assert not dock.isVisible()
    assert tab.isVisible()
    central = window.centralWidget().geometry()
    painted_edge = (tab.x()+tab._line_offset if name == 'left' else tab.y()+tab._line_offset)
    assert painted_edge == (central.left() if name == 'left' else central.bottom())
    QTest.mouseDClick(tab, Qt.MouseButton.LeftButton)
    settle(qapp)
    assert dock.isVisible()
    assert (dock.width() if name == 'left' else dock.height()) == pytest.approx(extent, abs=2)


@pytest.mark.parametrize('name,delta', [('left', 55), ('bottom', -55)])
def test_boundary_drags_resize_docks_and_keep_plot_tiled(qapp, window, name, delta):
    from tests.test_plot_windows import assert_tiles
    tab, dock = getattr(window, name+'_edge_btn'), getattr(window, name+'_dock')
    before = dock.width() if name == 'left' else dock.height()
    drag(tab, dx=delta if name == 'left' else 0, dy=delta if name == 'bottom' else 0)
    settle(qapp)
    after = dock.width() if name == 'left' else dock.height()
    assert after == pytest.approx(before+55, abs=2)
    assert_tiles(window.plot_panel)


@pytest.mark.parametrize('name', ['left', 'bottom'])
def test_hidden_panel_can_be_dragged_open_from_its_edge(qapp, window, name):
    tab, dock = getattr(window, name+'_edge_btn'), getattr(window, name+'_dock')
    QTest.mouseDClick(tab, Qt.MouseButton.LeftButton)
    settle(qapp)
    assert not dock.isVisible()
    drag(tab, dx=220 if name == 'left' else 0, dy=-200 if name == 'bottom' else 0)
    settle(qapp)
    assert dock.isVisible()
    assert not dock.isFloating()


def test_content_touches_dock_boundaries_without_extra_margins(qapp, window):
    central = window.centralWidget().geometry()
    assert central.left()-window.left_dock.geometry().right()-1 == 1
    assert window.bottom_dock.geometry().top()-central.bottom()-1 == 1
    margins = window._center_layout.contentsMargins()
    assert (margins.left(), margins.top(), margins.right(), margins.bottom()) == (0, 0, 0, 0)
    assert window.plot_panel.layout().spacing() == 0
    for tab in (window.left_edge_btn, window.bottom_edge_btn):
        assert tab.styleSheet() == ''


@pytest.mark.parametrize('name,area,delta', [
    ('left', Qt.DockWidgetArea.RightDockWidgetArea, -45),
    ('bottom', Qt.DockWidgetArea.TopDockWidgetArea, 45),
])
def test_opposite_dock_edges_reverse_drag_direction(qapp, window, name, area, delta):
    tab, dock = getattr(window, name+'_edge_btn'), getattr(window, name+'_dock')
    window.addDockWidget(area, dock)
    settle(qapp)
    window._sync_panel_toggle_buttons()
    before = dock.width() if name == 'left' else dock.height()
    drag(tab, dx=delta if name == 'left' else 0, dy=delta if name == 'bottom' else 0)
    settle(qapp)
    assert (dock.width() if name == 'left' else dock.height()) == pytest.approx(before+45, abs=2)
