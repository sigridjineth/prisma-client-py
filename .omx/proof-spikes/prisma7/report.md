# Prisma 7 compatibility proof spike (worker-2)

Date: 2026-05-26
Worker tasks: 2 (A1 generator), 3 (A2 runtime), 6 (next plan), 7 (release-blocking test matrix)

## Classification

| Gate | Result | Release impact |
| --- | --- | --- |
| A1 generator compatibility | **Blocked / release blocker** | Current Python generator does not generate under Prisma 7.8.0. Prisma 7 rejects the generator manifest's `requiresEngines: ["queryEngine"]`; a custom probe then shows Prisma 7 generator params no longer match this repo's required data shape (`binaryPaths: null`, datasource has no `url` under Prisma config, version hash differs). |
| A2 direct runtime compatibility | **Blocked => JS bridge mandatory/default** | The repo's supported runtime is a Python HTTP client over a Rust `query-engine` sidecar. Prisma 7 default generated client is Rust-binary-free TypeScript query compiler + driver adapter. No public supported Python direct-runtime boundary was proven; passing A2 would require relying on generated `internal/*` TypeScript files explicitly marked private. |
| Recommended next plan | **Option B JS runtime bridge first; v5/v6 fallback if scope is too broad** | Build a Node/JS bridge around the public Prisma Client 7 API and driver adapters, or stay on the v5/v6 Rust-engine maintenance line. Do not attempt a direct Python query-compiler integration from private generated internals. |

## Environment / local caveats

Evidence: `logs/00-environment.txt`.

- `node --version`: `v25.4.0`.
- `npm --version`: `11.7.0`.
- `npm view prisma version engines --json`: latest observed `7.8.0`, requiring Node `^20.19 || ^22.12 || >=24.0`; local Node satisfies this.
- Bare `python` is absent; `python3` is available and was used for isolated fixture checks.
- Initial npm use with the global cache hit cache/permission failures (`logs/01-npm-install.txt`); reruns used a fixture-local npm cache successfully (`logs/03-*`, `09-*`, `13-*`). Later global `npm view` hit `ENOSPC`; this is local disk/cache pressure, not a Prisma architecture finding.

## A1 generator proof

### Fixture and commands

Fixture path: `.omx/proof-spikes/prisma7/fixture`.

Key commands and outcomes:

1. `npm install --cache ../npm-cache` in the Prisma 7 fixture: **PASS** (`logs/03-npm-install-local-cache.txt`).
2. `.venv/bin/python -m pip install -e ../../../..`: **PASS** after correcting the relative path (`logs/04-pip-install-editable-corrected.txt`).
3. `npx prisma generate` with provider `./.venv/bin/python ./py-generator.py`: **FAIL** (`logs/05-prisma7-python-generator-generate.txt`). Output:

```text
Loaded Prisma config from prisma.config.ts.
Prisma schema loaded from prisma/schema.prisma.
Error: Could not convert engine type queryEngine
```

Repo evidence for why this fails:

- `src/prisma/generator/generator.py:218-225` returns a manifest requiring `queryEngine`.
- `src/prisma/generator/models.py:390-403` documents that `binary_paths` only exists when the generator requests engines.
- Prisma 7 docs state Rust engines are no longer the default and engine env/configuration is legacy for v7.

### Prisma 7 generator payload shape probe

A custom line-protocol generator in `.omx/proof-spikes/prisma7/custom-probe` avoids the Python generator's `requiresEngines` manifest and records Prisma 7's `generate` params.

Commands:

- `npx prisma generate`: **PASS** (`logs/09-prisma7-custom-probe-line-protocol.txt`).
- Params inspection: **PASS** (`logs/10-prisma7-custom-probe-params-shape.txt`).

Observed shape notes:

