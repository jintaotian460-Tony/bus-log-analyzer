"""Project-scoped cloud CSV -> CAN/LIN BLF, including sparse-signal evidence.

Complete reconstructed frames use normal BLF objects. Physical observations,
missing values and the exact decoder definitions also travel inside standard
GLOBAL_MARKER objects. No neighbouring CSV is needed when reopening the file.
These markers are an Osvanta extension, not a claim of original bus capture.
"""
from __future__ import annotations

import copy
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import re
import struct
import tempfile
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation

import can
from can.io import blf
import numpy as np

PREFIX = 'OSVANTA:CLOUD:1:'
COLUMN = re.compile(r'^([^()]+)\((.*)\)/(\d+)ms$')


def cache_directory():
    base = os.environ.get('OSVANTA_DATA_DIR') or str(Path(os.environ.get('LOCALAPPDATA', tempfile.gettempdir())) / 'Osvanta' / 'BusLogAnalyzer')
    folder = Path(base) / 'cloud_cache'
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def columns(path):
    from core.supplemental_csv import csv_header
    encoding, headers = csv_header(path, 'auto', ',')
    groups = {}
    for index, header in enumerate(headers):
        match = COLUMN.match(header.strip())
        if not match:
            continue
        parts = match[2].rsplit('-', 2)
        if len(parts) != 3:
            continue
        try:
            frame_id = int(parts[1], 16)
        except ValueError:
            continue
        key = (parts[2], frame_id)
        group = groups.setdefault(key, {})
        name = match[1].strip()
        if name in group:
            raise ValueError(f'Duplicate cloud signal: {name}')
        group[name] = index
    return encoding, headers, groups


def is_cloud_csv(path):
    return Path(path).suffix.lower() == '.csv' and bool(columns(path)[2])


def _bits(signal):
    position = signal.start
    result = set()
    for _ in range(signal.length):
        result.add(position)
        position = position + 1 if signal.byte_order == 'little_endian' else (position + 15 if position % 8 == 0 else position - 1)
    return result


