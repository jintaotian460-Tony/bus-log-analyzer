from unittest.mock import Mock
from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QMessageBox


def test_project_tree_float_and_auto_decode(qapp, tmp_path, monkeypatch):
    from gui.main_window import MainWindow
    from core.project_config import save_project
    monkeypatch.setattr(QMessageBox, 'warning', Mock())
    monkeypatch.setattr(QMessageBox, 'critical', Mock())
    window = MainWindow('Explorer test', 'test')
    try:
        first, second = tmp_path / 'first', tmp_path / 'second'
        first.mkdir(); second.mkdir()
        window.project = dict(name='First', log_root=str(first), csv_sources=[])
        window._update_project_label()
        assert window.log_explorer.root_path == str(first)
        assert window.signal_dock.isFloating()
        assert window.left_dock.widget() is window.log_explorer
        assert window.signal_dock.isAncestorOf(window.signal_tree)
        window.left_dock.hide()
        assert not window._configuration_dict()['panels']['log_explorer']
        log = first / 'one.csv'
        log.write_text('Timestamps [s],X\n0,10\n')
        monkeypatch.setattr(window, 'load_data', Mock())
        window.log_explorer.fileActivated.emit(str(log))
        assert window.measurement_path == str(log)
        window.load_data.assert_called_once()
        profile = tmp_path / 'second.json'
        save_project(profile, dict(name='Second', log_root=str(second)), {'channels': {}}, {})
        monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **k: (str(profile), ''))
        window.open_project()
        assert window.log_explorer.root_path == str(second)
        assert window.measurement_path is None
    finally:
        window.close()
        qapp.processEvents()


def test_real_double_click_loads_once_and_hiding_survives_log_switch(qapp, tmp_path, monkeypatch):
    import time
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from gui.main_window import MainWindow
    monkeypatch.setattr(QMessageBox, 'warning', Mock())
    monkeypatch.setattr(QMessageBox, 'critical', Mock())
    window = MainWindow('Explorer mouse test', 'test')
    path = tmp_path / 'first.csv'
    path.write_text('Timestamps [s],X\n0,10\n1,20\n')
    other = tmp_path / 'second.csv'
    other.write_text('Timestamps [s],X\n0,30\n1,40\n')
    try:
        window.resize(1400,900)
        window.show()
        window.log_explorer.set_root(str(tmp_path))
        tree = window.log_explorer.tree
        calls = []
        window.log_explorer.fileActivated.connect(calls.append)

        def double_click(file):
            index = window.log_explorer.model.index(str(file))
            deadline = time.monotonic()+10
            while not index.isValid() or tree.visualRect(index).isEmpty():
                assert time.monotonic() < deadline
                qapp.processEvents(); QTest.qWait(20)
                index = window.log_explorer.model.index(str(file))
            tree.scrollTo(index)
            point = tree.visualRect(index).center()
            QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=point)
            QTest.mouseDClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=point)
            deadline = time.monotonic()+15
            while window._thread is not None:
                assert time.monotonic() < deadline
                qapp.processEvents(); QTest.qWait(20)
            assert window.store is not None

        double_click(path)
        assert calls == [str(path)]
        assert window.measurement_path == str(path)
        assert window.signal_dock.isVisible()
        window.signal_dock.close()
        assert window.signal_dock.isHidden()
        assert not window._configuration_dict()['panels']['signals_visible']
        double_click(other)
        assert calls == [str(path), str(other)]
        assert window.measurement_path == str(other)
        assert window.signal_dock.isHidden()
        window._act_signals.trigger()
        assert window.signal_dock.isVisible()
        assert window._configuration_dict()['panels']['signals_visible']
        window.signal_dock.close()
        assert window.signal_dock.isHidden()
        assert not window._configuration_dict()['panels']['signals_visible']
    finally:
        window.close()
        qapp.processEvents()


def test_signal_titlebar_controls_minimise_restore_maximise_and_hide(qapp, monkeypatch):
    from PySide6.QtCore import Qt
    from gui.main_window import MainWindow
    monkeypatch.setattr(QMessageBox, 'warning', Mock())
    window = MainWindow('Signal controls test', 'playback.5')
    try:
        window.show()
        panel = window.signal_dock
        panel.show()
        flags = panel.windowFlags()
        for flag in (Qt.WindowType.WindowMinimizeButtonHint, Qt.WindowType.WindowMaximizeButtonHint, Qt.WindowType.WindowCloseButtonHint):
            assert flags & flag
        assert not flags & Qt.WindowType.FramelessWindowHint
        panel.showMinimized()
        qapp.processEvents()
        assert panel.isMinimized()
        assert not window.isMinimized()
        window._act_signals.trigger()
        qapp.processEvents()
        assert panel.isVisible() and not panel.isMinimized()
        panel.showMaximized()
        qapp.processEvents()
        assert panel.isMaximized()
        panel.showNormal()
        panel.close()
        assert panel.isHidden()
        assert window.isVisible()
        assert not window._configuration_dict()['panels']['signals_visible']
        window._act_signals.trigger()
        assert panel.isVisible()
    finally:
        window.close()
        qapp.processEvents()
