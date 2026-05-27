import signal
import asyncio
import decimal
import warnings
import threading
import contextlib
from io import StringIO
from typing import Any, Dict, List, Optional, Generator, cast
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing_extensions import override

import pytest
from pytest_subprocess import FakeProcess
from _pytest.monkeypatch import MonkeyPatch

from prisma import BINARY_PATHS, Prisma, config, errors as prisma_errors, fields
from prisma.utils import temp_env_update
from prisma._types import TransactionId
from prisma.engine import utils, errors
from prisma._compat import get_running_loop
from prisma.binaries import platform
from prisma.engine.query import QueryEngine
from prisma._transactions import SyncTransactionManager, AsyncTransactionManager
from prisma.engine._js_bridge import (
    _STDERR_TAIL_LIMIT,
    SyncJSBridgeEngine,
    AsyncJSBridgeEngine,
    get_engine_mode,
    serialize_bridge_value,
    deserialize_bridge_value,
    _bridge_error_to_exception,
    bridge_raw_rows_to_legacy_result,
)

from .utils import Testdir, skipif_windows


@contextlib.contextmanager
def no_event_loop() -> Generator[None, None, None]:
    try:
        current: Optional[asyncio.AbstractEventLoop] = get_running_loop()
    except RuntimeError:
        current = None

    # if there is no running loop then we don't touch the event loop
    # as this can cause weird issues breaking other tests
    if not current:  # pragma: no cover
        yield
    else:  # pragma: no cover
        try:
            asyncio.set_event_loop(None)
            yield
        finally:
            asyncio.set_event_loop(current)


@pytest.mark.asyncio
async def test_engine_connects() -> None:
    """Can connect to engine"""
    db = Prisma()
    await db.connect()

    with pytest.raises(errors.AlreadyConnectedError):
        await db.connect()

    await db.disconnect()


@pytest.mark.asyncio
@skipif_windows
async def test_engine_process_sigint_mask() -> None:
    """Block SIGINT in current process"""
    signal.pthread_sigmask(signal.SIG_BLOCK, [signal.SIGINT])
    db = Prisma()
    await db.connect()

    with pytest.raises(errors.AlreadyConnectedError):
        await db.connect()

    await asyncio.wait_for(db.disconnect(), timeout=5)
    signal.pthread_sigmask(signal.SIG_UNBLOCK, [signal.SIGINT])


@pytest.mark.asyncio
@skipif_windows
async def test_engine_process_sigterm_mask() -> None:
    """Block SIGTERM in current process"""
    signal.pthread_sigmask(signal.SIG_BLOCK, [signal.SIGTERM])
    db = Prisma()
    await db.connect()

    with pytest.raises(errors.AlreadyConnectedError):
        await db.connect()

    await asyncio.wait_for(db.disconnect(), timeout=5)
    signal.pthread_sigmask(signal.SIG_UNBLOCK, [signal.SIGTERM])


def test_stopping_engine_on_closed_loop() -> None:
    """Stopping the engine with no event loop available does not raise an error"""
    with no_event_loop():
        engine = QueryEngine(dml_path=Path.cwd())
        engine.stop()


def test_get_engine_mode_validates_flag_values() -> None:
    assert get_engine_mode() == 'rust-legacy'

    with temp_env_update({'PRISMA_PY_ENGINE': ' js-bridge '}):
        assert get_engine_mode() == 'js-bridge'

    with temp_env_update({'PRISMA_PY_ENGINE': 'rust-legacy'}):
        assert get_engine_mode() == 'rust-legacy'

    with temp_env_update({'PRISMA_PY_ENGINE': 'binary'}):
        with pytest.raises(errors.InvalidEngineModeError) as exc:
            get_engine_mode()

    assert exc.value.value == 'binary'


