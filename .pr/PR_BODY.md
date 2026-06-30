## Milestone implementation PR — active claim map

This draft PR is the scoped execution surface for the Vault Synchronization and Integrity milestone.
It is derived from the project plan record:

`projects/github.com__dzackgarza__agent-memory/plans/execute-pr-37-vault-synchronization-and-integrity`

## Target issue set / subtree

- Parent issue: Refs #14
- Claimed child/scope issues: #5, #6, #23
- GitHub milestone: Vault Synchronization and Integrity

## Close / reference split

- Intended close candidates on merge: #5, #6, #23, but only for issues whose proof obligations land in this PR.
- Parent #14 is referenced by default.
  It may be closed only if final evidence proves the whole scoped subtree is complete.
- If normalization is not implemented in this PR, #5 remains referenced only and must stay open.

## Proof obligations claimed

### #6 — Vault auto-sync with conflict-aware push workflow

A user can initialize the vault, install auto-sync, inspect status, and remove auto-sync from the CLI. Normal vault changes are committed and pushed automatically.
Remote-advanced or conflict cases either integrate safely or create an auditable branch plus PR with diagnostics rather than leaving the vault silently conflicted.
`doctor` or a dedicated status surface reports the central vault path, remote, branch, worktree cleanliness, auto-sync install/enablement state, last sync success/failure, and `gh` availability when PR fallback is enabled.

### #23 — Link integrity and bulk rewrite after structural reorganizations

Broken `[[wikilink]]` references can be discovered with file/target evidence.
Bulk rewrite can repoint references to another key or an external URL. Mapping-based rewrite handles many-to-many changes.
Structural reorganization commands such as move/rename/delete/split/merge preserve inbound references or fail with an explicit backlink disposition path.

### #5 — OKF frontmatter preservation during normalization

If this PR introduces or changes normalization, it must preserve OKF metadata fields and reconcile duplicate/extra frontmatter into the canonical header or fail loudly naming the file and fields.
If no normalization behavior lands here, #5 is not claimed and remains open.

## Local execution plan

1. Reconcile this branch with current `main` using forward commits, keeping the draft PR branch and public audit trail intact.
2. Establish behavior-level proof for each claimed issue before implementation changes.
3. Implement #6 and #23 as the core milestone workstreams.
4. Decide #5 at the normalization boundary: implement and prove it only if normalization is actually touched; otherwise keep it as a referenced non-claim.
5. Run the repo's declared validation gate and the PR review loop before marking ready.

## Evidence required before ready-for-review

- Local `just test` from the repository root.
- PR checks green.
- Issue-by-issue PR evidence note naming the command/output or test proving each closure candidate.
- Review-thread scan showing no unresolved actionable threads.

## Exclusions / split conditions

- Excludes global agent work queue (#39), card schema system (#26/#35), and codebase health/slop remediation (#13/#36).
- No silent fallbacks, mock sync, optional critical dependencies, or dirty-vault success paths.
- If gitwatch conflict handling is not demonstrably safe and recoverable, implementation must use a first-class agent-memory sync workflow instead of depending on gitwatch.
