# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""A failed native start must terminate and leave usable diagnostics.

These checks also run with ``python -m unittest discover -s tests -p
test_startup_failure.py`` on a machine whose Qt windows cannot be created.
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import startup_support as startup

ROOT = Path(__file__).resolve().parents[1]


class StartupFailureTests(unittest.TestCase):
    def test_a_logically_visible_window_without_a_native_handle_fails(self):
        window = SimpleNamespace(isVisible=lambda: True, effectiveWinId=lambda: 0)
        with self.assertRaises(startup.StartupFailure) as caught:
            startup.require_native_window(window, 'windows', 'main')
        self.assertEqual(caught.exception.stage, 'main')

    def test_an_existing_native_handle_is_accepted(self):
        startup.require_native_window(SimpleNamespace(effectiveWinId=lambda: 1234), 'windows', 'main')

    def test_offscreen_tests_are_not_claimed_to_be_native_windows(self):
        startup.require_native_window(SimpleNamespace(), 'offscreen', 'main')

    def test_a_report_survives_failure_of_the_error_dialog(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'failure.json'
            with (patch.dict(os.environ, OSVANTA_STARTUP_LOG=str(target)),
                  patch.object(startup, 'windows_startup_check', return_value={
                      'available': False, 'winerror': 8, 'message': 'resource shortage',
                      'user_atoms': {'used': 16270, 'capacity': 16384}}),
                  patch.object(startup, '_native_error_message', side_effect=OSError('no dialog')),
                  patch.object(sys, 'stderr', io.StringIO())):
                result = startup.report_failure(startup.StartupFailure('main', 'no native window'),
                                                'Osvanta', 'test')
            self.assertEqual(result, target)
            report = json.loads(target.read_text(encoding='utf-8'))
            self.assertEqual(report['stage'], 'main')
            self.assertEqual(report['windows']['winerror'], 8)

    def test_an_unwritable_report_folder_uses_the_temp_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            not_a_folder = Path(folder) / 'file'
            not_a_folder.write_text('keep', encoding='utf-8')
            with (patch.dict(os.environ, OSVANTA_STARTUP_LOG=str(not_a_folder / 'report.json')),
                  patch.object(startup.tempfile, 'gettempdir', return_value=folder)):
                target = startup.write_report({'error': 'window creation failed'})
            self.assertIsNotNone(target)
            self.assertNotEqual(target, not_a_folder / 'report.json')
            self.assertEqual(json.loads(target.read_text(encoding='utf-8'))['error'], 'window creation failed')
            self.assertEqual(not_a_folder.read_text(encoding='utf-8'), 'keep')

    def test_the_entry_point_returns_failure_without_entering_the_event_loop(self):
        import app
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'failure.json'
            with (patch.dict(os.environ, OSVANTA_STARTUP_LOG=str(target)),
                  patch.object(sys, 'argv', ['app.py']),
                  patch.object(app, '_start_application', side_effect=startup.StartupFailure('startup screen', 'no window')),
                  patch.object(startup, 'windows_startup_check', return_value={'available': False, 'winerror': 8}),
                  patch.object(sys, 'stderr', io.StringIO())):
                code = app.main(notify_startup_errors=False)
            self.assertEqual(code, startup.STARTUP_FAILED)
            self.assertEqual(json.loads(target.read_text(encoding='utf-8'))['stage'], 'startup screen')

    def test_check_mode_does_not_import_qt_or_start_a_window(self):
        script = '''
import importlib.abc, sys
class NoQt(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname.startswith(('PySide6', 'pyqtgraph', 'asammdf')):
            raise AssertionError('check mode imported ' + fullname)
sys.meta_path.insert(0, NoQt())
import app
sys.argv = ['app.py', '--check-startup']
raise SystemExit(app.main())
'''
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'check.json'
            result = subprocess.run([sys.executable, '-X', 'utf8', '-c', script], cwd=ROOT,
                                    env=dict(os.environ, OSVANTA_STARTUP_LOG=str(target)),
                                    capture_output=True, text=True, encoding='utf-8', timeout=15)
            self.assertIn(result.returncode, (0, startup.STARTUP_FAILED), result.stderr)
            report = json.loads(result.stdout)
            self.assertTrue(report['check_only'])
            self.assertEqual(result.returncode == 0, report['available'])
            self.assertEqual(json.loads(target.read_text(encoding='utf-8'))['checked'], report['checked'])

    def test_a_missing_qt_runtime_does_not_disappear_without_a_report(self):
        script = '''
import sys
sys.modules['PySide6'] = None
import app
raise SystemExit(app.main(notify_startup_errors=False))
'''
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'missing-qt.json'
            result = subprocess.run([sys.executable, '-X', 'utf8', '-c', script], cwd=ROOT,
                                    env=dict(os.environ, OSVANTA_STARTUP_LOG=str(target)),
                                    capture_output=True, text=True, encoding='utf-8', timeout=15)
            self.assertEqual(result.returncode, startup.STARTUP_FAILED, result.stderr)
            report = json.loads(target.read_text(encoding='utf-8'))
            self.assertEqual(report['stage'], 'initialisation')
            self.assertIn('PySide6', report['error'])

    def _assert_injected_window_failure(self, stage):
        script = '''
import sys
from PySide6.QtWidgets import QApplication
import startup_support
wanted_stage = sys.argv[1]
folder = sys.argv[2]
sys.argv = ['app.py', '--release-test', folder, '--quit-when-done'] if wanted_stage == 'release test runner' else ['app.py']
notified = []
def notice(*args):
    notified.append(True)
    return False
startup_support._native_error_message = notice
def no_event_loop(self):
    raise AssertionError('event loop entered after a failed window')
QApplication.exec = no_event_loop
def reject_window(widget, platform, stage):
    if stage == wanted_stage:
        raise startup_support.StartupFailure(stage, 'injected native window failure')
startup_support.require_native_window = reject_window
import app
code = app.main(notify_startup_errors=(wanted_stage == 'release test runner'))
assert not notified, 'an automated failure displayed a blocking dialog'
if wanted_stage == 'startup screen':
    assert 'gui.main_window' not in sys.modules, 'heavy UI loaded after the startup window failed'
raise SystemExit(code)
'''
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'native-failure.json'
            app_log = Path(folder) / 'app.log'
            result = subprocess.run([sys.executable, '-X', 'utf8', '-c', script, stage, folder], cwd=ROOT,
                                    env=dict(os.environ, QT_QPA_PLATFORM='offscreen',
                                             OSVANTA_STARTUP_LOG=str(target), OSVANTA_APP_LOG=str(app_log),
                                             OSVANTA_CRASH_LOG=str(Path(folder) / 'crash.log'),
                                             OSVANTA_SESSION_RESTORE='0', OSVANTA_SURVEY_PROMPT='0'),
                                    capture_output=True, text=True, encoding='utf-8', timeout=20)
            self.assertEqual(result.returncode, startup.STARTUP_FAILED, result.stderr)
            self.assertEqual(json.loads(target.read_text(encoding='utf-8'))['stage'], stage)
            self.assertNotIn('started: main window ready.', app_log.read_text(encoding='utf-8') if app_log.exists() else '')

    def test_a_failed_startup_screen_stops_before_loading_the_main_window(self):
        self._assert_injected_window_failure('startup screen')

    def test_a_failed_main_window_never_logs_ready_or_enters_the_event_loop(self):
        self._assert_injected_window_failure('main')

    def test_a_failed_release_test_window_stops_before_starting_scenarios(self):
        self._assert_injected_window_failure('release test runner')

    def test_an_automated_scenario_failure_does_not_open_a_blocking_dialog(self):
        import app
        with tempfile.TemporaryDirectory() as folder:
            with (patch.dict(os.environ, OSVANTA_STARTUP_LOG=str(Path(folder) / 'failure.json')),
                  patch.object(sys, 'argv', ['app.py', '--release-test-scenario', 'unused.json']),
                  patch.object(app, '_start_application', side_effect=startup.StartupFailure('main', 'no window')),
                  patch.object(startup, 'windows_startup_check', return_value={'available': False}),
                  patch.object(startup, '_native_error_message') as notice,
                  patch.object(sys, 'stderr', io.StringIO())):
                self.assertEqual(app.main(), startup.STARTUP_FAILED)
            notice.assert_not_called()


if __name__ == '__main__':
    unittest.main()
