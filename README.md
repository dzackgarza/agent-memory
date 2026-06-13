# iwe2

`iwe2` is a global and project-scoped Markdown memory database for agents.
It stores memories as plain files in an IWE-backed vault and exposes a small CLI for normal agent work:
create, retrieve, update, delete, and search memories.

## Install And Setup

Install the CLI globally from this checkout:

```bash
just install
```

Run the first-time setup prompt for the global vault:

```bash
just setup
```

`just setup` installs the editable tool, prompts for the global vault path with `gum`, and runs:

```bash
iwe2 maintain init-global --vault <vault>
```

Bind a repository to that vault:

```bash
iwe2 init project --vault <vault>
```

This writes `.agent-memory.toml` in the repository and adds an `AGENTS.md` pointer to the project memory key.

## Normal Workflow

Create a memory:

```bash
iwe2 add --scope project --type decision --title "Parser choice" --content "Use the existing parser boundary."
```

Retrieve a memory:

```bash
iwe2 retrieve projects/<project-id>/decisions/parser-choice
```

Update a memory:

```bash
iwe2 update projects/<project-id>/decisions/parser-choice --content "Updated Markdown body."
```

Delete a memory:

```bash
iwe2 delete projects/<project-id>/decisions/parser-choice
```

Validate the current repository setup:

```bash
iwe2 doctor
```

## Search

Use default search when an agent needs context and does not know which search mode is best:

```bash
iwe2 search --scope both "parser"
```

Default search returns JSON with deduped `results` and separate sections for key/title, exact content, fuzzy content, and ranked content matches.

Use explicit search modes when needed:

```bash
iwe2 search keys --scope project "parser"
iwe2 search content --scope both --mode exact "literal text"
iwe2 search content --scope both --mode fuzzy "approximate topic"
iwe2 search content --scope both --mode ranked "semantic context"
iwe2 search metadata --scope project --type decision --tag project --created-after 2026-06-13T00:00:00+00:00
```

## Maintenance

Maintenance commands are intentionally separate from normal agent CRUD/search work:

```bash
iwe2 maintain init-global --vault <vault>
iwe2 maintain move <key> --to global/traps
iwe2 maintain split <key> --section "Section Title"
iwe2 maintain merge <key> --reference <other-key>
iwe2 maintain squash <key> --depth 3
iwe2 maintain validate
```

## Dependencies

Runtime tools:

- `git`
- `iwe`
- `rg`
- `npx`
- `@probelabs/probe`
- `zk`

Setup prompt dependency:

- `gum`

Python dependencies are declared in `pyproject.toml`.
