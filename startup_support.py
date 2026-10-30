# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Report failed starts without depending on a functioning Qt window.

This module intentionally imports no Qt or measurement libraries. A window
being logically visible is not proof that Windows allocated a native window.
"""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback

STARTUP_FAILED = 2


class StartupFailure(RuntimeError):
    def __init__(self, stage: str, message: str) -> None:
        super().__init__(message)
        self.stage = stage


def require_native_window(widget, platform_name: str, stage: str) -> None:
    """Check the real native ID after show(), without creating another window.

    QWidget.winId() would attempt creation again; effectiveWinId() only reads
    the current ID. Offscreen tests deliberately do not have Windows handles.
    """
    if platform_name.lower() == 'windows' and not widget.effectiveWinId():
        raise StartupFailure(stage, f'Windows could not create the {stage} window.')


def windows_startup_check() -> dict:
    """A bounded, reversible class-registration probe, plus read-only counts.

    Creates no windows, reads no clipboard contents and never deletes atoms
    belonging to another application. The temporary class is unregistered.
    """
    result = {'platform': sys.platform, 'available': True, 'checked': False}
    if sys.platform != 'win32':
        return result
    import ctypes as c
    from ctypes import wintypes as w

    kernel = c.WinDLL('kernel32', use_last_error=True)
    user = c.WinDLL('user32', use_last_error=True)
    kernel.GetModuleHandleW.argtypes = [w.LPCWSTR]
    kernel.GetModuleHandleW.restype = w.HMODULE

    class WindowClass(c.Structure):
        _fields_ = [('cbSize', w.UINT), ('style', w.UINT), ('lpfnWndProc', c.c_void_p),
                    ('cbClsExtra', c.c_int), ('cbWndExtra', c.c_int), ('hInstance', w.HINSTANCE),
                    ('hIcon', w.HANDLE), ('hCursor', w.HANDLE), ('hbrBackground', w.HANDLE),
                    ('lpszMenuName', w.LPCWSTR), ('lpszClassName', w.LPCWSTR), ('hIconSm', w.HANDLE)]

    user.RegisterClassExW.argtypes = [c.POINTER(WindowClass)]
    user.RegisterClassExW.restype = w.WORD
    user.UnregisterClassW.argtypes = [w.LPCWSTR, w.HINSTANCE]
    user.UnregisterClassW.restype = w.BOOL
    cls = WindowClass()
    cls.cbSize = c.sizeof(cls)
    cls.lpfnWndProc = c.cast(user.DefWindowProcW, c.c_void_p).value
    cls.hInstance = kernel.GetModuleHandleW(None)
    cls.lpszClassName = 'Osvanta.StartupResourceProbe'
    c.set_last_error(0)
    atom = user.RegisterClassExW(c.byref(cls))
    error = c.get_last_error() if not atom else 0
    result.update(checked=True, available=bool(atom), winerror=error,
                  message=c.FormatError(error).strip() if error else '')
    if atom:
        if not user.UnregisterClassW(cls.lpszClassName, cls.hInstance):
            result['cleanup_error'] = c.get_last_error()

    user.GetClipboardFormatNameW.argtypes = [w.UINT, w.LPWSTR, c.c_int]
    user.GetClipboardFormatNameW.restype = c.c_int
    buffer = c.create_unicode_buffer(1024)
    used = sum(bool(user.GetClipboardFormatNameW(index, buffer, len(buffer)))
               for index in range(0xC000, 0x10000))
    result['user_atoms'] = {'used': used, 'capacity': 16384}
    return result


def _report_paths():
    override = os.environ.get('OSVANTA_STARTUP_LOG')
    if override:
        yield Path(override)
    else:
        local = Path(os.environ.get('LOCALAPPDATA') or tempfile.gettempdir())
        yield local / 'Osvanta' / 'startup_diagnostic.json'
    yield Path(tempfile.gettempdir()) / f'osvanta_startup_error_{os.getpid()}.json'


def write_report(report: dict) -> Path | None:
    text = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    for path in _report_paths():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding='utf-8')
            return path
        except OSError:
            continue
    return None


def _native_error_message(title: str, message: str) -> bool:
    """Use Windows' built-in dialog rather than a second failing Qt window."""
    if sys.platform != 'win32':
        return False
    import ctypes as c
    from ctypes import wintypes as w
    user = c.WinDLL('user32', use_last_error=True)
    user.MessageBoxW.argtypes = [w.HWND, w.LPCWSTR, w.LPCWSTR, w.UINT]
    user.MessageBoxW.restype = c.c_int
    # Error icon + foreground request. Does not affect other applications.
    return bool(user.MessageBoxW(None, message, title, 0x00010010))


def report_failure(error: Exception, app_name: str, version: str, *, notify: bool = True) -> Path | None:
    try:
        diagnostic = windows_startup_check()
    except Exception as probe_error:
        diagnostic = {'probe_error': str(probe_error)}
    report = {'time_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'app': app_name, 'version': version, 'pid': os.getpid(),
              'stage': getattr(error, 'stage', 'initialisation'),
              'error': str(error), 'exception': type(error).__name__,
              'traceback': ''.join(traceback.format_exception(error)),
              'windows': diagnostic}
    path = write_report(report)
    message = (f'{app_name} could not start.\n\n{error}\n\n'
               f'Diagnostic report: {path or "could not be saved"}\n\n'
               'Close this message to exit the application.')
    if diagnostic.get('winerror'):
        message += f'\nWindows error {diagnostic["winerror"]}: {diagnostic.get("message", "")}'
    if sys.stderr is not None:
        try:
            print(message, file=sys.stderr, flush=True)
        except (OSError, ValueError, UnicodeError):
            pass
    if notify:
        try:
            _native_error_message(f'{app_name} - startup failed', message)
        except Exception:
            # Even error reporting must not turn a failed start into a process
            # waiting forever in Qt's event loop. The report is already saved.
            pass
    return path
