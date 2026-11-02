import sys
from unittest.mock import Mock
from pathlib import Path

import pytest
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
        explorer = window.log_explorer
        explorer.set_root('')
        explorer.tree.setRootIndex(explorer.model.index(''))
        explorer.model.setRootPath('')
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
        _stop_file_system_watch(window.log_explorer)
        window.close()
        qapp.processEvents()


def _stop_file_system_watch(explorer):
    explorer.set_root('')
    explorer.tree.setRootIndex(explorer.model.index(''))
    explorer.model.setRootPath('')


def _wait_file_job(qapp, explorer):
    import time
    from PySide6.QtTest import QTest
    deadline = time.monotonic() + 10
    while explorer._file_job is not None:
        assert time.monotonic() < deadline
        qapp.processEvents()
        QTest.qWait(10)
    return explorer._last_file_result


def test_log_tree_context_menu_entries(qapp, tmp_path, monkeypatch):
    import time
    from PySide6.QtCore import Qt
    from gui.main_window import MainWindow

    monkeypatch.setattr(QMessageBox, 'warning', Mock())
    root = tmp_path / 'logs'
    package = root / 'case'
    package.mkdir(parents=True)
    source = package / 'Recorder.csv'
    source.write_text('Timestamps [s],X\n0,10\n', encoding='utf-8')

    window = MainWindow('Log file operations test', 'test')
    explorer = window.log_explorer
    try:
        explorer.set_root(str(root))
        index = explorer.model.index(str(source))
        deadline = time.monotonic() + 5
        while not index.isValid() and time.monotonic() < deadline:
            qapp.processEvents()
            index = explorer.model.index(str(source))
        assert index.isValid()
        explorer.tree.setCurrentIndex(index)
        qapp.processEvents()

        assert explorer.tree.contextMenuPolicy() == Qt.ContextMenuPolicy.CustomContextMenu
        menu = explorer._context_menu(source)
        labels = [action.text() for action in menu.actions() if action.text()]
        assert '打开所在文件夹' in labels
        assert '复制路径' in labels
        assert '复制' in labels
        assert '压缩为 ZIP' in labels
        assert '粘贴到此处' in labels

        _stop_file_system_watch(explorer)
        archive = root / 'case.zip'
        archive.write_bytes(b'PK\x03\x04')
        menu = explorer._context_menu(archive)
        assert '解压 ZIP' in [action.text() for action in menu.actions() if action.text()]
    finally:
        _stop_file_system_watch(window.log_explorer)
        window.close()
        qapp.processEvents()


