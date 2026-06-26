## Scaffold draft PR — milestone claim

This is a planning/claim scaffold so this work unit is picked up as one coherent,
review-sized PR instead of fragmented one-issue-per-PR. It carries no implementation
yet; the implementer pushes commits here and switches `Refs` to `Closes` as each
obligation lands with proof.

- **Target issue set / subtree:** Epic #14 and children #5, #6, #23
- **GitHub milestone:** Vault Synchronization and Integrity
- **Issues to close on merge:** #5, #6, #23 (when each lands with proof)
- **Broader parent referenced only:** #14 (epic)
- **Proof obligations claimed:** OKF frontmatter is preserved/reconciled (not dropped) on normalization; auto-sync pushes with conflict awareness; broken `[[links]]` are surfaced and rewritten across reorganizations.
- **Proof obligations not claimed:** None deferred within this unit.

## Local implementation plan
- #5: preserve+reconcile extra frontmatter.
- #6: conflict-aware auto-sync.
- #23: bulk link refactor + staleness prevention.

## Evidence required
Each claimed issue lands with a committed red reproducer that fails on current behavior
and passes after the fix, verified under `just test-ci`.

## Exclusions / split conditions
Card schema design is out of scope (Card Schema System).
