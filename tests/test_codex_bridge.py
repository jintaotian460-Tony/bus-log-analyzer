# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Codex integration without credentials, network calls, or a real model."""
from __future__ import annotations


def test_final_answer_only_links_locally_issued_evidence():
    from core.codex_bridge import parse_answer

    class Tools:
        def resolve_evidence(self, ids):
            if ids != ['E1']:
                raise ValueError('unknown evidence')
            return ('verified E1',)

    result = parse_answer(
        '{"observations":["转速变化"],"hypotheses":["尚待核对请求"],'
        '"missing":["请求信号"],"evidence_ids":["E1"]}', Tools())
    assert result.observations == ('转速变化',)
    assert result.hypotheses == ('尚待核对请求',)
    assert result.missing == ('请求信号',)
    assert result.evidence == ('verified E1',)


def test_unknown_evidence_and_non_json_answers_are_rejected():
    import pytest
    from core.codex_bridge import parse_answer

    class Tools:
        def resolve_evidence(self, ids):
            raise ValueError('unknown evidence')

    with pytest.raises(ValueError, match='unknown evidence'):
        parse_answer('{"observations":[],"hypotheses":[],"missing":[], '
                     '"evidence_ids":["invented"]}', Tools())
    with pytest.raises(ValueError):
        parse_answer('I think this is fine', Tools())


def test_rpc_wait_routes_notifications_and_replies_to_tool_requests():
    from core.codex_bridge import JsonRpc

    class Transport:
        def __init__(self):
            self.sent = []
            self.incoming = iter([
                {'method': 'item/agentMessage/delta', 'params': {'delta': 'a'}},
                {'id': 'server-1', 'method': 'item/tool/call',
                 'params': {'tool': 'osvanta_analyze'}},
                {'id': 1, 'result': {'thread': {'id': 'new-thread'}}},
            ])

        def send(self, value):
            self.sent.append(value)

        def receive(self, timeout):
            return next(self.incoming)

    transport = Transport()
    updates = []
    rpc = JsonRpc(transport, on_event=lambda m, p: updates.append((m, p)),
                  on_request=lambda m, p: {'success': True})
    assert rpc.request('thread/start', {}) == {'thread': {'id': 'new-thread'}}
    assert transport.sent == [
        {'id': 1, 'method': 'thread/start', 'params': {}},
        {'id': 'server-1', 'result': {'success': True}},
    ]
    assert updates == [('item/agentMessage/delta', {'delta': 'a'})]


def test_session_streams_then_verifies_answer_and_reuses_its_own_thread():
    from core.codex_bridge import CodexSession

    class Tools:
        class Context:
            snapshot_id = 'snapshot-1'
        context = Context()

        def specs(self):
            return []

        def resolve_evidence(self, ids):
            assert ids == []
            return ()

    class Transport:
        def __init__(self):
            self.sent = []
            self.pending = []
            self.turn = 0

        def send(self, value):
            self.sent.append(value)
            method = value.get('method')
            if 'id' not in value:
                return
            if method == 'initialize':
                result = {'userAgent': 'codex/0.142.5'}
            elif method == 'config/read':
                result = {'config': {'model': 'test-model', 'model_provider': 'openai'}}
            elif method == 'thread/start':
                result = {'thread': {'id': 'only-osvanta-thread'},
                          'model': value['params']['model'], 'modelProvider': 'openai',
                          'sandbox': {'type': 'readOnly', 'networkAccess': False}}
            elif method == 'turn/start':
                self.turn += 1
                tid = f'turn-{self.turn}'
                text = ('{"observations":["checked"],"hypotheses":[], '
                        '"missing":[],"evidence_ids":[]}')
                self.pending += [
                    {'id': value['id'], 'result': {'turn': {'id': tid}}},
                    {'method': 'item/agentMessage/delta', 'params': {
                        'threadId': 'only-osvanta-thread', 'turnId': tid,
                        'itemId': 'a', 'delta': text}},
                    {'method': 'turn/completed', 'params': {
                        'threadId': 'only-osvanta-thread', 'turn': {
                            'id': tid, 'status': 'completed', 'items': []}}},
                ]
                return
            else:
                result = {}
            self.pending.append({'id': value['id'], 'result': result})

        def receive(self, timeout):
            return self.pending.pop(0) if self.pending else None

        def close(self):
            pass

    transport = Transport()
    session = CodexSession(transport, model='available-model')
    info = session.connect()
    assert info.model == 'available-model'
    assert info.provider == 'openai'
    updates = []
    assert session.ask('what changed?', Tools(), on_progress=updates.append).observations == ('checked',)
    assert session.ask('and then?', Tools()).observations == ('checked',)
    assert sum(m.get('method') == 'thread/start' for m in transport.sent) == 1
    start = next(m['params'] for m in transport.sent if m.get('method') == 'thread/start')
    assert start['model'] == 'available-model'
    assert start['ephemeral'] is True
    assert start['sandbox'] == 'read-only'
    assert start['approvalPolicy'] == 'never'
    assert not any(m.get('method') == 'thread/resume' for m in transport.sent)
    assert updates


