Closes #99.

Refs #98, #93, #41, and #14.

## Claim

One record boundary now owns every vault read and write. It resolves the key, vault,
project, record type, validator, and supported command before mutation.

## Delivered contract

- [x] Resolve records before each read or mutation.
  - [x] Fully qualified project keys ignore the caller's current project.
  - [x] Global keys work without a project binding.
  - [x] Memory commands refuse card keys and name the card command.
- [x] Make schema-backed cards fully writable.
  - [x] Generated update commands accept `--body` and `--body-file`.
  - [x] `--parent` controls placement and writes the containment link.
  - [x] `--set FIELD=` clears lists, removes optional fields, and rejects required fields.
- [x] Make reads usable from normal working directories.
  - [x] Unbound `--scope both` reads global records.
  - [x] Search and list results expose canonical card identity and next commands.
  - [x] Read-command defaults match the observed invocation corpus.
- [x] Keep validation local and complete.
  - [x] Malformed cards become findings instead of scan aborts.
  - [x] Validation reports local reference-closure problems separately.
  - [x] Each generated DAG contains the local transitive reference closure only.
- [x] Complete the schema-generated command surface.
  - [x] `papercut` uses the schema-generated commands.
  - [x] Generated add help states root types, parent types, and required fields.
- [x] Publish the agent-facing capability contract.
  - [x] The packaged skill states every command, option, default, and required argument.
  - [x] Retired command forms name their current replacement.
  - [x] Bare command groups fail without JSON output.
- [x] Keep ranked search stable.
  - [x] Ranked results include canonical keys and skipped matching files.
  - [x] Empty zero-score records do not change identical query results.
  - [x] The scoring engine uses the pinned `@probelabs/probe` package.
  - [x] Probe resolves only for search dispatch.

## Interface decisions

Cards remain outside memory-note commands. Use generated card commands for card reads,
updates, and deletion.

`search metadata` is a typed frontmatter filter. It takes no positional query.

The `uvx --python 3.14 --from git+...` form always resolves the current revision. The
packaged skill is the stable capability contract. `inspect schema` reports the active
vault schema.

## Proof

The regression suite covers card body replacement, field removal, cross-project key
resolution, unbound reads, malformed-card findings, local DAG closure, ranked-output
stability, command failure output, and packaged skill delivery.

The push gate and required GitHub checks own the final repository-wide proof.
