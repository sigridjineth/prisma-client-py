# Worker-4 Prisma 7 unsupported/deprecated inventory assist

Task: 8 — assist proof-spike synthesis and unsupported/deprecated inventory without broad source edits.
Date: 2026-05-26 UTC.
Scope: separate worker-4 findings artifact; this file intentionally does **not** overwrite `report.md` or task 5's owner output.

## Commands / evidence used

- `node --version` -> `v25.4.0`
- `npm --version` -> `11.7.0`
- `python3 --version` -> `Python 3.14.4`
- `npm view prisma version --json` -> `"7.8.0"`
- `npm view @prisma/engines-version version --json` -> `"7.8.0-6.3c6e192761c0362d496ed980de936e2f3cebcd3a"`
- `rg -n "MongoDB|mongodb|metrics|PRISMA_CLI_QUERY_ENGINE_TYPE|PRISMA_CLIENT_ENGINE_TYPE|PRISMA_QUERY_ENGINE_BINARY|OVERWRITE_DATASOURCES|/status|/metrics|/transaction|engineType|binaryTargets|prisma.config|skip-generate|skip-seed|--url|--schema" src tests docs databases examples scripts pyproject.toml setup.py README.md`
- Official docs checked:
  - Prisma 7 upgrade guide: https://www.prisma.io/docs/guides/upgrade-prisma-orm/v7
  - Prisma engines internals: https://docs.prisma.io/docs/v6/orm/more/internals/engines
  - Prisma generator reference: https://www.prisma.io/docs/orm/prisma-schema/overview/generators
  - Prisma ORM overview/config example: https://www.prisma.io/docs/orm

## Key external facts that should shape the final report

- Prisma 7 docs say MongoDB is not yet supported and MongoDB users should stay on Prisma ORM v6 for now.
- Prisma 7 minimum Node is 20.19.0; docs recommend 22.x. Local Node `v25.4.0` is newer than minimum, so any local failure under Node 25 should be recorded separately from Prisma 7 architectural compatibility.
- Prisma 7 ships as ESM; `package.json` should use `"type": "module"` for Prisma-side JS/TS code.
- Prisma 7 makes the `output` field required for the Prisma Client generator and no longer generates into `node_modules` by default.
- `prisma.config.ts` is now the default place for CLI/database configuration, including datasource URL, schema location, migrations, and seeds.
- Prisma 7 removed the Metrics preview feature.
- Prisma 7 removed environment variables currently relevant to this repo: `PRISMA_CLI_QUERY_ENGINE_TYPE`, `PRISMA_CLIENT_ENGINE_TYPE`, `PRISMA_QUERY_ENGINE_BINARY`, `PRISMA_QUERY_ENGINE_LIBRARY`, and several generate/migrate skip variables.
- Prisma 7 defaults to the TypeScript-based query compiler without Rust query-engine binaries. Prisma's engine docs mark Rust engine/binary configuration sections as v6.19-and-earlier / Rust-only, not a supported v7 default path.
- Prisma 7 direct database connections require driver adapters in the official JS client/runtime path.

## Unsupported/deprecated inventory table

