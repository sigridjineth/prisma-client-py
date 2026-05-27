from __future__ import annotations

import os
import json
import time
import queue
import atexit
import base64
import decimal
import logging
import threading
import subprocess
from typing import IO, TYPE_CHECKING, Any, overload
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing_extensions import Literal, override

from . import utils, errors
from .. import errors as prisma_errors, fields
from .._types import TransactionId
from .._compat import get_running_loop
from .._builder import dumps, serialize_datetime
from ._abstract import SyncAbstractEngine, AsyncAbstractEngine
from .._constants import DEFAULT_CONNECT_TIMEOUT

if TYPE_CHECKING:
    from ..types import MetricsFormat, DatasourceOverride  # noqa: TID251


__all__ = (
    'ENGINE_MODE_ENV',
    'JS_BRIDGE_SCRIPT_ENV',
    'JS_BRIDGE_NODE_ENV',
    'JS_BRIDGE_PROTOCOL_VERSION',
    'get_engine_mode',
    'serialize_bridge_value',
    'deserialize_bridge_value',
    'bridge_raw_rows_to_legacy_result',
    'SyncJSBridgeEngine',
    'AsyncJSBridgeEngine',
)


ENGINE_MODE_ENV = 'PRISMA_PY_ENGINE'
JS_BRIDGE_SCRIPT_ENV = 'PRISMA_PY_JS_BRIDGE_SCRIPT'
JS_BRIDGE_NODE_ENV = 'PRISMA_PY_NODE_BINARY'
JS_BRIDGE_CLIENT_MODULE_ENV = 'PRISMA_PY_BRIDGE_CLIENT_MODULE'
JS_BRIDGE_PROTOCOL_VERSION = '2026-05-26.phase0.v1'

_DEFAULT_ENGINE_MODE = 'rust-legacy'
_VALID_ENGINE_MODES = frozenset({_DEFAULT_ENGINE_MODE, 'js-bridge'})
_DEFAULT_REQUEST_TIMEOUT_MS = 30_000
_TIMED_OUT_REQUEST_CLOSE_TIMEOUT = timedelta(seconds=1)
_STDERR_TAIL_LIMIT = 8192

log: logging.Logger = logging.getLogger(__name__)


def get_engine_mode() -> Literal['rust-legacy', 'js-bridge']:
    value = os.environ.get(ENGINE_MODE_ENV, _DEFAULT_ENGINE_MODE).strip().lower()
    if value not in _VALID_ENGINE_MODES:
        raise errors.InvalidEngineModeError(value)

    return value  # type: ignore[return-value]


def _timeout_ms(timeout: timedelta | None, *, default: int = _DEFAULT_REQUEST_TIMEOUT_MS) -> int:
    if timeout is None:
        return default

    return max(1, int(timeout.total_seconds() * 1000))


def _parse_datetime(value: str) -> datetime:
    if value.endswith('Z'):
        value = f'{value[:-1]}+00:00'

    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def serialize_bridge_value(value: Any) -> Any:
    if isinstance(value, fields.Json):
        return {
            '$type': 'Json',
            'value': value.data,
        }
    if isinstance(value, fields.Base64):
        return {
            '$type': 'Bytes',
            'encoding': 'base64',
            'value': str(value),
        }
    if isinstance(value, datetime):
        return {
            '$type': 'DateTime',
            'value': serialize_datetime(value),
        }
    if isinstance(value, decimal.Decimal):
        return {
            '$type': 'Decimal',
            'value': str(value),
        }
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {
            '$type': 'Bytes',
            'encoding': 'base64',
            'value': base64.b64encode(bytes(value)).decode('ascii'),
        }
    if isinstance(value, list):
        return [serialize_bridge_value(item) for item in value]
    if isinstance(value, (tuple, set)):
        return [serialize_bridge_value(item) for item in value]
    if isinstance(value, dict):
        return {key: serialize_bridge_value(item) for key, item in value.items()}

    return value


