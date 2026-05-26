# Migration Flag and Default Policy

Date: 2026-05-26
Status: Phase 0 rollout contract

## Objective

Introduce the JS bridge without surprising existing users or claiming Prisma 7 parity before the contract, fixtures, and CI gates pass.

## Engine selection flag

Development flag:

```text
PRISMA_PY_ENGINE=js-bridge|rust-legacy
```

Meanings:

| Value | Meaning |
| --- | --- |
| `js-bridge` | Use the generated Node stdio bridge and Prisma JS/TS Client. Required for Prisma 7 mode. |
| `rust-legacy` | Use the existing Rust query-engine binary path. Intended for v5/v6 maintenance and compatibility testing only. |

A later implementation may also expose an explicit generator config value, but the environment flag is the Phase 0 diagnostic and CI switch.

## Default timeline

| Stage | Default | Requirements |
| --- | --- | --- |
| Phase 0 | No runtime default change | Contracts/fixtures/matrices/CI plan only. |
| Phase 1-3 development | `rust-legacy` or explicit opt-in depending on current repo defaults | `js-bridge` requires explicit flag and clear experimental warning. |
| SQLite preview | Explicit opt-in `js-bridge` | SQLite lifecycle/CRUD/serialization/error/transaction fixtures pass. |
| Multi-provider preview | Explicit opt-in `js-bridge` | SQLite + PostgreSQL pass, MySQL/MariaDB status documented. |
| Default flip candidate | `js-bridge` for Prisma 7 generated clients | All Required compatibility gates pass or documented breaks are approved. |
| Legacy retirement | `rust-legacy` v5/v6 only or removed | Separate release decision; not a Phase 0 decision. |

## Prisma major-version posture

- Prisma 7 generated clients should use JS bridge mode.
- Prisma 5/6 maintenance may continue to use Rust legacy mode while supported.
- If a user requests Prisma 7 with `rust-legacy`, fail with an explicit message unless a future proof shows a supported Prisma 7 Rust-engine path.
- If a user requests Prisma 5/6 with `js-bridge`, treat it as unsupported unless the bridge is explicitly tested for that major.

## Diagnostics

Required messages before default flip:

| Situation | Diagnostic |
| --- | --- |
| Node missing | `NODE_NOT_FOUND`: install Node satisfying Prisma 7 minimum and re-run generated bridge setup. |
| Node version unsupported | `NODE_UNSUPPORTED_VERSION`: show observed version and required range. |
| Generated bridge files missing | `PRISMA_CLIENT_NOT_FOUND`: re-run generation and bridge dependency install/build. |
| Adapter dependency missing | `ADAPTER_NOT_FOUND`: show provider, package, and install command. |
| Prisma 7 + Rust env var override | `RUST_ENGINE_UNSUPPORTED_FOR_PRISMA7`: explain `PRISMA_QUERY_ENGINE_BINARY` applies only to legacy mode. |
| Unsupported provider | `PROVIDER_UNSUPPORTED`: show provider and support matrix. |
| Metrics called in JS bridge mode | `METRICS_UNSUPPORTED_IN_JS_BRIDGE`: explain v6-only/removed behavior and future observability path. |

## User-facing migration notes required before release

- Node is a runtime dependency in Prisma 7 bridge mode.
- Driver adapter packages are required in the generated bridge project.
- Prisma 7 requires explicit Prisma Client output.
- Datasource configuration moves toward `prisma.config.ts` / adapter setup; legacy Python datasource overrides are provider-gated.
- Rust binary override docs apply only to legacy mode.
- MongoDB and metrics are not part of the first Prisma 7 default claim.
- Users do not need to write JavaScript calls for normal Python client operations.

## CI switch policy

CI must exercise both lanes while both exist:

- `PRISMA_PY_ENGINE=rust-legacy`: legacy Python tests that remain v5/v6-scoped.
- `PRISMA_PY_ENGINE=js-bridge`: Prisma 7 bridge tests.

A test that passes only because the flag is unset is not sufficient. Release gates must name the engine lane.

## Acceptance criteria

- The rollout has an opt-in preview stage and an explicit default-flip gate.
- The flag values are named and unambiguous.
- Prisma major-version behavior is explicit.
- Missing dependency and unsupported-provider diagnostics are specified.
- Legacy Rust behavior is quarantined rather than silently mixed with Prisma 7 bridge mode.
