# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.


from contextlib import contextmanager
import sys
import time

import pytest

from core.codex_bridge import BridgeError
from core.desktop_chats import DesktopChats, DesktopProject


def _wait(qapp, predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(.01)
    assert predicate()


@pytest.mark.skipif(sys.platform != 'win32', reason='The Codex desktop UI is Windows-only')
def test_codex_project_list_refresh_and_persistence(qapp, monkeypatch, tmp_path):
    from gui.assistant import AssistantPanel

    e702 = DesktopProject('702', '04_Software', (tmp_path / 'E702',))
    p717 = DesktopProject('717', 'P717_SVN', (tmp_path / 'P717',))
    listing = [e702, p717]
    monkeypatch.setattr(DesktopChats, 'projects', lambda _self, cancel: list(listing))
    for expected in (None, p717):
        panel = AssistantPanel(project_home=tmp_path)
        try:
            panel.refresh_codex_projects()
            _wait(qapp, lambda: panel._desktop_request is None)
            combo = panel.codex_project_combo
            assert [combo.itemText(i) for i in range(combo.count())] == [
                '不归入项目', '04_Software', 'P717_SVN',
            ]
            assert combo.currentData() == expected
            assert not hasattr(panel, 'agent_btn') and not hasattr(panel, 'project_agent_combo')
            combo.setCurrentIndex(2)
            assert combo.currentData() == p717
            assert str(p717.roots[0]) == combo.toolTip()
        finally:
            panel.shutdown()

    listing.remove(p717)
    panel = AssistantPanel(project_home=tmp_path)
    try:
        panel.refresh_codex_projects()
        _wait(qapp, lambda: panel._desktop_request is None)
        assert panel.codex_project_combo.currentIndex() == -1
        assert not panel.share_btn.isEnabled()
    finally:
        panel.shutdown()


@pytest.mark.parametrize('changed', [False, True])
def test_codex_thread_start_uses_project_identity_and_root(tmp_path, monkeypatch, changed):
    root = tmp_path / 'selected'
    root.mkdir()
    project = DesktopProject('project-702', '04_Software', (root,))
    service = DesktopChats(tmp_path / 'captures')
    calls = []

    class Rpc:
        def request(self, method, params, *, timeout):
            calls.append((method, params))
            if method == 'project/read':
                assert params == {'projectId': project.id}
                return {'project': {'id': project.id, 'name': project.name,
                                    'roots': [{'path': str(tmp_path if changed else root)}]}}
            if method == 'thread/start':
                return {'thread': {'id': '12345678-1234-1234-1234-123456789abc',
                                   'cwd': str(root), 'projectId': project.id, 'ephemeral': False}}
            return {}

        def finish(self):
            pass

    @contextmanager
    def connect(_servers, _cancel):
        rpc = Rpc()
        yield rpc, rpc, str(tmp_path)

    monkeypatch.setattr(service, '_connect', connect)
    monkeypatch.setattr(service, '_servers', dict)
    monkeypatch.setattr(service, '_is_registered', lambda _servers: True)
    if changed:
        with pytest.raises(BridgeError, match='已更新'):
            service.create('scope', 'Question?', lambda: False, project=project)
        assert [method for method, _ in calls] == ['project/read']
    else:
        service.create('scope', 'Question?', lambda: False, project=project)
        assert calls[1] == ('thread/start', {
            'cwd': str(root), 'ephemeral': False, 'approvalPolicy': 'never',
            'sandbox': 'read-only', 'projectId': project.id,
            'runtimeWorkspaceRoots': [str(root)],
        })
        assert calls[2][0] == 'thread/inject_items'
