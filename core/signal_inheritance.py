"""Data-free signal identities and conservative matching between recordings."""
from pathlib import Path
from collections import Counter

from core.bus_types import store_key_prefix


def recording_family(path):
    return 'mdf' if Path(path or '').suffix.lower() in {'.mf4', '.mdf'} else 'bus'


def identity(key, series=None):
    parts = key.split('::')
    return {
        'key': key,
        'source': store_key_prefix(series.channel) if series is not None else parts[0],
        'name': series.signal_name if series is not None else parts[-1],
        'unit': (series.unit or '') if series is not None else None,
    }


def compatible_source(left, right):
    # An unknown source carries no evidence of a conflict. Explicit bus and
    # channel identities must agree, including CAN versus LIN.
    return left == right or left in {'CH?', ''} or right in {'CH?', ''}


def match_signals(entries, store):
    """Return (new key, reason) per entry; never retain previous sample buffers.

    Full keys win, but changed units still require confirmation. A name-only
    fallback must be unique, with the same unit and compatible source.
    """
    available = {key: identity(key, store.get_series(key)) for key in store.all_keys()}
    by_name = {}
    for key, spec in available.items():
        by_name.setdefault(spec['name'], []).append(key)
    results = []
    for entry in entries:
        spec = entry['identity']
        exact = available.get(spec['key'])
        if exact and (spec['unit'] is None or exact['unit'] == spec['unit']):
            results.append((spec['key'], ''))
            continue
        candidates = by_name.get(spec['name'], [])
        if len(candidates) == 1:
            key = candidates[0]
            candidate = available[key]
            if (spec['unit'] is not None and candidate['unit'] == spec['unit']
                    and compatible_source(spec['source'], candidate['source'])):
                results.append((key, ''))
                continue
        reason = '待确认：同名信号、单位或来源存在差异' if candidates else '本日志未匹配'
        results.append((None, reason))
    # Do not silently merge two different inherited rows onto one signal.
    targets = Counter(key for key, _ in results if key)
    return [(None, '待确认：多个继承信号指向同一信号') if key and targets[key] > 1
            else (key, reason) for key, reason in results]
