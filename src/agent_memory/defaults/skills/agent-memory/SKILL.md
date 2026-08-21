---
name: agent-memory
description: "Use when deciding what belongs in agent memory, preserving significant experiences or reflections, managing plan records and the central vault, defining memory policy, converting historical material without erasing its evidential value, or invoking the agent-memory CLI. Contains the complete command surface: commands, options, defaults, and required arguments."
---

# Agent Memory

## Contents

- [Invocation](#invocation) — the one command form, and the output contract

- [Command Surface](#command-surface) — every command, option, and default

- [Options and Subcommands That Do Not Exist](#options-and-subcommands-that-do-not-exist)

- [Traps](#traps)

- [What Memory Is](#what-memory-is) — memory philosophy starts here

- [Requirements](#requirements)

- [Central Vault Policy](#central-vault-policy)

- [Memory Interaction Workflow](#memory-interaction-workflow)

- [Where Information Lives](#where-information-lives)

- [Decision Test](#decision-test)

- [Promotion Test](#promotion-test)

- [Entry Format](#entry-format)

- [Things to Avoid in Memories](#things-to-avoid-in-memories)

- [Transforming Historical Material into Memory](#transforming-historical-material-into-memory)

* * *

## Invocation

This is the only supported command form:

```bash
uvx --python 3.14 --from git+https://github.com/dzackgarza/agent-memory agent-memory <command>
```

Write the full prefix on every call.
There is no local install to configure, no `PATH` entry to rely on, and no provisioning step.
The runner resolves the latest revision each time, so the session always talks to current code.

Because the revision is never pinned, this document is the capability contract.
Read the tables below instead of probing `--help`.

Examples below write `agent-memory <command>` as shorthand for the full prefix.

### Output contract

Every command prints exactly one JSON object on stdout.
Three exceptions print raw Markdown: `retrieve`, `maintain squash`, and `maintain skill`.

Errors print `Error: <message>` on stderr and exit nonzero.

Search commands cap each result array at 10, not the response as a whole: `search` fills `results`, `key_matches`, `exact_content_matches`, `fuzzy_content_matches`, and `ranked_content_matches` independently, and `ranked_content_matches` holds one entry per matching block, so one file can repeat within it.
The cap is packaged configuration, not a command option.
Narrow the query, or filter the JSON with `jq`.

* * *

## Command Surface

`--scope` accepts `project`, `global`, or `both`. On `search`, `list`, `plan progress`, and `inspect links` it defaults to `both`. On every other `inspect` command and on `maintain normalize` it is **required**.

### Setup

| Command | Required | Optional (default) |
| --- | --- | --- |
| `maintain init-global VAULT` | `VAULT` — path to create | — |
| `init project --vault PATH` | `--vault` — existing global vault | `--project-id` (derived from the `origin` remote) |
| `doctor` | — | — |

Run `maintain init-global` once per machine.
Run `init project` once per repository.

### Memories

| Command | Required | Optional (default) |
| --- | --- | --- |
| `add` | `--scope`, `--type`, `--title`, `--content` | — |
| `search QUERY` | `QUERY` | `--scope` (both), `--visibility` (active) |
| `search keys QUERY` | `QUERY` | `--scope` (both), `--visibility` (active) |
| `search content QUERY` | `QUERY` | `--mode` (ranked), `--scope` (both), `--visibility` (active) |
| `search metadata` | — | `--scope` (both), `--type`, `--tag`, `--created-after`, `--visibility` (active) |
| `retrieve KEY` | `KEY` — a memory note, never a card | — |
| `update KEY` | `KEY` | `--title`, `--type`, `--content` |
| `delete KEY` | `KEY` | `--repoint`, `--orphan-ok` (False) |
| `list` | — | `--type` (every type), `--scope` (both), `--unmigrated` (False), `--visibility` (active) |

- `--type` on `add`, `update`, and `search metadata`: `decision`, `trap`, `advice`, `context`, `reference`, `plan`. `add --type plan` is rejected; plans are cards.

- `--mode` on `search content`: `exact`, `fuzzy`, `ranked`.

- `--repoint` takes a memory key or an external URL, and rewrites inbound wikilinks before deleting.
  `--orphan-ok` deletes while leaving inbound wikilinks dangling.

- `--created-after` takes an ISO timestamp, for example `2026-06-13T00:00:00+00:00`.

- `search metadata` filters typed frontmatter.
  It takes no positional query.

Keys are vault-relative paths: `projects/<project-id>/<directory>/<slug>` or `global/<directory>/<slug>`. The directory names are `decisions`, `traps`, `advice`, `context`, `references`, `plans`.

```bash
agent-memory add --scope project --type decision \
  --title "Parser choice" --content "Chose markdown-it because ..."
agent-memory search "parser"
agent-memory search content "markdown-it" --mode exact
agent-memory retrieve projects/agent-memory/decisions/parser-choice
agent-memory update projects/agent-memory/decisions/parser-choice --title "Parser choice (revised)"
```

### Cards

Cards are the typed, graph-linked records: plans, phases, tasks, features, specs, and card-form decisions.
Each type has a generated command group with the same four subcommands.

| Command | Required | Optional (default) |
| --- | --- | --- |
| `<type> add ID` | `ID` | `--parent`, `--set`, `--body`, `--body-file` |
| `<type> update ID` | `ID` | `--set`, `--body`, `--body-file` |
| `<type> show ID` | `ID` | — |
| `<type> delete ID` | `ID` | — |
| `card add TYPE ID` | `TYPE`, `ID` | same as `<type> add` |
| `card update ID` | `ID` | `--set`, `--body`, `--body-file` |
| `card show ID` / `card delete ID` | `ID` | — |
| `card migrate FROM` | `FROM` — in-repo directory to ingest | — |
| `card validate` | — | — |
| `card dag` | — | `--visibility` (active) |
| `plan progress` | — | `--scope` (both) |

`--set` repeats to append to a list field; `--set FIELD=` empties one.
There is no separate option for either.

Every card type has an optional Boolean `archived` field.
Archive with `--set archived=true` and restore with `--set archived=false`; the workflow `status` stays unchanged.
`--visibility` accepts `active`, `archived`, or `all` on list, search, and card DAG commands.
Active list and search results omit archived cards and return their identities in `archived_matches`. `<type> show ID` always returns an archived card with its stored fields and links.
The card DAG writes `plan-dag.md`, `plan-dag-archived.md`, or `plan-dag-all.md` for the selected visibility.
`inspect tree` and `inspect recent` cover memory-note indexes and timestamps; use list, search, show, or card DAG for cards.

The generated groups come from the active vault card schema.
The packaged default schema provides `feature`, `plan`, `phase`, `task`, `spec`, `decision`, and `papercut`. The `plan` group also carries `migrate`, `validate`, and `dag`, identical to their `card` equivalents.

| Type | Id prefix | `--parent` | Required `--set` fields |
| --- | --- | --- | --- |
| `feature` | `FEATURE-` | not required | `title` |
| `plan` | `PLAN-` | a `feature` | `title`, `status`, `description`, `successCriteria` |
| `phase` | `PHASE-` | a `plan` | `title`, `description`, `successCriteria` |
| `task` | `TASK-` | a `phase` | `title`, `description`, `successCriteria` |
| `spec` | `SPEC-` | a `feature` | `title` |
| `decision` | `DECISION-` | a `feature` | `title` |
| `papercut` | `PC-` | not required | `title` |

**This table is the packaged default schema, not a fixed contract.** Card types and their required fields come from the reader's own vault schema, which drifts: a vault keeps the schema it was initialized with until someone reconciles it.
If a command rejects a field this table does not list, or a whole type is missing, the vault schema is the authority and it is older or newer than this baseline.
`inspect schema` prints what the vault actually has.

Allowed `status` values on a plan card: `needs-agent-review`, `needs-human-input`, `approved-and-unstarted`, `in-progress`, `complete`, `blocked`, `unstarted`.

Optional fields on a `papercut`: `status`, `category` (`tool-failure`, `broken-env`, `stale-docs`, `bad-organization`, `surprising-behavior`, `missing-feature`, `workflow-friction`, `other`), `scope`, `agent`, `timestamp`, `description`, `resolution`. File one the moment a tool wastes your time; only `title` is required.

`--set` takes `key=value` and repeats for list fields.
`--set FIELD=` with nothing after the `=` empties a list field, on `add` and on `update` alike; it is the only way to clear one, since repeating `--set` only ever appends.
`--body` takes Markdown inline; `--body-file` takes a path.

`--parent` places the card and writes the `parents` link.
A card with several parents uses repeated assignments: `--set parents=[[FEATURE-A]] --set parents=[[FEATURE-B]]`. An explicit `parents` assignment replaces the link from `--parent`. Never write a bare `--set parents=` to add one.
An empty value clears the field.

Build the graph top down — a plan needs its feature to exist:

```bash
agent-memory feature add FEATURE-CLI-FRICTION --set title="CLI discovery friction"

printf '# Close the friction backlog\n\nMeasured from the invocation corpus.\n' \
  > /tmp/plan-body.md

agent-memory plan add PLAN-CLOSE-FRICTION-BACKLOG --parent FEATURE-CLI-FRICTION \
  --set title="Close the friction backlog" \
  --set status=in-progress \
  --set description="Remove the measured CLI-discovery friction." \
  --set successCriteria="Skill states the command surface" \
  --set successCriteria="Help probes fall to zero" \
  --body-file /tmp/plan-body.md

agent-memory phase add PHASE-DOCS --parent PLAN-CLOSE-FRICTION-BACKLOG \
  --set title="Documentation" \
  --set description="Rewrite the shipped skill document." \
  --set successCriteria="Interface section is findable first"

agent-memory task add TASK-REWRITE-SKILL --parent PHASE-DOCS \
  --set title="Rewrite the skill document" \
  --set description="State the surface instead of delegating to --help." \
  --set successCriteria="Cold agent reaches a correct first invocation"

agent-memory plan update PLAN-CLOSE-FRICTION-BACKLOG --set status=complete
agent-memory plan show PLAN-CLOSE-FRICTION-BACKLOG
```

Card bodies are editable through the CLI. Never hand-edit vault Markdown to change a body.

### Structured todos

| Command | Required | Optional |
| --- | --- | --- |
| `todo set KEY TODO-ID` | `KEY`, `TODO-ID` | `--status`, `--content`, `--note` |

`KEY` is the full vault-relative plan key, not a card id.

### Inspect

Read-only.
Every command emits JSON.

| Command | Required | Optional (default) |
| --- | --- | --- |
| `inspect overview` | `--scope` | `--format` (json) |
| `inspect schema` | — | `--format` (json) |
| `inspect paths` | `--scope` | `--kind` (all), `--format` (json) |
| `inspect tree` | `--scope`, `--depth INT` | `--format` (json) |
| `inspect links [KEY]` | — | `--broken` (False), `--scope` (both), `--direction` (both), `--depth` (1), `--format` (json) |
| `inspect outline KEY` | `KEY` | `--format` (json) |
| `inspect stats` | `--scope`, `--by` | `--format` (json) |
| `inspect recent` | `--scope`, `--since ISO` | `--format` (json) |
| `inspect export` | `--scope`, `--profile` | `--format` (graph-json) |

- `--format` never needs to be passed.
  `json` is its only accepted value on every `inspect` command, and `graph-json` is the only value on `inspect export`.

- `--kind`: `roots`, `indexes`, `notes`, `all`.

- `--by`: `type`, `scope`, `day`.

- `--direction`: `children`, `parents`, `both`.

- `--profile`: `map`, `context`, `archive`.

- `inspect links` needs `KEY` unless `--broken` is set.

### Vault maintenance

| Command | Required | Optional |
| --- | --- | --- |
| `maintain normalize` | `--scope` | — |
| `maintain move KEY --to DEST` | `KEY`, `--to` | — |
| `maintain split KEY --section TITLE` | `KEY`, `--section` | — |
| `maintain merge KEY --reference KEY` | `KEY`, `--reference` | — |
| `maintain squash KEY --depth N` | `KEY`, `--depth` | — |
| `maintain skill NAME` | `NAME` | — |
| `maintain add-card-status-option STATUS-SET STATUS` | both | — |
| `links rewrite` | — | `--from`, `--to`, `--map` |

- `maintain move --to` accepts destinations under `global/` only, such as `global/traps`.

- `maintain squash` prints rendered Markdown, not JSON.

- `maintain skill` accepts `agent-memory` and `vault-maintenance`, and prints Markdown.
  `maintain skill agent-memory` prints this document — it is how a cold agent fetches the command surface.
  Load `vault-maintenance` when a vault defect blocks a memory operation.

- `links rewrite` takes either `--from`/`--to` or `--map`, a TOML file with a `[rewrites]` table.

### Queue

| Command | Required | Optional |
| --- | --- | --- |
| `queue add` | `--project`, `--status`, `--summary` | `--agent`, `--timestamp`, `--link`, `--set` |
| `queue list` | — | — |

`--link` repeats for multiple wikilinks.

### Sync

| Command | Required |
| --- | --- |
| `sync run` | — |
| `sync status` | — |
| `sync install SECONDS` | `SECONDS` — positive timer interval |
| `sync enable` / `sync disable` / `sync remove` | — |

`sync run` commits vault changes, rebases from origin, and pushes.

* * *

## Options and Subcommands That Do Not Exist

| Attempted | Reality |
| --- | --- |
| `--limit` | Nowhere. Search returns at most 10 results. Narrow the query or filter with `jq`. |
| `--json` | Nowhere, and unnecessary: output is already JSON. |
| `--format` on `add`, `search`, `list`, `retrieve`, or card commands | `--format` exists only on `inspect` subcommands. |
| `--body` / `--body-file` on `add` or `update` | Memory bodies use `--content`. `--body`/`--body-file` are card options. |
| `--append-content` / `--append-body-file` | No append exists. Read, append locally, write the whole body back. |
| `plan list` | `list --type plan` |
| `retrieve -k KEY` | `retrieve KEY` |
| `plan push` | `sync run` |
| `plan retrieve` | `plan show ID`, or `card show ID`. `retrieve` reads memory notes only — it refuses card keys, because rendering a card as a note keeps the body and drops `id`, `status`, `parents`, and every other field. `list` returns card keys, so check the key before choosing a reader. |
| `inspect` with no subcommand | Pick one: `overview`, `schema`, `paths`, `tree`, `links`, `outline`, `stats`, `recent`, `export`. |
| `plan show`, `plan dag` | Both exist. See the card tables above. |

### Appending to a body

`--content`, `--body`, and `--body-file` all replace the body.
To add to an existing record, extract the current body, append locally, then write the whole body back.

Extraction is the step that goes wrong.
Neither `retrieve` nor `show` emits a bare body:

- `retrieve KEY` wraps the note in a four-backtick `` ```markdown `` fence whose opener carries the key as `#<key>`, then a `---` frontmatter block, then an `# <title>` heading, then the body.

- `<type> show ID` emits JSON. The body is the `body` field.

**Memories.** Use `maintain squash --depth 1`, which renders the note with no fence and no frontmatter, then drop the `# <title>` heading it starts with:

```bash
agent-memory maintain squash projects/myproj/decisions/parser-choice --depth 1 \
  | sed '1,/^$/d' > /tmp/body.md
printf '\n## Later analysis\n\nThe fuzzy mode turned out to matter.\n' >> /tmp/body.md
agent-memory update projects/myproj/decisions/parser-choice --content "$(cat /tmp/body.md)"
```

Do not keep the heading.
`add` and `update` prepend `# <title>` themselves and derive `description` from the first line of `--content`, so a body that still starts with the heading produces a duplicated heading and sets `description` to `'# <title>'`.

Depth 1 renders that note only: outgoing wikilinks stay as written and no linked note is inlined.

**Cards.** Take the `body` field out of the JSON:

```bash
agent-memory plan show PLAN-ID | jq -r .body > /tmp/card-body.md
printf '## Later analysis\n\nThe second workstream landed first.\n' >> /tmp/card-body.md
agent-memory plan update PLAN-ID --body-file /tmp/card-body.md
```

* * *

## Traps

- `list` with no `--type` lists every type.
  A `--type` naming no type in the schema is an error, not an empty result, so a misspelling never reads as "the vault holds none".

- `--scope` defaults to `both` on `search` and `list`, but is required on `inspect` commands and `maintain normalize`.

- A card id must carry its type's prefix.
  `plan add PLAN-X` succeeds; `plan add X` fails.

- `add --type plan` is rejected.
  Create plans with `plan add`.

- `maintain move` destinations must start with `global/`.

- Never create, edit, or reorganize vault Markdown or memory directories by hand.
  Every field and every body is reachable through the commands above.
  Hand edits bypass index maintenance, link rewriting, and frontmatter reconciliation.

- Do not call `iwe` directly for memory work.

* * *

## What Memory Is

Memory is the durable substrate of experience and continuing interpretation, not a technical ledger of instructions for a future agent.

A valuable memory may preserve:

- an episode: what happened, in what context, and in what sequence;

- consequences and salience: what failed, what changed, and why it mattered;

- causal cues visible at the time;

- contemporaneous reflections, hypotheses, and counterfactuals, labeled as such;

- later reinterpretations that supplement rather than rewrite the original episode;

- stable facts, decisions, or working guidance when those really are known.

Do not assume that an incident yields a complete prevention rule.
One episode can suggest causes or interventions without establishing them.
A proposed lesson such as “avoid X next time” is a time-localized hypothesis, not proof that X caused the incident or that the intervention prevents the whole failure class.

Keep these layers distinguishable when each carries durable value:

1. **Experience/evidence** — what occurred and the observable sequence.

2. **Reflection/interpretation** — what seemed causally important, including uncertainty and alternative explanations.

3. **Policy/intervention** — a proposed future action, explicitly provisional until experience supports it.

A policy may link to a memory, but it must not replace the experience that made the policy seem plausible.
If the policy later fails, the preserved episode must still support a new interpretation.

## Requirements

Every memory entry must be:

- **Durable** — likely useful in future sessions, whether for recognition, reconsideration, resumption, or action.

- **Non-duplicative** — not merely a copy of git history, public execution state, or an existing memory.

- **Specific** — preserves the concrete facts, context, sequence, causal cues, or decision needed to understand it later.

- **Epistemically labeled** — distinguishes observation, contemporaneous interpretation, later analysis, and proposed intervention where they differ.

- **Revisable** — later understanding can supplement or challenge the interpretation without rewriting the original experience to make the new theory look inevitable.

A memory does **not** need to contain a command, decision rule, remediation, or observable success condition.
Requiring actionability would systematically discard experiences whose future value is recognition and reinterpretation.

### When to Save Memories

Preserve an experience while its sequence, consequences, causal cues, and contemporaneous reflections are still available when:

- a surprising or consequential failure occurred;

- several corrections revealed a pattern that cannot be understood from the final rule alone;

- the cause remains uncertain or several explanations remain plausible;

- a proposed remedy has not been validated;

- future recognition of a similar situation may matter even if no instruction is known;

- later reinterpretation would require details that a compressed lesson would erase.

Also preserve stable findings, decisions, rationale, environment knowledge, and resumption context when they outlive the current session.

Do not create a memory merely because a command succeeded or a task finished.
Preserve what a future agent would otherwise be unable to reconstruct from the owning source.

### When to Check Memories

Check memories when starting related work:

- Before investigating a problem area

- When working on a feature you have touched before

- When resuming work after a conversation break

## Central Vault Policy

`agent-memory` is the only agent-facing interface for durable memories and project planning state.
Agents do not need to know or call the storage backend during normal project work.

The configured vault is the only durable agent-facing store for memory and plan state.
Do not create loose repo-local Markdown plans, correction logs, decision ledgers, or agent-facing doctrine files as substitutes for typed `agent-memory` records.

Use repo-local files only as temporary scratchpads while working through in-the-weeds investigation.
Before handoff, delete the scratchpad or promote its durable content:

- significant experiences, causal sequences, contemporaneous reflections, and unresolved interpretations -> `reference` or `context` memories;

- reusable working guidance and proposed interventions -> `advice` or `trap` memories, linked to the experience that grounds them when that experience matters;

- stable decisions and rationale -> `decision` memories;

- plans, phase state, queues, and residue ledgers -> plan cards;

- durable product/project doctrine, architecture rationale, and readable roadmap/proof projections -> wiki;

- active public user stories, roadmap nodes, feature contracts, proof burdens, execution state, bugs, gaps, and handoff contracts -> GitHub issue trees, milestones, or PR claim maps.

If the same fact appears in multiple surfaces, choose one authoritative owner and replace other copies with links or delete them.
Memory can point at GitHub or wiki artifacts, but it should not duplicate their live status.

The vault owns project records and memory data.
A checkout is bound when its `.agents` and `.hermes` links target the vault-owned project directory; a fresh clone is unbound until those links are installed even when that project already exists in the vault.
`.agent-memory.toml` is not part of the binding.

## Memory Interaction Workflow

1. **Search first** — Before writing a new memory, search existing memories with `agent-memory search`.

2. **Prefer update** — If a relevant memory exists, update it with `agent-memory update` instead of creating a duplicate.
   Edit additively: append a new section rather than rewriting the original account.
   See [Appending to a body](#appending-to-a-body).

3. **Never refactor or remove behind the tool** — Do not refactor, reorganize, or remove existing memory content outside `agent-memory`.

4. **Create via `agent-memory add`** — If no relevant memory exists, create a new typed entry through the CLI.

Meaningful titles and tags become the searchable anchors for steps 1 and 2. Keep one topic per record.

## Where Information Lives

| Information | Location | Example |
| --- | --- | --- |
| Significant experiences and their consequences | `reference` or `context` memories | Correction sequence preserving what happened, what seemed causally important, and what remains uncertain |
| Later interpretations and provisional lessons | Linked memories or additive sections in the experience record | “At the time, rushing and missing directions appeared relevant; leaving earlier might help” |
| Reusable operational guidance and proposed interventions | `advice` or `trap` memories | “Avoid unescaped `%` in crontab; build time strings in recipe/script” |
| Decisions with future agent relevance | `decision` memories | “Treat generated AGENTS output as derived; edit fragments instead” |
| Plans, phase state, contracts, queues, and residue ledgers | Plan cards | “Current phase is extraction; active residue is parser boundary proof” |
| Durable project doctrine, architecture rationale, and readable roadmap/proof projections | GitHub wiki | Feature page linked to stories, proof burdens, issues, and roadmap projections |
| Active public stories, roadmap nodes, proof burdens, execution state, bugs, gaps, handoffs, and TODOs | GitHub issue trees, milestones, and PR claim maps | Story issue with proof obligations; draft PR linked to milestone and claim set |

Completed work belongs in commits.
The experience of doing the work belongs in memory only when its sequence, consequences, interpretation, or epistemic salience has future value beyond the diff.
TODOs and gaps belong in GitHub issues, not repo artifacts.

## Decision Test

Before writing a memory, ask:

1. **What would be lost if this fades?** A sequence, consequence, causal cue, reflection, stable fact, rationale, or resumption context must be identifiable.

2. **Can the owning source reconstruct it?** If git, an issue, a paper, or current docs already preserve the same meaning, link or rely on that source instead of duplicating it.

3. **Does the value depend on the episode, not only a rule?** If later reinterpretation would need what happened and in what order, preserve the experience before extracting advice.

4. **What is known versus suspected?** Label direct observation, contemporary inference, later analysis, and proposed intervention separately.

5. **Is this live execution state?** TODOs and public work status belong in GitHub; plans belong in plan cards; neither should be disguised as memory.

A memory is warranted when future recognition or understanding would materially degrade without it.
It need not prescribe what the future agent should do.

## Promotion Test

After deciding that a memory is warranted, decide whether memory alone is sufficient.
Also update GitHub or the wiki when the correction or decision affects:

- public project direction, requirements, user stories, roadmaps, or proof burdens;

- work that multiple agents, branches, repos, or future PRs must coordinate;

- observed bugs, inefficiencies, false greens, or follow-up gaps in an owned repo;

- handoff state that should be auditable without reading the private vault.

Use memory for the reusable lesson and GitHub/wiki for the public project state.
Do not use memory to hide actionable repo work from the owning repo's issue tracker.

## Entry Format

Memories are stored as typed Markdown records.
`agent-memory` writes the frontmatter; supply the title, type, scope, and body through the commands above.

```yaml
---
title: Memory Title
status: active            # Optional: active, draft, reviewed, archived
tags: [tag1, tag2]
---
```

Memory entries should make their own epistemic role clear.
Depending on the record, include:

- **Experience:** what happened, context, sequence, consequences, and salient details.

- **Reflection:** what seemed important at the time and why.

- **Later analysis:** revised interpretations, alternative explanations, and evidence boundaries.

- **Proposed intervention:** what might help, with uncertainty and validation status.

- **Stable fact or decision:** the fact, source, rationale, and scope.

- **Resumption state:** what a future session needs to continue.

Do not force every memory to contain all of these.
In particular, do not invent an action or verification test for an experience whose durable value is recognition and reinterpretation.

Write titles and summaries that tell a future agent whether the record is an episode, reflection, hypothesis, decision, reference, or working rule.

## Things to Avoid in Memories

- **Git-history duplication** — a bare list of commits or file changes with no experience, consequence, or interpretation beyond what git preserves.

- **Status mirrors** — live TODOs, current issue state, or manually copied project status.

- **Contentless summaries** — accomplishments without salient detail or future interpretive value.

- **Premature normative compression** — replacing a consequential episode with only the rule currently believed to prevent recurrence.

- **Retrospective certainty** — rewriting tentative contemporary impressions as if the final causal account was known during the event.

- **Universal prevention claims from one incident** — preserve likely contributing factors and possible counterfactuals without claiming complete control of the failure class.

Chronology is not automatically noise.
Preserve sequence when cause and effect, correction assimilation, escalation, or changing interpretation depends on it.
Omit dates and step-by-step narration only when they add no meaning beyond the owning source.

## Transforming Historical Material into Memory

### Preserve before interpreting

When the value lies in an incident or experience, preserve enough of the episode to make future reinterpretation possible:

- the context and sequence;

- observed consequences;

- causal cues noticed at the time;

- contemporaneous reactions or lessons;

- later analysis and alternative explanations;

- what remains unknown;

- proposed interventions, clearly separated from the memory itself.

Do not rewrite the episode into a morality tale that makes the current policy look inevitable.
Add later interpretations rather than silently replacing earlier ones.

When the source is merely noisy documentation of a stable technical fact, concise extraction remains appropriate.
The distinction is whether compression would remove causal or experiential information needed to understand future similarities.

### Examples

| Source material | Durable form |
| --- | --- |
| A crash preceded by phone use, speeding, a missed light, rushing, and absent directions | Preserve the episode and consequences; note that distraction, speed, and rushing seemed relevant; record “leave earlier / avoid phone use” only as provisional counterfactuals |
| A correction sequence where each objection caused more schema machinery | Preserve the sequence and the consequences of each assimilation; link any proposed frame-reset rule as a separate hypothesis |
| API stderr has a documented rate-limit marker and no JSON | Stable technical fact: classify that response as `RATE_LIMIT`, with the source and exact boundary |
| Several cron edits revealed `%` expansion semantics | Stable technical lesson plus the observed failure if it would help recognize related quoting problems |
| “Spent two hours debugging” with no consequential sequence or insight | Omit the duration; preserve only the facts, surprises, or interpretations that matter |

### Epistemic continuity test

Before saving, ask:

> If the proposed lesson proves wrong, does this memory still contain enough experience and analysis to understand the incident and formulate a different lesson?

If no, the record has been compressed too far.

## Related Design Notes

- [OpenCode memory design notes](references/opencode-memory-design-notes.md) records durable design ideas salvaged from older Claude-local assistant and hook systems.
  Use it when designing or reviewing OpenCode memory/todo tooling; do not reintroduce the retired hook/session implementations.

- [Memory rubric](references/memory-rubric.md) scores whether a candidate record earns its place.
