## Intended result

Deferred review feedback from merged PR #65 is re-evaluated against current `main`, and every true finding is fixed through the real CLI/module boundary with tests. The result preserves structured CLI errors and keeps non-card commands usable when card schema loading is irrelevant.

## Scope

- Included: all deferred review items listed in #78: `CardLookupError` CLI boundary handling, root `list` command independence from schema-dependent card registration, and `CardConfigError` path/context typing around importlib resources traversables.
- Excluded: the full #52 vault-reading resilience story, #62 todo mutation, and broader QC gate restoration #69.
- Preserved behavior: accepted fixes must not turn schema/config failures into silent defaults, empty fallbacks, or swallowed errors.

## GitHub tracking

- Target issue set: #78
- Milestone: CLI Robustness & Vault Integrity
- Burndown: #77
- Closes on merge:
  - Closes #78
- References only:
  - Refs #65
  - Refs #69
  - Refs #52
  - Refs #62

## Implementation plan

Reproduce each deferred concern against current `main` before editing. For true findings, add boundary tests that fail before the fix and pass through the real CLI/module path. For false or obsolete findings, record the source-backed disposition in the PR body before marking the item complete.

## Claim map

- [ ] **#78 - `CardLookupError` is caught at the CLI boundary where generated commands can raise it**
  - Proof obligations claimed: unrecognized card ids from generated command paths print structured `Error: ...`, not a traceback.
  - Partial / not claimed: no broad rewrite of card lookup or #52 per-note resilience.
  - Evidence required: failing-before/green-after CLI boundary test or source-backed obsolete disposition.
  - Current evidence: deferred review item only.

- [ ] **#78 - root `list` does not depend on card schema registration when listing non-card memory types**
  - Proof obligations claimed: `agent-memory list --type decision --scope global` reaches the non-card listing path even if card schema registration fails, unless that command truly needs card schema state.
  - Partial / not claimed: no change to card-specific list behavior beyond the boundary requirement.
  - Evidence required: real CLI/module test with schema-load failure plus non-card listing path.
  - Current evidence: deferred review item only.

- [ ] **#78 - `CardConfigError` carries printable path context without assuming every traversable is a filesystem `Path`**
  - Proof obligations claimed: importlib resource traversables are handled as printable context; redundant `Path(str(path))` conversions are removed or justified.
  - Partial / not claimed: no unrelated loader redesign.
  - Evidence required: unit or integration coverage for non-Path traversable context, or source-backed obsolete disposition.
  - Current evidence: deferred review item only.

## Automated gates

Keep draft until every #78 item is reproduced-and-fixed or explicitly falsified against current `main`, and #77 is updated when this PR merges, closes, or is abandoned.
