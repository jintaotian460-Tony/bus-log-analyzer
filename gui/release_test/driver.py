# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""Drive one release-test scenario inside a running copy of the application.

The runner starts the application with ``--release-test-scenario <file>``.
Once the main window is up, the driver works it the way a user does: Open
Measurement, Databases, Decode, the plot buttons, closing the window.

Only the file dialog is answered directly, because it is the operating
system's window, not the application's. Every dialog the application opens
itself stays on screen for a moment, so the real platform paints it, and is
then answered: OK in the Database Manager, the cancelling button in a message
box. Each dialog's title is recorded against the step that raised it.

The result file is rewritten after every step, so the runner still has every
step up to a crash.
"""

from __future__ import annotations

import ctypes
import gc
import json
import os
import sys
import threading
import time
import tracemalloc
import weakref
from pathlib import Path

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QInputDialog
from shiboken6 import Shiboken

from core.bus_types import decode_key
from core.channel_config import ChannelConfig
from gui.dbc_manager import DBCManagerDialog

TICK_MS = 20
DIALOG_SECONDS = 0.4        # how long a dialog stays up before it is answered
LOAD_TIMEOUT = 900.0
CLOSE_AFTER_SECONDS = 0.1   # after the load thread starts: small files load in 0.5 s
PLOTTED_SIGNALS = 8
TIMED_PLOT_SIGNALS = 5      # plotted in Stacked by a timing scenario
# A timing scenario loads once the main window's background import of the
# readers has finished, as it would after a user has picked a file.
PRELOAD_THREAD = 'Measurement support preload'
SETTLE_SECONDS = 1.0
# A message box is answered with the first of these its buttons have.
_ANSWER_ROLES = (
    QMessageBox.ButtonRole.RejectRole, QMessageBox.ButtonRole.NoRole,
    QMessageBox.ButtonRole.AcceptRole, QMessageBox.ButtonRole.YesRole,
)


class ScenarioDriver(QObject):
    def __init__(self, window, spec: dict) -> None:
        super().__init__(window)
        self._window = window
        self._actions = window._toolbar_actions
        self._files: list[dict] = spec['files']
        self._steps: list[dict] = spec['steps']
        self._inheritance_files = spec.get('inheritance_files', [])
        self._detail_file = spec.get('detail_file')
        self._template_answers = []
        self._result_path = Path(spec['result'])
        # The driver starts right after the main window is shown. Start-up
        # times are these stamps less the runner's launch time: both read
        # time.perf_counter(), the system's performance counter.
        splash = getattr(window, '_splash', None)
        self._result = {
            'pid': os.getpid(), 'memory_checks': bool(sys.flags.dev_mode),
            'steps': [], 'finished': False,
            'splash_at': getattr(splash, 'shown_at', None),
            'window_at': time.perf_counter(),
        }
        self._timing = bool(spec.get('timing'))
        self._listed_at = 0.0
        self._current_file: dict | None = None
        self._pending_paths: list[str] = []
        self._dialogs: list[str] = []
        self._modal_address = 0
        self._modal_since = 0.0
        # How long dialogs waited on screen for their answer: a person's time,
        # left out of what a timing scenario reports.
        self._modal_shown_at = 0.0
        self._dialog_ms = 0.0
        self._stores: list[weakref.ref] = []
        self._stepping = False
        if sys.flags.dev_mode:
            # The memory checks name the damaged block; this names the code
            # that allocated it. Only what is allocated from here on.
            tracemalloc.start(5)
        QFileDialog.getOpenFileName = staticmethod(self._answer_file_dialog)
        self._script = self._run()
        # Two timers: Qt does not fire a timer again while its own slot runs,
        # and a step that opens a modal dialog is still inside that slot.
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._step)
        self._timer.start(TICK_MS)
        self._dialog_timer = QTimer(self)
        self._dialog_timer.timeout.connect(self._answer_dialog)
        self._dialog_timer.start(TICK_MS)
        # Runs once the event loop does: the window answers from then on.
        QTimer.singleShot(0, self._note_answering)

    # ── Event loop ───────────────────────────────────────────────────────

    def _step(self) -> None:
        # processEvents() inside a step must not start the next one, and the
        # script waits while a dialog is open.
        if self._stepping or QApplication.activeModalWidget() is not None:
            return
        self._stepping = True
        try:
            next(self._script)
        except StopIteration:
            self._stop()
        except Exception:
            import traceback
            self._result['error'] = traceback.format_exc()
            self._save()
            self._stop()
            self._window.close()
        finally:
            self._stepping = False

    def _note_answering(self) -> None:
        self._result['answering_at'] = time.perf_counter()

    def _stop(self) -> None:
        self._timer.stop()
        self._dialog_timer.stop()

    def _answer_file_dialog(self, *_args, **_kwargs) -> tuple[str, str]:
        if not self._pending_paths:
            self._dialogs.append('unexpected file dialog, cancelled')
            return '', ''
        return self._pending_paths.pop(0), ''

    def _answer_dialog(self) -> None:
        """Answer the open modal dialog once it has been up a moment."""
        dialog = QApplication.activeModalWidget()
        if dialog is None:
            self._modal_address = 0
            return
        address = Shiboken.getCppPointer(dialog)[0]
        if address != self._modal_address:
            self._modal_address, self._modal_since = address, time.monotonic()
            self._modal_shown_at = time.perf_counter()
            return
        if time.monotonic() - self._modal_since < DIALOG_SECONDS:
            return
        self._modal_address = 0
        self._dialog_ms += (time.perf_counter() - self._modal_shown_at) * 1000
        self._dialogs.append(dialog.windowTitle() or type(dialog).__name__)
        if isinstance(dialog, QInputDialog) and self._template_answers:
            dialog.setTextValue(self._template_answers.pop(0))
            dialog.accept()
        elif isinstance(dialog, DBCManagerDialog):
            dialog.accept()
        elif isinstance(dialog, QMessageBox):
            if dialog.windowTitle() in {'删除模板', '覆盖模板'}:
                dialog.button(QMessageBox.StandardButton.Yes).click()
                return
            buttons = {dialog.buttonRole(button): button for button in dialog.buttons()}
            role = next((role for role in _ANSWER_ROLES if role in buttons), None)
            if role is None:
                dialog.reject()
            else:
                buttons[role].click()
        else:
            dialog.reject()

    def _wait(self, seconds: float):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            yield

    # ── Scenario ─────────────────────────────────────────────────────────

    def _run(self):
        if not self._timing:  # a timing scenario opens its file at once
            yield from self._wait(1.0)  # the splash hands over to the window
        for step in self._steps:
            record = {'op': step['op']}
            self._dialogs = []
            started = time.monotonic()
            if step['op'] == 'open':
                self._open(self._files[step['file']], record)
            elif step['op'] == 'settle':
                yield from self._settle()
            elif step['op'] == 'load':
                yield from self._load(record)
            elif step['op'] == 'plot':
                yield from self._plot(record)
            elif step['op'] == 'plot_stacked':
                self._plot_stacked(record)
            elif step['op'] == 'playback':
                yield from self._playback(record)
            elif step['op'] == 'panels':
                self._panels(record)
            elif step['op'] == 'explorer_hidden_load':
                yield from self._explorer_hidden_load(record)
            elif step['op'] == 'log_file_operations':
                yield from self._log_file_operations(record)
            elif step['op'] == 'signal_window_controls':
                yield from self._signal_window_controls(record)
            elif step['op'] == 'signal_inheritance':
                yield from self._signal_inheritance(record)
            elif step['op'] == 'plot_detail':
                yield from self._plot_detail(record)
            elif step['op'] == 'window_templates':
                yield from self._window_templates(record)
            elif step['op'] == 'compact_layout':
                yield from self._compact_layout(record)
            elif step['op'] == 'plot_controls':
                yield from self._plot_controls(record)
            elif step['op'] == 'close_during_load':
                yield from self._close_during_load(record)
                return
            yield from self._wait(0.2)
            self._finish_step(record, started)
        yield from self._wait(1.0)
        self._result['finished'] = True
        self._save()
        self._stop()
        self._window.close()

    def _finish_step(self, record: dict, started: float) -> None:
        record['seconds'] = round(time.monotonic() - started, 1)
        record['dialogs'] = list(self._dialogs)
        record.update(_memory())
        self._result['steps'].append(record)
        self._save()

    def _open(self, file: dict, record: dict) -> None:
        window = self._window
        record['file'] = file['alias']
        self._current_file = file
        self._pending_paths.append(file['path'])
        self._dialog_ms = 0.0
        self._actions['Open Measurement'].trigger()
        record['opened_at'] = time.perf_counter()
        record['open_dialog_ms'] = round(self._dialog_ms, 1)
        record['opened'] = window.measurement_path == file['path']
        planned = ChannelConfig(
            name='Release test',
            channels={decode_key(key): path for key, path in file['databases'].items()},
        )
        if planned.channels == window.channel_config.channels:
            return  # a user keeps the databases already assigned
        if planned.is_empty():
            # The Database Manager removes rows one by one; the result is this.
            window.channel_config = ChannelConfig()
            return
        # As if loaded through the Database Manager's Load Channel Config.
        window.channel_config = planned
        self._actions['Databases'].trigger()
        record['databases_assigned'] = window.channel_config.channels == planned.channels

    def _load(self, record: dict):
        window = self._window
        record['file'] = self._current_file['alias'] if self._current_file else None
        action = self._actions['Decode']
        if not action.isEnabled():
            record['outcome'] = 'Decode was greyed out'
            return
        self._listed_at = 0.0
        clicked = time.perf_counter()
        action.trigger()
        if window._worker is not None:
            # Connected after the window's own slot, so it runs once the
            # signal tree has been given the signals.
            window._worker.finished.connect(self._on_load_finished)
        deadline = time.monotonic() + LOAD_TIMEOUT
        while window._thread is not None:
            if time.monotonic() > deadline:
                record['outcome'] = 'timed out'
                return
            yield
        yield from self._wait(0.3)  # results shown, any dialog answered
        if self._listed_at:
            record['load_ms'] = round((self._listed_at - clicked) * 1000, 1)
        store = window.store
        if store is None:
            record['outcome'] = 'failed' if 'Load failed' in self._dialogs else 'not loaded'
            return
        record.update(
            outcome='loaded', signals=len(store.all_keys()), frames=store.total_frames,
            decoded=store.decoded_frames, samples=store.total_samples,
            warnings=len(getattr(store, 'load_warnings', ()) or ()),
        )
        # Each Decode must free the measurement it replaces.
        gc.collect()
        self._stores.append(weakref.ref(store))
        record['stores_alive'] = sum(ref() is not None for ref in self._stores)

    def _on_load_finished(self, _store) -> None:
        self._window.signal_tree.repaint()
        self._listed_at = time.perf_counter()

    def _settle(self):
        deadline = time.monotonic() + LOAD_TIMEOUT
        while (any(thread.name == PRELOAD_THREAD for thread in threading.enumerate())
               and time.monotonic() < deadline):
            yield
        yield from self._wait(SETTLE_SECONDS)

    def _plot_stacked(self, record: dict) -> None:
        """Plot five signals in Stacked and time it until they are drawn.

        The time is split into its phases, with how long the GUI thread
        worked in it and any garbage collection, so a slow round shows where
        its time went.
        """
        window = self._window
        record['file'] = self._current_file['alias'] if self._current_file else None
        store = window.store
        if store is None:
            record['outcome'] = 'nothing to plot'
            return
        window.btn_windows.setChecked(True)
        keys = sorted(store.all_keys())[:TIMED_PLOT_SIGNALS]
        with _Collections() as collections:
            started = time.perf_counter()
            worked = time.thread_time()
            window.add_signals_to_plot(keys)
            added = time.perf_counter()
            QApplication.processEvents()
            handled = time.perf_counter()
            window.plot_panel.repaint()
            drawn = time.perf_counter()
            worked = time.thread_time() - worked
        record['plot_ms'] = round((drawn - started) * 1000, 1)
        record['plot_phases_ms'] = {
            'add': round((added - started) * 1000, 1),
            'events': round((handled - added) * 1000, 1),
            'repaint': round((drawn - handled) * 1000, 1),
        }
        record['plot_busy_ms'] = round(worked * 1000, 1)
        record['plot_collections'] = collections.found
        record['plotted'] = len(window.plot_panel.plotted_keys())
        record['outcome'] = 'plotted'

    def _plot(self, record: dict):
        window = self._window
        record['file'] = self._current_file['alias'] if self._current_file else None
        store = window.store
        if store is None:
            record['outcome'] = 'nothing to plot'
            return
        window.add_signals_to_plot(sorted(store.all_keys())[:PLOTTED_SIGNALS])
        record['plotted'] = len(window.plot_panel.plotted_keys())
        yield from self._wait(0.3)
        for button in (window.btn_cursor1, window.btn_cursor2):
            for _ in range(2):  # on, then off
                button.click()
                yield from self._wait(0.2)
        window.btn_clear_plot.click()
        record['outcome'] = 'plotted'

    def _playback(self, record: dict):
        """Exercise the packaged timer, cursor readouts and a real data gap."""
        from core.playback import data_gaps
        window = self._window
        if window.store is None:
            record['outcome'] = 'nothing to play'
            return
        keys = window.store.all_keys()
        can_key = next((k for k in keys if k.startswith('CH')), keys[0])
        lin_key = next((k for k in keys if 'ACT7ual_position' in k), keys[-1])
        window.add_signals_to_plot([can_key, lin_key])
        window.playback.apply_config(dict(window.playback.to_config(), gap_timeout=2))
        start = window.playback.clock.start + 1
        window.playback.seek(start)
        window.playback.toggle()
        yield from self._wait(0.4)
        window.playback.pause()
        record['timer_advanced'] = window.playback.clock.position > start + 0.15
        record['cursor_synchronised'] = abs(window.plot_panel.v_line.value()-window.playback.clock.position) < 1e-6
        record['time_mode'] = window.playback.time_mode.currentData() if hasattr(window.playback, 'time_mode') else window.playback.to_config()['time_mode']
        series = window.store.get_series(lin_key)
        gaps = data_gaps(series.timestamps, window.playback.to_config()['gap_timeout'])
        if gaps:
            gap = max(gaps, key=lambda interval: interval[1]-interval[0])
            window.playback.seek(sum(gap)/2)
            row = window.plot_panel._row_lookup[lin_key]
            record['gap_readout'] = window.plot_panel.table.item(row, 2).text().startswith('Stale')
        record['outcome'] = 'played'

    def _panels(self, record: dict):
        window = self._window
        window._act_explorer.setChecked(True)
        window.left_dock.show()
        window._act_explorer.trigger()
        record['explorer_hidden'] = not window.left_dock.isVisible()
        window._act_explorer.trigger()
        record['explorer_restored'] = window.left_dock.isVisible()
        window._act_signals.setChecked(False)
        window.signal_dock.hide()
        window._act_signals.trigger()
        record['signals_floating'] = window.signal_dock.isVisible() and window.signal_dock.isFloating()
        record['outcome'] = 'panels toggled'
        window.grab().save(str(self._result_path.with_suffix('.png')))
        window.signal_dock.grab().save(str(self._result_path.with_name(self._result_path.stem + '-signals.png')))

    def _signal_window_controls(self, record: dict):
        """Check the actual Win32 caption styles and its system commands."""
        window = self._window
        panel = window.signal_dock
        panel.showNormal()
        panel.raise_()
        panel.activateWindow()
        yield from self._wait(0.3)
        normal = panel.size()
        native = ctypes.WinDLL('user32', use_last_error=True)
        native.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        native.GetWindowLongW.restype = ctypes.c_long
        native.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
        native.PostMessageW.restype = ctypes.c_bool
        handle = int(panel.winId())
        style = native.GetWindowLongW(handle, -16)
        record['native_caption'] = (style & 0xC00000) == 0xC00000
        record['minimize_button'] = bool(style & 0x20000)
        record['maximize_button'] = bool(style & 0x10000)
        record['system_menu'] = bool(style & 0x80000)
        frame = panel.frameGeometry()
        panel.screen().grabWindow(0, frame.x(), frame.y(), frame.width(), frame.height()).save(str(self._result_path.with_name(self._result_path.stem+'-caption.png')))
        native.PostMessageW(handle, 0x112, 0xF020, 0)
        yield from self._wait(0.3)
        record['only_signal_window_minimized'] = panel.isMinimized() and not window.isMinimized()
        window._act_signals.trigger()
        yield from self._wait(0.3)
        record['signals_restores_minimized_window'] = panel.isVisible() and not panel.isMinimized()
        handle = int(panel.winId())
        restored_style = native.GetWindowLongW(handle, -16)
        record['buttons_after_restore'] = bool(restored_style & 0x20000) and bool(restored_style & 0x10000)
        native.PostMessageW(handle, 0x112, 0xF030, 0)
        yield from self._wait(0.3)
        record['maximized'] = panel.isMaximized() and (panel.width() > normal.width() or panel.height() > normal.height())
        handle = int(panel.winId())
        native.PostMessageW(handle, 0x112, 0xF120, 0)
        yield from self._wait(0.3)
        record['restored_normal'] = not panel.isMaximized() and not panel.isMinimized()
        handle = int(panel.winId())
        native.PostMessageW(handle, 0x112, 0xF060, 0)
        yield from self._wait(0.3)
        record['close_hides_only'] = panel.isHidden() and window.isVisible()
        window._act_signals.trigger()
        record['signals_restores_closed_window'] = panel.isVisible()
        record['outcome'] = 'window controls exercised'

    def _explorer_hidden_load(self, record: dict):
        """Real Qt mouse events must load once and respect a hidden tree."""
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        window = self._window
        path = Path(self._current_file['path'])
        window.signal_dock.close()
        record['signals_hidden_before'] = window.signal_dock.isHidden()
        window.left_dock.show()
        explorer = window.log_explorer
        explorer.set_root(str(path.parent))
        tree = explorer.tree
        deadline = time.monotonic()+15
        index = explorer.model.index(str(path))
        while not index.isValid() or tree.visualRect(index).isEmpty():
            if time.monotonic() > deadline:
                record['outcome'] = 'file row unavailable'
                return
            yield
            index = explorer.model.index(str(path))
        tree.scrollTo(index)
        yield from self._wait(0.2)
        point = tree.visualRect(index).center()
        requests = []
        request_received = requests.append
        explorer.fileActivated.connect(request_received)
        try:
            QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=point)
            QTest.mouseDClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=point)
            record['double_click_started_load'] = window._thread is not None
            deadline = time.monotonic()+LOAD_TIMEOUT
            while window._thread is not None:
                if time.monotonic() > deadline:
                    record['outcome'] = 'timed out'
                    return
                yield
            record['load_requests'] = len(requests)
            record['signals_still_hidden'] = window.signal_dock.isHidden()
            record['signals'] = len(window.store.all_keys()) if window.store else 0
            record['outcome'] = 'loaded' if window.store else 'not loaded'
            record['saved_visibility'] = window._configuration_dict()['panels']['signals_visible']
            window._act_signals.trigger()
            record['signals_restored'] = window.signal_dock.isVisible()
        finally:
            explorer.fileActivated.disconnect(request_received)

    def _log_file_operations(self, record: dict):
        """Use actual context-menu events/actions, then verify newly written data."""
        import tempfile
        import shutil
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QContextMenuEvent
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication, QMenu

        window = self._window
        root = Path(tempfile.mkdtemp(prefix='log-ops-', dir=self._result_path.parent))
        package = root / 'case'
        destination = root / 'paste'
        package.mkdir()
        destination.mkdir()
        source = root / 'Recorder.csv'
        source.write_text('Timestamps [s],X\n0,10\n', encoding='utf-8')
        shutil.copy2(source, package / source.name)
        notes = package / 'notes'
        notes.mkdir()
        (notes / 'note.txt').write_text('keep me', encoding='utf-8')

        explorer = window.log_explorer
        window.left_dock.show()
        window.signal_dock.hide()
        explorer.set_root(str(root))
        tree = explorer.tree

        def context(path):
            deadline = time.monotonic() + 10
            index = explorer.model.index(str(path))
            while True:
                tree.scrollTo(index)
                if index.isValid() and not tree.visualRect(index).isEmpty():
                    break
                if time.monotonic() > deadline:
                    raise RuntimeError(f'No visible log row: {path.name}')
                yield
                index = explorer.model.index(str(path))
            tree.setCurrentIndex(index)
            point = tree.visualRect(index).center()
            event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, point,
                                      tree.viewport().mapToGlobal(point))
            QApplication.sendEvent(tree.viewport(), event)
            yield from self._wait(.1)
            menu = QApplication.activePopupWidget()
            assert isinstance(menu, QMenu), 'Log row did not open a context menu'
            return menu

        def click(menu, label):
            action = next(action for action in menu.actions() if action.text() == label)
            assert action.isEnabled(), label
            QTest.mouseClick(menu, Qt.MouseButton.LeftButton,
                             pos=menu.actionGeometry(action).center())

        def finished():
            deadline = time.monotonic() + 10
            while explorer._file_job is not None:
                if time.monotonic() > deadline:
                    raise RuntimeError('File operation timed out')
                yield
            assert not explorer._last_file_result.errors, explorer._last_file_result.errors
            return explorer._last_file_result.paths

        menu = yield from context(source)
        labels = [action.text() for action in menu.actions()]
        record['context_menu'] = True
        record['context_event_on_file_row'] = True
        record['menu_items'] = labels
        record['required_items'] = all(
            label in labels for label in ('打开所在文件夹', '复制路径', '复制', '压缩为 ZIP', '粘贴到此处'))
        menu.grab().save(str(self._result_path.with_suffix('.log-menu.png')))
        window.screen().grabWindow(int(window.winId())).save(str(self._result_path.with_suffix('.log-explorer.png')))
        click(menu, '复制路径')
        record['path_clipboard'] = QApplication.clipboard().text() == str(source)
        menu = yield from context(source)
        click(menu, '复制')
        menu = yield from context(destination)
        click(menu, '粘贴到此处')
        yield from finished()
        copied = destination / source.name
        record['copied'] = copied.read_bytes() == source.read_bytes()
        menu = yield from context(package)
        click(menu, '压缩为 ZIP')
        yield from finished()
        archive = root / 'case.zip'
        record['zipped'] = archive.is_file()
        record['zip_visible_without_filter_change'] = '*.zip' in explorer.model.nameFilters()
        zip_menu = yield from context(archive)
        zip_labels = [action.text() for action in zip_menu.actions()]
        record['extract_menu_item'] = '解压 ZIP' in zip_labels
        zip_menu.grab().save(str(self._result_path.with_suffix('.log-extract-menu.png')))
        click(zip_menu, '解压 ZIP')
        extracted_paths = yield from finished()
        assert len(extracted_paths) == 1 and extracted_paths[0] != package
        extracted = extracted_paths[0] / package.name
        record['extracted'] = (
            (extracted / 'Recorder.csv').read_bytes() == source.read_bytes()
            and (extracted / 'notes' / 'note.txt').read_text(encoding='utf-8') == 'keep me')
        record['extracted_destination'] = str(extracted_paths[0])
        record['source_untouched'] = (package / 'Recorder.csv').read_bytes() == source.read_bytes()
        record['actions_clicked'] = True
        QApplication.clipboard().clear()
        record['outcome'] = (
            'passed' if all((record['context_menu'], record['required_items'],
                             record.get('extract_menu_item'), record['copied'],
                             record['zipped'], record['extracted'], record['path_clipboard']))
            else 'failed')

    def _signal_inheritance(self, record: dict):
        """Exercise file replacement through actual explorer double-click events."""
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        window = self._window
        window.plot_panel.clear_all()
        window.signal_tree.select_signal_keys([])
        explorer = window.log_explorer
        record['switches'] = []
        plotted_key = None
        expected_styles = None
        for number, path in enumerate(self._inheritance_files):
            explorer.set_root(str(Path(path).parent))
            window.left_dock.show()
            index = explorer.model.index(str(Path(path)))
            deadline = time.monotonic() + LOAD_TIMEOUT
            while not index.isValid() or explorer.tree.visualRect(index).isEmpty():
                if time.monotonic() > deadline:
                    raise RuntimeError('Inheritance test row unavailable')
                yield
                index = explorer.model.index(str(Path(path)))
            explorer.tree.scrollTo(index)
            yield from self._wait(0.2)
            point = explorer.tree.visualRect(index).center()
            QTest.mouseClick(explorer.tree.viewport(), Qt.MouseButton.LeftButton, pos=point)
            QTest.mouseDClick(explorer.tree.viewport(), Qt.MouseButton.LeftButton, pos=point)
            while window._thread is not None or window._calc_thread is not None:
                if time.monotonic() > deadline:
                    raise RuntimeError('Inheritance load timed out')
                yield
            yield from self._wait(0.2)
            if window.store is None:
                raise RuntimeError('Inheritance load failed')
            if number == 0:
                plotted_key = next(k for k in window.store.all_keys() if window.store.get_series(k).signal_name == 'Speed')
                selected_key = next(k for k in window.store.all_keys() if window.store.get_series(k).signal_name == 'Voltage')
                window.add_signal_to_plot(plotted_key)
                window.signal_tree.select_signal_keys([plotted_key, selected_key])
                item = window.plot_panel._items[plotted_key]
                item.color = '#123456'
                item.group = 'Inherited'
                item.own_axis = True
                item.axis_visible = False
                item.line_style = 'dash'
                from gui.signal_inheritance import STYLE_FIELDS
                expected_styles = {f: getattr(item, f) for f in STYLE_FIELDS}
            item = window.plot_panel._items.get(plotted_key)
            selected = window.signal_tree.selected_signal_keys()
            switch = {'file': Path(path).name, 'plotted': window.plot_panel.plotted_keys(),
                      'selected_count': len(selected), 'signals': len(window.store.all_keys()),
                      'unavailable': item.unavailable_reason if item else '',
                      'samples': len(item.series.values) if item else 0,
                      'first_value': float(item.series.values[0]) if item and len(item.series.values) else None,
                      'styles_preserved': bool(item) and all(getattr(item, f) == v for f, v in expected_styles.items())}
            record['switches'].append(switch)
            if number == 1:
                window.signal_dock.show()
                yield from self._wait(0.2)
                screen = window.screen()
                screen.grabWindow(int(window.winId())).save(str(self._result_path.with_suffix('.inheritance.png')))
        switches = record['switches']
        assert len(switches) == 5
        assert switches[1]['unavailable'] == '本日志未匹配' and switches[1]['samples'] == 0
        assert switches[1]['selected_count'] == 2 and switches[1]['styles_preserved']
        assert not switches[2]['unavailable'] and switches[2]['first_value'] == 90
        assert switches[2]['selected_count'] == 2 and switches[2]['styles_preserved']
        assert not switches[3]['plotted'] and switches[3]['selected_count'] == 0
        assert not switches[4]['plotted'] and switches[4]['selected_count'] == 0
        record['outcome'] = 'passed'

    def _plot_detail(self, record: dict):
        from PySide6.QtCore import Qt, QPointF, QEvent
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtTest import QTest
        from gui.time_axis import TimeAxis
        import numpy as np
        window = self._window
        window.open_measurement_path(self._detail_file, auto_decode=True)
        deadline = time.monotonic() + LOAD_TIMEOUT
        while window._thread is not None:
            if time.monotonic() > deadline:
                raise RuntimeError('Detail recording load timed out')
            yield
        panel = window.plot_panel
        panel.clear_all()
        keys = window.store.all_keys()[:4]
        window.add_signals_to_plot(keys)
        if window.btn_points.isChecked():
            QTest.mouseClick(window.btn_points, Qt.MouseButton.LeftButton)
        assert not panel._show_points
        QTest.mouseClick(window.btn_points, Qt.MouseButton.LeftButton)
        assert panel._show_points
        record['layouts'] = []
        for mode in ('windows',):
            window.btn_windows.setChecked(True)
            QTest.mouseClick(window.btn_fit, Qt.MouseButton.LeftButton)
            yield from self._wait(.2)
            panel._update_adaptive_points()
            dense_label = '稀疏显示' in panel.sampling_label.text()
            QTest.mouseClick(window.btn_time_10ms, Qt.MouseButton.LeftButton)
            yield from self._wait(.3)
            panel._update_adaptive_points()
            low, high = panel._driving_vb().viewRange()[0]
            axis = (panel._stacked_plots[-1] if panel._stacked_mode else panel.plot.plotItem).getAxis('bottom')
            levels = axis.tickValues(low, high, panel._driving_vb().width())
            assert isinstance(axis, TimeAxis) and levels[1][0] == .01
            counts = []
            for key in keys:
                item = panel._items[key]
                original = np.asarray(window.store.get_series(key).timestamps)
                shown, values = item.scatter.getData()
                expected = (original >= low) & (original <= high)
                assert np.array_equal(shown, original[expected])
                assert np.array_equal(values, np.asarray(item.series.values)[expected])
                expected_interval = (float(original[10]) - float(original[9])) * 1000
                expected_tooltip = f'距上一原始采样点 Δt = {expected_interval:.3f} ms'
                assert expected_tooltip in panel._sample_tooltip(item, 10)
                counts.append(len(shown))
            assert dense_label and '全量' in panel.sampling_label.text()
            assert panel.sampling_label.isVisible() and panel.sampling_label.width() > 100
            record['layouts'].append({'mode': mode, 'minor_seconds': levels[1][0],
                                      'real_samples': True, 'visible_counts': counts, 'density_label': dense_label})
            if mode == 'windows':
                window.screen().grabWindow(int(window.winId())).save(str(self._result_path.with_suffix('.detail-stacked.png')))
                scatter = panel._items[keys[0]].scatter
                x, y = scatter.getData()
                scene_point = scatter.mapToScene(QPointF(float(x[5]), float(y[5])))
                window.raise_()
                window.activateWindow()
                hovered = []
                callback = lambda _scatter, points, _event: hovered.append(len(points))
                scatter.sigHovered.connect(callback)
                QTest.mouseMove(panel.glw.viewport(), panel.glw.mapFromScene(scene_point) - QPointF(20, 10).toPoint())
                QTest.mouseMove(panel.glw.viewport(), panel.glw.mapFromScene(scene_point))
                position = panel.glw.mapFromScene(scene_point)
                event = QMouseEvent(QEvent.Type.MouseMove, QPointF(position),
                                    QPointF(panel.glw.viewport().mapToGlobal(position)),
                                    Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
                QApplication.sendEvent(panel.glw.viewport(), event)
                yield from self._wait(.8)
                tip = scatter.getViewBox().toolTip()
                hover_interval = (float(x[5]) - float(x[4])) * 1000
                record['hover_tooltip'] = (
                    f'距上一原始采样点 Δt = {hover_interval:.3f} ms' in tip)
                record['hover_text'] = tip
                record['hover_events'] = hovered
                record['hover_debug'] = {'hoverable': scatter.opts['hoverable'],
                                         'points_at': len(scatter.pointsAt(QPointF(float(x[5]), float(y[5])))),
                                         'scene_point': [scene_point.x(), scene_point.y()],
                                         'viewport_point': [panel.glw.mapFromScene(scene_point).x(), panel.glw.mapFromScene(scene_point).y()],
                                         'mouse_tracking': panel.glw.viewport().hasMouseTracking()}
                scatter.sigHovered.disconnect(callback)
        if not record.get('hover_tooltip'):
            self._result_path.with_suffix('.hover-debug.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
        assert record.get('hover_tooltip'), 'No sampling interval tooltip on actual hover'
        record['outcome'] = 'passed'

    def _window_templates(self, record: dict):
        from copy import deepcopy
        from PySide6.QtCore import Qt, QPointF, QEvent
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QComboBox, QToolButton
        from core.project_config import save_project, load_project
        from gui import analysis_templates, plot_windows
        window = self._window
        window.open_measurement_path(self._detail_file, auto_decode=True)
        deadline = time.monotonic() + LOAD_TIMEOUT
        while window._thread is not None:
            if time.monotonic() > deadline:
                raise RuntimeError('Window recording load timed out')
            yield
        window.plot_panel.clear_all()
        window.btn_windows.setChecked(True)
        panel = window.plot_panel
        plot_windows.apply_layout(panel, [dict(
            id=0, name='窗口 1', axis_mode='shared', height=220,
            keys=[], y_ranges={})])
        keys = window.store.all_keys()[:4]
        window.add_signals_to_plot(keys[:2])
        QTest.mouseClick(window.btn_new_window, Qt.MouseButton.LeftButton)
        window.add_signal_to_plot(keys[2])
        QTest.mouseClick(window.btn_new_window, Qt.MouseButton.LeftButton)
        window.add_signal_to_plot(keys[3])
        yield from self._wait(.3)
        assert len(panel._window_widgets) == 3
        assert [list(widget[1].rows) for widget in panel._window_widgets.values()] == [keys[:2], [keys[2]], [keys[3]]]
        combo = panel._window_widgets[0][0].findChild(QComboBox)
        combo.setCurrentIndex(1)
        yield from self._wait(.2)
        assert panel._items[keys[0]].view_box is not panel._items[keys[1]].view_box
        assert panel._window_specs[1]['axis_mode'] == 'shared'
        window.btn_cursor1.setChecked(True)
        window.btn_cursor2.setChecked(True)
        yield from self._wait(.2)  # cursor creation queues a range restore
        panel._stacked_plots[1].setXRange(10, 15, padding=0)
        yield from self._wait(.2)
        for plot in panel._stacked_plots:
            assert abs(plot.vb.viewRange()[0][0] - 10) < .05, [p.vb.viewRange()[0] for p in panel._stacked_plots]
            assert abs(plot.vb.viewRange()[0][1] - 15) < .05, [p.vb.viewRange()[0] for p in panel._stacked_plots]
        panel._stacked_c1_lines[1].setPos(12)
        panel._stacked_c2_lines[0].setPos(13)
        assert all(abs(line.value()-12) < 1e-8 for line in panel._stacked_c1_lines)
        assert all(abs(line.value()-13) < 1e-8 for line in panel._stacked_c2_lines)
        assert all(tree.rows[key].text(1) for _widget, tree, _title in panel._window_widgets.values() for key in tree.rows)
        scrollbar = panel._stacked_scroll.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        yield from self._wait(.2)
        assert panel.window_ruler.isVisible()
        scrollbar.setValue(0)
        window.resize(1600, 1050)
        window.bottom_dock.hide()
        yield from self._wait(.3)
        grip = panel._window_grips[0]
        old_height = panel._window_heights[0]
        before_heights = list(panel._window_heights)
        local = QPointF(grip.rect().center())
        global_pos = QPointF(grip.mapToGlobal(grip.rect().center())) + QPointF(0, -35)
        QTest.mousePress(grip, Qt.MouseButton.LeftButton, pos=local.toPoint())
        event = QMouseEvent(QEvent.Type.MouseMove, local, global_pos,
                            Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(grip, event)
        QTest.mouseRelease(grip, Qt.MouseButton.LeftButton)
        assert panel._window_heights[0] < old_height, {
            'before': before_heights, 'after': panel._window_heights,
            'visible': grip.isVisible(), 'geometry': grip.geometry().getRect(),
            'viewport': panel._stacked_scroll.viewport().size().toTuple(),
            'drag_start': getattr(grip, 'heights', None),
        }
        third = panel._window_widgets[2][0]
        up = next(button for button in third.findChildren(QToolButton) if button.text() == '↑')
        QTest.mouseClick(up, Qt.MouseButton.LeftButton)
        yield from self._wait(.3)
        assert panel._window_specs[1]['id'] == 2
        profile = self._result_path.with_suffix('.project.json')
        other_profile = self._result_path.with_suffix('.other-project.json')
        save_project(profile, {'name': 'Native analysis project'}, {'channels': {}}, {})
        save_project(other_profile, {'name': 'Other project'}, {'channels': {}}, {})
        window.project = {'name': 'Native analysis project'}
        analysis_templates.bind_profile(window, profile)
        self._template_answers.append('电机排查')
        window.template_menu.populate()
        next(action for action in window.template_menu.menu_widget.actions() if action.text() == '保存当前分析为模板…').trigger()
        saved = load_project(profile)['project']['analysis_templates']['电机排查']
        assert 'timestamps' not in json.dumps(saved)
        expected = deepcopy(saved['presentation']['window_layout'])
        panel._stacked_plots[0].setXRange(11, 14, padding=0)
        panel.move_cursor1(12)
        panel.move_cursor2(13)
        store, recording = window.store, window.measurement_path
        panel.clear_all()
        window.add_signal_to_plot(keys[-1])
        yield from self._wait(.2)
        panel._driving_vb().setXRange(11, 14, padding=0)
        panel.move_cursor1(12)
        panel.move_cursor2(13)
        window.template_menu.populate()
        next(action for action in window.template_menu.menu_widget.actions() if action.text() == '电机排查').trigger()
        yield from self._wait(.3)
        assert window.store is store and window.measurement_path == recording
        assert panel.plotted_keys() == keys
        assert abs(panel._driving_vb().viewRange()[0][0]-11) < .05, panel._driving_vb().viewRange()[0]
        assert abs(panel._driving_vb().viewRange()[0][1]-14) < .05, panel._driving_vb().viewRange()[0]
        assert panel.v_line.value() == 12 and panel.v_line2.value() == 13
        assert [(spec['id'], spec['axis_mode'], spec['height']) for spec in panel._window_specs] == [(spec['id'], spec['axis_mode'], spec['height']) for spec in expected]
        window.screen().grabWindow(int(window.winId())).save(str(self._result_path.with_suffix('.windows.png')))
        self._template_answers.extend(['电机排查', '重命名测试'])
        window.template_menu.rename()
        assert list(load_project(profile)['project']['analysis_templates']) == ['重命名测试']
        self._template_answers.append('重命名测试')
        window.template_menu.delete()
        assert not load_project(profile)['project']['analysis_templates']
        window.template_menu.persist({'电机排查': saved})
        self._pending_paths.append(str(other_profile))
        window.open_project()
        assert not window.template_menu.library()
        assert window.store is None and panel.plotted_keys() == []
        assert '电机排查' in load_project(profile)['project']['analysis_templates']
        record.update(outcome='passed', local_signal_lists=True, whole_window_axis_modes=True,
                      common_time_ruler=True, synchronized_zoom=True, synchronized_cursors=True,
                      adjustable_height=True, reorder_windows=True, template_save_apply=True,
                      keep_time_and_cursors=True, template_rename_delete=True, project_isolation=True)

    def _compact_layout(self, record: dict):
        """Check native geometry and actual divider mouse-event handlers."""
        from PySide6.QtCore import Qt, QPointF, QEvent
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtTest import QTest
        from gui import plot_windows
        window = self._window
        window.open_measurement_path(self._detail_file, auto_decode=True)
        deadline = time.monotonic()+LOAD_TIMEOUT
        while window._thread is not None:
            if time.monotonic() > deadline:
                raise RuntimeError('Compact layout recording load timed out')
            yield
        panel = window.plot_panel
        panel.clear_all()
        window.btn_windows.setChecked(True)
        window.left_dock.show()
        window.bottom_dock.show()
        window.signal_dock.hide()
        window.resize(1600, 1050)
        keys = window.store.all_keys()[:4]
        window.add_signals_to_plot(keys[:2])
        panel.new_window()
        window.add_signal_to_plot(keys[2])
        panel.new_window()
        window.add_signal_to_plot(keys[3])
        for spec, height in zip(panel._window_specs, (160, 320, 160)):
            spec['height'] = height
        plot_windows.arrange(panel)
        yield from self._wait(.3)

        def tiled():
            from PySide6.QtCore import QPoint
            rectangles = [plot.geometry() for plot in panel._stacked_plots]
            assert abs(rectangles[0].top()) < 1
            assert all(abs(a.bottom()-b.top()) < .01 for a, b in zip(rectangles, rectangles[1:]))
            assert abs(rectangles[-1].bottom()+plot_windows.RULER_HEIGHT-panel.glw.viewport().height()) <= 1
            footer = panel._cursor_line.mapToGlobal(QPoint())
            assert panel.window_ruler.mapToGlobal(QPoint(0, panel.window_ruler.height())).y() == footer.y()
            tree = panel._window_widgets[panel._window_specs[-1]['id']][1]
            if panel._stacked_scroll.verticalScrollBar().value() == panel._stacked_scroll.verticalScrollBar().maximum():
                assert abs(tree.mapToGlobal(QPoint(0, tree.height())).y()-footer.y()) <= 1
            assert abs(panel.window_ruler.mapToGlobal(QPoint()).x()-tree.mapToGlobal(QPoint(tree.width(), 0)).x()) <= 1
            assert panel.window_ruler.isVisible()

        def drag(widget, dx=0, dy=0):
            local = QPointF(widget.rect().center())
            global_pos = QPointF(widget.mapToGlobal(widget.rect().center()))
            QTest.mousePress(widget, Qt.MouseButton.LeftButton)
            QApplication.sendEvent(widget, QMouseEvent(QEvent.Type.MouseMove, local,
                global_pos+QPointF(dx, dy), Qt.MouseButton.NoButton,
                Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
            QTest.mouseRelease(widget, Qt.MouseButton.LeftButton)

        tiled()
        assert panel._stacked_scroll.verticalScrollBar().maximum() == 0
        panel._driving_vb().setXRange(11, 14, padding=0)
        panel.move_cursor1(12)
        panel.move_cursor2(13)
        series = {key: panel._items[key].series for key in keys}
        before = list(panel._window_heights)
        drag(panel._window_grips[0], dy=25)
        yield from self._wait(.2)
        assert panel._window_heights == [before[0]+25, before[1]-25, before[2]]
        tiled()
        # Resize, collapse and reopen both dock boundaries using their lines.
        for name, delta in (('left', 35), ('bottom', -30)):
            tab, dock = getattr(window, name+'_edge_btn'), getattr(window, name+'_dock')
            before_extent = dock.width() if name == 'left' else dock.height()
            drag(tab, dx=delta if name == 'left' else 0, dy=delta if name == 'bottom' else 0)
            yield from self._wait(.2)
            after_extent = dock.width() if name == 'left' else dock.height()
            assert abs(after_extent-before_extent-abs(delta)) <= 2
            tiled()
            QTest.mouseClick(tab, Qt.MouseButton.LeftButton)
            assert dock.isVisible()
            QTest.mouseDClick(tab, Qt.MouseButton.LeftButton)
            yield from self._wait(.2)
            assert not dock.isVisible() and tab.isVisible()
            tiled()
            QTest.mouseDClick(tab, Qt.MouseButton.LeftButton)
            yield from self._wait(.2)
            assert dock.isVisible()
            tiled()
        central = window.centralWidget().geometry()
        assert central.left()-window.left_dock.geometry().right()-1 <= 1
        assert window.bottom_dock.geometry().top()-central.bottom()-1 <= 1
        window.resize(1400, 900)
        yield from self._wait(.3)
        tiled()
        for _ in range(7):
            panel.new_window()
        yield from self._wait(.3)
        bar = panel._stacked_scroll.verticalScrollBar()
        assert bar.maximum() > 0 and min(panel._window_heights) >= plot_windows.MIN_WINDOW_HEIGHT
        bar.setValue(bar.maximum())
        yield from self._wait(.2)
        point = panel.glw.mapFromScene(panel._stacked_plots[-1].geometry().bottomLeft())
        point = panel._stacked_scroll.viewport().mapFromGlobal(panel.glw.viewport().mapToGlobal(point))
        assert abs(point.y()+plot_windows.RULER_HEIGHT-panel._stacked_scroll.viewport().height()) <= 1
        tiled()
        for spec in list(panel._window_specs[3:]):
            plot_windows.close_window(panel, spec)
        window.resize(1600, 1050)
        yield from self._wait(.3)
        tiled()
        assert panel._stacked_scroll.verticalScrollBar().maximum() == 0
        assert all(panel._items[key].series is original for key, original in series.items())
        assert abs(panel._driving_vb().viewRange()[0][0]-11) < .05
        assert abs(panel._driving_vb().viewRange()[0][1]-14) < .05
        assert panel.v_line.value() == 12 and panel.v_line2.value() == 13
        screenshot = str(self._result_path.with_suffix('.compact.png'))
        window.screen().grabWindow(int(window.winId())).save(screenshot)
        # Explicitly cover the two-window layout from the reported screenshot,
        # then one window. Check pixels in the former ruler strip as well as
        # widget geometry, so a white filler cannot masquerade as a real list.
        from PySide6.QtCore import QPoint
        from PySide6.QtGui import QPalette
        footer_screenshot = str(self._result_path.with_suffix('.signal-footer.png'))
        for count in (2, 1):
            plot_windows.close_window(panel, panel._window_specs[-1])
            yield from self._wait(.2)
            assert len(panel._window_specs) == count
            tiled()
            tree = panel._window_widgets[panel._window_specs[-1]['id']][1]
            footer_y = panel._cursor_line.mapToGlobal(QPoint()).y()
            target = window.mapFromGlobal(QPoint(tree.mapToGlobal(QPoint()).x()+15, footer_y-25))
            shot = window.grab().toImage()
            ratio = shot.devicePixelRatio()
            assert shot.pixelColor(round(target.x()*ratio), round(target.y()*ratio)) == tree.palette().color(QPalette.ColorRole.Base)
            if count == 2:
                window.screen().grabWindow(int(window.winId())).save(footer_screenshot)
        record.update(outcome='passed', proportional_fill=True, zero_gaps=True,
                      signal_list_to_footer=True, ruler_right_only=True,
                      footer_window_counts=[1, 2, 3], footer_pixels_verified=True,
                      footer_screenshot=footer_screenshot,
                      last_window_at_ruler=True, adjacent_resize=True, readable_overflow=True,
                      dock_dividers_resize=True, double_click_collapse=True,
                      collapsed_edge_reopen=True, viewport_preserved=True, screenshot=screenshot)

    def _plot_controls(self, record: dict):
        """Exercise real Qt menus and both ruler surfaces in the frozen app."""
        from PySide6.QtCore import Qt, QPointF, QEvent
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtTest import QTest
        from core.plot_style import LINE_WIDTHS
        window, panel = self._window, self._window.plot_panel
        assert panel._items, 'Load and plot a recording before plot_controls'
        window.resize(1700, 950)
        yield from self._wait(.2)
        buffers = {key: (item.curve.xData, item.curve.yData) for key, item in panel._items.items()}
        for width in LINE_WIDTHS:
            menu = window.btn_line_width.menu()
            action = next(action for action in menu.actions() if action.data() == width)
            menu.popup(window.btn_line_width.mapToGlobal(window.btn_line_width.rect().bottomLeft()))
            yield from self._wait(.1)
            QTest.mouseClick(menu, Qt.MouseButton.LeftButton, pos=menu.actionGeometry(action).center())
            yield from self._wait(.1)
            assert panel.line_width() == width
            assert all(item.curve.opts['pen'].widthF() == width for item in panel._items.values())
            assert all(item.curve.xData is buffers[key][0] and item.curve.yData is buffers[key][1]
                       for key, item in panel._items.items())
        assert window._configuration_dict()['plot_line_width'] == 4
        record['line_widths'] = list(LINE_WIDTHS)
        window._set_line_width(.5)
        record['ruler_layouts'] = []
        for mode in ('windows',):
            window.btn_windows.setChecked(True)
            panel.set_cursor1_enabled(True)
            panel.set_cursor2_enabled(True)
            panel.move_cursor1(12)
            panel.move_cursor2(18)
            panel.set_rect_zoom(True)
            yield from self._wait(.3)
            view = panel._driving_vb()
            view.setXRange(10, 20, padding=0)
            yield from self._wait(.1)
            views = {plot.vb for plot in panel._stacked_plots}
            views.update(vb for _, vb in panel._extra_axes)
            before_y = {other: list(other.viewRange()[1]) for other in views}
            width = view.sceneBoundingRect().width()
            target = panel.window_ruler
            start = QPointF(target.width()/2, 18)
            def send(kind, point, button, buttons):
                global_point = QPointF(target.mapToGlobal(point.toPoint()))
                event = QMouseEvent(kind, point, global_point, button, buttons, Qt.KeyboardModifier.NoModifier)
                QApplication.sendEvent(target, event)
                QApplication.processEvents()
            window.playback.follow.setChecked(True)
            send(QEvent.Type.MouseButtonPress, start, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
            send(QEvent.Type.MouseMove, start+QPointF(25, 3), Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton)
            send(QEvent.Type.MouseMove, start+QPointF(80, 8), Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton)
            send(QEvent.Type.MouseButtonRelease, start+QPointF(80, 8), Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)
            yield from self._wait(.2)
            expected = [10-80*10/width, 20-80*10/width]
            for other in views:
                assert max(abs(a-b) for a, b in zip(other.viewRange()[0], expected)) < .12
                assert max(abs(a-b) for a, b in zip(other.viewRange()[1], before_y[other])) < 1e-6
            assert panel.v_line.value() == 12 and panel.v_line2.value() == 18
            assert not window.playback.follow.isChecked()
            record['ruler_layouts'].append(dict(mode=mode, synchronized=True, y_unchanged=True,
                                               cursors_unchanged=True, follow_released=True))
        window.btn_windows.setChecked(True)
        panel.set_rect_zoom(False)
        panel.fit_to_window()
        yield from self._wait(.2)
        screenshots = {}
        for width in (.5, 4):
            window._set_line_width(width)
            yield from self._wait(.2)
            path = str(self._result_path.with_suffix(f'.width-{width:g}.png'))
            assert window.screen().grabWindow(int(window.winId())).save(path)
            screenshots[str(width)] = path
        window._set_line_width(None)
        yield from self._window_signal_drag(record)
        record.update(outcome='passed', line_width_menu=True, exact_selected_width=True,
                      original_buffers=True, ruler_left_drag=True, screenshots=screenshots)

    def _window_signal_drag(self, record):
        from PySide6.QtCore import Qt, QPointF, QMimeData
        from PySide6.QtGui import QDragEnterEvent, QDropEvent
        from gui.signal_tree import SignalTreeWidget
        from gui import plot_windows
        panel = self._window.plot_panel
        keys = panel.plotted_keys()[:2]
        target = panel._window_specs[-1]['id']
        if any(panel._items[key].window_id == target for key in keys):
            panel.new_window()
            target = panel._window_specs[-1]['id']
        yield from self._wait(.2)
        record['window_signal_drag'] = []
        for surface in ('plot', 'list', 'tree'):
            original = {key: panel._items[key] for key in keys}
            owners = {key: item.window_id for key, item in original.items()}
            panel._driving_vb().setXRange(10, 20, padding=0)
            yield from self._wait(.1)
            panel.discard_undo_history()
            mime = QMimeData()
            mime.setData(SignalTreeWidget.MIME_TYPE, '\n'.join(keys).encode('utf-8'))
            if surface == 'tree':
                widget = panel._window_widgets[target][1].viewport()
                position = QPointF(widget.rect().center())
            else:
                widget = panel.glw.viewport()
                if surface == 'list':
                    rect = panel._window_widgets[target][0].graphicsProxyWidget().sceneBoundingRect()
                else:
                    row = next(index for index, window in enumerate(panel._window_specs) if window['id'] == target)
                    rect = panel._stacked_plots[row].vb.sceneBoundingRect()
                position = QPointF(panel.glw.mapFromScene(rect.center()))
            enter = QDragEnterEvent(position.toPoint(), Qt.DropAction.MoveAction, mime,
                                   Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
            QApplication.sendEvent(widget, enter)
            event = QDropEvent(position, Qt.DropAction.MoveAction, mime,
                               Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
            QApplication.sendEvent(widget, event)
            assert event.isAccepted()
            # A later selection cannot redirect the queued drop to its source.
            plot_windows.activate(panel, owners[keys[0]])
            yield from self._wait(.2)
            assert all(panel._items[key] is original[key] and original[key].window_id == target for key in keys)
            assert len(panel._undo_stack) == 1
            assert max(abs(a-b) for a, b in zip(panel._driving_vb().viewRange()[0], (10, 20))) < .05
            panel.undo()
            yield from self._wait(.2)
            assert all(panel._items[key].window_id == owners[key] for key in keys)
            record['window_signal_drag'].append(dict(surface=surface, moved_together=True,
                                                     data_preserved=True, target_captured=True, undo=True))

    def _close_during_load(self, record: dict):
        window = self._window
        record['file'] = self._current_file['alias'] if self._current_file else None
        started = time.monotonic()
        self._actions['Decode'].trigger()
        # Timed from the thread's start, not the click: a file the system
        # has cached loads in under two seconds.
        while window._thread is not None and not window._thread.isRunning():
            yield
        yield from self._wait(CLOSE_AFTER_SECONDS)
        record['load_running_at_close'] = window._thread is not None
        record['closed_at'] = time.time()
        self._finish_step(record, started)
        self._result['finished'] = True
        self._save()
        self._stop()
        # Nothing answers a dialog from here on: one would hold the
        # application open until the runner gives up on it.
        window.close()

    def _save(self) -> None:
        temporary = self._result_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self._result, indent=1), encoding='utf-8')
        temporary.replace(self._result_path)


class _MemoryCounters(ctypes.Structure):
    _fields_ = [
        ('cb', ctypes.c_ulong), ('PageFaultCount', ctypes.c_ulong),
        ('PeakWorkingSetSize', ctypes.c_size_t), ('WorkingSetSize', ctypes.c_size_t),
        ('QuotaPeakPagedPoolUsage', ctypes.c_size_t), ('QuotaPagedPoolUsage', ctypes.c_size_t),
        ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t), ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
        ('PagefileUsage', ctypes.c_size_t), ('PeakPagefileUsage', ctypes.c_size_t),
        ('PrivateUsage', ctypes.c_size_t),
    ]


def _memory() -> dict:
    """This process's memory now and at its peak, in MB."""
    if sys.platform != 'win32':
        return {}
    counters = _MemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    kernel32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
    if not psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        return {}
    return {
        'memory_mb': round(counters.PrivateUsage / 1e6),
        'peak_mb': round(counters.PeakPagefileUsage / 1e6),
    }


class _Collections:
    """The garbage collections run while it is entered, on any thread: each
    as [generation, milliseconds]."""

    def __enter__(self) -> _Collections:
        self.found: list[list] = []
        self._started = 0.0
        gc.callbacks.append(self._note)
        return self

    def __exit__(self, *_exc) -> None:
        gc.callbacks.remove(self._note)

    def _note(self, phase: str, info: dict) -> None:
        if phase == 'start':
            self._started = time.perf_counter()
        else:
            self.found.append(
                [info['generation'], round((time.perf_counter() - self._started) * 1000, 1)])


def drive(window, spec_path: Path) -> ScenarioDriver:
    spec = json.loads(spec_path.read_text(encoding='utf-8'))
    return ScenarioDriver(window, spec)