def deserialize_bridge_value(value: Any) -> Any:
    if isinstance(value, list):
        return [deserialize_bridge_value(item) for item in value]

    if not isinstance(value, dict):
        return value

    tag = value.get('$type')
    if tag == 'Decimal':
        return decimal.Decimal(str(value['value']))
    if tag == 'BigInt':
        return int(value['value'])
    if tag == 'DateTime':
        return _parse_datetime(str(value['value']))
    if tag == 'Bytes':
        if value.get('encoding') != 'base64':
            raise TypeError('Bridge Bytes values must use base64 encoding.')
        return base64.b64decode(str(value['value']))
    if tag in {'JsonNull', 'DbNull'}:
        return None
    if tag == 'Json':
        return deserialize_bridge_value(value.get('value'))

    return {key: deserialize_bridge_value(item) for key, item in value.items()}


def _infer_raw_type(value: Any) -> str:
    if isinstance(value, dict):
        tag = value.get('$type')
        if tag == 'Decimal':
            return 'decimal'
        if tag == 'BigInt':
            return 'bigint'
        if tag == 'DateTime':
            return 'datetime'
        if tag == 'Bytes':
            return 'bytes'
        if tag in {'Json', 'JsonNull', 'DbNull'}:
            return 'json'
    if isinstance(value, bool):
        return 'bool'
    if isinstance(value, int):
        return 'int'
    if isinstance(value, float):
        return 'double'
    return 'unknown'


def _raw_value(value: Any) -> Any:
    if isinstance(value, dict):
        tag = value.get('$type')
        if tag in {'Decimal', 'BigInt', 'DateTime'}:
            return value['value']
        if tag == 'Bytes':
            return value['value']
        if tag in {'JsonNull', 'DbNull'}:
            return None
        if tag == 'Json':
            return value.get('value')

    return deserialize_bridge_value(value)


def bridge_raw_rows_to_legacy_result(rows: list[dict[str, Any]]) -> dict[str, Any]:
    columns = list(rows[0].keys()) if rows else []
    types = [_infer_raw_type(rows[0][column]) for column in columns] if rows else []
    return {
        'columns': columns,
        'types': types,
        'rows': [[_raw_value(row.get(column)) for column in columns] for row in rows],
    }


def _readline_with_timeout(stream: IO[str], timeout: timedelta) -> str:
    result: queue.Queue[str] = queue.Queue(maxsize=1)

    def read() -> None:
        result.put(stream.readline())

    thread = threading.Thread(target=read, daemon=True)
    thread.start()

    try:
        line = result.get(timeout=timeout.total_seconds())
    except queue.Empty as exc:
        raise TimeoutError() from exc

    if line == '':
        raise EOFError()

    return line


def _bridge_error_to_exception(data: dict[str, Any]) -> Exception:
    message = str(data.get('message') or 'An error occurred while communicating with the JS bridge.')
    raw_meta = data.get('meta')
    meta: dict[str, Any] = raw_meta.copy() if isinstance(raw_meta, dict) else {}
    raw_debug = data.get('debug')
    if isinstance(raw_debug, dict):
        stderr_tail = raw_debug.get('stderrTail')
        if isinstance(stderr_tail, str) and stderr_tail and 'stderrTail' not in meta:
            meta['stderrTail'] = stderr_tail
    prisma_code = data.get('prismaCode')
    retryable = bool(data.get('retryable', False))

    if isinstance(prisma_code, str):
        user_facing_error = {
            'user_facing_error': {
                'error_code': prisma_code,
                'message': message,
                'meta': meta,
            }
        }

        if prisma_code == 'P2028':
            if message.startswith('Transaction already closed'):
                return prisma_errors.TransactionExpiredError(message)
            return prisma_errors.TransactionError(message)

        if 'A value is required but not set' in message:
            return prisma_errors.MissingRequiredValueError(user_facing_error)

        error_cls: type[Exception] | None = None
        kind = meta.get('kind')
        if kind is not None:
            error_cls = utils.META_ERROR_MAPPING.get(kind)

        if error_cls is None:
            error_cls = utils.ERROR_MAPPING.get(prisma_code)

        if error_cls is not None:
            return error_cls(user_facing_error)

    return errors.JSBridgeError(
        code=str(data.get('code') or 'BRIDGE_ERROR'),
        message=message,
        meta=meta,
        prisma_code=prisma_code if isinstance(prisma_code, str) else None,
        retryable=retryable,
    )


