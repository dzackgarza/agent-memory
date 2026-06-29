from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Annotated

import cyclopts
from cyclopts import App, Parameter
from pydantic import ValidationError

from agent_memory.cards.config import CardSystemConfig
from agent_memory.cards.loader import load_card_system_config
from agent_memory.cards.storage import CardPlacementError
from agent_memory.models import (
    ContentSearchMode,
    InspectExportFormat,
    InspectExportProfile,
    InspectLinkDirection,
    InspectOutputFormat,
    InspectPathKind,
    InspectStatsGroup,
    MemoryScope,
    MemoryType,
    SearchScope,
)
from agent_memory.operations import (
    INSPECT_COMMAND_NAMES,
    CardFieldError,
    DependencyError,
    GlobalVaultNotInitializedError,
    JsonValue,
    MalformedMemoryError,
    MemoryOperationError,
    ProjectNotInitializedError,
    VaultCommitError,
    add_memory,
    add_plan_card,
    basic_doctor,
    delete_memory,
    delete_plan_card,
    init_global_vault,
    init_project,
    inspect_export,
    inspect_links,
    inspect_outline,
    inspect_overview,
    inspect_paths,
    inspect_recent,
    inspect_schema,
    inspect_stats,
    inspect_tree,
    merge_memory,
    migrate_plan_cards,
    move_memory,
    retrieve_memory,
    search_content_exact,
    search_content_fuzzy,
    search_content_ranked,
    search_keys,
    search_memories,
    search_metadata,
    split_memory,
    squash_memory,
    update_memory,
    update_plan_card,
    validate_plan_cards,
    write_plan_dag,
)
from agent_memory.operations import (
    doctor as run_doctor,
)

app = App(
    name="agent-memory",
    help=(
        "Memory database CLI for global and project Markdown vaults. "
        "Use `agent-memory maintain init-global --vault <path>` once, "
        "`agent-memory init project --vault <path>` per repository, then `add`, `search`, "
        "`inspect`, `retrieve`, `update`, `delete`, and `plan` during normal agent work."
    ),
)
init_app = app.command(App(name="init", help="Initialize project memory bindings."))
search_app = app.command(App(name="search", help="Query memories by keys, content, or metadata."))
inspect_app = app.command(App(name="inspect", help="Read-only vault navigation and analysis commands."))
maintain_app = app.command(App(name="maintain", help="Vault setup and maintenance workflows."))
plan_app = app.command(App(name="plan", help="Create, migrate, validate, and visualize vault-backed project plan cards."))


class CliUsageError(RuntimeError):
    """Raised when arguments are coherent CLI syntax but invalid together."""


def maintain_init_global(
    vault: Annotated[Path, Parameter(help="Path to the global memory vault to initialize.")],
) -> None:
    """Create the global IWE-backed memory vault once."""
    emit(init_global_vault(vault))


def init_project_command(
    *,
    vault: Annotated[Path, Parameter(help="Existing global memory vault for this repository.")],
    project_id: Annotated[str | None, Parameter(help="Stable project id for repositories without an origin remote.")] = None,
) -> None:
    """Bind the current Git repository to the global memory vault."""
    emit(init_project(vault=vault, cwd=Path.cwd(), project_id=project_id))


def add_command(
    *,
    scope: Annotated[MemoryScope, Parameter(help="Memory scope: project or global.")],
    memory_type: Annotated[MemoryType, Parameter(name="type", help="Memory type directory to write into.")],
    title: Annotated[str, Parameter(help="Memory title. The key is generated from this title.")],
    content: Annotated[str, Parameter(help="Markdown body content to store under the title.")],
) -> None:
    """Create a project or global memory."""
    emit(
        add_memory(
            scope=scope,
            memory_type=memory_type,
            title=title,
            content=content,
            cwd=Path.cwd(),
        )
    )


def update_command(
    key: Annotated[str, Parameter(help="Memory key to update.")],
    *,
    title: Annotated[str | None, Parameter(help="Replacement title.")] = None,
    memory_type: Annotated[MemoryType | None, Parameter(name="type", help="Replacement memory type.")] = None,
    content: Annotated[str | None, Parameter(help="Replacement Markdown body content.")] = None,
) -> None:
    """Update a memory title, type, or body."""
    emit(
        update_memory(
            key=key,
            title=title,
            memory_type=memory_type,
            content=content,
            cwd=Path.cwd(),
        )
    )


