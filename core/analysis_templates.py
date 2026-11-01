"""Project-scoped analysis recipes. Templates contain no measurement data."""
import copy
import json
import math
import os
import re
from pathlib import Path
from core.plot_style import normalise_line_width

KIND = 'osvanta.analysis_template'
STYLE_FIELDS = ('color', 'visible', 'group', 'axis_visible', 'own_axis', 'multistack_id', 'line_style')
VIEW_FIELDS = ('plot_type', 'step_lines', 'show_data_points', 'hide_plot_lines',
               'plot_line_width',
               'plot_background_color', 'group_colors', 'name_show_channel', 'name_show_message', 'y_ranges')


def _color(value):
    return isinstance(value, str) and re.fullmatch(r'#[a-fA-F0-9]{6}(?:[a-fA-F0-9]{2})?', value)


def _identity(value):
    if not isinstance(value, dict) or any(not isinstance(value.get(key), str) for key in ('key', 'source', 'name')):
        raise ValueError('Invalid template signal identity')
    if value.get('unit') is not None and not isinstance(value['unit'], str):
        raise ValueError('Invalid template unit')
    return {key: value.get(key) for key in ('key', 'source', 'name', 'unit')}


def normalise_windows(windows):
    if not isinstance(windows, list):
        raise ValueError('Window layout must be a list')
    result, ids, assigned = [], set(), set()
    for window in windows:
        if not isinstance(window, dict):
            raise ValueError('Invalid window')
        identifier = window.get('id')
        if not isinstance(identifier, int) or identifier < 0 or identifier in ids:
            raise ValueError('Window identities must be unique nonnegative integers')
        ids.add(identifier)
        if window.get('axis_mode') not in {'shared', 'individual'} or not isinstance(window.get('name'), str):
            raise ValueError('Invalid window name or axis mode')
        height = window.get('height', 220)
        if not isinstance(height, (int, float)) or not math.isfinite(height) or not 120 <= height <= 1600:
            raise ValueError('Invalid window height')
        keys = window.get('keys', [])
        if not isinstance(keys, list) or any(not isinstance(key, str) or key in assigned for key in keys) or len(keys) != len(set(keys)):
            raise ValueError('A plotted signal must belong to one window')
        assigned.update(keys)
        ranges = {}
        for key, bounds in window.get('y_ranges', {}).items():
            if not isinstance(bounds, list) or len(bounds) != 2 or any(not isinstance(n, (int, float)) or not math.isfinite(n) for n in bounds) or bounds[0] >= bounds[1]:
                raise ValueError('Invalid template Y range')
            if key in keys:
                ranges[key] = list(bounds)
        result.append(dict(id=identifier, name=window['name'], axis_mode=window['axis_mode'],
                           height=int(height), keys=list(keys), y_ranges=ranges))
    return result


def normalise_template(value):
    if not isinstance(value, dict) or value.get('kind') != KIND or value.get('schema_version') != 1:
        raise ValueError('Unsupported analysis template')
    presentation = value.get('presentation', {})
    if not isinstance(presentation, dict) or not isinstance(value.get('inheritance', []), list) or not isinstance(value.get('dependencies', {}), dict) or not isinstance(value.get('generated_signals', []), list):
        raise ValueError('Invalid analysis template structure')
    mode = presentation.get('plot_type', 'normal')
    if mode not in {'normal', 'stacked', 'multi_axis', 'multistack', 'windows'}:
        raise ValueError('Invalid template plot mode')
    view = {key: copy.deepcopy(presentation[key]) for key in VIEW_FIELDS if key in presentation}
    view['plot_type'] = mode
    if view.get('plot_line_width') is not None and normalise_line_width(view['plot_line_width']) is None:
        raise ValueError('Invalid template line width')
    for flag in ('step_lines', 'show_data_points', 'hide_plot_lines', 'name_show_channel', 'name_show_message'):
        if flag in view and not isinstance(view[flag], bool):
            raise ValueError('Invalid template display flag')
    if 'plot_background_color' in view and not _color(view['plot_background_color']):
        raise ValueError('Invalid template background')
    if not isinstance(view.get('group_colors', {}), dict) or any(not _color(color) for color in view.get('group_colors', {}).values()):
        raise ValueError('Invalid template group colors')
    if not isinstance(view.get('y_ranges', {}), dict):
        raise ValueError('Invalid template Y ranges')
    for bounds in view.get('y_ranges', {}).values():
        if not isinstance(bounds, list) or len(bounds) != 2 or any(not isinstance(n, (int, float)) or not math.isfinite(n) for n in bounds) or bounds[0] >= bounds[1]:
            raise ValueError('Invalid template Y range')
    if mode == 'windows':
        view['window_layout'] = normalise_windows(presentation.get('window_layout', []))
    entries = []
    seen = set()
    for entry in value.get('inheritance', []):
        if not isinstance(entry, dict):
            raise ValueError('Invalid template signal')
        key = entry.get('key')
        if not isinstance(key, str) or not key or key in seen:
            raise ValueError('Invalid or duplicate template signal')
        seen.add(key)
        clean = {field: copy.deepcopy(entry[field]) for field in STYLE_FIELDS if field in entry}
        if 'color' in clean and not _color(clean['color']):
            raise ValueError('Invalid template signal color')
        for flag in ('visible', 'axis_visible', 'own_axis'):
            if flag in clean and not isinstance(clean[flag], bool):
                raise ValueError('Invalid template signal flag')
        if any(field in clean and not isinstance(clean[field], str) for field in ('group', 'line_style')):
            raise ValueError('Invalid template signal style')
        if 'multistack_id' in clean and not isinstance(clean['multistack_id'], int):
            raise ValueError('Invalid template stack identity')
        clean.update(key=key, identity=_identity(entry['identity']), plotted=True, selected=False)
        entries.append(clean)
    dependencies = {key: _identity(spec) for key, spec in value.get('dependencies', {}).items()}
    definitions = []
    for definition in value.get('generated_signals', []):
        if not isinstance(definition, dict):
            raise ValueError('Invalid calculated signal definition')
        if any(not isinstance(definition.get(key, ''), str) for key in ('name', 'formula', 'unit')):
            raise ValueError('Invalid template calculated signal')
        definitions.append({key: definition.get(key, '') for key in ('name', 'formula', 'unit')})
    return dict(kind=KIND, schema_version=1, presentation=view, inheritance=entries,
                dependencies=dependencies, generated_signals=definitions)


def normalise_library(value):
    if not isinstance(value, dict) or any(not isinstance(name, str) or not name.strip() for name in value):
        raise ValueError('Analysis templates must be a named collection')
    return {name: normalise_template(template) for name, template in value.items()}


def write_library(profile, library, expected=None):
    """Only update this profile's template collection, preserving its paths."""
    path = Path(profile)
    original = path.read_bytes()
    data = json.loads(original.decode('utf-8-sig'))
    if data.get('kind') != 'osvanta.project' or data.get('schema_version') != 1:
        raise ValueError('Not an Osvanta project profile')
    current = normalise_library(data['project'].get('analysis_templates', {}))
    if expected is not None and current != normalise_library(expected):
        raise ValueError('工程模板已被其他窗口修改，请重新打开工程后再保存。')
    data['project']['analysis_templates'] = normalise_library(library)
    temporary = path.with_name(path.name + '.templates.tmp')
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        if path.read_bytes() != original:
            raise ValueError('工程文件在保存期间被修改，请重新打开工程。')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
