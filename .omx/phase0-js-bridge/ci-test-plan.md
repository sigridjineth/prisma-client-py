# CI Test Plan for Prisma 7 JS Bridge

Date: 2026-05-26
Status: Phase 0 CI contract

## CI goals

- Prove the bridge boundary before default flip.
- Keep legacy Rust/v5/v6 tests separate from Prisma 7 JS bridge tests.
- Exercise supported providers with explicit driver adapters.
- Fail on stdout contamination, missing dependencies, and unsupported providers with project-owned diagnostics.

## Matrix dimensions

| Dimension | Initial values | Notes |
| --- | --- | --- |
| Python | Supported project versions from packaging metadata | Keep existing package support policy; do not add unsupported Python solely for bridge. |
| Node | Minimum Prisma 7-supported line, current LTS, and current/new line used by proof spike | Phase 0 proof observed Node 25.4.0 locally; CI should include a stable LTS line and minimum-supported line. |
| OS | Linux first; macOS/Windows smoke later | Linux provider matrix is release-blocking; cross-platform stdio path should be smoke-tested. |
| Prisma | `prisma` and `@prisma/client` 7.8.0 target from plan | Upgrade requires refreshing fixtures/matrices. |
| Engine lane | `PRISMA_PY_ENGINE=js-bridge`, `PRISMA_PY_ENGINE=rust-legacy` where applicable | No implicit lane. |
| Provider | PostgreSQL first and only initial provider; SQLite and MySQL/MariaDB deferred | MongoDB excluded from initial Prisma 7 bridge. |

## Required suites

### Phase 0 artifact validation

Command shape:

```bash
python3 - <<'PY'
import json
from pathlib import Path
for path in Path('.omx/phase0-js-bridge/fixtures').glob('*.json'):
    json.loads(path.read_text())
print('phase0 fixtures valid')
PY
```

Gate: must pass before implementation lanes start.

### Protocol unit tests

Future suite targets:

- Envelope validation.
- Request ID matching.
- Timeout/cancellation classification.
- stdout/stderr protocol/logging separation.
- Error schema validation.
- Tagged scalar serialization/deserialization.

Gate: all pass for bridge mode.

### Generator tests

Existing anchors to preserve/update:

- `tests/test_cli/test_generate.py`
- `tests/test_generation/test_generator.py`
- `tests/test_generation/test_validation.py`
- `tests/test_generation/test_schema_dsl_parser.py`
- `tests/test_generation/test_partial_types.py`
- `tests/test_generation/exhaustive/test_exhaustive.py`

New Prisma 7 fixture requirements:

- Explicit generator output.
- `prisma.config.ts` datasource/config path.
- JS bridge project emitted beside generated Python client.
- Adapter package metadata emitted.
- Rust `requiresEngines: ["queryEngine"]` not requested in Prisma 7 bridge mode.

Gate: generation succeeds or fails only on deliberate compatibility guards, not incidental Pydantic missing fields.

### Bridge lifecycle integration tests

Provider: PostgreSQL first.

Required checks:

- Install/build generated bridge dependencies.
- Spawn bridge and receive `bridge.ready`.
- `bridge.healthcheck(requireDatabase=false)` passes.
- `bridge.healthcheck(requireDatabase=true)` passes.
- `client.connect` and `client.disconnect` pass.
- stdout contains only protocol JSON; stderr may contain logs.
- Bridge shutdown exits cleanly.

Gate: all pass on Linux + at least one Node LTS/minimum row.

### Query parity tests

Existing anchors to preserve:

- `tests/test_client.py`
- `tests/test_actions.py`
- `tests/test_batch.py`
- `tests/test_http.py` where still relevant to public behavior
- `tests/test_dotenv.py`
- `tests/test_validator.py`
- `tests/test_models.py`
- `tests/test_engine.py` split by lane

Bridge-specific checks:

- CRUD.
- select/include/filter/order.
- model hydration.
- scalar serialization.
- relation payloads.
- raw query behavior or explicit unsupported result.

Gate: PostgreSQL pass before preview and before stable default; SQLite/MySQL/MariaDB are formally deferred.

### Transaction/failure-mode tests

Required cases:

- Batch transaction commit and rollback, including second-operation failure rollback.
- Interactive transaction commit.
- Interactive transaction rollback on Python exception.
- Timeout inside transaction taints and rolls back.
- Cancellation taints and rolls back.
- Bridge process death invalidates all open transaction IDs with rollback outcome `unknown`/`lost`.
- Client disconnect with an open transaction rolls back or reports unsafe rollback timeout explicitly.
- Closed transaction ID reuse is rejected deterministically.
- Nested transaction attempt returns explicit unsupported error.
- Malformed protocol response kills bridge and maps error.
- Missing Node/package/adapter/generated client diagnostics.

Gate: PostgreSQL transaction suite passes before default flip.

### Packaging smoke tests

Required checks:

- Fresh temporary project generates Python client and JS bridge project.
- npm install/build uses fixture-local cache in CI.
- Generated package imports in Python.
- Bridge dependency diagnostics do not leak raw stack traces for common missing-dependency cases.

Gate: must pass before stable release.

## Release gates

No stable Prisma 7 JS bridge default until:

1. Phase 0 artifact validation passes.
2. PostgreSQL lifecycle/query/serialization/error/transaction suites pass.
3. SQLite and MySQL/MariaDB are explicitly deferred in release notes until separately supported.
4. Packaging smoke test passes in a fresh project.
5. Compatibility matrix Required rows pass.
6. Unsupported providers/features fail with explicit diagnostics.
7. JS bridge mode proves it does not spawn Rust query-engine binaries or use removed Prisma 7 engine env vars.
8. Documentation explains Node, adapters, unsupported providers, metrics posture, and legacy Rust mode.

## Failure handling

- CI failures in bridge mode block Prisma 7 default work.
- Legacy rust failures block v5/v6 maintenance only unless they affect shared Python API behavior.
- Provider-specific failures block only that provider unless they reveal a protocol or Python API issue.
- Flaky bridge lifecycle tests must capture stderr/stdout artifacts for diagnosis.

## Acceptance criteria

- The plan names matrix dimensions, suite families, and pass/fail gates.
- Provider gates are staged and explicit.
- Commands are concrete enough to become CI jobs.
- Legacy and JS bridge lanes are not conflated.