def _decode_signal(signal, payload):
    """Decode independently so overlapping diagnostic fields remain usable."""
    position, raw = signal.start, 0
    for index in range(signal.length):
        shift = index if signal.byte_order == 'little_endian' else signal.length-index-1
        raw |= ((payload[position//8] >> (position % 8)) & 1) << shift
        position = position+1 if signal.byte_order == 'little_endian' else (position+15 if position % 8 == 0 else position-1)
    if signal.is_float:
        raw = struct.unpack('<f' if signal.length == 32 else '<d', raw.to_bytes(signal.length//8, 'little'))[0]
    elif signal.is_signed and raw & (1 << (signal.length-1)):
        raw -= 1 << signal.length
    return signal.raw_to_scaled(raw, decode_choices=False)


def _number(cell, signal, packed=False):
    if cell == '':
        return None
    try:
        number = Decimal(cell)
        if not number.is_finite():
            raise ValueError('Non-finite value')
        if packed:
            if number != number.to_integral_value() or not -(1 << 63) <= number < (1 << 64):
                raise ValueError('Packed value is not an exact 64-bit integer')
            return int(number) & ((1 << signal.length) - 1)
        value = float(number)
        if not math.isfinite(value):
            raise ValueError('Value outside numeric range')
        if number == number.to_integral_value() and abs(number) > 2**53:
            raise ValueError('Physical integer exceeds exact plot precision; use an exact packed field instead')
        return value
    except InvalidOperation:
        matches = [int(key) * signal.scale + signal.offset for key, value in (signal.choices or {}).items() if str(value) == cell]
        if len(matches) == 1:
            return float(matches[0])
        raise ValueError(f'Unrecognised value: {cell}') from None


def _timestamp(text, settings):
    value = datetime.fromisoformat(text.strip().replace('/', '-'))
    if settings.get('time_basis', 'wall') == 'wall':
        if value.tzinfo is not None:
            raise ValueError('Wall-clock timestamps must not carry a timezone')
        value = value.replace(tzinfo=timezone.utc)
    elif value.tzinfo is None:
        value = value.replace(tzinfo=timezone(timedelta(hours=float(settings.get('utc_offset_hours', 0)))))
    return value.timestamp() + float(settings.get('offset_seconds', 0))


class CloudWriter(can.BLFWriter):
    def stop(self):
        while not self.file.closed and self._buffer_size:
            self._flush()
        super().stop()

    def marker(self, record, timestamp):
        encoded = (PREFIX + json.dumps(record, ensure_ascii=True, separators=(',', ':'), allow_nan=False)).encode('ascii')
        marker = b'Osvanta cloud reconstruction'
        body = blf.GLOBAL_MARKER_STRUCT.pack(0, 0xffffff, 0xff9900, 0, len(encoded), len(marker), 0)
        self._add_object(blf.GLOBAL_MARKER, body + encoded + marker, timestamp)

    def lin(self, timestamp, channel, frame_id, payload):
        body = bytearray(128)
        struct.pack_into('<H', body, 12, channel)
        body[37], body[38], body[39] = frame_id, len(payload), 1
        body[112:112 + len(payload)] = payload
        # Recomputed checksum, not an observed wire checksum. Direction unknown.
        ident = frame_id | (((frame_id ^ (frame_id >> 1) ^ (frame_id >> 2) ^ (frame_id >> 4)) & 1) << 6)
        ident |= ((~((frame_id >> 1) ^ (frame_id >> 3) ^ (frame_id >> 4) ^ (frame_id >> 5)) & 1) << 7)
        checksum = sum(payload) + (0 if frame_id in (60, 61) else ident)
        while checksum > 255:
            checksum = (checksum & 255) + (checksum >> 8)
        struct.pack_into('<H', body, 120, checksum ^ 255)
        body[122] = 2
        self._add_object(57, bytes(body), timestamp)


def _database(spec):
    from core.dbc_decoder import load_database_file
    db, _ = load_database_file(spec['path'], strict_first=False)
    return db


def convert_csv(path, settings, destination=None, progress=lambda text: None):
    """Validate completely before atomically publishing a newly generated BLF."""
    import cantools
    path = Path(path).resolve()
    settings = copy.deepcopy(settings)
    encoding, headers, groups = columns(path)
    time_column = settings.get('time_column', '采集时间')
    if time_column not in headers:
        raise ValueError(f'Missing timestamp column: {time_column}')
    matrices = []
    for spec in settings.get('matrices', []):
        spec = dict(spec)
        if not spec.get('path'):
            continue
        spec['sha256'] = sha256(spec['path'])
        if spec.get('sha256_locked') and spec['sha256_locked'] != spec['sha256']:
            raise ValueError(f"Matrix changed since project was saved: {spec['path']}. Review and replace it in Project settings.")
        matrices.append((spec, _database(spec)))
    if not matrices:
        raise ValueError('Configure the project CAN/LIN matrices before importing cloud CSV.')
    telemetry = _database({'path': settings['telemetry_database']}) if settings.get('telemetry_database') else None
    if telemetry and settings.get('telemetry_sha256_locked') and sha256(settings['telemetry_database']) != settings['telemetry_sha256_locked']:
        raise ValueError('Telemetry signal DBC changed since project was saved. Review it in Project settings.')
    bound, unmatched, embedded, bindings = [], [], {}, []
    used_channels = {}
    for (network, frame_id), names in groups.items():
        candidates = []
        assigned_matrices = [(spec, db) for spec, db in matrices if network in [p.strip() for p in str(spec.get('network', '')).split(',')]]
        for spec, db in assigned_matrices or matrices:
            assigned = str(spec.get('network', '')).strip()
            if assigned and network not in [part.strip() for part in assigned.split(',')]:
                continue
            for message in db.messages:
                overlap = set(names) & {s.name for s in message.signals}
                if message.frame_id == frame_id and overlap:
                    candidates.append((len(overlap), spec, db, message))
        if candidates:
            best = max(item[0] for item in candidates)
            candidates = [item[1:] for item in candidates if item[0] == best]
        if len(candidates) > 1:
            raise ValueError(f'Ambiguous matrix for platform network {network}, frame 0x{frame_id:X}. Assign the network explicitly in Project settings.')
        if not candidates:
            unmatched.append(dict(network=network, frame_id=frame_id, signals=list(names)))
            continue
        spec, db, message = candidates[0]
        unknown = set(names) - {s.name for s in message.signals}
        if unknown:
            unmatched.append(dict(network=network, frame_id=frame_id, signals=sorted(unknown)))
            names = {name: index for name, index in names.items() if name not in unknown}
        bus = str(spec.get('bus', 'LIN' if Path(spec['path']).suffix.lower() == '.ldf' else 'CAN')).upper()
        channel = int(spec.get('channel', 0))
        if bus not in ('CAN', 'LIN') or not 1 <= channel <= 254:
            raise ValueError('Each candidate matrix needs a CAN/LIN bus and a BLF channel between 1 and 254.')
        key = f'{bus}:{channel}'
        if key in used_channels and used_channels[key] != spec['sha256']:
            raise ValueError(f'Different matrices assigned to {key}')
        used_channels[key] = spec['sha256']
        packed = all('TestData' in name and next(s for s in message.signals if s.name == name).length == 64 for name in names)
        decoded_message = message
        output_db = cantools.database.Database(messages=list(db.messages), strict=False)
        if telemetry and bus == 'CAN':
            telemetry_ids = {m.frame_id for m in telemetry.messages}
            overrides = {m.frame_id for m in db.messages if m.frame_id in telemetry_ids and m.signals and
                         all('TestData' in s.name and s.length == 64 for s in m.signals)}
            output_db = cantools.database.Database(messages=[m for m in db.messages if m.frame_id not in overrides] +
                [m for m in telemetry.messages if m.frame_id in overrides], strict=False)
        if packed and telemetry:
            matches = [m for m in telemetry.messages if m.frame_id == frame_id]
            if len(matches) == 1:
                decoded_message = matches[0]
        # Project DBCs may define unrelated attributes with conflicting names.
        # Embed the decoding contract (layout, conversion, choices), not those
        # attributes; otherwise merging two valid files can produce an invalid DBC.
        output_db = copy.deepcopy(output_db)
        from cantools.database.can.formats.dbc import DbcSpecifics
        for item in output_db.messages:
            item.dbc = DbcSpecifics()
            for signal in item.signals:
                signal.dbc = DbcSpecifics()
        text = output_db.as_dbc_string()
        cantools.database.load_string(text, database_format='dbc', strict=False)
        embedded[key] = text
        definitions = [dict(name=s.name, unit=s.unit or '', choices={str(float(k*s.scale+s.offset)): str(v) for k, v in (s.choices or {}).items()}) for s in decoded_message.signals]
        binding = dict(network=network, bus=bus, channel=channel, frame_id=frame_id,
                       message=decoded_message.name, matrix=Path(spec['path']).name,
                       matrix_sha256=spec['sha256'], mode='append' if packed else 'replace', signals=definitions)
        bindings.append(binding)
        bound.append((len(bindings)-1, binding, message, decoded_message, names, packed))
    if not bound:
        raise ValueError('No CSV signals match the configured project matrices.')
    records, frames, events = [], 0, 0
    with path.open(encoding=encoding, newline='') as stream:
        reader = csv.reader(stream)
        next(reader)
        for line, row in enumerate(reader, 2):
            if not any(cell.strip() for cell in row):
                continue
            if len(row) != len(headers):
                raise ValueError(f'CSV row {line}: column count differs from header')
            if not any(row[index].strip() for _, _, _, _, names, _ in bound for index in names.values()):
                continue
            stamp = _timestamp(row[headers.index(time_column)], settings)
            for number, binding, message, decoded_message, names, packed in bound:
                if not any(row[index].strip() for index in names.values()):
                    continue
                try:
                    observed = {s.name: _number(row[names[s.name]].strip(), s, packed) for s in message.signals if s.name in names}
                    available = {name: value for name, value in observed.items() if value is not None}
                    complete = len(available) == len(message.signals)
                    raw = {}
                    for signal in message.signals:
                        if packed:
                            raw[signal.name] = available.get(signal.name, 0)
                        elif signal.name not in available:
                            raw[signal.name] = 0
                        else:
                            value = int(round((available[signal.name] - signal.offset) / signal.scale))
                            low = -(1 << (signal.length-1)) if signal.is_signed else 0
                            high = (1 << (signal.length-int(signal.is_signed))) - 1
                            if not low <= value <= high:
                                complete = False
                                value = 0
                            raw[signal.name] = value
                    if message.is_multiplexed():
                        complete = False
                        payload = bytes(message.length)
                    else:
                        payload = message.encode(raw, scaling=False, strict=False)
                    if not packed:
                        # Do not silently quantise physical observations into a different value.
                        if complete:
                            roundtrip = message.decode(payload, decode_choices=False)
                            complete = all(math.isclose(roundtrip[name], value, abs_tol=1e-9, rel_tol=1e-12) for name, value in available.items())
                    if complete:
                        records.append((stamp, 'frame', number, payload))
                        frames += 1
                    if not packed or not complete:
                        if packed:
                            known = set().union(*(_bits(s) for s in message.signals if s.name in available))
                            values = {s.name: _decode_signal(s, payload) if _bits(s) <= known else None for s in decoded_message.signals}
                        else:
                            values = {s.name: available.get(s.name) for s in decoded_message.signals}
                        records.append((stamp, 'sample', number, values))
                        events += 1
                except (ValueError, OverflowError, can.CanError) as exc:
                    raise ValueError(f'CSV row {line}, network {binding["network"]}, frame 0x{message.frame_id:X}: {exc}') from exc
    if not records:
        raise ValueError('The matching CSV columns contain no observations.')
    records.sort(key=lambda row: row[0])
    origin = records[0][0]
    manifest = dict(kind='manifest', schema=1, source=path.name, source_sha256=sha256(path),
                    time_mode='wall' if settings.get('time_basis', 'wall') == 'wall' else 'utc',
                    origin=origin, end=records[-1][0], frame_count=frames, sample_records=events,
                    bindings=bindings, databases=embedded, unmatched=unmatched,
                    provenance='CSV reconstruction; not an original CAN/LIN bus capture. Unobserved reserved bits and wire events are unavailable.')
    signature = hashlib.sha256(json.dumps([manifest['source_sha256'], settings, embedded], sort_keys=True).encode()).hexdigest()[:16]
    destination = Path(destination) if destination else cache_directory() / f'{path.stem}.{signature}.blf'
    if destination.resolve() == path:
        raise ValueError('Output cannot overwrite source CSV')
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if read_metadata(destination)[0] == manifest:
            return str(destination)
        raise ValueError(f'Output already exists with different content: {destination}')
    progress(f'Cloud CSV: {len(bindings)} messages, {frames:,} reconstructed frames; {len(unmatched)} unmatched groups')
    fd, temporary = tempfile.mkstemp(prefix=destination.name + '.', suffix='.tmp', dir=destination.parent)
    os.close(fd)
    try:
        writer = CloudWriter(temporary)
        try:
            writer.marker(manifest, origin)
            for stamp, kind, number, data in records:
                binding = bindings[number]
                if kind == 'sample':
                    writer.marker(dict(kind='sample', binding=number, values=data), stamp)
                elif binding['bus'] == 'LIN':
                    writer.lin(stamp, binding['channel'], binding['frame_id'], data)
                else:
                    writer.on_message_received(can.Message(timestamp=stamp, arbitration_id=binding['frame_id'],
                        is_extended_id=binding['frame_id'] > 0x7ff, is_fd=len(data) > 8,
                        channel=binding['channel']-1, data=data))
        finally:
            writer.stop()
        # Publish only after the entire metadata stream can be reopened.
        if read_metadata(temporary)[0] != manifest:
            raise ValueError('BLF verification failed')
        os.replace(temporary, destination)
    finally:
        if Path(temporary).exists():
            Path(temporary).unlink()
    return str(destination)


class _NotCloud(Exception):
    pass


class _MarkerReader(can.BLFReader):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.found = False
        self.examined = 0

    def _parse_data(self, data):
        pos = 0
        while True:
            self._pos = pos
            try:
                pos = data.index(b'LOBJ', pos, pos + 8)
            except ValueError:
                if pos + 8 > len(data):
                    return
                raise blf.BLFParseError('Invalid object chain') from None
            _, header_size, version, size, kind = blf.OBJ_HEADER_BASE_STRUCT.unpack_from(data, pos)
            end = pos + size
            if size < header_size or header_size < 32:
                raise blf.BLFParseError('Invalid object size')
            if end > len(data):
                return
            flags, _, _, ticks = blf.OBJ_HEADER_V1_STRUCT.unpack_from(data, pos + 16) if version == 1 else blf.OBJ_HEADER_V2_STRUCT.unpack_from(data, pos + 16)
            stamp = ticks * (1e-5 if flags == blf.TIME_TEN_MICS else 1e-9) + self.start_timestamp
            if kind == blf.GLOBAL_MARKER and end - pos - header_size >= blf.GLOBAL_MARKER_STRUCT.size:
                body = pos + header_size
                fields = blf.GLOBAL_MARKER_STRUCT.unpack_from(data, body)
                length = fields[4]
                start = body + blf.GLOBAL_MARKER_STRUCT.size
                if length > end - start:
                    raise ValueError('Truncated BLF marker')
                text = data[start:start+length]
                if text.startswith(PREFIX.encode()):
                    record = json.loads(text[len(PREFIX):].decode('ascii'))
                    self.found = True
                    yield stamp, record
            self.examined += 1
            if not self.found and self.examined >= 128:
                raise _NotCloud()
            pos = end


def read_metadata(path):
    """Normal BLFs are probed for at most 128 objects, not read twice."""
    manifest, samples = None, []
    try:
        with _MarkerReader(path) as reader:
            for stamp, record in reader:
                if record.get('kind') == 'manifest':
                    if manifest is not None or record.get('schema') != 1:
                        raise ValueError('Invalid cloud BLF manifest')
                    manifest = record
                elif record.get('kind') == 'sample':
                    if manifest is None or not 0 <= record.get('binding', -1) < len(manifest['bindings']):
                        raise ValueError('Invalid cloud BLF sample binding')
                    samples.append((stamp, record))
    except _NotCloud:
        return None, []
    except (struct.error, blf.BLFParseError):
        if manifest is not None:
            raise ValueError('Truncated or corrupt cloud BLF') from None
        # Leave malformed ordinary BLFs to the existing reader diagnostics.
        return None, []
    if manifest and len(samples) != manifest['sample_records']:
        raise ValueError('Incomplete cloud BLF observations')
    return manifest, samples


def embedded_channels(manifest):
    from core.channel_config import ChannelConfig
    from core.bus_types import decode_key
    channels = {}
    for key, text in manifest['databases'].items():
        channel = decode_key(key)
        encoded = text.encode('utf-8-sig')
        digest = hashlib.sha256(encoded).hexdigest()
        path = cache_directory() / f'{digest}.dbc'
        if not path.exists() or sha256(path) != digest:
            path.write_bytes(encoded)
        channels[channel] = str(path)
    return ChannelConfig('BLF embedded matrix versions', channels)


def attach_metadata(store, manifest, samples):
    if not manifest:
        return
    from core.bus_types import BusType
    staged = {}
    for stamp, record in samples:
        number = record['binding']
        binding = manifest['bindings'][number]
        for signal in binding['signals']:
            value = record['values'].get(signal['name'])
            if value is not None and (not isinstance(value, (float, int)) or not math.isfinite(value)):
                raise ValueError('Invalid cloud BLF observation')
            staged.setdefault((number, signal['name']), []).append((stamp-store.base_ts, float('nan') if value is None else value))
    decoded = store.decoded_frames
    for number, binding in enumerate(manifest['bindings']):
        channel = (BusType(binding['bus']), binding['channel'])
        if binding['mode'] == 'replace':
            store.remove_message_series(channel, binding['message'])
        for signal in binding['signals']:
            rows = staged.get((number, signal['name']), [])
            if not rows:
                continue
            rows.sort(key=lambda row: row[0])
            choices = signal['choices']
            values = [row[1] for row in rows]
            labels = [choices.get(str(float(v)), v) if math.isfinite(v) else v for v in values] if choices else []
            store.add_series_bulk(channel=channel, message_name=binding['message'], message_id=binding['frame_id'],
                signal_name=signal['name'], unit=signal['unit'], timestamps=np.array([row[0] for row in rows]),
                values=np.array(values), raw_values=labels, has_labels=bool(choices))
    store.decoded_frames = decoded
    store.sort_merged_series()
    store.cloud_manifest = manifest
    store.supplemental_time_bounds = (manifest['origin']-store.base_ts, manifest['end']-store.base_ts)
    store.diagnostics_text += '\n\nCLOUD CSV RECONSTRUCTION\n' + manifest['provenance'] + '\n' + '\n'.join(
        f"Platform network {b['network']} -> {b['bus']} {b['channel']}: {b['message']} (0x{b['frame_id']:X}), {b['matrix']}, SHA256={b['matrix_sha256']}" for b in manifest['bindings'])
    if manifest['unmatched']:
        store.diagnostics_text += '\nUNMATCHED CSV GROUPS\n' + json.dumps(manifest['unmatched'], ensure_ascii=False)
