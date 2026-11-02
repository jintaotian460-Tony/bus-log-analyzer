# Osvanta Bus Log Analyzer

> A portable Windows application for loading automotive measurements, decoding CAN signals, inspecting raw frames, and plotting signal data interactively.

> **User guide:** [osvanta.github.io/bus-log-analyzer](https://osvanta.github.io/bus-log-analyzer/),
> or **Help ▸ Getting started** (`F1`) in the application.

> Formerly released as **CANScope**. The repository moved to
> [github.com/osvanta/bus-log-analyzer](https://github.com/osvanta/bus-log-analyzer)
> in August 2026.

![Python](https://img.shields.io/badge/Python-3.12-blue?logo=python)
![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey?logo=windows)
![License](https://img.shields.io/badge/License-MPL--2.0-green)
![Release](https://img.shields.io/github/v/release/osvanta/bus-log-analyzer)

## Screenshots

Stacked plot:
<img width="2299" height="1588" alt="Stacked plot" src="https://github.com/user-attachments/assets/57baedc2-0382-4f63-a7e9-cc786e18ded8" />


Multiple Axis plot:
<img width="2214" height="1590" alt="multi-axis plot" src="https://github.com/user-attachments/assets/7d3ef351-1b02-45e0-85f5-4dfd1217ad6e" />

Trace, the raw CAN and LIN frames:
<img width="1543" height="1019" alt="CANScope_RawCAN Frame" src="https://github.com/user-attachments/assets/69ad9749-ecec-445d-a31f-4b47901855cf" />

## Supported formats

| Measurement | Content | Database |
|---|---|---|
| BLF (`.blf`) | Raw CAN frames | DBC or ARXML required |
| ASC (`.asc`) | Raw CAN frames | DBC or ARXML required |
| TRC (`.trc`) | Raw CAN frames (PEAK trace, versions 1.0 to 3.0) | DBC or ARXML required |
| MDF4/MDF3 (`.mf4`, `.mdf`) | Raw CAN bus logging or pre-decoded signals | Required only for raw bus logging |
| CSV (`.csv`) | Raw CAN export, narrow signals, or wide signals | Required only for raw CAN data |

MDF and CSV content is detected automatically. Database Manager supports per-channel DBC/ARXML assignments, mixed database formats, decode-coverage indicators, and a channel-0 fallback.


## Current capabilities

- Fast bulk loading and decoding for BLF, ASC, TRC, MDF/MF4, and CSV measurements.
- Searchable channel/message/signal tree with multi-select plotting and drag-and-drop.
- Indexed **Trace** of the raw CAN and LIN frames, with filtering and on-demand signal decode. Its **Δt** column shows the time since the previous frame with the same ID on the same channel, the bytes that changed since that frame are highlighted, and it can follow plot Cursor 1 both ways.
- Formula-based **Calculated Signal** with arithmetic, comparisons, logical expressions, temporal operations, and a stateful diagnostic counter; **Insert function** (`Shift+F3`) lists every function by category, with how to use each. The definitions are saved in configurations.
- **Statistics** in the signal table: Min, Max, Mean, Std dev, RMS and Samples, over the visible range or between the cursors, and the **Δ C2−C1** difference between the cursors. Right-click the table header to choose the columns.
- **Table**: the plotted signals as a table, right of the plot, a row per sample time.
- Named **markers** on the plot: add one where you right-click or at Cursor 1 (`M`), drag, rename, go to; they are saved in configurations and written into MF4 exports as events.
- **File ▸ Measurement info** (`Ctrl+I`): the file's format, size, start time and duration, and for MF4 its header, events and attachments. MF4 events can be drawn on the plot.
- Export of the plotted signals to CSV or Excel (`.xlsx`), on a selected signal's timestamps or a recurrence time you enter, or to MF4; **File ▸ Save decoded measurement as MF4** writes every decoded signal.
- **Tools ▸ Convert files**: join, cut and resample measurements, keep chosen signals, and convert between BLF, ASC, MF4 and CSV (TRC as an input). Signal selections can be saved by name and reused on other files, or shared as text files.
- JSON configuration save/load for measurement paths, database mapping, calculated signals, plotted order, groups, colors, visibility, axes, cursors, markers, and display settings.
- YAML rule-based diagnostics for expressions, ranges, fault flags, and message loss; optional GitHub Models analysis is available through `Ctrl+Shift+A`.

### Offline telemetry playback

After decoding, use **Play / Pause**, **Restart**, the speed selector, and the
position slider or seconds field above the plot. Cursor 1, its value columns,
the Table and an open Trace follow the playback position. **Follow view** scrolls
the plot without changing its Y scale. Clicking or dragging a cursor pauses
playback at that position. The clock advances by elapsed time, including when
the GUI briefly stalls. Reaching the end pauses; Play starts again from the start.

**Stale after** marks each plotted signal separately when it has had no update
for longer than the selected timeout. Longer gaps have amber shading and broken
curves; cursor values say **Stale** rather than presenting a held value as a new
measurement. Before a signal's first sample they say **No data**. Zero disables
gap marking. Raw measurements and exported samples are unchanged. The supplied
E702 project selects a 2-second timeout; recordings without a configured default
start with gap marking off. Configure the DBC that matches the recorded layout.

The playback bar displays absolute time when the source has an epoch time base.
**BLF UTC → local** is the standard interpretation. Some cloud CSV-to-BLF
converters write local clock fields into the BLF header: select **Cloud local
wall time** for these files to retain the original clock time, avoiding a second
timezone conversion. This is an explicit display setting, not a change to every
BLF or to relative timestamps. Time mode, speed, timeout, position and Follow
view are saved with the configuration; playback always reopens paused.

Open a saved playback session directly with `BusLogAnalyzer.exe --config session.json`.
This feature replays measurements on screen; it does not transmit on a CAN bus.

### Projects and additional CSV signals

In **Log Explorer**, right-click a file or folder to open its location, copy
its path, copy/paste files or folders, or compress/extract ZIP archives. ZIP
files are visible alongside measurement files. Ctrl+C / Ctrl+V and multiple
selection are supported; Explorer's file clipboard works in both directions.
File operations run in the background and can be cancelled. Existing items
are kept: duplicate names get a numeric suffix. Extraction creates a separate
folder and rejects paths outside it. RAR and 7z extraction are not supported.

**File → Project settings** names a project, sets its clock and freshness defaults,
and adds CSV signal sources. Each CSV source selects its timestamp column and
physical signal columns, its encoding and delimiter, and a time alignment rule.
CSV signals appear under **CSV signals**, retain their own samples, and share the
plot/playback axis with the main recording. Blank cells are missing samples;
zero remains zero. They do not create CAN/LIN frames or alter the raw Trace.
The importer sorts reverse-ordered exports and refuses missing columns, invalid
timestamps, and source clocks that do not overlap the recording.

For cloud converters that write local fields directly into the BLF header,
choose **Cloud BLF header contains local clock fields** for the CSV. For a standard
UTC BLF, choose **Absolute date/time with UTC offset** and enter the CSV's offset.
Already aligned numeric CSV times use **Relative seconds from recording zero**.
An additional time adjustment is explicit; no resampling or value interpolation
is introduced. Changing only the playback display timezone does not move samples.

**File → Save project** writes a reusable project profile: DBC/channel mappings,
CSV sources and selected columns, time/freshness rules, and default plot keys.
It excludes the specific recording and playback progress. **Open project** starts
a fresh session; choose that project's main measurement afterward. **Save/Open
configuration** continues to save/reopen a particular recording and its view.
Database and fixed CSV paths are stored relative to the project file where
possible. A source path such as `{measurement_stem}.csv` selects a same-named
CSV beside each main recording, so a project can be reused on later logs.

Project-specific DBCs, recorded data and personal project profiles stay outside
the source repository. No E702 or ACT7 column names are built into the importer.

### Plot controls

- **Stacked** is the default mode and gives each signal its own lane and Y scale.
- **Multi-Axis** overlays the signals, with one axis per unit and an optional individual axis per signal.
- **MultiStack** has lanes that each hold several signals; drag signals into a lane to overlay them there.
- Dual draggable cursors provide values and delta time; a click on the plot places Cursor 1 there and a Shift+click places Cursor 2. A cursor switched on always appears inside the visible window, and cursors are black on a light plot background and white on a dark one.
- Fit-to-window and vertical-fit work across all plot modes while preserving the relevant view range.
- After the plot mode buttons, Fit to Window, Fit Vertical, the cursors, the line mode, Show Data Points and Hide Line are black-and-white icons; pointing at one shows its name.
- Signals can be grouped, reordered by dragging, recolored, hidden, or removed.
- The line-mode button (`S`) switches between **Step Mode**, the default, where each value is held until the next sample, and **Interpolation Mode**, where straight lines join the samples. Its icon shows the mode the plot is in.
- **Show Data Points** uses adaptive markers and thinner lines.
- **Hide Line** is enabled only when data points are shown; when ON, it displays points without signal lines.

## Install and run

### Portable release

Download the latest Windows ZIP from [Releases](https://github.com/osvanta/bus-log-analyzer/releases), extract it, and run `BusLogAnalyzer.exe`. No installer or Python installation is required.

### From source

```powershell
git clone https://github.com/osvanta/bus-log-analyzer.git
cd bus-log-analyzer
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

The portable build and CI release build use Python 3.12.

## Basic workflow

1. Select **Open Measurement** and choose a supported measurement.
2. If the file contains raw CAN frames, select **Databases** and configure DBC/ARXML mapping.
3. Select **Decode** (`F9`).
4. Search the signal tree, then double-click, drag, press `Space`, or use the context menu to plot signals.
5. Use cursors, plot modes, data points, grouping, and fit controls as needed.
6. In the **File** menu, select **Save configuration** to preserve the session, or **Export plotted signals** to write them to CSV, Excel or MF4.
7. Open the **Trace** when the source contains raw CAN frames.
8. The **Help** menu has Getting started (`F1`), the keyboard shortcuts, GitHub Discussions for questions and ideas, GitHub issues for problems and feature requests, and About.

## Keyboard shortcuts

| Key | Action |
|---|---|
| `F1` | Getting started, in the user guide |
| `Ctrl+O` | Open Measurement |
| `F9` | Decode |
| `Ctrl+S` | Save configuration |
| `Ctrl+E` | Export plotted signals |
| `Ctrl+I` | Measurement info |
| `Ctrl+Shift+R` | Open the Trace |
| `Shift+F3` | Insert function, in Calculated Signal |
| `F` | Fit X and Y to all visible data |
| `V` | Fit Y to the current X range |
| `Z` / `Esc` | Rectangle Zoom on or off / leave it |
| `S` | Step Mode or Interpolation Mode |
| `Space` | Plot selected signal-tree entries |
| `C` | Change the selected signal color |
| `R` | Toggle both cursors |
| `M` | Add a marker at Cursor 1 |
| `Delete` | Remove selected plotted signals |
| `Ctrl+Z` | Undo the last plot action |
| `Ctrl+Shift+-` / `Ctrl+Shift++` | Collapse / expand signal-tree messages |


## Test and build

```powershell
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m pytest
```

Build the portable application with:

```powershell
.\build_portable.bat
```

Output is written to `dist\BusLogAnalyzer\`. Pushing a `v*.*.*` tag runs the Windows build workflow and publishes a release ZIP.

## Repository layout

```text
app.py                 Application entry point and version
core/readers/          BLF, ASC, TRC, MDF/MF4, and CSV readers
core/                  Decode, signal storage, export, and generated-signal logic
core/diagnostics/      Rule engine and optional AI-assisted diagnostics
gui/                   Main window, plots, database manager, CAN Trace, diagnostics
config/diagnostics/    User-editable diagnostic rules and documentation
resources/             Application icon and splash assets
guide/, mkdocs.yml     User guide, published to GitHub Pages
tests/                 Pytest regression suite
```

Runtime dependencies are maintained in [requirements.txt](requirements.txt).

## Project log browsing and cloud playback

The **Log Explorer** opens a folder tree whose root is saved with the project.
Double-clicking a supported log replaces and decodes the active measurement.
The explorer can be hidden; **Signals** opens the separate movable floating
signal tree. Use the toolbar actions to show either panel again.
The floating signal tree uses standard title-bar minimize, maximize/restore
and close buttons. Close hides only the signal window and stays hidden while
switching logs. **Signals** restores a hidden or minimized signal window;
saved configurations retain the visibility choice.

Under **File → Project settings → CAN/LIN import matrices**, configure a fixed
set of DBC/LDF versions, platform network numbers and exported BLF channels.
Unassigned candidates are considered only when the frame ID and signal names
identify one matrix uniquely. Saved matrix fingerprints reject later file
changes until the version is reviewed in Project settings. An optional signal
DBC expands packed telemetry fields instead of displaying their 64-bit chunks.

Opening a matching cloud CSV converts it to a cached single-file playback BLF.
**File → Save playback BLF…** saves a portable copy. The BLF includes normal
CAN/CAN FD/LIN frame objects, decoder definitions and Osvanta observation
markers; reopening it needs no neighbouring CSV or external database.
Only complete, representable observations produce reconstructed bus frames.
Partial observations retain actual values and explicit missing markers rather
than invented payload values. Unsupported fields are listed in Diagnostics.
Plots show **未采集** at a missing observation and retain the configured stale
timeout and clock interpretation.

These are reconstructed cloud observations, not original bus captures: wire
ordering at equal timestamps, unobserved reserved bits, timing inside a frame
and bus-error events cannot be recovered. Third-party BLF readers can read the
standard frame objects; they may ignore Osvanta's sparse-observation markers.

### Keep signal choices when changing recordings

Double-clicking another log in the explorer carries selected signals and plot
presentation (order, colour, groups, line style and axes) to the new recording.
Only the complete signal name must match, including case and whitespace;
units, buses, channels and message groups do not block inheritance. Values
and units always come from the new recording. For multiple same-name channels,
the existing full key is preferred when present; otherwise the first channel
in recording order is used. Old selections that resolve to one channel appear
once. Right-click an unmatched entry and choose **匹配到本日志信号…** to select
a different signal manually.

Unavailable signals remain grey, labelled **本日志未匹配**, with empty samples.
They recover when a later recording contains them. Calculated curves are
recomputed from the new inputs. Changing between MF4/MDF and telemetry/bus
recordings clears this handoff; opening another project also clears it.

### Inspect original samples and the time ruler

The **采样点** button shows original sample positions. Dense views retain a
capped subset and explicitly display **稀疏显示，放大查看全部**; after zooming,
**全量（当前视图）** means every sample in view is drawn. Hover a point for its
timestamp, value and interval from the preceding original sample.

The bottom time ruler has a baseline, long labelled ticks and short minor
ticks, also when grid lines are enabled. **10 ms** focuses the cursor or view
centre with 10 ms minor ticks; **Fit** restores the full recording. These
controls change the view, not the recorded sampling interval. All four plot
layouts use the same ruler.

### Multi-window analysis and engineering templates

**多窗口** arranges graph windows vertically, each with its own signal list.
The obsolete global signal list stays hidden and takes no plot width, including
after startup, configuration restore and log changes.
Click its title to select the destination, then double-click a signal or drag
it into that window. Moving an already plotted signal transfers it to the
destination window. **＋窗口** adds a window; arrows change its order, its close
button removes it and its curves. Drag the thin line between two windows to
adjust their relative heights. Windows fill the available area proportionally,
with no outer padding or gaps; extra windows scroll at a readable minimum height.
The last graph meets the persistent bottom time ruler on the right. Its signal
list spans the ruler height and reaches the cursor/file information bar directly;
there is no unused strip below the list, including after resizing or scrolling. The log explorer and
bottom information panel also use thin divider lines: drag to resize,
double-click to collapse or reopen. Collapsed panels keep their line at the edge.
Each window chooses either **全部共轴** or **每条信号独立轴** for all its signals.
Horizontal navigation and both cursors are synchronized in both directions.
A shared time ruler stays below the scrollable windows, including at 10 ms zoom.
Hold the left mouse button on the bottom ruler and drag horizontally to pan
time in every window. This also works in the other plot layouts. Panning keeps
the zoom span, Y ranges and cursor times, and turns off **Follow view** so
playback cannot pull the viewport back.

The **线宽** menu selects 0.5, 1, 1.5, 2, 3 or 4 px for all signal curves.
An explicit width also applies to selected signals and point mode; **自动**
uses the original automatic widths. The choice survives log switches and is
saved in configurations and analysis templates. On narrow windows it is
available as a submenu under **»**.

Drag a signal name from a window's local list to another window's list or
plot to move it. Ctrl/Shift selects several signals for one move. Colors,
line styles, visibility and original data stay with the signals; **Ctrl+Z**
undoes the move. Dropping inside the same window keeps the existing curve.

**分析模板 → 保存当前分析为模板…** saves a named view in the active engineering
project. The first save asks for a project file if none is linked. The menu
applies, renames and deletes that project's templates. Templates contain signal
identities, styles, groups, window ownership/order/heights, axis mode and Y
limits, and the definitions required by plotted calculated signals. They do
not contain measurement files, samples, saved time ranges or cursor positions.
Applying one replaces the current curves and layout while retaining the open
recording, current time range, both cursor positions and measurement events.
Missing signals stay grey and may be confirmed manually; calculations use the
current recording. Existing same-family inheritance and cross-family clearing
also apply to the window layout. Project libraries remain separate.

## Security and license

Release binaries are currently unsigned, so Windows SmartScreen may show a warning on first launch.

Osvanta Bus Log Analyzer is licensed under the [Mozilla Public License 2.0](LICENSE).

MPL-2.0 is a file-level copyleft license. You may use it freely, including
inside commercial and proprietary products, and you may combine these files with
files of your own under any terms you choose. The one obligation is reciprocal:
if you distribute a modified version of a file that carries the MPL notice, the
source of that modified file must be made available under the MPL.

Releases up to and including `v00.00.59` were published under the MIT License and
remain available under those terms — see [LICENSE-MIT](LICENSE-MIT). The change to
MPL-2.0 applies from `v00.00.60` onward.

Osvanta Bus Log Analyzer bundles third-party components under their own licenses, including
LGPL-3.0 components (Qt via PySide6, python-can, asammdf, chardet). They are used
unmodified and remain available from their upstream projects. See
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for the full component list, and
`licenses/` for the complete license texts — both ship inside the portable build.

"Osvanta", "Osvanta Bus Log Analyzer", and the earlier names "CAN Scope" and
"CANScope" are names used by this project. The license grants rights to the
code, not to the project name.
