"""Physical CSV signals aligned to a recording; never synthesises bus frames."""
from __future__ import annotations

import codecs
import csv
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path

import numpy as np


class SupplementalCSVError(ValueError):
    pass


def source_path(spec: dict, measurement: str | Path) -> Path:
    measurement = Path(measurement)
    path = str(spec.get('path', '')).strip()
    if not path:
        raise SupplementalCSVError('Select a supplemental CSV file')
    path = path.replace('{measurement_stem}', measurement.stem)
    result = Path(path)
    return result if result.is_absolute() else measurement.parent / result


def csv_header(path, encoding='auto', delimiter=','):
    path = Path(path)
    if delimiter not in (',', ';', '\t'):
        raise SupplementalCSVError('CSV delimiter must be comma, semicolon or tab')
    if encoding == 'auto':
        with path.open('rb') as stream:
            sample = stream.read(65536)
        for candidate in ('utf-8-sig', 'gb18030'):
            try:
                codecs.getincrementaldecoder(candidate)().decode(sample, final=False)
                encoding = candidate
                break
            except UnicodeError:
                continue
        else:
            raise SupplementalCSVError(f'Cannot determine CSV encoding: {path.name}')
    with path.open(encoding=encoding, newline='') as stream:
        for row in csv.reader(stream, delimiter=delimiter):
            if any(value.strip() for value in row):
                header = [value.strip() for value in row]
                if len(header) != len(set(header)):
                    raise SupplementalCSVError('CSV has duplicate column headers')
                return encoding, header
    raise SupplementalCSVError(f'CSV is empty: {path.name}')


def signal_name(header):
    """Cloud exports append description and period; keep the literal name."""
    return header.split('(', 1)[0].strip()


def _timestamp(value, spec, origin):
    kind = spec.get('time_basis', 'wall')
    adjustment = float(spec.get('offset_seconds', 0))
    if not math.isfinite(adjustment):
        raise SupplementalCSVError('Time adjustment must be finite')
    if kind == 'relative':
        stamp = float(value) + adjustment
    else:
        if origin <= 946684800:
            raise SupplementalCSVError('Date/time CSV needs a recording with an absolute time origin')
        fmt = spec.get('time_format', '')
        moment = (datetime.strptime(value, fmt) if fmt
                  else datetime.fromisoformat(value.replace('/', '-')))
        if kind == 'wall':
            if moment.tzinfo is not None:
                raise SupplementalCSVError('Wall-clock alignment expects dates without a timezone')
            moment = moment.replace(tzinfo=timezone.utc)
        elif kind == 'absolute':
            offset = float(spec.get('utc_offset_hours', 0))
            if not math.isfinite(offset) or not -14 <= offset <= 14:
                raise SupplementalCSVError('CSV UTC offset must be between -14 and +14 hours')
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone(timedelta(hours=offset)))
        else:
            raise SupplementalCSVError(f'Unknown CSV time alignment: {kind}')
        # Subtract datetimes before converting to float, preserving milliseconds
        # rather than subtracting two large, independently rounded epochs.
        stamp = (moment.astimezone(timezone.utc) -
                 datetime.fromtimestamp(origin, timezone.utc)).total_seconds() + adjustment
    if not math.isfinite(stamp):
        raise SupplementalCSVError('CSV timestamp is not finite')
    return stamp


