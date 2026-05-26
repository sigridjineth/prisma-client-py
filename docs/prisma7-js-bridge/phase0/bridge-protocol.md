# Bridge Protocol and Lifecycle Contract

Date: 2026-05-26
Status: Phase 0 contract; implementation must not start until reviewed
Applies to: Python `JSBridgeEngine` <-> generated Node Prisma Client bridge

## Goals

1. Define a narrow, testable stdio protocol between Python and Node.
2. Keep the boundary stable even if Prisma generated TypeScript internals change.
3. Make lifecycle, timeout, cancellation, error, logging, and serialization behavior fixture-backed.
4. Preserve Python API compatibility while allowing the runtime implementation to call public Prisma JS/TS Client APIs.

## Non-goals

- Do not expose a user-facing JavaScript API.
- Do not reuse Prisma private generated `internal/*.ts` files as a supported contract.
- Do not depend on Rust query-engine HTTP endpoints, Rust binary paths, or removed Prisma 7 engine environment variables for JS bridge mode.
- Do not implement transport beyond local stdio in the first bridge release.

## Transport

- Transport: newline-delimited UTF-8 JSON over child process stdio.
- Python writes requests to Node `stdin`.
- Node writes only protocol JSON lines to `stdout`.
- Node writes logs, warnings, stack traces, adapter debug output, and `console.*` output to `stderr`, unless a later structured log side channel is explicitly added.
- Every protocol JSON value must be one complete JSON object on one line. Embedded newlines are allowed only as escaped JSON string characters.
- The bridge process must never print banners, npm output, Prisma debug logs, or progress messages to stdout.

## Versioning

Protocol version: `2026-05-26.phase0.v1`.

Every request may include `clientVersion`; every response should include metadata identifying:

- `protocolVersion`
- Python package version, when known
- Prisma CLI/client version, when known
- bridge package/build version, when known
- provider and adapter package, when known

A minor protocol extension is allowed only when older clients can ignore the new field. A breaking change requires a new protocol version and fixture set.

## Request envelope

```json
{
  "id": "req_000001",
  "method": "query.execute",
  "params": {},
  "timeoutMs": 30000,
  "transactionId": null,
  "clientVersion": "prisma-client-py/0.15.x"
}
```

Required fields:

| Field | Type | Rule |
| --- | --- | --- |
| `id` | string | Unique per live request. The response must echo exactly this value. |
| `method` | string | Namespaced method. See method catalog below. |
| `params` | object | Method-specific payload. Empty object when no params are needed. |
| `timeoutMs` | integer | Positive timeout budget in milliseconds owned by Python and enforced by both sides. |

Optional fields:

| Field | Type | Rule |
| --- | --- | --- |
| `transactionId` | string or null | Present only when a request must execute against an interactive transaction client. |
| `clientVersion` | string | Human-readable caller version for diagnostics. |
| `trace` | object | Optional correlation data; the bridge must echo safe correlation keys in metadata when possible. |

Request IDs are caller-generated. Reusing an in-flight ID is a protocol error.

## Response envelope

Exactly one terminal response must be emitted for each accepted request ID.

Success:

```json
{
  "id": "req_000001",
  "result": {},
  "meta": {
    "protocolVersion": "2026-05-26.phase0.v1",
    "elapsedMs": 12,
    "provider": "sqlite",
    "adapter": "@prisma/adapter-better-sqlite3"
  }
}
```

Failure:

```json
{
  "id": "req_000001",
  "error": {
    "code": "PRISMA_VALIDATION_ERROR",
    "message": "Invalid `prisma.user.findUnique()` invocation",
    "meta": {"model": "User", "action": "findUnique"},
    "prismaCode": "P2009",
    "debug": {"stack": "...", "cause": "..."},
    "retryable": false
  },
  "meta": {
    "protocolVersion": "2026-05-26.phase0.v1",
    "elapsedMs": 4
  }
}
```

Rules:

- A response contains exactly one of `result` or `error`.
- Unknown response IDs are fatal protocol violations from Python's perspective.
- Missing responses are timeout failures.
- A malformed JSON line is a fatal protocol violation and the bridge process should be terminated.
- Errors are data, not logs; user-visible error data belongs in the `error` object on stdout.

## Lifecycle methods

### `bridge.ready` notification

Node emits this stdout notification after loading configuration, importing the generated Prisma Client, constructing the adapter, and becoming ready to accept requests.