class BaseJSBridgeEngine:
    engine_mode = 'js-bridge'

    dml_path: Path
    provider: str
    process: subprocess.Popen[str] | None

    def __init__(
        self,
        *,
        dml_path: Path,
        provider: str,
        log_queries: bool = False,
    ) -> None:
        self.dml_path = dml_path
        self.provider = provider
        self._log_queries = log_queries
        self._next_request_id = 0
        self._request_lock = threading.RLock()
        self.process = None
        self._stderr_tail = ''
        self._stderr_tail_lock = threading.Lock()
        self._stderr_thread: threading.Thread | None = None

    def _reset_stderr_tail(self) -> None:
        with self._stderr_tail_lock:
            self._stderr_tail = ''
        self._stderr_thread = None

    def _append_stderr_tail(self, chunk: str) -> None:
        if not chunk:
            return

        with self._stderr_tail_lock:
            self._stderr_tail = f'{self._stderr_tail}{chunk}'[-_STDERR_TAIL_LIMIT:]

    def _stderr_tail_text(self) -> str | None:
        with self._stderr_tail_lock:
            return self._stderr_tail or None

    def _stderr_meta(self, meta: dict[str, Any] | None = None) -> dict[str, Any] | None:
        stderr_tail = self._stderr_tail_text()
        if stderr_tail is None:
            return meta

        next_meta = meta.copy() if meta is not None else {}
        next_meta.setdefault('stderrTail', stderr_tail)
        return next_meta

    def _start_stderr_reader(self, process: subprocess.Popen[str]) -> None:
        stream = process.stderr
        if stream is None:
            return

        def read_stderr() -> None:
            try:
                while True:
                    chunk = stream.read(4096)
                    if chunk == '':
                        return
                    self._append_stderr_tail(chunk)
            except Exception as exc:  # pragma: no cover - diagnostic drain must not crash the client
                log.debug('JS bridge stderr reader stopped: %s', exc)

        thread = threading.Thread(target=read_stderr, daemon=True)
        self._stderr_thread = thread
        thread.start()

    def _bridge_script(self) -> Path:
        override = os.environ.get(JS_BRIDGE_SCRIPT_ENV)
        if override:
            return Path(override)

        generated_runtime = self.dml_path.parent / 'js_bridge' / 'runtime.mjs'
        if generated_runtime.exists():
            return generated_runtime

        return self.dml_path.parent / 'js-bridge' / 'index.mjs'

    def _generated_package_dir(self, script: Path) -> Path | None:
        package_dir = script.parent
        if package_dir.joinpath('package.json').exists() and package_dir.name == 'js_bridge':
            return package_dir
        return None

    def _prepare_generated_package(self, package_dir: Path) -> dict[str, str]:
        client_module = './generated/prisma/client.ts'
        client_path = package_dir / 'generated' / 'prisma' / 'client.ts'
        if not client_path.exists():
            raise errors.JSBridgeError(
                code='PRISMA_CLIENT_NOT_FOUND',
                message=(
                    'Generated Prisma 7 TypeScript client was not found for JS bridge mode. '
                    f'Run `npm install && npm run generate` in {package_dir}.'
                ),
                meta={'path': str(client_path), 'packageDir': str(package_dir)},
            )

        tsx_package = package_dir / 'node_modules' / 'tsx'
        if not tsx_package.exists():
            raise errors.JSBridgeError(
                code='JS_BRIDGE_DEPENDENCIES_NOT_FOUND',
                message=(
                    'JS bridge Node dependencies are not installed. '
                    f'Run `npm install && npm run generate` in {package_dir}.'
                ),
                meta={
                    'path': str(tsx_package),
                    'packageDir': str(package_dir),
                    'package': 'tsx',
                    'install': 'npm install && npm run generate',
                },
            )

        return {JS_BRIDGE_CLIENT_MODULE_ENV: client_module}

    def _spawn_process(self) -> subprocess.Popen[str]:
        script = self._bridge_script()
        if not script.exists():
            raise errors.JSBridgeError(
                code='PRISMA_CLIENT_NOT_FOUND',
                message=(
                    'Generated Prisma JS bridge entrypoint was not found. '
                    f'Set {JS_BRIDGE_SCRIPT_ENV} or re-run generation.'
                ),
                meta={'path': str(script)},
            )

        package_dir = self._generated_package_dir(script)
        env = os.environ.copy()
        env.update(
            PRISMA_PY_BRIDGE_SCHEMA_PATH=str(self.dml_path.absolute()),
            PRISMA_PY_BRIDGE_PROVIDER=self.provider,
            PRISMA_PY_BRIDGE_PROTOCOL_VERSION=JS_BRIDGE_PROTOCOL_VERSION,
        )
        if package_dir is not None:
            env.update(self._prepare_generated_package(package_dir))

        if self._log_queries:
            env.update(LOG_QUERIES='y')

        node = os.environ.get(JS_BRIDGE_NODE_ENV, 'node')
        args = [node]
        if package_dir is not None:
            args.extend(['--import', 'tsx'])
        args.append(script.name)
        try:
            process = subprocess.Popen(
                args,
                cwd=str(script.parent),
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError as exc:
            raise errors.JSBridgeError(
                code='NODE_NOT_FOUND',
                message='Node executable was not found for Prisma JS bridge mode.',
                meta={'executable': node},
            ) from exc

        self._reset_stderr_tail()
        self.process = process
        self._start_stderr_reader(process)
        return process

    def _close_process(self, *, timeout: timedelta | None = None) -> None:
        process = self.process
        if process is None:
            return

        total_seconds = timeout.total_seconds() if timeout is not None else None
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=total_seconds)
            except subprocess.TimeoutExpired:
                process.kill()

        self.process = None

    def _next_id(self, prefix: str) -> str:
        self._next_request_id += 1
        return f'{prefix}_{self._next_request_id}'

    def _write_request(self, request: dict[str, Any]) -> None:
        process = self.process
        if process is None or process.stdin is None:
            raise errors.NotConnectedError('Not connected to the JS bridge')

        line = dumps(request)
        log.debug('Sending JS bridge request: %s', line)
        process.stdin.write(f'{line}\n')
        process.stdin.flush()

    def _request_timeout_error(self) -> errors.JSBridgeError:
        return errors.JSBridgeError(
            code='BRIDGE_TIMEOUT',
            message='Timed out waiting for the JS bridge response.',
            meta=self._stderr_meta(),
            retryable=False,
        )

    def _read_stdout(self, timeout: timedelta) -> dict[str, Any]:
        process = self.process
        if process is None or process.stdout is None:
            raise errors.NotConnectedError('Not connected to the JS bridge')

        try:
            line = _readline_with_timeout(process.stdout, timeout=timeout)
        except TimeoutError as exc:
            raise self._request_timeout_error() from exc
        except EOFError as exc:
            raise errors.JSBridgeError(
                code='BRIDGE_PROCESS_EXITED',
                message='JS bridge exited before writing a response.',
                meta=self._stderr_meta(),
                retryable=False,
            ) from exc

        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise errors.JSBridgeError(
                code='BRIDGE_PROTOCOL_ERROR',
                message='Bridge wrote non-protocol data to stdout.',
                meta=self._stderr_meta({'stdoutLine': line.rstrip('\n')}),
                retryable=False,
            ) from exc

        if not isinstance(value, dict):
            raise errors.JSBridgeError(
                code='BRIDGE_PROTOCOL_ERROR',
                message='Bridge stdout JSON must be an object.',
                meta=self._stderr_meta({'stdoutValue': value}),
                retryable=False,
            )

        return value

    def _wait_until_ready(self, timeout: timedelta) -> None:
        deadline = time.monotonic() + timeout.total_seconds()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise errors.JSBridgeError(
                    code='BRIDGE_STARTUP_TIMEOUT',
                    message='Node bridge did not emit bridge.ready before the connect timeout.',
                    meta=self._stderr_meta(),
                    retryable=True,
                )

            data = self._read_stdout(timedelta(seconds=remaining))
            if data.get('method') == 'bridge.ready':
                params = data.get('params', {})
                if isinstance(params, dict):
                    protocol = params.get('protocolVersion')
                    if protocol and protocol != JS_BRIDGE_PROTOCOL_VERSION:
                        raise errors.JSBridgeError(
                            code='BRIDGE_PROTOCOL_ERROR',
                            message='Node bridge protocol version does not match the Python client.',
                            meta=self._stderr_meta({'expected': JS_BRIDGE_PROTOCOL_VERSION, 'got': protocol}),
                        )
                return

            raise errors.JSBridgeError(
                code='BRIDGE_PROTOCOL_ERROR',
                message='Expected bridge.ready as the first JS bridge stdout message.',
                meta=self._stderr_meta({'message': data}),
            )

    def _request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        tx_id: TransactionId | None = None,
        timeout_ms: int = _DEFAULT_REQUEST_TIMEOUT_MS,
        request_id_prefix: str = 'req',
    ) -> Any:
        with self._request_lock:
            request_id = self._next_id(request_id_prefix)
            request: dict[str, Any] = {
                'id': request_id,
                'method': method,
                'params': params,
                'timeoutMs': timeout_ms,
                'clientVersion': 'prisma-client-py',
            }
            if tx_id is not None:
                request['transactionId'] = str(tx_id)

            self._write_request(request)
            deadline = time.monotonic() + (timeout_ms / 1000)

            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._close_process(timeout=_TIMED_OUT_REQUEST_CLOSE_TIMEOUT)
                    raise self._request_timeout_error()

                try:
                    data = self._read_stdout(timedelta(seconds=remaining))
                except errors.JSBridgeError as exc:
                    if exc.code == 'BRIDGE_TIMEOUT':
                        self._close_process(timeout=_TIMED_OUT_REQUEST_CLOSE_TIMEOUT)
                    raise

                if data.get('method') == 'bridge.ready':
                    continue

                if data.get('id') != request_id:
                    raise errors.JSBridgeError(
                        code='BRIDGE_PROTOCOL_ERROR',
                        message='JS bridge response ID did not match the in-flight request.',
                        meta=self._stderr_meta({'expected': request_id, 'got': data.get('id')}),
                    )

                if ('result' in data) == ('error' in data):
                    raise errors.JSBridgeError(
                        code='BRIDGE_PROTOCOL_ERROR',
                        message='JS bridge response must contain exactly one of result or error.',
                        meta=self._stderr_meta({'id': request_id}),
                    )

                if 'error' in data:
                    error = data['error']
                    if not isinstance(error, dict):
                        raise errors.JSBridgeError(
                            code='BRIDGE_PROTOCOL_ERROR',
                            message='JS bridge error payload must be an object.',
                            meta=self._stderr_meta({'id': request_id}),
                        )
                    raise _bridge_error_to_exception(error)

                return data['result']

    def _connect(self, timeout: timedelta, datasources: list[DatasourceOverride] | None) -> None:
        if self.provider != 'postgresql':
            raise errors.JSBridgeError(
                code='PROVIDER_UNSUPPORTED',
                message='JS bridge mode currently supports only PostgreSQL.',
                meta={'provider': self.provider, 'supported': ['postgresql']},
            )

        if self.process is not None:
            raise errors.AlreadyConnectedError('Already connected to the JS bridge')

        self._spawn_process()
        try:
            self._wait_until_ready(timeout)
            datasource = None
            if datasources:
                if len(datasources) > 1:
                    raise errors.JSBridgeError(
                        code='DATASOURCE_OVERRIDE_UNSUPPORTED',
                        message='JS bridge mode accepts at most one datasource override.',
                    )
                datasource = datasources[0]

            self._request(
                'client.connect',
                {
                    'datasource': datasource,
                    'logQueries': self._log_queries,
                    'adapterOptions': {},
                },
                timeout_ms=_timeout_ms(timeout),
                request_id_prefix='req_connect',
            )
        except Exception:
            self._close_process(timeout=timeout)
            raise

    def _disconnect(self, timeout: timedelta | None) -> None:
        if self.process is None:
            return

        timeout_ms = _timeout_ms(timeout, default=5_000)
        try:
            self._request(
                'client.disconnect',
                {'rollbackOpenTransactions': True},
                timeout_ms=timeout_ms,
                request_id_prefix='req_disconnect',
            )
            self._request(
                'bridge.shutdown',
                {},
                timeout_ms=timeout_ms,
                request_id_prefix='req_shutdown',
            )
        except Exception as exc:
            log.debug('Graceful JS bridge shutdown failed: %s', exc)
        finally:
            self._close_process(timeout=timeout)

    def _legacy_query(self, content: str, *, tx_id: TransactionId | None) -> Any:
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise errors.JSBridgeError(
                code='BRIDGE_PROTOCOL_ERROR',
                message='Legacy query payload must be JSON in JS bridge mode.',
            ) from exc

        if not isinstance(payload, dict):
            raise errors.JSBridgeError(
                code='BRIDGE_PROTOCOL_ERROR',
                message='Legacy query payload must be a JSON object in JS bridge mode.',
            )

        return self._request(
            'query.graphql',
            payload,
            tx_id=tx_id,
            request_id_prefix='req_query',
        )

    def query_operation(
        self,
        *,
        operation: dict[str, Any],
        tx_id: TransactionId | None,
    ) -> Any:
        return self._request(
            operation['method'],
            operation['params'],
            tx_id=tx_id,
            request_id_prefix='req_query',
        )

    def _start_transaction(self, *, content: str) -> TransactionId:
        params = json.loads(content)
        if not isinstance(params, dict):
            raise errors.JSBridgeError(
                code='BRIDGE_PROTOCOL_ERROR',
                message='Transaction start payload must be a JSON object.',
            )

        timeout_ms = params.pop('timeout', None)
        max_wait_ms = params.pop('max_wait', None)
        result = self._request(
            'transaction.start',
            {
                'timeoutMs': timeout_ms,
                'maxWaitMs': max_wait_ms,
                'isolationLevel': params.get('isolation_level'),
            },
            timeout_ms=int((timeout_ms or 0) + (max_wait_ms or 0) or 7_000),
            request_id_prefix='req_tx_start',
        )
        return TransactionId(result['transactionId'])

    def _commit_transaction(self, tx_id: TransactionId) -> None:
        self._request(
            'transaction.commit',
            {},
            tx_id=tx_id,
            timeout_ms=5_000,
            request_id_prefix='req_tx_commit',
        )

    def _rollback_transaction(self, tx_id: TransactionId) -> None:
        self._request(
            'transaction.rollback',
            {'reason': 'python-rollback'},
            tx_id=tx_id,
            timeout_ms=5_000,
            request_id_prefix='req_tx_rollback',
        )

    def _metrics_error(self) -> errors.JSBridgeError:
        return errors.JSBridgeError(
            code='METRICS_UNSUPPORTED_IN_JS_BRIDGE',
            message='Metrics are not supported by JS bridge mode.',
            retryable=False,
        )