def read_source(spec, measurement, origin):
    path = source_path(spec, measurement)
    encoding, header = csv_header(path, spec.get('encoding', 'auto'), spec.get('delimiter', ','))
    time_column = str(spec.get('time_column', '')).strip()
    if time_column not in header:
        raise SupplementalCSVError(f'{path.name}: timestamp column not found: {time_column}')
    selected = spec.get('columns', [])
    if not selected:
        prefix = spec.get('prefix', '')
        if not prefix:
            raise SupplementalCSVError('Choose CSV signal columns or a non-empty signal prefix')
        selected = [h for h in header if signal_name(h).startswith(prefix)]
    if not selected:
        raise SupplementalCSVError(f'{path.name}: no CSV signal columns matched')
    columns = []
    for entry in selected:
        entry = {'column': entry} if isinstance(entry, str) else entry
        requested = entry['column']
        matches = [i for i, h in enumerate(header) if h == requested or signal_name(h) == requested]
        if len(matches) != 1:
            raise SupplementalCSVError(f'{path.name}: column must match exactly once: {requested}')
        i = matches[0]
        if header[i] == time_column:
            raise SupplementalCSVError('A timestamp column cannot also be a signal')
        columns.append((i, entry.get('name') or signal_name(header[i]), entry.get('unit', '')))
    names = [c[1] for c in columns]
    if len(names) != len(set(names)):
        raise SupplementalCSVError('Supplemental signal names must be unique within a source')
    samples = [[] for _ in columns]
    missing, invalid = 0, 0
    with path.open(encoding=encoding, newline='') as stream:
        reader = csv.reader(stream, delimiter=spec.get('delimiter', ','))
        header_seen = False
        for line_number, row in enumerate(reader, 1):
            if not any(value.strip() for value in row):
                continue
            if not header_seen:
                header_seen = True
                continue
            if len(row) != len(header):
                raise SupplementalCSVError(f'{path.name}, row {line_number}: column count differs from header')
            cells = [row[i].strip() for i, _, _ in columns]
            if not any(cells):
                missing += 1
                continue
            try:
                stamp = _timestamp(row[header.index(time_column)].strip(), spec, origin)
            except (ValueError, OverflowError) as exc:
                raise SupplementalCSVError(f'{path.name}, row {line_number}: {exc}') from exc
            for target, cell in zip(samples, cells):
                if not cell:
                    continue
                try:
                    number = float(cell)
                    if not math.isfinite(number):
                        invalid += 1
                        continue
                    if cell.lstrip('+-').isdigit() and abs(int(cell)) > 2**53:
                        raise SupplementalCSVError('Selected an integer beyond exact float precision; import packed frames through a bus reader')
                    label = None
                except SupplementalCSVError:
                    raise
                except ValueError:
                    number, label = float('nan'), cell
                target.append((stamp, number, label))
    result = []
    for (_, name, unit), rows in zip(columns, samples):
        if not rows:
            raise SupplementalCSVError(f'{path.name}: selected signal has no valid samples: {name}')
        rows.sort(key=lambda row: row[0])
        labels = any(row[2] is not None for row in rows)
        result.append({'name': name, 'unit': unit,
                       'timestamps': np.array([row[0] for row in rows], dtype=np.float64),
                       'values': np.array([row[1] for row in rows], dtype=np.float64),
                       'labels': [row[2] if row[2] is not None else row[1] for row in rows] if labels else []})
    report = {'path': str(path), 'name': str(spec.get('name') or path.stem),
              'signals': len(result), 'samples': sum(len(s['timestamps']) for s in result),
              'encoding': encoding, 'empty_rows': missing, 'invalid_values': invalid,
              'time_basis': spec.get('time_basis', 'wall')}
    return result, report


def attach_csv_sources(store, sources, measurement, progress=lambda text: None):
    """Read and validate every source before adding any supplemental series."""
    staged, names = [], set()
    primary_bounds = store.time_bounds()
    for spec in sources:
        signals, report = read_source(spec, measurement, store.base_ts)
        span = (min(float(s['timestamps'][0]) for s in signals),
                max(float(s['timestamps'][-1]) for s in signals))
        if primary_bounds[0] is not None and (span[1] < primary_bounds[0] or span[0] > primary_bounds[1]):
            raise SupplementalCSVError(
                f"{report['name']}: CSV times do not overlap the recording. "
                'Check the file, time alignment and UTC offset.')
        message = f"CSV: {report['name']}"
        if message in names:
            raise SupplementalCSVError('CSV source group names must be unique')
        names.add(message)
        for signal in signals:
            key = store._make_key(None, message, signal['name'])
            if key in store.all_keys():
                raise SupplementalCSVError(f'Supplemental signal already exists: {key}')
        staged.append((message, signals, report))
    bounds, reports = [], []
    decoded, channels = store.decoded_frames, set(store.channels)
    for message, signals, report in staged:
        for signal in signals:
            store.add_series_bulk(channel=None, message_name=message, message_id=0,
                                  signal_name=signal['name'], unit=signal['unit'],
                                  timestamps=signal['timestamps'], values=signal['values'],
                                  raw_values=signal['labels'], has_labels=bool(signal['labels']))
            bounds.append((float(signal['timestamps'][0]), float(signal['timestamps'][-1])))
        progress(f"CSV source {report['name']}: {report['signals']} signals, {report['samples']:,} samples; aligned by {report['time_basis']}")
        reports.append(report)
    # Supplemental physical values do not create decoded/raw CAN/LIN frames.
    store.decoded_frames, store.channels = decoded, channels
    store.csv_source_reports = reports
    store.supplemental_time_bounds = ((min(x[0] for x in bounds), max(x[1] for x in bounds))
                                      if bounds else None)
