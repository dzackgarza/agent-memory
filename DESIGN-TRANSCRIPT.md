Is there something like iwe that can be repurposed for an LLM agent memory system? One would want a centralized store instead of per-project stores, where one can initialize it in a project to set up a subfolder in the centralized "vault" and easily scope their queries to either the project or global memories. This allows agents in a project to record repo-specific decisions, traps, workflows, etc, as well as fluidly move more general items "up" into memories, advice, etc that span all projects on a machine. Thus one can version control the entire vault and have a transferable set of memories. One COULD build this from the ground up, but one should also consider either repurposing a tool with nearby functionalities, forking such a tool, composing several tools into a thin wrapper CLI, etc. The goal is to minimize owned code surface while getting the actual needed functionality. Note that most tools advertised as "LLM memory tools" are the wrong paradigm here: they hide memories in databases so that e.g. they can use reranking and embedding techniques, when there are CLI tools that can do this more easily on-the-fly (e.g. semgrep, probe, etc), meaning that one really wants a simple organized managed markdown hierarchy, where the tool has some basic searching/aggregation/filtering capabilities but ultimately allows manual management with standard file system tools and searching with standard CLI tools (e.g. clever greps). One mostly wants a convenience layer on top of what is the agent equivalent of a headless Obsidian vault. Survey, analysis, recommendations?

---

Yes. The closest fit is **not** a conventional “LLM memory” product. It is a **single central Markdown knowledge graph**, with a thin project-aware CLI wrapper around existing tools. The best base is **IWE**, optionally paired with **zk** or **Probe/ripgrep** for body search.

The strongest recommendation is:

```text
IWE as the authoritative vault/graph/refactoring layer
+ rg or Probe for full-content search
+ optional zk for indexed Markdown full-text search and note creation
+ a small `mem` wrapper for project detection, scoping, templates, and promotion
+ AGENTS.md as the repo-local bootstrap pointer
```

This keeps the owned code surface small: you do not write a database, embedding stack, parser, backlink engine, or MCP layer. You only own the project-scoping and memory-specific workflow.

## 1. Why IWE is the closest core

IWE is already very close to the desired primitive. It treats a directory of Markdown files as a knowledge graph, keeps the files as ordinary Markdown, avoids cloud/database lock-in, supports Git versioning, and exposes CLI plus MCP operations. Its model is graph-first rather than folder-tree-first, with inclusion links and cross-references, which is useful for “project memory contains these notes; global memory contains these reusable notes.” ([GitHub][1])

The relevant IWE operations are also near your desired workflow: `find`, `retrieve`, `tree`, `squash`, `new`, `extract`, `inline`, `rename`, and `delete`. The `rename`/refactor path is especially useful for promoting or reorganizing memories while preserving links. ([GitHub][1]) IWE also has an MCP server exposing search, retrieve, tree, stats, create, update, delete, rename, extract, inline, normalize, and attach operations, so an agent can interact with the vault without you having to write a protocol server. ([IWE][2])

The main caution is that IWE’s `find` is not a full-body search engine: its docs say fuzzy queries match title and key, not note bodies. ([IWE][3]) Therefore, IWE should be the **graph and lifecycle layer**, not the only search layer. For content search, compose it with `rg`, `probe`, or `zk`.

Use **one central IWE workspace**, not one IWE workspace per repo:

```text
~/.agent-vault/
  .iwe/
  README.md
  AGENTS.md
  global/
    index.md
    advice/
    traps/
    workflows/
    tools/
    style/
  projects/
    github.com__owner__repo/
      index.md
      decisions/
      traps/
      workflows/
      sessions/
      facts/
  inbox/
    unsorted/
    project/
    global/
  templates/
  _meta/
    projects.toml
```

Each project directory is just a subtree in the central vault. That gives you global linking, global refactors, a single Git history, and a transferable machine-level memory repository.

## 2. Where zk fits

`zk` is the other strong candidate. It is a CLI tool for a plain-text Zettelkasten/personal wiki, supports Markdown links, wikilinks, tags, YAML frontmatter, templates, LSP integration, advanced filtering, and automation. ([GitHub][4]) Its filtering model is very useful here: it supports path filtering, full-text search, exact/regex search, tags, link queries, recursive link exploration, sorting, limits, and interactive `fzf` selection. ([ZK Org][5])

