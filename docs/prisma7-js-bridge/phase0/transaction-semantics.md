<!-- markdownlint-disable MD013 -->

# Phase 0 Transaction Semantics and Failure Contract

Status: Phase 0 contract, docs-only
Owner lane: Transactions/failure
Target architecture: Python client -> persistent Node stdio bridge -> generated Prisma JS/TS Client -> Prisma driver adapter -> database

## Scope

This document defines the transaction contract that later JS bridge implementation
must satisfy before `PRISMA_PY_ENGINE=js-bridge` can become a default runtime path.
It intentionally does not prescribe implementation code.

The contract covers:

- Batch write transactions exposed by Python `batch_()`.
- Interactive transactions exposed by Python `tx()` / context managers.
- Transaction/session identifiers crossing the Python <-> Node boundary.
- Connection pinning expectations.
- Nested transaction behavior.
- Rollback behavior for Python exceptions, cancellations, timeouts, bridge death,
  and client disconnects.
- Retry and timeout policy.
- Acceptance criteria and fixture expectations for later phases.

## Source-of-truth API behavior to preserve

Current Python behavior to preserve where possible:

- `Prisma.batch_()` queues write operations and executes them as one atomic unit
  when `commit()` is called or the context manager exits.
- `Prisma.tx()` creates a context manager that returns a copied Prisma client
  bound to a transaction ID.
- A successful `tx()` context commits on exit.
- Any Python exception escaping the `tx()` context triggers rollback, then the
  original exception remains the user-visible failure.
- `max_wait` defaults to 2 seconds and controls acquisition wait time.
- `timeout` defaults to 5 seconds and controls maximum transaction runtime.
- Starting a transaction from a client that is already bound to a transaction is
  currently warned as surprising behavior; the JS bridge contract must replace
  this ambiguity with explicit nested-transaction rules before default flip.

## Terms

| Term | Definition |
| --- | --- |
| Batch transaction | A finite list of independent Prisma operations sent as one atomic unit. Mirrors Prisma JS `$transaction([operationA, operationB])` and Python `batch_()`. |
| Interactive transaction | A long-lived transaction context with multiple round trips, dependent operations, and Python logic between operations. Mirrors Prisma JS `$transaction(async (tx) => ...)` but is driven by Python `tx()` calls over the bridge. |
| Transaction ID | Opaque identifier returned to Python when an interactive transaction starts. It is included on every bridged request that must execute inside that transaction. |
| Bridge session | The lifetime of one connected Python client and one Node bridge process. A session may own zero or more completed transactions and at most the configured number of open interactive transactions. |
| Pinned client | The Prisma JS transaction client or equivalent bridge-side handle that must be used for all operations carrying a given transaction ID. |
| Rollback-confirmed | A terminal state where the bridge observed Prisma rollback completion or observed a Prisma timeout/cancellation error that guarantees rollback. |
| Rollback-unknown | A terminal state where the bridge or process died before rollback could be confirmed. The Python API must treat this as a failed transaction and require reconnect before reuse. |

## Transaction model

### Batch transactions

Batch writes are the preferred bridge path for independent operations that do not
need intermediate Python decisions.

Contract:

1. Python `batch_()` builds a finite ordered list of write actions.
2. Commit sends one bridge request whose method is reserved for batch execution
   `query.batch`.
3. The Node bridge executes the operations through Prisma Client as one atomic
   transaction.
4. Operations execute in the order Python queued them.
5. If any operation fails, Prisma rolls back the entire batch and the Python
   caller receives one mapped exception.
6. A batch has no long-lived `transactionId`; request/response correlation uses
   the normal protocol `id`.
7. Retrying a failed batch is not automatic unless the caller explicitly opts in
   and the failure is classified retryable.
8. A batch commit response must include transaction metadata sufficient for
   golden fixtures: operation count, elapsed time, and provider when known.

Batch non-goals for first bridge implementation:

