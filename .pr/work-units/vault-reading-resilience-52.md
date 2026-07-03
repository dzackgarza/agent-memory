## Intended result

Vault reads are schema-driven and resilient: one malformed or non-conforming note becomes a structured per-note finding while well-formed notes still return, and schema/card boundary errors reach the CLI as clean user-facing errors rather than tracebacks.

## Scope

- Included: the full #52 acceptance surface, including per-note findings for `search`/`inspect`, nested todo-tree reproducer behavior, unknown card type diagnostics, and malformed `cards.yaml` diagnostics.
- Excluded: the todo mutation feature #62, global queue #39, and arbitrary new schema-engine scope beyond what #52 requires.
- Preserved behavior: malformed data is surfaced as a finding/error, not silently swallowed or laundered into empty/default metadata.

## GitHub tracking

- Target issue set: #52
- Milestone: Card Schema System
- Closes on merge:
  - Closes #52
- References only:
  - Refs #65
  - Refs #41
  - Refs #26

## Implementation plan

Audit what #65 already handles as partial boundary cleanup, then either depend on those commits or absorb equivalent fixes. Add the missing real-vault CLI fixture from #52: mixed well-formed notes plus the nested `todo_tree`/non-conforming note. Change the reader path so failures are attached to the offending note and the rest of the result set survives, while still failing loudly for schema/config failures that cannot produce a valid reader.

## Claim map

- [ ] **#52 - non-conforming notes produce per-note findings without vault-wide aborts**
  - Proof obligations claimed: mixed fixture vault; well-formed records returned; offending note named with structured finding; no traceback for expected boundary failures.
  - Partial / not claimed: no todo mutation command and no global queue surface.
  - Evidence required: committed red reproducer, green real-CLI tests for `search`/`inspect`, and explicit assertions rejecting silent swallow/default behavior.
  - Current evidence: #65 partially covers malformed schema and unknown-id boundaries only; #52 remains open.

## Automated gates

Keep draft until the per-note resilience fixture and full boundary-error cases are evidenced, not merely described as handled by partial #65 work.