class FakeJSBridgeProcess:
    def __init__(self) -> None:
        self.stdin = StringIO()
        self.stdout = StringIO(
            '\n'.join(
                [
                    '{"method":"bridge.ready","params":{"protocolVersion":"2026-05-26.phase0.v1","provider":"postgresql"}}',
                    '{"id":"req_connect_1","result":{"status":"connected"},"meta":{"protocolVersion":"2026-05-26.phase0.v1"}}',
                    '{"id":"req_disconnect_2","result":{"status":"disconnected"},"meta":{"protocolVersion":"2026-05-26.phase0.v1"}}',
                    '{"id":"req_shutdown_3","result":{"status":"shutdown"},"meta":{"protocolVersion":"2026-05-26.phase0.v1"}}',
                ]
            )
            + '\n'
        )
        self.stderr = StringIO()
        self.returncode: Optional[int] = None
        self.args: Optional[List[str]] = None
        self.env: Optional[Dict[str, str]] = None
        self.terminated = False
        self.killed = False

    def poll(self) -> Optional[int]:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def wait(self, timeout: Optional[float] = None) -> int:  # noqa: ARG002
        self.returncode = 0
        return 0


class BlockingJSBridgeStdout(StringIO):
    def __init__(self, line: str) -> None:
        super().__init__(line)
        self._release = threading.Event()

    @override
    def readline(self, size: int = -1, /) -> str:
        self._release.wait(timeout=5)
        return super().readline(size)

    def release(self) -> None:
        self._release.set()


class TimeoutJSBridgeProcess(FakeJSBridgeProcess):
    def __init__(self) -> None:
        super().__init__()
        self.stdout = cast(
            StringIO,
            BlockingJSBridgeStdout(
                '{"id":"req_query_1","result":{"late":true},"meta":{"protocolVersion":"2026-05-26.phase0.v1"}}\n'
            ),
        )


class FakeTransactionClient:
    def __init__(self, engine: object, tx_id: Optional[TransactionId] = None) -> None:
        self._engine = engine
        self._tx_id = tx_id

    def is_transaction(self) -> bool:
        return self._tx_id is not None

    def _copy(self) -> 'FakeTransactionClient':
        return FakeTransactionClient(self._engine)


class FakeSyncLegacyTransactionEngine:
    def __init__(self) -> None:
        self.started = 0
        self.last_content: Optional[str] = None

    def start_transaction(self, *, content: str) -> TransactionId:
        self.started += 1
        self.last_content = content
        return TransactionId('inner_sync_tx')


class FakeAsyncLegacyTransactionEngine:
    def __init__(self) -> None:
        self.started = 0
        self.last_content: Optional[str] = None

    async def start_transaction(self, *, content: str) -> TransactionId:
        self.started += 1
        self.last_content = content
        return TransactionId('inner_async_tx')


def test_sync_js_bridge_nested_transaction_raises_deterministic_error(tmp_path: Path) -> None:
    engine = SyncJSBridgeEngine(dml_path=tmp_path / 'schema.prisma', provider='postgresql')
    client = FakeTransactionClient(engine, TransactionId('outer_sync_tx'))
    manager = SyncTransactionManager(
        client=cast(Any, client),
        max_wait=timedelta(seconds=1),
        timeout=timedelta(seconds=2),
    )

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter('always')
        with pytest.raises(errors.JSBridgeError) as exc:
            manager.start()

    assert captured == []
    assert exc.value.code == 'TRANSACTION_NESTED_UNSUPPORTED'
    assert exc.value.meta == {'outerTransactionId': 'outer_sync_tx'}


@pytest.mark.asyncio
async def test_async_js_bridge_nested_transaction_raises_deterministic_error(tmp_path: Path) -> None:
    engine = AsyncJSBridgeEngine(dml_path=tmp_path / 'schema.prisma', provider='postgresql')
    client = FakeTransactionClient(engine, TransactionId('outer_async_tx'))
    manager = AsyncTransactionManager(
        client=cast(Any, client),
        max_wait=timedelta(seconds=1),
        timeout=timedelta(seconds=2),
    )

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter('always')
        with pytest.raises(errors.JSBridgeError) as exc:
            await manager.start()

    assert captured == []
    assert exc.value.code == 'TRANSACTION_NESTED_UNSUPPORTED'
    assert exc.value.meta == {'outerTransactionId': 'outer_async_tx'}