The tradeoff is that `zk` maintains a SQLite search database inside the notebook metadata directory. The source of truth is still the Markdown notebook, but there is an index. ([ZK Org][6]) That may be acceptable because the database is rebuildable and not the canonical memory store. It is different from vector-memory products where the operational memory is effectively hidden in a service database.

The best use of `zk` is as an optional secondary layer:

```bash
ZK_NOTEBOOK_DIR="$HOME/.agent-vault" zk list "projects/github.com__owner__repo" -m "auth migration"
ZK_NOTEBOOK_DIR="$HOME/.agent-vault" zk list global -m '"retry budget" AND trap'
```

I would not make `zk` the central engine unless you prefer its note creation and full-text filtering over IWE’s graph/refactoring model. It is excellent for indexed Markdown search; IWE is better for graph structure, note movement, inclusion trees, and MCP-facing agent workflows.

## 3. Where Basic Memory fits

Basic Memory is close in spirit but less aligned with the exact architecture. It uses Markdown files as readable/auditable context and provides MCP integration, but it also maintains a SQLite index for search. ([Basic Memory][7]) More importantly, its “projects” are separate knowledge bases: notes in one project do not appear in another, and relations cannot link across projects. ([Basic Memory][8]) That is almost the opposite of your desired global/project fluidity.

There is one viable Basic Memory variant: create **one Basic Memory project** for the whole machine and use folders for project/global separation. Basic Memory explicitly allows folders inside a project and says to keep notes in the same project when they should link/search together. ([Basic Memory][8]) But then you are using Basic Memory against its advertised project-isolation model, and you inherit its search/index behavior. It is reasonable if you want an already polished MCP memory server, but less clean if the desired primitive is “managed Markdown hierarchy plus standard CLI tools.”

## 4. Tools I would not choose as the core

**Memsearch** is too RAG-shaped for this use case. It does keep Markdown as the source of truth, but its core pipeline chunks Markdown, embeds it, and stores/searches it through Milvus with hybrid retrieval. ([GitHub][9]) That is a legitimate architecture, but it is the paradigm you explicitly want to avoid.

**qmd** is interesting as a local Markdown search component for agents, with CLI/SDK/MCP interfaces, but it advertises BM25 plus vector search plus LLM reranking. ([KnightLi的博客][10]) That makes it a possible search plugin, not the canonical memory substrate.

**OpenClaw memory** is useful as a pattern, not as the vault engine. Its memory design uses plain Markdown files such as `MEMORY.md`, daily memory files, and optional `DREAMS.md`, with a compact curated layer and a working daily layer. ([OpenClaw][11]) That tiering idea is good: keep an append-only inbox or daily/session layer, then promote durable memories into curated project/global notes.

**Memaris** is also a sidecar, not a core vault. It analyzes Claude Code conversation history and generates personalized `CLAUDE.md`-style guidance from past coding behavior. ([GitHub][12]) It could be useful as an importer or summarizer that writes into your vault’s inbox, but it should not own the vault.

**Obsidian, Foam, and Dendron** are better treated as optional human-facing UIs or inspiration. Foam, for example, is a VS Code/GitHub-based personal knowledge management system with wikilinks, graph visualization, templates, and link maintenance. ([GitHub][13]) That is useful for manual editing, but it is editor-first rather than agent-memory-first.

## 5. Recommended design

Use a **central vault** and make each repo contain only a pointer.

Inside a repo:

```text
.agent-memory.toml
AGENTS.md
```

Example `.agent-memory.toml`:

```toml
vault = "~/.agent-vault"
project_id = "github.com__owner__repo"
project_root_strategy = "git-root"
global_scopes = ["global/advice", "global/traps", "global/workflows", "global/tools"]
```

Example repo-local `AGENTS.md` fragment:

````markdown
# Agent memory

This repository uses the central agent memory vault at `~/.agent-vault`.

Project memory key: `projects/github.com__owner__repo/index`.

Before changing architecture, search both project and global memory:

```bash
mem search --scope both "<task or subsystem>"
```
````

Record durable repo-specific lessons with:

```bash
mem note --project --type decision
mem note --project --type trap
mem note --project --type workflow
```

Promote reusable lessons with:

```bash
mem promote <note-key> --to global/advice
```

````

`AGENTS.md` is a good bootstrap convention because it is an open Markdown format for agent instructions, intended to sit in repositories, with nested files allowing closer instructions to take precedence. :contentReference[oaicite:15]{index=15}

## 6. The thin wrapper CLI