def test_model_rejection_is_actionable_and_does_not_display_raw_payload():
    import pytest

    from core.codex_bridge import BridgeError, CodexSession

    session = CodexSession(object())
    session.thread_id = 'ours'
    session._active = True
    session._on_event('error', {'threadId': 'ours', 'willRetry': True})
    with pytest.raises(BridgeError, match='账号无法使用') as caught:
        session._on_event('error', {'threadId': 'ours', 'error': {
            'codexErrorInfo': 'badRequest',
            'message': "The 'gpt-5.5' model is not supported when using Codex with a ChatGPT account.",
            'additionalDetails': 'private request context',
        }})
    assert 'private request context' not in str(caught.value)


def test_readonly_model_catalogue_disables_general_tools():
    import json
    import pytest

    from core.codex_bridge import ANALYST_MODEL, BridgeError, _readonly_model_catalogue

    stock = {'models': [{'slug': ANALYST_MODEL, 'tool_mode': 'code_mode_only',
                         'multi_agent_version': 'v2', 'multi_agent_reasoning_effort': 'xhigh',
                         'apply_patch_tool_type': 'freeform', 'shell_type': 'unified_exec',
                         'input_modalities': ['text', 'image'],
                         'experimental_supported_tools': ['clock', 'send_user_message_async']},
                        {'slug': 'other'}]}
    [profile] = _readonly_model_catalogue(json.dumps(stock))['models']
    assert profile['slug'] == ANALYST_MODEL
    assert not set(profile) & {'tool_mode', 'multi_agent_version',
                               'multi_agent_reasoning_effort', 'apply_patch_tool_type'}
    assert profile['shell_type'] == 'disabled'
    assert profile['input_modalities'] == ['text']
    assert profile['experimental_supported_tools'] == []
    assert profile['supports_search_tool'] is False
    with pytest.raises(BridgeError, match='缺少已验证'):
        _readonly_model_catalogue('{"models": []}')


def test_launch_uses_an_independent_home_and_no_host_environment(tmp_path):
    from core.codex_bridge import prepare_launch

    program = tmp_path / 'codex.exe'
    program.write_bytes(b'fake')
    launch = prepare_launch(tmp_path / 'private-home', str(program))
    assert launch.env['CODEX_HOME'] == str(tmp_path / 'private-home')
    assert launch.cwd == str(tmp_path / 'private-home' / 'work')
    assert 'app-server' in launch.command
    assert '--strict-config' in launch.command
    assert 'features.shell_tool=false' in launch.command
    assert 'features.hooks=false' in launch.command
    assert 'features.plugins=false' in launch.command
    assert 'web_search="disabled"' in launch.command
    assert 'model_provider="openai"' in launch.command
    assert 'OPENAI_API_KEY' not in launch.env
    assert launch.config['features.shell_tool'] is False


def test_launch_refuses_an_existing_unknown_configuration(tmp_path):
    import pytest
    from core.codex_bridge import prepare_launch, BridgeError

    home = tmp_path / 'existing'
    home.mkdir()
    config = home / 'config.toml'
    config.write_text('sandbox_mode="danger-full-access"\n', encoding='utf-8')
    with pytest.raises(BridgeError, match='配置'):
        prepare_launch(home, 'codex')
    assert 'danger-full-access' in config.read_text(encoding='utf-8')


def test_external_process_transport_uses_real_json_lines_and_closes(tmp_path):
    import sys
    from core.codex_bridge import StdioTransport, JsonRpc

    child = tmp_path / 'fake_app_server.py'
    child.write_text(
        'import json,sys\n'
        'for line in sys.stdin:\n'
        ' m=json.loads(line)\n'
        ' if "id" in m:\n'
        '  print(json.dumps({"id":m["id"],"result":{"ok":True}}),flush=True)\n',
        encoding='utf-8',
    )
    transport = StdioTransport([sys.executable, str(child)], cwd=str(tmp_path))
    try:
        assert JsonRpc(transport).request('initialize', {}, timeout=5) == {'ok': True}
    finally:
        transport.close()


def test_cancel_after_turn_start_closes_the_unacknowledged_session():
    from types import SimpleNamespace
    import pytest
    from core.codex_bridge import Cancelled, CodexSession, ConnectionInfo

    class Transport:
        closed = False
        cancelled = False

        def send(self, request):
            assert request['method'] == 'turn/start'
            self.cancelled = True

        def receive(self, timeout):
            raise AssertionError('Cancellation should be observed before waiting for a reply')

        def close(self):
            self.closed = True

    transport = Transport()
    session = CodexSession(transport)
    session.info = ConnectionInfo('openai', 'fake')
    session.thread_id = 'thread'
    session._snapshot_id = 'snapshot'
    tools = SimpleNamespace(context=SimpleNamespace(snapshot_id='snapshot'))
    with pytest.raises(Cancelled):
        session.ask('test', tools, cancel=lambda: transport.cancelled)
    assert transport.closed and session.thread_id is None


def test_cancel_releases_a_write_to_a_server_that_never_reads_stdin(tmp_path):
    import sys
    import threading
    import time
    import pytest
    from core.codex_bridge import Cancelled, JsonRpc, StdioTransport

    transport = StdioTransport(
        [sys.executable, '-c', 'import time; time.sleep(60)'], cwd=str(tmp_path))
    cancelled = threading.Event()
    timer = threading.Timer(0.25, cancelled.set)
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(Cancelled):
            JsonRpc(transport, cancel=cancelled.is_set).request(
                'test', {'content': 'x' * (1024 * 1024)})
    finally:
        timer.cancel()
        transport.close()
    assert time.monotonic() - started < 5
