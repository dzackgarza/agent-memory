from __future__ import annotations

import json
import shutil
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import cyclopts
from cyclopts import App, Parameter
from pydantic import ValidationError

from agent_memory.cards.config import CardSystemConfig, CardTypeSpec
from agent_memory.cards.loader import CardConfigError
from agent_memory.cards.storage import CardLookupError, CardPlacementError
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
    BUNDLED_SKILL_NAMES,
    INSPECT_COMMAND_NAMES,
    QUEUE_CARD_TYPE,
    CardFieldError,
    DependencyError,
    GlobalVaultNotInitializedError,
    JsonValue,
    MalformedMemoryError,
    MemoryOperationError,
    ProjectNotInitializedError,
    VaultCommitError,
    add_card,
    add_memory,
    add_queue_item,
    basic_doctor,
    bundled_skill_text,
    config_for_schema_advertisement,
    delete_card_record,
    delete_memory,
    delete_memory_orphaning_backlinks,
    delete_memory_repointing_backlinks,
    disable_sync_systemd_timer,
    enable_sync_systemd_timer,
    init_global_vault,
    init_project,
    inspect_broken_links,
    inspect_export,
    inspect_links,
    inspect_outline,
    inspect_overview,
    inspect_paths,
    inspect_recent,
    inspect_schema,
    inspect_stats,
    inspect_tree,
    install_sync_systemd_timer,
    list_cards,
    list_cards_with_unmigrated,
    list_queue_items,
    load_card_system,
    merge_memory,
    migrate_cards,
    move_memory,
    plan_progress,
    remove_sync_systemd_timer,
    retrieve_memory,
    rewrite_wikilinks,
    search_content_exact,
    search_content_fuzzy,
    search_content_ranked,
    search_keys,
    search_memories,
    search_metadata,
    show_card,
    split_memory,
    squash_memory,
    sync_status,
    sync_vault,
    update_card_record,
    update_memory,
    update_plan_todo,
    validate_card_records,
    write_card_dag,
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
card_app = app.command(App(name="card", help="Operate on schema-defined vault-backed cards."))
sync_app = app.command(App(name="sync", help="Synchronize the configured memory vault with its git remote."))
links_app = app.command(App(name="links", help="Inspect and rewrite vault links."))
todo_app = app.command(App(name="todo", help="Mutate structured todos on vault plan records."))
queue_app = app.command(App(name="queue", help="Append and list global agent work queue items."))


class CliUsageError(RuntimeError):
    """Raised when arguments are coherent CLI syntax but invalid together."""


@dataclass(frozen=True)
class CardConfigAvailable:
    config: CardSystemConfig


@dataclass(frozen=True)
class CardConfigUnavailable:
    error: CardConfigError


type CardConfigRegistrationState = CardConfigAvailable | CardConfigUnavailable


def maintain_init_global(
    vault: Annotated[Path, Parameter(help="Path to the global memory vault to initialize.")],
) -> None:
    """Create the global IWE-backed memory vault once."""
    emit(init_global_vault(vault))


def maintain_skill_command(
    name: Annotated[
        str,
        Parameter(help=f"Bundled maintenance skill name. Available: {', '.join(BUNDLED_SKILL_NAMES)}."),
    ],
) -> None:
    """Print a bundled maintenance skill entrypoint."""
    print(bundled_skill_text(name), end="")


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
    memory_type: Annotated[MemoryType, Parameter(name="type", help="Memory type directory to write into. Plain plan memories are rejected; use agent-memory plan add.")],
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
    *,
    repoint: Annotated[
        str | None,
        Parameter(help="Rewrite inbound wikilinks to this key or external URL before deleting."),
    ] = None,
    orphan_ok: Annotated[
        bool,
        Parameter(
            name="orphan-ok",
            help="Allow deletion while leaving inbound wikilinks pointing at the deleted key.",
        ),
    ] = False,
) -> None:
    """Delete a memory and clean its index entry."""
    if repoint is not None and orphan_ok:
        raise MemoryOperationError("delete accepts --repoint or --orphan-ok, not both")
    if repoint is not None:
        emit(delete_memory_repointing_backlinks(key=key, repoint=repoint, cwd=Path.cwd()))
        return
    if orphan_ok:
        emit(delete_memory_orphaning_backlinks(key=key, cwd=Path.cwd()))
        return
    emit(delete_memory(key=key, cwd=Path.cwd()))


