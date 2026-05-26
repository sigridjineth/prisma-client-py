from __future__ import annotations

import json
from typing import Any, Iterator
from pathlib import Path

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / 'pyproject.toml').exists())
PHASE0 = ROOT / 'docs' / 'prisma7-js-bridge' / 'phase0'
FIXTURES = PHASE0 / 'fixtures'
PROTOCOL_VERSION = '2026-05-26.phase0.v1'

REQUIRED_TRANSACTION_SCENARIOS = {
    'interactiveCommit',
    'interactiveRollbackOnPythonException',
    'timeoutTaintsTransaction',
    'batchTransaction',
    'batchFailureRollsBack',
    'interactiveCancellationRollsBack',
    'bridgeDeathMarksTransactionLost',
    'disconnectRollsBackOpenTransaction',
    'disconnectRollbackTimeoutUnsafeShutdown',
    'nestedTransactionUnsupported',
    'closedTransactionIdReuseRejected',
}


def load_fixture(name: str) -> dict[str, Any]:
    path = FIXTURES / name
    with path.open(encoding='utf-8') as handle:
        value = json.load(handle)

    assert isinstance(value, dict)
    return value


def assert_protocol_meta(meta: object) -> None:
    assert isinstance(meta, dict)
    assert meta.get('protocolVersion') == PROTOCOL_VERSION
    assert isinstance(meta.get('elapsedMs'), int)
    assert meta['elapsedMs'] >= 0


def assert_response_envelope(response: dict[str, Any]) -> None:
    assert isinstance(response.get('id'), str)
    assert ('result' in response) ^ ('error' in response), response
    assert_protocol_meta(response.get('meta'))

    if 'error' in response:
        error = response['error']
        assert isinstance(error, dict)
        assert isinstance(error.get('code'), str)
        assert isinstance(error.get('message'), str)
        assert isinstance(error.get('retryable'), bool)
        assert 'meta' in error
        assert 'debug' in error


def assert_request_envelope(request: dict[str, Any]) -> None:
    assert isinstance(request.get('id'), str)
    assert isinstance(request.get('method'), str)
    assert isinstance(request.get('params'), dict)
    assert isinstance(request.get('timeoutMs'), int)
    assert request['timeoutMs'] > 0
    assert request.get('clientVersion') == 'prisma-client-py/phase0'


def iter_request_response_pairs(fixture: dict[str, Any]) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
    request = fixture.get('request')
    response = fixture.get('response')
    if isinstance(request, dict) and isinstance(response, dict):
        yield request, response

    requests = fixture.get('requests')
    responses = fixture.get('responses')
    if isinstance(requests, list) and isinstance(responses, list):
        assert len(requests) == len(responses)
        for item_request, item_response in zip(requests, responses):
            assert isinstance(item_request, dict)
            assert isinstance(item_response, dict)
            yield item_request, item_response

    stdin = fixture.get('stdin')
    stdout = fixture.get('stdout')
    if isinstance(stdin, list) and isinstance(stdout, list):
        responses_by_id = {
            item['id']: item for item in stdout if isinstance(item, dict) and isinstance(item.get('id'), str)
        }
        for item_request in stdin:
            assert isinstance(item_request, dict)
            response_for_request = responses_by_id[item_request['id']]
            yield item_request, response_for_request

    for key in REQUIRED_TRANSACTION_SCENARIOS:
        scenario = fixture.get(key)
        if isinstance(scenario, dict):
            yield from iter_request_response_pairs(scenario)

    scenarios = fixture.get('scenarios')
    if isinstance(scenarios, list):
        for scenario in scenarios:
            assert isinstance(scenario, dict)
            yield from iter_request_response_pairs(scenario)


def error_code(response: dict[str, Any]) -> str:
    error = response['error']
    assert isinstance(error, dict)
    code = error['code']
    assert isinstance(code, str)
    return code


def error_meta(response: dict[str, Any]) -> dict[str, Any]:
    error = response['error']
    assert isinstance(error, dict)
    meta = error['meta']
    assert isinstance(meta, dict)
    return meta


def result(response: dict[str, Any]) -> Any:
    assert 'result' in response
    return response['result']


def markdown_table_rows(path: Path) -> Iterator[list[str]]:
    for line in path.read_text(encoding='utf-8').splitlines():
        stripped = line.strip()
        if not stripped.startswith('|'):
            continue

        cells = [cell.strip() for cell in stripped.strip('|').split('|')]
        if not cells or all(set(cell) <= {'-', ':'} for cell in cells):
            continue

        yield cells