The wrapper should be boring. It should not implement memory intelligence. It should do path resolution, templates, scoping, and command composition.

Suggested commands:

```bash
mem vault init
mem project init
mem note --project --type decision
mem note --project --type trap
mem note --global --type advice
mem search --scope project "query"
mem search --scope global "query"
mem search --scope both "query"
mem retrieve <key>
mem promote <key> --to global/advice
mem squash --scope project
mem doctor
````

Internally:

```text
mem vault init
  -> mkdir ~/.agent-vault
  -> git init
  -> iwe init
  -> optionally zk init
  -> create global/, projects/, inbox/, templates/

mem project init
  -> detect git root and remote
  -> derive stable project_id
  -> create projects/<project_id>/index.md
  -> write .agent-memory.toml
  -> optionally write or update AGENTS.md pointer

mem note
  -> create Markdown from template
  -> place under projects/<project_id>/... or global/...
  -> add frontmatter
  -> optionally call iwe normalize

mem search
  -> for graph/title/key: iwe find
  -> for body search: rg/probe/zk
  -> merge/deduplicate results

mem promote
  -> move note from project subtree to global subtree
  -> update frontmatter
  -> use iwe rename or link-aware refactor when possible
  -> commit or stage changes optionally
```

A useful frontmatter schema:

```yaml
---
type: decision # decision | trap | workflow | fact | advice | convention
scope: project # project | global
project_id: github.com__owner__repo
status: active # active | superseded | tentative
created: 2026-06-12
updated: 2026-06-12
source: human # human | agent | imported
confidence: high # low | medium | high
promotable: false
supersedes: []
related: []
---
```

For action-sensitive memories, add fields such as `authority`, `expires`, `safe_to_act`, and `requires_confirmation`. OpenClaw’s memory guidance is useful here: operational memories should record boundaries, approvals, expiry, and success/failure evidence rather than vague advice. ([OpenClaw][11])

## 7. Scoping implementation

For IWE, represent scope structurally:

```text
global/index.md
  includes global/advice/...
  includes global/traps/...
  includes global/workflows/...

projects/github.com__owner__repo/index.md
  includes projects/github.com__owner__repo/decisions/...
  includes projects/github.com__owner__repo/traps/...
  includes projects/github.com__owner__repo/workflows/...
```

Then the wrapper can scope graph queries by inclusion ancestry.

Project scope:

```bash
cd "$VAULT"
iwe find --included-by "projects/$PROJECT_ID/index:0" "$QUERY"
```

Global scope:

```bash
cd "$VAULT"
iwe find --included-by "global/index:0" "$QUERY"
```

Both scopes can be implemented as two calls merged by the wrapper, which is more robust than exposing users to raw query syntax. IWE does support graph filters such as `$includedBy`, `$includes`, `$references`, and `$referencedBy`, but its query language is explicitly described as experimental, so hiding it behind a wrapper is the safer interface. ([IWE][14])

For body search, use filesystem paths instead of graph filters:

```bash
rg -n --glob '*.md' "$QUERY" \
  "$VAULT/projects/$PROJECT_ID" \
  "$VAULT/global"
```

Or with Probe:

```bash
probe search "$QUERY" \
  "$VAULT/projects/$PROJECT_ID" \
  "$VAULT/global" \
  --format json \
  --max-tokens 8000
