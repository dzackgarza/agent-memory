## Intended result

Deferred review feedback from merged PR #65 is re-evaluated against current `main`, and every true finding is fixed through the real CLI/module boundary with tests.
The result preserves structured CLI errors and keeps non-card commands usable when card schema loading is irrelevant.

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

Reproduce each deferred concern against current `main` before editing.
For true findings, add boundary tests that fail before the fix and pass through the real CLI/module path.
For false or obsolete findings, record the source-backed disposition in the PR body before marking the item complete.

## Claim map

- [x] **#78 - `CardLookupError` is caught at the CLI boundary where generated commands can raise it**
  - Proof obligations claimed: unrecognized card ids from generated command paths print structured `Error: ...`, not a traceback.
  - Partial / not claimed: no broad rewrite of card lookup or #52 per-note resilience.
  - Evidence: red test committed in `f7ba149`; green fix committed in `5629d7f`; targeted test `test_generated_card_update_unknown_id_prefix_is_structured_cli_error` passes.

- [x] **#78 - root `list` does not depend on card schema registration when listing non-card memory types**
  - Proof obligations claimed: `agent-memory list --type decision --scope global` reaches the non-card listing path even if card schema registration fails, unless that command truly needs card schema state.
  - Partial / not claimed: no change to card-specific list behavior beyond the boundary requirement.
  - Evidence: red test committed in `f7ba149`; green fix committed in `5629d7f`; targeted test `test_root_list_global_memory_type_does_not_require_project_card_schema` passes.

- [x] **#78 - `CardConfigError` carries printable path context without assuming every traversable is a filesystem `Path`**
  - Proof obligations claimed: importlib resource traversables are handled as printable context; redundant `Path(str(path))` conversions are removed or justified.
  - Partial / not claimed: no unrelated loader redesign.
  - Evidence: red test committed in `f7ba149`; green fix committed in `5629d7f`; targeted test `test_load_card_system_config_reads_packaged_defaults_from_zip_resource` passes.

## Automated gates

`just test` passed on 2026-07-04 with 191 tests in 354.74s.
`just test-ci` exited 0 on 2026-07-04 with both pytest passes green, diff-cover at 86%, deptry/import-linter/semgrep/vibecheck green, and push/CI quality checks completed.
The ai-slop-detector step still printed a critical-threshold notice for `operations.py`; that pre-existing/out-of-scope residue is tracked by #80.
Keep draft until PR body publication, automated review, and #77 merge/close synchronization are complete.
