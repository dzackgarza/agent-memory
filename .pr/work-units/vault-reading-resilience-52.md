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

Audit what #65 already handles as partial boundary cleanup, then either depend on those commits or absorb equivalent fixes.
Add the missing real-vault CLI fixture from #52: mixed well-formed notes plus the nested `todo_tree`/non-conforming note.
Change the reader path so failures are attached to the offending note and the rest of the result set survives, while still failing loudly for schema/config failures that cannot produce a valid reader.

## Claim map

- [x] **#52 - non-conforming notes produce per-note findings without vault-wide aborts**
  - Proof obligations claimed: mixed fixture vault; well-formed records returned; offending note named with structured finding; no traceback for expected boundary failures.
  - Partial / not claimed: no todo mutation command and no global queue surface.
  - Red proof: `735c19a test: reproduce inspect export missing note findings`.
  - Green fix: `f40a244 fix: report inspect export note findings`.
  - Targeted evidence: `direnv exec . uv run --python 3.14 pytest` over the #52 search, overview, export, malformed note, unknown card type, and malformed `cards.yaml` cases passed `10 passed in 34.92s`.

## Automated gates

`just test` passed on 2026-07-04 with 194 tests in 374.67s.
`just test-ci` exited 0 on 2026-07-04 with both pytest passes green, diff-cover at 85%, deptry/import-linter/semgrep/vibecheck green, and push/CI quality checks completed.
The ai-slop-detector step still printed a critical-threshold notice for `operations.py`; that outstanding detector debt is tracked by #80.
Keep draft until PR body publication, automated review, and #77 merge/close synchronization are complete.