- Reads inside `batch_()` remain unsupported unless the compatibility matrix
  later upgrades them.
- Passing generated IDs between queued operations is not supported by batch mode;
  callers should use nested writes or interactive transactions.

### Interactive transactions

Interactive transactions are required for dependent read/modify/write flows and
for Python logic between database operations.

Contract:

1. Python `tx(max_wait=..., timeout=...)` sends a transaction-start request.
2. The bridge starts a Prisma interactive transaction and returns an opaque
   `transactionId` only after the underlying transaction is ready to accept
   operations.
3. Python receives a copied client with `_tx_id == transactionId`.
4. Every bridged query from that copied client includes `transactionId`.
5. The Node bridge routes every request with that ID through the pinned Prisma
   transaction client or equivalent Prisma 7 transaction context.
6. Python context exit with no exception sends commit.
7. Python context exit with an exception, including cancellation exceptions,
   sends rollback.
8. After commit, rollback, timeout, cancellation, or unknown bridge loss, the
   `transactionId` is terminal and cannot be reused.
9. Any operation received for a terminal or unknown ID fails with a mapped
   `TransactionError`/`TransactionExpiredError` rather than silently executing on
   the root client.


Bridge host model:

The Node bridge hosts interactive transactions by starting a Prisma `$transaction(async (tx) => ...)` callback and keeping that callback unresolved while a bridge-owned command loop dispatches transaction-bound requests to `tx`. `transaction.commit` resolves the command loop so Prisma commits; `transaction.rollback` and `transaction.abort` reject or throw inside the loop so Prisma rolls back. The transaction client must never escape that callback lifetime, and Python only sees the opaque `transactionId` managed by the bridge table.

Interactive transaction methods reserved for the protocol contract:

| Method | Purpose | Required result/error behavior |
| --- | --- | --- |
| `transaction.start` | Open an interactive transaction. | Returns `transactionId`, timeout settings, and provider metadata; fails before creating Python transaction client if acquisition fails. |
| `transaction.query` or normal query with `transactionId` | Execute one operation inside the transaction. | Uses only the pinned transaction client; never falls back to root Prisma client. |
| `transaction.commit` | Commit an open transaction. | Returns terminal `committed` state; repeated commit for same ID fails idempotently as already terminal. |
| `transaction.rollback` | Roll back an open transaction. | Returns terminal `rolled_back` state; repeated rollback for same ID should be safe and reported as already terminal where Prisma can prove it. |
| `transaction.abort` | Bridge-internal cancellation path. | Rolls back when possible, then marks state terminal; maps uncertainty explicitly. |

## Transaction/session IDs

Transaction IDs are bridge-owned opaque strings.

Requirements:

- Python must not parse, synthesize, sort, or persist transaction IDs.
- IDs must be unique within a bridge session and should be globally unique enough
  for logs and fixtures, for example UUIDv7/UUIDv4 or Prisma-provided IDs with a
  bridge prefix.
- IDs must not include credentials, SQL, database URLs, or user data.
- IDs are session-local. Reusing an ID after reconnect or bridge restart must
  fail as `TransactionExpiredError` or a bridge-specific transaction-lost error.
- The bridge maintains a transaction table keyed by ID with at least:
  - `state`: `starting`, `open`, `committing`, `rolling_back`, `committed`,
    `rolled_back`, `timed_out`, `cancelled`, `lost`, `failed`.
  - `startedAt`, `deadlineAt`, `maxWaitMs`, `timeoutMs`.
  - provider/adapter metadata when available.
  - nesting depth or parent ID when nested transactions are in scope.

## Connection pinning

Interactive transaction requests must preserve the database connection semantics
Prisma requires.

Requirements:

- Every operation for an open interactive transaction is routed through the same
  Prisma transaction client/transaction context.
- The bridge must not interleave a transaction-bound operation onto the root
  Prisma client, even as a fallback after an error.