def delete_command(
    key: Annotated[str, Parameter(help="Memory key to delete.")],
) -> None:
    """Delete a memory and clean its index entry."""
    emit(delete_memory(key=key, cwd=Path.cwd()))


def search_default(
    query: Annotated[str, Parameter(help="Query text.")],
    *,
    scope: Annotated[SearchScope, Parameter(help="Scope to search: project, global, or both. Defaults to both.")] = SearchScope.BOTH,
) -> None:
    """Return a curated report combining key, exact content, fuzzy, and ranked search."""
    emit(search_memories(scope=scope, query=query, cwd=Path.cwd()))


def search_content_command(
    query: Annotated[str, Parameter(help="Content query text.")],
    *,
    scope: Annotated[SearchScope, Parameter(help="Scope to search: project, global, or both. Defaults to both.")] = SearchScope.BOTH,
    mode: Annotated[
        ContentSearchMode,
        Parameter(help="Content search mode: exact, fuzzy, or ranked."),
    ],
) -> None:
    """Search memory body text with the selected content mode."""
    if mode is ContentSearchMode.EXACT:
        emit(search_content_exact(scope=scope, query=query, cwd=Path.cwd()))
        return
    if mode is ContentSearchMode.FUZZY:
        emit(search_content_fuzzy(scope=scope, query=query, cwd=Path.cwd()))
        return
    assert mode is ContentSearchMode.RANKED, f"unsupported content search mode: {mode}"
    emit(search_content_ranked(scope=scope, query=query, cwd=Path.cwd()))


def search_metadata_command(
    *,
    scope: Annotated[SearchScope, Parameter(help="Scope to search: project, global, or both. Defaults to both.")] = SearchScope.BOTH,
    memory_type: Annotated[MemoryType | None, Parameter(name="type", help="Filter by memory type.")] = None,
    tag: Annotated[str | None, Parameter(help="Filter by tag.")] = None,
    created_after: Annotated[
        str | None,
        Parameter(help="Filter by ISO timestamp, for example 2026-06-13T00:00:00+00:00."),
    ] = None,
) -> None:
    """Search memory frontmatter fields."""
    emit(
        search_metadata(
            scope=scope,
            memory_type=memory_type,
            tag=tag,
            created_after=created_after,
            cwd=Path.cwd(),
        )
    )


def search_keys_command(
    query: Annotated[str, Parameter(help="Query text for memory keys and titles.")],
    *,
    scope: Annotated[SearchScope, Parameter(help="Scope to search: project, global, or both. Defaults to both.")] = SearchScope.BOTH,
) -> None:
    """Search memory keys and titles."""
    emit(search_keys(scope=scope, query=query, cwd=Path.cwd()))


def inspect_overview_command(
    *,
    scope: Annotated[SearchScope, Parameter(help="Scope to inspect: project, global, or both.")],
    output_format: Annotated[InspectOutputFormat, Parameter(name="format", help="Output format: json.")],
) -> None:
    """Summarize scoped vault roots, notes, indexes, and memory categories."""
    emit(inspect_overview(scope=scope, output_format=output_format, cwd=Path.cwd()))


def inspect_schema_command(
    *,
    output_format: Annotated[InspectOutputFormat, Parameter(name="format", help="Output format: json.")],
) -> None:
    """Print the user-facing command and metadata schema."""
    emit(inspect_schema(output_format=output_format))


def inspect_paths_command(
    *,
    scope: Annotated[SearchScope, Parameter(help="Scope to inspect: project, global, or both.")],
    kind: Annotated[InspectPathKind, Parameter(help="Path class: roots, indexes, notes, or all.")],
    output_format: Annotated[InspectOutputFormat, Parameter(name="format", help="Output format: json.")],
) -> None:
    """List vault paths for roots, indexes, notes, or all scoped Markdown files."""
    emit(inspect_paths(scope=scope, kind=kind, output_format=output_format, cwd=Path.cwd()))


