## Intended result

Urgent-frontmatter debt is either resolved with concrete disposition evidence or redirected into live implementation issues, so it is not left as ambiguous permanent backlog.

## Scope

- Included: #57 urgent-frontmatter debt ledger disposition.
- Excluded: #67 non-blocking PR #35 review residue disposition; it remains an open issue and requires its own evidence pass.
- Excluded: implementing the substantive child work already owned elsewhere unless this PR deliberately narrows to a concrete residual fix; silent closure of any residue without evidence.
- Preserved behavior: ledger cleanup must not pretend implementation work is complete merely because it was cataloged.

## GitHub tracking

- Target issue set: #57
- Milestone: unassigned ledger cleanup.
- Closes on merge:
  - Closes #57
- References only:
  - Refs #67
  - Refs #41

## Implementation plan

Audit each ledger bullet against current GitHub state and source behavior. For each bullet, either cite the merged PR/issue that now owns or completed it, create/link the missing live issue before closing the ledger, or implement a small residual fix if it is genuinely self-contained. Keep a top-level disposition note in the PR so closure is reviewable.

## #57 disposition ledger

Audited 2026-07-13 against the current issue and PR surfaces.

### Urgent frontmatter/update path

- **Structured frontmatter reads and same-path index recovery (#48, #54):** completed by merged [PR #58](https://github.com/dzackgarza/agent-memory/pull/58). Its targeted CLI proof covers structured YAML preservation and missing-index repair.
- **Update/transition preservation (#55, #56):** [PR #58](https://github.com/dzackgarza/agent-memory/pull/58) merged the focused fix. The later draft [PR #84](https://github.com/dzackgarza/agent-memory/pull/84) remains a live, separately reviewable continuation; it must not be represented as merged evidence.
- **Normalization boundary (#5):** completed by merged [PR #42](https://github.com/dzackgarza/agent-memory/pull/42), which supplies conflict-detecting OKF reconciliation before a future normalization writer runs. It deliberately does not add a normalize command.
- **Skipped broad review/QC:** now owned by [#93](https://github.com/dzackgarza/agent-memory/issues/93). It must review the merged urgent path and either resolve or explicitly route every finding.

### Deferred debt from the urgent slice

- **Card-schema work (#26, #35, #52, #53):** the schema milestones are closed. [PR #35](https://github.com/dzackgarza/agent-memory/pull/35) delivered generated card commands and validation; [PR #71](https://github.com/dzackgarza/agent-memory/pull/71) completed resilient schema-driven reading.
- **Parked `split-card` continuation at `0438f93`:** preserved and now owned by [#94](https://github.com/dzackgarza/agent-memory/issues/94). The branch and its WIP stash are not evidence of completion and must not be discarded by this ledger.
- **Existing QC debt (#49):** completed by merged [PR #69](https://github.com/dzackgarza/agent-memory/pull/69).
- **Codebase-health/slop debt (#1, #2, #13):** [PR #92](https://github.com/dzackgarza/agent-memory/pull/92) merged the completed remediation it claims; the still-open [PR #75](https://github.com/dzackgarza/agent-memory/pull/75) remains the visible owner of its unmerged #1/#2/#13 claim map.
- **Card-dependent enhancements (#50, #51):** completed by merged [PR #60](https://github.com/dzackgarza/agent-memory/pull/60) and [PR #35](https://github.com/dzackgarza/agent-memory/pull/35), respectively.

This ledger closes no implementation issue by itself. Its completion condition is that each omitted obligation has the linked merged evidence or a live successor above.

## Claim map

- [x] **#57 - urgent frontmatter preservation residue is fully dispositioned**
  - Proof obligations claimed: every listed not-claimed item is either completed, still tracked by a specific open issue/PR, or explicitly obsolete with evidence.
  - Partial / not claimed: no administrative closure that hides unowned implementation work.
  - Evidence: the disposition ledger above links every original #57 bullet to its merged implementation or open successor.

## Automated gates

Ready for review: the disposition ledger is evidence-linked and does not launder unfinished implementation work as mere cleanup.
