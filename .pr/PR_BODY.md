## Scaffold draft PR — milestone claim

This is a planning/claim scaffold so this work unit is picked up as one coherent,
review-sized PR instead of fragmented one-issue-per-PR. It carries no implementation
yet; the implementer pushes commits here and switches `Refs` to `Closes` as each
obligation lands with proof.

- **Target issue set / subtree:** Epic #32 and children #18, #29
- **GitHub milestone:** Config & Path Resolution
- **Issues to close on merge:** #18, #29 (when each fix lands with proof)
- **Broader parent referenced only:** #32 (epic)
- **Proof obligations claimed:** Config resolves with no repo-local .agent-memory.toml present; a literal `~` path is rejected/normalized rather than materialized in cwd.
- **Proof obligations not claimed:** None deferred within this unit.

## Local implementation plan
- #18: derive config from vault registry + git remote; remove .agent-memory.toml dependence.
- #29: normalize `default_vault` at the config boundary; fail loud on literal `~`.

## Evidence required
Each claimed issue lands with a committed red reproducer that fails on current behavior
and passes after the fix, verified under `just test-ci`.

## Exclusions / split conditions
CLI command crash/atomicity hardening is out of scope (CLI Robustness & Vault Integrity).
