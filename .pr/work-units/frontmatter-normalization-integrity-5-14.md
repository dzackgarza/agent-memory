## Intended result

Any planned normalization path preserves OKF frontmatter and reconciles duplicate/extra frontmatter blocks into the canonical header or fails loudly naming the file; it never silently drops metadata fields while making a formatting change look harmless.

## Scope

- Included: #5's full remaining Vault Synchronization and Integrity requirement; tests/fixtures for OKF field preservation and duplicate-frontmatter reconciliation/fail-loud behavior; parent #14 closure only if #5 is fully evidenced.
- Excluded: unrelated vault sync issues already completed under #14, broad formatter replacement without the OKF invariant, and manual cleanup of unrelated historical vault files.
- Preserved behavior: normalization must not launder malformed metadata into empty/default values or erase non-canonical fields.

## GitHub tracking

- Target issue set / subtree: #5 as the remaining open child of #14
- Milestone: Vault Synchronization and Integrity
- Closes on merge:
  - Closes #5
  - Closes #14
- References only:
  - Refs #41

## Implementation plan

The normalization command now exists. It reconciles each selected memory before the real IWE normalization boundary, then proves the double-frontmatter fixture preserves canonical OKF metadata and rejects unreconcilable fields with the affected file path.

## Claim map

- [x] **#5/#14 - normalization preserves or loudly rejects OKF/extra frontmatter**
  - Proof obligations claimed: all OKF fields preserved; extra/duplicate frontmatter reconciled or named in a loud failure; no silent field discard; parent #14 has no remaining open child obligations after #5.
  - Partial / not claimed: no closure of #14 if #5 is split or if normalization remains only a future requirement without executable proof.
  - Evidence required: red fixture for the concrete double-frontmatter failure mode, green normalization/reconciliation test, and explicit parent-subtree check before ready.
  - Current evidence: `tests/test_cli_workflows.py::test_maintain_normalize_reconciles_extra_okf_frontmatter_before_iwe_writes` and `tests/test_cli_workflows.py::test_maintain_normalize_fails_before_iwe_writes_unreconcilable_frontmatter` exercise the real CLI path.

## Automated gates

Keep draft until #5 has implementation evidence or the PR explicitly removes #14 from the closure list because the work was split.
