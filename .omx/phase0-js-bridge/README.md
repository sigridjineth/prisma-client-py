# Phase 0: Prisma 7 JS/TS Bridge Contracts

Date: 2026-05-26
Status: Phase 0 contract artifact set
Scope: documentation, contracts, matrices, fixture examples, and CI plan only; no runtime implementation code

## Purpose

This directory is the team-run evidence and mirrored working copy for the canonical Phase 0 review bundle in `docs/prisma7-js-bridge/phase0/`.

The Python API remains the public API. Query execution is delegated to a generated Node bridge process that imports the generated Prisma JS/TS Client and talks to Python over newline-delimited JSON messages on stdio.

## Source inputs

These artifacts are derived from the leader-approved planning bundle:

- `.omx/plans/prd-prisma7-js-bridge-migration.md`
- `.omx/plans/test-spec-prisma7-js-bridge-migration.md`
- `.omx/plans/team-handoff-prisma7-js-bridge-phase0.md`
- `.omx/ultragoal/goals.json` active goals `G001` through `G006`
- proof spike evidence under `.omx/proof-spikes/prisma7/`

The worker did not mutate `.omx/ultragoal`; that path remains leader-owned.


## Canonical source of truth

`docs/prisma7-js-bridge/phase0/` is the canonical Phase 0 contract for later implementation work. `.omx/phase0-js-bridge/` is retained as team-run evidence and a mirrored working copy; if a mirrored `.omx` artifact ever diverges, the `docs/` artifact wins. Golden fixture JSON is mirrored into `docs/prisma7-js-bridge/phase0/fixtures/` so Phase 1 agents do not need to infer contracts from hidden state.

## Artifact map

| Artifact | Purpose | Primary goal coverage |
| --- | --- | --- |
| [`bridge-protocol.md`](bridge-protocol.md) | Stdio protocol, lifecycle, request/response envelopes, timeouts, cancellation, errors, and serialization rules. | G001 |
| [`transaction-semantics.md`](transaction-semantics.md) | Batch and interactive transaction behavior, transaction IDs, rollback/failure policy, and nested transaction handling. | G002 |
| [`compatibility-matrix.md`](compatibility-matrix.md) | Python public API compatibility criteria and default-flip gates. | G003 |
| [`adapter-support-matrix.md`](adapter-support-matrix.md) | Provider/driver-adapter support phases and package assumptions. | G003 |
| [`migration-flag-default-policy.md`](migration-flag-default-policy.md) | `PRISMA_PY_ENGINE` flag, rollout posture, default flip, diagnostics, and release gating. | G004 |
| [`rejected-alternatives.md`](rejected-alternatives.md) | Decision log for rejected or deferred runtime architectures. | G004 |
| [`golden-fixtures.md`](golden-fixtures.md) | Fixture contract and review checklist for golden request/response/error/serialization/transaction examples. | G005 |
| [`ci-test-plan.md`](ci-test-plan.md) | Provider matrix, exact suite families, commands, and pass/fail gates. | G005 |
| [`integration-review-checklist.md`](integration-review-checklist.md) | Cross-artifact consistency checklist for Phase 0 review. | G006 |
| [`fixtures/manifest.json`](fixtures/manifest.json) and sibling JSON files | Concrete golden fixture examples for later tests. | G005 |

## Phase 0 invariants

- The bridge protocol is project-owned and versioned independently from Prisma private internals.
- stdout from the Node bridge is protocol-only; diagnostic logs go to stderr or a later structured side channel.
- The default Prisma 7 path must not rely on Rust query-engine binaries or removed Prisma 7 engine environment variables.
- Node is a runtime dependency for Prisma 7 bridge mode.
- SQLite is the first implementation target; networked databases require explicit adapter gates.
- MongoDB and removed metrics behavior are not part of the initial Prisma 7 default claim.
- Existing Python import paths, CRUD signatures, model hydration, context-manager behavior, and exception classes are compatibility gates before the JS bridge becomes the default.

## Review stop condition

Phase 0 is review-ready when every artifact above exists, JSON fixtures validate, no runtime code is changed, and implementation lanes can be assigned without re-opening the architecture decision.
