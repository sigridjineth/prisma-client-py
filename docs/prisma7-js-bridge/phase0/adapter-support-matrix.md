# Phase 0 adapter support matrix

<!-- markdownlint-disable MD013 -->
Status: Phase 0 contract. No runtime implementation is authorized by this artifact.

The Prisma 7 JS bridge must instantiate generated Prisma JS/TS Client with a
Prisma driver adapter inside the generated Node bridge project. Python users
configure providers through the existing Python/generator workflow; they do not
construct adapter instances directly.

## Support levels

| Level | Meaning | Release gate |
| --- | --- | --- |
| First | Required for the first JS bridge opt-in integration target. | Must pass before any opt-in release is called usable. |
| Next | Planned after SQLite; can ship when provider-specific fixtures and CI services pass. | Must pass before provider is documented as supported. |
| Deferred | Known provider/adapter path, but not in first bridge scope. | Must have clear diagnostics or legacy-only guidance. |
| Unsupported | Not accepted by the JS bridge until a new Phase 0 decision changes this matrix. | Must fail early with actionable message. |

## Adapter package matrix

| Provider / deployment target | Prisma schema provider | Required JS package(s) in generated bridge project | Level | Scope notes | Pass criteria | Fail criteria |
| --- | --- | --- | --- | --- | --- | --- |
| Local SQLite file | `sqlite` | `@prisma/client`, `@prisma/adapter-better-sqlite3`; TypeScript projects may also need `@types/better-sqlite3` as a dev dependency. | First | Primary Phase 1 integration path. Use a local `file:` URL and generated Prisma Client output. Native dependency installation must be surfaced clearly. | Generate bridge project, install packages, instantiate `PrismaBetterSqlite3`, receive bridge `ready`, pass CRUD/scalar/error/batch transaction fixtures. | Missing native build support, missing adapter, corrupted stdout protocol, or any Python API parity regression. |
| SQLite-compatible libSQL / Turso | `sqlite` | `@prisma/client`, `@prisma/adapter-libsql`. | Deferred | Useful for remote SQLite-compatible deployments but not required for first local SQLite proof. | Later provider fixture passes URL/auth-token configuration and CRUD/scalar parity. | Treating libSQL as local file SQLite without explicit config or silently ignoring auth token. |
| Cloudflare D1 | `sqlite` | `@prisma/client`, `@prisma/adapter-d1`. | Deferred | D1 binding model is not a normal local Node subprocess fit; requires separate runtime design. | Later bridge design documents how a Python subprocess supplies D1 binding equivalent, or declares unsupported. | Claiming support while requiring Cloudflare Worker globals in local Python runtime. |
| Self-hosted PostgreSQL | `postgresql` | `@prisma/client`, `@prisma/adapter-pg`. | Next | First networked DB candidate. Must validate connection pooling, schema option behavior, and Decimal/JSON/Bytes mappings. | Docker/service-backed CI passes CRUD, select/include, scalar, error, batch transaction, and connection failure fixtures. | Provider marked supported without networked CI or with pool/timeout behavior undocumented. |
| Neon / serverless PostgreSQL | `postgresql` | `@prisma/client`, `@prisma/adapter-neon`. | Deferred | Serverless transport and pooling differences should follow after self-hosted PostgreSQL. | Later CI or documented smoke environment validates connection string and cold-start/timeout diagnostics. | Reusing `@prisma/adapter-pg` assumptions for Neon-specific runtime behavior without tests. |
| Prisma Postgres | `postgresql` | `@prisma/client`, `@prisma/adapter-ppg`. | Deferred | Managed Prisma Postgres path; not needed for first bridge parity. | Later tests prove setup and provider-specific connection diagnostics. | Generated bridge installs the package by default for all PostgreSQL users. |
| CockroachDB | `cockroachdb` | Likely PostgreSQL-compatible adapter path; exact adapter choice must be revalidated before support. | Deferred | Prisma supports CockroachDB as a PostgreSQL-compatible provider, but bridge support must not be inferred without fixtures. | Dedicated CockroachDB fixtures pass ID generation, transaction retry, and scalar behavior. | Marking supported based only on PostgreSQL tests. |
| Self-hosted MySQL / MariaDB | `mysql` | `@prisma/client`, `@prisma/adapter-mariadb`. | Next | Second networked DB candidate. The adapter uses the JavaScript `mariadb` driver path. | Docker/service-backed CI passes CRUD, scalar, error, and batch transaction fixtures for MySQL and/or MariaDB. | Provider marked supported without proving connection options, Decimal/DateTime behavior, and transaction rollback. |
| PlanetScale | `mysql` | `@prisma/client`, `@prisma/adapter-planetscale`; Node versions below built-in `fetch` support may need `undici`. | Deferred | Serverless MySQL path with different transaction and connection assumptions. | Later fixtures document transaction limits and pass provider-specific setup smoke tests. | Treating PlanetScale as equivalent to self-hosted MySQL for transaction semantics. |
| Microsoft SQL Server | `sqlserver` | `@prisma/client`, `@prisma/adapter-mssql` / `node-mssql` adapter path must be verified against current Prisma docs before support. | Deferred | Not in first bridge release; package naming and setup must be confirmed in the implementation phase. | Later SQL Server service CI passes provider-specific scalar and transaction fixtures. | Shipping unverified package names or support claims. |
| MongoDB | `mongodb` | None approved for JS bridge Phase 0. | Unsupported | Prisma supports MongoDB generally, but driver-adapter bridge support is not approved for this phase. | Clear unsupported-provider diagnostic points to legacy/deferred documentation. | Attempting to instantiate SQL driver adapters or silently falling back. |
| Unsupported/custom providers | Any unknown provider or community adapter | None approved by default. | Unsupported | Community adapters require a separate compatibility review and fixture set. | Early validation rejects the provider unless an explicit adapter configuration is documented and tested. | Guessing package names or installing community packages automatically. |