class SyncJSBridgeEngine(BaseJSBridgeEngine, SyncAbstractEngine):
    def __init__(
        self,
        *,
        dml_path: Path,
        provider: str,
        log_queries: bool = False,
    ) -> None:
        super().__init__(dml_path=dml_path, provider=provider, log_queries=log_queries)
        atexit.register(self.stop)

    @override
    def close(self, *, timeout: timedelta | None = None) -> None:
        self._disconnect(timeout=timeout)

    @override
    async def aclose(self, *, timeout: timedelta | None = None) -> None:
        self.close(timeout=timeout)

    @override
    def connect(
        self,
        timeout: timedelta = DEFAULT_CONNECT_TIMEOUT,
        datasources: list[DatasourceOverride] | None = None,
    ) -> None:
        self._connect(timeout=timeout, datasources=datasources)

    @override
    def query(self, content: str, *, tx_id: TransactionId | None) -> Any:
        return self._legacy_query(content, tx_id=tx_id)

    @override
    def start_transaction(self, *, content: str) -> TransactionId:
        return self._start_transaction(content=content)

    @override
    def commit_transaction(self, tx_id: TransactionId) -> None:
        self._commit_transaction(tx_id)

    @override
    def rollback_transaction(self, tx_id: TransactionId) -> None:
        self._rollback_transaction(tx_id)

    @overload
    def metrics(
        self,
        *,
        format: Literal['json'],
        global_labels: dict[str, str] | None,
    ) -> dict[str, Any]: ...

    @overload
    def metrics(
        self,
        *,
        format: Literal['prometheus'],
        global_labels: dict[str, str] | None,
    ) -> str: ...

    @override
    def metrics(
        self,
        *,
        format: MetricsFormat,
        global_labels: dict[str, str] | None,
    ) -> str | dict[str, Any]:
        raise self._metrics_error()