def test_sync_legacy_nested_transaction_still_warns() -> None:
    engine = FakeSyncLegacyTransactionEngine()
    client = FakeTransactionClient(engine, TransactionId('outer_sync_tx'))
    manager = SyncTransactionManager(
        client=cast(Any, client),
        max_wait=timedelta(seconds=1),
        timeout=timedelta(seconds=2),
    )

    with pytest.warns(UserWarning, match='already in a transaction'):
        tx_client = manager.start()

    assert engine.started == 1
    assert engine.last_content is not None
    assert tx_client._tx_id == TransactionId('inner_sync_tx')


@pytest.mark.asyncio
async def test_async_legacy_nested_transaction_still_warns() -> None:
    engine = FakeAsyncLegacyTransactionEngine()
    client = FakeTransactionClient(engine, TransactionId('outer_async_tx'))
    manager = AsyncTransactionManager(
        client=cast(Any, client),
        max_wait=timedelta(seconds=1),
        timeout=timedelta(seconds=2),
    )

    with pytest.warns(UserWarning, match='already in a transaction'):
        tx_client = await manager.start()

    assert engine.started == 1
    assert engine.last_content is not None
    assert tx_client._tx_id == TransactionId('inner_async_tx')


def test_js_bridge_process_exit_error_includes_stderr_tail(tmp_path: Path) -> None:
    process = FakeJSBridgeProcess()
    process.stdout = StringIO()
    process.stderr = StringIO('bridge diagnostic before exit\n')
    engine = SyncJSBridgeEngine(dml_path=tmp_path / 'schema.prisma', provider='postgresql')
    engine.process = cast(Any, process)  # bypass connect; this test targets local transport diagnostics only
    engine._start_stderr_reader(cast(Any, process))
    thread = engine._stderr_thread
    assert thread is not None
    thread.join(timeout=5)

    with pytest.raises(errors.JSBridgeError) as exc:
        engine._request('query.graphql', {}, timeout_ms=1, request_id_prefix='req_query')

    assert exc.value.code == 'BRIDGE_PROCESS_EXITED'
    assert exc.value.meta['stderrTail'] == 'bridge diagnostic before exit\n'
    assert 'transactionId' not in exc.value.meta
    assert 'transactionState' not in exc.value.meta
    assert 'rollbackOutcome' not in exc.value.meta
    assert exc.value.retryable is False


def test_js_bridge_process_exit_error_marks_transaction_lost(tmp_path: Path) -> None:
    process = FakeJSBridgeProcess()
    process.stdout = StringIO()
    process.stderr = StringIO('bridge diagnostic before tx exit\n')
    engine = SyncJSBridgeEngine(dml_path=tmp_path / 'schema.prisma', provider='postgresql')
    engine.process = cast(Any, process)  # bypass connect; this test targets local transport diagnostics only
    engine._start_stderr_reader(cast(Any, process))
    thread = engine._stderr_thread
    assert thread is not None
    thread.join(timeout=5)

    with pytest.raises(errors.JSBridgeError) as exc:
        engine._request(
            'query.graphql',
            {},
            tx_id=TransactionId('tx_process_exit'),
            timeout_ms=1,
            request_id_prefix='req_query',
        )

    assert exc.value.code == 'BRIDGE_PROCESS_EXITED'
    assert exc.value.meta['transactionId'] == 'tx_process_exit'
    assert exc.value.meta['transactionState'] == 'lost'
    assert exc.value.meta['rollbackOutcome'] == 'unknown'
    assert exc.value.meta['stderrTail'] == 'bridge diagnostic before tx exit\n'
    assert exc.value.retryable is False


def test_js_bridge_stderr_tail_is_bounded(tmp_path: Path) -> None:
    process = FakeJSBridgeProcess()
    process.stderr = StringIO(f'{"x" * (_STDERR_TAIL_LIMIT + 100)}tail')
    engine = SyncJSBridgeEngine(dml_path=tmp_path / 'schema.prisma', provider='postgresql')

    engine._start_stderr_reader(cast(Any, process))
    thread = engine._stderr_thread
    assert thread is not None
    thread.join(timeout=5)

    tail = engine._stderr_tail_text()
    assert tail is not None
    assert len(tail) <= _STDERR_TAIL_LIMIT
    assert tail.endswith('tail')


