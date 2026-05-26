# Phase 0 Integration Review Checklist

Date: 2026-05-26
Status: Phase 0 review gate

## Artifact presence

- [ ] `README.md` summarizes the artifact set and source inputs.
- [ ] `bridge-protocol.md` covers envelope, lifecycle, timeout/cancel, errors, serialization, and stdout/stderr split.
- [ ] `transaction-semantics.md` covers batch, interactive, IDs, rollback, cancellation, timeout, bridge death, disconnect, and nested behavior.
- [ ] `compatibility-matrix.md` covers public Python API gates and default-flip criteria.
- [ ] `adapter-support-matrix.md` covers SQLite first, PostgreSQL next, MySQL/MariaDB posture, required adapter packages, and unsupported/deferred providers.
- [ ] `migration-flag-default-policy.md` covers `PRISMA_PY_ENGINE`, opt-in/default flip, legacy Rust posture, and diagnostics.
- [ ] `golden-fixtures.md` and `fixtures/*.json` cover lifecycle, query success, errors, serialization, and transaction lifecycle.
- [ ] `ci-test-plan.md` names suite families, matrix dimensions, provider gates, and release gates.
- [ ] `rejected-alternatives.md` records direct runtime, Rust/env-var path, one-shot Node, HTTP-first, direct JS calls, and private internals decisions.

## Consistency checks

- [ ] Protocol version is consistent across markdown and JSON fixtures.
- [ ] Error code names match between protocol, fixtures, migration policy, and transaction semantics.
- [ ] Transaction timeout/cancellation behavior matches protocol error semantics.
- [ ] Adapter package assumptions are labeled assumptions until dependency validation.
- [ ] Compatibility breaks are also covered by migration diagnostics.
- [ ] CI gates prove every Required compatibility row before default flip.
- [ ] Unsupported MongoDB and metrics behavior are documented consistently.
- [ ] No artifact instructs implementers to use private Prisma generated internals as the bridge boundary.
- [ ] No artifact requires Rust query-engine binaries for JS bridge default mode.

## No-runtime-code check

Reviewers should verify:

```bash
git diff --name-only
```

Allowed Phase 0 paths from this task:

- `.omx/phase0-js-bridge/**`
- `docs/**` if additional docs are added

No `src/**`, `tests/**`, `scripts/**`, package metadata, or generated runtime code should be modified by this Phase 0 worker task.

## Ready-for-implementation lanes

After this review passes, implementation lanes can be assigned independently:

1. Generator output and JS bridge package skeleton.
2. Node bridge lifecycle/protocol runtime.
3. Python `JSBridgeEngine` feature-flagged integration.
4. Query/result parity and scalar serialization.
5. Transaction/failure-mode implementation.
6. CI packaging and provider hardening.
7. User docs and default flip cleanup.
