# Python API Compatibility Matrix

Date: 2026-05-26
Status: Phase 0 compatibility contract

## Compatibility principle

The JS bridge revives Prisma 7 without asking Python users to call JavaScript directly. Default flip is blocked until compatibility gates below pass or a breaking/deferred behavior is explicitly approved and documented.

## Status meanings

| Status | Meaning |
| --- | --- |
| Required | Must pass before JS bridge becomes default. |
| Required with changed backend | Public Python API remains source-compatible, but implementation changes. |
| Partial | Supported for a subset; unsupported cases must fail with explicit diagnostics. |
| Deferred | Not part of initial default claim. |
| Breaking / v6-only | Behavior cannot be preserved for Prisma 7 default and must be documented as legacy or removed. |

## Public API matrix

| Area | Current behavior anchor | JS bridge expectation | Status | Pass/fail gate |
| --- | --- | --- | --- | --- |
| Generated imports | `from prisma import Prisma`, `from prisma.models import User` after generation. | Import paths remain valid for generated Python client. | Required | Existing generated-client import tests pass. |
| Client construction | `Prisma(...)` supports sync/async generated clients and config values. | Constructor signatures remain source-compatible; unsupported JS bridge options fail explicitly. | Required | Static type snapshots and constructor tests pass. |
| `connect()` / `disconnect()` | Starts/stops Rust query engine today. | Starts/stops Node bridge and Prisma JS Client while preserving timeout parameter behavior. | Required with changed backend | Lifecycle fixtures and client tests pass. |
| Context managers | `with Prisma()` and `async with Prisma()` connect/disconnect. | Same public behavior; shutdown rolls back active transactions. | Required | Context-manager tests pass for bridge mode. |
| CRUD actions | Model delegates expose create/read/update/delete/upsert/count/etc. | Same Python method names and signatures; bridge receives model/action/args envelope. | Required | Existing action tests pass or intentional Prisma 7 deltas documented. |
| select/include/filter/order | Python builders produce selections and filters. | Result shape and validation remain source-compatible. | Required | Golden result hydration and existing selecting-fields tests pass. |
| Model hydration | Results become generated Python models. | Hydration/deserialization preserves scalar, relation, optional, and list behavior. | Required | Serialization fixtures and model tests pass. |
| Exceptions | Existing public exception classes where possible. | Bridge maps JS/Prisma/adapter errors into existing classes with `meta`/cause details. | Required | Error mapping fixtures pass. |
| Batch transactions | Existing batch behavior where exposed. | Map to Prisma JS Client `$transaction([...])`. | Required | Batch transaction fixtures pass for SQLite. |
| Interactive transactions | `Prisma.tx()` context manager. | Preserve context-manager source API; use bridge transaction IDs. | Required before default flip unless explicitly deferred | SQLite rollback/commit/timeout fixtures pass. |
| Nested transactions | Existing behavior warns and is unstable. | Explicit unsupported result in JS bridge initial mode. | Partial | Attempt fails with `TRANSACTION_NESTED_UNSUPPORTED`. |
| Raw queries | Raw SQL support varies by provider. | Supported only per adapter/provider after fixtures. | Partial | Provider-specific raw query fixtures pass or `RAW_QUERY_UNSUPPORTED`. |
| Datasource override | Python datasource override and SQLite path adjustment. | SQLite override is first required case; other providers require adapter mapping. | Partial | SQLite override test passes; unsupported providers fail explicitly. |
| Metrics | Rust engine `/metrics` public method. | Prisma 7 removed legacy metrics path; no default parity claim. | Breaking / v6-only initially | `get_metrics()` is documented v6-only or replaced by new observability contract. |
| Binary override env vars | `PRISMA_QUERY_ENGINE_BINARY`, engine hash/cache settings. | Not supported in JS bridge default. | Breaking / v6-only | JS bridge mode never reads/spawns Rust query-engine binary. |
| MongoDB | Existing docs/code mention MongoDB support. | Prisma 7 MongoDB support is not in initial default path. | Deferred / v6-only | MongoDB v7 path fails with explicit unsupported provider message. |
| Data Proxy/library engine | Not supported today. | Not part of first JS bridge. | Deferred | Explicit unsupported diagnostics. |
| Generated JS/TS project | Not present today. | Generated beside Python client with explicit Prisma Client output. | Required with changed backend | Generator fixture verifies files and package metadata later. |

## Default flip gates

JS bridge may become default only when all Required rows pass for SQLite and at least one networked DB path is either passing or formally deferred from stable scope.

Minimum default-flip criteria:

1. Existing generated Python client imports unchanged.
2. Existing CRUD API signatures remain source-compatible.
3. `connect`, `disconnect`, sync/async context managers, and timeout behavior pass bridge tests.
4. Model hydration golden fixtures pass for all supported scalar categories.
5. Error mapping fixtures preserve existing exception classes where possible.
6. Transactions pass existing behavior tests or documented changed behavior is approved.
7. User code does not need direct JS calls.
8. JS bridge mode does not spawn Rust query-engine binaries or use Prisma 7 removed engine env vars.
9. Unsupported features fail with project-owned diagnostics, not Pydantic/Node stack leaks.

## Intentional compatibility breaks for initial Prisma 7 bridge

| Break/defer | Reason | Required user-facing messaging |
| --- | --- | --- |
| Rust binary overrides unavailable in JS bridge mode. | Prisma 7 default is Rust-binary-free and driver-adapter based. | `PRISMA_QUERY_ENGINE_BINARY` applies only to legacy Rust/v5/v6 mode. |
| Legacy metrics API not supported in JS bridge mode. | Prisma 7 removed legacy metrics feature. | Point to future adapter/OpenTelemetry observability story. |
| MongoDB excluded from first Prisma 7 bridge default. | Prisma 7 MongoDB support is not available in the planned bridge gate. | Provider unsupported for Prisma 7 bridge; use legacy/v6 maintenance lane. |
| Nested interactive transactions unsupported. | Existing Python docs already mark related behavior unstable; savepoint semantics need separate proof. | Raise `TRANSACTION_NESTED_UNSUPPORTED`. |

## Acceptance criteria

- Every public API area has a status and pass/fail gate.
- Required gates can be converted directly into tests.
- Partial/deferred/breaking behavior has explicit user-facing messaging.
- Default flip cannot occur without transaction, error, serialization, and lifecycle fixture evidence.
