"""Reusable project settings, separate from a measurement session."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

PROJECT_KIND = 'osvanta.project'
PROJECT_SCHEMA = 1


def normalise_project(value=None, folder=None):
    value = copy.deepcopy({} if value is None else value)
    if not isinstance(value, dict):
        raise ValueError('Project settings must be an object')
    value['name'] = str(value.get('name', '')).strip()
    if 'analysis_templates' in value:
        from core.analysis_templates import normalise_library
        value['analysis_templates'] = normalise_library(value['analysis_templates'])
    if value.get('log_root') and folder:
        value['log_root'] = _path(value['log_root'], folder, False)
    cloud = value.get('cloud_import', {})
    if not isinstance(cloud, dict) or not isinstance(cloud.get('matrices', []), list):
        raise ValueError('Cloud import matrices must be a list')
    for spec in cloud.get('matrices', []):
        if not isinstance(spec, dict) or not spec.get('path'):
            raise ValueError('Each candidate matrix needs a path')
        if folder:
            spec['path'] = _path(spec['path'], folder, False)
    if cloud.get('telemetry_database') and folder:
        cloud['telemetry_database'] = _path(cloud['telemetry_database'], folder, False)
    sources = value.setdefault('csv_sources', [])
    if not isinstance(sources, list):
        raise ValueError('CSV sources must be a list')
    for source in sources:
        if not isinstance(source, dict) or not str(source.get('path', '')).strip():
            raise ValueError('Each CSV source needs a file path')
        path = str(source['path'])
        if folder and '{measurement_stem}' not in path and not Path(path).is_absolute():
            source['path'] = str((Path(folder) / path).resolve())
    return value


def _path(value, folder, relative):
    if not value or '{measurement_stem}' in value:
        return value
    if relative:
        if not Path(value).is_absolute():
            return value
        try:
            return os.path.relpath(value, folder)
        except ValueError:  # another Windows drive
            return value
    return str((Path(folder) / value).resolve()) if not Path(value).is_absolute() else value


def save_project(path, project, channel_config, playback):
    path = Path(path)
    project = normalise_project(project)
    if project.get('log_root'):
        project['log_root'] = _path(project['log_root'], path.parent, True)
    cloud = project.get('cloud_import', {})
    from core.cloud_blf import sha256
    for spec in cloud.get('matrices', []):
        digest = sha256(spec['path'])
        if spec.get('sha256_locked') and spec['sha256_locked'] != digest:
            raise ValueError('Matrix changed; review its version in Project settings before saving.')
        spec['sha256_locked'] = digest
        spec['path'] = _path(spec['path'], path.parent, True)
    if cloud.get('telemetry_database'):
        digest = sha256(cloud['telemetry_database'])
        if cloud.get('telemetry_sha256_locked') and cloud['telemetry_sha256_locked'] != digest:
            raise ValueError('Telemetry DBC changed; review its version in Project settings before saving.')
        cloud['telemetry_sha256_locked'] = digest
        cloud['telemetry_database'] = _path(cloud['telemetry_database'], path.parent, True)
    for source in project['csv_sources']:
        source['path'] = _path(source['path'], path.parent, True)
    channels = copy.deepcopy(channel_config)
    channels['channels'] = {k: _path(v, path.parent, True) for k, v in channels.get('channels', {}).items()}
    defaults = copy.deepcopy(playback)
    defaults['position'] = 0.0
    data = {'kind': PROJECT_KIND, 'schema_version': PROJECT_SCHEMA, 'project': project,
            'channel_config': channels, 'playback': defaults}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    return data


def load_project(path):
    path = Path(path)
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict):
        raise ValueError('Project profile must be an object')
    if data.get('kind') != PROJECT_KIND or data.get('schema_version') != PROJECT_SCHEMA:
        raise ValueError('Unsupported Osvanta project format or schema version')
    data['project'] = normalise_project(data.get('project'), path.parent)
    channels = data.get('channel_config', {})
    channels['channels'] = {k: _path(v, path.parent, False) for k, v in channels.get('channels', {}).items()}
    data['channel_config'] = channels
    return data
