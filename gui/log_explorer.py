"""Filesystem-backed log explorer; one active measurement at a time."""
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QDir
from PySide6.QtGui import QShortcut, QKeySequence
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLineEdit, QTreeView, QLabel, QFileDialog, QFileSystemModel


class LogExplorer(QWidget):
    fileActivated = Signal(str)
    rootChanged = Signal(str)
    SUFFIXES = ('.blf', '.asc', '.trc', '.mf4', '.mdf', '.csv')

    def __init__(self, parent=None):
        super().__init__(parent)
        self.root_path = ''
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
        self.model.setNameFilters([f'*{text}*{suffix}' for suffix in self.SUFFIXES] if text else ['*' + suffix for suffix in self.SUFFIXES])

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
