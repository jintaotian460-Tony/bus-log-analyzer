# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

from __future__ import annotations

import sys
from pathlib import Path

APP_NAME    = "Osvanta Bus Log Analyzer"
APP_VERSION = "v00.02.02+tony.1"


def main(*, notify_startup_errors: bool = True) -> int:
    from startup_support import STARTUP_FAILED, report_failure, windows_startup_check, write_report
    checking = '--check-startup' in sys.argv
    automated = ('--release-test-scenario' in sys.argv or
                 ('--release-test' in sys.argv and '--quit-when-done' in sys.argv))
    try:
        if checking:
            import json
            result = windows_startup_check()
            result.update(app=APP_NAME, version=APP_VERSION, check_only=True)
            path = write_report(result)
            result['report_path'] = str(path) if path else None
            if sys.stdout is not None:
                print(json.dumps(result, ensure_ascii=False), flush=True)
            return 0 if result['available'] else STARTUP_FAILED
        return _start_application()
    except Exception as error:
        report_failure(error, APP_NAME, APP_VERSION,
                       notify=notify_startup_errors and not checking and not automated)
        return STARTUP_FAILED


def _start_application() -> int:
    from startup_support import require_native_window
    # Hidden: "--release-test <folder>" tests this build instead of starting
    # it, and restarts it once per scenario. See gui/release_test.
    from gui import release_test
    if release_test.requested(sys.argv):
        return release_test.run(sys.argv, APP_NAME, APP_VERSION)

    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)

    # First, so every later failure is recorded: the packaged application has
    # no console, and a Qt fatal error otherwise ends it without a trace.
    from gui.crash_log import CrashLog
    crash_log = CrashLog(app)  # noqa: F841 — alive until exit

    # The application opens no console: what one would have shown, and every
    # line of the Log panel, goes to a log file instead. After the crash log,
    # which takes over the C runtime's stderr only while sys.stderr is missing.
    from gui.app_log import AppLog
    app_log = AppLog(APP_NAME, APP_VERSION)  # noqa: F841 — alive until exit

    # Before any worker thread exists: an automatic collection on a worker
    # thread destroys discarded plot items there and crashes Qt.
    from gui.gc_guard import GuiThreadGarbageCollector
    gc_guard = GuiThreadGarbageCollector(app)  # noqa: F841 — alive until exit

    # Resolve resource path for both dev and frozen EXE
    _res_root = (
        Path(sys._MEIPASS) if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")
        else Path(__file__).resolve().parent
    )
    # Prefer PNG (full RGBA, sharp on HiDPI); fall back to ICO
    for _icon_name in ('app_icon.png', 'app_icon.ico'):
        _icon_path = _res_root / 'resources' / _icon_name
        if _icon_path.exists():
            app.setWindowIcon(QIcon(str(_icon_path)))
            break

    # ── Show splash immediately — before any heavy imports ────────────────
    # gui.splash only imports PySide6 (already loaded) + pathlib.
    # All heavy modules (cantools, python-can, asammdf, pyqtgraph) are
    # imported lazily when MainWindow / PlotPanel are first constructed.
    from gui.splash import SplashScreen
    splash = SplashScreen(version=APP_VERSION)
    splash.show()
    require_native_window(splash, app.platformName(), 'startup screen')
    app.processEvents()

    splash.set_status('Loading UI components...')
    from gui.main_window import MainWindow   # ← heavy imports happen here

    splash.set_status('Building main window...')
    window = MainWindow(app_name=APP_NAME, version=APP_VERSION, splash=splash)

    window.show()
    require_native_window(window, app.platformName(), 'main')
    app_log.write(f'{APP_NAME} {APP_VERSION} started: main window ready.\n')
    splash.finish(window)
    driver = release_test.drive_if_requested(window, sys.argv)  # noqa: F841 — alive until exit
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
