## Intended result

An onboarding agent can ask the CLI what plans/cards exist, where they live, and whether any are stranded in unmanaged harness memory areas, instead of treating an empty managed `plans/` folder as proof that no plans exist.

## Scope

- Included: #46 doctor warning for unmigrated plan/card records, first-class list command with type/scope/unmigrated filters, and plan progress summary surface once todo metadata is available.
- Excluded: archive/retire semantics (#47), todo mutation (#62), and global queue implementation (#39).
- Preserved behavior: normal search/retrieval semantics remain unchanged except for explicit listing/discovery additions.

## GitHub tracking

- Target issue set: #46
- Milestone: unassigned discoverability feature.
- Closes on merge:
  - Closes #46
- References only:
  - Refs #41
  - Refs #39
  - Refs #47

## Implementation plan

Define the unmanaged harness scan roots and managed-scope classification first, with fixtures for stranded cards/plans. Add `doctor` warnings using those classifiers, then expose the same classification through a list command. Add progress summaries only to the extent the current plan/todo schema can support them without inventing hidden state.

## Claim map

- [ ] **#46 - unmigrated plans/cards are visible through doctor and list/progress surfaces**
  - Proof obligations claimed: unmanaged harness record is flagged with path and suggested destination; managed records are not false positives; list filters by type/scope/unmigrated; progress summary reflects real plan todo metadata where present.
  - Partial / not claimed: no archive state or retire workflow.
  - Evidence required: CLI fixture tests covering managed, unmanaged, and mixed states; clear output assertions; no hard-coded downstream repo names.
  - Current evidence: issue report only.

## Automated gates

Keep draft until the discovery surfaces are proven by subprocess or equivalent integration tests over fixture vaults.
