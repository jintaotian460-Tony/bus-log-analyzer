# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import sys
import threading

import pytest

from core.assistant_analysis import AnalysisContext, capture_snapshot
from core.assistant_tools import AnalysisTools
from core.codex_bridge import Cancelled
from core.shared_capture import CaptureLease, UNAVAILABLE, query_capture
from tests.test_assistant_agent_ui import _wait
from tests.test_assistant_tools import ACTUATOR_KEYS, _actuator_store


@pytest.fixture
def snapshot():
    context = AnalysisContext('not-shared.csv', tuple(ACTUATOR_KEYS.values()), 0, 4, '区间')
    return capture_snapshot(_actuator_store(), context)


@pytest.fixture
def capture_listener(monkeypatch):
    from core import shared_capture

    class IdleListener:
        def __init__(self, *args, **kwargs):
            pass

        def accept(self):
            raise OSError

        def close(self):
            pass

    # This isolates lifecycle tests from the Windows pipe sandbox. The
    # native App Server smoke test exercises the real pipe and stdio MCP.
    monkeypatch.setattr(shared_capture, 'Listener', IdleListener)


def test_shared_snapshot_only_issues_scoped_evidence(snapshot, capture_listener, tmp_path):
    lease = CaptureLease(AnalysisTools(snapshot), tmp_path)
    try:
        def call(operation, **arguments):
            return lease._dispatch({'operation': operation, 'arguments': arguments})

        described = call('describe')['data']
        assert 'osvanta_lookup_reference' not in {tool['name'] for tool in described['tools']}
        assert 'not-shared.csv' not in str(call('osvanta_list_signals'))
        assert 'error' in call('osvanta_read_samples', key='other-log::signal')
        assert 'error' in call('osvanta_analyze', start=-1)
        evidence = call('osvanta_compare_events', **ACTUATOR_KEYS)['data']['evidence']
        assert lease.evidence([evidence[0]['id'], 'ev-9999'])[0].source_id == snapshot.context.source_id
        assert lease.evidence(['ev-9999']) == ()
    finally:
        lease.close()
    assert call('describe') == {'error': UNAVAILABLE}
    assert not tuple(tmp_path.iterdir())
    with pytest.raises(ValueError, match=UNAVAILABLE):
        query_capture(tmp_path, lease.scope, 'describe', {})


@pytest.mark.skipif(sys.platform != 'win32', reason='The shared UI is Windows-only')
@pytest.mark.parametrize('reason', ['cancel', 'switch', 'project', 'shutdown'])
def test_shared_chat_revokes_on_scope_exit(qapp, monkeypatch, tmp_path, snapshot, capture_listener,
                                         reason):
    from gui import assistant
    from core.desktop_chats import DesktopProject

    entered = threading.Event()
    proceed = threading.Event()
    thread_id = '12345678-1234-1234-1234-123456789abc'
    opened = []
    monkeypatch.setattr(assistant.QDesktopServices, 'openUrl', lambda url: opened.append(url.toString()) or True)

    def create(_, scope, question, cancel, *, project=None):
        entered.set()
        proceed.wait(3)
        if cancel():
            raise Cancelled('已取消。')
        return thread_id

    monkeypatch.setattr(assistant.DesktopChats, 'create', create)
    panel = assistant.AssistantPanel(project_home=tmp_path)
    try:
        panel.begin_shared(snapshot, '目标变了，执行器为什么没跟上？', None)
        assert entered.wait(2)
        lease = panel._shared_lease
        if reason == 'cancel':
            panel.cancel_codex_btn.click()
        elif reason == 'switch':
            panel.invalidate_context('测量已切换')
        elif reason == 'project':
            panel._fill_codex_projects([DesktopProject('demo', 'Demo', (tmp_path,))])
            panel.codex_project_combo.setCurrentIndex(1)
        else:
            panel.shutdown(wait_ms=0)
        assert panel._shared_lease is None
        with pytest.raises(ValueError, match=UNAVAILABLE):
            query_capture(tmp_path / 'codex-captures', lease.scope, 'describe', {})
        proceed.set()
        _wait(qapp, lambda: panel._desktop_request is None)
        qapp.processEvents()
        assert not opened
    finally:
        proceed.set()
        panel.shutdown()

