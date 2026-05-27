import signal
import asyncio
import decimal
import contextlib
from io import StringIO
from typing import Dict, List, Optional, Generator
from pathlib import Path
from datetime import datetime, timezone, timedelta

import pytest
from pytest_subprocess import FakeProcess
from _pytest.monkeypatch import MonkeyPatch

from prisma import BINARY_PATHS, Prisma, config, errors as prisma_errors, fields
from prisma.utils import temp_env_update
from prisma.engine import utils, errors
from prisma._compat import get_running_loop
from prisma.binaries import platform
from prisma.engine.query import QueryEngine
from prisma.engine._js_bridge import (
    SyncJSBridgeEngine,
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
