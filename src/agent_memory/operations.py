from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from importlib import resources
from pathlib import Path

import frontmatter
import tomli_w
import yaml
from markdown_it import MarkdownIt
from pydantic import BaseModel

from agent_memory import iwe
from agent_memory.cards.config import CardSystemConfig
from agent_memory.cards.dag import PLAN_DAG_FILENAME, render_dag
from agent_memory.cards.factory import build_card_models
from agent_memory.cards.loader import load_card_system_config
from agent_memory.cards.migration import migrate_plans
from agent_memory.cards.storage import card_type_for_id, create_card, find_card_path, update_card
from agent_memory.cards.validation import load_card_records, validate_cards
from agent_memory.models import (
    GlobalNoteMetadata,
    InspectExportFormat,
    InspectExportProfile,
    InspectLinkDirection,
    InspectOutputFormat,
    InspectPathKind,
    InspectStatsGroup,
    MemoryScope,
    MemoryType,
    MetadataValue,
    ProjectConfig,
    ProjectNoteMetadata,
    PromotedNoteMetadata,
    SearchScope,
)
from slugify import slugify

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
JsonObject = dict[str, JsonValue]
IndexEntry = tuple[str, str, str]
ProjectRecord = dict[str, str]


@dataclass(frozen=True)
class DependencyCheck:
    name: str
    command: tuple[str, ...]
    install_instructions: str


@dataclass(frozen=True)
class SyncSystemdPaths:
    unit_dir: Path
    service: Path
    timer: Path
    timer_wants: Path


@dataclass(frozen=True)
class WikilinkRewrite:
    from_key: str
    to_target: str
    replacement: str
    from_fragment: str | None = None


OKF_VERSION = "0.1"
MARKDOWN_PARSER = MarkdownIt("commonmark")
WIKILINK_PATTERN = re.compile(r"\[\[([^\]]+)\]\]")
AGENTS_SECTION_START = "<!-- agent-memory:start -->"
AGENTS_SECTION_END = "<!-- agent-memory:end -->"
ZK_NOTEBOOK_DB_IGNORE = ".zk/notebook.db"
VAULT_GIT_USER_NAME = "agent-memory"
VAULT_GIT_USER_EMAIL = "agent-memory@localhost"
ROOT_INDEX_ENTRIES: tuple[IndexEntry, ...] = (("Global", "global/index.md", "Global memory shared across projects."),)
PROJECT_AGENT_STATE_DIRECTORIES: tuple[str, ...] = (".agents", ".hermes")
BUNDLED_SKILL_NAMES: tuple[str, ...] = ("vault-maintenance",)
VAULT_MAINTENANCE_SKILL_COMMAND = "agent-memory maintain skill vault-maintenance"
VAULT_MAINTENANCE_SKILL_HINT = (
    "\nVault recovery is owned by the bundled vault-maintenance skill. "
    f"Run `{VAULT_MAINTENANCE_SKILL_COMMAND}` and follow its referenced workflows "
    "before retrying normal memory work."
)
SYNC_SYSTEMD_SERVICE_NAME = "agent-memory-sync.service"
SYNC_SYSTEMD_TIMER_NAME = "agent-memory-sync.timer"
SYNC_STATE_FILENAME = "sync-state.json"

MEMORY_TYPE_DIRECTORIES: dict[MemoryType, str] = {
    MemoryType.DECISION: "decisions",
    MemoryType.TRAP: "traps",
    MemoryType.ADVICE: "advice",
    MemoryType.CONTEXT: "context",
    MemoryType.REFERENCE: "references",
    MemoryType.PLAN: "plans",
}

# The directory names for every memory type, in MemoryType enum order. This is the
# single source for both the global vault layout and the per-project layout.
MEMORY_TYPE_DIRECTORY_NAMES: tuple[str, ...] = tuple(MEMORY_TYPE_DIRECTORIES[memory_type] for memory_type in MemoryType)

# Canonical ordering of `agent-memory inspect` subcommands. The CLI layer drives command
# registration from this tuple and inspect_schema advertises it, so the command set has
# exactly one source of truth in the inner layer the CLI depends on.
INSPECT_COMMAND_NAMES: tuple[str, ...] = (
    "overview",
    "schema",
    "paths",
    "tree",
    "links",
    "outline",
    "stats",
    "recent",
    "export",
)


def index_descriptions(scope: MemoryScope) -> dict[str, str]:
    scope_word = scope.value.capitalize()
    return {MEMORY_TYPE_DIRECTORIES[memory_type]: f"{scope_word} {memory_type.value} memories." for memory_type in MemoryType}


VAULT_DIRECTORIES: tuple[Path, ...] = (
    *(Path("global") / name for name in MEMORY_TYPE_DIRECTORY_NAMES),
    Path("projects"),
    Path("inbox/unsorted"),
    Path("inbox/project"),
    Path("inbox/global"),
    Path("templates"),
    Path("_meta"),
)
BASIC_DEPENDENCIES: tuple[DependencyCheck, ...] = (
    DependencyCheck(
        "git",
        ("git", "--version"),
        "run `just setup` from the agent-memory checkout; manual install: install Git from your OS package manager.",
    ),
    DependencyCheck(
        "rg",
        ("rg", "--version"),
        "run `just setup` from the agent-memory checkout; manual install: run `cargo install ripgrep`.",
    ),
    DependencyCheck(
        "npx",
        ("npx", "--version"),
        "run `just setup` from the agent-memory checkout; manual install: install Node.js with npm/npx.",
    ),
    DependencyCheck(
        "@probelabs/probe",
        ("npx", "-y", "@probelabs/probe@latest", "--version"),
        "run `just setup` from the agent-memory checkout; manual install: run `npx -y @probelabs/probe@latest --version`.",
    ),
    DependencyCheck(
        "zk",
        ("zk", "--version"),
        "run `just setup` from the agent-memory checkout; manual install: install zk v0.15.5 to a directory on PATH.",
    ),
)


@dataclass(frozen=True)
class StarterConfig:
    default_vault: Path
    global_scopes: tuple[str, ...]
    search_max_results: int
    search_max_tokens: int


class ProjectNotInitializedError(RuntimeError):
    """Raised when a project command runs before project memory setup is done."""

    GUIDANCE = (
        "No project memory config found. Run `agent-memory maintain init-global --vault "
        "{default_vault}` once if the global vault does not exist, then run "
        "`agent-memory init project --vault <path-to-global-vault>` from this repository."
    )

    def __init__(self, default_vault: Path) -> None:
        super().__init__(self.GUIDANCE.format(default_vault=default_vault))


class GlobalVaultNotInitializedError(RuntimeError):
    """Raised when a global-scope operation runs but the global vault does not exist.

    Distinct from ProjectNotInitializedError: a global operation does not depend on the
    cwd repo being bound, so the remedy is `maintain init-global` only -- never
    `init project` in the unrelated current repository.
    """

    GUIDANCE = (
        "Global memory vault not found at {vault}. Run `agent-memory maintain init-global "
        "--vault {vault}` once to create it, or set AGENT_MEMORY_VAULT to an existing "
        "global vault."
    )

    def __init__(self, vault: Path) -> None:
        super().__init__(self.GUIDANCE.format(vault=vault))


class VaultCommitError(RuntimeError):
    """Raised when a git commit fails in the global or project memory vault."""


class CardFieldError(ValueError):
    """Raised when a plan card field assignment is malformed for CLI input."""


class MemoryOperationError(ValueError):
    """Raised when a memory operation is syntactically valid but incomplete."""


def bundled_skill_text(name: str) -> str:
    if name not in BUNDLED_SKILL_NAMES:
        names = ", ".join(BUNDLED_SKILL_NAMES)
        raise MemoryOperationError(f"unknown bundled skill {name!r}; available skills: {names}")
    skill_path = resources.files("agent_memory.defaults").joinpath("skills", name, "SKILL.md")
    return skill_path.read_text(encoding="utf-8")


def vault_commit_error_message(git_stderr: str) -> str:
    detail = git_stderr.strip()
    base = f"Vault commit failed: {detail}" if detail else "Vault commit failed"
    return f"{base}{VAULT_MAINTENANCE_SKILL_HINT}"


class MalformedMemoryError(ValueError):
    """Raised when a vault Markdown file is not a valid memory document."""

    def __init__(self, path: Path, detail: str) -> None:
        super().__init__(f"Malformed memory file {path}: {detail}")


class DependencyError(RuntimeError):
    """Raised when a required external dependency is missing or failing."""

    def __init__(
        self,
        name: str,
        command: tuple[str, ...],
        install_instructions: str,
        stdout: str | None,
        stderr: str | None,
    ) -> None:
        self.name = name
        self.command = command
        self.install_instructions = install_instructions
        self.stdout = stdout
        self.stderr = stderr
        if stdout is None and stderr is None:
            message = f"Missing required dependency: {name}.\nInstall instructions: {install_instructions}"
        else:
            assert stdout is not None and stderr is not None, "dependency failure carries both stdout and stderr"
            message = "\n".join(
                [
                    f"Dependency check failed: {name}.",
                    f"Command: {' '.join(command)}",
                    f"Install instructions: {install_instructions}",
                    f"stdout: {stdout.strip()}",
                    f"stderr: {stderr.strip()}",
                ]
            )
        super().__init__(message)


def starter_config() -> StarterConfig:
    payload = tomllib.loads(resources.files("agent_memory.defaults").joinpath("global.toml").read_text(encoding="utf-8"))
    default_vault = payload["default_vault"]
    global_scopes = payload["global_scopes"]
    search_max_results = payload["search_max_results"]
    search_max_tokens = payload["search_max_tokens"]
    assert isinstance(default_vault, str), "starter config default_vault must be a string"
    assert isinstance(global_scopes, list), "starter config global_scopes must be a list"
    assert all(isinstance(scope, str) for scope in global_scopes), "starter config global_scopes entries must be strings"
    assert isinstance(search_max_results, int), "starter config search_max_results must be an integer"
    assert isinstance(search_max_tokens, int), "starter config search_max_tokens must be an integer"
    assert search_max_results > 0, "starter config search_max_results must be positive"
    assert search_max_tokens > 0, "starter config search_max_tokens must be positive"
    return StarterConfig(
        default_vault=normalize_vault_path(Path(default_vault)),
        global_scopes=tuple(global_scopes),
        search_max_results=search_max_results,
        search_max_tokens=search_max_tokens,
    )


@dataclass(frozen=True)
class MemoryDocument:
    metadata: dict[str, MetadataValue]
    body: str


@dataclass(frozen=True)
class NoteRecord:
    key: str
    path: Path
    title: str
    memory_type: MemoryType
    scope: MemoryScope
    tags: tuple[str, ...]
    timestamp: str | None
    document: MemoryDocument


@dataclass(frozen=True)
class MemoryTransition:
    old_key: str
    new_key: str
    old_title: str
    new_title: str
    scope: MemoryScope
    memory_type: MemoryType
    source_path: Path
    destination_path: Path
    metadata: dict[str, MetadataValue]
    body: str
    description: str


@dataclass(frozen=True)
class LinkRecord:
    key: str
    path: Path
    title: str
    depth: int


LinkNeighborProvider = Callable[[ProjectConfig, str], tuple[str, ...]]


def normalize_vault_path(vault: Path) -> Path:
    return vault.expanduser().resolve(strict=False)


def init_global_vault(vault: Path) -> JsonObject:
    vault = normalize_vault_path(vault)
    vault.mkdir(parents=True)
    run_checked(["git", "init"], cwd=vault)
    configure_vault_git(vault)
    write_new_file(vault / ".gitignore", f"{ZK_NOTEBOOK_DB_IGNORE}\n")
    run_checked(["zk", "--no-input", "init", str(vault)], cwd=vault)
    write_agent_memory_marker(vault)
    for relative_dir in VAULT_DIRECTORIES:
        (vault / relative_dir).mkdir(parents=True)
    write_new_file(
        vault / "index.md",
        render_memory(
            {"okf_version": OKF_VERSION},
            parent_index_body("Agent Memory Vault", ROOT_INDEX_ENTRIES),
        ),
    )
    write_new_file(
        vault / "global" / "index.md",
        render_memory(
            {"okf_version": OKF_VERSION},
            parent_index_body(
                "Global Memory",
                directory_index_entries(MEMORY_TYPE_DIRECTORY_NAMES, index_descriptions(MemoryScope.GLOBAL)),
            ),
        ),
    )
    write_section_indexes(vault / "global", MEMORY_TYPE_DIRECTORY_NAMES)
    write_new_file(vault / "_meta" / "projects.toml", tomli_w.dumps({"projects": []}))
    index_zk_notebook(vault)
    commit_vault_changes(vault, "Initialize agent-memory vault")
    return {"vault": str(vault)}


def write_agent_memory_marker(vault: Path) -> None:
    # The vault's agent-memory metadata marker. Its presence proves the vault was
    # initialized by this tool; the file is asserted but never read, so it carries only
    # the marker version. liwe loads notes directly from the vault root in-process, so no
    # .iwe/ config directory is created.
    marker_dir = vault / ".agents" / "memories"
    marker_dir.mkdir(parents=True)
    write_new_file(marker_dir / "config.toml", tomli_w.dumps({"okf_version": OKF_VERSION}))


