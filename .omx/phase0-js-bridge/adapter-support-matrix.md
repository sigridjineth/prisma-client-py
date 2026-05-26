# Adapter Support Matrix

Date: 2026-05-26
Status: Phase 0 provider/adapter contract

## Driver-adapter principle

Prisma 7 bridge mode calls the public generated Prisma JS/TS Client. Database connectivity is owned by Prisma driver adapters and JavaScript database drivers, not by Python Rust query-engine binaries.

Adapter package names and versions must be validated in implementation CI against the pinned Prisma target (`prisma` / `@prisma/client` 7.8.0 in the Phase 0 plan) before release.

## Support phases

| Phase | Provider | Adapter package assumption | JS driver assumption | Status | Required gates |
| --- | --- | --- | --- | --- | --- |
| 1 | SQLite | `@prisma/adapter-better-sqlite3` | `better-sqlite3` | First supported target | Generate, install/build, ready/healthcheck, CRUD, serialization, datasource override, batch tx, interactive tx, raw query decision. |
| 2 | PostgreSQL | `@prisma/adapter-pg` | `pg` | Next networked DB target | Same as SQLite plus service container, connection timeout/pool settings, rollback under concurrent connections. |
| 2/3 | MySQL/MariaDB | Prisma-maintained MariaDB/MySQL adapter path, expected `@prisma/adapter-mariadb` if validated | `mariadb` | Next after PostgreSQL if adapter/package validation passes | CRUD, transactions, raw query parameterization, pool timeout mapping. |
| 3 | SQL Server | Prisma-maintained SQL Server adapter path if validated | `node-mssql` | Deferred | Dedicated adapter and CI service validation. |
| 3 | libSQL/Turso | `@prisma/adapter-libsql` if validated | libSQL driver | Deferred | Separate datasource/edge semantics. |
| 3 | Serverless Postgres providers | Neon/Prisma Postgres/PlanetScale adapters as applicable | provider-specific | Deferred | Separate connection and deployment semantics. |
| v6-only/deferred | MongoDB | none for initial Prisma 7 bridge | n/a | Unsupported in first Prisma 7 default | Explicit provider unsupported diagnostic. |

## SQLite first-slice contract

SQLite is first because it has the smallest CI surface and exercises the bridge boundary without a networked service.

Required SQLite coverage:

- `prisma.config.ts` or equivalent v7 config loads a file datasource.
- Generated Prisma Client output is explicit and project-local.
- Generated bridge package declares adapter and driver dependencies.
- Bridge starts and emits `bridge.ready`.
- `bridge.healthcheck(requireDatabase=true)` succeeds.
- CRUD and select/include/filter/order fixtures pass.
- Decimal, BigInt, DateTime, Bytes, JSON, enum, null, relation, and raw row serialization fixtures pass where schema supports them.
- SQLite datasource override maps Python `datasource={'url':'file:...'}` to adapter construction or validated config injection.
- Batch transaction commits and rolls back.
- Interactive transaction commits, rolls back on Python exception, times out, and taints on cancellation.

## PostgreSQL next-slice contract

PostgreSQL is the first networked database gate because it exercises driver adapter pool/connection settings and transaction pinning.

Required PostgreSQL coverage:

- `@prisma/adapter-pg` and `pg` versions match the Prisma target.
- CI service uses explicit health checks and deterministic database setup.
- Pool timeout defaults are documented because Prisma 7 driver adapters may differ from legacy Rust-engine defaults.
- Transaction rollback tests prove uncommitted writes are not visible outside the transaction and are removed after rollback.
- Datasource override support is either implemented through adapter options or explicitly unsupported.

## MySQL/MariaDB posture

MySQL/MariaDB support is next after PostgreSQL only after package validation confirms the Prisma 7 adapter and driver combination.

Required decisions before claiming support:

- Confirm adapter package name and import path against current Prisma docs/npm metadata.
- Confirm parameter placeholder behavior for raw queries.
- Confirm transaction isolation and timeout mapping.
- Confirm CI service image and charset/collation defaults.

Until then, MySQL/MariaDB is documented as planned, not supported.

## Unsupported/deferred providers

| Provider/feature | Initial status | Rationale |
| --- | --- | --- |
| MongoDB | Unsupported/v6-only | Prisma 7 initial support path excludes MongoDB in the proof-spike/planning evidence. |
| Data Proxy/Accelerate | Deferred | Requires deployment/network semantics beyond local stdio bridge. |
| Edge/serverless adapters | Deferred | Different process, pooling, and credential models. |
| Custom community adapters | Deferred | Must be opt-in and outside default CI guarantee. |

## Adapter diagnostics contract

Missing or incompatible adapter dependencies must fail during bridge startup with structured errors:

- `ADAPTER_NOT_FOUND`
- `ADAPTER_VERSION_UNSUPPORTED`
- `PROVIDER_UNSUPPORTED`
- `DATASOURCE_OVERRIDE_UNSUPPORTED`
- `NODE_UNSUPPORTED_VERSION`

Diagnostics must name the provider, expected package, generated bridge directory, and install command suggestion.

## Acceptance criteria

- SQLite first target and PostgreSQL next target are explicit.
- Adapter package assumptions are named but must be validated before release.
- Unsupported/deferred providers have rationale.
- Provider support requires lifecycle, CRUD, serialization, transaction, raw query, and packaging evidence.
