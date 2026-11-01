"""Restore selections and plot presentation using only the new recording."""
import array
from copy import deepcopy

from PySide6.QtWidgets import QInputDialog, QMessageBox

from core.signal_inheritance import identity, match_signals
from core.signal_store import SignalSeries
from core.calculated_signals import formula_references, TIME_SIGNAL_KEY

STYLE_FIELDS = ('color', 'visible', 'group', 'axis_visible', 'own_axis',
                'multistack_id', 'line_style')


def capture(window, config):
    selected = window.signal_tree.selected_signal_keys()
    plotted = window.plot_panel.plotted_keys()
    old = {entry['key']: entry for entry in getattr(window, '_inherited_signals', [])}
    entries = []
    styles = {entry['key']: entry for entry in (config or {}).get('signals', [])}
    for key in dict.fromkeys(plotted + selected):
        item = window.plot_panel._items.get(key)
        series = item.series if item else (window.store.get_series(key) if window.store else None)
        spec = (old[key]['identity'] if key in old and (series is None or (item and item.unavailable_reason))
                else identity(key, series))
        entry = dict(styles.get(key, {'key': key}))
        entry.update(identity=deepcopy(spec), plotted=key in plotted, selected=key in selected)
        entries.append(entry)
    if not entries:
        return None
    config = dict(config or {'plot_type': 'normal', 'signals': []})
    config['inheritance'] = entries
    dependencies = {}
    pending = [key for key in plotted if window.calculated_signals.contains_key(key)]
    seen = set()
    previous = getattr(window, '_inherited_dependencies', {})
    while pending:
        key = pending.pop()
        if key in seen or key == TIME_SIGNAL_KEY:
            continue
        seen.add(key)
        definition = window.calculated_signals.definition(key)
        if definition:
            pending.extend(formula_references(definition.formula))
        elif window.store is not None:
            bound = getattr(window, '_inherited_source_bindings', {}).get(key, key)
            series = window.store.get_series(bound) if bound else None
            dependencies[key] = identity(bound, series) if series is not None else previous.get(key, identity(key))
    config['dependencies'] = dependencies
    return config


def restore(window, config):
    entries = deepcopy(config['inheritance'])
    original_keys = [entry['key'] for entry in entries]
    raw_entries = [entry for entry in entries if not window.calculated_signals.contains_key(entry['key'])]
    matches = iter(match_signals(raw_entries, window.store))
    unavailable = {}
    selected = []
    generated = []
    window._inherited_dependencies = deepcopy(config.get('dependencies', {}))
    refs = list(window._inherited_dependencies)
    matched_refs = match_signals([{'identity': window._inherited_dependencies[k]} for k in refs], window.store)
    window._inherited_source_bindings = {ref: key for ref, (key, _) in zip(refs, matched_refs)}
    window._inherited_signals = entries
    window.plot_panel.begin_batch_add()
    try:
        for entry in entries:
            if window.calculated_signals.contains_key(entry['key']):
                key, reason = entry['key'], '正在重新计算'
                if entry['plotted']:
                    generated.append(key)
            else:
                key, reason = next(matches)
            if key:
                entry['key'] = key
                if not window.calculated_signals.contains_key(key):
                    entry['identity'] = identity(key, window.store.get_series(key))
            else:
                key = entry['key']
                unavailable[key] = (entry['identity']['name'], reason)
            if entry['selected']:
                selected.append(key)
            if not entry['plotted']:
                continue
            if reason:
                spec = entry['identity']
                series = SignalSeries(channel=None, message_name=spec['key'].split('::')[-2],
                                      message_id=0, signal_name=spec['name'], unit=spec['unit'] or '',
                                      timestamps=array.array('d'), values=array.array('d'))
                window.plot_panel.add_series(key, series, color=entry.get('color'))
                window.plot_panel._items[key].unavailable_reason = reason
            elif not window.calculated_signals.contains_key(key):
                window.plot_panel.add_series(key, window.store.get_series(key), color=entry.get('color'))
            item = window.plot_panel._items.get(key)
            if item:
                for field in STYLE_FIELDS:
                    if field in entry:
                        setattr(item, field, entry[field])
    finally:
        window.plot_panel.end_batch_add()
    window.signal_tree.set_unavailable_signals(unavailable)
    window.signal_tree.select_signal_keys(selected)
    if config.get('window_layout') and window.plot_panel._windows_mode:
        mapping = dict(zip(original_keys, [entry['key'] for entry in entries]))
        window.plot_panel.apply_window_layout(config['window_layout'], mapping)
    for key in generated:
        # The placeholder already owns its saved window. Queue recomputation
        # directly rather than routing it as a user drag into the active one.
        definition = window.calculated_signals.definition(key)
        window._queue_calculation(definition, 'lazy', plot_after=True)
    missing = len(unavailable)
    window._log(f'Signal inheritance: {len(entries) - missing} matched, {missing} unavailable / pending confirmation.')
    return dict(zip(original_keys, [entry['key'] for entry in entries]))


def confirm_mapping(window, key):
    if window.store is None:
        return
    config = capture(window, window._capture_temporary_plot_configuration())
    if not config:
        return
    entry = next((e for e in config['inheritance'] if e['key'] == key), None)
    if entry is None:
        return
    keys = window.store.all_keys()
    keys.sort(key=lambda k: (window.store.get_series(k).signal_name != entry['identity']['name'], k))
    labels = [f'{k}  [{window.store.get_series(k).unit or "无单位"}]' for k in keys]
    label, ok = QInputDialog.getItem(window, '确认信号匹配',
                                   f'{key}\n选择本日志中的信号（来源和单位以此选择为准）：', labels, 0, False)
    if not ok or label not in labels:
        return
    target = keys[labels.index(label)]
    if any(e is not entry and e['key'] == target for e in config['inheritance']):
        QMessageBox.information(window, '信号已存在', '目标信号已在继承列表中，请先移除重复条目。')
        return
    entry['identity'] = identity(target, window.store.get_series(target))
    entry['key'] = target
    for layout in config.get('window_layout', []):
        layout['keys'] = [target if original == key else original for original in layout['keys']]
        if key in layout.get('y_ranges', {}):
            layout['y_ranges'][target] = layout['y_ranges'].pop(key)
    for reference, spec in config.get('dependencies', {}).items():
        if spec['key'] == key:
            config['dependencies'][reference] = identity(target, window.store.get_series(target))
    window.plot_panel.clear_all()
    restore(window, config)
