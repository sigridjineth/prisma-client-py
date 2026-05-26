# Phase 0 compatibility matrix: Python API gates

Status: Phase 0 contract. No runtime implementation is authorized by this artifact.

This matrix defines the Python API compatibility gates for the Prisma 7 JS/TS
Prisma Client bridge. The bridge may change the internal engine boundary, but it
must not require application code to call JavaScript directly.

## Status legend

| Status | Meaning | Default-flip rule |
| --- | --- | --- |
| Supported | Must work before JS bridge becomes default. | Required pass. |
| Partial | A subset may ship behind explicit opt-in with documented limits. | Must not block opt-in; blocks default unless explicitly accepted. |
| Deferred | Out of first bridge release. Must fail clearly or stay on legacy path. | Blocks default if existing Python API behavior would silently regress. |
| Breaking | Intentional compatibility break. Requires migration note and release approval. | Blocks default until approved. |

## Compatibility principles

1. Existing Python imports and generated client module names remain source-compatible.
2. Python methods keep their public signatures unless a breaking change is explicitly
   listed in this matrix.
3. JS bridge mode is selected behind a feature flag first; legacy Rust-query-engine
   behavior remains the fallback until all default-flip gates pass.
4. Python users never instantiate Prisma JS Client or adapter classes directly.
5. Exceptions remain Python exceptions; Prisma error metadata may be added but must
   not replace established exception classes where compatibility is possible.
6. Bridge stdout is protocol-only; user-visible logs and diagnostics must not corrupt
   protocol frames.

## Python API surface matrix

| Python API area | Required behavior in JS bridge mode | Status | Pass criteria | Fail criteria |
| --- | --- | --- | --- | --- |
| Generated package imports | Existing generated imports such as `from prisma import Prisma`, generated models, enums, partials, and type helpers import without user code changes. | Supported | Existing import smoke tests pass for async and sync clients generated from fixture schemas. | Any public import path removed, renamed, or requiring direct JS/TS imports. |
| Client construction | `Prisma()` and generated client constructors keep current accepted arguments unless documented as legacy-only. | Supported | Constructor tests pass with default options, datasource override patterns that remain supported, and JS bridge opt-in flag. | Constructor requires adapter objects, Node paths, or JS Client objects from user code. |
| Connect/disconnect lifecycle | `connect()`, `disconnect()`, async context manager, and sync wrapper semantics remain compatible while supervising the Node subprocess internally. | Supported | Repeated connect/disconnect and context-manager tests leave no bridge process running and map startup failures to Python exceptions. | Resource leak, double-connect behavior regression, or uncaught Node process failure. |
| CRUD model actions | Generated model actions (`find_*`, `create`, `update`, `delete`, `upsert`, `count`, aggregates where currently exposed) retain Python signatures and return shapes. | Supported | Existing model-action suites pass against SQLite JS bridge fixtures. | Signature drift, relation payload shape drift, or requiring JS query objects. |
| Filters/order/select/include | Python query builder inputs serialize to the bridge without API changes. | Supported | Golden serialization fixtures match for nested filters, order, pagination, `select`, and `include`. | Python accepts input but bridge emits malformed Prisma Client call or returns incompatible payload. |
| Model hydration | Results hydrate into existing Python model classes, including relation payloads and unset/optional fields. | Supported | Golden result fixtures round-trip through Python deserialization and equality checks. | Raw JS objects leak through, relation fields disappear, or unset/null distinction changes silently. |
| Scalar mapping | `Decimal`, `BigInt`, `DateTime`, `Json`, `Bytes`, enums, `None`, and list values preserve existing Python representations. | Supported | Serialization and deserialization fixtures pass for each scalar and nested relation payload. | Precision loss, timezone drift, bytes encoding mismatch, enum value mismatch, or JSON mutation. |
| Raw query APIs | Existing raw query methods remain available only after query/result mapping and SQL injection safety behavior are documented. | Partial | Opt-in release either passes provider-specific raw-query fixtures or raises documented unsupported errors. | Raw queries silently change parameterization, return shapes, or transaction behavior. |
| Batch transactions | Existing batch transaction API maps to Prisma Client transaction behavior with deterministic rollback on failure. | Supported | Batch transaction golden lifecycle fixtures pass for commit, rollback, timeout, and bridge death. | Partial commits after Python exception, lost transaction errors, or unpinned connection behavior where pinning is required. |
| Interactive transactions | Preserve source compatibility if implemented; otherwise document as deferred with explicit runtime error in JS bridge mode. | Partial | Either parity tests pass or unsupported path raises a specific Python exception with migration guidance. | Silent fallback, hanging transaction, or nested transaction behavior differs without documentation. |
| Nested transactions | No implicit nested interactive transaction support unless the transaction contract defines it. | Deferred | Nested attempts fail deterministically with documented exception, or pass explicit semantics tests if implemented later. | Deadlock, ambiguous commit/rollback, or untracked inner transaction IDs. |
| Error classes | Existing Python exception classes remain the primary public surface; Prisma `code`, `meta`, and retryability may be attached. | Supported | Error mapping fixtures prove validation, known request, initialization, panic/process death, timeout, and cancellation mappings. | JS stack/error objects leak as untyped strings or established Python exception handlers stop working. |
| Feature flag/default policy | Development flag is `PRISMA_PY_ENGINE=js-bridge|rust-legacy`; initial default is legacy/explicit opt-in until parity gates pass. | Supported | Tests prove default selection, opt-in selection, invalid flag diagnostics, and no Rust engine spawn in JS bridge mode. | JS bridge becomes default before gates pass or unsupported configs silently choose it. |
| Configuration/datasource overrides | Existing Python-side config behavior is preserved where it does not conflict with Prisma 7 `prisma.config.ts` datasource handling. | Partial | Supported override paths are documented and tested per provider. | Python config appears accepted but bridge uses a different datasource. |
| Logging/debug hooks | Python logging hooks remain usable; Node stderr can be captured/forwarded without stdout protocol corruption. | Supported | Tests assert stdout contains only JSON protocol frames and stderr/log side channel carries diagnostics. | Logs interleave with JSON responses on stdout. |
| Metrics/tracing | Existing metrics APIs are not required for first JS bridge opt-in unless they already gate release behavior. | Deferred | Unsupported metrics paths fail clearly or are documented as legacy-only. | Metrics silently report legacy engine values while JS bridge is active. |
| CLI generation commands | Existing `prisma generate` workflows remain the user entrypoint while generator internals create the JS bridge project. | Supported | Fixture project generation produces Python client plus JS bridge project without manual JS calls. | User must hand-write bridge entrypoint or generated Prisma Client import paths. |
| Packaging/runtime dependency diagnostics | Missing Node, missing npm packages, and missing generated JS Client output produce actionable Python errors. | Supported | Failure-mode tests cover missing Node, unsupported Node, missing `@prisma/client`, missing adapter, and missing generated output. | Process exits with opaque npm/Node stack only. |