- The bridge must serialize operations per transaction ID unless a later
  provider-specific proof demonstrates safe parallelism. Prisma transactions are
  connection-bound, and one database connection can execute only one query at a
  time.
- Operations for different transaction IDs may run concurrently only when the
  adapter/provider can acquire separate connections and the bridge can enforce
  per-transaction ordering.
- If the pinned transaction client is lost, all future operations for that ID
  fail terminally and the Python root client must reconnect before new
  transactions are trusted.

## Nested transactions

Prisma ORM v7.5.0 introduced nested transaction rollback behavior for SQL
databases using savepoints. The Python bridge must still be conservative until
fixtures prove parity across adapters.

Phase 0 contract:

1. Nested transaction support is **deferred by default** for the first JS bridge
   implementation unless explicitly enabled by a compatibility gate.
2. Calling `tx()` while the Python client is already transaction-bound must not
   silently open an independent root transaction.
3. First implementation behavior must be one of:
   - fail fast with a documented `NestedTransactionUnsupportedError` subclass or
     mapped `TransactionError`; or
   - implement savepoint-backed nested rollback only after provider-specific
     fixtures pass.
4. If nested transactions are enabled, inner rollback must not commit or roll
   back the outer transaction by itself. Inner rollback maps to a savepoint
   rollback; outer commit/rollback remains authoritative.
5. Nested transaction IDs must represent parent/child relationships in metadata
   even if the public ID remains opaque.
6. Providers without savepoint support, or adapters where Prisma nested
   behavior is unverified, must stay fail-fast.

Acceptance gate for enabling nested transactions:

- SQLite, PostgreSQL, and MySQL/MariaDB adapter-specific fixtures prove:
  - inner success + outer success commits all changes;
  - inner failure + caught Python exception rolls back only inner changes;
  - outer failure rolls back both inner and outer changes;
  - uncaught Python exception from inner context leaves the user-visible
    exception unchanged;
  - no operation escapes the pinned transaction context.

## Failure contract

### Python exception

When a Python exception escapes `with prisma.tx()` or `async with prisma.tx()`:

1. Python sends `transaction.rollback` for the active ID.
2. If rollback succeeds, the original Python exception is re-raised.
3. If rollback fails, rollback failure is logged/attached as diagnostic metadata,
   but the original Python exception remains primary unless rollback failure means
   the process/client state is unsafe.
4. The transaction ID becomes terminal regardless of rollback outcome.

### Python cancellation

Cancellation includes `asyncio.CancelledError`, task-group cancellation, signal
shutdown routed through application code, or sync cancellation wrappers.

Contract:

- Cancellation is treated like an exception for transaction outcome: rollback is
  attempted before control returns to the cancellation path.
- Async implementation must shield the rollback request long enough to give the
  bridge one bounded chance to roll back.
- Rollback shielding may not exceed the configured cleanup timeout.
- If cleanup timeout expires, mark the transaction `rollback_unknown`, terminate
  or quarantine the bridge process, and require reconnect.

### Transaction timeout

Timeout sources:

- Prisma interactive transaction `timeout` option.
- Bridge request `timeoutMs` for individual protocol requests.
- Python-side watchdog/cleanup timeout.

Contract:

- `max_wait` maps to Prisma `maxWait` in milliseconds.
- `timeout` maps to Prisma interactive transaction `timeout` in milliseconds.
- Defaults remain compatible with Python docs: `max_wait=2s`, `timeout=5s`.
- Transaction-level timeout is authoritative for total interactive transaction
  runtime.
- Request-level timeout must be shorter than or equal to the remaining
  transaction deadline unless the request is rollback/cleanup.
- Prisma timeout that reports the transaction has closed maps to
  `TransactionExpiredError`.
- After timeout, no subsequent operation may execute on the root client using the
  expired transaction's payload.

### Bridge death or Node process crash

If the Node bridge process exits, crashes, or stops responding while a
transaction is open:

