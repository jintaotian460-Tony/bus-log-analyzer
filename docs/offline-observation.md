# Offline observation interface

The observation area uses the compact graphic-window layout familiar from CANape:
a light signal legend beside each plot, a shared time ruler and linked cursors.
Only this area is styled; loading, decoding, project configuration and the other
application panels retain their existing behavior.

- Only the top window shows the **信号 / C1 / C2** column header. Lower windows
  start directly with signal rows, without duplicate headings or empty header space.
  Reordering, closing or restoring windows keeps the heading on the top window only.
  There is no separate window title or up/down/close button row. The window name
  appears only in its right-click menu. Right-click a signal, the top list header,
  empty list space or its plot to choose
  the target for new signals, rename, reorder, close, or switch shared/individual
  Y axes. Closing removes that window's plotted curves, not source measurements;
  Ctrl+Z restores the window and its curves.
- Signal names use dark text. A line sample beside each name identifies its color
  and line pattern, including bright yellow or white curves.
- C1/C2 values are right aligned. Hover a name for its full identity and unit;
  hover a cursor-column heading for its time.
- Y axes show units and ticks. Full signal names stay in the legend and axis tooltip.
- Dotted green grid lines are independent of the text color, on dark and light plots.
- The shared bottom ruler supports left-drag time panning and wheel zoom. The top
  cursor handles move the linked cursors across all windows.
- The header is a single recording-status row. Playback transport controls and
  their Follow view option are absent; the fixed 10 ms toolbar shortcut is removed.
  The ruler still resolves 10 ms and finer intervals when zoomed with the wheel.
- Original sample points, configurable line width, window-to-window signal dragging,
  templates and exact-name inheritance retain their existing behavior.

## Reference and boundary

Reference: Vector, [CANape Quick Start, version 1.07, January 2023](https://support.vector.com/sys_attachment.do?sys_id=048d66643bbf6650c1dc1d24c3e45ab5),
Analysis section, printed pages 34–36 (graphic legends, display control and measurement
cursor). The dark plot background follows the user's CANape screenshots; the official
quick-start example also illustrates a light background. No proprietary artwork or
CANape code is incorporated.

This is an Osvanta presentation change, not CANape file compatibility or online
measurement/calibration support. Removed legacy layout choices are not reintroduced.