def inspect_tree_command(
    *,
    scope: Annotated[SearchScope, Parameter(help="Scope to inspect: project, global, or both.")],
    depth: Annotated[
        int,
        Parameter(help="Number of Markdown-link levels to traverse from each scoped root."),
    ],
    output_format: Annotated[InspectOutputFormat, Parameter(name="format", help="Output format: json.")],
) -> None:
    """Traverse the memory graph from the scoped root indexes."""
    emit(inspect_tree(scope=scope, depth=depth, output_format=output_format, cwd=Path.cwd()))


def inspect_links_command(
    key: Annotated[str, Parameter(help="Memory key to inspect.")],
    *,
    direction: Annotated[
        InspectLinkDirection,
        Parameter(help="Link direction: children, parents, or both."),
    ],
    depth: Annotated[int, Parameter(help="Number of graph levels to traverse.")],
    output_format: Annotated[InspectOutputFormat, Parameter(name="format", help="Output format: json.")],
) -> None:
    """Show graph neighbors for a memory key."""
    emit(
        inspect_links(
            key=key,
            direction=direction,
            depth=depth,
            output_format=output_format,
            cwd=Path.cwd(),
        )
    )


def inspect_outline_command(
    key: Annotated[str, Parameter(help="Memory key to outline.")],
    *,
    output_format: Annotated[InspectOutputFormat, Parameter(name="format", help="Output format: json.")],
) -> None:
    """Extract Markdown headings for a memory key."""
    emit(inspect_outline(key=key, output_format=output_format, cwd=Path.cwd()))


def inspect_stats_command(
    *,
    scope: Annotated[SearchScope, Parameter(help="Scope to inspect: project, global, or both.")],
    group: Annotated[InspectStatsGroup, Parameter(name="by", help="Grouping: type, scope, or day.")],
    output_format: Annotated[InspectOutputFormat, Parameter(name="format", help="Output format: json.")],
) -> None:
    """Count memories by type, scope, or day."""
    emit(inspect_stats(scope=scope, group=group, output_format=output_format, cwd=Path.cwd()))


def inspect_recent_command(
    *,
    scope: Annotated[SearchScope, Parameter(help="Scope to inspect: project, global, or both.")],
    since: Annotated[
        str,
        Parameter(help="ISO timestamp lower bound, for example 2026-06-13T00:00:00+00:00."),
    ],
    output_format: Annotated[InspectOutputFormat, Parameter(name="format", help="Output format: json.")],
) -> None:
    """List memories created after a timestamp."""
    emit(inspect_recent(scope=scope, since=since, output_format=output_format, cwd=Path.cwd()))


def inspect_export_command(
    *,
    scope: Annotated[SearchScope, Parameter(help="Scope to export: project, global, or both.")],
    profile: Annotated[
        InspectExportProfile,
        Parameter(help="Export profile: map, context, or archive."),
    ],
    output_format: Annotated[InspectExportFormat, Parameter(name="format", help="Output format: graph-json.")],
) -> None:
    """Export the scoped memory graph for external analysis."""
    emit(inspect_export(scope=scope, profile=profile, output_format=output_format, cwd=Path.cwd()))


def retrieve_command(
    key: Annotated[str, Parameter(help="Full vault-relative key to retrieve (memory or plan card).")],
) -> None:
    """Retrieve one vault note by full key."""
    print(retrieve_memory(key=key, cwd=Path.cwd()), end="")


def maintain_squash_command(
    key: Annotated[str, Parameter(help="Root memory key to squash.")],
    *,
    depth: Annotated[int, Parameter(help="Graph depth to include.")],
) -> None:
    """Consolidate a memory graph into rendered text."""
    print(squash_memory(key=key, depth=depth, cwd=Path.cwd()), end="")


def maintain_move_command(
    key: Annotated[str, Parameter(help="Memory key to move.")],
    *,
    destination: Annotated[str, Parameter(name="to", help="Destination scope path, such as global/traps.")],
) -> None:
    """Move a memory into a maintenance destination."""
    emit(move_memory(key=key, destination=destination, cwd=Path.cwd()))