def init_project(vault: Path, cwd: Path, project_id: str | None = None) -> JsonObject:
    vault = normalize_vault_path(vault)
    assert (vault / ".agents" / "memories" / "config.toml").is_file(), "vault must be initialized with agent-memory metadata"
    git_root = git_root_for(cwd)
    remote = "" if project_id is not None else git_remote(git_root)
    project_id = validate_project_id(project_id) if project_id is not None else project_id_from_remote(remote)
    project_dir = vault / "projects" / project_id
    project_dir.mkdir(parents=True, exist_ok=True)

    project_index = project_dir / "index.md"
    if not project_index.exists():
        project_index.write_text(
            render_memory(
                {"okf_version": OKF_VERSION},
                parent_index_body(
                    project_id,
                    directory_index_entries(
                        MEMORY_TYPE_DIRECTORY_NAMES,
                        index_descriptions(MemoryScope.PROJECT),
                    ),
                ),
            ),
            encoding="utf-8",
        )

    for directory in MEMORY_TYPE_DIRECTORY_NAMES:
        dir_path = project_dir / directory
        dir_path.mkdir(exist_ok=True)
        sec_index = dir_path / "index.md"
        if not sec_index.exists():
            sec_index.write_text(
                render_memory({"okf_version": OKF_VERSION}, leaf_index_body(section_title(directory))),
                encoding="utf-8",
            )

    install_project_agent_state_links(git_root, project_dir)

    index_link_target = f"projects/{project_id}/index.md"
    vault_index = vault / "index.md"
    vault_index_content = vault_index.read_text(encoding="utf-8") if vault_index.is_file() else ""
    if f"({index_link_target})" not in vault_index_content:
        append_index_link(
            vault_index,
            project_id,
            index_link_target,
            "Project memory bundle.",
        )

    write_agents_pointer(git_root, vault, project_id)
    append_project_record(
        vault / "_meta" / "projects.toml",
        {"project_id": project_id, "root": str(git_root), "remote": remote},
    )
    index_zk_notebook(vault)

    paths = [
        vault / "index.md",
        vault / "_meta" / "projects.toml",
        project_index,
    ] + [project_dir / directory / "index.md" for directory in MEMORY_TYPE_DIRECTORY_NAMES]

    commit_vault_changes(vault, f"Register project {project_id}", paths=paths)
    return {
        "project_id": project_id,
        "vault": str(vault),
        "project_root": str(git_root),
    }


def add_memory(
    scope: MemoryScope,
    memory_type: MemoryType,
    title: str,
    content: str,
    cwd: Path,
) -> JsonObject:
    config = config_for_memory_scope(scope, cwd)
    slug = memory_slug(title)
    directory = memory_directory(config, scope, memory_type)
    path = directory / f"{slug}.md"
    key = memory_key(config.vault, path)
    description = okf_description(content)
    metadata = note_metadata(config, scope, memory_type, title, description)
    body = f"# {title}\n\n{content}\n"

    # Track existing state for rollback
    index_path = directory / "index.md"
    index_existed = index_path.exists()
    old_index_content = index_path.read_text(encoding="utf-8") if index_existed else None
    path_existed = path.exists()

    write_new_memory(path, metadata, body)
    append_index_link(index_path, title, path.name, description)

    try:
        index_zk_notebook(config.vault)
        commit_vault_changes(config.vault, f"Record {scope.value} {memory_type.value} memory: {title}", paths=[path, index_path])
    except subprocess.CalledProcessError as e:
        # Rollback!
        run_checked_optional(["git", "reset", "HEAD", "--", str(path.relative_to(config.vault)), str(index_path.relative_to(config.vault))], cwd=config.vault)
        if index_existed and old_index_content is not None:
            index_path.write_text(old_index_content, encoding="utf-8")
        elif not index_existed and index_path.exists():
            index_path.unlink()

        if not path_existed and path.exists():
            path.unlink()

        index_zk_notebook(config.vault)

        git_stderr = e.stderr or ""
        raise VaultCommitError(vault_commit_error_message(git_stderr)) from e

    return {"key": key, "path": str(path)}


def memory_transition(
    config: ProjectConfig,
    key: str,
    title: str | None,
    memory_type: MemoryType | None,
    content: str | None,
) -> MemoryTransition:
    source_path = config.vault / f"{key}.md"
    document = read_memory(source_path)
    old_title = metadata_string(document.metadata, "title", source_path)
    scope = MemoryScope(metadata_string(document.metadata, "scope", source_path))
    old_type = MemoryType(metadata_string(document.metadata, "type", source_path))
    new_title = title if title is not None else old_title
    new_type = memory_type if memory_type is not None else old_type
    body = updated_memory_body(document.body, new_title, content)
    description = okf_description(content if content is not None else body)
    metadata = note_metadata(config, scope, new_type, new_title, description)
    destination_path = memory_directory(config, scope, new_type) / f"{memory_slug(new_title)}.md"
    return MemoryTransition(
        old_key=key,
        new_key=memory_key(config.vault, destination_path),
        old_title=old_title,
        new_title=new_title,
        scope=scope,
        memory_type=new_type,
        source_path=source_path,
        destination_path=destination_path,
        metadata=metadata,
        body=body,
        description=description,
    )


def memory_slug(title: str) -> str:
    slug = slugify(title)
    assert slug, "title must produce a nonempty slug"
    return slug


def sync_memory_transition_indexes(transition: MemoryTransition) -> None:
    if transition.destination_path.parent == transition.source_path.parent:
        replace_index_link(
            transition.destination_path.parent / "index.md",
            transition.old_title,
            transition.new_title,
            transition.destination_path.name,
            transition.description,
        )
        return
    remove_index_link(transition.source_path.parent / "index.md", transition.old_title)
    append_index_link(
        transition.destination_path.parent / "index.md",
        transition.new_title,
        transition.destination_path.name,
        transition.description,
    )


def update_memory(
    key: str,
    title: str | None,
    memory_type: MemoryType | None,
    content: str | None,
    cwd: Path,
) -> JsonObject:
    if title is None and memory_type is None and content is None:
        raise MemoryOperationError("update requires at least one of --title, --type, or --content")
    config = load_project_config(cwd)
    transition = memory_transition(config, key, title, memory_type, content)
    if transition.new_key != transition.old_key:
        iwe.rename(config.vault, transition.old_key, transition.new_key)
    write_memory(transition.destination_path, transition.metadata, transition.body)
    sync_memory_transition_indexes(transition)
    index_zk_notebook(config.vault)
    rewritten: list[JsonObject] = []
    if transition.new_key != transition.old_key:
        rewritten = rewrite_wikilink_files(config, (wikilink_rewrite(transition.old_key, transition.new_key),))
        index_zk_notebook(config.vault)

    paths = [
        transition.source_path,
        transition.destination_path,
        transition.source_path.parent / "index.md",
        transition.destination_path.parent / "index.md",
        *rewritten_record_paths(rewritten),
    ]
    try:
        commit_vault_changes(
            config.vault,
            f"Update {transition.scope.value} {transition.memory_type.value} memory: {transition.new_title}",
            paths=paths,
        )
    except subprocess.CalledProcessError as e:
        git_stderr = e.stderr or ""
        raise VaultCommitError(vault_commit_error_message(git_stderr)) from e

    result: JsonObject = {"key": transition.new_key, "path": str(transition.destination_path)}
    if transition.new_key != transition.old_key:
        result["rewritten"] = json_list(rewritten)
    return result


def delete_backlink_disposition_error(key: str, inbound_keys: Sequence[str]) -> str:
    return f"delete would orphan inbound wikilinks for {key}; inbound={', '.join(inbound_keys)}; rerun with --repoint <key-or-url> or --orphan-ok"


def delete_memory(
    key: str,
    cwd: Path,
    repoint: str | None = None,
    orphan_ok: bool = False,
) -> JsonObject:
    config = load_project_config(cwd)
    if repoint is not None and orphan_ok:
        raise MemoryOperationError("delete accepts --repoint or --orphan-ok, not both")
    inbound_keys = non_index_incoming_link_keys(config, key)
    if inbound_keys and repoint is None and not orphan_ok:
        raise MemoryOperationError(delete_backlink_disposition_error(key, inbound_keys))
    path = config.vault / f"{key}.md"
    try:
        document = read_memory(path)
    except MalformedMemoryError:
        remove_index_link_by_target(path.parent / "index.md", path.name)
        if path.exists():
            path.unlink()
        commit_message = f"Delete memory: {key}"
    else:
        title = metadata_string(document.metadata, "title", path)
        remove_index_link(path.parent / "index.md", title)
        commit_message = f"Delete memory: {title}"
        iwe.delete(config.vault, key)

    rewritten: list[JsonObject] = []
    if repoint is not None:
        rewritten = rewrite_wikilink_files(config, (wikilink_rewrite(key, repoint),))
    index_zk_notebook(config.vault)
    try:
        commit_vault_changes(
            config.vault,
            commit_message,
            paths=[
                path,
                path.parent / "index.md",
                *rewritten_record_paths(rewritten),
                *rewritten_record_parent_index_paths(rewritten),
            ],
        )
    except subprocess.CalledProcessError as e:
        git_stderr = e.stderr or ""
        raise VaultCommitError(vault_commit_error_message(git_stderr)) from e

    result: JsonObject = {"deleted": key}
    if repoint is not None:
        result["repointed_to"] = repoint
        result["rewritten"] = json_list(rewritten)
    if orphan_ok:
        result["orphaned"] = json_list(inbound_keys)
    return result


def search_memories(scope: SearchScope, query: str, cwd: Path) -> JsonObject:
    config = config_for_search_scope(scope, cwd)
    key_matches = key_search_records(config, scope, query)
    exact_matches = exact_content_records(config, scope, query)
    fuzzy_matches = fuzzy_content_records(config, scope, query)
    results = dedupe_records_by_key([*key_matches, *exact_matches, *fuzzy_matches])[: config.search_max_results]
    ranked_matches = search_content_ranked(scope, query, cwd)
    ranked_results = ranked_matches["results"]
    assert isinstance(ranked_results, list), "ranked search results must be a JSON list"
    return {
        "query": query,
        "scope": scope.value,
        "results": json_list(results),
        "key_matches": json_list(key_matches),
        "exact_content_matches": json_list(exact_matches),
        "fuzzy_content_matches": json_list(fuzzy_matches),
        "ranked_content_matches": ranked_results,
    }


def search_keys(scope: SearchScope, query: str, cwd: Path) -> JsonObject:
    config = config_for_search_scope(scope, cwd)
    return {
        "query": query,
        "scope": scope.value,
        "results": json_list(key_search_records(config, scope, query)),
    }


def search_content_exact(scope: SearchScope, query: str, cwd: Path) -> JsonObject:
    config = config_for_search_scope(scope, cwd)
    return {
        "query": query,
        "scope": scope.value,
        "results": json_list(exact_content_records(config, scope, query)),
    }


def search_metadata(
    scope: SearchScope,
    memory_type: MemoryType | None,
    tag: str | None,
    created_after: str | None,
    cwd: Path,
) -> JsonObject:
    config = config_for_search_scope(scope, cwd)
    created_after_datetime = parse_created_after(created_after)
    records = [
        metadata_search_record_json(record) for record in inspect_note_records(config, scope) if note_record_matches_metadata(record, memory_type, tag, created_after_datetime)
    ]
    return {
        "scope": scope.value,
        "results": json_list(records[: config.search_max_results]),
    }


def key_search_records(config: ProjectConfig, scope: SearchScope, query: str) -> list[JsonObject]:
    query_text = query.casefold()
    records: list[JsonObject] = []
    for record in inspect_note_records(config, scope):
        key_matches = query_text in record.key.casefold()
        title_matches = query_text in record.title.casefold()
        if key_matches or title_matches:
            json_record = note_record_json(record)
            json_record["source"] = "keys"
            records.append(json_record)
    return dedupe_records_by_key(records)[: config.search_max_results]


def exact_content_records(config: ProjectConfig, scope: SearchScope, query: str) -> list[JsonObject]:
    roots = [str(root) for root in search_roots(config, scope)]
    # span plan cards too, so one query covers both memories and plans (issue #4). Plans
    # are project-scoped, so include them whenever the scope reaches the project.
    if scope in (SearchScope.PROJECT, SearchScope.BOTH):
        # An initialized project always has its plans/ directory (it is also the
        # MemoryType.PLAN directory), so it is unconditionally a valid search root.
        roots.append(str(project_plans_root(config, load_card_system_config())))
    output = run_ripgrep_search(
        [
            "rg",
            "--json",
            "--line-number",
            "--with-filename",
            "--fixed-strings",
            query,
            *roots,
        ],
        cwd=config.vault,
    )
    records: list[JsonObject] = []
    for raw_match in output.splitlines():
        payload = json.loads(raw_match)
        if payload.get("type") != "match":
            continue
        data = payload["data"]
        assert isinstance(data, dict), f"unexpected rg match payload: {payload}"
        path_text = data["path"]["text"]
        line_number = data["line_number"]
        lines = data["lines"]["text"]
        assert isinstance(path_text, str), f"unexpected rg path type: {payload}"
        assert isinstance(line_number, int), f"unexpected rg line number: {payload}"
        assert isinstance(lines, str), f"unexpected rg line text: {payload}"
        path = Path(path_text)
        if not path.is_absolute():
            path = config.vault / path
        path = path.resolve()
        records.append(
            {
                "key": memory_key(config.vault, path),
                "path": str(path),
                "line": line_number,
                "text": lines.rstrip(),
                "source": "exact",
            }
        )
    return dedupe_records_by_key(records)[: config.search_max_results]


def zk_search_scope(config: ProjectConfig, scope: SearchScope, query: str) -> list[JsonObject]:
    results: list[JsonObject] = []
    for root in search_roots(config, scope):
        results.extend(
            zk_search_root(
                vault=config.vault,
                root=root,
                query=query,
                limit=config.search_max_results,
            )
        )
    return results


def fuzzy_content_records(config: ProjectConfig, scope: SearchScope, query: str) -> list[JsonObject]:
    return dedupe_records_by_key(zk_search_scope(config, scope, query))[: config.search_max_results]


def dedupe_records_by_key(records: Sequence[JsonObject]) -> list[JsonObject]:
    seen: set[str] = set()
    deduped: list[JsonObject] = []
    for record in records:
        key = record["key"]
        assert isinstance(key, str), "search records must include string keys"
        if key not in seen:
            seen.add(key)
            deduped.append(record)
    return deduped


def json_list(values: Sequence[JsonValue]) -> list[JsonValue]:
    # Widen a homogeneous JSON-value sequence to the list[JsonValue] shape required by
    # JsonObject slots. Sequence is covariant, so list[JsonObject] and list[str] inputs
    # satisfy Sequence[JsonValue]; the copy decouples the emitted payload from callers.
    return list(values)


def search_content_ranked(scope: SearchScope, query: str, cwd: Path) -> JsonObject:
    config = config_for_search_scope(scope, cwd)
    roots = search_roots(config, scope)
    per_root_tokens = config.search_max_tokens // len(roots)
    assert per_root_tokens > 0, "ranked content search token budget must cover every selected scope root"
    payloads = [
        probe_search_root(
            root=root,
            query=query,
            max_results=config.search_max_results,
            max_tokens=per_root_tokens,
            cwd=config.vault,
        )
        for root in roots
    ]
    return merge_probe_payloads(
        payloads,
        max_results=config.search_max_results,
        max_tokens=config.search_max_tokens,
    )


