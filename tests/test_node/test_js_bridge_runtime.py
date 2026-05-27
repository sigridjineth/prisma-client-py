import os
import json
import shutil
import textwrap
import subprocess
from typing import Any
from pathlib import Path

import pytest

RUNTIME = Path('src/prisma/generator/templates/js_bridge/runtime.mjs')


def _read_json_line(proc: subprocess.Popen[str]) -> dict[str, Any]:
    line = proc.stdout.readline() if proc.stdout is not None else ''
    assert line, 'expected a stdout protocol line from bridge runtime'
    return json.loads(line)


def _send(proc: subprocess.Popen[str], frame: dict[str, Any]) -> None:
    assert proc.stdin is not None
    proc.stdin.write(json.dumps(frame) + '\n')
    proc.stdin.flush()


@pytest.fixture()
def fake_bridge_modules(tmp_path: Path) -> dict[str, str]:
    client = tmp_path / 'fake-client.mjs'
    client.write_text(
        textwrap.dedent(
            """
            export class PrismaClient {
              constructor(options) {
                this.options = options;
                this.connected = false;
                this.transactionOptions = [];
                this.txUser = {
                  findMany: async () => [{id: 2n, email: 'tx@example.com'}],
                  create: async (args) => ({id: 2n, ...args.data}),
                };
                this.user = {
                  findMany: async (args) => {
                    if (args && args.delayMs) {
                      await new Promise((resolve) => setTimeout(resolve, args.delayMs));
                    }
                    return [{id: 1n, email: 'alice@example.com', when: new Date('2026-05-26T00:00:00.000Z')}];
                  },
                  validationError: async () => {
                    const err = new Error('bad where');
                    err.name = 'PrismaClientValidationError';
                    err.code = 'P2009';
                    throw err;
                  }
                };
              }
              async $connect() {
                console.log('fake client diagnostic on stdout API');
                this.connected = true;
              }
              async $disconnect() {
                this.connected = false;
              }
              async $queryRawUnsafe(sql, ...params) {
                return [{ok: true, sql, params}];
              }
              async $transaction(operationsOrCallback, options) {
                this.transactionOptions.push(options || null);
                if (Array.isArray(operationsOrCallback)) {
                  return await Promise.all(operationsOrCallback);
                }
                if (typeof operationsOrCallback === 'function') {
                  return await operationsOrCallback({
                    user: this.txUser,
                    $queryRawUnsafe: async (sql, ...params) => [{tx: true, sql, params}],
                    $executeRawUnsafe: async () => 1,
                  });
                }
                throw new Error('unsupported transaction input');
              }
            }
            """
        ).strip()
    )
    adapter = tmp_path / 'fake-adapter.mjs'
    adapter.write_text(
        textwrap.dedent(
            """
            export class PrismaPg {
              constructor(options) {
                this.options = options;
              }
            }
            """
        ).strip()
    )
    return {
        'PRISMA_PY_BRIDGE_CLIENT_MODULE': client.as_uri(),
        'PRISMA_PY_BRIDGE_ADAPTER_MODULE': adapter.as_uri(),
        'PRISMA_PY_BRIDGE_PROVIDER': 'postgresql',
        'PRISMA_PY_BRIDGE_ADAPTER': '@prisma/adapter-pg',
        'PRISMA_PY_BRIDGE_CLIENT_VERSION': '7.8.0',
        'DATABASE_URL': 'postgresql://user:pass@localhost:5432/db',
    }


def _spawn_runtime(env: dict[str, str]) -> subprocess.Popen[str]:
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node is not installed globally')
    return subprocess.Popen(
        [node, str(RUNTIME)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, **env},
    )


