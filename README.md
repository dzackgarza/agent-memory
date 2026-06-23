# agent-memory

`agent-memory` is a global and project-scoped Markdown memory database for agents.
It stores memories and vault-backed plan cards as plain files in an IWE-backed vault and exposes a small CLI for normal agent work: create, retrieve, update, delete, search, inspect, and plan-card lifecycle operations.

## Install And Setup

Install the CLI globally from this checkout:

```bash
just install
```

This installs `agent-memory` as an editable global `uv` tool and provisions the command-line tools that the app invokes:

- `iwe` through the documented Cargo installer when absent
- `rg` through Cargo when absent
- `zk` `v0.15.5` into `~/.local/bin`
- Probe through `npx -y @probelabs/probe@latest`

Run the first-time setup prompt for the global vault:

```bash
just setup
```

`just setup` runs `just install`, prompts for the global vault path with `gum`, and runs:

```bash
agent-memory maintain init-global --vault <vault>
```

Bind a repository to that vault:

```bash
agent-memory init project --vault <vault>
```

This writes `.agent-memory.toml` in the repository, adds an `AGENTS.md` pointer to the project memory key, creates vault-owned project `.agents` and `.hermes` directories, and symlinks the repository `.agents` and `.hermes` paths to those vault locations.
Existing local `.agents` or `.hermes` contents are moved into the vault-owned project directory during initialization.

## Normal Workflow

Create a memory:

```bash
agent-memory add --scope project --type decision --title "Parser choice" --content "Use the existing parser boundary."
```

Memory types are `decision`, `trap`, `advice`, `context`, `reference`, and `plan`.

Retrieve a memory:

```bash
agent-memory retrieve projects/<project-id>/decisions/parser-choice
```

`retrieve` expects a full vault-relative key.
Use `agent-memory search --scope both "<term>"` to discover keys first; basename or slug fragments such as `parser-choice` are not a supported `retrieve` contract.

Update a memory:

```bash
agent-memory update projects/<project-id>/decisions/parser-choice --content "Updated Markdown body."
```

Delete a memory:

```bash
agent-memory delete projects/<project-id>/decisions/parser-choice
```

Validate the current repository setup:

```bash
agent-memory doctor
```

## Plan Cards

Project plan cards live in the same vault under `projects/<project-id>/plans/`. The shipped source of truth is the `agent-memory plan ...` surface, not a parallel in-repo `.agents/plans` tree after migration.

Create plan cards in the vault:

```bash
agent-memory plan add --type feature --id FEATURE-DEMO --set title=Demo --set status=in-progress --set description="Plan card in the vault"
agent-memory plan add --type plan --id PLAN-DEMO --parent FEATURE-DEMO --set title=Plan --set status=in-progress --set parents=[[FEATURE-DEMO]] --set successCriteria=ships
```

Validate and render the shared DAG:

```bash
agent-memory plan validate
agent-memory plan dag
```

Migrate an existing in-repo card tree into the vault:

```bash
agent-memory plan migrate --from .agents/plans
```

Search and retrieve plan cards through the same vault-key surface:

```bash
agent-memory search --scope project "plan card"
agent-memory retrieve projects/<project-id>/plans/features/FEATURE-DEMO/FEATURE-DEMO
```

`plan validate` and `plan dag` load every registered project plans root from the vault metadata, so cross-project `dependsOn` references are validated and rendered from one shared graph.

## Search

Use default search when an agent needs context and does not know which search mode is best:

```bash
agent-memory search --scope both "parser"
```

Default search returns JSON with deduped `results` and separate sections for key/title, exact content, fuzzy content, and ranked content matches.

Use explicit search modes when needed:

```bash
agent-memory search keys --scope project "parser"
agent-memory search content --scope both --mode exact "literal text"
agent-memory search content --scope both --mode fuzzy "approximate topic"
agent-memory search content --scope both --mode ranked "semantic context"
agent-memory search metadata --scope project --type decision --tag project --created-after 2026-06-13T00:00:00+00:00
```

## Inspect

Use `inspect` when an agent needs to understand a large vault without mutating it:

```bash
agent-memory inspect overview --scope both --format json
agent-memory inspect schema --format json
agent-memory inspect paths --scope project --kind notes --format json
agent-memory inspect tree --scope project --depth 2 --format json
```

Use targeted inspect commands after a search result or known key:

```bash
agent-memory inspect links projects/<project-id>/decisions/parser-choice --direction parents --depth 1 --format json
agent-memory inspect outline projects/<project-id>/decisions/parser-choice --format json
agent-memory inspect recent --scope both --since 2026-06-13T00:00:00+00:00 --format json
agent-memory inspect stats --scope both --by type --format json
agent-memory inspect export --scope project --profile map --format graph-json
```

`inspect` is read-only.
Use it for navigation, schema discovery, path enumeration, graph traversal, outline extraction, recency filtering, and graph export.
Maintenance commands remain under `maintain`.

## Maintenance

Maintenance commands are intentionally separate from normal agent CRUD/search work:

```bash
agent-memory maintain init-global --vault <vault>
agent-memory maintain move <key> --to global/traps
agent-memory maintain split <key> --section "Section Title"
agent-memory maintain merge <key> --reference <other-key>
agent-memory maintain squash <key> --depth 3
```

## Dependencies

Runtime tools:

- `git`
- `iwe`
- `rg`
- `npx`
- `@probelabs/probe`
- `zk`

Setup and installation tools:

- `uv`
- `cargo`
- `gh`
- `gum`
- `tar`
- `trash`

Python dependencies are declared in `pyproject.toml`.