## Provider-dependent Python API gates

| Gate | SQLite first release | PostgreSQL next path | MySQL/MariaDB next path | Default-flip requirement |
| --- | --- | --- | --- | --- |
| CRUD parity | Required. | Required before provider marked supported. | Required before provider marked supported. | SQLite required; at least one networked DB must be supported or explicitly deferred in release notes. |
| Scalar parity | Required for all scalars supported by SQLite fixture schemas. | Required for provider-specific native mappings and JSON/Decimal behavior. | Required for provider-specific native mappings and Decimal/DateTime behavior. | Provider cannot be marked supported until scalar fixtures pass. |
| Transactions | Batch required; interactive may be partial/deferred with explicit errors. | Batch required; connection-pinning semantics must be verified. | Batch required; adapter pool semantics must be verified. | Default flip blocked by undocumented transaction divergence. |
| Raw queries | Deferred unless provider-specific parameterization tests pass. | Partial until parameter/result fixtures pass. | Partial until parameter/result fixtures pass. | Raw-query behavior cannot silently differ from existing Python API. |
| Migrate/introspection CLI | May remain delegated to Prisma CLI and existing Python CLI wrappers. | Same. | Same. | CLI smoke tests must prove JS bridge runtime does not break generation/setup. |

## Default-flip pass/fail checklist

The JS bridge may become the default only when every required item below is true:

- [ ] Existing generated Python client imports are unchanged.
- [ ] Async and sync client lifecycle tests pass in JS bridge mode.
- [ ] CRUD, filter/order/select/include, model hydration, and scalar fixtures pass for SQLite.
- [ ] Error mapping fixtures preserve Python exception classes where possible.
- [ ] Batch transaction fixtures pass; interactive transaction status is either passing or explicitly deferred.
- [ ] Missing Node/package/adapter/generated-output diagnostics are actionable from Python.
- [ ] `PRISMA_PY_ENGINE=js-bridge` does not spawn the Rust query engine.
- [ ] Unsupported or deferred APIs fail with documented Python exceptions, not silent fallback.
- [ ] User code never imports or instantiates Prisma JS Client, driver adapters, or bridge internals directly.
- [ ] Release notes list every partial/deferred/breaking behavior.

## Reference anchors

- Phase 0 PRD: `.omx/plans/prd-prisma7-js-bridge-migration.md`.
- Phase 0 test spec: `.omx/plans/test-spec-prisma7-js-bridge-migration.md`.
- Prisma driver adapters overview: <https://www.prisma.io/docs/orm/core-concepts/supported-databases/database-drivers>.
