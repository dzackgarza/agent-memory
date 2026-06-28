## Implementation plan

This PR owns the Config & Path Resolution implementation unit.

- [x] #38: add a failing CLI-boundary reproducer for a git repository with no origin remote so project-scoped plan storage cannot silently degrade to global scope.
- [x] #38: implement stable no-origin project identity through an explicit init-time project id or existing registry binding, with loud failure when neither exists.
- [x] #18: remove the runtime dependence on repo-local .agent-memory.toml where project identity and vault binding can be resolved from authoritative registry/symlink/global config state.
- [x] #29: normalize vault paths at the config boundary so literal ~ paths are not materialized under cwd.
- [x] Run `just test-ci` and update evidence below.

## Scope

- **Target issue set / subtree:** Epic #32 and children #18, #29, #38
- **GitHub milestone:** Config & Path Resolution
- **Issues to close on merge:** #18, #29, #38 when each fix lands with proof
- **Broader parent referenced only:** #32 epic

## Claim map

- [x] **#38 - no-origin project identity preserves project-scoped plan storage**
  - Proof obligations claimed: no-origin repos initialize only with a stable explicit/existing identity; project plan cards land under the project vault area; missing identity fails before any global write.
  - Evidence: red reproducer and green implementation commits on this branch; `just test-ci` passed.
- [x] **#18 - config resolves without repo-local .agent-memory.toml**
  - Proof obligations claimed: bound project config resolves from authoritative registry/symlink/global config state, not a checked-in repo-local TOML file.
  - Evidence: red reproducer and green implementation commits on this branch; `just test-ci` passed.
- [x] **#29 - vault paths are normalized at the boundary**
  - Proof obligations claimed: default_vault and configured vault paths do not expose or materialize literal ~ directories under cwd.
  - Evidence: red reproducer and green implementation commits on this branch; `just test-ci` passed.

## Exclusions / split conditions

CLI command crash/atomicity hardening is out of scope and remains under CLI Robustness & Vault Integrity.
