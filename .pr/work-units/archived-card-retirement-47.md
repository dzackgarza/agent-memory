## Intended result

Users can retire/archive cards without destroying them, while default active views stay uncluttered and at least one normal discovery path makes archived-card presence visible to agents.

## Scope

- Included: research/design decision for archive representation and view semantics; implementation of the chosen archive/retire operation; documented/testable behavior for search, inspect/list/tree/recent, exact-key retrieval, and plan DAG where applicable.
- Excluded: unmigrated harness discovery (#46), global queue #39, and unrelated schema-engine changes.
- Preserved behavior: archived records keep project/type/parentage/location context and are never silently hidden from all discovery paths.

## GitHub tracking

- Target issue set: #47
- Milestone: unassigned archive/discoverability feature.
- Closes on merge:
  - Closes #47
- References only:
  - Refs #41
  - Refs #46

## Implementation plan

Start by recording the archive-state decision in the PR body/issue before implementation: metadata status, moved subtree, or a combined model. Then add the retire/archive mutation and update each discovery surface named in #47 with tests for active-default, archived-explicit, exact-key retrieval, and search behavior.

## Claim map

- [ ] **#47 - archived cards have explicit retirement and visibility semantics**
  - Proof obligations claimed: archive operation; active default behavior; explicit archived discovery; exact-key behavior; search/inspect/tree/recent/DAG semantics documented and tested where applicable.
  - Partial / not claimed: no #46 unmigrated scan and no unrelated card-schema expansion.
  - Evidence required: design decision captured before code; integration tests for active and archived views; documentation updated only after behavior exists.
  - Current evidence: issue research questions only.

## Automated gates

Keep draft until the design questions are resolved into a single behavior contract and the implementation/tests prove that contract.
