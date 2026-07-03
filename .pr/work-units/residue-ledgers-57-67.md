## Intended result

The remaining ledger issues are either resolved with concrete disposition evidence or redirected into live implementation issues, so review residue and urgent-frontmatter debt are not left as ambiguous permanent backlog.

## Scope

- Included: #57 urgent-frontmatter debt ledger disposition and #67 non-blocking PR #35 review residue disposition.
- Excluded: implementing the substantive child work already owned elsewhere unless this PR deliberately narrows to a concrete residual fix; silent closure of any residue without evidence.
- Preserved behavior: ledger cleanup must not pretend implementation work is complete merely because it was cataloged.

## GitHub tracking

- Target issue set: #57, #67
- Milestone: unassigned ledger cleanup.
- Closes on merge:
  - Closes #57
  - Closes #67
- References only:
  - Refs #35
  - Refs #41

## Implementation plan

Audit each ledger bullet against current GitHub state and source behavior. For each bullet, either cite the merged PR/issue that now owns or completed it, create/link the missing live issue before closing the ledger, or implement a small residual fix if it is genuinely self-contained. Keep a top-level disposition note in the PR so closure is reviewable.

## Claim map

- [ ] **#57 - urgent frontmatter preservation residue is fully dispositioned**
  - Proof obligations claimed: every listed not-claimed item is either completed, still tracked by a specific open issue/PR, or explicitly obsolete with evidence.
  - Partial / not claimed: no administrative closure that hides unowned implementation work.
  - Evidence required: bullet-by-bullet disposition with links.
  - Current evidence: ledger text only.

- [ ] **#67 - PR #35 review residue has no remaining actionable work**
  - Proof obligations claimed: Kilo/Copilot residue is checked against final PR #35 state and current policy; any still-actionable item is moved to a live issue before #67 closes.
  - Partial / not claimed: no reopening of #35 or broad card-schema work.
  - Evidence required: disposition note with links to review/check state and successor issues if any.
  - Current evidence: issue text only.

## Automated gates

Keep draft until the disposition ledger is evidence-linked and does not launder unfinished implementation work as mere cleanup.
