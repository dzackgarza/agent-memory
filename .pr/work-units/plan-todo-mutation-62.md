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

Begin with the issue reproducer as a failing test: a real plan record with `todos:` cannot transition a todo via the CLI today. Design the narrow mutation command, validate it against the todo schema/allowed statuses, update only the targeted todo node, and prove unrelated frontmatter fields and body prose survive byte/semantic comparison.

## Claim map

- [ ] **#62 - plan-record todo status/content is mutable through CLI**
  - Proof obligations claimed: status transition; note/content update if supported; missing todo/id failures are clean; unrelated frontmatter/body preserved; no raw `AssertionError` for vault plan keys.
  - Partial / not claimed: no claim to finish #52's per-note reading model or #65's unrelated QC fixes.
  - Evidence required: committed red reproducer, green CLI subprocess tests, preservation assertions, and normal test gate evidence.
  - Current evidence: issue reproducer plus #65 partial error-boundary work only; feature implementation still required.

## Automated gates

Keep draft until the todo mutation path is proven through the real CLI and the PR body distinguishes any inherited gate blockers from this branch's own regressions.
