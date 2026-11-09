# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Local, version-scoped references for an Osvanta project Agent."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import re
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

import numpy as np


KINDS = ('c', 'a2l', 'dbc', 'requirement')
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_MATCHES = 4
MAX_LOOKUPS = 8
_FILENAME_VERSION = re.compile(r'(?<![A-Za-z0-9])V\d{4}(?![A-Za-z0-9])', re.I)
_TOKEN = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,39}\Z')
_ABSOLUTE_PATH = re.compile(r'(?i)(?:[a-z]:[\\/]|\\\\)[^\r\n"<>|]+|(?<!\w)/(?:[^/\s]+/)+[^\s"\x27]+')
_VERSION_LABELS = {
    'measurement': '日志版本信号',
    'manual': '手动指定的参考版本',
    'filename_hint': '文件名候选，实车版本未核实',
    'default_hint': 'Agent 默认参考，实车版本未核实',
    'unsupported': '版本未登记，未选用其他版本的代码',
    'conflicting': '日志版本线索不一致，未选用版本代码',
    'unknown': '未确认版本，未选用版本代码',
}


@dataclass(frozen=True)
class Reference:
    kind: str
    path: Path
    member: str = ''
    digest: str = ''

    @property
    def name(self) -> str:
        return PurePosixPath(self.member).name if self.member else self.path.name


@dataclass(frozen=True)
class ProjectAgent:
    name: str
    releases: dict[str, tuple[Reference, ...]]
    default_version: str = ''
    references: tuple[Reference, ...] = ()
    version_signal: str = ''
    version_values: dict[int, str] | None = None


@dataclass(frozen=True)
class BoundProject:
    name: str
    version: str
    basis: str
    release_references: tuple[Reference, ...]
    project_references: tuple[Reference, ...]

    @property
    def version_label(self) -> str:
        return f'{self.version or "未知"}（{_VERSION_LABELS[self.basis]}）'

    def model_context(self) -> dict:
        return {
            'project': self.name,
            'version': self.version or None,
            'version_basis': _VERSION_LABELS[self.basis],
            'release_kinds': sorted({ref.kind for ref in self.release_references}),
            'project_reference_kinds': sorted({ref.kind for ref in self.project_references}),
        }


@dataclass(frozen=True)
class ReferenceEvidence:
    kind: str
    citation: str
    excerpt: str
    local_locator: str
    evidence_id: str = ''

    @property
    def text(self) -> str:
        return f'{self.citation}\n{self.excerpt}'


def _token(value) -> str:
    if not isinstance(value, str) or not _TOKEN.fullmatch(value):
        raise ValueError('项目或版本标识格式错误。')
    return value


def _references(rows) -> tuple[Reference, ...]:
    if not isinstance(rows, list) or len(rows) > 16:
        raise ValueError('每组资料需为不超过 16 项的列表。')
    out = []
    for row in rows:
        if not isinstance(row, dict) or row.get('kind') not in KINDS:
            raise ValueError('资料类型不支持。')
        if not isinstance(row.get('path'), str) or not Path(row['path']).is_absolute():
            raise ValueError('资料必须登记本地绝对路径。')
        member = row.get('member', '')
        if not isinstance(member, str) or (member and (
                '\\' in member or ':' in member or PurePosixPath(member).is_absolute()
                or '..' in PurePosixPath(member).parts
                or Path(row['path']).suffix.lower() != '.zip')):
            raise ValueError('压缩包成员必须是登记的相对文件。')
        extension = (PurePosixPath(member).suffix if member else Path(row['path']).suffix).lower()
        expected = {'c': {'.c', '.h'}, 'dbc': {'.dbc'}, 'a2l': {'.a2l'},
                    'requirement': {'.docx', '.md', '.txt'}}[row['kind']]
        if extension not in expected:
            raise ValueError('资料文件后缀与登记类型不符。')
        digest = row.get('sha256', '')
        if not isinstance(digest, str) or (digest and not re.fullmatch(r'[0-9a-f]{64}', digest)):
            raise ValueError('资料 SHA-256 格式错误。')
        out.append(Reference(row['kind'], Path(row['path']), member, digest))
    return tuple(out)


def load_catalog(path: Path) -> dict[str, ProjectAgent]:
    if not path.is_file():
        return {}
    with path.open('rb') as stream:
        raw = stream.read(256_001)
    if len(raw) > 256_000:
        raise ValueError('项目 Agent 配置过大。')
    payload = json.loads(raw)
    rows = payload.get('agents') if isinstance(payload, dict) else None
    if not isinstance(rows, list) or len(rows) > 24:
        raise ValueError('项目 Agent 配置需要 agents 列表。')
    agents = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('releases'), dict):
            raise ValueError('项目 Agent 缺少版本资料。')
        name = _token(row.get('name'))
        releases = {_token(version): _references(sources)
                    for version, sources in row['releases'].items()}
        if name in agents:
            raise ValueError('项目 Agent 名称重复。')
        default = row.get('default_version', '')
        if not isinstance(default, str) or (default and default not in releases):
            raise ValueError('默认版本未登记。')
        signal = row.get('version_signal', '')
        values = row.get('version_values', {})
        if not isinstance(signal, str) or not isinstance(values, dict) or (values and not signal):
            raise ValueError('版本信号配置错误。')
        agents[name] = ProjectAgent(
            name, releases, default, _references(row.get('references', [])),
            signal, {int(code): _token(version) for code, version in values.items()},
        )
    return agents