def test_log_tree_basic_file_operations(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from gui.main_window import MainWindow

    monkeypatch.setattr(QMessageBox, 'warning', Mock())
    root = tmp_path / 'logs'
    package = root / 'case'
    package.mkdir(parents=True)
    source = package / 'Recorder.csv'
    source.write_text('Timestamps [s],X\n0,10\n', encoding='utf-8')
    (package / 'notes').mkdir()
    (package / 'notes' / 'note.txt').write_text('keep me', encoding='utf-8')
    paste_target = root / 'paste'
    paste_target.mkdir()

    window = MainWindow('Log file operations test', 'test')
    explorer = window.log_explorer
    try:
        explorer._copy_files([source])
        explorer._paste_files(paste_target)
        _wait_file_job(qapp, explorer)
        copied = paste_target / source.name
        assert copied.read_text(encoding='utf-8').startswith('Timestamps')

        explorer._compress_to_zip([package])
        _wait_file_job(qapp, explorer)
        archive = root / 'case.zip'
        assert archive.is_file()
        explorer._extract_zip(archive)
        assert not _wait_file_job(qapp, explorer).errors
        extracted = root / 'case (1)' / 'case'
        assert extracted != package
        assert (extracted / 'Recorder.csv').read_text(encoding='utf-8').startswith('Timestamps')
        assert (extracted / 'notes' / 'note.txt').read_text(encoding='utf-8') == 'keep me'

        menu = explorer._context_menu(archive)
        assert '解压 ZIP' in [action.text() for action in menu.actions() if action.text()]
        assert not QMessageBox.warning.called
        QApplication.clipboard().clear()
    finally:
        _stop_file_system_watch(explorer)
        window.close()
        qapp.processEvents()


def test_log_tree_file_operations_never_overwrite_and_reject_zip_escape(
        qapp, tmp_path, monkeypatch):
    import zipfile
    from PySide6.QtWidgets import QApplication
    from gui.main_window import MainWindow

    warning = Mock()
    monkeypatch.setattr(QMessageBox, 'warning', warning)
    root = tmp_path / 'logs'
    package = root / 'case'
    paste = root / 'paste'
    package.mkdir(parents=True)
    paste.mkdir()
    source = package / 'Recorder.csv'
    source.write_text('new recording', encoding='utf-8')
    existing_copy = paste / 'Recorder.csv'
    existing_copy.write_text('existing copy', encoding='utf-8')
    existing_archive = root / 'case.zip'
    existing_archive.write_text('existing archive', encoding='utf-8')
    existing_extract = root / 'case (1)'
    existing_extract.mkdir()
    (existing_extract / 'keep.txt').write_text('existing folder', encoding='utf-8')

    window = MainWindow('Safe file operations test', 'test')
    explorer = window.log_explorer
    try:
        explorer._copy_files([source])
        explorer._paste_files(paste)
        _wait_file_job(qapp, explorer)
        assert existing_copy.read_text(encoding='utf-8') == 'existing copy'
        assert (paste / 'Recorder (1).csv').read_text(encoding='utf-8') == 'new recording'

        explorer._compress_to_zip([package])
        _wait_file_job(qapp, explorer)
        assert existing_archive.read_text(encoding='utf-8') == 'existing archive'
        safe_archive = root / 'case (1).zip'
        assert safe_archive.is_file()

        explorer._extract_zip(safe_archive)
        _wait_file_job(qapp, explorer)
        assert (existing_extract / 'keep.txt').read_text(encoding='utf-8') == 'existing folder'
        extracted = root / 'case (1) (1)'
        assert (extracted / 'case' / 'Recorder.csv').read_text(encoding='utf-8') == 'new recording'

        unsafe_archive = root / 'unsafe.zip'
        with zipfile.ZipFile(unsafe_archive, 'w') as archive:
            archive.writestr('../escaped.txt', 'must not escape')
        explorer._extract_zip(unsafe_archive)
        _wait_file_job(qapp, explorer)
        assert not (root.parent / 'escaped.txt').exists()
        assert not (root / 'unsafe').exists()
        assert warning.call_count == 1
        assert 'unsafe entry' in warning.call_args.args[-1]
    finally:
        QApplication.clipboard().clear()
        _stop_file_system_watch(explorer)
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


def test_zip_is_visible_on_open_and_clipboard_tracks_external_changes(qapp, tmp_path):
    from PySide6.QtCore import QMimeData, QUrl
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    from gui.log_explorer import LogExplorer

    first = tmp_path / 'a.csv'
    second = tmp_path / 'b.csv'
    first.write_text('one')
    second.write_text('two')
    archive = tmp_path / 'case.zip'
    archive.write_bytes(b'ZIP')
    explorer = LogExplorer()
    try:
        explorer.set_root(str(tmp_path))
        assert '*.zip' in explorer.model.nameFilters()
        explorer._copy_files([first])
        assert explorer._clipboard_paths() == [first]
        external = QMimeData()
        external.setUrls([QUrl.fromLocalFile(str(second))])
        QApplication.clipboard().setMimeData(external)
        QTest.qWait(20)
        assert explorer._clipboard_paths() == [second]
        QApplication.clipboard().setText('plain text')
        assert not explorer._clipboard_paths()
    finally:
        QApplication.clipboard().clear()
        _stop_file_system_watch(explorer)
        explorer.close()
        qapp.processEvents()


def test_file_job_runs_off_gui_thread_and_can_be_cancelled(qapp, tmp_path):
    import threading
    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtTest import QTest
    from gui.log_explorer import LogExplorer
    from core import log_file_operations as ops

    explorer = LogExplorer()
    started, release = threading.Event(), threading.Event()
    gui_id = threading.get_ident()
    worker_ids, ticks = [], []
    def slow_operation(progress):
        worker_ids.append(threading.get_ident())
        started.set()
        release.wait(3)
        try:
            progress('test')
        except ops.Cancelled:
            return ops.Result(cancelled=True)
        return ops.Result()
    try:
        explorer._start_file_operation('复制', slow_operation)
        assert started.wait(3)
        QTimer.singleShot(0, lambda: ticks.append(True))
        qapp.processEvents()
        assert ticks and worker_ids == [worker_ids[0]] and worker_ids[0] != gui_id
        QTest.mouseClick(explorer.cancel_operation, Qt.MouseButton.LeftButton)
        release.set()
        result = _wait_file_job(qapp, explorer)
        assert result.cancelled
    finally:
        release.set()
        _wait_file_job(qapp, explorer)
        explorer.close()
        qapp.processEvents()


def test_tree_copy_paste_shortcuts_and_explorer_launch(qapp, tmp_path, monkeypatch):
    import time
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from gui.log_explorer import LogExplorer, QDesktopServices, QProcess

    source = tmp_path / '记录.csv'
    source.write_text('original samples')
    explorer = LogExplorer()
    explorer.resize(500, 500)
    try:
        explorer.set_root(str(tmp_path))
        explorer.show()
        index = explorer.model.index(str(source))
        deadline = time.monotonic() + 5
        while not index.isValid() or explorer.tree.visualRect(index).isEmpty():
            assert time.monotonic() < deadline
            qapp.processEvents()
            QTest.qWait(20)
            index = explorer.model.index(str(source))
        explorer.tree.setCurrentIndex(index)
        explorer.activateWindow()
        explorer.tree.setFocus()
        qapp.processEvents()
        QTest.keyClick(explorer.tree, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        assert explorer._clipboard_paths() == [source]
        QTest.keyClick(explorer.tree, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        assert not _wait_file_job(qapp, explorer).errors
        assert (tmp_path / '记录 (1).csv').read_text() == 'original samples'

        launch = Mock(return_value=(True, 123))
        opened = Mock(return_value=True)
        monkeypatch.setattr(QProcess, 'startDetached', launch)
        monkeypatch.setattr(QDesktopServices, 'openUrl', opened)
        explorer._open_in_explorer(source)
        launch.assert_called_once_with('explorer.exe', ['/select,', str(source)])
        assert not opened.called
        launch.return_value = (False, 0)
        explorer._open_in_explorer(source)
        assert Path(opened.call_args.args[0].toLocalFile()) == tmp_path
        explorer._open_in_explorer(tmp_path)
        assert Path(opened.call_args.args[0].toLocalFile()) == tmp_path
    finally:
        QApplication.clipboard().clear()
        _stop_file_system_watch(explorer)
        explorer.close()
        qapp.processEvents()


def test_main_window_close_cancels_file_job_without_destroying_running_thread(qapp):
    import threading
    from gui.main_window import MainWindow
    from core import log_file_operations as ops

    release, started = threading.Event(), threading.Event()
    def slow_operation(progress):
        started.set()
        release.wait(3)
        try:
            progress('test')
        except ops.Cancelled:
            return ops.Result(cancelled=True)
        return ops.Result()
    window = MainWindow('Close during file operation', 'test')
    try:
        window.show()
        window.log_explorer._start_file_operation('复制', slow_operation)
        assert started.wait(3)
        window.close()
        assert window._closing and not window.isVisible()
        release.set()
        assert _wait_file_job(qapp, window.log_explorer).cancelled
    finally:
        release.set()
        _wait_file_job(qapp, window.log_explorer)
        window.close()
        qapp.processEvents()


@pytest.mark.skipif(sys.platform != 'win32', reason='Recycle Bin delete is Windows-only')
def test_delete_moves_selection_to_the_recycle_bin(qapp, tmp_path, monkeypatch):
    from gui.main_window import MainWindow

    monkeypatch.setattr(QMessageBox, 'warning', Mock())
    monkeypatch.setattr(QMessageBox, 'question',
                        lambda *a, **k: QMessageBox.StandardButton.Yes)
    root = tmp_path / 'logs'
    package = root / 'case'
    package.mkdir(parents=True)
    doomed = package / 'Recorder.csv'
    doomed.write_text('Timestamps [s],X\n0,10\n', encoding='utf-8')

    window = MainWindow('Recycle delete test', 'test')
    explorer = window.log_explorer
    try:
        explorer.set_root(str(root))
        explorer._delete_selection([doomed, package])
        result = _wait_file_job(qapp, explorer)
        assert not result.errors
        assert doomed not in result.paths or not doomed.exists()
        assert not doomed.exists() and not package.exists()

        # a cancelled confirmation must not start anything
        monkeypatch.setattr(QMessageBox, 'question',
                            lambda *a, **k: QMessageBox.StandardButton.No)
        keeper = root / 'keep.csv'
        keeper.write_text('x', encoding='utf-8')
        explorer._delete_selection([keeper])
        qapp.processEvents()
        assert explorer._file_job is None and keeper.exists()
    finally:
        _stop_file_system_watch(explorer)
        window.close()
        qapp.processEvents()


def test_delete_entry_is_offered_but_not_for_the_root(qapp, tmp_path, monkeypatch):
    from gui.main_window import MainWindow

    monkeypatch.setattr(QMessageBox, 'warning', Mock())
    root = tmp_path / 'logs'
    root.mkdir()
    (root / 'a.csv').write_text('x', encoding='utf-8')

    window = MainWindow('Delete menu test', 'test')
    explorer = window.log_explorer
    try:
        explorer.set_root(str(root))
        menu = explorer._context_menu(root / 'a.csv')
        delete = next(a for a in menu.actions() if a.text().startswith('删除'))
        assert delete.isEnabled()

        # the log root itself is never a delete target
        menu = explorer._context_menu(root)
        delete = next(a for a in menu.actions() if a.text().startswith('删除'))
        assert not delete.isEnabled()
    finally:
        _stop_file_system_watch(explorer)
        window.close()
        qapp.processEvents()