class AsyncJSBridgeEngine(BaseJSBridgeEngine, AsyncAbstractEngine):
    def __init__(
        self,
        *,
        dml_path: Path,
        provider: str,
        log_queries: bool = False,
    ) -> None:
        super().__init__(dml_path=dml_path, provider=provider, log_queries=log_queries)
        atexit.register(self.stop)

    async def _run_sync(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        loop = get_running_loop()
        return await loop.run_in_executor(None, lambda: func(*args, **kwargs))

    @override
    def close(self, *, timeout: timedelta | None = None) -> None:
        self._disconnect(timeout=timeout)

    @override
    async def aclose(self, *, timeout: timedelta | None = None) -> None:
        await self._run_sync(self.close, timeout=timeout)

    @override
    async def connect(
        self,
        timeout: timedelta = DEFAULT_CONNECT_TIMEOUT,
        datasources: list[DatasourceOverride] | None = None,
    ) -> None:
        await self._run_sync(self._connect, timeout=timeout, datasources=datasources)

    @override
    async def query(self, content: str, *, tx_id: TransactionId | None) -> Any:
        return await self._run_sync(self._legacy_query, content, tx_id=tx_id)

    async def aquery_operation(
        self,
        *,
        operation: dict[str, Any],
        tx_id: TransactionId | None,
    ) -> Any:
        return await self._run_sync(super().query_operation, operation=operation, tx_id=tx_id)

    @override
    async def start_transaction(self, *, content: str) -> TransactionId:
        return await self._run_sync(self._start_transaction, content=content)

    @override
    async def commit_transaction(self, tx_id: TransactionId) -> None:
        await self._run_sync(self._commit_transaction, tx_id)

    @override
    async def rollback_transaction(self, tx_id: TransactionId) -> None:
        await self._run_sync(self._rollback_transaction, tx_id)

    @overload
    async def metrics(
        self,
        *,
        format: Literal['json'],
        global_labels: dict[str, str] | None,
    ) -> dict[str, Any]: ...

    @overload
    async def metrics(
        self,
        *,
        format: Literal['prometheus'],
        global_labels: dict[str, str] | None,
    ) -> str: ...

    @override
    async def metrics(
        self,
        *,
        format: MetricsFormat,
        global_labels: dict[str, str] | None,
    ) -> str | dict[str, Any]:
        raise self._metrics_error()