```

Probe is a reasonable complement because it is a code-and-Markdown context engine with AST-aware search, BM25/TF-IDF/hybrid ranking, and no external service dependency. ([GitHub][15]) Its `search` command is designed to combine grep-like search with tree-sitter parsing and ranking, with JSON output and token limits that are useful for agents. ([GitHub][16])

## 8. Promotion workflow

Promotion should be explicit, not automatic.

A project-specific trap:

```text
projects/github.com__owner__repo/traps/do-not-regenerate-client.md
```

may become a reusable global lesson:

```text
global/traps/generated-client-regeneration.md
```

The wrapper should:

```text
1. Move or rename the note.
2. Change `scope: project` to `scope: global`.
3. Preserve `origin_project_id`.
4. Add a backlink or provenance note.
5. Use IWE link-aware refactoring where possible.
6. Optionally leave a short project note pointing to the global memory.
```

Example after promotion:

```yaml
---
type: trap
scope: global
origin_project_id: github.com__owner__repo
status: active
created: 2026-06-12
promoted: 2026-06-12
---
```

This avoids the main failure mode of agent memory systems: dumping every transient observation into global context. Project memory should be append-friendly; global memory should be curated.

## 9. Search and aggregation policy

Use three retrieval modes, not one.

First, **exact and lexical search**:

```bash
rg -n --glob '*.md' "token refresh" "$VAULT/projects/$PROJECT_ID" "$VAULT/global"
```

Second, **ranked contextual search**:

```bash
probe search '"token refresh" AND trap' "$VAULT/projects/$PROJECT_ID" "$VAULT/global"
```

Third, **graph expansion**:

```bash
cd "$VAULT"
iwe retrieve -k "projects/$PROJECT_ID/decisions/auth-token-refresh" --depth 2
```

This is better than immediately adopting embeddings. Most repo memory queries are lexical or structural: names of modules, commands, migrations, services, error messages, decisions, and traps. Embeddings can be added later, but should remain a rebuildable index over Markdown, not the canonical store.

## 10. When to fork IWE

Do not fork first. Compose first.

Fork IWE only if one of these becomes a hard requirement:

```text
1. First-class `--vault` support independent of current working directory.
2. Native `--scope project|global|both` query semantics.
3. Full-body search inside IWE itself.
4. Stable memory-specific commands: promote, demote, inbox, review.
5. A more stable query API than the currently experimental query language.
```

Until then, a wrapper is enough. IWE already owns the hard parts: Markdown graph interpretation, refactors, MCP, and normalized graph operations. Your wrapper owns the local convention.

## 11. Final recommendation

Build this as:

```text
agent-vault/
  core: IWE
  body search: rg + Probe
  optional indexed search: zk
  repo bootstrap: AGENTS.md + .agent-memory.toml
  owned code: thin `mem` CLI
```

Do **not** start with Basic Memory, Memsearch, or a vector database product unless you decide that agent-driven semantic retrieval is more important than transparent, manually editable, Git-versioned Markdown. Basic Memory is the nearest “LLM memory” tool, but its project isolation and index-backed design are not as natural for a single transferable cross-project vault. IWE plus a wrapper gives the cleaner substrate: one vault, normal files, normal Git, graph-aware Markdown operations, and standard CLI search.

[1]: https://github.com/iwe-org/iwe 'GitHub - iwe-org/iwe: Markdown memory system for you and your AI agent · GitHub'
[2]: https://iwe.md/docs/agentic/mcp/ 'MCP Server – IWE'
[3]: https://iwe.md/docs/agentic/find/ 'IWE Find – IWE'
[4]: https://github.com/zk-org/zk 'GitHub - zk-org/zk: A plain text note-taking assistant · GitHub'
[5]: https://zk-org.github.io/zk/notes/note-filtering.html 'Searching and filtering notes - zk : a plain text note-taking assistant'
[6]: https://zk-org.github.io/zk/notes/notebook.html 'Notebook - zk : a plain text note-taking assistant'
[7]: https://docs.basicmemory.com/start-here/what-is-basic-memory 'What is Basic Memory - Basic Memory'
[8]: https://docs.basicmemory.com/concepts/projects-and-folders 'Projects and folders - Basic Memory'
[9]: https://github.com/zilliztech/memsearch 'GitHub - zilliztech/memsearch: A persistent, unified memory layer for all your AI agents (e.g. Claude Code, Codex), backed by Markdown and Milvus. · GitHub'
[10]: https://knightli.com/en/2026/05/01/qmd-markdown-search-for-ai-agents/ 'qmd: Local Markdown Document Search for AI Agents'
[11]: https://docs.openclaw.ai/concepts/memory 'Memory overview - OpenClaw'
[12]: https://github.com/probelabs/memaris 'GitHub - probelabs/memaris: Stop re-teaching your AI. Turn past Claude Code sessions into persistent memory. · GitHub'
[13]: https://github.com/foambubble/foam 'GitHub - foambubble/foam: A personal knowledge management and sharing system for VSCode · GitHub'
[14]: https://iwe.md/docs/concepts/query-language/ 'Query Language – IWE'
[15]: https://github.com/probelabs/probe 'GitHub - probelabs/probe: AI-friendly semantic code search engine for large codebases. Combines ripgrep speed with tree-sitter AST parsing. Powers AI coding assistants with precise, context-aware code understanding. · GitHub'
[16]: https://github.com/probelabs/probe/blob/main/docs/probe-cli/search.md 'probe/docs/probe-cli/search.md at main · probelabs/probe · GitHub'
