"""Filesystem-backed log explorer; one active measurement at a time."""
import shutil
import zipfile
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QDir, QMimeData, QProcess, QUrl
from PySide6.QtGui import QShortcut, QKeySequence, QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QFileSystemModel, QHBoxLayout, QLabel,
    QLineEdit, QMenu, QMessageBox, QPushButton, QTreeView, QVBoxLayout, QWidget,
)


class LogExplorer(QWidget):
    fileActivated = Signal(str)
    rootChanged = Signal(str)
    operationCompleted = Signal(str)
    SUFFIXES = ('.blf', '.asc', '.trc', '.mf4', '.mdf', '.csv')
    ARCHIVE_SUFFIXES = ('.zip',)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.root_path = ''
        self._copied_paths = []
        self.model = QFileSystemModel(self)
        self.model.setReadOnly(True)
        self.model.setFilter(QDir.Filter.AllDirs | QDir.Filter.Files | QDir.Filter.NoDotAndDotDot)
        self.model.setNameFilterDisables(False)
        self.model.setNameFilters(['*' + suffix for suffix in self.SUFFIXES])
        self.tree = QTreeView()
        self.tree.setModel(self.model)
        self.tree.setHeaderHidden(True)
        self.tree.setTextElideMode(Qt.TextElideMode.ElideMiddle)
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
        path = QFileDialog.getExistingDirectory(self, 'Project log folder', self.root_path)
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
        return [
            Path(self.model.filePath(index))
            for index in self.tree.selectionModel().selectedRows(0)
            if index.isValid()
        ]

    def _path_at(self, position):
        index = self.tree.indexAt(position)
        if not index.isValid():
            return Path(self.root_path) if self.root_path else None
        if not self.tree.selectionModel().isSelected(index):
            self.tree.setCurrentIndex(index)
        return Path(self.model.filePath(index))

    def _show_context_menu(self, position):
        path = self._path_at(position)
        if path is None:
            return
        menu = self._context_menu(path)
        menu.exec(self.tree.viewport().mapToGlobal(position))

    def _context_menu(self, path):
        path = Path(path)
        selected = self._selected_paths()
        target = path if path.is_dir() else path.parent
        menu = QMenu(self)
        open_action = menu.addAction('打开所在文件夹' if path.is_file() else '打开文件夹')
        open_action.triggered.connect(lambda: self._open_in_explorer(path))

        if selected:
            copy_path_action = menu.addAction('复制路径')
            copy_path_action.triggered.connect(lambda: self._copy_paths_to_clipboard(selected))
            copy_action = menu.addAction('复制')
            copy_action.triggered.connect(lambda: self._copy_files(selected))
            compress_action = menu.addAction('压缩为 ZIP')
            compress_action.triggered.connect(lambda: self._compress_to_zip(selected))

        paste_action = menu.addAction('粘贴到此处')
        paste_action.setEnabled(bool(self._clipboard_paths()))
        paste_action.triggered.connect(lambda: self._paste_files(target))

        if path.is_file() and path.suffix.lower() in self.ARCHIVE_SUFFIXES:
            menu.addSeparator()
            extract_action = menu.addAction('解压 ZIP')
            extract_action.triggered.connect(lambda: self._extract_zip(path))

        return menu

    def _open_in_explorer(self, path):
        path = Path(path)
        if path.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
            return
        if QProcess.startDetached('explorer.exe', ['/select,', str(path)]):
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))

    def _copy_paths_to_clipboard(self, paths):
        self._copied_paths = []
        QApplication.clipboard().setText('\r\n'.join(str(path) for path in paths))
        self.operationCompleted.emit(f'Copied {len(paths)} path(s) to the clipboard')

    def _copy_files(self, paths):
        paths = [path for path in paths if path.exists()]
        if not paths:
            return
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
        QApplication.clipboard().setMimeData(mime)
        self._copied_paths = [str(path) for path in paths]
        self.operationCompleted.emit(f'Copied {len(paths)} item(s)')

    def _clipboard_paths(self):
        if self._copied_paths:
            return [Path(path) for path in self._copied_paths if Path(path).exists()]

        mime = QApplication.clipboard().mimeData()
        urls = mime.urls() if mime is not None else []
        paths = [Path(url.toLocalFile()) for url in urls if url.isLocalFile()]
        return [path for path in paths if path.exists()]

    def _paste_files(self, target):
        target = Path(target)
        if not target.is_dir():
            return
        created, errors = [], []
        for source in self._clipboard_paths():
            source = source.resolve()
            try:
                if source == target.resolve() or target.resolve() in source.parents:
                    raise OSError(f'Cannot paste {source.name} into itself')
                destination = self._unique_path(target / source.name)
                if source.is_file():
                    shutil.copy2(source, destination)
                else:
                    shutil.copytree(source, destination)
                created.append(destination)
            except OSError as exc:
                errors.append(f'{source.name}: {exc}')
        self._report_file_operation('Pasted', created, errors)

    def _compress_to_zip(self, paths):
        paths = [path for path in paths if path.exists()]
        if not paths:
            return
        stem = paths[0].name if len(paths) == 1 else paths[0].parent.name
        errors = []
        try:
            destination = self._unique_path(paths[0].parent / f'{stem}.zip')
            with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
                for path in paths:
                    self._write_zip_entry(archive, path)
        except OSError as exc:
            errors.append(f'{stem}: {exc}')
            destination = locals().get('destination')
            if destination is not None and destination.exists():
                destination.unlink()
            destination = None
        if destination is not None:
            self._report_file_operation('Zipped', [destination], errors)
        else:
            self._report_file_operation('Zipped', [], errors)

    def _write_zip_entry(self, archive, path):
        path = Path(path)
        if path.is_file():
            archive.write(path, path.name)
            return
        for child in path.rglob('*'):
            archive.write(child, child.relative_to(path.parent))

    def _extract_zip(self, archive_path):
        archive_path = Path(archive_path)
        errors = []
        try:
            destination = self._unique_path(archive_path.parent / archive_path.stem)
            with zipfile.ZipFile(archive_path) as archive:
                root = destination.resolve()
                for member in archive.infolist():
                    target = (destination / member.filename).resolve()
                    if root != target and root not in target.parents:
                        raise OSError(f'unsafe entry: {member.filename}')
                    if member.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with archive.open(member) as source, target.open('wb') as output:
                            shutil.copyfileobj(source, output)
        except (OSError, zipfile.BadZipFile) as exc:
            errors.append(f'{archive_path.name}: {exc}')
            destination = locals().get('destination')
            if destination is not None and destination.exists():
                shutil.rmtree(destination)
            destination = None
        if destination is not None:
            self._report_file_operation('Extracted', [destination], errors)
        else:
            self._report_file_operation('Extracted', [], errors)

    @staticmethod
    def _unique_path(path):
        if not path.exists():
            return path
        stem, suffix = path.stem, path.suffix
        for number in range(1, 10000):
            candidate = path.with_name(f'{stem} ({number}){suffix}')
            if not candidate.exists():
                return candidate
        raise OSError(f'Could not find a free name for {path.name}')

    def _report_file_operation(self, verb, paths, errors):
        if paths:
            self.select_file(str(paths[-1]))
        message = f'{verb} {len(paths)} item(s)' if paths else f'{verb} failed'
        if errors:
            message += '\n' + '\n'.join(errors)
            QMessageBox.warning(self, 'File operation', message)
        self.operationCompleted.emit(message.replace('\n', '; '))