```json
{
  "method": "bridge.ready",
  "params": {
    "protocolVersion": "2026-05-26.phase0.v1",
    "pid": 12345,
    "provider": "sqlite",
    "adapter": "@prisma/adapter-better-sqlite3",
    "prismaClientVersion": "7.8.0"
  }
}
```

This notification has no `id` and no paired response. Python startup fails if `ready` is not received before the connect timeout.

### `bridge.healthcheck`

Python may call this after startup and before queries.

Request params:

```json
{"requireDatabase": false}
```

Result:

```json
{
  "status": "ok",
  "databaseReachable": null,
  "activeTransactions": 0
}
```

`requireDatabase=true` should verify that the Prisma client can reach the configured database. The SQLite Phase 1 smoke test must cover both false and true.

### `client.connect`

Constructs or validates the Prisma Client instance and adapter.

Params:

```json
{
  "datasource": null,
  "logQueries": false,
  "adapterOptions": {}
}
```

Datasource overrides are supported only when the target adapter has a documented mapping. Unsupported overrides fail with `DATASOURCE_OVERRIDE_UNSUPPORTED` instead of being silently ignored.

### `client.disconnect`

Disconnects Prisma Client and closes all non-transaction resources. Active interactive transactions must be rolled back before shutdown completes.

### `bridge.shutdown`

Graceful process shutdown. The Node bridge should:

1. Reject new requests.
2. Roll back active transactions.
3. Disconnect Prisma Client.
4. Emit a terminal response.
5. Exit with code `0`.

Python may kill the process if graceful shutdown exceeds the disconnect timeout.

## Query method catalog

### `query.execute`

Executes a single Prisma operation through the public generated Prisma Client API.

Params:

```json
{
  "kind": "model",
  "model": "User",
  "action": "findMany",
  "args": {
    "where": {"email": {"contains": "@example.com"}},
    "select": {"id": true, "email": true}
  },
  "resultShape": "many"
}
```

Rules:

- `model` is the Prisma model name as generated by Prisma Client.
- `action` is a public Prisma Client action, for example `findUnique`, `findFirst`, `findMany`, `create`, `update`, `delete`, `upsert`, `count`, `aggregate`, or `groupBy`.
- `args` is already expressed in Prisma JS Client argument shape; Python is responsible for translating Python API calls into this operation envelope before sending to the bridge.
- The bridge must not require Python to call private generated TypeScript internals.

### `query.raw`

Executes raw query commands when the provider/adapter gate allows it.

Params:

```json
{
  "action": "queryRaw",
  "sql": "SELECT id, email FROM User WHERE email = ?",
  "parameters": ["alice@example.com"],
  "resultShape": "rows"
}
```

Raw execution must be fixture-backed per provider before release. If raw query behavior cannot be made safe for a provider, the bridge returns `RAW_QUERY_UNSUPPORTED` for that provider.

### `query.batch`

Executes independent operations in a single batch transaction when all operations are serializable through the bridge contract.

Params:

```json
{
  "operations": [
    {"kind": "model", "model": "User", "action": "create", "args": {"data": {"email": "a@example.com"}}},
    {"kind": "model", "model": "User", "action": "count", "args": {}}
  ],
  "isolationLevel": null
}
```

The bridge maps this to Prisma JS Client `$transaction([...])` where supported.

## Timeout rules

- Python owns the request timeout budget and includes it in `timeoutMs`.
- Node must enforce the same budget where feasible by racing operation execution with a timer.
- If Python timeout fires first, Python sends `bridge.cancel` when the request was accepted and then treats the original request as failed with `BRIDGE_TIMEOUT`.
- Node may still emit a late response. Python must ignore late responses for already-closed IDs and record them as debug telemetry.
- Timeout errors must include `retryable` only when retrying is known safe. Write operations inside transactions are not retryable by default.

## Cancellation rules

### `bridge.cancel`

```json
{
  "id": "req_cancel_000001",
  "method": "bridge.cancel",
  "params": {"targetRequestId": "req_000001", "reason": "python-timeout"},
  "timeoutMs": 1000
}
```

Cancellation is best-effort unless Prisma JS Client exposes a stronger cancellation primitive for the specific operation. If cancellation cannot interrupt the in-flight Prisma call, the bridge must still mark the target request as cancelled from the Python protocol perspective and must not reuse the request ID.

For interactive transactions, cancellation of any operation marks the transaction as tainted. A tainted transaction must roll back and reject subsequent operations with `TRANSACTION_CLOSED`.

## Error schema

```json
{
  "code": "BRIDGE_PROTOCOL_ERROR",
  "message": "Human-readable summary",
  "meta": {"field": "method"},
  "prismaCode": null,
  "debug": {"stack": null, "stderrTail": null},
  "retryable": false
}
```