def _logged_version(agent: ProjectAgent, store) -> tuple[str, str]:
    if not agent.version_signal or store is None:
        return '', ''
    series = store.get_series(agent.version_signal)
    if series is None:
        return '', ''
    values = series.numpy_values()
    finite = values[np.isfinite(values)]
    if not len(finite):
        return '', ''
    first = float(finite[0])
    if not np.all(finite == first):
        return '', 'conflicting'
    if not first.is_integer():
        return '', 'unsupported'
    return (agent.version_values or {}).get(int(first), ''), 'measurement'


def bind_project(agent: ProjectAgent, store, measurement_path, manual_version='') -> BoundProject:
    version, basis = _logged_version(agent, store)
    if basis == 'measurement':
        if version not in agent.releases:
            basis = 'unsupported'
        elif manual_version and manual_version != version:
            version, basis = '', 'conflicting'
    elif not basis:
        if manual_version:
            version = manual_version
            basis = 'manual' if version in agent.releases else 'unsupported'
        else:
            # The file name is a hint. It cannot establish ECU software identity.
            name = Path(measurement_path).name if measurement_path else ''
            hints = {match.upper() for match in _FILENAME_VERSION.findall(name)}
            if len(hints) > 1:
                basis = 'conflicting'
            elif hints:
                version = hints.pop()
                basis = 'filename_hint' if version in agent.releases else 'unsupported'
            else:
                version = agent.default_version
                basis = 'default_hint' if version else 'unknown'
    sources = agent.releases.get(version, ()) if basis not in ('unsupported', 'conflicting') else ()
    return BoundProject(agent.name, version, basis, sources, agent.references)


def _read_bounded(stream) -> bytes:
    data = stream.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError('资料文件超过大小上限。')
    return data


def _lines(ref: Reference) -> list[str]:
    with ref.path.open('rb') as stream:
        raw = _read_bounded(stream)
    if ref.digest and sha256(raw).hexdigest() != ref.digest:
        raise ValueError('资料指纹已变。')
    if ref.member or ref.path.suffix.lower() == '.docx':
        with ZipFile(BytesIO(raw)) as archive:
            with archive.open(ref.member or 'word/document.xml') as stream:
                data = _read_bounded(stream)
        if not ref.member:
            root = ElementTree.fromstring(data)
            word = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
            return [''.join(node.text or '' for node in paragraph.iter(word + 't'))
                    for paragraph in root.iter(word + 'p')]
    else:
        data = raw
    try:
        return data.decode('utf-8-sig').splitlines()
    except UnicodeDecodeError:
        return data.decode('gb18030', errors='replace').splitlines()


def lookup(project: BoundProject, kind: str, query: str) -> tuple[list[ReferenceEvidence], list[str]]:
    if kind not in KINDS or not isinstance(query, str) or not 2 <= len(query.strip()) <= 80:
        raise ValueError('请选择资料类型并提供 2 至 80 字符的信号、函数或需求关键词。')
    needle = query.strip().casefold()
    if not any(char.isalnum() for char in needle):
        raise ValueError('关键词需包含文字或数字。')
    hits = []
    unavailable = []
    for refs, label in ((project.release_references, project.version_label),
                        (project.project_references, '项目级参考，与实车软件版本未绑定')):
        for ref in refs:
            if ref.kind != kind:
                continue
            try:
                lines = _lines(ref)
            except (OSError, ValueError, KeyError, BadZipFile, ElementTree.ParseError):
                unavailable.append(ref.name)
                continue
            paragraph = ref.path.suffix.lower() == '.docx'
            for i, line in enumerate(lines):
                pos = line.casefold().find(needle)
                if pos < 0:
                    continue
                # A short context around the literal match, not the full source.
                start = max(i - 1, 0)
                end = min(i + 2, len(lines))
                context = []
                for index in range(start, end):
                    text = lines[index].strip()
                    if index == i and len(text) > 160:
                        match = text.casefold().find(needle)
                        text = text[max(0, match - 52):max(0, match - 52) + 160]
                    else:
                        text = text[:120]
                    context.append(f'{index + 1}: {_ABSOLUTE_PATH.sub("[路径省略]", text)}')
                unit = '段' if paragraph else '行'
                locator = str(ref.path)
                if ref.member:
                    locator += '!/' + ref.member
                hits.append(ReferenceEvidence(
                    kind, f'{project.name} {label} · {ref.name} {unit} {i + 1}',
                    '\n'.join(context), f'{locator}:{i + 1}'))
                if len(hits) >= MAX_MATCHES:
                    return hits, unavailable
    return hits, unavailable

