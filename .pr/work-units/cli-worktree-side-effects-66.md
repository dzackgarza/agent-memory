## Intended result

Read-only CLI commands run from a bound repository do not mutate that repository's working tree. In particular, `agent-memory search` must not regenerate root `AGENTS.md`, must not create a literal `~` directory, and must leave a snapshot of the caller repo unchanged except for explicitly documented init/bind commands.

## Scope

- Included: reproduce #66 with a real bound-repo subprocess fixture; identify the write path that creates root `AGENTS.md` during read-only commands; fix tilde/path expansion before any mkdir/write; prove read-only command paths leave the caller worktree unchanged.
- Excluded: broad config-path redesign, archived-card discoverability, todo mutation, and unrelated hygiene-policy changes in downstream repos.
- Preserved behavior: explicit init/bind/setup commands may still write their documented files; read-only commands still resolve config and fail loudly on invalid config.

## GitHub tracking

- Target issue set: #66
- Milestone: unassigned bug; likely CLI/vault integrity surface.
- Closes on merge:
  - Closes #66
- References only:
  - Refs #41

## Implementation plan

Start with the failing reproducer from #66: bind a temporary repo to a vault, snapshot the repo tree, invoke a read-only CLI command through the real entry point, and assert no new root `AGENTS.md` or literal `~` tree appears. Then repair the responsible write/config path narrowly and add nearby negative coverage for explicit write commands so the fix does not erase intended setup behavior.

## Claim map

- [ ] **#66 - read-only CLI commands have no caller-worktree side effects**
  - Proof obligations claimed: real subprocess boundary; unchanged worktree snapshot; no literal tilde path; root `AGENTS.md` creation gated behind explicit setup/write command.
  - Partial / not claimed: no general rewrite of vault resolution or downstream repo hygiene policy.
  - Evidence required: committed red reproducer, green reproducer after fix, relevant `just test`/targeted pytest output, and no broad fallback/silent path creation.
  - Current evidence: issue report only; implementation evidence still required.

## Automated gates

Keep draft until the targeted reproducer and the repo's normal test gate are green or any remaining gate blocker is explicitly linked to a separate issue/PR.