def maintain_split_command(
    key: Annotated[str, Parameter(help="Memory key containing the section.")],
    *,
    section: Annotated[str, Parameter(help="Markdown section title to extract.")],
) -> None:
    """Extract a section into a separate memory."""
    emit(split_memory(key=key, section=section, cwd=Path.cwd()))


def maintain_merge_command(
    key: Annotated[str, Parameter(help="Memory key receiving the referenced content.")],
    *,
    reference: Annotated[str, Parameter(help="Referenced memory key to inline.")],
) -> None:
    """Inline a referenced memory back into its parent."""
    emit(merge_memory(key=key, reference=reference, cwd=Path.cwd()))


def doctor_command() -> None:
    """Validate dependencies and the current repository memory setup."""
    emit(run_doctor(cwd=Path.cwd()))


def plan_add_command(
    type_name: Annotated[str, Parameter(name="type", help="Card type, e.g. feature, plan, phase, task.")],
    card_id: Annotated[str, Parameter(name="id", help="Card id, must start with the type's prefix (e.g. TASK-...).")],
    *,
    parent: Annotated[str | None, Parameter(help="Parent card id for non-root cards.")] = None,
    set_: Annotated[
        list[str] | None,
        Parameter(
            name="set",
            help="Field assignment key=value; repeat for list fields.",
            negative_iterable=[],
            allow_leading_hyphen=True,
        ),
    ] = None,
    empty_set: Annotated[list[str] | None, Parameter(name="empty-set", help="Fields to initialize as empty lists.")] = None,
    body: Annotated[str | None, Parameter(help="Markdown body for the card.")] = None,
    body_file: Annotated[Path | None, Parameter(name="body-file", help="Path to a file containing markdown body for the card.")] = None,
) -> None:
    """Add a plan card to the project vault."""
    if body is not None and body_file is not None:
        raise CliUsageError("Cannot specify both --body and --body-file")
    if body_file is not None:
        body = body_file.read_text(encoding="utf-8")
    emit(
        add_plan_card(
            type_name=type_name,
            card_id=card_id,
            parent_id=parent,
            assignments=set_ or [],
            empty_set=empty_set,
            body=body if body is not None else f"# {card_id}\n",
            cwd=Path.cwd(),
        )
    )


def plan_update_command(
    card_id: Annotated[str, Parameter(name="id", help="Card id to update.")],
    *,
    set_: Annotated[
        list[str] | None,
        Parameter(
            name="set",
            help="Field assignment key=value; repeat for list fields.",
            negative_iterable=[],
            allow_leading_hyphen=True,
        ),
    ] = None,
) -> None:
    """Update fields on an existing plan card."""
    emit(update_plan_card(card_id=card_id, assignments=set_ or [], cwd=Path.cwd()))


def plan_delete_command(card_id: Annotated[str, Parameter(name="id", help="Card id to delete.")]) -> None:
    """Delete a plan card."""
    emit(delete_plan_card(card_id=card_id, cwd=Path.cwd()))


def plan_validate_command() -> None:
    """Validate the plan card graph across the whole vault (references, containment, DAG cycles)."""
    emit(validate_plan_cards(cwd=Path.cwd()))


def plan_dag_command() -> None:
    """Render the plan dependency and containment DAG to plan-dag.md."""
    emit(write_plan_dag(cwd=Path.cwd()))


def plan_migrate_command(
    source: Annotated[Path, Parameter(name="from", help="In-repo plans directory to ingest, e.g. .agents/plans.")],
) -> None:
    """Migrate an in-repo card tree into the project vault."""
    emit(migrate_plan_cards(source=source.expanduser(), cwd=Path.cwd()))