def test_js_bridge_runtime_lifecycle_stdout_protocol_only(fake_bridge_modules: dict[str, str]) -> None:
    proc = _spawn_runtime(fake_bridge_modules)
    try:
        ready = _read_json_line(proc)
        assert ready['method'] == 'bridge.ready'
        assert ready['params']['protocolVersion'] == '2026-05-26.phase0.v1'
        assert ready['params']['provider'] == 'postgresql'
        assert ready['params']['adapter'] == '@prisma/adapter-pg'

        _send(
            proc,
            {
                'id': 'req_health_1',
                'method': 'bridge.healthcheck',
                'params': {'requireDatabase': False},
                'timeoutMs': 1000,
                'clientVersion': 'prisma-client-py/phase1',
            },
        )
        health = _read_json_line(proc)
        assert health['id'] == 'req_health_1'
        assert health['result'] == {'status': 'ok', 'databaseReachable': None, 'activeTransactions': 0}

        _send(
            proc,
            {
                'id': 'req_connect_1',
                'method': 'client.connect',
                'params': {'datasource': None, 'logQueries': False, 'adapterOptions': {}},
                'timeoutMs': 1000,
            },
        )
        connect = _read_json_line(proc)
        assert connect['result'] == {'status': 'connected'}

        _send(
            proc,
            {
                'id': 'req_query_1',
                'method': 'query.execute',
                'params': {'kind': 'model', 'model': 'User', 'action': 'findMany', 'args': {}},
                'timeoutMs': 1000,
            },
        )
        query = _read_json_line(proc)
        assert query['id'] == 'req_query_1'
        assert query['result'][0]['id'] == {'$type': 'BigInt', 'value': '1'}
        assert query['result'][0]['when'] == {'$type': 'DateTime', 'value': '2026-05-26T00:00:00.000Z'}

        _send(proc, {'id': 'req_disconnect_1', 'method': 'client.disconnect', 'params': {}, 'timeoutMs': 1000})
        assert _read_json_line(proc)['result'] == {'status': 'disconnected'}

        _send(proc, {'id': 'req_shutdown_1', 'method': 'bridge.shutdown', 'params': {}, 'timeoutMs': 1000})
        assert _read_json_line(proc)['result'] == {'status': 'shutdown'}
        assert proc.wait(timeout=5) == 0
        stderr = proc.stderr.read() if proc.stderr is not None else ''
        assert 'fake client diagnostic on stdout API' in stderr
    finally:
        if proc.poll() is None:
            proc.kill()


def test_js_bridge_runtime_interactive_transaction_lifecycle(fake_bridge_modules: dict[str, str]) -> None:
    proc = _spawn_runtime(fake_bridge_modules)
    try:
        assert _read_json_line(proc)['method'] == 'bridge.ready'

        _send(
            proc,
            {
                'id': 'req_connect_1',
                'method': 'client.connect',
                'params': {'datasource': None, 'logQueries': False, 'adapterOptions': {}},
                'timeoutMs': 1000,
            },
        )
        assert _read_json_line(proc)['result'] == {'status': 'connected'}

        _send(
            proc,
            {
                'id': 'req_tx_start_1',
                'method': 'transaction.start',
                'params': {'timeoutMs': 5000, 'maxWaitMs': 1000, 'isolationLevel': 'Serializable'},
                'timeoutMs': 1000,
            },
        )
        tx_start = _read_json_line(proc)
        tx_id = tx_start['result']['transactionId']
        assert tx_id.startswith('tx_')

        _send(
            proc,
            {
                'id': 'req_tx_query_1',
                'method': 'query.execute',
                'transactionId': tx_id,
                'params': {'kind': 'model', 'model': 'User', 'action': 'findMany', 'args': {}},
                'timeoutMs': 1000,
            },
        )
        tx_query = _read_json_line(proc)
        assert tx_query['result'][0]['email'] == 'tx@example.com'

        _send(
            proc,
            {
                'id': 'req_tx_commit_1',
                'method': 'transaction.commit',
                'transactionId': tx_id,
                'params': {},
                'timeoutMs': 1000,
            },
        )
        assert _read_json_line(proc)['result'] == {'status': 'committed'}

        _send(
            proc,
            {
                'id': 'req_tx_closed_1',
                'method': 'query.execute',
                'transactionId': tx_id,
                'params': {'kind': 'model', 'model': 'User', 'action': 'findMany', 'args': {}},
                'timeoutMs': 1000,
            },
        )
        assert _read_json_line(proc)['error']['code'] == 'TRANSACTION_CLOSED'

        _send(
            proc,
            {
                'id': 'req_tx_start_2',
                'method': 'transaction.start',
                'params': {'timeoutMs': 5000, 'maxWaitMs': 1000, 'isolationLevel': None},
                'timeoutMs': 1000,
            },
        )
        rollback_tx_id = _read_json_line(proc)['result']['transactionId']
        _send(
            proc,
            {
                'id': 'req_tx_rollback_1',
                'method': 'transaction.rollback',
                'transactionId': rollback_tx_id,
                'params': {'reason': 'test-rollback'},
                'timeoutMs': 1000,
            },
        )
        assert _read_json_line(proc)['result'] == {'status': 'rolled_back'}

        _send(proc, {'id': 'req_shutdown_1', 'method': 'bridge.shutdown', 'params': {}, 'timeoutMs': 1000})
        assert _read_json_line(proc)['result'] == {'status': 'shutdown'}
        assert proc.wait(timeout=5) == 0
    finally:
        if proc.poll() is None:
            proc.kill()


