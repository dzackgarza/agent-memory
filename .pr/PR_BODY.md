# Card Schema System: generated card commands and config-driven validation

## Claim Map

Milestone: **Card Schema System**

This updates existing PR #35 instead of opening another narrow PR. The branch now claims the coherent card-schema slice that issue #51 describes: cards are the single source of truth, type-specific commands are generated from the configured card definitions (`feature add`, `plan add`, or any vault-defined peer command), and `plan` remains a card type with plan-specific extras layered on top.

Closes:

- #51 — reconciles the plan/card command schism by generating card-type command groups from `CardSystemConfig`, including `plan add/update/delete/show` from the `plan` card type.
- #53 — removes hardcoded validation roles/order/tag ancestry from the engine path and derives those rules from the card config.

Refs, not closes:

- #26 — this lands the urgent near-term schema-driven command/validation slice, but does not claim the full plugin/card-system umbrella.
- #62 — the legacy `add --type plan` memory-note path is now rejected in favor of card-backed plan records, but `todo set` is not claimed because there is no `todos` object-list CLI contract yet.
- #46 — plan lifecycle/search behavior is exercised through card-backed plans, but progress summary semantics are not claimed.

Not claimed:

- Recursive/object-list CLI construction for todo forests.
- A `todo set` command.
- `--group` or arbitrary agent-supplied routing segments.
- The rescued vault-sync/backlink branch.
- Splitting `operations.py` as a separate architecture cleanup.

## Implementation Surface

- `src/agent_memory/cli.py`
  - Adds a generic `card` command group.
  - Registers top-level command groups for configured card types at CLI startup, so each card type owns one add/update/delete/show path through card creation.
  - Keeps plan extras (`validate`, `dag`, `migrate`) as code-driven conveniences on the generated `plan` command group.

- `src/agent_memory/operations.py`
  - Generalizes plan-card operations to card operations.
  - Adds `show_card`.
  - Rejects writable `MemoryType.PLAN` note creation with a hard error that points users to `agent-memory plan add`.
  - Updates schema inspection and AGENTS pointer output so plan work is card-backed.

- `src/agent_memory/cards/config.py`
  - Adds declarative `tagged_ancestor` and `ordered_children`.
  - Validates workflow roles and ordered-child config at schema load time.

- `src/agent_memory/cards/validation.py`
  - Derives workflow status, ordered children, tagged ancestry, and project root behavior from loaded card config instead of hardcoded type-name constants.

- `src/agent_memory/defaults/cards.yaml`
  - Carries the shipped feature/plan/phase/task hierarchy rules as data.

- `tests/test_cli_workflows.py`
  - Adds end-to-end coverage for a vault-only card type.
  - Proves generated plan help follows project card schema.
  - Proves legacy writable plan memory notes are rejected.
  - Exercises card-backed plan lifecycle and search behavior.

## Proof

Passing:

- `just test`
  - `187 passed`
- `just test-ci`
  - Python preflight passed.
  - Formatting passed.
  - Semgrep autofix produced no findings.
  - Mypy passed.
  - Commit-tier pytest passed before the latest review-remediation commits: `186 passed`.
  - Coverage pytest passed before the latest review-remediation commits: `186 passed`.
  - Diff coverage passed at 88%.

Blocked / dispositioned:

- `just test-ci` still reports a Ruff E501 line-length finding in `tests/test_card_validation.py:43`.
  - Independent Route B QC disposition cleared it as a weak formatting finding, not a semantic branch blocker.
- `just test-ci` still exits nonzero at the deptry import dependency linting step:
  - `src/agent_memory/operations.py:18:8 DEP001 'frontmatter' imported but missing from the dependency definitions`
  - `src/agent_memory/operations.py:21:1 DEP001 'markdown_it' imported but missing from the dependency definitions`
- Independent Route B QC disposition cleared both deptry findings because the dependencies are already declared:
  - `python-frontmatter>=1.3.0`
  - `markdown-it-py>=3`
- The separate Route C remediation removed the repo-local `[tool.deptry.package_module_name_map]` section because repo-local deptry config violates `POLICY.GLOBAL_QC_AUTHORITY`.
- Remaining action is central QC alignment for deptry package/module mapping; this branch does not carry local QC config.