def test_phase0_fixture_manifest_lists_required_transaction_gates() -> None:
    manifest = load_fixture('manifest.json')

    assert manifest['schemaVersion'] == 1
    assert manifest['protocolVersion'] == PROTOCOL_VERSION
    assert set(manifest['transactionScenarioKeys']) == REQUIRED_TRANSACTION_SCENARIOS

    fixture_names = manifest['fixtures']
    assert isinstance(fixture_names, list)
    assert 'transaction-lifecycle.json' in fixture_names
    for fixture_name in fixture_names:
        fixture_path = FIXTURES / fixture_name
        assert fixture_path.exists(), fixture_path
        fixture = load_fixture(fixture_name)
        assert fixture.get('protocolVersion') == PROTOCOL_VERSION


def test_phase0_fixture_protocol_envelopes_are_request_response_safe() -> None:
    manifest = load_fixture('manifest.json')

    for fixture_name in manifest['fixtures']:
        fixture = load_fixture(fixture_name)
        for request, response in iter_request_response_pairs(fixture):
            assert_request_envelope(request)
            assert_response_envelope(response)
            assert response['id'] == request['id']


def test_transaction_lifecycle_fixture_covers_success_rollback_and_failure_semantics() -> None:
    fixture = load_fixture('transaction-lifecycle.json')
    assert set(fixture) >= REQUIRED_TRANSACTION_SCENARIOS | {'name', 'protocolVersion'}

    interactive_commit = fixture['interactiveCommit']
    commit_requests = interactive_commit['requests']
    commit_responses = interactive_commit['responses']
    started_transaction_id = commit_responses[0]['result']['transactionId']

    assert commit_requests[0]['method'] == 'transaction.start'
    assert commit_requests[0]['params'] == {'timeoutMs': 5000, 'maxWaitMs': 2000, 'isolationLevel': None}
    assert {request.get('transactionId') for request in commit_requests[1:]} == {started_transaction_id}
    assert [request['method'] for request in commit_requests[1:]] == [
        'query.execute',
        'query.execute',
        'transaction.commit',
    ]
    assert result(commit_responses[-1]) == {'status': 'committed'}

    rollback = fixture['interactiveRollbackOnPythonException']
    rollback_requests = rollback['requests']
    rollback_responses = rollback['responses']
    rollback_id = rollback_responses[0]['result']['transactionId']
    assert rollback_requests[-1]['method'] == 'transaction.rollback'
    assert rollback_requests[-1]['transactionId'] == rollback_id
    assert rollback_requests[-1]['params']['reason'] == 'python-exception'
    assert result(rollback_responses[-1]) == {'status': 'rolled_back'}

    timeout = fixture['timeoutTaintsTransaction']['response']
    assert error_code(timeout) == 'BRIDGE_TIMEOUT'
    assert error_meta(timeout)['tainted'] is True
    assert timeout['error']['retryable'] is False

    cancellation = fixture['interactiveCancellationRollsBack']
    cancellation_requests = cancellation['requests']
    assert [request['method'] for request in cancellation_requests] == [
        'transaction.start',
        'bridge.cancel',
        'transaction.rollback',
    ]
    assert cancellation_requests[1]['params']['targetRequestId'] == 'req_tx_query_cancel'
    assert cancellation_requests[2]['params']['reason'] == 'python-cancellation'
    assert cancellation['responses'][1]['result']['transactionState'] == 'cancelled'
    assert cancellation['responses'][2]['result']['rollbackOutcome'] == 'confirmed'

    bridge_death = fixture['bridgeDeathMarksTransactionLost']['response']
    assert error_code(bridge_death) == 'BRIDGE_PROCESS_EXITED'
    assert error_meta(bridge_death)['transactionState'] == 'lost'
    assert error_meta(bridge_death)['rollbackOutcome'] == 'unknown'
    assert bridge_death['error']['retryable'] is False

    disconnect = fixture['disconnectRollsBackOpenTransaction']['responses'][-1]
    assert result(disconnect)['connected'] is False
    assert result(disconnect)['rolledBackTransactionIds'] == ['tx_phase0_disconnect']
    assert result(disconnect)['rollbackOutcome'] == 'confirmed'

    unsafe_disconnect = fixture['disconnectRollbackTimeoutUnsafeShutdown']['response']
    assert error_code(unsafe_disconnect) == 'BRIDGE_SHUTDOWN_UNSAFE'
    assert error_meta(unsafe_disconnect)['safeToReuseBridge'] is False

    nested = fixture['nestedTransactionUnsupported']['response']
    assert error_code(nested) == 'TRANSACTION_NESTED_UNSUPPORTED'
    assert error_meta(nested)['outerTransactionId'] == 'tx_phase0_outer'

    closed_reuse = fixture['closedTransactionIdReuseRejected']['response']
    assert error_code(closed_reuse) == 'TRANSACTION_CLOSED'
    assert error_meta(closed_reuse)['transactionState'] == 'committed'