| Area | Prisma 7 classification | Local evidence | Release-blocking implication | Recommended report wording |
| --- | --- | --- | --- | --- |
| MongoDB | **Unsupported explicit error / v7 blocker** | README/docs list MongoDB experimental support (`README.md:59-66`, `docs/index.md:62`); generator/test paths still special-case `mongodb` (`src/prisma/generator/models.py:153`, `tests/test_generation/test_validation.py:236`). | A Prisma 7 compatibility claim must exclude MongoDB or keep v6-only MongoDB support. | "MongoDB is not Prisma 7 compatible yet; keep MongoDB on v6 maintenance or disable with explicit error in a v7 lane." |
| Metrics API | **Unsupported explicit error / remove or bridge** | Python API exposes `get_metrics()` (`src/prisma/_base_client.py:359-393`, `478-510`); binary engine calls `/metrics` (`src/prisma/engine/_query.py:266-299`, `426-459`); database tests assert metrics (`databases/tests/test_metrics.py`, `databases/sync_tests/test_metrics.py`); docs advertise metrics (`docs/reference/metrics.md`). | Any direct Prisma 7 runtime proof cannot rely on the removed `/metrics` endpoint. A JS bridge would need a replacement using driver adapter instrumentation or Client Extensions, not parity by default. | "Metrics are removed in Prisma 7; mark Python metrics API unsupported/deprecated for v7 unless redesigned." |
| `prisma.config.ts` and env loading | **Supported with migration; current repo incomplete** | Current CLI/generator commands mainly pass `--schema` (`src/prisma/cli/commands/generate.py:58`, `src/prisma/cli/utils.py:123`, `databases/main.py:311-325`, `tests/utils.py:195`) and Python loads `.env`/`prisma/.env` itself (`src/prisma/_base_client.py:53-65`). | Test matrix needs fixture coverage for `prisma.config.ts`; datasource/env assumptions should not remain schema-only. | "Prisma 7 config/env loading requires a new config fixture and migration path; schema-only env behavior is not enough." |
| Required generator `output` | **Supported with migration; release blocker if missing** | README example generator has no `output` (`README.md:92-96`); database test template has no `output` (`databases/templates/schema.prisma.jinja2:6-22`). Prisma Client Python historically defaults output to installed package path (`README.md:131-134`). | A1 generator proof must include an explicit `output`; docs and fixtures must be updated before claiming compatibility. | "All Prisma 7 schemas must set generator output explicitly; legacy default output is obsolete." |
| Node minimum / ESM | **Supported with environment/package migration** | Local versions: Node `v25.4.0`, npm `11.7.0`; repo caches/runs Prisma CLI with Node (`src/prisma/cli/prisma.py:35-43`, `84-94`). | Node 20.19+ must be enforced/diagnosed. Prisma-side bridge/config scripts must be ESM-aware. | "Require Node >=20.19 and ESM-compatible Prisma-side code; local Node 25 is acceptable but may expose separate compatibility noise." |
| Removed CLI flags | **Supported with migration; some current commands break** | `--skip-generate` used by database test/push flows (`databases/main.py:324`, `tests/test_client.py:50`); many commands pass `--schema` (`databases/main.py:312,325`, `tests/utils.py:195`, Dockerfiles, test CLI). Prisma 7 removes `--skip-generate` from `migrate dev`/`db push`; `db execute --schema/--url` removed. | Database/test harness needs explicit generate sequencing and config-based commands. | "Remove reliance on Prisma 6 auto-generate/skip flags; call `prisma generate` explicitly and move connection config to prisma.config.ts." |
| Removed env vars | **Unsupported in v7; current repo relies on them** | CLI forces `PRISMA_CLI_QUERY_ENGINE_TYPE=binary` (`src/prisma/cli/prisma.py:27-31`); runtime forces `PRISMA_CLIENT_ENGINE_TYPE=binary` (`src/prisma/engine/_query.py:71-78`); generator reads `PRISMA_CLIENT_ENGINE_TYPE` (`src/prisma/generator/models.py:542-553`); binary override uses `PRISMA_QUERY_ENGINE_BINARY` (`src/prisma/engine/utils.py:81-90`); docs tell users to set `PRISMA_QUERY_ENGINE_BINARY` (`docs/reference/binaries.md:17-24`). | These cannot be part of a v7-supported path. Treat them as v5/v6 compatibility only. | "All Rust/binary env overrides are obsolete for Prisma 7 default runtime; gate or remove in v7 mode." |
| Rust binary cache/hash/platform logic | **Unsupported for default v7 runtime; v6-only unless Rust-engine compatibility mode is deliberately proven** | Cache installs Prisma CLI into `config.binary_cache_dir` (`src/prisma/cli/prisma.py:68-116`); query engine resolution executes platform binary and checks version (`src/prisma/engine/utils.py:38-129`); `binaryTargets` warnings/logic exist (`src/prisma/generator/models.py:451-461`, `src/prisma/engine/utils.py:42-60`). | Direct A2 cannot pass by finding a private Rust binary path. If maintained, this is legacy v5/v6 or explicit compatibility-mode only. | "Binary cache/platform resolution is not a Prisma 7 default runtime strategy; do not treat it as A2 proof." |
| HTTP query-engine endpoints (`/status`, root `/`, `/transaction/*`) | **Unknown/private/obsolete for v7; do not mark supported** | Runtime waits on `/status`, posts queries to `/`, and uses `/transaction/*` (`src/prisma/engine/_query.py:197-264`, `357-424`). | A2 must find a documented public boundary or fail to JS bridge. These endpoints look like Rust sidecar internals, not Prisma 7 public API. | "A2 is blocked unless an official non-JS public query boundary exists; private HTTP sidecar endpoints are not support evidence." |
| `OVERWRITE_DATASOURCES` datasource override | **Unsupported/unknown in v7** | Runtime injects `OVERWRITE_DATASOURCES` when datasource overrides are provided (`src/prisma/engine/_query.py:83-85`); Prisma 7 docs move datasource config to `prisma.config.ts` / adapter construction. | Datasource override behavior must be redesigned for JS bridge/adapters or config generation. | "Datasource overrides need a v7 design; legacy `OVERWRITE_DATASOURCES` should be considered unsupported until proven by docs." |
| Changed generator/runtime lifecycle | **Supported only with redesign/proof** | Current generation schema examples omit required output; runtime supports only `EngineType.binary` (`src/prisma/_base_client.py:395-410`); query builder sends GraphQL-ish payloads to Rust HTTP engine (`src/prisma/_base_client.py:412-423`). | Final gate should likely classify direct runtime as blocked unless another worker proves a public runtime boundary. | "Generator may be salvageable (A1), but runtime needs either official JS Client bridge or a new public supported boundary; do not imply direct binary parity." |
| Driver adapters | **Mandatory for direct JS-client DB connections** | No local adapter bridge found in grep; current Python code expects Prisma query engine to manage DB connection pool. | Option B JS bridge should include provider-specific adapter packages and connection pooling differences in test matrix. | "A JS runtime bridge must manage adapter selection and connection pooling semantics per provider." |

