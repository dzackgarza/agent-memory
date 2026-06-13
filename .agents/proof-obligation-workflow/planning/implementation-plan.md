# Implementation Plan

Source: [DESIGN-TRANSCRIPT.md](../../../DESIGN-TRANSCRIPT.md)

Stack:

- Python package managed by uv with `requires-python = ">=3.14"`.
- Cyclopts for CLI shape and help generation.
- Pydantic v2 for strict command/config models.
- Standard library `tomllib` plus `tomli-w` for TOML output.
- Git, IWE, `rg`, `npx`, `@probelabs/probe`, and `zk` as required subprocess
  dependencies.

Repository structure:

- `src/iwe2/__init__.py`: package exports.
- `src/iwe2/__main__.py`: executable module entrypoint.
- `src/iwe2/cli.py`: Cyclopts app and subcommands only.
- `src/iwe2/models.py`: strict Pydantic contracts and enums.
- `src/iwe2/operations.py`: typed orchestration and filesystem/subprocess behavior.
- `tests/test_cli_workflows.py`: public CLI integration tests through `uv run iwe2`.

Public API:

- `iwe2 vault init <vault>`
- `iwe2 project init --vault <vault>`
- `iwe2 note --scope <project|global> --type <decision|trap|workflow|fact|advice|convention> --title <title> --content <content>`
- `iwe2 search --scope <project|global|both> <query>`
- `iwe2 search-context --scope <project|global|both> --max-results <count> --max-tokens <count> <query>`
- `iwe2 search-index --scope <project|global|both> --limit <count> <query>`
- `iwe2 retrieve <key>`
- `iwe2 squash <key> --depth <depth>`
- `iwe2 promote <key> --to <global-subdir>`
- `iwe2 doctor`

State model:

- The central vault is the source of memory files.
- The central vault is a Git repository whose wrapper-owned mutations are committed
  automatically.
- The repo-local `.agent-memory.toml` is a required machine-readable pointer for
  project-scoped commands.
- The repo-local `AGENTS.md` memory section is a required human/agent bootstrap pointer.
- Notes are Markdown files with strict frontmatter and body text.
- Project IDs are derived from Git remotes using the transcript's `github.com__owner__repo` shape.

Configuration model:

- No runtime default vault path.
- `vault init` and `project init` receive explicit vault paths.
- Project-scoped commands require `.agent-memory.toml`.
- The generated config is complete and validated before use.

Error model:

- Required binaries, paths, Git metadata, IWE commands, `rg` commands, Probe commands,
  and `zk` commands fail loudly.
- Runtime code does not substitute empty search results for command failure.
- Invalid command data fails at Pydantic/Cyclopts boundaries.

Code ownership budget:

| Component                            | Planned owner | Local code allowed                              | Local code forbidden                                |
| ------------------------------------ | ------------- | ----------------------------------------------- | --------------------------------------------------- |
| CLI presentation                     | local         | Cyclopts command declarations and docstrings    | Business logic in callbacks                         |
| Config/models                        | local         | Pydantic models, enums, path validation         | Loose dict/Any config plumbing                      |
| Vault/project/note/search operations | local         | Thin filesystem and subprocess orchestration    | Custom graph parser, database, embeddings, reranker |
| Graph retrieval/refactor             | IWE           | Subprocess invocation with checked failures     | Reimplementing backlink or context retrieval        |
| Title/key graph search               | IWE           | Scope anchor selection and output formatting    | Custom fuzzy matcher or graph-query implementation  |
| Body search                          | `rg`          | Scope root selection and output formatting      | Custom search index or fuzzy ranker                 |
| Ranked contextual search             | Probe         | Scope root selection and JSON merge             | Custom ranking, parser, or embedding search         |
| Indexed Markdown search              | `zk`          | Scope root selection and JSON result validation | Custom search index or fuzzy ranker                 |
| Tests                                | local         | Real CLI integration tests                      | Mocks, source-text policy tests, helper-only tests  |