def search_content_fuzzy(scope: SearchScope, query: str, cwd: Path) -> JsonObject:
    config = config_for_search_scope(scope, cwd)
    records = zk_search_scope(config, scope, query)
    return {
        "query": query,
        "scope": scope.value,
        "results": json_list(records[: config.search_max_results]),
    }


def zk_search_root(vault: Path, root: Path, query: str, limit: int) -> list[JsonObject]:
    relative_root = root.relative_to(vault).as_posix()
    result = run_checked(
        [
            "zk",
            "--notebook-dir",
            str(vault),
            "--working-dir",
            str(vault),
            "list",
            relative_root,
            "--match",
            query,
            "--limit",
            str(limit),
            "--format",
            "jsonl",
            "--no-pager",
            "--quiet",
        ],
        cwd=vault,
    )
    records: list[JsonObject] = []
    for line in result.stdout.splitlines():
        records.append(zk_result_record(json.loads(line), vault=vault, root=root))
    return records


def zk_result_record(raw_record: JsonValue, vault: Path, root: Path) -> JsonObject:
    assert isinstance(raw_record, dict), "zk result must be a JSON object"
    abs_path_value = raw_record["absPath"]
    assert isinstance(abs_path_value, str), "zk result absPath must be a string"
    abs_path = Path(abs_path_value).resolve()
    abs_path.relative_to(root)
    title_value = raw_record["title"]
    assert isinstance(title_value, str), "zk result title must be a string"
    return {
        "key": memory_key(vault, abs_path),
        "path": str(abs_path),
        "title": title_value,
    }


def probe_search_root(
    root: Path,
    query: str,
    max_results: int,
    max_tokens: int,
    cwd: Path,
) -> JsonObject:
    result = run_checked(
        [
            "npx",
            "-y",
            "@probelabs/probe@latest",
            "search",
            query,
            str(root),
            "--format",
            "json",
            "--max-results",
            str(max_results),
            "--max-tokens",
            str(max_tokens),
        ],
        cwd=cwd,
    )
    decoded = json.loads(result.stdout)
    assert isinstance(decoded, dict), "probe search must emit a JSON object"
    return decoded


def merge_probe_payloads(
    payloads: Sequence[JsonObject],
    max_results: int,
    max_tokens: int,
) -> JsonObject:
    assert payloads, "probe payload merge requires at least one payload"
    results: list[JsonObject] = []
    skipped_files: list[JsonObject] = []
    total_bytes = 0
    total_tokens = 0
    versions: set[str] = set()
    for payload in payloads:
        results.extend(probe_results(payload))
        skipped_files.extend(probe_skipped_files(payload))
        limits = json_child(payload, "limits")
        total_bytes += json_int(limits, "total_bytes")
        total_tokens += json_int(limits, "total_tokens")
        version = payload["version"]
        assert isinstance(version, str), "probe version must be a string"
        versions.add(version)
    assert len(versions) == 1, "all probe payloads must come from the same Probe version"
    ranked_results = sorted(results, key=probe_score, reverse=True)[:max_results]
    return {
        "limits": {
            "max_bytes": None,
            "max_results": max_results,
            "max_tokens": max_tokens,
            "total_bytes": total_bytes,
            "total_tokens": total_tokens,
        },
        "results": json_list(ranked_results),
        "skipped_files": json_list(skipped_files),
        "summary": {
            "count": len(ranked_results),
            "total_bytes": total_bytes,
            "total_tokens": total_tokens,
        },
        "version": versions.pop(),
    }


def probe_results(payload: JsonObject) -> list[JsonObject]:
    raw_results = payload["results"]
    assert isinstance(raw_results, list), "probe results must be a list"
    results: list[JsonObject] = []
    for result in raw_results:
        assert isinstance(result, dict), "probe result entries must be JSON objects"
        results.append(result)
    return results


def probe_skipped_files(payload: JsonObject) -> list[JsonObject]:
    # Probe's JSON contract emits "skipped_files" only when it skips files under the
    # token budget; when nothing is skipped the key is omitted entirely. Absence is the
    # documented "no files skipped" outcome. A present key must be a real list of objects
    # or the payload is malformed and must fail loudly.
    raw_skipped = payload.get("skipped_files")
    if raw_skipped is None:
        assert "skipped_files" not in payload, "probe skipped_files must be a list, not null"
        return []
    assert isinstance(raw_skipped, list), "probe skipped files must be a list"
    skipped_files: list[JsonObject] = []
    for skipped_file in raw_skipped:
        assert isinstance(skipped_file, dict), "probe skipped-file entries must be JSON objects"
        skipped_files.append(skipped_file)
    return skipped_files


def probe_score(result: JsonObject) -> float:
    score = result["score"]
    assert isinstance(score, int | float), "probe result score must be numeric"
    assert not isinstance(score, bool), "probe result score must be numeric"
    return float(score)


def json_child(payload: JsonObject, key: str) -> JsonObject:
    child = payload[key]
    assert isinstance(child, dict), f"probe {key} must be a JSON object"
    return child


def json_int(payload: JsonObject, key: str) -> int:
    value = payload[key]
    assert isinstance(value, int), f"probe {key} must be an integer"
    assert not isinstance(value, bool), f"probe {key} must be an integer"
    return value


def retrieve_memory(key: str, cwd: Path) -> str:
    config = load_project_config(cwd)
    try:
        return iwe.retrieve(config.vault, key)
    except KeyError as exc:
        raise AssertionError(
            "retrieve expects a full vault-relative key. "
            f"Could not resolve `{key}`. "
            'Use `agent-memory search --scope both "<term>"` to discover keys, then retrieve a result such as '
            "`projects/<project-id>/decisions/parser-choice` or "
            "`projects/<project-id>/plans/features/FEATURE-ID/FEATURE-ID`."
        ) from exc


def squash_memory(key: str, depth: int, cwd: Path) -> str:
    assert depth > 0, f"squash depth must be positive: {depth}"
    config = load_project_config(cwd)
    return iwe.squash(config.vault, key, depth)


def split_memory(key: str, section: str, cwd: Path) -> JsonObject:
    config = load_project_config(cwd)
    source_path = memory_path_for_key(config, key)
    source_document = read_memory(source_path)
    source_title = metadata_string(source_document.metadata, "title", source_path)
    memory_type = MemoryType(metadata_string(source_document.metadata, "type", source_path))
    scope = MemoryScope(metadata_string(source_document.metadata, "scope", source_path))
    affected_keys = iwe.extract(config.vault, key, section)
    extracted_keys: list[str] = []
    for affected_key in affected_keys:
        if affected_key == key:
            continue
        extracted_path = memory_path_for_key(config, affected_key)
        extracted_body = extracted_path.read_text(encoding="utf-8")
        title = first_heading_title(extracted_body)
        description = f"Extracted from {source_title}."
        write_memory(extracted_path, note_metadata(config, scope, memory_type, title, description), extracted_body)
        append_index_link(extracted_path.parent / "index.md", title, extracted_path.name, description)
        extracted_keys.append(affected_key)
    assert extracted_keys, "split must create at least one extracted memory"
    assert len(extracted_keys) == 1, f"split section must create exactly one extracted memory: {extracted_keys}"
    rewritten = rewrite_wikilink_files(
        config,
        (wikilink_rewrite(f"{key}#{section}", extracted_keys[0]),),
    )
    index_zk_notebook(config.vault)
    paths = list(
        set(
            [memory_path_for_key(config, k) for k in affected_keys]
            + [memory_path_for_key(config, k).parent / "index.md" for k in affected_keys]
            + rewritten_record_paths(rewritten)
            + rewritten_record_parent_index_paths(rewritten)
        )
    )
    commit_vault_changes(config.vault, f"Split memory section: {section}", paths=paths)
    return {
        "key": key,
        "section": section,
        "output": json_list(affected_keys),
        "extracted": json_list(extracted_keys),
        "rewritten": json_list(rewritten),
    }


def merge_memory(key: str, reference: str, cwd: Path) -> JsonObject:
    config = load_project_config(cwd)
    reference_path = memory_path_for_key(config, reference)
    reference_document = read_memory(reference_path)
    reference_title = metadata_string(reference_document.metadata, "title", reference_path)
    affected_keys = iwe.inline(config.vault, key, reference)
    rewritten = rewrite_wikilink_files(
        config,
        (wikilink_rewrite(reference, f"{key}#{reference_title}"),),
        include_indexes=False,
    )
    index_zk_notebook(config.vault)
    # Build pathspecs without asserting existence -- iwe.inline deletes the
    # reference file, so memory_path_for_key() would fail on the merged-away key.
    affected_paths = [config.vault / f"{k}.md" for k in affected_keys]
    paths = list(set(affected_paths + [p.parent / "index.md" for p in affected_paths] + rewritten_record_paths(rewritten) + rewritten_record_parent_index_paths(rewritten)))
    commit_vault_changes(config.vault, f"Merge memory reference: {reference}", paths=paths)
    return {"key": key, "reference": reference, "output": json_list(affected_keys), "rewritten": json_list(rewritten)}


def move_memory(key: str, destination: str, cwd: Path) -> JsonObject:
    config = load_project_config(cwd)
    assert destination.startswith("global/"), "move destination must be global"
    source_path = config.vault / f"{key}.md"
    assert source_path.is_file(), "memory to move must exist"
    destination_key = f"{destination}/{source_path.stem}"
    destination_path = config.vault / f"{destination_key}.md"
    assert destination_path.parent.is_dir(), "move destination directory must exist"

    source_document = read_memory(source_path)
    memory_type = MemoryType(metadata_string(source_document.metadata, "type", source_path))
    title = metadata_string(source_document.metadata, "title", source_path)
    description = metadata_string(source_document.metadata, "description", source_path)
    iwe.rename(config.vault, key, destination_key)

    moved_document = read_memory(destination_path)
    moved_metadata = PromotedNoteMetadata(
        type=memory_type,
        title=title,
        description=description,
        tags=okf_tags(MemoryScope.GLOBAL, memory_type, ("promoted",)),
        timestamp=okf_timestamp(),
        scope=MemoryScope.GLOBAL,
        origin_project_id=require_project_id(config),
    ).to_yaml_payload()
    write_memory(destination_path, moved_metadata, moved_document.body)
    append_index_link(
        destination_path.parent / "index.md",
        title,
        destination_path.name,
        description,
    )

    pointer_description = f"Promoted to {destination_key}."
    pointer_metadata = ProjectNoteMetadata(
        type=memory_type,
        title=title,
        description=pointer_description,
        tags=okf_tags(MemoryScope.PROJECT, memory_type, ("promotion-pointer",)),
        timestamp=okf_timestamp(),
        scope=MemoryScope.PROJECT,
        project_id=require_project_id(config),
    ).to_yaml_payload()
    pointer_body = f"# {title}\n\nPromoted to [[{destination_key}]].\n"
    assert source_path.parent.is_dir(), "project memory parent must be a directory"
    write_new_memory(source_path, pointer_metadata, pointer_body)
    replace_index_link(source_path.parent / "index.md", title, title, source_path.name, pointer_description)
    index_zk_notebook(config.vault)
    rewritten = rewrite_wikilink_files(config, (wikilink_rewrite(key, destination_key),))
    index_zk_notebook(config.vault)
    paths = [
        source_path,
        destination_path,
        source_path.parent / "index.md",
        destination_path.parent / "index.md",
        *rewritten_record_paths(rewritten),
        *rewritten_record_parent_index_paths(rewritten),
    ]
    commit_vault_changes(config.vault, f"Move memory {key} to {destination_key}", paths=paths)
    return {"key": destination_key, "path": str(destination_path), "rewritten": json_list(rewritten)}


def check_dependency(dependency: DependencyCheck, cwd: Path) -> JsonObject:
    if shutil.which(dependency.command[0]) is None:
        raise DependencyError(
            dependency.name,
            dependency.command,
            dependency.install_instructions,
            None,
            None,
        )
    result = subprocess.run(dependency.command, cwd=cwd, check=False, text=True, capture_output=True)
    if result.returncode != 0:
        raise DependencyError(
            dependency.name,
            dependency.command,
            dependency.install_instructions,
            result.stdout,
            result.stderr,
        )
    return {"name": dependency.name, "command": list(dependency.command), "status": "ok"}


def basic_doctor(cwd: Path) -> JsonObject:
    dependencies = [check_dependency(dependency, cwd) for dependency in BASIC_DEPENDENCIES]
    return {
        "dependencies": json_list(dependencies),
        "tools": [dependency.name for dependency in BASIC_DEPENDENCIES],
    }


def doctor(cwd: Path) -> JsonObject:
    basic = basic_doctor(cwd)
    config = find_project_config(cwd)
    if config is None:
        return unbound_doctor(basic)
    git_root = git_root_for(cwd)
    project_id = require_project_id(config)
    project_dir = config.vault / "projects" / project_id
    assert_vault_zk_initialized(config.vault)
    return {
        "vault": str(config.vault),
        "project_id": project_id,
        "project_root": str(git_root),
        "project_bound": True,
        "agent_state": project_agent_state_records(git_root, project_dir),
        "auto_sync": sync_auto_status(),
        "last_sync": sync_state(),
        "tools": basic["tools"],
        "dependencies": basic["dependencies"],
    }


def unbound_doctor(basic: JsonObject) -> JsonObject:
    # `doctor` from an unbound directory reports global vault and tool health instead of
    # crashing (issue #25). global_only_config() resolves the known global vault and names
    # the init-global remedy if it is missing.
    config = global_only_config()
    assert_vault_zk_initialized(config.vault)
    return {
        "vault": str(config.vault),
        "project_id": None,
        "project_root": None,
        "project_bound": False,
        "agent_state": [],
        "auto_sync": sync_auto_status(),
        "last_sync": sync_state(),
        "tools": basic["tools"],
        "dependencies": basic["dependencies"],
    }


def assert_vault_zk_initialized(vault: Path) -> None:
    assert (vault / ".zk" / "config.toml").is_file(), "vault must be initialized with zk"
    assert (vault / ".zk" / "templates" / "default.md").is_file(), "zk default template must exist"


def run_checked(args: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, text=True, capture_output=True)


