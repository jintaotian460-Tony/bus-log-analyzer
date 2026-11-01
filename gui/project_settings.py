"""Project defaults and supplemental signal sources; databases use the existing manager."""
from __future__ import annotations

import copy
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)


class CloudImportDialog(QDialog):
    """Explicit candidate versions and platform-network -> BLF-channel bindings."""
    def __init__(self, settings=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Cloud CSV — CAN/LIN candidate matrices')
        self.resize(1020, 570)
        self.settings = copy.deepcopy(settings or {})
        self.time_column = QLineEdit(self.settings.get('time_column', '采集时间'))
        self.basis = QComboBox()
        self.basis.addItem('Local wall clock fields', 'wall')
        self.basis.addItem('Absolute time with UTC offset', 'absolute')
        self.basis.setCurrentIndex(max(0, self.basis.findData(self.settings.get('time_basis', 'wall'))))
        self.utc = QDoubleSpinBox()
        self.utc.setRange(-14, 14)
        self.utc.setValue(float(self.settings.get('utc_offset_hours', 8)))
        self.utc.setSuffix(' h')
        self.offset = QDoubleSpinBox()
        self.offset.setRange(-864000, 864000)
        self.offset.setDecimals(3)
        self.offset.setValue(float(self.settings.get('offset_seconds', 0)))
        self.offset.setSuffix(' s')
        self.telemetry = QLineEdit(self.settings.get('telemetry_database', ''))
        choose = QPushButton('Browse…')
        choose.clicked.connect(self._telemetry)
        row = QHBoxLayout()
        row.addWidget(self.telemetry, 1)
        row.addWidget(choose)
        form = QFormLayout()
        form.addRow('Timestamp column', self.time_column)
        form.addRow('Clock interpretation', self.basis)
        form.addRow('UTC offset', self.utc)
        form.addRow('Time adjustment', self.offset)
        form.addRow('Packed telemetry signal DBC (optional)', row)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(['Platform network(s)', 'Bus CAN/LIN', 'BLF channel', 'Fixed matrix file / version'])
        self.table.setColumnWidth(0, 150)
        self.table.setColumnWidth(1, 95)
        self.table.setColumnWidth(2, 90)
        self.table.horizontalHeader().setStretchLastSection(True)
        for spec in self.settings.get('matrices', []):
            self._row(spec)
        add = QPushButton('Add matrices…')
        add.clicked.connect(self._add)
        remove = QPushButton('Remove selected')
        remove.clicked.connect(lambda: self.table.removeRow(self.table.currentRow()) if self.table.currentRow() >= 0 else None)
        actions = QHBoxLayout()
        actions.addWidget(add)
        actions.addWidget(remove)
        actions.addStretch()
        note = QLabel('Leave the platform network blank to consider this matrix when ID and signal names match uniquely.\n'
                      'BLF channel numbers identify exported streams; they are not inferred physical bus numbers.\n'
                      'Apply locks the current matrix fingerprints. Replace/review changed versions here before importing.\n'
                      'Partial observations retain missing markers. Generated BLFs contain the decoder definitions.')
        note.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.table, 1)
        layout.addLayout(actions)
        layout.addWidget(note)
        layout.addWidget(buttons)

    def _telemetry(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Packed telemetry signal definition', self.telemetry.text(), 'DBC (*.dbc)')
        if path:
            self.telemetry.setText(path)

    def _row(self, spec):
        row = self.table.rowCount()
        self.table.insertRow(row)
        for col, key in enumerate(('network', 'bus', 'channel', 'path')):
            item = QTableWidgetItem(str(spec.get(key, '')))
            item.setToolTip(str(spec.get('sha256_locked', '')) if key == 'path' else '')
            self.table.setItem(row, col, item)

    def _add(self):
        paths, _ = QFileDialog.getOpenFileNames(self, 'Candidate CAN/LIN matrices', '', 'DBC / LDF (*.dbc *.ldf)')
        for path in paths:
            self._row(dict(network='', bus='LIN' if Path(path).suffix.lower() == '.ldf' else 'CAN',
                           channel=self.table.rowCount()+1, path=path))

    def value(self):
        from core.cloud_blf import sha256
        matrices = []
        channels = set()
        for row in range(self.table.rowCount()):
            values = [self.table.item(row, col).text().strip() for col in range(4)]
            network, bus, channel, path = values
            bus, channel = bus.upper(), int(channel)
            if bus not in ('CAN', 'LIN') or not 1 <= channel <= 254:
                raise ValueError('Choose CAN/LIN and a BLF channel between 1 and 254.')
            if (bus, channel) in channels:
                raise ValueError('Each candidate matrix needs a distinct BLF bus/channel.')
            channels.add((bus, channel))
            matrices.append(dict(network=network, bus=bus, channel=channel, path=str(Path(path).resolve()), sha256_locked=sha256(path)))
        telemetry = self.telemetry.text().strip()
        result = dict(self.settings, matrices=matrices, time_column=self.time_column.text().strip(),
                      time_basis=self.basis.currentData(), utc_offset_hours=self.utc.value(),
                      offset_seconds=self.offset.value(), telemetry_database=telemetry)
        if telemetry:
            result['telemetry_sha256_locked'] = sha256(telemetry)
        return result

    def _accept(self):
        try:
            self.value()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, 'Invalid matrix configuration', str(exc))
            return
        self.accept()