1. Python marks every open transaction as `lost` / `rollback_unknown`.
2. Python fails all pending transaction-bound operations with a bridge process
   error that includes `retryable=false` for the original transaction.
3. Python must not try to commit or continue the lost transaction on a respawned
   bridge.
4. The root client may reconnect by spawning a fresh bridge, but open
   transaction IDs do not survive.
5. Diagnostics must include bridge exit status, signal if any, last request ID,
   and affected transaction IDs without leaking query payloads by default.

### Client disconnect

Client disconnect means `Prisma.disconnect()`, context manager exit, garbage
collection cleanup, process shutdown hook, or explicit bridge shutdown while
transactions are open.

Contract:

- Graceful disconnect attempts rollback for all open transactions before bridge
  shutdown.
- Disconnect waits for rollback only up to a bounded cleanup timeout.
- If all rollbacks are confirmed, bridge shutdown is graceful.
- If any rollback is unknown, bridge shutdown must surface a warning/error and
  mark the session unsafe for reuse.
- New queries after disconnect fail with the existing disconnected-client error
  shape and never implicitly reconnect into an old transaction.

### Bridge protocol cancellation

If the protocol supports per-request cancellation, cancellation of a
transaction-bound query must not cancel only the Python wait while leaving the
query untracked.

Contract:

- A cancelled transaction-bound request transitions the transaction to
  `cancelling` and attempts rollback unless the caller explicitly configured a
  safe continue policy in a future phase.
- First implementation uses rollback-on-cancel for all transaction-bound query
  cancellations.
- Non-transaction root queries may use request-level cancellation without marking
  the bridge session unsafe if Prisma/adapter proves cancellation isolated.

## Retry policy

Default: no automatic retry for transactional writes.

Rationale:

- Retrying a transaction can duplicate external side effects around Python logic.
- A lost bridge cannot prove the database state of an open transaction.
- Idempotency is application-specific.

Allowed retry classes:

| Failure | Automatic retry? | Caller opt-in retry? | Notes |
| --- | --- | --- | --- |
| `transaction.start` acquisition timeout | No | Yes | Safe only before any transaction work begins. |
| Serialization/deadlock provider error | No | Yes | Caller must rerun the whole transaction body. |
| Bridge death with open transaction | No | No for same ID | Caller may start a new transaction after reconnect if application logic is idempotent. |
| Batch request lost before bridge accepted it | No by default | Future opt-in only | Requires request acceptance/ack fixture to prove no execution occurred. |
| Validation error/user error | No | No | Deterministic caller input problem. |
| Network/database connection failure | No | Yes only from start | Must be classified by provider and adapter. |

The bridge error schema must expose `retryable` and `retryScope` fields later
consumed by Python exception mapping:

- `retryable`: boolean classification for whether retry may be safe.
- `retryScope`: `none`, `start`, `wholeTransaction`, or `wholeBatch`.
- `stateKnown`: `committed`, `rolledBack`, `notStarted`, or `unknown`.

## Timeout policy

Timeouts must be explicit at the Python boundary and preserved through the
bridge.

| Setting | Python API | Bridge/Prisma mapping | Default |
| --- | --- | --- | --- |
| Acquisition wait | `max_wait: timedelta` | Prisma `maxWait` | 2 seconds |
| Interactive runtime | `timeout: timedelta` | Prisma `timeout` and bridge transaction deadline | 5 seconds |
| Individual request timeout | protocol `timeoutMs` | bridge watchdog per request | min(request timeout, remaining transaction deadline) |
| Rollback cleanup timeout | internal setting | bridge/Python cleanup watchdog | bounded; proposed 2 seconds for first implementation |

Rules:

- Convert all timeout values to integer milliseconds at the bridge boundary.
- Reject non-positive values before sending the request to Node.
- Include the effective deadline in transaction-start metadata for fixtures.
- On any timeout, terminal transaction state must be observable in the response
  or follow-up healthcheck metadata.

## Error mapping requirements

Later implementation must map transaction failures to existing Python exception
classes where possible:

| Condition | Python-visible class target | Required metadata |
| --- | --- | --- |
| Transaction already closed/expired | `TransactionExpiredError` | transaction ID, state, deadline, Prisma code if present |
| Transaction misuse/not started | `TransactionNotStartedError` or `TransactionError` | method, client state |
| Nested transaction unsupported | `TransactionError` until a dedicated class exists | provider, parent transaction ID, feature gate |
| Prisma validation/runtime error inside tx | Existing Prisma error mapping where possible | `transactionId`, rollback outcome |
| Bridge crash/lost transaction | Bridge process error + transaction metadata | exit code/signal, `stateKnown=unknown`, `retryable=false` |
| Rollback failure after Python exception | Original Python exception primary; rollback diagnostic attached/logged | rollback error code/message |

## Observability and protocol metadata

Every transaction lifecycle response should include metadata suitable for golden
fixtures and troubleshooting:

- `transactionId`.
- `state` before and after the operation.
- `requestId` that caused the transition.
- `startedAt`, `endedAt`, and elapsed milliseconds when available.
- `timeoutMs`, `maxWaitMs`, and effective `deadlineAt`.
- `provider` and `adapter` when available.
- `nestedDepth` and `parentTransactionId` when nested support is enabled.
- `rollbackOutcome`: `notNeeded`, `confirmed`, `unknown`, or `failed`.

Logs may include transaction IDs and states but must not include query variables,
raw SQL parameters, credentials, or serialized model data unless the user enables
a debug mode that is documented separately.

## Golden fixture requirements

The Phase 0 fixture set must include transaction lifecycle examples for:

1. Batch success with two write operations.
2. Batch failure where the second operation fails and the first is rolled back.
3. Interactive success: start, two transaction-bound queries, commit.
4. Interactive Python exception: start, query, rollback, original exception
   preserved.
5. Interactive timeout: start, query after deadline, `TransactionExpiredError`.
6. Python cancellation: cancelled operation triggers rollback attempt.
7. Bridge death: open transaction becomes `rollback_unknown` / `lost`.
8. Client disconnect with open transaction: graceful rollback succeeds.
9. Client disconnect with rollback timeout: unsafe shutdown diagnostic.
10. Nested transaction unsupported: fail-fast without opening an inner root
    transaction.
11. Nested savepoint path, when enabled: inner rollback with outer continuation.

Each fixture must assert both protocol envelopes and Python exception mapping.

## Acceptance criteria

This transaction contract is accepted when all of the following are true:

- Batch and interactive transaction semantics are separately defined.
- Transaction ID ownership, lifecycle states, and terminal-state reuse rules are
  explicit.
- Connection pinning forbids fallback from a transaction-bound query to the root
  Prisma client.
- Nested transaction behavior is fail-fast by default, with clear savepoint gates
  for future enablement.
- Rollback behavior is defined for Python exception, Python cancellation,
  transaction timeout, bridge death, and client disconnect.
- Retry policy defaults to no automatic transactional retry and defines explicit
  opt-in retry scopes.
- Timeout mapping preserves Python defaults and maps to Prisma `maxWait` and
  `timeout` in milliseconds.
- Error mapping preserves existing Python exception classes where possible.
- Golden transaction fixture requirements are listed for the CI/fixture lane.
- No runtime implementation files are changed by this Phase 0 artifact.

## References

- Local plan: `.omx/plans/prd-prisma7-js-bridge-migration.md`.
- Local test spec: `.omx/plans/test-spec-prisma7-js-bridge-migration.md`.
- Current Python transaction docs: `docs/reference/transactions.md`.
- Current Python batching docs: `docs/reference/batching.md`.
- Prisma Client transaction docs: <https://www.prisma.io/docs/orm/prisma-client/queries/transactions>.
- Prisma ORM v7.5.0 changelog for nested transaction savepoints: <https://www.prisma.io/changelog/2026-03-11>.
