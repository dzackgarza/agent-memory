## Implementation plan

This PR owns the CLI Robustness & Vault Integrity implementation unit.

- [x] #24: make vault writes atomic when `add` reaches git commit failure and preserve unrelated staged vault changes.
- [x] #21: delete malformed/frontmatter-less memory notes without leaking parser assertions or leaving stale index links.
- [x] #19: make project initialization reconcile an existing project vault directory.
- [x] #20: document plan-card required fields/status values, cleanly report invalid plan field input, and add `--body-file`.
- [x] #30: improve recurring CLI misuse diagnostics for command/option mistakes.
- [x] Run `just test` and update evidence below.

## Scope

- **Target issue set / subtree:** Epic #31 and children #24, #21, #20, #19, #30
- **GitHub milestone:** CLI Robustness & Vault Integrity
- **Issues to close on merge:** Closes #24, closes #21, closes #20, closes #19, closes #30
- **Broader parent referenced only:** Refs #31 epic

## Claim map

- [x] **#24 - atomic vault writes preserve clean state on commit failure**
  - Proof obligations claimed: forced vault commit failure leaves no partial memory file and does not commit unrelated staged vault content.
  - Evidence: red reproducer and green implementation commits on this branch; `just test` passed.
- [x] **#21 - malformed note deletion is typed and index-clean**
  - Proof obligations claimed: deleting a frontmatter-less linked memory removes the file and index link through a typed malformed-memory path, without catching `AssertionError`.
  - Evidence: red reproducer and green implementation commits on this branch; `just test` passed.
- [x] **#19 - project init reconciles existing vault directories**
  - Proof obligations claimed: `init project` handles pre-existing project vault directories and remains idempotent.
  - Evidence: red reproducer and green implementation commits on this branch; `just test` passed.
- [x] **#20 - plan add help and validation are discoverable**
  - Proof obligations claimed: `plan add --help` lists required fields/status values, invalid field values produce clean field-level errors, and `--body-file` populates card bodies.
  - Evidence: red reproducer and green implementation commits on this branch; `just test` passed.
- [x] **#30 - CLI misuse diagnostics are actionable**
  - Proof obligations claimed: common command/option mistakes report concrete parser guidance without raw tracebacks or assertion class names.
  - Evidence: red reproducer and green implementation commits on this branch; `just test` passed.

## Exclusions / split conditions

Config & Path Resolution is out of scope and remains under epic #32.