def test_query_batch_fixture_uses_atomic_batch_protocol_and_rollback_metadata() -> None:
    fixture = load_fixture('transaction-lifecycle.json')

    batch = fixture['batchTransaction']
    batch_request = batch['request']
    batch_response = batch['response']
    assert batch_request['method'] == 'query.batch'
    assert 'transactionId' not in batch_request
    assert batch_request['params']['isolationLevel'] is None
    assert [operation['action'] for operation in batch_request['params']['operations']] == ['create', 'count']
    assert isinstance(result(batch_response), list)
    assert len(result(batch_response)) == 2

    failed_batch = fixture['batchFailureRollsBack']
    failed_response = failed_batch['response']
    assert failed_batch['request']['method'] == 'query.batch'
    assert [operation['action'] for operation in failed_batch['request']['params']['operations']] == [
        'create',
        'create',
    ]
    assert error_code(failed_response) == 'PRISMA_KNOWN_REQUEST_ERROR'
    assert error_meta(failed_response)['operationIndex'] == 1
    assert error_meta(failed_response)['rollbackOutcome'] == 'confirmed'
    assert failed_response['error']['prismaCode'] == 'P2002'
    assert failed_response['error']['retryable'] is False


def test_postgresql_provider_metadata_and_missing_adapter_guard_are_fixture_backed() -> None:
    lifecycle = load_fixture('protocol-lifecycle.json')
    ready = lifecycle['stdout'][0]
    assert ready['method'] == 'bridge.ready'
    assert ready['params']['provider'] == 'postgresql'
    assert ready['params']['adapter'] == '@prisma/adapter-pg'

    query = load_fixture('query-success.json')
    assert query['scenario']['provider'] == 'postgresql'
    assert query['scenario']['adapter'] == '@prisma/adapter-pg'
    assert query['response']['meta']['provider'] == 'postgresql'
    assert query['response']['meta']['adapter'] == '@prisma/adapter-pg'

    errors = load_fixture('error-mapping.json')
    missing_adapter = errors['scenarios'][1]['response']
    assert error_code(missing_adapter) == 'ADAPTER_NOT_FOUND'
    assert error_meta(missing_adapter)['provider'] == 'postgresql'
    assert error_meta(missing_adapter)['package'] == '@prisma/adapter-pg'
    assert error_meta(missing_adapter)['install'] == 'npm install @prisma/adapter-pg pg'
    assert missing_adapter['error']['retryable'] is False


def test_provider_support_matrix_keeps_postgresql_first_and_other_providers_deferred() -> None:
    rows = list(markdown_table_rows(PHASE0 / 'adapter-support-matrix.md'))
    by_target = {row[0]: row for row in rows if len(row) >= 4}

    postgresql = by_target['Self-hosted PostgreSQL']
    assert postgresql[1] == '`postgresql`'
    assert '@prisma/adapter-pg' in postgresql[2]
    assert postgresql[3] == 'First'
    assert 'batch transaction, interactive transaction' in postgresql[5]

    assert by_target['Local SQLite file'][1] == '`sqlite`'
    assert by_target['Local SQLite file'][3] == 'Deferred'
    assert by_target['Self-hosted MySQL / MariaDB'][1] == '`mysql`'
    assert by_target['Self-hosted MySQL / MariaDB'][3] == 'Deferred'
    assert by_target['MongoDB'][1] == '`mongodb`'
    assert by_target['MongoDB'][3] == 'Unsupported'


def test_js_bridge_no_rust_spawn_and_deferred_provider_guards_are_documented() -> None:
    protocol = (PHASE0 / 'bridge-protocol.md').read_text(encoding='utf-8')
    compatibility = (PHASE0 / 'compatibility-matrix.md').read_text(encoding='utf-8')
    ci_plan = (PHASE0 / 'ci-plan.md').read_text(encoding='utf-8')
    adapter_matrix = (PHASE0 / 'adapter-support-matrix.md').read_text(encoding='utf-8')

    assert 'Do not depend on Rust query-engine HTTP endpoints' in protocol
    assert '`PRISMA_PY_ENGINE=js-bridge` does not spawn the Rust query engine' in compatibility
    assert 'does not spawn Rust query-engine binaries' in ci_plan
    assert '`Deferred` and `Unsupported` providers must not silently' in adapter_matrix
    assert 'JS bridge startup must fail' in adapter_matrix
    assert 'before any query is sent' in adapter_matrix


def test_postgresql_js_bridge_contract_ci_job_is_service_backed() -> None:
    workflow = (ROOT / '.github' / 'workflows' / 'test.yml').read_text(encoding='utf-8')

    assert 'postgres-js-bridge-contracts:' in workflow
    assert 'image: postgres:15' in workflow
    assert 'POSTGRES_USER: postgres' in workflow
    assert 'POSTGRES_PASSWORD: prisma' in workflow
    assert 'PRISMA_PY_ENGINE: js-bridge' in workflow
    assert 'POSTGRESQL_URL: postgresql://postgres:prisma@localhost:5432/prisma' in workflow
    assert (
        'python -m pytest --confcutdir=tests/prisma7_js_bridge_contracts tests/prisma7_js_bridge_contracts' in workflow
    )
