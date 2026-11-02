"""Data-free signal identities and exact-name matching between recordings."""
from pathlib import Path

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


def match_signals(entries, store):
    """Return (new key, reason) per entry; never retain previous sample buffers.

    Only the complete signal name controls eligibility, including case and
    whitespace. Units and sources are descriptive, not matching constraints.
    Among same-name channels keep the existing key when possible, otherwise
    use the first channel in the new recording's stable order.
    """
    available = {key: identity(key, store.get_series(key)) for key in store.all_keys()}
    by_name = {}
    for key, spec in available.items():
        by_name.setdefault(spec['name'], []).append(key)
    results = []
    for entry in entries:
        spec = entry['identity']
        candidates = by_name.get(spec['name'], [])
        if not candidates:
            results.append((None, '本日志未匹配'))
        else:
            key = spec['key'] if spec['key'] in candidates else candidates[0]
            results.append((key, ''))
    return results