def run_checked_optional(args: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=False, text=True, capture_output=True)


def commit_vault_changes(vault: Path, message: str, paths: list[Path] | None = None) -> None:
    if paths is not None:
        rel_paths = [
            str(path.relative_to(vault))
            for path in paths
            if path.exists()
            or run_checked_optional(
                ["git", "ls-files", "--error-unmatch", str(path.relative_to(vault))],
                cwd=vault,
            ).returncode
            == 0
        ]
        if not rel_paths:
            return
        for path in paths:
            rel_path = path.relative_to(vault)
            if str(rel_path) in rel_paths:
                run_checked(["git", "add", "--", str(rel_path)], cwd=vault)
        # Skip commit if there are no cached changes for these paths
        diff_res = run_checked_optional(["git", "diff", "--cached", "--quiet", "--", *rel_paths], cwd=vault)
        if diff_res.returncode != 0:
            run_checked(["git", "commit", "-m", message, "--", *rel_paths], cwd=vault)
    else:
        run_checked(["git", "add", "--all", "."], cwd=vault)
        # Skip commit if there are no cached changes in the vault
        diff_res = run_checked_optional(["git", "diff", "--cached", "--quiet"], cwd=vault)
        if diff_res.returncode != 0:
            run_checked(["git", "commit", "-m", message], cwd=vault)


def git_status_entries(repo: Path) -> tuple[str, ...]:
    result = run_checked(["git", "status", "--short"], cwd=repo)
    return tuple(line for line in result.stdout.splitlines() if line)


def git_status_records(repo: Path) -> list[JsonValue]:
    records: list[JsonValue] = []
    for entry in git_status_entries(repo):
        assert len(entry) >= 4, f"unexpected git status entry shape: repo={repo}; entry={entry!r}"
        record: JsonObject = {"status": entry[:2], "path": entry[3:]}
        records.append(record)
    return records


def git_current_branch(repo: Path) -> str:
    result = run_checked(["git", "branch", "--show-current"], cwd=repo)
    branch = result.stdout.strip()
    assert branch, f"git repository must be on a named branch: {repo}"
    return branch


def git_upstream(repo: Path) -> str:
    result = run_checked(["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"], cwd=repo)
    upstream = result.stdout.strip()
    assert upstream, f"git repository must have an upstream branch: {repo}"
    return upstream


def git_ahead_behind(repo: Path) -> tuple[int, int]:
    result = run_checked(["git", "rev-list", "--left-right", "--count", "HEAD...@{upstream}"], cwd=repo)
    parts = result.stdout.split()
    assert len(parts) == 2, f"unexpected git ahead/behind output: repo={repo}; output={result.stdout!r}"
    ahead, behind = (int(part) for part in parts)
    return ahead, behind


def git_head(repo: Path) -> str:
    return run_checked(["git", "rev-parse", "HEAD"], cwd=repo).stdout.strip()


def sync_conflict_branch_name(branch: str, head: str) -> str:
    safe_branch = re.sub(r"[^A-Za-z0-9._-]+", "-", branch).strip("-")
    assert safe_branch, f"cannot build conflict branch from branch name: {branch!r}"
    return f"agent-memory-sync-conflict-{safe_branch}-{head[:12]}"


def sync_config(cwd: Path) -> tuple[ProjectConfig, bool]:
    config = find_project_config(cwd)
    if config is not None:
        return config, True
    return global_only_config(), False


def sync_systemd_paths() -> SyncSystemdPaths:
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config_home is None:
        config_home = Path.home() / ".config"
    else:
        assert xdg_config_home, "XDG_CONFIG_HOME must be non-empty when set; unset it to use ~/.config"
        config_home = Path(xdg_config_home)
    unit_dir = config_home / "systemd" / "user"
    return SyncSystemdPaths(
        unit_dir=unit_dir,
        service=unit_dir / SYNC_SYSTEMD_SERVICE_NAME,
        timer=unit_dir / SYNC_SYSTEMD_TIMER_NAME,
        timer_wants=unit_dir / "timers.target.wants" / SYNC_SYSTEMD_TIMER_NAME,
    )


def sync_state_path() -> Path:
    xdg_state_home = os.environ.get("XDG_STATE_HOME")
    if xdg_state_home is None:
        state_home = Path.home() / ".local" / "state"
    else:
        assert xdg_state_home, "XDG_STATE_HOME must be non-empty when set; unset it to use ~/.local/state"
        state_home = Path(xdg_state_home)
    return state_home / "agent-memory" / SYNC_STATE_FILENAME


def empty_sync_state(state_path: Path) -> JsonObject:
    return {
        "last_attempt": {"status": "never_run"},
        "last_failure": {"status": "none"},
        "last_success": {"status": "none"},
        "state_path": str(state_path),
    }


def sync_state() -> JsonObject:
    state_path = sync_state_path()
    if not state_path.exists():
        return empty_sync_state(state_path)
    decoded = json.loads(state_path.read_text(encoding="utf-8"))
    assert isinstance(decoded, dict), f"sync state file must contain a JSON object: {state_path}"
    assert decoded["state_path"] == str(state_path), f"sync state path mismatch: {state_path}"
    return decoded


def sync_attempt_record(result: JsonObject) -> JsonObject:
    pushed = result["pushed"]
    assert isinstance(pushed, bool), "sync result must include pushed boolean"
    if pushed:
        status = "success"
    else:
        status_value = result["status"]
        assert isinstance(status_value, str), "non-pushed sync result must include status"
        status = status_value
    return {"result": result, "status": status}


def write_sync_state(result: JsonObject) -> JsonObject:
    state_path = sync_state_path()
    previous_state = sync_state()
    previous_last_failure = previous_state["last_failure"]
    assert isinstance(previous_last_failure, dict), "previous sync state last_failure must be an object"
    previous_last_success = previous_state["last_success"]
    assert isinstance(previous_last_success, dict), "previous sync state last_success must be an object"
    attempt = sync_attempt_record(result)
    pushed = result["pushed"]
    assert isinstance(pushed, bool), "sync result must include pushed boolean"
    if pushed:
        last_success = attempt
        last_failure = previous_last_failure
    else:
        last_success = previous_last_success
        last_failure = attempt
    state: JsonObject = {
        "last_attempt": attempt,
        "last_failure": last_failure,
        "last_success": last_success,
        "state_path": str(state_path),
    }
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return state


def sync_systemd_unit_names() -> JsonObject:
    return {
        "service": SYNC_SYSTEMD_SERVICE_NAME,
        "timer": SYNC_SYSTEMD_TIMER_NAME,
    }


def sync_timer_interval_seconds(timer_path: Path) -> int:
    for line in timer_path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if key == "OnUnitActiveSec":
            assert separator == "=", f"timer interval line must contain '=': timer={timer_path}; line={line!r}"
            assert value.endswith("s"), f"timer interval must be recorded in seconds: timer={timer_path}; value={value!r}"
            interval = int(value[:-1])
            assert interval > 0, f"timer interval must be positive: timer={timer_path}; value={value!r}"
            return interval
    raise AssertionError(f"timer unit must contain OnUnitActiveSec: timer={timer_path}")


def sync_timer_enabled(paths: SyncSystemdPaths) -> bool:
    if paths.timer_wants.is_symlink():
        assert paths.timer_wants.resolve() == paths.timer.resolve(), (
            f"auto-sync timer enablement symlink points at the wrong unit; link={paths.timer_wants}; target={paths.timer_wants.resolve()}; expected={paths.timer}"
        )
        return True
    assert not paths.timer_wants.exists(), f"auto-sync timer enablement path is not a symlink: {paths.timer_wants}"
    return False


def sync_auto_status() -> JsonObject:
    paths = sync_systemd_paths()
    service_exists = paths.service.is_file()
    timer_exists = paths.timer.is_file()
    enabled = sync_timer_enabled(paths)
    assert service_exists == timer_exists, (
        "auto-sync systemd installation must contain both unit files; "
        f"service={paths.service} exists={service_exists}; timer={paths.timer} exists={timer_exists}; "
        "run `agent-memory sync remove` and then `agent-memory sync install <seconds>`"
    )
    assert not enabled or timer_exists, f"auto-sync timer cannot be enabled without an installed timer unit; timer={paths.timer}; link={paths.timer_wants}"
    status: JsonObject = {
        "enabled": enabled,
        "installed": service_exists,
        "service_path": str(paths.service),
        "timer_path": str(paths.timer),
        "timer_wants_path": str(paths.timer_wants),
        "unit_names": sync_systemd_unit_names(),
    }
    if timer_exists:
        status["interval_seconds"] = sync_timer_interval_seconds(paths.timer)
    return status


def render_sync_service(vault: Path) -> str:
    command = shlex.join([sys.executable, "-m", "agent_memory", "sync", "run"])
    vault_arg = shlex.quote(str(vault))
    return "\n".join(
        (
            "[Unit]",
            "Description=Synchronize the agent-memory vault",
            "",
            "[Service]",
            "Type=oneshot",
            f"WorkingDirectory={vault_arg}",
            f"Environment=AGENT_MEMORY_VAULT={vault_arg}",
            f"ExecStart={command}",
            "",
        )
    )


def render_sync_timer(interval_seconds: int) -> str:
    assert interval_seconds > 0, f"sync timer interval must be positive seconds: {interval_seconds}"
    return "\n".join(
        (
            "[Unit]",
            f"Description=Run agent-memory vault synchronization every {interval_seconds} seconds",
            "",
            "[Timer]",
            f"OnBootSec={interval_seconds}s",
            f"OnUnitActiveSec={interval_seconds}s",
            "Persistent=true",
            f"Unit={SYNC_SYSTEMD_SERVICE_NAME}",
            "",
            "[Install]",
            "WantedBy=timers.target",
            "",
        )
    )


def install_sync_systemd_timer(cwd: Path, interval_seconds: int) -> JsonObject:
    config, _project_bound = sync_config(cwd)
    assert_vault_zk_initialized(config.vault)
    paths = sync_systemd_paths()
    paths.unit_dir.mkdir(parents=True, exist_ok=True)
    paths.service.write_text(render_sync_service(config.vault), encoding="utf-8")
    paths.timer.write_text(render_sync_timer(interval_seconds), encoding="utf-8")
    return {
        "vault": str(config.vault),
        "auto_sync": sync_auto_status(),
    }


def enable_sync_systemd_timer(cwd: Path) -> JsonObject:
    config, _project_bound = sync_config(cwd)
    assert_vault_zk_initialized(config.vault)
    paths = sync_systemd_paths()
    assert paths.service.is_file(), f"auto-sync service unit must be installed before enable: {paths.service}"
    assert paths.timer.is_file(), f"auto-sync timer unit must be installed before enable: {paths.timer}"
    if paths.timer_wants.is_symlink():
        assert paths.timer_wants.resolve() == paths.timer.resolve(), (
            f"auto-sync timer enablement symlink points at the wrong unit; link={paths.timer_wants}; target={paths.timer_wants.resolve()}; expected={paths.timer}"
        )
    else:
        assert not paths.timer_wants.exists(), f"auto-sync timer enablement path is not a symlink: {paths.timer_wants}"
        paths.timer_wants.parent.mkdir(parents=True, exist_ok=True)
        paths.timer_wants.symlink_to(paths.timer)
    return {
        "vault": str(config.vault),
        "auto_sync": sync_auto_status(),
    }


def disable_sync_systemd_paths(paths: SyncSystemdPaths) -> None:
    if paths.timer_wants.is_symlink():
        assert paths.timer_wants.resolve() == paths.timer.resolve(), (
            f"auto-sync timer enablement symlink points at the wrong unit; link={paths.timer_wants}; target={paths.timer_wants.resolve()}; expected={paths.timer}"
        )
        paths.timer_wants.unlink()
        return
    assert not paths.timer_wants.exists(), f"auto-sync timer enablement path is not a symlink: {paths.timer_wants}"


def disable_sync_systemd_timer(cwd: Path) -> JsonObject:
    config, _project_bound = sync_config(cwd)
    paths = sync_systemd_paths()
    disable_sync_systemd_paths(paths)
    return {
        "vault": str(config.vault),
        "auto_sync": sync_auto_status(),
    }


def remove_sync_systemd_timer(cwd: Path) -> JsonObject:
    config, _project_bound = sync_config(cwd)
    paths = sync_systemd_paths()
    disable_sync_systemd_paths(paths)
    if paths.service.exists():
        paths.service.unlink()
    if paths.timer.exists():
        paths.timer.unlink()
    return {
        "vault": str(config.vault),
        "auto_sync": sync_auto_status(),
    }


def sync_status(cwd: Path) -> JsonObject:
    config, project_bound = sync_config(cwd)
    assert_vault_zk_initialized(config.vault)
    vault = config.vault
    changes = git_status_records(vault)
    ahead, behind = git_ahead_behind(vault)
    return {
        "vault": str(vault),
        "initialized": True,
        "project_bound": project_bound,
        "git": {
            "remote": git_remote(vault),
            "branch": git_current_branch(vault),
            "head": git_head(vault),
            "upstream": git_upstream(vault),
            "ahead": ahead,
            "behind": behind,
            "worktree_clean": not changes,
            "changes": changes,
        },
        "auto_sync": sync_auto_status(),
        "last_sync": sync_state(),
    }


def push_sync_conflict_branch(vault: Path, remote: str, branch: str, committed: bool, conflict_head: str) -> JsonObject:
    conflict_branch = sync_conflict_branch_name(branch, conflict_head)
    run_checked(["git", "rebase", "--abort"], cwd=vault)
    run_checked(["git", "branch", conflict_branch, conflict_head], cwd=vault)
    run_checked(["git", "push", "origin", f"{conflict_branch}:{conflict_branch}"], cwd=vault)
    run_checked(["git", "reset", "--hard", f"origin/{branch}"], cwd=vault)
    status_after = git_status_entries(vault)
    assert not status_after, f"vault sync conflict recovery must leave a clean worktree: vault={vault}; status={status_after}"
    return {
        "vault": str(vault),
        "remote": remote,
        "branch": branch,
        "committed": committed,
        "pushed": False,
        "head": git_head(vault),
        "worktree_clean": True,
        "status": "conflict_branch_pushed",
        "conflict_branch": conflict_branch,
        "conflict_head": conflict_head,
    }