def test_js_bridge_request_timeout_closes_process_before_late_response(tmp_path: Path) -> None:
    process = TimeoutJSBridgeProcess()
    engine = SyncJSBridgeEngine(dml_path=tmp_path / 'schema.prisma', provider='postgresql')
    engine.process = cast(Any, process)  # bypass connect; this test targets request timeout cleanup only

    with pytest.raises(errors.JSBridgeError) as exc:
        engine._request('query.graphql', {}, timeout_ms=1, request_id_prefix='req_query')

    cast(BlockingJSBridgeStdout, process.stdout).release()

    assert exc.value.code == 'BRIDGE_TIMEOUT'
    assert process.terminated is True
    assert engine.process is None

    with pytest.raises(errors.NotConnectedError):
        engine._request('bridge.healthcheck', {}, timeout_ms=1, request_id_prefix='req_health')


def test_js_bridge_request_total_deadline_ignores_stray_ready_frames(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    process = FakeJSBridgeProcess()
    engine = SyncJSBridgeEngine(dml_path=tmp_path / 'schema.prisma', provider='postgresql')
    engine.process = cast(Any, process)  # bypass connect; patched I/O targets request deadline behavior only
    monotonic_values = iter([100.0, 100.0, 100.002])
    read_timeouts: list[timedelta] = []

    def read_stdout(timeout: timedelta) -> dict[str, Any]:
        read_timeouts.append(timeout)
        return {'method': 'bridge.ready'}

    monkeypatch.setattr('prisma.engine._js_bridge.time.monotonic', lambda: next(monotonic_values))
    monkeypatch.setattr(engine, '_read_stdout', read_stdout)

    with pytest.raises(errors.JSBridgeError) as exc:
        engine._request('query.graphql', {}, timeout_ms=1, request_id_prefix='req_query')

    assert exc.value.code == 'BRIDGE_TIMEOUT'
    assert process.terminated is True
    assert engine.process is None
    assert len(read_timeouts) == 1
    assert read_timeouts[0].total_seconds() == pytest.approx(0.001)


def test_js_bridge_request_serializes_concurrent_threads(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    engine = SyncJSBridgeEngine(dml_path=tmp_path / 'schema.prisma', provider='postgresql')
    engine.process = cast(Any, FakeJSBridgeProcess())  # bypass connect; patched I/O targets request serialization only

    writes: list[str] = []
    results: dict[str, Any] = {}
    failures: list[BaseException] = []
    writes_lock = threading.Lock()
    read_calls_lock = threading.Lock()
    first_read_started = threading.Event()
    release_first_response = threading.Event()
    second_request_written = threading.Event()
    read_calls = [0]

    def write_request(request: dict[str, Any]) -> None:
        request_id = str(request['id'])
        with writes_lock:
            writes.append(request_id)
        if request_id == 'req_query_2':
            second_request_written.set()

    def read_stdout(_timeout: timedelta) -> dict[str, Any]:
        with read_calls_lock:
            read_calls[0] += 1
            call = read_calls[0]

        if call == 1:
            first_read_started.set()
            assert release_first_response.wait(timeout=5)
            return {'id': 'req_query_1', 'result': {'call': 1}}

        return {'id': 'req_query_2', 'result': {'call': 2}}

    def request(label: str) -> None:
        try:
            results[label] = engine._request('query.graphql', {'label': label}, request_id_prefix='req_query')
        except BaseException as exc:  # pragma: no cover - surfaced by assertions below
            failures.append(exc)

    first = threading.Thread(target=request, args=('first',), daemon=True)
    second = threading.Thread(target=request, args=('second',), daemon=True)
    monkeypatch.setattr(engine, '_write_request', write_request)
    monkeypatch.setattr(engine, '_read_stdout', read_stdout)

    first.start()
    assert first_read_started.wait(timeout=5)

    try:
        second.start()
        assert not second_request_written.wait(timeout=0.1)
    finally:
        release_first_response.set()

    first.join(timeout=5)
    second.join(timeout=5)

    assert not first.is_alive()
    assert not second.is_alive()
    assert failures == []
    assert writes == ['req_query_1', 'req_query_2']
    assert results == {'first': {'call': 1}, 'second': {'call': 2}}


def test_js_bridge_does_not_spawn_rust_binary(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    bridge = tmp_path / 'bridge.mjs'
    bridge.write_text('process.exit(0)')
    fake_process = FakeJSBridgeProcess()

    def popen(args: list[str], **kwargs: object) -> FakeJSBridgeProcess:
        fake_process.args = args
        fake_process.env = kwargs.get('env')  # type: ignore[assignment]
        return fake_process

    def ensure(*args: object, **kwargs: object) -> None:
        raise AssertionError('Rust query engine binary resolution should not run in JS bridge mode')

    monkeypatch.setattr('prisma.engine._js_bridge.subprocess.Popen', popen)
    monkeypatch.setattr(utils, 'ensure', ensure)

    with temp_env_update({'PRISMA_PY_ENGINE': 'js-bridge', 'PRISMA_PY_JS_BRIDGE_SCRIPT': str(bridge)}):
        engine = SyncJSBridgeEngine(dml_path=tmp_path / 'schema.prisma', provider='postgresql')
        engine.connect(timeout=timedelta(seconds=1))
        engine.close(timeout=timedelta(seconds=1))

    assert fake_process.args is not None
    assert 'prisma-query-engine' not in ' '.join(fake_process.args)
    assert fake_process.env is not None
    assert fake_process.env['PRISMA_PY_BRIDGE_PROVIDER'] == 'postgresql'
    assert 'PRISMA_QUERY_ENGINE_BINARY' not in fake_process.env
    written = fake_process.stdin.getvalue()
    assert '"method": "client.connect"' in written
    assert '"method": "client.disconnect"' in written
    assert '"method": "bridge.shutdown"' in written


def test_js_bridge_default_script_matches_generated_package(tmp_path: Path) -> None:
    generated = tmp_path / 'js_bridge'
    generated.mkdir()
    runtime = generated / 'runtime.mjs'
    runtime.write_text('process.exit(0)')

    engine = SyncJSBridgeEngine(dml_path=tmp_path / 'schema.prisma', provider='postgresql')

    assert engine._bridge_script() == runtime


def test_js_bridge_generated_package_requires_prisma_client_output(tmp_path: Path) -> None:
    generated = tmp_path / 'js_bridge'
    generated.mkdir()
    generated.joinpath('package.json').write_text('{"private": true}')

    engine = SyncJSBridgeEngine(dml_path=tmp_path / 'schema.prisma', provider='postgresql')

    with pytest.raises(errors.JSBridgeError) as exc:
        engine._prepare_generated_package(generated)

    assert exc.value.code == 'PRISMA_CLIENT_NOT_FOUND'
    assert 'npm install && npm run generate' in str(exc.value)


def test_js_bridge_generated_package_requires_node_dependencies(tmp_path: Path) -> None:
    generated = tmp_path / 'js_bridge'
    generated.joinpath('generated', 'prisma').mkdir(parents=True)
    generated.joinpath('package.json').write_text('{"private": true}')
    generated.joinpath('runtime.mjs').write_text('process.exit(0)')
    generated.joinpath('generated', 'prisma', 'client.ts').write_text('export class PrismaClient {}')

    engine = SyncJSBridgeEngine(dml_path=tmp_path / 'schema.prisma', provider='postgresql')

    with pytest.raises(errors.JSBridgeError) as exc:
        engine._prepare_generated_package(generated)

    assert exc.value.code == 'JS_BRIDGE_DEPENDENCIES_NOT_FOUND'
    assert exc.value.meta['package'] == 'tsx'
    assert exc.value.meta['install'] == 'npm install && npm run generate'
    assert 'JS bridge Node dependencies are not installed' in str(exc.value)


def test_js_bridge_generated_package_uses_local_ts_client(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    generated = tmp_path / 'js_bridge'
    generated.joinpath('generated', 'prisma').mkdir(parents=True)
    generated.joinpath('node_modules', 'tsx').mkdir(parents=True)
    generated.joinpath('package.json').write_text('{"private": true}')
    runtime = generated / 'runtime.mjs'
    runtime.write_text('process.exit(0)')
    generated.joinpath('generated', 'prisma', 'client.ts').write_text('export class PrismaClient {}')
    fake_process = FakeJSBridgeProcess()

    def popen(args: list[str], **kwargs: object) -> FakeJSBridgeProcess:
        fake_process.args = args
        fake_process.env = kwargs.get('env')  # type: ignore[assignment]
        return fake_process

    monkeypatch.setattr('prisma.engine._js_bridge.subprocess.Popen', popen)

    engine = SyncJSBridgeEngine(dml_path=tmp_path / 'schema.prisma', provider='postgresql')
    engine.connect(timeout=timedelta(seconds=1))
    engine.close(timeout=timedelta(seconds=1))

    assert fake_process.args == ['node', '--import', 'tsx', 'runtime.mjs']
    assert fake_process.env is not None
    assert fake_process.env['PRISMA_PY_BRIDGE_CLIENT_MODULE'] == './generated/prisma/client.ts'


def test_js_bridge_node_binary_override_reports_missing_node(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    bridge = tmp_path / 'bridge.mjs'
    bridge.write_text('process.exit(0)')

    def popen(*args: object, **kwargs: object) -> FakeJSBridgeProcess:  # noqa: ARG001
        raise FileNotFoundError('missing node')

    monkeypatch.setattr('prisma.engine._js_bridge.subprocess.Popen', popen)

    with temp_env_update(
        {
            'PRISMA_PY_NODE_BINARY': 'missing-node-for-prisma-py',
            'PRISMA_PY_JS_BRIDGE_SCRIPT': str(bridge),
        }
    ):
        engine = SyncJSBridgeEngine(dml_path=tmp_path / 'schema.prisma', provider='postgresql')
        with pytest.raises(errors.JSBridgeError) as exc:
            engine._spawn_process()

    assert exc.value.code == 'NODE_NOT_FOUND'
    assert exc.value.meta == {'executable': 'missing-node-for-prisma-py'}


def test_js_bridge_deferred_provider_fails_before_node_spawn(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    def spawn(self: SyncJSBridgeEngine) -> FakeJSBridgeProcess:  # noqa: ARG001
        raise AssertionError('Deferred providers must fail before starting Node')

    monkeypatch.setattr(SyncJSBridgeEngine, '_spawn_process', spawn)

    engine = SyncJSBridgeEngine(dml_path=tmp_path / 'schema.prisma', provider='sqlite')
    with pytest.raises(errors.JSBridgeError) as exc:
        engine.connect(timeout=timedelta(seconds=1))

    assert exc.value.code == 'PROVIDER_UNSUPPORTED'
    assert exc.value.meta == {'provider': 'sqlite', 'supported': ['postgresql']}


def test_js_bridge_error_mapping_matches_known_prisma_errors() -> None:
    exc = _bridge_error_to_exception(
        {
            'code': 'PRISMA_KNOWN_REQUEST_ERROR',
            'message': 'Unique constraint failed on the fields: (`email`)',
            'meta': {'target': ['email']},
            'prismaCode': 'P2002',
        }
    )

    assert isinstance(exc, prisma_errors.UniqueViolationError)
    assert exc.code == 'P2002'
    assert exc.meta == {'target': ['email']}


def test_js_bridge_error_mapping_uses_meta_kind_for_field_errors() -> None:
    exc = _bridge_error_to_exception(
        {
            'code': 'PRISMA_VALIDATION_ERROR',
            'message': 'Unknown argument `emali`.',
            'meta': {'kind': 'UnknownArgument', 'argumentPath': ['where', 'emali'], 'selectionPath': ['user']},
            'prismaCode': 'P2009',
        }
    )

    assert isinstance(exc, prisma_errors.FieldNotFoundError)


def test_js_bridge_error_mapping_preserves_required_value_and_transaction_errors() -> None:
    missing = _bridge_error_to_exception(
        {
            'code': 'PRISMA_VALIDATION_ERROR',
            'message': 'A value is required but not set',
            'meta': {'field': 'email'},
            'prismaCode': 'P2009',
        }
    )
    expired = _bridge_error_to_exception(
        {
            'code': 'PRISMA_KNOWN_REQUEST_ERROR',
            'message': 'Transaction already closed: timeout',
            'meta': {},
            'prismaCode': 'P2028',
        }
    )
    generic = _bridge_error_to_exception(
        {
            'code': 'PRISMA_KNOWN_REQUEST_ERROR',
            'message': 'Transaction API error',
            'meta': {},
            'prismaCode': 'P2028',
        }
    )

    assert isinstance(missing, prisma_errors.MissingRequiredValueError)
    assert missing.code == 'P2009'
    assert isinstance(expired, prisma_errors.TransactionExpiredError)
    assert isinstance(generic, prisma_errors.TransactionError)


def test_js_bridge_error_mapping_keeps_bridge_failures_as_js_bridge_errors() -> None:
    exc = _bridge_error_to_exception(
        {
            'code': 'BRIDGE_PROTOCOL_ERROR',
            'message': 'bad bridge frame',
            'meta': {'field': 'id'},
            'retryable': False,
        }
    )

    assert isinstance(exc, errors.JSBridgeError)
    assert exc.code == 'BRIDGE_PROTOCOL_ERROR'
    assert exc.meta == {'field': 'id'}


def test_js_bridge_error_mapping_preserves_debug_stderr_tail() -> None:
    exc = _bridge_error_to_exception(
        {
            'code': 'BRIDGE_PROTOCOL_ERROR',
            'message': 'bad bridge frame',
            'meta': {'field': 'id'},
            'debug': {'stderrTail': 'node diagnostic\n'},
            'retryable': False,
        }
    )

    assert isinstance(exc, errors.JSBridgeError)
    assert exc.meta == {'field': 'id', 'stderrTail': 'node diagnostic\n'}


def test_js_bridge_scalar_deserialization() -> None:
    assert str(deserialize_bridge_value({'$type': 'Decimal', 'value': '123.45'})) == '123.45'
    assert deserialize_bridge_value({'$type': 'BigInt', 'value': '9007199254740993'}) == 9007199254740993
    assert deserialize_bridge_value({'$type': 'Bytes', 'encoding': 'base64', 'value': 'AQID'}) == b'\x01\x02\x03'
    assert deserialize_bridge_value({'$type': 'DateTime', 'value': '2026-05-26T05:54:00.000Z'}).tzinfo is not None
    assert deserialize_bridge_value({'nested': [{'$type': 'JsonNull'}]}) == {'nested': [None]}


def test_js_bridge_scalar_serialization_tags_python_values_for_prisma_js_client() -> None:
    assert serialize_bridge_value(
        {
            'when': datetime(2026, 5, 26, 5, 54, 0, 123456, tzinfo=timezone.utc),
            'amount': decimal.Decimal('123.45'),
            'encoded': fields.Base64.encode(b'hello'),
            'raw': b'\x01\x02\x03',
            'json': fields.Json({'nested': [1, None, {'ok': True}]}),
        }
    ) == {
        'when': {'$type': 'DateTime', 'value': '2026-05-26T05:54:00.123000+00:00'},
        'amount': {'$type': 'Decimal', 'value': '123.45'},
        'encoded': {'$type': 'Bytes', 'encoding': 'base64', 'value': 'aGVsbG8='},
        'raw': {'$type': 'Bytes', 'encoding': 'base64', 'value': 'AQID'},
        'json': {'$type': 'Json', 'value': {'nested': [1, None, {'ok': True}]}},
    }


def test_js_bridge_raw_rows_to_legacy_result() -> None:
    result = bridge_raw_rows_to_legacy_result(
        [
            {
                'count': {'$type': 'BigInt', 'value': '42'},
                'max_created_at': {'$type': 'DateTime', 'value': '2026-05-26T05:54:00.000Z'},
            }
        ]
    )

    assert result == {
        'columns': ['count', 'max_created_at'],
        'types': ['bigint', 'datetime'],
        'rows': [['42', '2026-05-26T05:54:00.000Z']],
    }


def test_engine_binary_does_not_exist(monkeypatch: MonkeyPatch) -> None:
    """No query engine binary found raises an error"""

    def mock_exists(path: Path) -> bool:
        return False

    monkeypatch.setattr(Path, 'exists', mock_exists, raising=True)

    with pytest.raises(errors.BinaryNotFoundError) as exc:
        utils.ensure(BINARY_PATHS.query_engine)

    assert exc.match(
        r'Expected .*, .* or .* to exist but none were found or could not be executed\.\nTry running prisma py fetch'
    )


def test_engine_binary_does_not_exist_no_binary_paths(
    monkeypatch: MonkeyPatch,
) -> None:
    """No query engine binary found raises an error"""

    def mock_exists(path: Path) -> bool:
        return False

    monkeypatch.setattr(Path, 'exists', mock_exists, raising=True)

    with pytest.raises(errors.BinaryNotFoundError) as exc:
        utils.ensure({})

    assert exc.match(
        r'Expected .* or .* to exist but neither were found or could not be executed\.\nTry running prisma py fetch'
    )


def test_mismatched_version_error(fake_process: FakeProcess) -> None:
    """Mismatched query engine versions raises an error"""

    fake_process.register_subprocess(
        [
            str(utils._resolve_from_binary_paths(BINARY_PATHS.query_engine)),
            '--version',
        ],
        stdout='query-engine unexpected-hash',
    )

    with pytest.raises(errors.MismatchedVersionsError) as exc:
        utils.ensure(BINARY_PATHS.query_engine)

    assert exc.match(f'Expected query engine version `{config.expected_engine_version}` but got `unexpected-hash`')


def test_ensure_local_path(testdir: Testdir, fake_process: FakeProcess) -> None:
    """Query engine in current directory required to be the expected version"""

    fake_engine = testdir.path / platform.check_for_extension(f'prisma-query-engine-{platform.binary_platform()}')
    fake_engine.touch()

    fake_process.register_subprocess(
        [str(fake_engine), '--version'],
        stdout='query-engine a-different-hash',
    )
    with pytest.raises(errors.MismatchedVersionsError):
        path = utils.ensure(BINARY_PATHS.query_engine)

    fake_process.register_subprocess(
        [str(fake_engine), '--version'],
        stdout=f'query-engine {config.expected_engine_version}',
    )
    path = utils.ensure(BINARY_PATHS.query_engine)
    assert path == fake_engine


def test_ensure_env_override(testdir: Testdir, fake_process: FakeProcess) -> None:
    """Query engine path in environment variable can be any version"""
    fake_engine = testdir.path / 'my-query-engine'
    fake_engine.touch()

    fake_process.register_subprocess(
        [str(fake_engine), '--version'],
        stdout='query-engine a-different-hash',
    )

    with temp_env_update({'PRISMA_QUERY_ENGINE_BINARY': str(fake_engine)}):
        path = utils.ensure(BINARY_PATHS.query_engine)

    assert path == fake_engine


def test_ensure_env_override_does_not_exist() -> None:
    """Query engine path in environment variable not found raises an error"""
    with temp_env_update({'PRISMA_QUERY_ENGINE_BINARY': 'foo'}):
        with pytest.raises(errors.BinaryNotFoundError) as exc:
            utils.ensure(BINARY_PATHS.query_engine)

    assert exc.match(r'PRISMA_QUERY_ENGINE_BINARY was provided, but no query engine was found at foo')
