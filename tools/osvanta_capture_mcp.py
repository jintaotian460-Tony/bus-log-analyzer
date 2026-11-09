# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Codex Desktop stdio MCP for user-approved live Osvanta captures."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.shared_capture import REQUEST_BYTES, encode, query_capture  # noqa: E402

TOOL = {
    'name': 'query_capture',
    'description': ('Read an explicitly shared Osvanta capture. Use operation "describe" '
                    'with empty arguments first to obtain the allowed query schemas.'),
    'inputSchema': {
        'type': 'object',
        'properties': {
            'capture': {'type': 'string'},
            'operation': {'type': 'string'},
            'arguments': {'type': 'object'},
        },
        'required': ['capture', 'operation', 'arguments'],
        'additionalProperties': False,
    },
}


def handle(method, params, directory):
    if method == 'initialize':
        version = params.get('protocolVersion')
        return {
            'protocolVersion': version if version in ('2024-11-05', '2025-03-26', '2025-06-18',
                                                       '2025-11-25') else '2025-11-25',
            'capabilities': {'tools': {}},
            'serverInfo': {'name': 'osvanta_capture', 'version': '1.0'},
        }
    if method == 'ping':
        return {}
    if method == 'tools/list':
        return {'tools': [TOOL]}
    if method != 'tools/call':
        return None
    try:
        args = params.get('arguments')
        if (params.get('name') != TOOL['name'] or not isinstance(args, dict)
                or set(args) != {'capture', 'operation', 'arguments'}
                or not isinstance(args['arguments'], dict)):
            raise ValueError('无效的捕获查询。')
        data = query_capture(directory, args['capture'], args['operation'], args['arguments'])
        return {'content': [{'type': 'text', 'text': encode(data).decode('utf-8')}]}
    except ValueError as exc:
        return {'content': [{'type': 'text', 'text': str(exc)}], 'isError': True}


def main(directory):
    reader = sys.stdin.buffer
    writer = sys.stdout.buffer
    while line := reader.readline(REQUEST_BYTES * 4 + 1):
        if len(line) > REQUEST_BYTES * 4:
            return
        try:
            request = json.loads(line)
        except ValueError:
            continue
        if not isinstance(request, dict) or 'id' not in request:
            continue
        reply = {'jsonrpc': '2.0', 'id': request['id']}
        params = request.get('params', {})
        result = handle(request.get('method'), params, directory) if isinstance(params, dict) else None
        if result is None:
            reply['error'] = {'code': -32601, 'message': 'Unsupported method or parameters'}
        else:
            reply['result'] = result
        writer.write(encode(reply) + b'\n')
        writer.flush()


if __name__ == '__main__':
    main(Path(sys.argv[1]).resolve(strict=False))