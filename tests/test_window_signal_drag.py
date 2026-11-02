import numpy as np
import pytest
from PySide6.QtCore import Qt, QPointF, QMimeData
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QApplication

from core.signal_store import SignalSeries
from gui import plot_windows
from gui.plot_widget import PlotPanel
from gui.signal_tree import SignalTreeWidget


def settle(qapp):
    for _ in range(6):
        qapp.processEvents()


@pytest.fixture
def panel(qapp):
    widget = PlotPanel()
    widget.resize(1350, 850)
    widget.show()
    widget.set_windows(True)
    for name in ('A', 'B', 'C'):
        if name == 'C':
            widget.new_window()
        signal = SignalSeries(channel=1, message_name='Message', message_id=1,
                              signal_name=name, unit='V', timestamps=np.arange(101, dtype=float)/10,
                              values=np.arange(101, dtype=float))
        widget.add_series(signal.key, signal)
    settle(qapp)
    widget.discard_undo_history()
    yield widget
    widget.close()
    settle(qapp)


def payload(keys):
    mime = QMimeData()
    mime.setData(SignalTreeWidget.MIME_TYPE, '\n'.join(keys).encode('utf-8'))
    return mime


def send_drop(panel, mime, target_id=1, surface='plot'):
    if surface == 'tree':
        target = panel._window_widgets[target_id][1].viewport()
        position = QPointF(target.rect().center())
    else:
        target = panel.glw.viewport()
        if surface == 'list':
            rect = panel._window_widgets[target_id][0].graphicsProxyWidget().sceneBoundingRect()
        else:
            row = next(index for index, spec in enumerate(panel._window_specs) if spec['id'] == target_id)
            rect = panel._stacked_plots[row].vb.sceneBoundingRect()
        position = QPointF(panel.glw.mapFromScene(rect.center()))
    enter = QDragEnterEvent(position.toPoint(), Qt.DropAction.MoveAction, mime,
                            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(target, enter)
    drop = QDropEvent(position, Qt.DropAction.MoveAction, mime,
                      Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(target, drop)
    return drop.isAccepted()


@pytest.mark.parametrize('surface', ['plot', 'list', 'tree'])
def test_cross_window_drop_moves_all_selected_signals_once(panel, qapp, surface):
    keys = panel.plotted_keys()[:2]
    first, second = [panel._items[key] for key in keys]
    first.color, first.line_style = '#123456', 'dotted'
    second.visible = False
    second.unavailable_reason = '本日志未匹配'
    panel.set_line_width(1)
    panel.redraw()
    panel.set_cursor1_enabled(True)
    panel.move_cursor1(3)
    settle(qapp)
    panel._driving_vb().setXRange(2, 5, padding=0)
    settle(qapp)
    before = list(panel._driving_vb().viewRange()[0])
    assert send_drop(panel, payload(keys), surface=surface)
    assert first.window_id == 0, 'Proxy destruction must wait until after the drop handler'
    # A different active window before queued delivery must not steal the drop.
    plot_windows.activate(panel, 0)
    settle(qapp)
    assert first.window_id == second.window_id == 1
    assert panel._items[keys[0]] is first and panel._items[keys[1]] is second
    assert first.color == '#123456' and first.line_style == 'dotted'
    assert not second.visible and second.unavailable_reason == '本日志未匹配'
    assert all(key not in panel._window_widgets[0][1].rows for key in keys)
    assert all(key in panel._window_widgets[1][1].rows for key in keys)
    assert len(panel.plotted_keys()) == 3
    assert len(panel._undo_stack) == 1
    assert panel._driving_vb().viewRange()[0] == pytest.approx(before, abs=.03)
    assert panel.v_line.value() == 3
    assert first.curve.opts['pen'].widthF() == 1
    panel.undo()
    settle(qapp)
    assert all(panel._items[key].window_id == 0 for key in keys)
    assert panel._items[keys[0]].series is first.series


def test_drag_source_exports_all_selected_signal_keys(panel, qapp, monkeypatch):
    captured = {}
    class Drag:
        def __init__(self, source):
            captured['source'] = source
        def setMimeData(self, mime):
            captured['mime'] = mime
        def exec(self, actions):
            captured['actions'] = actions
    monkeypatch.setattr(plot_windows, 'QDrag', Drag)
    tree = panel._window_widgets[0][1]
    keys = panel.plotted_keys()[:2]
    for key in keys:
        tree.rows[key].setSelected(True)
    tree.startDrag(Qt.DropAction.MoveAction)
    assert captured['source'] is tree
    assert captured['actions'] == Qt.DropAction.MoveAction
    assert bytes(captured['mime'].data(SignalTreeWidget.MIME_TYPE)).decode('utf-8').splitlines() == keys


def test_drop_in_same_window_is_a_noop_and_foreign_payload_is_ignored(panel, qapp):
    key = panel.plotted_keys()[0]
    assert send_drop(panel, payload([key]), target_id=0)
    settle(qapp)
    assert panel._items[key].window_id == 0
    assert not panel._undo_stack
    mime = QMimeData()
    mime.setText('unrelated content')
    assert not send_drop(panel, mime)
    assert panel._items[key].window_id == 0


def test_late_drop_is_cancelled_after_layout_or_measurement_rebuild(panel, qapp):
    key = panel.plotted_keys()[0]
    assert send_drop(panel, payload([key]))
    panel.redraw()
    settle(qapp)
    assert panel._items[key].window_id == 0
    assert not panel._undo_stack