- Top-level keys: `allowNoModels`, `datamodel`, `datasources`, `dmmf`, `generator`, `otherGenerators`, `schemaPath`, `version`.
- `version`: `3c6e192761c0362d496ed980de936e2f3cebcd3a` (not this repo's expected `5fe21811a6ba0b952a3bc71400666511fe3b902f`).
- `binaryPaths`: `None` / absent for the Rust-free generator path.
- `datasources[0]`: includes `name`, `provider`, `activeProvider`, `schemas`, `sourceFilePath`; no `url` when datasource URL is configured via `prisma.config.ts`.
- DMMF `datamodel.models[0]` includes extra v7 fields (`schema`, `nativeType`) while retaining many existing fields.

Python model parse checks against recorded Prisma 7 params:

- Without debug: **FAIL** on pinned engine/version mismatch (`logs/11-prisma7-pythondata-parse-no-debug.txt`). This is expected from `src/prisma/generator/models.py:372-387`.
- With `PRISMA_PY_DEBUG_GENERATOR=1`: **FAIL** on `datasources.0.url` missing (`logs/12-prisma7-pythondata-parse-debug.txt`). This is expected from `src/prisma/generator/models.py:436-443`, where `Datasource.url` is required.

A1 conclusion: **blocked release blocker**. It is not just a version pin; Prisma 7 rejects the requested engine manifest and the generated data shape fails current model validation even after bypassing the version gate.

## A2 runtime proof

### Current repo runtime boundary

Current code only supports binary Rust query engines:

- `src/prisma/generator/models.py:621-629`: `EngineType.binary` is accepted; `library` and `dataproxy` are rejected. There is no `client` engine type.
- `src/prisma/_base_client.py:395-403` and `514-522`: sync/async clients instantiate `SyncQueryEngine` / `AsyncQueryEngine` only when `_engine_type == EngineType.binary`; all other modes raise `NotImplementedError`.
- `src/prisma/engine/_query.py:58-84`: runtime resolves `BINARY_PATHS.query_engine`, starts a local subprocess, and sets `PRISMA_ENGINE_PROTOCOL='graphql'`.
- `src/prisma/engine/utils.py:69-125`: engine binary resolution and exact expected engine-version validation are hard gates.
- `src/prisma/cli/prisma.py:27-31`: CLI wrapper still sets `PRISMA_CLI_QUERY_ENGINE_TYPE='binary'`; Prisma v7 docs list this env var among removed/unsupported variables.

### Prisma 7 generated runtime shape

Runtime probe path: `.omx/proof-spikes/prisma7/runtime-probe`.

Command: `npm install --cache ../npm-cache && npx prisma generate`: **PASS** (`logs/13-prisma7-runtime-probe-generate-default.txt`).

Observed generated output:

- Generated files are TypeScript only (`generated/prisma/*.ts`, `generated/prisma/internal/*.ts`, `generated/prisma/models/*.ts`).
- `find generated -type f | rg 'query-engine|libquery|schema-engine|\.node$|\.dylib'`: **no matches**.
- `generated/prisma/internal/class.ts` imports `@prisma/client/runtime/query_compiler_fast_bg.sqlite.mjs` and `.wasm-base64.mjs` and includes an `engineVersion` hash.
- Generated namespace includes adapter types (`runtime.SqlDriverAdapterFactory`) and example client construction with `adapter`.
- `generated/prisma/internal/*.ts` headers say: `WARNING: This is an internal file that is subject to change!` Therefore those files are not an acceptable public boundary for A2.

Official docs checked:

- Prisma v7 upgrade guide: <https://www.prisma.io/docs/guides/upgrade-prisma-orm/v7>
  - `output` is required; `prisma-client` is the new path; driver adapters are required; several engine env vars were removed.
- Prisma generators reference: <https://www.prisma.io/docs/orm/prisma-schema/overview/generators>
  - `prisma-client` outputs plain TypeScript into a required custom output path.
- Prisma engines docs: <https://docs.prisma.io/docs/v6/orm/more/internals/engines>
  - Prisma v7 defaults to the TypeScript query compiler without Rust engine binaries; legacy Rust engine settings are optional/legacy.
- No-Rust-engine docs: <https://www.prisma.io/docs/orm/v6/prisma-client/setup-and-configuration/no-rust-engine>
  - Rust-free runtime uses `engineType = "client"` and driver adapters.

A2 conclusion: **blocked => JS bridge mandatory/default**. The only public v7 runtime surface proven here is generated Prisma Client in JS/TS with driver adapters. Direct Python runtime would need a new public API from Prisma or a maintained Python port of the query compiler/adapter contract; private generated internals are explicitly not acceptable.

## Recommended next plan (Task 6)

1. **Default plan: Option B JS runtime bridge.**
   - Keep Python generation/type-safety as the public Python API layer.
   - Add a narrow Node sidecar/bridge that imports the generated Prisma 7 client from the configured output and executes operations through the public `PrismaClient` API.
   - Use JSON-RPC/stdin or HTTP over localhost for Python-to-Node calls; define a stable request/response envelope owned by this project.
   - Driver adapter configuration should live in generated/bridge JS or project config, not in Python private Prisma internals.

2. **Generator migration slice before runtime work.**
   - Remove or conditionalize `requires_engines=['queryEngine']` for Prisma 7.
   - Make generator payload parsing tolerate Prisma 7 config-style datasources (`url` absent) and `binaryPaths: null`.
   - Replace exact engine-version equality with a compatibility matrix keyed by Prisma major/runtime mode.
   - Add explicit fail-fast messaging for unsupported direct binary mode under Prisma 7.

3. **Do not pass A2 by reading generated private internals.**
   - The generated `internal/*.ts` files can inform implementation, but not serve as the supported boundary.

4. **Fallback if Option B is too broad:** declare Prisma 5/6 maintenance compatibility and document Prisma 7 as unsupported until a JS bridge lands.

## Minimum release-blocking test matrix (Task 7)

### Generator / schema gate

- `npx prisma@7 generate` fixture with Python generator provider and Prisma config datasource: must either generate or fail with the project-owned explicit Prisma 7 unsupported message.
- Custom generator payload-shape regression: recorded keys must parse or fail on a deliberate compatibility guard, not incidental Pydantic missing-field errors.
- Existing tests: `tests/test_cli/test_generate.py`, `tests/test_generation/test_generator.py`, `tests/test_generation/test_validation.py`, `tests/test_generation/test_schema_dsl_parser.py`, `tests/test_generation/test_partial_types.py`.
- Exhaustive snapshots: `tests/test_generation/exhaustive/test_exhaustive.py` for both async and sync schemas.

### Runtime / bridge gate

- Prisma 7 default generated-client fixture (`provider = "prisma-client"`) must have no Rust query-engine files and must be callable only through the bridge/public JS client.
- JS bridge smoke test per supported provider, at minimum SQLite first; later Postgres/MySQL before release.
- Python client operations through the bridge: connect/disconnect, find/create/update/delete, transactions if supported, raw query behavior, errors.
- Existing runtime tests to keep: `tests/test_client.py`, `tests/test_actions.py`, `tests/test_batch.py`, `tests/test_http.py`, `tests/test_dotenv.py`, `tests/test_validator.py`, `tests/test_models.py`, `tests/test_engine.py`.

### CLI / environment gate

- Node version matrix: minimum Node 20.19, recommended 22.x, and current >=24 line. Local Node 25.4.0 satisfies current Prisma 7 engines but should remain a separate environment row.
- Ensure the project does not set removed Prisma 7 env vars in v7 mode (`PRISMA_CLI_QUERY_ENGINE_TYPE`, `PRISMA_CLIENT_ENGINE_TYPE`, `PRISMA_QUERY_ENGINE_BINARY`, etc.).
- npm cache should be fixture-local in CI to avoid global cache contamination.

### Compatibility/fallback gate

- v5/v6 maintenance tests continue to pass with current binary engine path.
- Prisma 7 unsupported path, if chosen as fallback, must be explicit and tested.
- Integration scripts (`tests/integrations/sync/test.sh`, `tests/integrations/recursive-types/test.sh`, `tests/integrations/custom-generator/test.py`) should be separate release-blocking jobs; current collection appears disabled by `tests/integrations/conftest.py` according to subagent test probe.

## Subagent findings integrated

Subagents spawned: 3, model `gpt-5.4-mini`.

- `019e6294-ab20-7041-b6bb-dc2adb1e76e8` (Ramanujan / debug-root-cause): identified generator version/data-shape gates, binary-only runtime construction, exact engine hash checks, and missing Prisma 7 bridge tests.
- `019e6294-bd4c-7a42-832d-44efebcb7070` (Bacon / test probe): mapped existing generator/runtime/exhaustive/integration coverage and identified missing Prisma 7 fixture and integration gating gaps.
- `019e6294-cee2-7fc3-9b66-c21e7e4e6bd2` (Einstein / change-slice): proposed safe slices (version/engine pin, binary artifact resolver, CLI bridge, engine-type guardrails) and flagged direct Python runtime as unsupported by current code.

## Verification summary

- PASS: Prisma 7 latest/version/Node engine lookup (`logs/00-environment.txt`).
- PASS: Fixture-local npm install for Prisma 7 (`logs/03-*`, `09-*`, `13-*`).
- PASS: Editable Python install in fixture venv after corrected path (`logs/04-*`).
- FAIL (expected evidence): current Python generator under Prisma 7 fails with `Could not convert engine type queryEngine` (`logs/05-*`).
- PASS: Custom generator probe captures Prisma 7 params (`logs/09-*`, `10-*`).
- FAIL (expected evidence): current PythonData parsing fails on version and then missing datasource `url` (`logs/11-*`, `12-*`).
- PASS: Prisma 7 default generated client has no query-engine binary and uses TypeScript runtime/query compiler/adapter shape (`logs/13-*`).
- PASS: No broad source edits; changes are contained to `.omx/proof-spikes/prisma7`.

## Leader nudge / verification scope note

Leader nudge received at 2026-05-26T04:49Z: disk was near full; avoid heavy npm/cache installs and broad tests; finish with existing A1/A2 command evidence and do not chase private internals. Verification after this point is limited to lightweight artifact validation, report completeness, source-cleanliness, and previously captured command logs.
