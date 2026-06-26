## Scaffold draft PR — milestone claim

This is a planning/claim scaffold so this work unit is picked up as one coherent,
review-sized PR instead of fragmented one-issue-per-PR. It carries no implementation
yet; the implementer pushes commits here and switches `Refs` to `Closes` as each
obligation lands with proof.

- **Target issue set / subtree:** Epic #31 and children #24, #21, #20, #19, #30
- **GitHub milestone:** CLI Robustness & Vault Integrity
- **Issues to close on merge:** #24, #21, #20, #19, #30 (when each fix lands with proof)
- **Broader parent referenced only:** #31 (epic; closes only when the whole subtree is satisfied)
- **Proof obligations claimed:** Atomicity: forced failure mid-`add` leaves no partial file and a clean vault. Graceful handling: delete/plan add/init project handle real inputs without uncaught AssertionError/FileExistsError.
- **Proof obligations not claimed:** None deferred within this unit.

## Local implementation plan
- Per bug: write red reproducer, fix at the shared boundary (not per-caller), confirm vault clean after the command.
- Sequence #24 (data integrity) first.

## Evidence required
Each claimed issue lands with a committed red reproducer that fails on current behavior
and passes after the fix, verified under `just test-ci`.

## Exclusions / split conditions
Vault/config path resolution (Config & Path Resolution) and structural link maintenance (Vault Sync & Integrity) are out of scope.