class CSVSourceDialog(QDialog):
    def __init__(self, spec=None, measurement='', parent=None):
        super().__init__(parent)
        self.setWindowTitle('CSV signal source')
        self.resize(720, 650)
        self.spec = copy.deepcopy(spec or {})
        self.measurement = measurement
        self.path = QLineEdit(self.spec.get('path', ''))
        self.path.setPlaceholderText('CSV file, or {measurement_stem}.csv beside the main recording')
        browse = QPushButton('Browse…')
        browse.clicked.connect(self._browse)
        path_row = QHBoxLayout()
        path_row.addWidget(self.path, 1)
        path_row.addWidget(browse)
        self.name = QLineEdit(self.spec.get('name', ''))
        self.encoding = QComboBox()
        for label, value in [('Automatic (UTF-8 / GB18030)', 'auto'), ('UTF-8', 'utf-8-sig'), ('GB18030 / GBK', 'gb18030')]:
            self.encoding.addItem(label, value)
        self.encoding.setCurrentIndex(max(0, self.encoding.findData(self.spec.get('encoding', 'auto'))))
        self.delimiter = QComboBox()
        for label, value in [('Comma', ','), ('Semicolon', ';'), ('Tab', '\t')]:
            self.delimiter.addItem(label, value)
        self.delimiter.setCurrentIndex(max(0, self.delimiter.findData(self.spec.get('delimiter', ','))))
        self.time_column = QComboBox()
        self.time_column.setEditable(True)
        self.time_column.setCurrentText(self.spec.get('time_column', ''))
        self.basis = QComboBox()
        for label, value in [('Cloud BLF header contains local clock fields', 'wall'),
                             ('Absolute date/time with UTC offset', 'absolute'),
                             ('Relative seconds from recording zero', 'relative')]:
            self.basis.addItem(label, value)
        self.basis.setCurrentIndex(max(0, self.basis.findData(self.spec.get('time_basis', 'wall'))))
        self.basis.setToolTip('Wall clock: for a BLF header containing the same clock fields as the CSV.\n'
                              'Absolute: interpret CSV dates in the UTC offset below.\n'
                              'Relative: the CSV already contains seconds on the recording time axis.')
        self.utc_offset = QDoubleSpinBox()
        self.utc_offset.setRange(-14, 14)
        self.utc_offset.setSuffix(' h')
        self.utc_offset.setValue(float(self.spec.get('utc_offset_hours', 0)))
        self.offset = QDoubleSpinBox()
        self.offset.setRange(-864000, 864000)
        self.offset.setDecimals(3)
        self.offset.setSuffix(' s')
        self.offset.setValue(float(self.spec.get('offset_seconds', 0)))
        self.prefix = QLineEdit(self.spec.get('prefix', ''))
        select = QPushButton('Select prefix')
        select.clicked.connect(self._select_prefix)
        prefix_row = QHBoxLayout()
        prefix_row.addWidget(self.prefix, 1)
        prefix_row.addWidget(select)
        form = QFormLayout()
        form.addRow('File', path_row)
        form.addRow('Signal group', self.name)
        form.addRow('Encoding', self.encoding)
        form.addRow('Delimiter', self.delimiter)
        form.addRow('Timestamp column', self.time_column)
        form.addRow('Time alignment', self.basis)
        form.addRow('CSV UTC offset', self.utc_offset)
        form.addRow('Additional time adjustment', self.offset)
        form.addRow('Signal name prefix', prefix_row)
        inspect = QPushButton('Read columns')
        inspect.clicked.connect(self._read_columns)
        self.columns = QListWidget()
        self.columns.setToolTip('Only checked columns are imported. Descriptions are shown in tooltips.')
        self.status = QLabel('Choose a file and read its columns')
        self.status.setWordWrap(True)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self._accept_source)
        self.buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(inspect)
        layout.addWidget(self.columns, 1)
        layout.addWidget(self.status)
        layout.addWidget(self.buttons)
        self.basis.currentIndexChanged.connect(lambda: self.utc_offset.setEnabled(self.basis.currentData() == 'absolute'))
        self.utc_offset.setEnabled(self.basis.currentData() == 'absolute')
        from core.supplemental_csv import source_path
        if self.path.text() and source_path(self.spec, measurement or 'recording.blf').is_file():
            self._read_columns()

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Supplemental CSV signals', self.path.text(), 'CSV files (*.csv)')
        if path:
            self.path.setText(path)
            if not self.name.text():
                self.name.setText(Path(path).stem)
            self._read_columns()

    def _read_columns(self):
        from core.supplemental_csv import csv_header, source_path, signal_name
        self.columns.clear()
        try:
            path = source_path({'path': self.path.text()}, self.measurement or 'recording.blf')
            _, header = csv_header(path, self.encoding.currentData(), self.delimiter.currentData())
        except (ValueError, OSError, UnicodeError) as exc:
            self.status.setText(str(exc))
            return
        selected = {}
        for entry in self.spec.get('columns', []):
            entry = {'column': entry} if isinstance(entry, str) else entry
            selected[entry['column']] = entry
        previous_time = self.time_column.currentText()
        self.time_column.clear()
        self.time_column.addItems(header)
        self.time_column.setCurrentText(previous_time if previous_time in header else header[0])
        for column in header:
            if column == self.time_column.currentText():
                continue
            name = signal_name(column)
            entry = selected.get(column, selected.get(name, {'column': name}))
            item = QListWidgetItem(name)
            item.setToolTip(column)
            item.setData(Qt.ItemDataRole.UserRole, entry)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            chosen = column in selected or name in selected
            if not selected:
                chosen = bool(self.prefix.text()) and name.startswith(self.prefix.text())
            item.setCheckState(Qt.CheckState.Checked if chosen else Qt.CheckState.Unchecked)
            self.columns.addItem(item)
        self.status.setText(f'{len(header)} columns read; check the physical signal columns to import')

    def _select_prefix(self):
        prefix = self.prefix.text().strip()
        for index in range(self.columns.count()):
            item = self.columns.item(index)
            item.setCheckState(Qt.CheckState.Checked if prefix and item.text().startswith(prefix)
                               else Qt.CheckState.Unchecked)

    def value(self):
        result = dict(self.spec)
        result.update(path=self.path.text().strip(), name=self.name.text().strip(),
                      encoding=self.encoding.currentData(), delimiter=self.delimiter.currentData(),
                      time_column=self.time_column.currentText().strip(), time_basis=self.basis.currentData(),
                      utc_offset_hours=self.utc_offset.value(), offset_seconds=self.offset.value(),
                      prefix=self.prefix.text().strip(),
                      columns=[self.columns.item(i).data(Qt.ItemDataRole.UserRole)
                               for i in range(self.columns.count())
                               if self.columns.item(i).checkState() == Qt.CheckState.Checked])
        return result

    def _accept_source(self):
        value = self.value()
        if not value['path'] or not value['name'] or not value['time_column'] or not value['columns']:
            QMessageBox.warning(self, 'Incomplete CSV source', 'Choose a file, group, timestamp column and at least one signal.')
            return
        from core.supplemental_csv import signal_name
        if any(c['column'] in (value['time_column'], signal_name(value['time_column'])) for c in value['columns']):
            QMessageBox.warning(self, 'Timestamp selected as signal', 'Uncheck the timestamp column in the signal list.')
            return
        if '{measurement_stem}' not in value['path'] and not Path(value['path']).is_absolute():
            from core.supplemental_csv import source_path
            self.path.setText(str(source_path(value, self.measurement or 'recording.blf').resolve()))
        self.accept()


