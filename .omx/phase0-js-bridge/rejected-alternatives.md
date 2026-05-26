# Rejected and Deferred Alternatives

Date: 2026-05-26
Status: Phase 0 decision record

## Chosen architecture

Persistent Node subprocess over stdio using a project-owned JSON-RPC-style protocol. The subprocess imports the generated Prisma JS/TS Client and executes operations through public Prisma Client APIs and driver adapters.

## Rejected alternatives

### Direct Python runtime against Prisma 7 internals

Decision: Rejected.

Rationale:

- Proof spike found no supported public Python runtime boundary for Prisma 7's TypeScript query compiler.
- Generated `internal/*.ts` files are explicitly private and subject to change.
- A Python port of the query compiler/adapter stack would be a separate large project, not a migration slice.

Do not re-open unless Prisma publishes a stable non-JS runtime/query boundary suitable for Python.

### Legacy Rust query-engine/env-var path as Prisma 7 default

Decision: Rejected for default Prisma 7 bridge mode; allowed only as legacy/v5/v6 maintenance if separately supported.

Rationale:

- Prisma 7 default is Rust-binary-free and driver-adapter based.
- Proof spike found current generator manifest `requiresEngines: ["queryEngine"]` is rejected by Prisma 7.
- Removed/unsupported env vars include the legacy engine-selection variables used by current code paths.

Do not claim Prisma 7 support by passing old binary resolver tests.

### One-shot Node process per query

Decision: Rejected.

Rationale:

- Process startup per query is high overhead.
- Interactive transactions require session state across multiple queries.
- Error/logging/lifecycle behavior is harder to make deterministic.

Could be retained only as a diagnostic fallback, not the mainline runtime.

### Local HTTP bridge first

Decision: Deferred, not initial architecture.

Rationale:

- HTTP would simplify manual debugging but adds port allocation, localhost security, lifecycle, firewall, and cleanup concerns.
- stdio is enough for local child-process ownership and easier to keep private to the Python client.

Revisit only if stdio becomes untestable or inadequate for streaming/large payloads.

### User-facing JS calls / TypeScript-first rewrite

Decision: Rejected for this migration.

Rationale:

- The project value is the generated Python API.
- Requiring users to call JS directly would violate compatibility gates.
- Python models, types, and client ergonomics should remain the public surface.

### Private Prisma generated file adapter

Decision: Rejected.

Rationale:

- Private generated `internal/*.ts` files are not stable contracts.
- They can inform implementation but must not become the bridge boundary.
- The bridge boundary is the generated public Prisma Client instance plus project-owned JSON protocol.

### Data Proxy/Accelerate first

Decision: Deferred.

Rationale:

- Requires external service/network semantics beyond a local stdio bridge.
- Does not solve local SQLite first-slice and Python compatibility gates.

### Full Rust legacy removal in Phase 0

Decision: Rejected.

Rationale:

- Phase 0 is contracts only.
- Existing v5/v6 users may need a maintenance path until JS bridge parity is proven.
- Removal/quarantine is a later release decision.

## Acceptance criteria

- Every rejected or deferred alternative has a rationale.
- Future agents have clear conditions for re-opening a decision.
- The chosen stdio bridge remains the only Phase 0 implementation target.