def test_js_bridge_runtime_protocol_error_timeout_and_cancel(fake_bridge_modules: dict[str, str]) -> None:
    proc = _spawn_runtime(fake_bridge_modules)
    try:
        assert _read_json_line(proc)['method'] == 'bridge.ready'

        _send(proc, {'id': 'bad_method', 'method': 'bridge.nope', 'params': {}, 'timeoutMs': 1000})
        bad_method = _read_json_line(proc)
        assert bad_method['error']['code'] == 'BRIDGE_PROTOCOL_ERROR'
        assert bad_method['error']['retryable'] is False

        _send(
            proc,
            {
                'id': 'req_validation_1',
                'method': 'query.execute',
                'params': {'kind': 'model', 'model': 'User', 'action': 'validationError', 'args': {}},
                'timeoutMs': 1000,
            },
        )
        validation = _read_json_line(proc)
        assert validation['error']['code'] == 'PRISMA_VALIDATION_ERROR'
        assert validation['error']['prismaCode'] == 'P2009'

        _send(
            proc,
            {
                'id': 'req_timeout_1',
                'method': 'query.execute',
                'params': {'kind': 'model', 'model': 'User', 'action': 'findMany', 'args': {'delayMs': 200}},
                'timeoutMs': 20,
            },
        )
        timeout = _read_json_line(proc)
        assert timeout['id'] == 'req_timeout_1'
        assert timeout['error']['code'] == 'BRIDGE_TIMEOUT'

        _send(
            proc,
            {
                'id': 'req_cancel_target_1',
                'method': 'query.execute',
                'params': {'kind': 'model', 'model': 'User', 'action': 'findMany', 'args': {'delayMs': 500}},
                'timeoutMs': 1000,
            },
        )
        _send(
            proc,
            {
                'id': 'req_cancel_1',
                'method': 'bridge.cancel',
                'params': {'targetRequestId': 'req_cancel_target_1', 'reason': 'python-timeout'},
                'timeoutMs': 1000,
            },
        )
        frames = [_read_json_line(proc), _read_json_line(proc)]
        by_id = {frame['id']: frame for frame in frames}
        assert by_id['req_cancel_1']['result'] == {
            'status': 'cancellation_requested',
            'targetRequestId': 'req_cancel_target_1',
        }
        assert by_id['req_cancel_target_1']['error']['code'] == 'BRIDGE_CANCELLED'

        _send(proc, {'id': 'req_shutdown_1', 'method': 'bridge.shutdown', 'params': {}, 'timeoutMs': 1000})
        assert _read_json_line(proc)['result'] == {'status': 'shutdown'}
        assert proc.wait(timeout=5) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