class ProjectSettingsDialog(QDialog):
    def __init__(self, project, playback, measurement='', parent=None):
        super().__init__(parent)
        self.setWindowTitle('Project settings')
        self.resize(800, 450)
        self.project = copy.deepcopy(project)
        self.playback = copy.deepcopy(playback)
        self.measurement = measurement
        self.sources = copy.deepcopy(project.get('csv_sources', []))
        self.cloud_import = copy.deepcopy(project.get('cloud_import', {}))
        self.name = QLineEdit(project.get('name', ''))
        self.log_root = QLineEdit(project.get('log_root', ''))
        folder = QPushButton('Browse…')
        folder.clicked.connect(self._folder)
        root_row = QHBoxLayout()
        root_row.addWidget(self.log_root, 1)
        root_row.addWidget(folder)
        self.clock = QComboBox()
        self.clock.addItem('BLF UTC → local', 'utc')
        self.clock.addItem('Cloud local wall time', 'wall')
        self.clock.setCurrentIndex(max(0, self.clock.findData(playback.get('time_mode', 'utc'))))
        self.timeout = QDoubleSpinBox()
        self.timeout.setRange(0, 3600)
        self.timeout.setDecimals(2)
        self.timeout.setSuffix(' s')
        self.timeout.setSpecialValueText('Off')
        self.timeout.setValue(float(playback.get('gap_timeout', 0)))
        form = QFormLayout()
        form.addRow('Project name', self.name)
        form.addRow('Log explorer folder', root_row)
        form.addRow('Recording clock', self.clock)
        form.addRow('Stale after', self.timeout)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(['Signal group', 'CSV source', 'Time alignment'])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(1, 390)
        self._refresh()
        buttons_row = QHBoxLayout()
        for label, slot in [('Add CSV…', self._add), ('Edit…', self._edit), ('Remove', self._remove)]:
            button = QPushButton(label)
            button.clicked.connect(slot)
            buttons_row.addWidget(button)
        buttons_row.addStretch(1)
        matrices = QPushButton('CAN/LIN import matrices…')
        matrices.clicked.connect(self._matrices)
        buttons_row.addWidget(matrices)
        note = QLabel('DBC/channel mappings are configured in Databases and saved with the project.\n'
                      'Apply reloads the selected measurement with these CSV sources. Save project creates a reusable profile.')
        note.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.table, 1)
        layout.addLayout(buttons_row)
        layout.addWidget(note)
        layout.addWidget(buttons)

    def _refresh(self):
        self.table.setRowCount(len(self.sources))
        for row, source in enumerate(self.sources):
            for col, key in enumerate(('name', 'path', 'time_basis')):
                value = source.get(key, '')
                if key == 'time_basis':
                    value = {'wall': 'Cloud wall clock', 'absolute': 'Absolute / UTC offset',
                             'relative': 'Relative seconds'}.get(value, value)
                item = QTableWidgetItem(str(value))
                item.setToolTip(item.text())
                self.table.setItem(row, col, item)

    def _add(self):
        dialog = CSVSourceDialog(measurement=self.measurement, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.sources.append(dialog.value())
            self._refresh()

    def _edit(self):
        row = self.table.currentRow()
        if row < 0:
            return
        dialog = CSVSourceDialog(self.sources[row], self.measurement, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.sources[row] = dialog.value()
            self._refresh()

    def _remove(self):
        row = self.table.currentRow()
        if row >= 0:
            self.sources.pop(row)
            self._refresh()

    def value(self):
        project = dict(self.project, name=self.name.text().strip(), csv_sources=self.sources,
                       log_root=self.log_root.text().strip(), cloud_import=self.cloud_import)
        playback = dict(self.playback, time_mode=self.clock.currentData(), gap_timeout=self.timeout.value())
        return project, playback

    def _folder(self):
        path = QFileDialog.getExistingDirectory(self, 'Project log folder', self.log_root.text())
        if path:
            self.log_root.setText(path)

    def _matrices(self):
        dialog = CloudImportDialog(self.cloud_import, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.cloud_import = dialog.value()
