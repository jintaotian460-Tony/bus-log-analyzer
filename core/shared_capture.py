# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Serve one approved AnalysisTools instance over an authenticated local pipe."""
from __future__ import annotations

from multiprocessing import AuthenticationError
from multiprocessing.connection import Client, Listener
from pathlib import Path
import json
import os
import re
import secrets
import threading

REQUEST_BYTES = 16 * 1024
RESPONSE_BYTES = 1024 * 1024
UNAVAILABLE = 'Osvanta 捕获已撤销或不可用。'
_SCOPE = re.compile(r'[0-9a-f]{48}')
_PREFIX = r'\\.\pipe\osvanta-capture-'


def encode(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')


class CaptureLease:
    """An in-memory, revocable capability. The descriptor contains no capture data."""

    def __init__(self, tools, directory: Path):
        self.scope = secrets.token_hex(24)
        self._key = secrets.token_bytes(32)
        self._closed = threading.Event()
        self._tools = tools
        self._specs = tools.specs()
        self._evidence = {}
        self._file = directory / f'{self.scope}.json'
        directory.mkdir(parents=True, exist_ok=True)
        self._listener = Listener(_PREFIX + self.scope, family='AF_PIPE', authkey=self._key)
        temporary = self._file.with_suffix('.tmp')
        try:
            temporary.write_bytes(encode({'key': self._key.hex()}))
            os.replace(temporary, self._file)
            threading.Thread(target=self._serve, daemon=True).start()
        except BaseException:
            temporary.unlink(missing_ok=True)
            self.close()
            raise

    def evidence(self, ids):
        issued = self._evidence
        return tuple(issued[evidence_id] for evidence_id in ids if evidence_id in issued)

    def close(self):
        if self._closed.is_set():
            return
        self._closed.set()
        self._tools = None
        self._evidence = {}
        self._listener.close()
        try:
            self._file.unlink(missing_ok=True)
        except OSError:
            # A reader can hold the descriptor open on Windows. The pipe is
            # already closed, and the random name is never reissued.
            pass

    def _serve(self):
        while not self._closed.is_set():
            try:
                connection = self._listener.accept()
            except (AuthenticationError, EOFError):
                continue
            except OSError:
                return
            with connection:
                try:
                    if not connection.poll(2):
                        continue
                    request = json.loads(connection.recv_bytes(REQUEST_BYTES))
                    payload = encode(self._dispatch(request))
                    if len(payload) > RESPONSE_BYTES:
                        payload = encode({'error': '工具结果超过大小上限。'})
                    if not self._closed.is_set():
                        connection.send_bytes(payload)
                except (EOFError, OSError, ValueError, TypeError):
                    continue

    def _dispatch(self, request):
        tools = self._tools
        if (tools is None or self._closed.is_set() or not isinstance(request, dict)
                or set(request) != {'operation', 'arguments'}
                or not isinstance(request['arguments'], dict)):
            return {'error': UNAVAILABLE}
        operation = request['operation']
        arguments = request['arguments']
        if operation == 'describe' and not arguments:
            return {'data': {'tools': self._specs, 'project': tools.project_context}}
        if not any(spec['name'] == operation for spec in self._specs):
            return {'error': '此捕获未授权该操作。'}
        try:
            result = tools.call(operation, arguments)
            ids = [row['id'] for row in result.get('evidence', result.get('matches', []))]
            if ids and not self._closed.is_set():
                self._evidence = {**self._evidence, **dict(
                    zip(ids, tools.resolve_evidence(ids)))}
            return {'data': result}
        except (ValueError, TypeError, OSError, OverflowError) as exc:
            if isinstance(exc, ValueError):
                return {'error': str(exc)}
            return {'error': '此捕获暂时无法完成查询。'}


def query_capture(directory: Path, scope, operation, arguments):
    if not isinstance(scope, str) or not _SCOPE.fullmatch(scope):
        raise ValueError(UNAVAILABLE)
    request = encode({'operation': operation, 'arguments': arguments})
    if len(request) > REQUEST_BYTES:
        raise ValueError('查询参数超过大小上限。')
    try:
        with (directory / f'{scope}.json').open('rb') as stream:
            key = bytes.fromhex(json.loads(stream.read(512))['key'])
        if len(key) != 32:
            raise ValueError
        with Client(_PREFIX + scope, family='AF_PIPE', authkey=key) as connection:
            connection.send_bytes(request)
            if not connection.poll(10):
                raise ValueError
            response = json.loads(connection.recv_bytes(RESPONSE_BYTES))
    except (AuthenticationError, EOFError, OSError, ValueError, TypeError, KeyError) as exc:
        raise ValueError(UNAVAILABLE) from exc
    if 'error' in response:
        raise ValueError(response['error'])
    return response['data']