## Adapter selection rules

1. Adapter selection is generated from the Prisma datasource provider plus an explicit
   provider support table; never infer a package name from arbitrary provider text.
2. The generated bridge project includes only the adapter package needed for the
   selected provider target, not every possible adapter.
3. Missing adapter packages fail during bridge startup with a Python exception that
   names the required package and the install/build command that should have produced it.
4. Provider support is opt-in by matrix status: `First` and passing `Next` providers
   may run in JS bridge mode; `Deferred` and `Unsupported` providers must not silently
   choose JS bridge mode.
5. SQLite is the first implementation target because it requires no external database
   service and can prove protocol, subprocess, serialization, and Python API parity
   before networked DB complexity is added.

## Provider rollout gates

### SQLite first

SQLite support is complete when all of these pass:

- [ ] Generated bridge package contains `@prisma/client` and `@prisma/adapter-better-sqlite3`.
- [ ] Local file database URL is supplied through the Prisma 7 config path used by the generated bridge.
- [ ] Bridge starts, emits `ready`, answers `healthcheck`, and shuts down without leaving a process.
- [ ] Python CRUD, select/include/filter/order, scalar, error mapping, and batch transaction fixtures pass.
- [ ] Native dependency install/build failures produce actionable Python diagnostics.
- [ ] Tests confirm JS bridge mode does not spawn the Rust query engine.

### PostgreSQL next

PostgreSQL support can be marked supported only when:

- [ ] Generated bridge package contains `@prisma/client` and `@prisma/adapter-pg`.
- [ ] CI provides a PostgreSQL service and a direct connection string.
- [ ] CRUD, JSON, Decimal, Bytes, DateTime, relation, error, and batch transaction fixtures pass.
- [ ] Connection timeout/pool behavior is documented because Prisma 7 adapter defaults may differ from legacy assumptions.
- [ ] Provider-specific setup docs include self-hosted PostgreSQL first; Neon/Supabase/serverless variants remain deferred unless separately tested.

### MySQL / MariaDB next

MySQL/MariaDB support can be marked supported only when:

- [ ] Generated bridge package contains `@prisma/client` and `@prisma/adapter-mariadb`.
- [ ] CI provides a MySQL or MariaDB service with stable credentials.
- [ ] CRUD, Decimal, DateTime, relation, error, and batch transaction fixtures pass.
- [ ] PlanetScale/serverless behavior is documented as deferred unless separate tests pass.

## Unsupported-provider behavior

For every provider not marked `First` or passing `Next`, JS bridge startup must fail
before any query is sent. The Python exception should include:

- datasource provider name;
- JS bridge support level (`Deferred` or `Unsupported`);
- required adapter package if known;
- whether `PRISMA_PY_ENGINE=rust-legacy` may be used temporarily;
- link or pointer to this matrix.

## Phase 0 acceptance criteria

- This matrix names the first provider target, next provider targets, required
  adapter packages, unsupported providers, and pass/fail gates.
- Compatibility status does not imply implementation exists; it defines the later
  implementation checklist.
- No runtime code, generated templates, package manifests, or CI workflows are
  changed by this Phase 0 artifact.

## Reference anchors

- Phase 0 PRD: `.omx/plans/prd-prisma7-js-bridge-migration.md`.
- Phase 0 test spec: `.omx/plans/test-spec-prisma7-js-bridge-migration.md`.
- Prisma driver adapters overview: <https://www.prisma.io/docs/orm/core-concepts/supported-databases/database-drivers>.
- Prisma SQLite adapter setup: <https://www.prisma.io/docs/concepts/database-connectors/sqlite>.
- Prisma PostgreSQL adapter setup: <https://docs.prisma.io/docs/orm/core-concepts/supported-databases/postgresql>.
- Prisma MySQL/MariaDB adapter setup: <https://www.prisma.io/docs/orm/overview/databases/mysql>.
