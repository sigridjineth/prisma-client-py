# Prisma 7 Proof Spike Report

## 1. Exact versions tested

Captured on 2026-05-26 from the worker-1 proof-spike environment. Full command transcript: [`version-commands.log`](./version-commands.log). Machine-readable capture: [`versions.json`](./versions.json).

| Component | Exact version tested | Evidence command | Notes |
| --- | --- | --- | --- |
| Node.js | `v25.4.0` | `node --version` | Local runtime used for Prisma CLI proof. This is above Prisma 7's documented Node >=20.19 floor; if later failures occur, classify them separately from Node-minimum failures. |
| npm | `11.7.0` | `npm --version` | npm printed a non-blocking upgrade notice to 11.15.0 during the Prisma command. |
| Python | `Python 3.14.4` | `python3 --version`; `python3 -c 'import sys, platform; ...'` | Executable: `/opt/homebrew/opt/python@3.14/bin/python3.14`; platform: `macOS-15.7.3-arm64-arm-64bit-Mach-O`. Note this differs from the project support matrix of Python 3.8-3.12. |
| Prisma CLI latest/tested | `7.8.0` | `npm view prisma version --json`; `npm exec --yes --package prisma@7.8.0 -- prisma --version` | `prisma --version` succeeded under Node `v25.4.0`; reported Query Compiler `enabled`, operating system `darwin`, architecture `arm64`, Studio `0.27.3`, and no local `@prisma/client` installed. |
| `@prisma/engines-version` | `7.8.0-6.3c6e192761c0362d496ed980de936e2f3cebcd3a` | `npm view @prisma/engines-version version --json` | Prisma CLI also reported PSL `@prisma/prisma-schema-wasm 7.8.0-6.3c6e192761c0362d496ed980de936e2f3cebcd3a`, Schema Engine `schema-engine-cli 3c6e192761c0362d496ed980de936e2f3cebcd3a`, and Default Engines Hash `3c6e192761c0362d496ed980de936e2f3cebcd3a`. |

### Version command outcomes

All version-capture commands exited `0`:

- `node --version` -> `v25.4.0`
- `npm --version` -> `11.7.0`
- `python3 --version` -> `Python 3.14.4`
- `npm view prisma version --json` -> `"7.8.0"`
- `npm view @prisma/engines-version version --json` -> `"7.8.0-6.3c6e192761c0362d496ed980de936e2f3cebcd3a"`
- `npm exec --yes --package prisma@7.8.0 -- prisma --version` -> Prisma CLI `7.8.0`, Query Compiler `enabled`, Default Engines Hash `3c6e192761c0362d496ed980de936e2f3cebcd3a`

### Existing coverage and version-drift risks

A read-only test probe found the current repository already has version-related coverage around these files:

- `src/prisma/_config.py` is the current source of truth for Prisma `5.19.0` and engine hash `5fe21811a6ba0b952a3bc71400666511fe3b902f`.
- `tests/test_cli/test_version.py` checks version-command shape, but it does not pin equality to config values.
- `tests/test_engine.py` covers exact engine-version mismatch behavior.
- `src/prisma/cli/_node.py` and `tests/test_node/test_node.py` cover Node/npm resolver and minimum-version behavior, but not this proof spike's exact tested Node/npm versions.
- `scripts/docs.py` syncs version literals into `README.md`, `docs/index.md`, `docs/reference/config.md`, and `docs/reference/binaries.md`, but no dedicated regression test currently guards that sync.

This means the exact proof-spike versions above are evidence for this run, not a source-level compatibility claim. A future implementation phase should add equality-based regression checks before changing project defaults.

## Remaining report sections

The other proof-spike gates and inventories are owned by separate team lanes. This worker-1 section only records exact tested versions.