def sync_vault(cwd: Path) -> JsonObject:
    config, _project_bound = sync_config(cwd)
    vault = config.vault
    branch = git_current_branch(vault)
    remote = git_remote(vault)
    status_before = git_status_entries(vault)
    committed = bool(status_before)
    if committed:
        commit_vault_changes(vault, "Auto-sync vault changes")
    sync_head = git_head(vault)
    run_checked(["git", "fetch", "origin", branch], cwd=vault)
    rebase = run_checked_optional(["git", "rebase", f"origin/{branch}"], cwd=vault)
    if rebase.returncode != 0:
        result = push_sync_conflict_branch(vault, remote, branch, committed, sync_head)
        write_sync_state(result)
        return result
    run_checked(["git", "push", "origin", branch], cwd=vault)
    status_after = git_status_entries(vault)
    assert not status_after, f"vault sync must leave a clean worktree: vault={vault}; status={status_after}"
    result = {
        "vault": str(vault),
        "remote": remote,
        "branch": branch,
        "committed": committed,
        "pushed": True,
        "head": git_head(vault),
        "worktree_clean": True,
    }
    write_sync_state(result)
    return result


def configure_vault_git(vault: Path) -> None:
    run_checked(["git", "config", "--local", "core.hooksPath", ""], cwd=vault)
    run_checked(["git", "config", "user.name", VAULT_GIT_USER_NAME], cwd=vault)
    run_checked(["git", "config", "user.email", VAULT_GIT_USER_EMAIL], cwd=vault)


def index_zk_notebook(vault: Path) -> None:
    run_checked(
        [
            "zk",
            "--notebook-dir",
            str(vault),
            "--working-dir",
            str(vault),
            "index",
            "--quiet",
        ],
        cwd=vault,
    )


def write_new_file(path: Path, content: str) -> None:
    assert not path.exists(), f"refusing to overwrite {path}"
    path.write_text(content, encoding="utf-8")


def install_project_agent_state_links(git_root: Path, project_dir: Path) -> None:
    for name in PROJECT_AGENT_STATE_DIRECTORIES:
        install_project_agent_state_link(git_root, project_dir, name)


def install_project_agent_state_link(git_root: Path, project_dir: Path, name: str) -> None:
    repo_path = git_root / name
    vault_path = project_dir
    if repo_path.is_symlink():
        assert repo_path.resolve() == vault_path.resolve(), f"refusing to replace foreign symlink {repo_path}"
        return
    if repo_path.exists():
        assert repo_path.is_dir(), f"refusing to replace non-directory {repo_path}"
        migrate_directory_contents(repo_path, vault_path)
        repo_path.rmdir()
    repo_path.symlink_to(vault_path, target_is_directory=True)


def migrate_directory_contents(source: Path, destination: Path) -> None:
    for child in tuple(source.iterdir()):
        target = destination / child.name
        if child.is_dir() and not child.is_symlink() and target.is_dir() and not target.is_symlink():
            migrate_directory_contents(child, target)
            child.rmdir()
            continue
        assert not target.exists() and not target.is_symlink(), f"refusing to overwrite migrated path {target}"
        shutil.move(str(child), str(target))


def describe_non_symlink_path(path: Path) -> str:
    if not path.exists():
        return "missing"
    if path.is_dir():
        return "a regular directory"
    if path.is_file():
        return "a regular file"
    return "not a symlink"


def project_agent_state_records(git_root: Path, project_dir: Path) -> list[JsonValue]:
    records: list[JsonValue] = []
    for name in PROJECT_AGENT_STATE_DIRECTORIES:
        repo_path = git_root / name
        vault_path = project_dir
        issues: list[JsonValue] = []
        if not vault_path.is_dir():
            issues.append(f"vault project directory is missing: {vault_path}")
        if not repo_path.is_symlink():
            issues.append(f"{repo_path} must be a symlink into the vault project, but it is {describe_non_symlink_path(repo_path)}")
        elif repo_path.resolve() != vault_path.resolve():
            issues.append(f"{repo_path} points outside the vault project: it resolves to {repo_path.resolve()}")
        records.append(
            {
                "name": name,
                "repo_path": str(repo_path),
                "vault_path": str(vault_path),
                "ok": not issues,
                "issues": issues,
            }
        )
    return records


def agents_pointer_section(vault: Path, project_id: str) -> str:
    add_examples = "".join(f"agent-memory add --scope project --type {memory_type.value} --title <title> --content <content>\n" for memory_type in MemoryType)
    return (
        f"{AGENTS_SECTION_START}\n"
        "# Agent memory\n\n"
        f"This repository uses the central agent memory vault at `{vault}`.\n\n"
        f"Project memory key: `projects/{project_id}/index`.\n\n"
        "Repository `.agents` and `.hermes` paths are symlinks to the same vault-owned project directory.\n\n"
        "Before changing architecture, search both project and global memory:\n\n"
        "```bash\n"
        'agent-memory search --scope both "<task or subsystem>"\n'
        "```\n\n"
        "Record durable repo-specific lessons with:\n\n"
        "```bash\n"
        f"{add_examples}"
        "```\n\n"
        "Use `agent-memory retrieve <key>`, `agent-memory update <key>`, and `agent-memory delete <key>` for memory CRUD.\n\n"
        "The vault should be committed at all times. Treat staged or unstaged vault changes as an ephemeral error state. "
        f"Before normal memory work resumes, load the bundled vault-maintenance skill with `{VAULT_MAINTENANCE_SKILL_COMMAND}` "
        "and follow its referenced check, repair, and commit workflows.\n\n"
        "Move reusable lessons during maintenance with:\n\n"
        "```bash\n"
        "agent-memory maintain move <key> --to global/advice\n"
        "```\n"
        f"{AGENTS_SECTION_END}\n"
    )


def write_agents_pointer(project_root: Path, vault: Path, project_id: str) -> None:
    agents_path = project_root / "AGENTS.md"
    section = agents_pointer_section(vault, project_id)
    if not agents_path.exists():
        write_new_file(agents_path, section)
        return

    existing = agents_path.read_text(encoding="utf-8")
    has_start = AGENTS_SECTION_START in existing
    has_end = AGENTS_SECTION_END in existing
    assert has_start == has_end, f"malformed agent-memory AGENTS section in {agents_path}"
    if has_start:
        prefix, marked = existing.split(AGENTS_SECTION_START, 1)
        _, suffix = marked.split(AGENTS_SECTION_END, 1)
        agents_path.write_text(f"{prefix}{section}{suffix}", encoding="utf-8")
        return

    separator = "\n\n" if existing.strip() else ""
    agents_path.write_text(f"{existing.rstrip()}{separator}{section}", encoding="utf-8")


def write_section_indexes(root: Path, sections: Sequence[str]) -> None:
    for section in sections:
        write_new_file(
            root / section / "index.md",
            render_memory({"okf_version": OKF_VERSION}, leaf_index_body(section_title(section))),
        )


def parent_index_body(title: str, entries: Sequence[IndexEntry]) -> str:
    assert entries, "parent index must include at least one child"
    links = "\n\n".join(okf_index_entry(*entry) for entry in entries)
    return f"# {title}\n\n# Subdirectories\n\n{links}\n"


def leaf_index_body(title: str) -> str:
    return f"# {title}\n\n# Concepts\n"


def section_title(section: str) -> str:
    return section.replace("-", " ").title()


def directory_index_entries(
    children: Sequence[str],
    descriptions: dict[str, str],
) -> list[IndexEntry]:
    return [(section_title(child), f"{child}/index.md", descriptions[child]) for child in children]


def okf_index_entry(title: str, target: str, description: str) -> str:
    assert target.endswith(".md"), "OKF index links must target markdown files"
    return f"* [{title}]({target}) - {description}"


def okf_timestamp() -> str:
    return f"{date.today().isoformat()}T00:00:00Z"


def okf_description(content: str) -> str:
    content_lines = content.strip().splitlines()
    assert content_lines, "note content must provide an OKF description"
    return content_lines[0]


def okf_tags(
    scope: MemoryScope,
    memory_type: MemoryType,
    extra_tags: Sequence[str],
) -> list[str]:
    return [scope.value, memory_type.value, *extra_tags]


def append_index_link(index_path: Path, title: str, target: str, description: str) -> None:
    assert index_path.is_file(), "parent index must exist before linking"
    with index_path.open("a", encoding="utf-8") as index_file:
        index_file.write("\n" + okf_index_entry(title, target, description) + "\n")


def locate_index_link(index_path: Path, title: str) -> tuple[list[str], int]:
    assert index_path.is_file(), "index must exist before editing a link"
    # IWE rewrites the OKF bullet marker to "-" when it renames linked notes, so an
    # entry may start with either bullet. This is the single owner of that contract.
    link_prefixes = (f"* [{title}](", f"- [{title}](")
    lines = index_path.read_text(encoding="utf-8").splitlines()
    matching_indexes = [index for index, line in enumerate(lines) if any(line.startswith(prefix) for prefix in link_prefixes)]
    assert len(matching_indexes) == 1, "index must contain exactly one link for the title"
    return lines, matching_indexes[0]


def replace_index_link(index_path: Path, existing_title: str, new_title: str, target: str, description: str) -> None:
    lines, entry_start = locate_index_link(index_path, existing_title)
    lines[entry_start] = okf_index_entry(new_title, target, description)
    index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def remove_index_link(index_path: Path, title: str) -> None:
    lines, entry_start = locate_index_link(index_path, title)
    del lines[entry_start]
    index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def remove_index_link_by_target(index_path: Path, target: str) -> None:
    if not index_path.is_file():
        return
    lines = index_path.read_text(encoding="utf-8").splitlines()
    matching_indexes = []
    for index, line in enumerate(lines):
        striped = line.strip()
        if (striped.startswith("* [") or striped.startswith("- [")) and f"]({target})" in striped:
            matching_indexes.append(index)
    if matching_indexes:
        for idx in reversed(matching_indexes):
            del lines[idx]
        index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def metadata_string(metadata: dict[str, MetadataValue], key: str, path: Path) -> str:
    if key not in metadata:
        raise MalformedMemoryError(path, f"frontmatter missing required field: {key}")
    value = metadata[key]
    if not isinstance(value, str):
        raise MalformedMemoryError(path, f"frontmatter field {key} must be a string")
    return value


def metadata_string_optional(metadata: dict[str, MetadataValue], key: str, path: Path) -> str | None:
    if key not in metadata:
        return None
    value = metadata[key]
    if not isinstance(value, str):
        raise MalformedMemoryError(path, f"frontmatter field {key} must be a string")
    # An empty string reads as ABSENT: missing and empty are both "no value", so a blank
    # optional field never reaches a consumer (e.g. parse_memory_timestamp) as "".
    if value == "":
        return None
    return value


def updated_memory_body(current_body: str, title: str, content: str | None) -> str:
    if content is not None:
        return f"# {title}\n\n{content}\n"
    lines = current_body.splitlines(keepends=True)
    assert lines, "memory body must not be empty"
    assert lines[0].startswith("# "), "memory body must begin with a level-one title"
    lines[0] = f"# {title}\n"
    return "".join(lines)


def git_root_for(cwd: Path) -> Path:
    result = run_checked(["git", "rev-parse", "--show-toplevel"], cwd=cwd)
    root = Path(result.stdout.strip())
    assert root.is_dir(), "git root must be a directory"
    return root


def git_remote(git_root: Path) -> str:
    result = run_checked(["git", "remote", "get-url", "origin"], cwd=git_root)
    remote = result.stdout.strip()
    assert remote, "git origin remote must be configured"
    return remote


def project_id_from_remote(remote: str) -> str:
    stripped = remote.removesuffix(".git")
    is_ssh_remote = stripped.startswith("git@github.com:")
    is_https_remote = stripped.startswith("https://github.com/")
    assert is_ssh_remote or is_https_remote, f"unsupported remote URL: {remote}"
    repository = stripped.removeprefix("git@github.com:") if is_ssh_remote else stripped.removeprefix("https://github.com/")
    parts = repository.split("/")
    assert len(parts) == 2, "GitHub remote must have owner and repository"
    owner, repo = parts
    assert owner and repo, "GitHub remote owner and repository must be nonempty"
    return f"github.com__{owner}__{repo}"


def validate_project_id(project_id: str) -> str:
    assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", project_id), f"invalid project id: {project_id}"
    return project_id


def config_from_agent_state_link(git_root: Path) -> ProjectConfig | None:
    starter = starter_config()
    linked_project_dirs: list[Path] = []
    for name in PROJECT_AGENT_STATE_DIRECTORIES:
        repo_path = git_root / name
        if repo_path.is_symlink():
            linked_project_dirs.append(repo_path.resolve())
    if not linked_project_dirs:
        return None
    project_dir = linked_project_dirs[0]
    assert all(path == project_dir for path in linked_project_dirs), "agent state links must point at one project directory"
    assert project_dir.parent.name == "projects", "agent state link must point at vault projects directory"
    vault = project_dir.parent.parent
    project_id = validate_project_id(project_dir.name)
    assert (vault / ".agents" / "memories" / "config.toml").is_file(), "agent state link must point inside an initialized vault"
    return ProjectConfig(
        vault=normalize_vault_path(vault),
        project_id=project_id,
        project_root_strategy="git-root",
        global_scopes=starter.global_scopes,
        search_max_results=starter.search_max_results,
        search_max_tokens=starter.search_max_tokens,
    )


def find_project_config(cwd: Path) -> ProjectConfig | None:
    # Return the cwd repo's vault-backed project binding, or None when the directory is
    # not a git repo or lacks the agent-state symlink installed by init project.
    try:
        git_root = git_root_for(cwd)
    except subprocess.CalledProcessError:
        return None
    return config_from_agent_state_link(git_root)


def load_project_config(cwd: Path) -> ProjectConfig:
    config = find_project_config(cwd)
    if config is None:
        raise ProjectNotInitializedError(starter_config().default_vault)
    return config


def require_project_id(config: ProjectConfig) -> str:
    # Project-scoped paths read project_id through here, so a global-only config (whose
    # project_id is None) fails loud if it ever reaches project code instead of silently
    # composing a wrong vault path.
    assert config.project_id is not None, "operation requires a bound project; global-only config has no project_id"
    return config.project_id


