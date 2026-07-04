## Intended result

Agents can update the `todos:` frontmatter tree on vault plan records through a first-class CLI surface, without hand-editing vault files and without confusing card-backed `plan update` with legacy/vault plan-record keys.

## Scope

- Included: a command such as `agent-memory todo set <plan-key> <todo-id> --status ... --note ...`; validation of todo ids/status/content fields; preservation of all unrelated frontmatter/body content; clean diagnostics for missing plan or todo id.
- Excluded: the partial structured-error work in #65, full schema-driven reading #52, and broader card-schema redesign.
- Preserved behavior: existing body-only `agent-memory update` behavior remains body-only unless explicitly extended with tests; card-backed `plan update` continues to operate on card ids.

## GitHub tracking

- Target issue set: #62
- Milestone: CLI Robustness & Vault Integrity
- Closes on merge:
  - Closes #62
- References only:
  - Refs #65
  - Refs #52
  - Refs #41

## Implementation plan

Begin with the issue reproducer as a failing test: a real plan record with `todos:` cannot transition a todo via the CLI today.
Design the narrow mutation command, validate it against the todo schema/allowed statuses, update only the targeted todo node, and prove unrelated frontmatter fields and body prose survive byte/semantic comparison.

## Claim map

- [x] **#62 - plan-record todo status/content is mutable through CLI**
  - Proof obligations claimed: status transition; note/content update if supported; missing todo/id failures are clean; unrelated frontmatter/body preserved; no raw `AssertionError` for vault plan keys.
  - Partial / not claimed: no claim to finish #52's per-note reading model or #65's unrelated QC fixes.
  - Evidence required: committed red reproducer, green CLI subprocess tests, preservation assertions, and normal test gate evidence.
  - Current evidence: red reproducer committed in `fd371e8`; implementation committed in `68402a4`; test typing correction committed in `494301c`. Targeted issue tests passed with `direnv exec . uv run --python 3.14 pytest tests/test_cli_workflows.py::test_todo_set_mutates_nested_plan_todo_and_preserves_record tests/test_cli_workflows.py::test_todo_set_reports_clean_errors_for_invalid_inputs` (2 passed in 14.81s). Commit-tier `just test` passed on 2026-07-04 with 193 tests in 383.45s.

## Automated gates

Push-tier `just test-ci`, PR body publication, automated review, and #77 merge/close synchronization remain before merge.
