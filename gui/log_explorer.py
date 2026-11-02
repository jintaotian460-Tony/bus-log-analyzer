"""Filesystem-backed log explorer; one active measurement at a time."""
from pathlib import Path
import time

from PySide6.QtCore import Qt, Signal, QDir, QMimeData, QProcess, QThread, QUrl
from PySide6.QtGui import QShortcut, QKeySequence, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QFileDialog, QFileSystemModel, QHBoxLayout,
    QLabel, QLineEdit, QMenu, QMessageBox, QPushButton, QTreeView, QVBoxLayout, QWidget,
)

from core import log_file_operations as file_ops
from gui import app_log


class _FileJob(QThread):
    progressed = Signal(str)

    def __init__(self, operation, args, parent):
        super().__init__(parent)
        self.operation, self.args = operation, args
        self.result = file_ops.Result()
        self._last_progress = 0.0

    def _progress(self, name):
        if self.isInterruptionRequested():
            raise file_ops.Cancelled
        now = time.monotonic()
        if now - self._last_progress >= 0.1:
            self._last_progress = now
            self.progressed.emit(name)

    def run(self):
        try:
            self.result = self.operation(*self.args, progress=self._progress)
        except Exception as exc:
            self.result = file_ops.Result(errors=[str(exc)])


class LogExplorer(QWidget):
    fileActivated = Signal(str)
    rootChanged = Signal(str)
    operationCompleted = Signal(str)
    SUFFIXES = ('.blf', '.asc', '.trc', '.mf4', '.mdf', '.csv')
    ARCHIVE_SUFFIXES = ('.zip',)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.root_path = ''
        self._file_job = None
        self._last_file_result = file_ops.Result()
        self.model = QFileSystemModel(self)
        self.model.setReadOnly(True)
        self.model.setFilter(QDir.Filter.AllDirs | QDir.Filter.Files | QDir.Filter.NoDotAndDotDot)
        self.model.setNameFilterDisables(False)
        self._filter('')
        self.tree = QTreeView()
        self.tree.setModel(self.model)
        self.tree.setHeaderHidden(True)
        self.tree.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tree.setToolTip('右键：打开文件夹、复制、粘贴、压缩 / 解压 ZIP；支持 Ctrl+C / Ctrl+V')
        for column in range(1, 4):
            self.tree.hideColumn(column)
        self.tree.setSortingEnabled(True)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        self.tree.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.tree.doubleClicked.connect(self._activate)
        for key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            shortcut = QShortcut(QKeySequence(key), self.tree)
            shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
            shortcut.activated.connect(lambda: self._activate(self.tree.currentIndex()))
        for sequence, action in ((QKeySequence.StandardKey.Copy, self._copy_selection),
                                 (QKeySequence.StandardKey.Paste, self._paste_selection)):
            shortcut = QShortcut(QKeySequence(sequence), self.tree)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(action)
        self.tree.setVisible(False)
        folder = QPushButton('Open folder…')
        folder.clicked.connect(self.choose_root)
        self.search = QLineEdit()
        self.search.setPlaceholderText('Filter log filenames')
        self.search.textChanged.connect(self._filter)
        self.label = QLabel('Choose a log folder for this project')
        self.label.setWordWrap(True)
        row = QHBoxLayout()
        row.addWidget(folder)
        row.addStretch()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.addLayout(row)
        layout.addWidget(self.label)
        layout.addWidget(self.search)
        layout.addWidget(self.tree, 1)
        self.operation_status = QLabel()
        self.operation_status.setWordWrap(True)
        self.cancel_operation = QPushButton('取消')
        self.cancel_operation.clicked.connect(self.cancel_file_operation)
        self.operation_status.hide()
        self.cancel_operation.hide()
        layout.addWidget(self.operation_status)
        layout.addWidget(self.cancel_operation)

    def set_root(self, path):
        path = str(Path(path).resolve()) if path else ''
        if path == self.root_path:
            return
        self.root_path = path
        valid = bool(path) and Path(path).is_dir()
        self.tree.setVisible(valid)
        self.label.setText(Path(path).name if valid else ('Folder unavailable: ' + path if path else 'Choose a log folder for this project'))
        self.label.setToolTip(path)
        if valid:
            self.tree.setRootIndex(self.model.setRootPath(path))

    def choose_root(self):
        # Non-native on purpose. The Windows shell folder picker (a COM
        # IFileOpenDialog) has hung the GUI thread for minutes on end, with
        # nothing on screen: the call pumps messages internally, so even the
        # crash log's freeze watchdog keeps seeing a beating event loop and
        # records nothing (BLA_crash.log 2026-11-02, RPC_E_WRONG_THREAD
        # storm under choose_root). Qt's own dialog uses no COM. The two
        # app-log lines leave a timestamped record should any dialog ever
        # hang again: an "opened" with no "returned" is the stuck call.
        app_log.record('Log folder chooser opened.')
        path = QFileDialog.getExistingDirectory(
            self, 'Project log folder', self.root_path,
            options=QFileDialog.Option.DontUseNativeDialog)
        app_log.record(f'Log folder chooser returned: {path if path else "cancelled"}')
        if path:
            self.set_root(path)
            self.rootChanged.emit(self.root_path)

    def _filter(self, text):
        text = text.strip()
        suffixes = (*self.SUFFIXES, *self.ARCHIVE_SUFFIXES)
        self.model.setNameFilters(
            [f'*{text}*{suffix}' for suffix in suffixes] if text
            else ['*' + suffix for suffix in suffixes])

    def _activate(self, index):
        path = str(Path(self.model.filePath(index)))
        if Path(path).is_file() and Path(path).suffix.lower() in self.SUFFIXES:
            self.fileActivated.emit(path)

    def select_file(self, path):
        if path and self.root_path:
            index = self.model.index(str(path))
            if index.isValid():
                self.tree.setCurrentIndex(index)
                self.tree.scrollTo(index)

    def _selected_paths(self):
        return [Path(self.model.filePath(index))
                for index in self.tree.selectionModel().selectedRows(0) if index.isValid()]

    def _copy_selection(self):
        self._copy_files(self._selected_paths())

    def _paste_selection(self):
        index = self.tree.currentIndex()
        path = Path(self.model.filePath(index)) if index.isValid() else None
        if path is None:
            path = Path(self.root_path) if self.root_path else None
        if path is not None:
            self._paste_files(path if path.is_dir() else path.parent)

    def _path_at(self, position):
        index = self.tree.indexAt(position)
        if not index.isValid():
            self.tree.clearSelection()
            return Path(self.root_path) if self.root_path else None
        if not self.tree.selectionModel().isSelected(index):
            self.tree.setCurrentIndex(index)
        return Path(self.model.filePath(index))

    def _show_context_menu(self, position):
        path = self._path_at(position)
        if path is not None:
            menu = self._context_menu(path)
            menu.aboutToHide.connect(menu.deleteLater)
            menu.popup(self.tree.viewport().mapToGlobal(position))

    def _context_menu(self, path):
        path = Path(path)
        selected = self._selected_paths()
        selected = selected if path in selected else [path]
        target = path if path.is_dir() else path.parent
        busy = self._file_job is not None
        menu = QMenu(self)
        open_action = menu.addAction('打开所在文件夹' if path.is_file() else '打开文件夹')
        open_action.triggered.connect(lambda: self._open_in_explorer(path))
        copy_path_action = menu.addAction('复制路径')
        copy_path_action.triggered.connect(lambda: self._copy_paths_to_clipboard(selected))
        copy_action = menu.addAction('复制')
        copy_action.setShortcut(QKeySequence.StandardKey.Copy)
        copy_action.triggered.connect(lambda: self._copy_files(selected))
        paste_action = menu.addAction('粘贴到此处')
        paste_action.setShortcut(QKeySequence.StandardKey.Paste)
        paste_action.setEnabled(not busy and bool(self._clipboard_paths()))
        paste_action.triggered.connect(lambda: self._paste_files(target))
        menu.addSeparator()
        compress_action = menu.addAction('压缩为 ZIP')
        compress_action.setEnabled(not busy)
        compress_action.triggered.connect(lambda: self._compress_to_zip(selected))
        if path.is_file() and path.suffix.lower() in self.ARCHIVE_SUFFIXES:
            extract_action = menu.addAction('解压 ZIP')
            extract_action.setEnabled(not busy)
            extract_action.triggered.connect(lambda: self._extract_zip(path))
        return menu

    def _open_in_explorer(self, path):
        path = Path(path)
        if path.is_dir():
            return QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        result = QProcess.startDetached('explorer.exe', ['/select,', str(path)])
        started = result[0] if isinstance(result, tuple) else result
        if not started:
            return QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))
        return True

    def _copy_paths_to_clipboard(self, paths):
        value = '\r\n'.join(str(path) for path in paths)
        QApplication.clipboard().setText(value)
        if QApplication.clipboard().text() != value:
            QMessageBox.warning(self, '复制失败', 'Windows 剪贴板暂不可用，请稍后重试。')
            return
        self.operationCompleted.emit(f'已复制 {len(paths)} 个路径')

    def _copy_files(self, paths):
        paths = [Path(path) for path in paths if Path(path).exists()]
        if not paths:
            return
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
        mime.setData('Preferred DropEffect', b'\x01\x00\x00\x00')
        QApplication.clipboard().setMimeData(mime)
        if self._clipboard_paths() != paths:
            QMessageBox.warning(self, '复制失败', 'Windows 剪贴板暂不可用，请稍后重试。')
            return
        self.operationCompleted.emit(f'已复制 {len(paths)} 个文件或文件夹')

    def _clipboard_paths(self):
        mime = QApplication.clipboard().mimeData()
        urls = mime.urls() if mime is not None else []
        return [Path(url.toLocalFile()) for url in urls
                if url.isLocalFile() and Path(url.toLocalFile()).exists()]

    def _paste_files(self, target):
        paths = self._clipboard_paths()
        if paths and Path(target).is_dir():
            self._start_file_operation('粘贴', file_ops.paste, paths, Path(target))

    def _compress_to_zip(self, paths):
        self._start_file_operation('压缩', file_ops.compress, [Path(path) for path in paths])

    def _extract_zip(self, path):
        self._start_file_operation('解压', file_ops.extract, Path(path))

    def _start_file_operation(self, verb, operation, *args):
        if self._file_job is not None:
            return
        self._operation_verb = verb
        self.operation_status.setText(f'正在{verb}…')
        self.operation_status.show()
        self.cancel_operation.show()
        job = self._file_job = _FileJob(operation, args, self)
        job.progressed.connect(lambda name: self.operation_status.setText(f'正在{verb}：{name}'))
        job.finished.connect(self._finish_file_operation)
        job.start()

    def cancel_file_operation(self):
        if self._file_job is not None:
            self._file_job.requestInterruption()

    def _finish_file_operation(self):
        job = self._file_job
        result = self._last_file_result = job.result
        self._file_job = None
        job.deleteLater()
        self.operation_status.hide()
        self.cancel_operation.hide()
        message = f'{self._operation_verb}完成：{len(result.paths)} 项'
        if result.cancelled:
            message = f'{self._operation_verb}已取消，已完成 {len(result.paths)} 项'
        if result.errors:
            message += '\n' + '\n'.join(result.errors)
        if not getattr(self.window(), '_closing', False):
            if result.paths:
                self.select_file(str(result.paths[-1]))
            if result.errors:
                QMessageBox.warning(self, '文件操作失败', message)
        self.operationCompleted.emit(message.replace('\n', '; '))