def global_vault_path() -> Path:
    # The vault a scope-global operation targets when the cwd is unbound. Resolved from
    # AGENT_MEMORY_VAULT when set, else the shipped default vault. Independent of any cwd
    # project binding (issue #25).
    override = os.environ.get("AGENT_MEMORY_VAULT")
    if override is not None:
        assert override, "AGENT_MEMORY_VAULT must not be empty when set"
        return normalize_vault_path(Path(override))
    return starter_config().default_vault


def global_only_config() -> ProjectConfig:
    # Config for operations whose scope is global only. The global vault is the known
    # location, so no cwd project binding is required; project_id stays None because the
    # global scope root never reads it.
    starter = starter_config()
    vault = global_vault_path()
    if not (vault / ".agents" / "memories" / "config.toml").is_file():
        raise GlobalVaultNotInitializedError(vault)
    return ProjectConfig(
        vault=vault,
        project_id=None,
        project_root_strategy="git-root",
        global_scopes=starter.global_scopes,
        search_max_results=starter.search_max_results,
        search_max_tokens=starter.search_max_tokens,
    )


def config_for_schema_advertisement(cwd: Path) -> ProjectConfig | None:
    # The vault whose card schema `inspect schema` should advertise: the cwd's bound
    # project when bound, else the configured global vault when it is actually
    # initialized. When no vault is configured+initialized, return None so the caller
    # advertises the packaged defaults -- the honest answer for a genuinely
    # unconfigured state. That state is selected here by the explicit initialized-vault
    # query, never by catching GlobalVaultNotInitializedError. Any other error (e.g. an
    # empty AGENT_MEMORY_VAULT) still propagates loudly from global_vault_path.
    config = find_project_config(cwd)
    if config is not None:
        return config
    vault = global_vault_path()
    if not (vault / ".agents" / "memories" / "config.toml").is_file():
        return None
    return global_only_config()


def config_for_memory_scope(scope: MemoryScope, cwd: Path) -> ProjectConfig:
    # A bound repo's configured vault is authoritative for every scope, including global,
    # since its global memory lives in that same vault. Only an unbound directory falls
    # back to the standalone global vault, and only for a global write (issue #25).
    config = find_project_config(cwd)
    if config is not None:
        return config
    if scope is MemoryScope.GLOBAL:
        return global_only_config()
    raise ProjectNotInitializedError(starter_config().default_vault)


def config_for_search_scope(scope: SearchScope, cwd: Path) -> ProjectConfig:
    # As above: prefer the cwd binding for any scope. An unbound directory can still run a
    # global-only search against the standalone global vault; project and both need a
    # binding because both reaches project memory (issue #25).
    config = find_project_config(cwd)
    if config is not None:
        return config
    if scope is SearchScope.GLOBAL:
        return global_only_config()
    raise ProjectNotInitializedError(starter_config().default_vault)


def append_project_record(projects_file: Path, record: ProjectRecord) -> None:
    records = load_project_records(projects_file)
    existing = next((r for r in records if r["project_id"] == record["project_id"]), None)
    if existing is not None:
        existing["root"] = record["root"]
        existing["remote"] = record["remote"]
    else:
        records.append(record)
    projects_file.write_text(tomli_w.dumps({"projects": records}), encoding="utf-8")


def load_project_records(projects_file: Path) -> list[ProjectRecord]:
    raw = tomllib.loads(projects_file.read_text(encoding="utf-8"))
    projects = raw["projects"]
    assert isinstance(projects, list), "project index must contain a projects list"
    records: list[ProjectRecord] = []
    for project in projects:
        assert isinstance(project, dict), "project index entries must be tables"
        project_id = project["project_id"]
        root = project["root"]
        remote = project["remote"]
        assert isinstance(project_id, str)
        assert isinstance(root, str)
        assert isinstance(remote, str)
        records.append({"project_id": project_id, "root": root, "remote": remote})
    return records


def scope_root(config: ProjectConfig, scope: MemoryScope) -> Path:
    # Branch instead of building both paths eagerly: the global root needs no project_id,
    # so a global-only config (project_id is None) must not touch the project path.
    if scope is MemoryScope.GLOBAL:
        return config.vault / "global"
    assert scope is MemoryScope.PROJECT, f"unsupported memory scope: {scope}"
    return config.vault / "projects" / require_project_id(config)


# Search scopes resolve to one or both memory scopes. The order for BOTH is a contract:
# content search (search_roots) walks project-then-global, while inspection
# (inspect_root_paths) reports global-then-project so that overview/tree roots list the
# shared global vault first. Parameterizing the order keeps that difference explicit
# instead of forking the dispatch table four ways.
def search_scope_memory_scopes(scope: SearchScope, *, both_order: tuple[MemoryScope, MemoryScope]) -> tuple[MemoryScope, ...]:
    scopes = {
        SearchScope.PROJECT: (MemoryScope.PROJECT,),
        SearchScope.GLOBAL: (MemoryScope.GLOBAL,),
        SearchScope.BOTH: both_order,
    }
    return scopes[scope]


CONTENT_SCOPE_ORDER: tuple[MemoryScope, MemoryScope] = (MemoryScope.PROJECT, MemoryScope.GLOBAL)
INSPECT_SCOPE_ORDER: tuple[MemoryScope, MemoryScope] = (MemoryScope.GLOBAL, MemoryScope.PROJECT)


def memory_directory(config: ProjectConfig, scope: MemoryScope, memory_type: MemoryType) -> Path:
    return scope_root(config, scope) / MEMORY_TYPE_DIRECTORIES[memory_type]


def note_metadata(
    config: ProjectConfig,
    scope: MemoryScope,
    memory_type: MemoryType,
    title: str,
    description: str,
) -> dict[str, MetadataValue]:
    timestamp = okf_timestamp()
    if scope is MemoryScope.PROJECT:
        return ProjectNoteMetadata(
            type=memory_type,
            title=title,
            description=description,
            tags=okf_tags(scope, memory_type, ()),
            timestamp=timestamp,
            scope=MemoryScope.PROJECT,
            project_id=require_project_id(config),
        ).to_yaml_payload()
    assert scope is MemoryScope.GLOBAL, f"unsupported note scope: {scope}"
    return GlobalNoteMetadata(
        type=memory_type,
        title=title,
        description=description,
        tags=okf_tags(scope, memory_type, ()),
        timestamp=timestamp,
        scope=MemoryScope.GLOBAL,
    ).to_yaml_payload()


def run_ripgrep_search(args: Sequence[str], cwd: Path) -> str:
    result = subprocess.run(args, cwd=cwd, check=False, text=True, capture_output=True)
    if result.returncode == 0:
        return result.stdout
    assert result.returncode == 1, f"ripgrep search failed with exit code {result.returncode}: {result.stderr}"
    return result.stdout


def search_roots(config: ProjectConfig, scope: SearchScope) -> tuple[Path, ...]:
    return tuple(scope_root(config, memory_scope) for memory_scope in search_scope_memory_scopes(scope, both_order=CONTENT_SCOPE_ORDER))


def memory_key(vault: Path, path: Path) -> str:
    return path.relative_to(vault).with_suffix("").as_posix()


def memory_files(config: ProjectConfig, scope: SearchScope) -> tuple[Path, ...]:
    return tuple(path for directory in memory_note_directories(config, scope) for path in sorted(directory.glob("*.md")) if path.name not in ("index.md", PLAN_DAG_FILENAME))


def memory_note_directories(config: ProjectConfig, scope: SearchScope) -> tuple[Path, ...]:
    scope_order = search_scope_memory_scopes(scope, both_order=CONTENT_SCOPE_ORDER)
    return tuple(memory_directory(config, memory_scope, memory_type) for memory_scope in scope_order for memory_type in MemoryType)


def inspect_note_records(config: ProjectConfig, scope: SearchScope) -> tuple[NoteRecord, ...]:
    return tuple(note_record_for_path(config, path) for path in memory_files(config, scope))


def note_record_for_path(config: ProjectConfig, path: Path) -> NoteRecord:
    document = read_memory(path)
    stored_scope = MemoryScope(metadata_string(document.metadata, "scope", path))
    layout_scope = MemoryScope(inspect_scope_for_path(config, path))
    assert stored_scope is layout_scope, "memory note metadata scope must match vault layout"
    tags = document.metadata.get("tags")
    if tags is None:
        raise MalformedMemoryError(path, "frontmatter must include tags")
    if not isinstance(tags, list):
        raise MalformedMemoryError(path, "frontmatter tags must be a list")
    # read_memory already guarantees every list item is a string.
    return NoteRecord(
        key=memory_key(config.vault, path),
        path=path,
        title=metadata_string(document.metadata, "title", path),
        memory_type=MemoryType(metadata_string(document.metadata, "type", path)),
        scope=stored_scope,
        tags=tuple(tags),
        timestamp=metadata_string_optional(document.metadata, "timestamp", path),
        document=document,
    )


def note_record_matches_metadata(
    record: NoteRecord,
    memory_type: MemoryType | None,
    tag: str | None,
    created_after: datetime | None,
) -> bool:
    return (
        (memory_type is None or record.memory_type is memory_type)
        and (tag is None or tag in record.tags)
        and (created_after is None or (record.timestamp is not None and parse_memory_timestamp(record.timestamp) > created_after))
    )


def note_record_core(record: NoteRecord) -> JsonObject:
    return {
        "key": record.key,
        "path": str(record.path),
        "title": record.title,
        "type": record.memory_type.value,
    }


def note_record_json(record: NoteRecord) -> JsonObject:
    return {
        **note_record_core(record),
        "scope": record.scope.value,
        "tags": json_list(record.tags),
        "timestamp": record.timestamp,
    }


def metadata_search_record_json(record: NoteRecord) -> JsonObject:
    return {
        **note_record_core(record),
        "tags": json_list(record.tags),
        "timestamp": record.timestamp,
    }


def note_path_record_json(record: NoteRecord) -> JsonObject:
    return {
        **note_record_core(record),
        "scope": record.scope.value,
    }


