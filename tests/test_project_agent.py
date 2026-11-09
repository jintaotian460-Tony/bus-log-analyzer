# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from hashlib import sha256
import json
from types import SimpleNamespace
from zipfile import ZipFile

import numpy as np
import pytest

from core.assistant_analysis import AnalysisContext, capture_snapshot
from core.assistant_tools import AnalysisTools
from core.project_agent import bind_project, load_catalog
from tests.test_assistant import ENGS_KEY, _step_store


@pytest.fixture
def project_home(tmp_path):
    releases = {}
    for version, level in (('V1335', 35), ('V1336', 36)):
        archive = tmp_path / f'{version}.zip'
        with ZipFile(archive, 'w') as package:
            package.writestr('climate.c', f'if (ProtectionOK) {{\n    DemoMotorCmd = {level};\n}}\n')
        releases[version] = [{
            'kind': 'c', 'path': str(archive), 'member': 'climate.c',
            'sha256': sha256(archive.read_bytes()).hexdigest(),
        }]
    dbc = tmp_path / 'iface.dbc'
    dbc.write_text('SG_ DemoMotorCmd : 0|8@1+ (1,0) [0|100] "%" ECU\n')
    profile = {
        'name': 'E702', 'default_version': 'V1336', 'releases': releases,
        'references': [{'kind': 'dbc', 'path': str(dbc)}],
        'version_signal': 'CH?::CSV::RegisteredVersion',
        'version_values': {'1335': 'V1335', '1336': 'V1336'},
    }
    (tmp_path/'assistant-project-agents.json').write_text(
        json.dumps({'agents': [profile]}), encoding='utf-8')
    return tmp_path


def _agent(project_home):
    return load_catalog(project_home/'assistant-project-agents.json')['E702']


def _version_store(*values):
    series = SimpleNamespace(numpy_values=lambda: np.asarray(values, dtype=float))
    return SimpleNamespace(get_series=lambda key: series)


def _analysis_tools(project):
    snapshot = capture_snapshot(
        _step_store(), AnalysisContext('private-log.blf', (ENGS_KEY,), 0, 1, 'test'))
    return AnalysisTools(snapshot, project)


def test_version_signal_beats_filename_and_conflict_never_falls_back(project_home):
    agent = _agent(project_home)
    actual = bind_project(agent, _version_store(1335, 1335), 'car_V1336.mf4')
    assert actual.basis == 'measurement' and actual.version == 'V1335'
    conflict = bind_project(agent, _version_store(1335, 1336), 'car_V1336.mf4')
    assert conflict.basis == 'conflicting' and not conflict.release_references
    manual_conflict = bind_project(agent, _version_store(1335), 'car.blf', 'V1336')
    assert not manual_conflict.release_references
    filename = bind_project(agent, None, 'car_V1335.mf4')
    assert filename.basis == 'filename_hint' and '未核实' in filename.version_label
    assert bind_project(agent, None, 'car.mf4').basis == 'default_hint'
    assert bind_project(agent, None, 'car.mf4', 'V1335').basis == 'manual'
    unsupported = bind_project(agent, None, 'car_V1310.mf4')
    assert unsupported.version == 'V1310' and not unsupported.release_references


def test_lookup_is_bound_to_one_release_and_cites_project_level_dbc_separately(project_home):
    tools = _analysis_tools(bind_project(_agent(project_home), None, 'car_V1335.mf4'))
    rows = tools.call('osvanta_lookup_reference', {'kind': 'c', 'query': 'DemoMotorCmd'})
    payload = json.dumps(rows, ensure_ascii=False)
    assert 'DemoMotorCmd = 35' in payload and 'DemoMotorCmd = 36' not in payload
    assert 'private-log' not in payload and 'car_V1335' not in payload
    assert str(project_home) not in payload
    citation, = tools.resolve_evidence([rows['matches'][0]['id']])
    assert citation.local_locator.endswith('V1335.zip!/climate.c:2')
    assert '文件名候选' in citation.citation
    dbc = tools.call('osvanta_lookup_reference', {'kind': 'dbc', 'query': 'DemoMotorCmd'})
    assert '项目级参考' in dbc['matches'][0]['citation']
    with pytest.raises(ValueError, match='只接受'):
        tools.call('osvanta_lookup_reference', {
            'kind': 'c', 'query': 'DemoMotorCmd', 'version': 'V1336',
        })
    with pytest.raises(ValueError, match='未发行'):
        tools.resolve_evidence(['src-99'])


def test_lookup_rejects_changed_source_and_unbound_tool(project_home):
    agent = _agent(project_home)
    tools = _analysis_tools(bind_project(agent, None, 'car_V1335.mf4'))
    with ZipFile(project_home/'V1335.zip', 'w') as archive:
        archive.writestr('climate.c', 'DemoMotorCmd = 99;')
    result = tools.call('osvanta_lookup_reference', {'kind': 'c', 'query': 'DemoMotorCmd'})
    assert result['matches'] == [] and result['unavailable_sources'] == ['climate.c']
    unbound = _analysis_tools(None)
    assert all(s['name'] != 'osvanta_lookup_reference' for s in unbound.specs())
    with pytest.raises(ValueError, match='未授权'):
        unbound.call('osvanta_lookup_reference', {'kind': 'c', 'query': 'DemoMotorCmd'})