Required fields:

| Field | Type | Rule |
| --- | --- | --- |
| `code` | string | Stable project-owned code. |
| `message` | string | Safe for users. |
| `retryable` | boolean | Conservative by default. |

Optional fields:

| Field | Type | Rule |
| --- | --- | --- |
| `meta` | object | Structured user-safe details. |
| `prismaCode` | string or null | Prisma `P####` code when provided by Prisma. |
| `debug.stack` | string or null | Captured stack for debug logs; may be redacted from normal exceptions. |
| `debug.stderrTail` | string or null | Bounded bridge stderr tail for startup/protocol failures. |

Minimum code families:

| Code | Meaning | Retryable default |
| --- | --- | --- |
| `BRIDGE_STARTUP_TIMEOUT` | Node process did not emit ready in time. | true |
| `BRIDGE_PROTOCOL_ERROR` | Malformed JSON, stdout contamination, invalid envelope, or ID mismatch. | false |
| `BRIDGE_TIMEOUT` | Operation exceeded timeout. | false |
| `BRIDGE_CANCELLED` | Operation was cancelled by Python. | false |
| `BRIDGE_PROCESS_EXITED` | Bridge died before responding. | unknown/false |
| `BRIDGE_SHUTDOWN_UNSAFE` | Shutdown or disconnect could not prove open transaction rollback. | false |
| `NODE_NOT_FOUND` | Node executable missing. | false |
| `NODE_UNSUPPORTED_VERSION` | Node does not satisfy Prisma 7 minimum. | false |
| `PRISMA_CLIENT_NOT_FOUND` | Generated `@prisma/client` output missing. | false |
| `ADAPTER_NOT_FOUND` | Required adapter package missing. | false |
| `DATASOURCE_OVERRIDE_UNSUPPORTED` | Python datasource override cannot map to the chosen adapter. | false |
| `PRISMA_VALIDATION_ERROR` | Prisma validation error. | false |
| `PRISMA_RUNTIME_ERROR` | Prisma runtime error. | depends on Prisma code |
| `PRISMA_KNOWN_REQUEST_ERROR` | Prisma known request error with stable `P####` code. | depends on Prisma code |
| `TRANSACTION_CLOSED` | Transaction was committed, rolled back, timed out, or tainted. | false |
| `TRANSACTION_NESTED_UNSUPPORTED` | Nested interactive transaction unsupported in JS bridge mode. | false |
| `RAW_QUERY_UNSUPPORTED` | Provider/adapter raw query path not in support matrix. | false |

Python exception mapping is part of the compatibility matrix. Existing project exception classes should be reused where possible, with JS bridge details in `meta`.

## Serialization rules

The bridge uses tagged JSON for values that JSON cannot represent safely.

| Type | Encoding | Notes |
| --- | --- | --- |
| Decimal | `{"$type":"Decimal","value":"12.34"}` | Preserve exact string representation. |
| BigInt | `{"$type":"BigInt","value":"9007199254740993"}` | Never emit as JSON number when unsafe. |
| DateTime | `{"$type":"DateTime","value":"2026-05-26T05:54:00.000Z"}` | ISO 8601 UTC by default; preserve offset if round-trip tests require it. |
| Bytes | `{"$type":"Bytes","encoding":"base64","value":"AQID"}` | Base64 only. |
| JSON | Native JSON value, or `{"$type":"Json","value":...}` when disambiguation is needed. | Must distinguish database JSON null from Prisma/field null where Prisma requires it. |
| Enum | string | Must match generated Prisma enum value and Python enum alias behavior. |
| Null | JSON `null` for nullable field null. | Special Prisma null sentinels require tagged representation if supported. |
| Relations | nested objects/arrays | Shape follows selected/include result shape. |
| Raw rows | array of row objects | Column names are strings; special scalar values use tags. |

Tagged values are allowed in request params and response result payloads.

## Acceptance criteria

- Request and response envelopes are fully specified and fixture-backed.
- Startup, ready, healthcheck, disconnect, and shutdown behavior are specified.
- Timeout and cancellation behavior is specified without assuming unsupported Prisma internals.
- Error schema includes stable code, message, meta, Prisma code, debug stack, and retryable flag.
- Serialization rules cover Decimal, BigInt, DateTime, JSON, Bytes, enums, null, relation payloads, and raw results.
- stdout/stderr separation is mandatory and testable.
- No runtime code is implied by this artifact.