def parse_created_after(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None, "created-after must include timezone information"
    return parsed


def parse_memory_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None, "memory timestamp must include timezone information"
    return parsed


def write_new_memory(path: Path, metadata: dict[str, MetadataValue], body: str) -> None:
    write_new_file(path, render_memory(metadata, body))


def write_memory(path: Path, metadata: dict[str, MetadataValue], body: str) -> None:
    path.write_text(render_memory(metadata, body), encoding="utf-8")


def render_memory(metadata: dict[str, MetadataValue], body: str) -> str:
    frontmatter = yaml.safe_dump(metadata, sort_keys=False)
    return f"---\n{frontmatter}---\n{body}"


def read_memory(path: Path) -> MemoryDocument:
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("---"):
        raise MalformedMemoryError(path, "memory must start with frontmatter")
    try:
        document = frontmatter.loads(raw)
    except (ValueError, yaml.YAMLError) as e:
        raise MalformedMemoryError(path, "frontmatter must be valid YAML") from e
    parsed = document.metadata
    body = document.content
    if not isinstance(parsed, dict):
        raise MalformedMemoryError(path, "frontmatter must be a mapping")
    metadata: dict[str, MetadataValue] = {}
    for key, value in parsed.items():
        if not isinstance(key, str):
            raise MalformedMemoryError(path, "frontmatter keys must be strings")
        if isinstance(value, datetime):
            if key != "timestamp":
                raise MalformedMemoryError(path, "only timestamp may be parsed as a YAML datetime")
            if value.tzinfo is None:
                raise MalformedMemoryError(path, "timestamp must include timezone information")
            metadata[key] = value.isoformat().replace("+00:00", "Z")
        elif isinstance(value, list):
            if not all(isinstance(item, str) for item in value):
                raise MalformedMemoryError(path, "frontmatter lists must contain strings")
            metadata[key] = value
        else:
            if not isinstance(value, str | bool):
                raise MalformedMemoryError(path, "frontmatter values must be strings, booleans, datetimes, or string lists")
            metadata[key] = value
    return MemoryDocument(metadata=metadata, body=body)


def inspect_overview(
    *,
    scope: SearchScope,
    output_format: InspectOutputFormat,
    cwd: Path,
) -> JsonObject:
    assert output_format is InspectOutputFormat.JSON, "inspect overview currently emits JSON"
    config = load_project_config(cwd)
    notes = inspect_note_records(config, scope)
    indexes = inspect_index_records(config, scope)
    return {
        "vault": str(config.vault),
        "project_id": require_project_id(config),
        "scope": scope.value,
        "roots": json_list(inspect_root_keys(config, scope)),
        "totals": {
            "notes": len(notes),
            "indexes": len(indexes),
        },
        "notes_by_scope": inspect_counts([note.scope.value for note in notes]),
        "notes_by_type": inspect_counts([note.memory_type.value for note in notes]),
    }


def inspect_schema(*, output_format: InspectOutputFormat) -> JsonObject:
    assert output_format is InspectOutputFormat.JSON, "inspect schema currently emits JSON"
    config = config_for_schema_advertisement(Path.cwd())
    cards_config, card_model_by_type = load_card_system(config)
    return {
        "commands": {"inspect": list(INSPECT_COMMAND_NAMES)},
        "scopes": [scope.value for scope in SearchScope],
        "memory_types": [memory_type.value for memory_type in MemoryType],
        "path_kinds": [kind.value for kind in InspectPathKind],
        "link_directions": [direction.value for direction in InspectLinkDirection],
        "stats_groups": [group.value for group in InspectStatsGroup],
        "export_profiles": [profile.value for profile in InspectExportProfile],
        "formats": {
            "inspect": json_list([InspectOutputFormat.JSON.value]),
            "export": json_list([InspectExportFormat.GRAPH_JSON.value]),
        },
        "card_system": {
            "root": cards_config.root,
            "status_count": len(cards_config.statuses),
            "type_count": len(cards_config.card_types),
            "status_sets": {
                status_set_name: {
                    "default": status_set.default,
                    "options": json_list(status_set.options),
                }
                for status_set_name, status_set in cards_config.status_sets.items()
            },
            "types": [
                {
                    "name": card_type.name,
                    "id_prefix": card_type.id_prefix,
                    "status_set": card_type.status_set,
                    "parents": json_list(card_type.parents),
                    "container": card_type.container,
                    "own_dir": card_type.own_dir,
                    "required_fields": json_list([field.name for field in card_type.fields if field.required]),
                    "field_count": len(card_type.fields),
                }
                for card_type in cards_config.card_types
            ],
            "models": json_list(sorted(card_model_by_type.keys())),
        },
        "metadata_fields": list(ProjectNoteMetadata.model_fields),
    }


def inspect_paths(
    *,
    scope: SearchScope,
    kind: InspectPathKind,
    output_format: InspectOutputFormat,
    cwd: Path,
) -> JsonObject:
    assert output_format is InspectOutputFormat.JSON, "inspect paths currently emits JSON"
    config = load_project_config(cwd)
    root_records = inspect_root_records(config, scope)
    index_records = inspect_index_records(config, scope)
    note_records = inspect_path_note_records(config, scope)
    records_by_kind = {
        InspectPathKind.ROOTS: root_records,
        InspectPathKind.INDEXES: index_records,
        InspectPathKind.NOTES: note_records,
        InspectPathKind.ALL: (*root_records, *index_records, *note_records),
    }
    return {
        "scope": scope.value,
        "kind": kind.value,
        "paths": json_list(records_by_kind[kind]),
    }


def inspect_tree(
    *,
    scope: SearchScope,
    depth: int,
    output_format: InspectOutputFormat,
    cwd: Path,
) -> JsonObject:
    assert output_format is InspectOutputFormat.JSON, "inspect tree currently emits JSON"
    assert depth >= 0, "inspect tree depth must be nonnegative"
    config = load_project_config(cwd)
    roots = [inspect_tree_node(config, key, depth) for key in inspect_root_keys(config, scope)]
    return {"scope": scope.value, "depth": depth, "roots": json_list(roots)}


def inspect_links(
    *,
    key: str,
    direction: InspectLinkDirection,
    depth: int,
    output_format: InspectOutputFormat,
    cwd: Path,
) -> JsonObject:
    assert output_format is InspectOutputFormat.JSON, "inspect links currently emits JSON"
    assert depth >= 0, "inspect links depth must be nonnegative"
    config = load_project_config(cwd)
    path = memory_path_for_key(config, key)
    records = link_records_for_direction(config, path, depth, direction)
    return {
        "key": key,
        "direction": direction.value,
        "depth": depth,
        "links": json_list([link_record_json(record) for record in records]),
    }


def inspect_broken_links(
    *,
    scope: SearchScope,
    output_format: InspectOutputFormat,
    cwd: Path,
) -> JsonObject:
    assert output_format is InspectOutputFormat.JSON, "inspect links --broken currently emits JSON"
    config = load_project_config(cwd)
    records = broken_wikilink_records(config, scope)
    return {
        "scope": scope.value,
        "broken_links": json_list(records),
    }


def inspect_outline(
    *,
    key: str,
    output_format: InspectOutputFormat,
    cwd: Path,
) -> JsonObject:
    assert output_format is InspectOutputFormat.JSON, "inspect outline currently emits JSON"
    config = load_project_config(cwd)
    path = memory_path_for_key(config, key)
    document = read_memory(path)
    return {
        "key": key,
        "path": str(path),
        "headings": json_list(markdown_headings(document.body)),
    }


def inspect_stats(
    *,
    scope: SearchScope,
    group: InspectStatsGroup,
    output_format: InspectOutputFormat,
    cwd: Path,
) -> JsonObject:
    assert output_format is InspectOutputFormat.JSON, "inspect stats currently emits JSON"
    config = load_project_config(cwd)
    notes = inspect_note_records(config, scope)
    counts_by_group = {
        InspectStatsGroup.TYPE: inspect_counts([note.memory_type.value for note in notes]),
        InspectStatsGroup.SCOPE: inspect_counts([note.scope.value for note in notes]),
        InspectStatsGroup.DAY: inspect_day_counts(notes),
    }
    return {"scope": scope.value, "by": group.value, "counts": counts_by_group[group]}


def inspect_recent(
    *,
    scope: SearchScope,
    since: str,
    output_format: InspectOutputFormat,
    cwd: Path,
) -> JsonObject:
    assert output_format is InspectOutputFormat.JSON, "inspect recent currently emits JSON"
    since_datetime = parse_memory_timestamp(since)
    config = load_project_config(cwd)
    records = [record for record in inspect_note_records(config, scope) if record.timestamp is not None and parse_memory_timestamp(record.timestamp) > since_datetime]
    records.sort(key=lambda record: record.timestamp or "", reverse=True)
    return {"scope": scope.value, "since": since, "results": json_list([note_record_json(record) for record in records])}


def inspect_export(
    *,
    scope: SearchScope,
    profile: InspectExportProfile,
    output_format: InspectExportFormat,
    cwd: Path,
) -> JsonObject:
    assert output_format is InspectExportFormat.GRAPH_JSON, "inspect export currently emits graph-json"
    config = load_project_config(cwd)
    paths = inspect_markdown_paths(config, scope)
    path_by_key = {memory_key(config.vault, path): path for path in paths}
    nodes = [inspect_export_node(config, key, path, profile) for key, path in sorted(path_by_key.items())]
    edges: list[JsonObject] = []
    for key, path in sorted(path_by_key.items()):
        for target in outgoing_link_keys(config, path):
            if target in path_by_key:
                edges.append({"source": key, "target": target})
    return {
        "scope": scope.value,
        "profile": profile.value,
        "format": output_format.value,
        "nodes": json_list(nodes),
        "edges": json_list(edges),
    }


def inspect_root_paths(config: ProjectConfig, scope: SearchScope) -> tuple[Path, ...]:
    return tuple(scope_root(config, memory_scope) for memory_scope in search_scope_memory_scopes(scope, both_order=INSPECT_SCOPE_ORDER))


def inspect_root_keys(config: ProjectConfig, scope: SearchScope) -> tuple[str, ...]:
    return tuple(memory_key(config.vault, root / "index.md") for root in inspect_root_paths(config, scope))


def inspect_root_records(config: ProjectConfig, scope: SearchScope) -> tuple[JsonObject, ...]:
    return tuple(
        {
            "key": memory_key(config.vault, root / "index.md"),
            "path": str(root / "index.md"),
            "scope": inspect_scope_for_path(config, root / "index.md"),
        }
        for root in inspect_root_paths(config, scope)
    )


def inspect_markdown_paths(config: ProjectConfig, scope: SearchScope) -> tuple[Path, ...]:
    paths: list[Path] = []
    for root in inspect_root_paths(config, scope):
        for path in sorted(root.rglob("*.md")):
            paths.append(path)
    return tuple(paths)


def inspect_index_records(config: ProjectConfig, scope: SearchScope) -> tuple[JsonObject, ...]:
    records: list[JsonObject] = []
    for path in inspect_markdown_paths(config, scope):
        if path.name != "index.md":
            continue
        document = read_memory(path)
        records.append(
            {
                "key": memory_key(config.vault, path),
                "path": str(path),
                "title": first_heading_title(document.body),
                "scope": inspect_scope_for_path(config, path),
            }
        )
    return tuple(records)


def inspect_path_note_records(config: ProjectConfig, scope: SearchScope) -> tuple[JsonObject, ...]:
    return tuple(note_path_record_json(record) for record in inspect_note_records(config, scope))


def inspect_counts(values: Sequence[str]) -> JsonObject:
    counts = Counter(values)
    return {key: counts[key] for key in sorted(counts)}


def inspect_day_counts(records: Sequence[NoteRecord]) -> JsonObject:
    counts = Counter(parse_memory_timestamp(record.timestamp).date().isoformat() for record in records if record.timestamp is not None)
    return {key: counts[key] for key in sorted(counts)}


def inspect_scope_for_path(config: ProjectConfig, path: Path) -> str:
    relative = path.relative_to(config.vault)
    scopes = {
        "global": MemoryScope.GLOBAL.value,
        "projects": MemoryScope.PROJECT.value,
    }
    return scopes[relative.parts[0]]


def memory_path_for_key(config: ProjectConfig, key: str) -> Path:
    path = config.vault / f"{key}.md"
    assert path.is_file(), f"memory key does not exist: {key}"
    return path


def first_heading_title(markdown: str) -> str:
    tokens = MARKDOWN_PARSER.parse(markdown)
    for index, token in enumerate(tokens):
        if token.type != "heading_open" or token.tag != "h1":
            continue
        assert index + 1 < len(tokens), "markdown heading must have body content"
        body = tokens[index + 1]
        assert body.type == "inline", "markdown heading body must be inline"
        title = body.content.strip()
        assert title, "heading title must be nonempty"
        return title
    assert False, "markdown document must contain a top-level heading"


def markdown_headings(markdown: str) -> tuple[JsonObject, ...]:
    tokens = MARKDOWN_PARSER.parse(markdown)
    headings: list[JsonObject] = []
    for index, token in enumerate(tokens):
        if token.type != "heading_open":
            continue
        assert token.tag.startswith("h"), f"unexpected heading tag: {token.tag}"
        level = int(token.tag[1:])
        assert 1 <= level <= 6, "markdown heading level must be between 1 and 6"
        assert index + 1 < len(tokens), "markdown heading must have body content"
        body = tokens[index + 1]
        assert body.type == "inline", "markdown heading body must be inline"
        title = body.content.strip()
        assert title, "markdown heading title must be nonempty"
        assert token.map is not None, "markdown heading must provide source map"
        line_number = token.map[0] + 1
        headings.append({"level": level, "title": title, "line": line_number})
    return tuple(headings)


def _markdown_link_target(link_token: object) -> str | None:
    href = None
    if hasattr(link_token, "attrs"):
        attrs = getattr(link_token, "attrs")
        if isinstance(attrs, dict):
            href = attrs.get("href")
        elif isinstance(attrs, list):
            attrs = dict(attrs)
            href = attrs.get("href")
    if href is None:
        return None
    assert isinstance(href, str), "markdown link target must be text"
    return href


def outgoing_link_keys(config: ProjectConfig, path: Path) -> tuple[str, ...]:
    markdown = path.read_text(encoding="utf-8")
    tokens = MARKDOWN_PARSER.parse(markdown)
    keys: list[str] = []
    for token in tokens:
        if token.type != "inline":
            continue
        for child in token.children or []:
            if child.type != "link_open":
                continue
            href = _markdown_link_target(child)
            if href is None:
                continue
            target = href.split("#", 1)[0]
            if not target:
                continue
            # outgoing_link_keys owns intra-vault note-to-note edges only. The markdown-it
            # walk yields every link (external URLs, autolinks, reference-style, non-.md);
            # a target that is not a vault-relative .md file is simply not an outgoing vault
            # edge, so skip it by contract. This is a membership test, not error handling.
            if not target.endswith(".md"):
                continue
            target_path = (path.parent / target).resolve()
            vault = config.vault.resolve()
            if not target_path.is_relative_to(vault):
                continue
            keys.append(target_path.relative_to(vault).with_suffix("").as_posix())
    return tuple(keys)


def wikilink_key(raw_target: str) -> str:
    key = raw_target.split("|", 1)[0].split("#", 1)[0].strip()
    assert key, f"wikilink target must not be empty: {raw_target!r}"
    assert not key.startswith(("http://", "https://")), f"wikilink target must be a vault key, not a URL: {raw_target!r}"
    return key


def wikilink_fragment(raw_target: str) -> str | None:
    target = raw_target.split("|", 1)[0].strip()
    if "#" not in target:
        return None
    fragment = " ".join(target.split("#", 1)[1].split())
    assert fragment, f"wikilink fragment must not be empty: {raw_target!r}"
    return fragment


def outgoing_wikilink_keys(path: Path) -> tuple[str, ...]:
    text = path.read_text(encoding="utf-8")
    return tuple(wikilink_key(match.group(1)) for match in WIKILINK_PATTERN.finditer(text))


def wikilink_argument_text(raw_target: str) -> str:
    stripped = raw_target.strip()
    if stripped.startswith("[[") and stripped.endswith("]]"):
        return stripped[2:-2].strip()
    return stripped


def wikilink_argument_key(raw_target: str) -> str:
    return wikilink_key(wikilink_argument_text(raw_target))


def wikilink_argument_fragment(raw_target: str) -> str | None:
    return wikilink_fragment(wikilink_argument_text(raw_target))


def wikilink_argument_target(raw_target: str) -> str:
    key = wikilink_argument_key(raw_target)
    fragment = wikilink_argument_fragment(raw_target)
    if fragment is None:
        return key
    return f"{key}#{fragment}"


def wikilink_replacement(raw_target: str) -> str:
    stripped = raw_target.strip()
    if stripped.startswith(("http://", "https://")):
        return stripped
    return f"[[{wikilink_argument_target(stripped)}]]"


def wikilink_rewrite(from_target: str, to_target: str) -> WikilinkRewrite:
    return WikilinkRewrite(
        from_key=wikilink_argument_key(from_target),
        to_target=to_target,
        replacement=wikilink_replacement(to_target),
        from_fragment=wikilink_argument_fragment(from_target),
    )


def wikilink_target_path(config: ProjectConfig, key: str) -> Path:
    target_path = (config.vault / f"{key}.md").resolve()
    vault = config.vault.resolve()
    assert target_path.is_relative_to(vault), f"wikilink target leaves memory vault: {key}"
    return target_path


def broken_wikilink_records(config: ProjectConfig, scope: SearchScope) -> list[JsonObject]:
    records: list[JsonObject] = []
    for path in inspect_markdown_paths(config, scope):
        source_key = memory_key(config.vault, path)
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            for match in WIKILINK_PATTERN.finditer(line):
                target = wikilink_key(match.group(1))
                target_path = wikilink_target_path(config, target)
                if not target_path.is_file():
                    records.append(
                        {
                            "line": line_number,
                            "source_key": source_key,
                            "source_path": str(path),
                            "target": target,
                            "target_path": str(target_path),
                        }
                    )
    return records


def rewrite_wikilinks_in_text(
    text: str,
    *,
    old_key: str,
    replacement: str,
    old_fragment: str | None = None,
) -> tuple[str, int]:
    replacements = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal replacements
        if wikilink_key(match.group(1)) != old_key:
            return match.group(0)
        if old_fragment is not None and wikilink_fragment(match.group(1)) != old_fragment:
            return match.group(0)
        replacements += 1
        return replacement

    return WIKILINK_PATTERN.sub(replace, text), replacements


def rewrite_wikilink_files(
    config: ProjectConfig,
    rewrites: Sequence[WikilinkRewrite],
    *,
    include_indexes: bool = True,
) -> list[JsonObject]:
    records: list[JsonObject] = []
    for path in inspect_markdown_paths(config, SearchScope.BOTH):
        if not include_indexes and path.name == "index.md":
            continue
        rewritten = path.read_text(encoding="utf-8")
        replacements = 0
        for rewrite in rewrites:
            rewritten, rewrite_replacements = rewrite_wikilinks_in_text(
                rewritten,
                old_key=rewrite.from_key,
                replacement=rewrite.replacement,
                old_fragment=rewrite.from_fragment,
            )
            replacements += rewrite_replacements
        if replacements:
            path.write_text(rewritten, encoding="utf-8")
            records.append({"path": str(path), "replacements": replacements})
    return records


def rewrite_record(rewrite: WikilinkRewrite) -> JsonObject:
    return {"from": rewrite.from_key, "to": rewrite.to_target}


def rewritten_record_paths(records: Sequence[JsonObject]) -> list[Path]:
    paths: list[Path] = []
    for record in records:
        path = record["path"]
        assert isinstance(path, str), f"rewritten record path must be a string: {record}"
        paths.append(Path(path))
    return paths


def rewritten_record_parent_index_paths(records: Sequence[JsonObject]) -> list[Path]:
    return [path.parent / "index.md" for path in rewritten_record_paths(records)]


def wikilink_rewrite_map(map_path: Path) -> tuple[WikilinkRewrite, ...]:
    decoded = tomllib.loads(map_path.read_text(encoding="utf-8"))
    rewrites = decoded["rewrites"]
    assert isinstance(rewrites, dict), f"wikilink rewrite map must contain a [rewrites] table: {map_path}"
    records: list[WikilinkRewrite] = []
    for from_target, to_target in rewrites.items():
        assert isinstance(from_target, str), f"wikilink rewrite source must be a string: {map_path}"
        assert isinstance(to_target, str), f"wikilink rewrite destination must be a string: {map_path}; source={from_target}"
        records.append(wikilink_rewrite(from_target, to_target))
    assert records, f"wikilink rewrite map must contain at least one rewrite: {map_path}"
    return tuple(records)


def rewrite_wikilinks(
    *,
    from_target: str | None,
    to_target: str | None,
    map_path: Path | None,
    cwd: Path,
) -> JsonObject:
    config = load_project_config(cwd)
    if map_path is None:
        assert from_target is not None and to_target is not None, "links rewrite requires --from and --to unless --map is supplied"
        rewrite = wikilink_rewrite(from_target, to_target)
        return {
            "from": rewrite.from_key,
            "to": rewrite.to_target,
            "rewritten": json_list(rewrite_wikilink_files(config, (rewrite,))),
        }
    assert from_target is None and to_target is None, "links rewrite --map cannot be combined with --from or --to"
    rewrites = wikilink_rewrite_map(map_path)
    return {
        "map": str(map_path),
        "rewrites": json_list([rewrite_record(rewrite) for rewrite in rewrites]),
        "rewritten": json_list(rewrite_wikilink_files(config, rewrites)),
    }


def incoming_link_keys(config: ProjectConfig, target_key: str) -> tuple[str, ...]:
    keys: list[str] = []
    for path in inspect_markdown_paths(config, SearchScope.BOTH):
        source_key = memory_key(config.vault, path)
        if source_key == target_key:
            continue
        if target_key in outgoing_link_keys(config, path):
            keys.append(source_key)
    return tuple(sorted(keys))


def non_index_incoming_link_keys(config: ProjectConfig, target_key: str) -> tuple[str, ...]:
    keys: list[str] = []
    for path in inspect_markdown_paths(config, SearchScope.BOTH):
        if path.name == "index.md":
            continue
        source_key = memory_key(config.vault, path)
        if source_key == target_key:
            continue
        if target_key in outgoing_wikilink_keys(path):
            keys.append(source_key)
    return tuple(sorted(keys))


def inspect_tree_node(config: ProjectConfig, key: str, depth: int) -> JsonObject:
    path = memory_path_for_key(config, key)
    document = read_memory(path)
    children: list[JsonObject] = []
    if depth > 0:
        for child_key in outgoing_link_keys(config, path):
            children.append(inspect_tree_node(config, child_key, depth - 1))
    return {
        "key": key,
        "path": str(path),
        "title": inspect_title_for_document(document, path),
        "children": json_list(children),
    }


def inspect_title_for_document(document: MemoryDocument, path: Path) -> str:
    if "title" in document.metadata:
        return metadata_string(document.metadata, "title", path)
    return first_heading_title(document.body)


def link_records_for_direction(
    config: ProjectConfig,
    path: Path,
    depth: int,
    direction: InspectLinkDirection,
) -> tuple[LinkRecord, ...]:
    if direction is InspectLinkDirection.CHILDREN:
        return child_link_records(config, path, depth)
    if direction is InspectLinkDirection.PARENTS:
        return parent_link_records(config, path, depth)
    assert direction is InspectLinkDirection.BOTH, f"unsupported link traversal direction: {direction}"
    return tuple(dedupe_link_records((*parent_link_records(config, path, depth), *child_link_records(config, path, depth))))


def child_link_records(config: ProjectConfig, path: Path, depth: int) -> tuple[LinkRecord, ...]:
    return traverse_link_records(config, path, depth, child_link_keys)


def parent_link_records(config: ProjectConfig, path: Path, depth: int) -> tuple[LinkRecord, ...]:
    return traverse_link_records(config, path, depth, parent_link_keys)


def child_link_keys(config: ProjectConfig, current_key: str) -> tuple[str, ...]:
    return outgoing_link_keys(config, memory_path_for_key(config, current_key))


def parent_link_keys(config: ProjectConfig, current_key: str) -> tuple[str, ...]:
    return incoming_link_keys(config, current_key)


def traverse_link_records(
    config: ProjectConfig,
    path: Path,
    depth: int,
    neighbor_provider: LinkNeighborProvider,
) -> tuple[LinkRecord, ...]:
    if depth == 0:
        return ()
    start_key = memory_key(config.vault, path)
    records: list[LinkRecord] = []
    frontier = [(start_key, 0)]
    seen = {start_key}
    while frontier:
        current_key, current_depth = frontier.pop(0)
        if current_depth == depth:
            continue
        for related_key in neighbor_provider(config, current_key):
            if related_key not in seen:
                seen.add(related_key)
                related_path = memory_path_for_key(config, related_key)
                related_document = read_memory(related_path)
                record_depth = current_depth + 1
                records.append(LinkRecord(related_key, related_path, inspect_title_for_document(related_document, related_path), record_depth))
                frontier.append((related_key, record_depth))
    return tuple(records)


def dedupe_link_records(records: Sequence[LinkRecord]) -> list[LinkRecord]:
    deduped: dict[str, LinkRecord] = {}
    for record in records:
        if record.key not in deduped:
            deduped[record.key] = record
    return [deduped[key] for key in sorted(deduped)]


def link_record_json(record: LinkRecord) -> JsonObject:
    return {
        "key": record.key,
        "path": str(record.path),
        "title": record.title,
        "depth": record.depth,
    }


def inspect_export_node(
    config: ProjectConfig,
    key: str,
    path: Path,
    profile: InspectExportProfile,
) -> JsonObject:
    document = read_memory(path)
    node: JsonObject = {
        "key": key,
        "path": str(path),
        "title": inspect_title_for_document(document, path),
        "scope": inspect_scope_for_path(config, path),
    }
    if "type" in document.metadata:
        node["type"] = metadata_string(document.metadata, "type", path)
    if profile is InspectExportProfile.MAP:
        return node
    if profile is InspectExportProfile.CONTEXT:
        node["content"] = document.body
        return node
    assert profile is InspectExportProfile.ARCHIVE, f"unsupported inspect export profile: {profile}"
    node["metadata"] = {key: json_metadata_value(value) for key, value in sorted(document.metadata.items())}
    node["content"] = document.body
    return node


def json_metadata_value(value: MetadataValue) -> JsonValue:
    if isinstance(value, str | bool):
        return value
    return json_list(value)


# --- Plan cards (issue #4): bridge the config-driven card engine to the project vault ---


def load_card_system(config: ProjectConfig | None = None) -> tuple[CardSystemConfig, dict[str, type[BaseModel]]]:
    project_id = None if config is None else config.project_id
    cards_config = load_card_system_config(config.vault if config is not None else None, project_id)
    return cards_config, build_card_models(cards_config)


def project_plans_root(config: ProjectConfig, cards_config: CardSystemConfig) -> Path:
    return config.vault / "projects" / require_project_id(config) / cards_config.root


def all_plans_roots(config: ProjectConfig, cards_config: CardSystemConfig) -> list[Path]:
    records = load_project_records(config.vault / "_meta" / "projects.toml")
    return [config.vault / "projects" / record["project_id"] / cards_config.root for record in records]


def coerce_scalar_field(field_type: str, value: str) -> object:
    assert field_type not in ("string_list", "wikilink_list"), "list fields must be appended, not coerced"
    if field_type == "int":
        return int(value)
    if field_type == "number":
        return float(value)
    if field_type == "bool":
        return value.lower() in ("true", "1", "yes")
    return value


def append_list_field(fields: dict[str, object], key: str, value: str) -> None:
    if key not in fields:
        fields[key] = []
    bucket = fields[key]
    assert isinstance(bucket, list), "list field accumulator must be a list"
    bucket.append(value)


def parse_card_fields(
    cards_config: CardSystemConfig,
    type_name: str,
    assignments: Sequence[str],
    empty_set: Sequence[str] | None = None,
) -> dict[str, object]:
    spec = next((card_type for card_type in cards_config.card_types if card_type.name == type_name), None)
    assert spec is not None, f"unknown card type: {type_name}"
    field_types = {field.name: field.type for field in spec.fields}
    fields: dict[str, object] = {}
    for assignment in assignments:
        if "=" not in assignment:
            raise CardFieldError(f"field assignment must be key=value: {assignment}")
        key, value = assignment.split("=", 1)
        if key not in field_types:
            raise CardFieldError(f"unknown field {key} for card type {type_name}")
        field_type = field_types[key]
        if field_type in ("string_list", "wikilink_list"):
            append_list_field(fields, key, value)
        else:
            try:
                fields[key] = coerce_scalar_field(field_type, value)
            except ValueError as e:
                if field_type not in ("int", "number"):
                    raise
                raise CardFieldError(f"field {key} expects {field_type} value, got {value}") from e

    if empty_set is not None:
        for key in empty_set:
            if key not in field_types:
                raise CardFieldError(f"unknown field {key} for card type {type_name}")
            fields[key] = []

    return fields


def add_plan_card(
    type_name: str,
    card_id: str,
    parent_id: str | None,
    assignments: Sequence[str],
    body: str,
    cwd: Path,
    empty_set: Sequence[str] | None = None,
) -> JsonObject:
    config = load_project_config(cwd)
    cards_config, models = load_card_system(config)
    fields = parse_card_fields(cards_config, type_name, assignments, empty_set=empty_set)
    path = create_card(
        project_plans_root(config, cards_config),
        cards_config,
        models,
        type_name=type_name,
        card_id=card_id,
        parent_id=parent_id,
        fields=fields,
        body=body,
    )

    try:
        commit_vault_changes(config.vault, f"Add {type_name} card: {card_id}", paths=[path])
    except subprocess.CalledProcessError as e:
        # Rollback!
        run_checked_optional(["git", "reset", "HEAD", "--", str(path.relative_to(config.vault))], cwd=config.vault)
        if path.exists():
            path.unlink()
            # Clean up empty parent directories if created
            parent_dir = path.parent
            while parent_dir != config.vault:
                try:
                    parent_dir.rmdir()
                    parent_dir = parent_dir.parent
                except OSError:
                    break
        git_stderr = e.stderr or ""
        raise VaultCommitError(vault_commit_error_message(git_stderr)) from e

    return {"id": card_id, "path": str(path)}


def update_plan_card(card_id: str, assignments: Sequence[str], cwd: Path) -> JsonObject:
    config = load_project_config(cwd)
    cards_config, models = load_card_system(config)
    type_name = card_type_for_id(cards_config, card_id).name
    updates = parse_card_fields(cards_config, type_name, assignments)
    path = update_card(project_plans_root(config, cards_config), cards_config, models, card_id, updates)
    commit_vault_changes(config.vault, f"Update plan card: {card_id}", paths=[path])
    return {"id": card_id, "path": str(path)}


def delete_plan_card(card_id: str, cwd: Path) -> JsonObject:
    config = load_project_config(cwd)
    cards_config, _models = load_card_system(config)
    plans_root = project_plans_root(config, cards_config)
    path = find_card_path(plans_root, card_id)
    path.unlink()
    commit_vault_changes(config.vault, f"Delete plan card: {card_id}", paths=[path])
    return {"deleted": card_id}


def validate_plan_cards(cwd: Path) -> JsonObject:
    config = load_project_config(cwd)
    cards_config, models = load_card_system(config)
    records = load_card_records(all_plans_roots(config, cards_config), cards_config, models)
    problems = validate_cards(records, cards_config)
    return {"problems": json_list([{"kind": problem.kind, "card": problem.card_id, "detail": problem.detail} for problem in problems])}


def write_plan_dag(cwd: Path) -> JsonObject:
    config = load_project_config(cwd)
    cards_config, models = load_card_system(config)
    records = load_card_records(all_plans_roots(config, cards_config), cards_config, models)
    plans_root = project_plans_root(config, cards_config)
    plans_root.mkdir(parents=True, exist_ok=True)
    path = plans_root / PLAN_DAG_FILENAME
    path.write_text(render_dag(records), encoding="utf-8")
    commit_vault_changes(config.vault, "Update plan DAG", paths=[path])
    return {"path": str(path)}


def migrate_plan_cards(source: Path, cwd: Path) -> JsonObject:
    config = load_project_config(cwd)
    cards_config, models = load_card_system(config)
    paths = migrate_plans(source, project_plans_root(config, cards_config), cards_config, models)
    commit_vault_changes(config.vault, f"Migrate {len(paths)} plan cards", paths=paths)
    return {"migrated": json_list([str(path) for path in paths])}
