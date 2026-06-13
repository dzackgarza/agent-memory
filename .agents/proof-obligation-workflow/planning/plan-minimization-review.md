# Plan Minimization Review

The selected path remains the transcript's recommended architecture: compose IWE and
normal filesystem search behind a thin CLI wrapper.

Surface-reducing decisions:

- Do not build a database, embedding store, reranker, parser, backlink engine, or wrapper MCP server. Wrapper MCP support is dropped scope, not deferred runtime.
- Do not make `zk`, Probe, or vector search part of the MVP runtime. The transcript marks these as optional complements; IWE `find` proves scoped title/key graph search and `rg` proves scoped body search.
- Do not produce action-sensitive frontmatter or lifecycle state frontmatter. Notes carry the stable OKF-compatible metadata required by the wrapper and no operational authority, expiry, confirmation, or status fields.
- Do not support multiple vault discovery locations. The vault is explicit at initialization and then recorded in `.agent-memory.toml`.
- Do not support project IDs from several ambient sources. Git remote identity is the normal path; an explicit project ID can be added only if a real no-remote workflow becomes required.
- Do not add local generic QC tooling. The project justfile delegates to `~/ai-review-ci`.

Rejected surface-reducing alternative:

- Direct shell alias around raw `iwe` commands was rejected because the transcript requires project detection, scope routing, promotion provenance, and repo-local bootstrap config that IWE does not own directly.

Adopted local ownership:

- The wrapper owns the memory-specific convention and state transitions.
- IWE owns graph mechanics.
- IWE owns fuzzy title/key graph search.
- `rg` owns lexical body search.