def register_commands() -> None:
    maintain_app.command(maintain_init_global, name="init-global")
    init_app.command(init_project_command, name="project")
    app.command(add_command, name="add")
    app.command(update_command, name="update")
    app.command(delete_command, name="delete")
    search_app.default(search_default)
    search_app.command(search_content_command, name="content")
    search_app.command(search_metadata_command, name="metadata")
    search_app.command(search_keys_command, name="keys")
    inspect_commands: dict[str, Callable[..., None]] = {
        "overview": inspect_overview_command,
        "schema": inspect_schema_command,
        "paths": inspect_paths_command,
        "tree": inspect_tree_command,
        "links": inspect_links_command,
        "outline": inspect_outline_command,
        "stats": inspect_stats_command,
        "recent": inspect_recent_command,
        "export": inspect_export_command,
    }
    assert tuple(inspect_commands) == INSPECT_COMMAND_NAMES, "inspect command registry must match the canonical schema order"
    for name in INSPECT_COMMAND_NAMES:
        inspect_app.command(inspect_commands[name], name=name)
    app.command(retrieve_command, name="retrieve")
    maintain_app.command(maintain_squash_command, name="squash")
    maintain_app.command(maintain_move_command, name="move")
    maintain_app.command(maintain_split_command, name="split")
    maintain_app.command(maintain_merge_command, name="merge")
    plan_app.command(plan_add_command, name="add")
    plan_app.command(plan_update_command, name="update")
    plan_app.command(plan_delete_command, name="delete")
    plan_app.command(plan_validate_command, name="validate")
    plan_app.command(plan_dag_command, name="dag")
    plan_app.command(plan_migrate_command, name="migrate")
    app.command(doctor_command, name="doctor")


def field_help(config: CardSystemConfig, card_type_name: str, field_name: str, field_type: str) -> str:
    if field_type == "status":
        card_type = next(ct for ct in config.card_types if ct.name == card_type_name)
        options = config.status_sets[card_type.status_set].options
        return f"{field_name} ({field_type}; allowed: {', '.join(options)})"
    return f"{field_name} ({field_type})"


def plan_add_help_text(config: CardSystemConfig) -> str:
    doc = [
        "Add a plan card to the project vault.",
        "",
        "Allowed card types and id prefixes:",
    ]
    for card_type in config.card_types:
        doc.append(f"  - {card_type.name} (prefix: {card_type.id_prefix}-)")
    doc.append("")
    doc.append("Required fields per card type:")
    for card_type in config.card_types:
        required_fields = [field_help(config, card_type.name, field.name, field.type) for field in card_type.fields if field.required]
        doc.append(f"  - {card_type.name}: {', '.join(required_fields)}")
    return "\n".join(doc)


plan_add_command.__doc__ = plan_add_help_text(load_card_system_config())
register_commands()


def emit(payload: Mapping[str, JsonValue]) -> None:
    print(json.dumps(payload, sort_keys=True))


def add_command_scope_hint(arguments: list[str]) -> str | None:
    if arguments and arguments[0] == "add" and "--type" in arguments and "--scope" not in arguments:
        return "Unknown option: --type. Did you mean --scope?"
    return None


def missing_argument_message(error: cyclopts.exceptions.MissingArgumentError, arguments: list[str]) -> str:
    message = str(error)
    if len(arguments) >= 2 and arguments[:2] == ["search", "content"] and "--mode" not in arguments:
        return f"{message} Missing required option: --mode (exact, fuzzy, or ranked)."
    return message


def main() -> None:
    scope_hint = add_command_scope_hint(sys.argv[1:])
    if scope_hint is not None:
        print(f"Error: {scope_hint}", file=sys.stderr)
        raise SystemExit(1)

    try:
        basic_doctor(Path.cwd())
        app(sys.argv[1:], print_error=False, exit_on_error=False)
    except cyclopts.exceptions.MissingArgumentError as e:
        print(f"Error: {missing_argument_message(e, sys.argv[1:])}", file=sys.stderr)
        raise SystemExit(1)
    except cyclopts.exceptions.CycloptsError as e:
        print(f"Error: {e}", file=sys.stderr)
        raise SystemExit(1)
    except ValidationError as e:
        msgs = []
        for err in e.errors():
            loc = ".".join(str(l) for l in err["loc"])
            msgs.append(f"Field '{loc}': {err['msg']} (input: {err['input']})")
        print("Error: Validation failed:\n" + "\n".join(msgs), file=sys.stderr)
        raise SystemExit(1)
    except (
        CardPlacementError,
        CardFieldError,
        CliUsageError,
        MalformedMemoryError,
        MemoryOperationError,
        VaultCommitError,
        ProjectNotInitializedError,
        GlobalVaultNotInitializedError,
        DependencyError,
    ) as e:
        print(f"Error: {e}", file=sys.stderr)
        raise SystemExit(1)