def todo_set_command(
    key: Annotated[str, Parameter(help="Full vault-relative plan memory key.")],
    todo_id: Annotated[str, Parameter(help="Todo id to mutate inside the plan record's todos tree.")],
    *,
    status: Annotated[str | None, Parameter(help="Replacement todo status.")] = None,
    content: Annotated[str | None, Parameter(help="Replacement todo content.")] = None,
    note: Annotated[str | None, Parameter(help="Replacement todo note.")] = None,
) -> None:
    """Update one todo node in a plan memory record."""
    emit(update_plan_todo(key=key, todo_id=todo_id, status=status, content=content, note=note, cwd=Path.cwd()))


def queue_add_command(
    *,
    project: Annotated[str | None, Parameter(help="Project id the queue item belongs to.")] = None,
    agent: Annotated[str | None, Parameter(help="Agent or user handing off the work.")] = None,
    status: Annotated[str | None, Parameter(help="Queue status from the vault card schema.")] = None,
    summary: Annotated[str | None, Parameter(help="Queue item summary.")] = None,
    timestamp: Annotated[str | None, Parameter(help="Optional timestamp string.")] = None,
    link: Annotated[
        list[str] | None,
        Parameter(help="Related wikilink; repeat for multiple links.", negative_iterable=[], allow_leading_hyphen=True),
    ] = None,
    set_: Annotated[
        list[str] | None,
        Parameter(
            name="set",
            help="Additional schema field assignment key=value.",
            negative_iterable=[],
            allow_leading_hyphen=True,
        ),
    ] = None,
) -> None:
    """Add a global queue item using the vault queue-item schema."""
    emit(
        add_queue_item(
            project=project,
            agent=agent,
            status=status,
            summary=summary,
            timestamp=timestamp,
            links=link or [],
            extra_assignments=set_ or [],
            cwd=Path.cwd(),
        )
    )


def queue_list_command() -> None:
    """List global queue items from the configured vault."""
    emit(list_queue_items(cwd=Path.cwd()))


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
    emit(inspect_schema(output_format=output_format, cwd=Path.cwd()))


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
    key: Annotated[str | None, Parameter(help="Memory key to inspect. Omit only with --broken.")] = None,
    *,
    broken: Annotated[bool, Parameter(help="Report broken wikilinks across the selected scope.")] = False,
    scope: Annotated[SearchScope, Parameter(help="Scope for --broken: project, global, or both.")] = SearchScope.BOTH,
    direction: Annotated[
        InspectLinkDirection,
        Parameter(help="Link direction: children, parents, or both."),
    ] = InspectLinkDirection.BOTH,
    depth: Annotated[int, Parameter(help="Number of graph levels to traverse.")] = 1,
    output_format: Annotated[InspectOutputFormat, Parameter(name="format", help="Output format: json.")] = InspectOutputFormat.JSON,
) -> int | None:
    """Show graph neighbors for a memory key."""
    if broken:
        assert key is None, "inspect links --broken is vault-scoped and does not accept a memory key"
        payload = inspect_broken_links(scope=scope, output_format=output_format, cwd=Path.cwd())
        emit(payload)
        broken_links = payload["broken_links"]
        assert isinstance(broken_links, list), "broken link report must contain a list"
        return 1 if broken_links else 0
    assert key is not None, "inspect links requires a memory key unless --broken is set"
    emit(
        inspect_links(
            key=key,
            direction=direction,
            depth=depth,
            output_format=output_format,
            cwd=Path.cwd(),
        )
    )
    return None


