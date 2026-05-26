# Golden Fixtures Contract

Date: 2026-05-26
Status: Phase 0 fixture plan and examples

## Purpose

Golden fixtures make the Python <-> Node bridge boundary reviewable before runtime code exists. Later tests should load the JSON files in `fixtures/` and assert envelope validation, error mapping, serialization, and transaction lifecycle behavior.

## Fixture files

| File | Coverage |
| --- | --- |
| `fixtures/manifest.json` | Fixture set metadata, protocol version, source plan, and validation expectations. |
| `fixtures/protocol-lifecycle.json` | `bridge.ready`, `bridge.healthcheck`, `client.connect`, `client.disconnect`, and `bridge.shutdown`. |
| `fixtures/query-success.json` | Model query request/response and result shape. |
| `fixtures/error-mapping.json` | Prisma validation/runtime and bridge protocol errors. |
| `fixtures/serialization.json` | Decimal, BigInt, DateTime, JSON, Bytes, enum, null, relations, and raw rows. |
| `fixtures/transaction-lifecycle.json` | Batch success/failure, interactive commit/rollback/timeout/cancellation, bridge death, disconnect rollback/unsafe timeout, nested unsupported, and closed-ID reuse examples. |

## Fixture invariants

- JSON must be valid and deterministic.
- Every request with `id` must have exactly one response with the same `id`, unless the scenario is explicitly a timeout/cancellation case.
- Protocol-only output must be represented as stdout JSON lines; logs must be modeled as stderr entries.
- Error fixtures must include stable `code`, `message`, `meta`, `prismaCode`, `debug`, and `retryable` fields.
- Special scalar values must use the tagged encoding from `bridge-protocol.md`.
- Transaction IDs are opaque and scoped to the scenario.

## Required later test assertions

1. Envelope schema validates each request/response.
2. Request ID matching rejects missing, duplicated, or mismatched IDs.
3. stdout contamination is classified as `BRIDGE_PROTOCOL_ERROR`.
4. Error fixtures map to expected Python exception classes where possible.
5. Serialization fixtures round-trip into generated Python model/scalar types.
6. Transaction fixtures assert commit/rollback state transitions, rollback uncertainty, nested unsupported behavior, cancellation, disconnect handling, and closed-ID rejection.
7. Unsupported provider/feature fixtures produce explicit project-owned errors.

## Acceptance criteria

- Fixture examples exist for lifecycle, success, errors, serialization, and transactions.
- Fixture files are valid JSON.
- Later test suites can import these files without requiring a Node bridge implementation.