## Report consistency checklist for leader / worker-3

- Do not write "Prisma 7 compatible" unless both A1 generator proof and A2 public runtime proof pass, or unless Option B JS bridge proof is explicitly included.
- Keep MongoDB out of any v7-supported provider list.
- Mark metrics as removed/deprecated for v7; do not promise `Prisma.get_metrics()` parity.
- Separate Node 25 local failures from Prisma 7 architecture blockers.
- Any v7 schema examples should include explicit generator `output` and a project-root `prisma.config.ts`.
- Avoid citing `PRISMA_QUERY_ENGINE_BINARY`, `PRISMA_CLIENT_ENGINE_TYPE`, `/status`, `/metrics`, `/transaction/*`, or root HTTP query endpoints as supported Prisma 7 surfaces.
- If preserving v5/v6 compatibility, label Rust-binary code paths as legacy maintenance, not the Prisma 7 default.

## Minimal release-blocking tests implied by this inventory

1. Prisma 7 generator fixture with explicit `output` and `prisma.config.ts`.
2. Negative MongoDB fixture proving explicit v7 unsupported behavior or v6-only lane.
3. Metrics API test changed to skip/error under v7, or replacement bridge instrumentation test.
4. CLI harness test proving `db push`/migrate flows do not rely on removed `--skip-generate` behavior.
5. Runtime smoke test through a public supported boundary: either official JS Client + driver adapter bridge, or documented non-JS boundary. Private Rust HTTP endpoints should fail the supportability gate.
6. Provider matrix for adapter packages and connection-pool behavior if Option B is selected.
