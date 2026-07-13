## Intended result

An onboarding agent can see progress already recorded in a managed plan's todo tree without inferring progress for plans whose schema does not carry one.

## Scope

- Included: the remaining #46 plan-progress surface over existing `type: plan` todo metadata.
- Already delivered on `main`: doctor warning for unmigrated plan/card records and `list --type --scope --unmigrated` (#59).
- Excluded: archive/retire semantics (#47), todo mutation (#62), card-hierarchy progress inference, and global queue implementation (#39).
- Preserved behavior: structured `PLAN-*` cards remain separate from plan-memory todo trees.

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

Expose `agent-memory plan progress --scope <project|global|both>` for plan-memory records with a `todos` tree. Count completion using the active card schema's complete workflow role, aggregate nested todo statuses, and report plan records without a `todos` list separately.

## Claim map

- [x] **#46 - plan progress reflects existing todo metadata without inventing card progress**
  - Proof obligations claimed: nested todo statuses aggregate per plan and across project, global, and combined scopes; the active schema supplies only complete-state membership; plan records without a todo tree are explicit unsupported records; malformed managed notes become findings without hiding valid progress.
  - Partial / not claimed: no archive state, retire workflow, or card-hierarchy progress model.
  - Evidence: `tests/test_cli_workflows.py::test_plan_progress_counts_legacy_todo_statuses_across_scopes` passed locally.

## Automated gates

Keep draft until the PR checks verify the focused integration proof.
