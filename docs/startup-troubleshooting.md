# A start with no visible window

The application now checks that Windows actually created both the startup
screen and the main window. If either native window is missing, it writes a
diagnostic report, tries Windows' built-in error dialog and exits with code 2.
It does not enter Qt's event loop with no usable window. The release-test
runner checks its own window too, before discovering measurements or starting
scenarios. Automated release runs save the failure and exit without a modal
dialog that could block the controller.

The latest diagnostic is stored at
`%LOCALAPPDATA%\Osvanta\startup_diagnostic.json`, with a temporary-folder
fallback if that location cannot be written. `OSVANTA_STARTUP_LOG` can name a
different report file for automated testing. The report contains startup
errors and resource counts, not clipboard contents or measurement samples.

`BusLogAnalyzer.exe --check-startup` checks native Windows class registration
without importing Qt or opening a measurement, saves the same report, and
returns 0 when the probe succeeds or 2 when it fails. A console/source run
also prints the report. This is a diagnostic, not a guarantee that a complete
application will start, and it does not block an otherwise successful Qt start.

Windows can return error 8 even with free physical RAM. Its User Atom table is
also used to register window-class, message and clipboard-format names. The
report includes its occupancy when a native Windows window cannot be created.
The program does not delete other applications' atoms, terminate them, modify
system settings or end the Windows session.

Reference: [Microsoft: About Atom Tables](https://learn.microsoft.com/en-us/windows/win32/dataxchg/about-atom-tables).

An offscreen test does not validate a visible Windows window. Verify a native
start separately before treating a new build as ready for desktop use.
