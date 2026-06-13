# Test Adequacy Report

Target reader: a future agent deciding whether the current tests prove the MVP
implementation obligations.

Reader task: determine whether the public CLI tests exclude plausible broken
implementations for IOB-001 through IOB-009.

## Adequacy Verdict

The MVP tests are proof-bearing for the accepted happy-path workflow. They exercise the
public `iwe2` CLI through real subprocesses, real temp vaults, real Git repositories,
real Markdown files, real IWE commands, real IWE `find`, and real `rg` search. The
suite does not use
mocks, source-text policing, skipped tests, or helper-only branch tests.

## Obligation Mapping

| Obligation                | Proof evidence                                                                                                                                                                                                                  | Plausible broken implementation excluded                                                                                                    |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| IOB-001 `vault init`      | TEST-001 runs `iwe2 vault init <vault>` and checks the IWE config plus root/global graph indexes. Commit `af6db4b` added the red graph-link proof; commit `e0a57a8` made it green.                                              | A CLI that creates directories but omits IWE graph inclusion links fails `iwe tree` verification.                                           |
| IOB-002 `project init`    | TEST-002 creates real Git repos with a remote, initializes a project, reads `.agent-memory.toml`, checks `AGENTS.md` bootstrap guidance, and checks the project IWE tree.                                                       | A hard-coded project ID, wrong vault pointer, missing AGENTS pointer, stale AGENTS memory section, or missing project graph children fails. |
| IOB-003 note creation     | TEST-003 creates project and global notes through the CLI and inspects their scoped paths and frontmatter.                                                                                                                      | A note writer that ignores scope, type directories, or frontmatter fails.                                                                   |
| IOB-004 scoped search     | TEST-003 writes distinct dynamic body strings and runs `search --scope project`, `global`, and `both`; `test_search_uses_iwe_graph_filters_for_title_matches` writes title/key-only fuzzy matches in project and global scopes. | A search command rooted at the wrong subtree, returning placeholder output, ignoring body scope, or ignoring graph/title scope fails.       |
| IOB-005 graph retrieval   | TEST-004 retrieves a note created in the same workflow through `iwe2 retrieve <key>`.                                                                                                                                           | Retrieval that bypasses IWE or returns fixed text fails on the dynamic note body.                                                           |
| IOB-006 promotion         | TEST-005 promotes a project trap to `global/traps`, then checks destination content, `scope: global`, `origin_project_id`, and the project pointer.                                                                             | Promotion that loses content, drops provenance, or deletes the project-local audit pointer fails.                                           |
| IOB-007 doctor            | TEST-006 runs `iwe2 doctor` after real initialization and checks the exact vault path, project ID, project root, and required tools.                                                                                            | A hollow status command or a command that does not inspect the declared project contract fails.                                             |
| IOB-008 module entrypoint | TEST-007 runs `python -m iwe2 vault init <vault>` through the project runner and checks the durable IWE vault graph state.                                                                                                      | A package without `iwe2.__main__` or a module entrypoint dispatching to a different surface fails.                                          |
| IOB-009 squash            | TEST-008 creates dynamic project and global notes, then runs `iwe2 squash <project-index-key> --depth 3`.                                                                                                                       | A missing squash command, wrong vault cwd, placeholder output, or graph key rooted outside the project subtree fails.                       |

## Residual Edges

| Edge                                                           | Status    | Reason                                                                                                                              |
| -------------------------------------------------------------- | --------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| Missing Git remote negative path                               | Deferred  | The MVP happy path requires a real remote; failure propagates from Git and does not need speculative error-path code.               |
| Missing `iwe`, `rg`, or `git` binaries                         | Deferred  | These are hard dependencies for this bespoke system. Setup failure should be loud, not tested through optional-dependency branches. |
| IWE link rewriting across arbitrary backlinks during promotion | Delegated | The wrapper invokes IWE for graph-aware movement; the MVP verifies the wrapper-owned destination and pointer policy.                |
| Probe and `zk` retrieval modes                                 | Deferred  | The design marks them optional complements. They are not part of the accepted MVP proof graph.                                      |

## Conclusion

The test plan excludes the main gaming paths for the MVP: no-op initialization,
directory-only vault creation, missing module execution, hard-coded project identity,
missing repo-local AGENTS bootstrap, unscoped body or title/key search, placeholder
retrieval, hollow squash, lossy promotion, and hollow doctor output.
