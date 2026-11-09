# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Create and read native Codex chats. All model turns are run in Desktop."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID
import json
import os
import re
import subprocess
import sys
import tomllib

from core.codex_bridge import BridgeError, Cancelled, JsonRpc, StdioTransport

MCP_NAME = 'osvanta_capture'
VERIFIED_DESKTOP_VERSION = 'codex-cli 0.167.0-alpha.3.2'
TITLE = 'Osvanta 日志分析'
MAX_QUESTION = 8_000
EVIDENCE_ID = re.compile(r'\b(?:ev|src)-[1-9][0-9]*\b')


def chat_url(thread_id):
    return f'codex://threads/{UUID(thread_id)}'


def _quote_powershell(value):
    return "'" + str(value).replace("'", "''") + "'"


@dataclass(frozen=True)
class DesktopProject:
    id: str
    name: str
    roots: tuple[Path, ...]

    @classmethod
    def from_rpc(cls, project):
        roots = tuple(Path(root['path']) for root in project['roots'])
        if any(not path.is_absolute() for path in roots):
            raise BridgeError('Codex 返回的项目目录无效。')
        return cls(project['id'], project['name'], roots)


class DesktopChats:
    def __init__(self, directory: Path, *, home=None, executable=None):
        self.directory = directory.resolve()
        self.home = (Path(home or os.environ.get('OSVANTA_DESKTOP_CODEX_HOME')
                          or Path.home() / '.codex').resolve())
        self.executable = executable or os.environ.get('OSVANTA_DESKTOP_CODEX_EXE')
        self._packaged = getattr(sys, 'frozen', False)
        self.mcp_command = Path(sys.executable).resolve()
        if self._packaged:
            self.mcp_command = self.mcp_command.with_name('OsvantaCaptureMCP.exe')
            self.mcp_args = [str(self.directory)]
        else:
            adapter = Path(__file__).resolve().parents[1] / 'tools' / 'osvanta_capture_mcp.py'
            self.mcp_args = ['-B', str(adapter), str(self.directory)]

    def setup_command(self):
        return ' '.join(['codex', 'mcp', 'add', MCP_NAME, '--',
                         *map(_quote_powershell, [self.mcp_command, *self.mcp_args])])

    def _command(self):
        if self.executable:
            return str(Path(self.executable).resolve(strict=True))
        root = Path(os.environ.get('LOCALAPPDATA', '')) / 'OpenAI' / 'Codex' / 'bin'
        candidates = [path for path in root.glob('*/codex.exe') if path.is_file()]
        if not candidates:
            raise BridgeError('未找到 Codex 桌面端，请检查安装或设置 OSVANTA_DESKTOP_CODEX_EXE。')
        return str(max(candidates, key=lambda path: path.stat().st_mtime_ns))

    def _servers(self):
        try:
            with (self.home / 'config.toml').open('rb') as stream:
                servers = tomllib.load(stream).get('mcp_servers', {})
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            raise BridgeError('无法读取 Codex 桌面端的 MCP 设置。') from exc
        if not isinstance(servers, dict):
            raise BridgeError('Codex 桌面端的 MCP 设置格式无效。')
        return servers

    def _is_registered(self, servers):
        server = servers.get(MCP_NAME)
        if not isinstance(server, dict) or server.get('enabled') is False:
            return False
        command = server.get('command')
        args = server.get('args')
        if not isinstance(command, str) or not Path(command).is_absolute():
            return False
        registered = Path(command).resolve()
        if not registered.is_file():
            return False
        if registered == self.mcp_command and args == self.mcp_args:
            return True

        # An existing Desktop configuration can keep using the trusted
        # source adapter with the same capture directory after deployment.
        if (not self._packaged or registered.name.lower() not in ('python.exe', 'pythonw.exe')
                or not isinstance(args, list) or len(args) != 3
                or args[0] != '-B' or args[2] != str(self.directory)
                or not isinstance(args[1], str)):
            return False
        adapter = Path(args[1])
        return (adapter.is_absolute() and adapter.name == 'osvanta_capture_mcp.py'
                and adapter.parent.name == 'tools' and adapter.is_file())

    @contextmanager
    def _connect(self, servers, cancel):
        if cancel():
            raise Cancelled('已取消。')
        work = self.directory.parent / 'codex-shared-work'
        work.mkdir(parents=True, exist_ok=True)
        keep = {'PATH', 'SYSTEMROOT', 'WINDIR', 'PATHEXT', 'TEMP', 'TMP', 'LOCALAPPDATA',
                'APPDATA', 'COMSPEC', 'SYSTEMDRIVE', 'HOME', 'USERPROFILE'}
        env = {key: value for key, value in os.environ.items() if key.upper() in keep}
        env['CODEX_HOME'] = str(self.home)
        try:
            command = self._command()
            checked = subprocess.run(
                [command, '--version'], cwd=work, env=env, capture_output=True,
                text=True, encoding='utf-8', errors='replace', timeout=5, check=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            )
            if checked.stdout.strip() != VERIFIED_DESKTOP_VERSION:
                raise BridgeError('当前 Codex 桌面端版本尚未验证共享聊天。')
            if cancel():
                raise Cancelled('已取消。')
            overrides = ['features.hooks=false', 'features.plugins=false', 'features.apps=false',
                         'project_doc_max_bytes=0', 'approval_policy="never"',
                         'sandbox_mode="read-only"', 'web_search="disabled"']
            for name in servers:
                key = name if re.fullmatch(r'[A-Za-z0-9_-]+', name) else json.dumps(name)
                overrides.append(f'mcp_servers.{key}.enabled=false')
            argv = [command, 'app-server', '--listen', 'stdio://']
            for override in overrides:
                argv.extend(['-c', override])
            transport = StdioTransport(argv, cwd=work, env=env)
        except (OSError, subprocess.SubprocessError) as exc:
            raise BridgeError('无法连接 Codex 桌面端的本地聊天存储。') from exc
        try:
            rpc = JsonRpc(transport, cancel=cancel)
            rpc.request('initialize', {
                'clientInfo': {'name': 'osvanta_desktop', 'version': '1.0'},
                'capabilities': {'experimentalApi': True},
            }, timeout=12)
            rpc.notify('initialized')
            yield rpc, transport, str(work)
        finally:
            transport.close()

    def projects(self, cancel):
        projects, cursor = [], None
        with self._connect(self._servers(), cancel) as (rpc, _, _):
            while True:
                page = rpc.request('project/list', {'cursor': cursor, 'limit': 100}, timeout=12)
                projects.extend(DesktopProject.from_rpc(item) for item in page['data'])
                cursor = page.get('nextCursor')
                if not cursor:
                    return projects

    def create(self, scope, question, cancel, *, project: DesktopProject | None = None):
        if not question.strip() or len(question) > MAX_QUESTION:
            raise BridgeError(f'请输入 1 至 {MAX_QUESTION} 字的分析问题。')
        servers = self._servers()
        if not self._is_registered(servers):
            raise BridgeError('请先使用「配置共享」注册 Osvanta 捕获工具，再重启 Codex 的 MCP 服务器。')
        instructions = (
            f'Osvanta 已授权当前聊天访问一个临时快照。工具 {MCP_NAME} / query_capture，'
            f'capture={scope}。先用 operation=describe、arguments={{}} 获取允许的查询，'
            '再对照目标、下发指令、状态与实测反馈。区分未变化、无样本和原因未确认。'
            '引用工具实际返回的 ev- 或 src- 编号，便于 Osvanta 定位。'
            '若快照已撤销，请提示用户重新授权。'
        )
        with self._connect(servers, cancel) as (rpc, transport, work):
            params = {'cwd': work, 'ephemeral': False, 'approvalPolicy': 'never',
                      'sandbox': 'read-only', 'environments': []}
            if project is not None:
                try:
                    live = rpc.request('project/read', {'projectId': project.id}, timeout=12)
                except Cancelled:
                    raise
                except BridgeError as exc:
                    raise BridgeError('无法读取所选 Codex 项目，请刷新项目列表后重试。') from exc
                if DesktopProject.from_rpc(live['project']) != project:
                    raise BridgeError('所选 Codex 项目已更新，请刷新项目列表后重试。')
                params['projectId'] = project.id
                if project.roots:
                    if not all(path.is_dir() for path in project.roots):
                        raise BridgeError('所选 Codex 项目目录不可用。')
                    params['cwd'] = str(project.roots[0])
                    params['runtimeWorkspaceRoots'] = [str(path) for path in project.roots]
                    del params['environments']
            created = rpc.request('thread/start', params, timeout=12)['thread']
            if created.get('ephemeral') is not False:
                raise BridgeError('Codex 未创建可持久化的聊天。')
            if project is not None and (created.get('projectId') != project.id
                                        or Path(created['cwd']) != Path(params['cwd'])):
                raise BridgeError('Codex 返回的聊天项目或工作目录与所选项目不一致。')
            thread_id = str(UUID(created['id']))
            rpc.request('thread/inject_items', {'threadId': thread_id, 'items': [
                {'type': 'message', 'role': 'assistant', 'content': [
                    {'type': 'output_text', 'text': instructions},
                ]},
                {'type': 'message', 'role': 'user', 'content': [
                    {'type': 'input_text', 'text': question},
                ]},
            ]}, timeout=12)
            rpc.request('thread/name/set', {'threadId': thread_id, 'name': TITLE}, timeout=12)
            transport.finish()
        return thread_id

    def read(self, thread_id, cancel):
        thread_id = str(UUID(thread_id))
        with self._connect(self._servers(), cancel) as (rpc, _, _):
            turns = rpc.request('thread/turns/list', {
                'threadId': thread_id, 'limit': 12, 'itemsView': 'full',
            }, timeout=12)['data']
        messages, ids = [], []
        for turn in reversed(turns):
            for item in turn.get('items', []):
                if item.get('type') == 'userMessage':
                    question = '\n'.join(part['text'] for part in item.get('content', [])
                                         if part.get('type') == 'text')
                    if question:
                        messages.append('【你】\n' + question[:MAX_QUESTION])
                elif item.get('type') == 'agentMessage':
                    answer = item.get('text', '')[:24_000]
                    messages.append('【Codex】\n' + answer)
                    ids.extend(EVIDENCE_ID.findall(answer))
        return '\n\n'.join(messages[-24:]), tuple(dict.fromkeys(ids))