@pytest.mark.skipif(sys.platform != 'win32', reason='The Codex desktop UI is Windows-only')
def test_selected_project_reaches_native_chat_and_evidence_returns_to_plot(
        qapp, monkeypatch, tmp_path, capture_listener):
    from PySide6.QtWidgets import QMessageBox
    from core.desktop_chats import DesktopProject
    from gui import app_data, assistant
    from gui.main_window import MainWindow

    project = DesktopProject('p702', '04_Software', (tmp_path / '702',))
    project.roots[0].mkdir()
    question = '目标变了，执行器为什么没跟上？'
    thread_id = '12345678-1234-1234-1234-123456789abc'
    created, opened, confirmed = [], [], []
    monkeypatch.setattr(app_data, 'per_user_folder', lambda: tmp_path)
    monkeypatch.setattr(MainWindow, '_settings_folder', staticmethod(lambda: tmp_path))
    monkeypatch.setattr(assistant.DesktopChats, 'projects', lambda _self, cancel: [project])
    monkeypatch.setattr(assistant.QDesktopServices, 'openUrl',
                        lambda url: opened.append(url.toString()) or True)

    def consent(prompt):
        confirmed.append(prompt.informativeText())
        return QMessageBox.StandardButton.Yes

    def create(_, scope, question, cancel, *, project=None):
        created.append((scope, question, project))
        return thread_id

    monkeypatch.setattr(QMessageBox, 'exec', consent)
    monkeypatch.setattr(assistant.DesktopChats, 'create', create)

    window = MainWindow('Osvanta Bus Log Analyzer', '00.00.99')
    panel = window.assistant_panel
    try:
        window.store = _actuator_store()
        window.measurement_path = 'private-log.csv'
        window.add_signals_to_plot(list(ACTUATOR_KEYS.values()))
        window._assistant_first_show(True)
        _wait(qapp, lambda: panel._desktop_request is None)
        panel.codex_project_combo.setCurrentIndex(1)
        panel.question_input.setPlainText(question)
        panel.share_btn.click()
        _wait(qapp, lambda: panel._desktop_request is None)
        lease = panel._shared_lease
        assert created == [(lease.scope, question, project)]
        assert '本次授权' in panel.privacy_label.text()
        assert opened == [f'codex://threads/{thread_id}']
        assert project.name in confirmed[0] and str(project.roots[0]) in confirmed[0]
        response = lease._dispatch({'operation': 'osvanta_compare_events', 'arguments': ACTUATOR_KEYS})
        identifier = response['data']['evidence'][0]['id']

        def read(_, chat, cancel):
            assert chat == thread_id
            return f'延迟 0.25 s [{identifier}]', (identifier, 'ev-9999')

        monkeypatch.setattr(assistant.DesktopChats, 'read', read)
        panel.read_chat_btn.click()
        _wait(qapp, lambda: panel._desktop_request is None)
        assert panel.codex_evidence_list.count() == 1
        evidence = panel.codex_evidence_list.item(0)
        calls = []
        monkeypatch.setattr(window.plot_panel, 'center_on_time',
                            lambda time, span, **kwargs: calls.append((time, span)))
        panel.codex_evidence_list.itemActivated.emit(evidence)
        assert len(calls) == 1
        window._release_store()
        assert panel.codex_project_combo.currentData() == project
        assert panel.codex_evidence_list.count() == 0
        assert panel._shared_lease is None
        assert panel.privacy_label.text() == panel.PRIVACY_NOTE
    finally:
        panel.shutdown()
        window.deleteLater()
        qapp.processEvents()