def links_rewrite_command(
    from_target: Annotated[str | None, Parameter(name="from", help="Existing wikilink target key.")] = None,
    to_target: Annotated[
        str | None,
        Parameter(name="to", help="New wikilink target key or external URL."),
    ] = None,
    map_path: Annotated[
        Path | None,
        Parameter(name="map", help="TOML mapping file with a [rewrites] table."),
    ] = None,
) -> None:
    """Rewrite wikilink targets across the vault."""
    emit(
        rewrite_wikilinks(
            from_target=from_target,
            to_target=to_target,
            map_path=map_path,
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


def list_command(
    *,
    type_: Annotated[str, Parameter(name="type", help="Card or memory type to list, e.g. plan, decision, feature, task.")],
    scope: Annotated[SearchScope, Parameter(help="Scope to list: project, global, or both.")] = SearchScope.BOTH,
    unmigrated: Annotated[bool, Parameter(help="Include records stranded outside managed global/project folders.")] = False,
) -> None:
    """List managed cards/memories and optionally stranded harness-local records."""
    if unmigrated:
        emit(list_cards_with_unmigrated(card_type=type_, scope=scope, cwd=Path.cwd()))
        return
    emit(list_cards(card_type=type_, scope=scope, cwd=Path.cwd()))


def plan_progress_command(
    *,
    scope: Annotated[SearchScope, Parameter(help="Scope to report: project, global, or both.")] = SearchScope.BOTH,
) -> None:
    """Summarize plan todo progress across the scoped vault."""
    emit(plan_progress(scope=scope, cwd=Path.cwd()))


def resolve_card_body(card_id: str, body: str | None, body_file: Path | None) -> str:
    if body is not None and body_file is not None:
        raise CliUsageError("Cannot specify both --body and --body-file")
    if body_file is not None:
        try:
            return body_file.read_text(encoding="utf-8")
        except OSError as e:
            raise CliUsageError(f"Cannot read --body-file {body_file}: {e.strerror}") from e
    return body if body is not None else f"# {card_id}\n"


def card_add_command(
    type_name: Annotated[str, Parameter(name="type", help="Card type from the active card schema.")],
    card_id: Annotated[str, Parameter(name="id", help="Card id; prefix must match the declared card type.")],
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
    """Add a schema-defined card to the project vault."""
    emit(
        add_card(
            type_name=type_name,
            card_id=card_id,
            parent_id=parent,
            assignments=set_ or [],
            empty_set=empty_set,
            body=resolve_card_body(card_id, body, body_file),
            cwd=Path.cwd(),
        )
    )


def card_update_command(
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
    """Update fields on an existing schema-defined card."""
    emit(update_card_record(card_id=card_id, assignments=set_ or [], cwd=Path.cwd()))


def card_delete_command(card_id: Annotated[str, Parameter(name="id", help="Card id to delete.")]) -> None:
    """Delete a schema-defined card."""
    emit(delete_card_record(card_id=card_id, cwd=Path.cwd()))


def card_show_command(card_id: Annotated[str, Parameter(name="id", help="Card id to show.")]) -> None:
    """Show one schema-defined card."""
    emit(show_card(card_id=card_id, cwd=Path.cwd()))


def card_validate_command() -> None:
    """Validate the card graph across the whole vault."""
    emit(validate_card_records(cwd=Path.cwd()))


def card_dag_command() -> None:
    """Render the dependency and containment DAG to plan-dag.md."""
    emit(write_card_dag(cwd=Path.cwd()))


def card_migrate_command(
    source: Annotated[Path, Parameter(name="from", help="In-repo plans directory to ingest, e.g. .agents/plans.")],
) -> None:
    """Migrate an in-repo card tree into the project vault."""
    emit(migrate_cards(source=source.expanduser(), cwd=Path.cwd()))


def generated_card_add_command(card_type: CardTypeSpec) -> Callable[..., None]:
    def add_for_type(
        card_id: Annotated[str, Parameter(name="id", help="Card id; prefix must match this generated card command.")],
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
        emit(
            add_card(
                type_name=card_type.name,
                card_id=card_id,
                parent_id=parent,
                assignments=set_ or [],
                empty_set=empty_set,
                body=resolve_card_body(card_id, body, body_file),
                cwd=Path.cwd(),
            )
        )

    add_for_type.__name__ = f"{card_type.name}_add_command"
    return add_for_type


def generated_card_update_command(card_type: CardTypeSpec) -> Callable[..., None]:
    def update_for_type(
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
        emit(update_card_record(card_id=card_id, assignments=set_ or [], cwd=Path.cwd()))

    update_for_type.__name__ = f"{card_type.name}_update_command"
    return update_for_type


def generated_card_delete_command(card_type: CardTypeSpec) -> Callable[..., None]:
    def delete_for_type(card_id: Annotated[str, Parameter(name="id", help="Card id to delete.")]) -> None:
        emit(delete_card_record(card_id=card_id, cwd=Path.cwd()))

    delete_for_type.__name__ = f"{card_type.name}_delete_command"
    return delete_for_type


def generated_card_show_command(card_type: CardTypeSpec) -> Callable[..., None]:
    def show_for_type(card_id: Annotated[str, Parameter(name="id", help="Card id to show.")]) -> None:
        emit(show_card(card_id=card_id, cwd=Path.cwd()))

    show_for_type.__name__ = f"{card_type.name}_show_command"
    return show_for_type


def sync_run_command() -> None:
    """Commit current vault changes, rebase from origin, and push the vault branch."""
    emit(sync_vault(cwd=Path.cwd()))


def sync_status_command() -> None:
    """Report the configured vault's current git synchronization state."""
    emit(sync_status(cwd=Path.cwd()))


def sync_install_command(
    interval_seconds: Annotated[int, Parameter(name="seconds", help="Timer interval in positive seconds.")],
) -> None:
    """Install user systemd service and timer files for vault synchronization."""
    emit(install_sync_systemd_timer(cwd=Path.cwd(), interval_seconds=interval_seconds))


def sync_enable_command() -> None:
    """Enable the installed user systemd timer for vault synchronization."""
    emit(enable_sync_systemd_timer(cwd=Path.cwd()))


def sync_disable_command() -> None:
    """Disable the user systemd timer for vault synchronization."""
    emit(disable_sync_systemd_timer(cwd=Path.cwd()))


def sync_remove_command() -> None:
    """Remove the user systemd service and timer files for vault synchronization."""
    emit(remove_sync_systemd_timer(cwd=Path.cwd()))


ROOT_COMMAND_NAMES = {
    "init",
    "search",
    "inspect",
    "maintain",
    "card",
    "sync",
    "links",
    "todo",
    "queue",
    "add",
    "update",
    "delete",
    "list",
    "retrieve",
    "doctor",
}


def active_card_config() -> CardSystemConfig:
    if shutil.which("git") is None:
        cards_config, _models = load_card_system(None)
        return cards_config
    config = config_for_schema_advertisement(Path.cwd())
    cards_config, _models = load_card_system(config)
    return cards_config


def register_commands(registration_state: CardConfigRegistrationState) -> None:
    maintain_app.command(maintain_init_global, name="init-global")
    maintain_app.command(maintain_skill_command, name="skill")
    init_app.command(init_project_command, name="project")
    app.command(add_command, name="add")
    app.command(update_command, name="update")
    app.command(delete_command, name="delete")
    app.command(list_command, name="list")
    todo_app.command(todo_set_command, name="set")
    queue_app.command(queue_add_command, name="add")
    queue_app.command(queue_list_command, name="list")
    search_app.default(search_default)
    search_app.command(search_content_command, name="content")
    search_app.command(search_metadata_command, name="metadata")
    search_app.command(search_keys_command, name="keys")
    inspect_commands: dict[str, Callable[..., object]] = {
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
    card_app.command(card_add_command, name="add")
    card_app.command(card_update_command, name="update")
    card_app.command(card_delete_command, name="delete")
    card_app.command(card_show_command, name="show")
    card_app.command(card_validate_command, name="validate")
    card_app.command(card_dag_command, name="dag")
    card_app.command(card_migrate_command, name="migrate")
    if isinstance(registration_state, CardConfigAvailable):
        register_generated_card_type_commands(registration_state.config)
    links_app.command(links_rewrite_command, name="rewrite")
    sync_app.command(sync_run_command, name="run")
    sync_app.command(sync_status_command, name="status")
    sync_app.command(sync_install_command, name="install")
    sync_app.command(sync_enable_command, name="enable")
    sync_app.command(sync_disable_command, name="disable")
    sync_app.command(sync_remove_command, name="remove")
    app.command(doctor_command, name="doctor")


def register_generated_card_type_commands(config: CardSystemConfig) -> None:
    for card_type in config.card_types:
        if card_type.name == QUEUE_CARD_TYPE:
            continue
        assert card_type.name not in ROOT_COMMAND_NAMES, f"card type collides with root CLI command: {card_type.name}"
        type_app = app.command(App(name=card_type.name, help=f"{card_type.name} cards from the active card schema."))
        add_command_for_type = generated_card_add_command(card_type)
        add_command_for_type.__doc__ = card_type_add_help_text(config, card_type)
        type_app.command(add_command_for_type, name="add")
        type_app.command(generated_card_update_command(card_type), name="update")
        type_app.command(generated_card_delete_command(card_type), name="delete")
        type_app.command(generated_card_show_command(card_type), name="show")
        if card_type.name == "plan":
            type_app.command(card_validate_command, name="validate")
            type_app.command(card_dag_command, name="dag")
            type_app.command(card_migrate_command, name="migrate")
            type_app.command(plan_progress_command, name="progress")


def field_help(config: CardSystemConfig, card_type_name: str, field_name: str, field_type: str) -> str:
    if field_type == "status":
        card_type = next(ct for ct in config.card_types if ct.name == card_type_name)
        options = config.status_sets[card_type.status_set].options
        return f"{field_name} ({field_type}; allowed: {', '.join(options)})"
    return f"{field_name} ({field_type})"


def card_add_help_text(config: CardSystemConfig) -> str:
    doc = [
        "Add a schema-defined card to the project vault.",
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


def card_type_add_help_text(config: CardSystemConfig, card_type: CardTypeSpec) -> str:
    doc = [
        f"Add a {card_type.name} card to the project vault.",
        "",
        f"ID prefix: {card_type.id_prefix}-",
        f"Container: {card_type.container or '<parent>'}",
        "",
        "Required --set fields:",
    ]
    required_fields = [field_help(config, card_type.name, field.name, field.type) for field in card_type.fields if field.required and field.name != "id"]
    doc.append(f"  {', '.join(required_fields)}")
    return "\n".join(doc)


try:
    active_config = active_card_config()
except CardConfigError as error:
    CARD_CONFIG_REGISTRATION_STATE: CardConfigRegistrationState = CardConfigUnavailable(error)
else:
    CARD_CONFIG_REGISTRATION_STATE = CardConfigAvailable(active_config)
    card_add_command.__doc__ = card_add_help_text(active_config)

register_commands(CARD_CONFIG_REGISTRATION_STATE)


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


def command_requires_card_schema(arguments: list[str]) -> bool:
    if not arguments or arguments[0].startswith("-"):
        return False
    return arguments[0] == "card" or arguments[0] not in ROOT_COMMAND_NAMES


def main() -> None:
    scope_hint = add_command_scope_hint(sys.argv[1:])
    if scope_hint is not None:
        print(f"Error: {scope_hint}", file=sys.stderr)
        raise SystemExit(1)
    if isinstance(CARD_CONFIG_REGISTRATION_STATE, CardConfigUnavailable) and command_requires_card_schema(sys.argv[1:]):
        print(f"Error: {CARD_CONFIG_REGISTRATION_STATE.error}", file=sys.stderr)
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
        CardConfigError,
        CardLookupError,
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
