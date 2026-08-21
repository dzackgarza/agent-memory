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
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from functools import cache
from importlib import resources
from pathlib import Path
from uuid import uuid4

import frontmatter
import tomli_w
import yaml
from markdown_it import MarkdownIt
from pydantic import BaseModel, ValidationError

from agent_memory import iwe
from agent_memory.cards.config import CardSystemConfig, card_fields
from agent_memory.cards.dag import PLAN_DAG_FILENAMES, plan_dag_filename, render_dag
from agent_memory.cards.factory import build_card_models
from agent_memory.cards.loader import load_card_system_config
from agent_memory.cards.migration import migrate_plans
from agent_memory.cards.storage import UNSET_FIELD, card_type_for_id, create_card, find_card_path, split_card
from agent_memory.cards.storage import update_card as write_card_updates
from agent_memory.cards.validation import CardLoadFinding, CardRecord, CardScan, reference_field_names, scan_card_records, validate_cards, wikilink_ids
from agent_memory.models import (
    ArchiveVisibility,
    BaseNoteMetadata,
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
class DeleteBacklinksBlocked:
    pass


@dataclass(frozen=True)
class DeleteBacklinksOrphaned:
    pass


@dataclass(frozen=True)
class DeleteBacklinksRepointed:
    target: str


type DeleteBacklinkDisposition = DeleteBacklinksBlocked | DeleteBacklinksOrphaned | DeleteBacklinksRepointed


class CardListingSource(Enum):
    MANAGED = "managed"
    MANAGED_AND_UNMIGRATED = "managed_and_unmigrated"


class SyncCommitState(Enum):
    CLEAN = "clean"
    COMMITTED = "committed"


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
BUNDLED_SKILL_NAMES: tuple[str, ...] = ("agent-memory", "vault-maintenance")
VAULT_MAINTENANCE_SKILL_COMMAND = "agent-memory maintain skill vault-maintenance"
VAULT_MAINTENANCE_SKILL_HINT = (
    "\nDispatch a dedicated vault-maintenance subagent for this failure. "
    "It owns inspection, repair, validation, commit, and push of the affected vault paths; "
    "continue the parent task rather than treating vault maintenance as a blocker. "
    f"Use `{VAULT_MAINTENANCE_SKILL_COMMAND}` for the delegated workflow."
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
WRITABLE_MEMORY_TYPES: tuple[MemoryType, ...] = tuple(memory_type for memory_type in MemoryType if memory_type is not MemoryType.PLAN)
PLAN_TODO_STATUSES: frozenset[str] = frozenset(
    (
        "pending",
        "unstarted",
        "approved-and-unstarted",
        "in-progress",
        "needs-agent-review",
        "needs-human-input",
        "revision-required",
        "blocked",
        "complete",
        "decided",
        "implemented",
    )
)
PLAN_TODO_CHILD_KEYS: tuple[str, ...] = ("children", "todos", "tasks")

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
QUEUE_CARD_TYPE = "queue-item"
QUEUE_CARD_ID_PREFIX = "QUEUE"
QUEUE_DIRECTORY = "queue"
# Tag that marks the stub `maintain move` leaves behind so a moved key still resolves.
PROMOTION_POINTER_TAG = "promotion-pointer"
# Ranked search runs on Probe, so Probe is a scoring engine, not just a CLI surface. Pinned
# because an unpinned `@latest` re-resolves per invocation: the ranking could change between
# two searches in one session with nothing in the vault having moved.
#
# Bumping this version is what the payload-shape asserts in probe_results, probe_skipped_files,
# probe_score, json_child and json_int are holding up. They are asserts on purpose -- a shape
# change is a broken pin, not user input -- but they assert against THIS version. Re-run a
# ranked search against a real vault after any bump, and read the failure as "the pin moved".
PROBE_PACKAGE = "@probelabs/probe@0.6.0-rc331"


def index_descriptions(scope: MemoryScope) -> dict[str, str]:
    scope_word = scope.value.capitalize()
    return {MEMORY_TYPE_DIRECTORIES[memory_type]: f"{scope_word} {memory_type.value} memories." for memory_type in MemoryType}


VAULT_DIRECTORIES: tuple[Path, ...] = (
    *(Path("global") / name for name in MEMORY_TYPE_DIRECTORY_NAMES),
    Path("projects"),
    Path(QUEUE_DIRECTORY),
    Path("inbox/unsorted"),
    Path("inbox/project"),
    Path("inbox/global"),
    Path("templates"),
    Path("_meta"),
)
PROBE_DEPENDENCY = DependencyCheck(
    "@probelabs/probe",
    ("bunx", "--silent", PROBE_PACKAGE, "--version"),
    f"run `just setup` from the agent-memory checkout; manual install: run `bunx --silent {PROBE_PACKAGE} --version`.",
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
        "bunx",
        ("bunx", "--version"),
        "run `just setup` from the agent-memory checkout; manual install: install bun from https://bun.sh.",
    ),
    PROBE_DEPENDENCY,
    DependencyCheck(
        "zk",
        ("zk", "--version"),
        "run `just setup` from the agent-memory checkout; manual install: install zk v0.15.5 to a directory on PATH.",
    ),
)
NON_SEARCH_DEPENDENCIES: tuple[DependencyCheck, ...] = tuple(dependency for dependency in BASIC_DEPENDENCIES if dependency is not PROBE_DEPENDENCY)


@dataclass(frozen=True)
class StarterConfig:
    default_vault: Path
    search_max_results: int
    search_max_tokens: int


class ProjectNotInitializedError(RuntimeError):
    """Raised when a project command runs before project memory setup is done."""

    GUIDANCE = (
        "No project memory binding found. Run `agent-memory maintain init-global --vault "
        "{default_vault}` once if the global vault does not exist, then run "
        "`agent-memory init project --vault <path-to-global-vault>` from this repository."
    )
    # A read that only needs project scope has a route that works right now, without any
    # setup mutation. Name it first: the setup instructions below are the slower remedy.
    READ_ROUTE = "Rerun with `--scope global` to read the global vault from an unbound directory."

    def __init__(self, default_vault: Path, *, read_route: bool = False) -> None:
        guidance = self.GUIDANCE.format(default_vault=default_vault)
        super().__init__(f"{self.READ_ROUTE} {guidance}" if read_route else guidance)


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


@cache
def starter_config() -> StarterConfig:
    payload = tomllib.loads(resources.files("agent_memory.defaults").joinpath("global.toml").read_text(encoding="utf-8"))
    default_vault = payload["default_vault"]
    search_max_results = payload["search_max_results"]
    search_max_tokens = payload["search_max_tokens"]
    assert isinstance(default_vault, str), "starter config default_vault must be a string"
    assert isinstance(search_max_results, int), "starter config search_max_results must be an integer"
    assert isinstance(search_max_tokens, int), "starter config search_max_tokens must be an integer"
    assert search_max_results > 0, "starter config search_max_results must be positive"
    assert search_max_tokens > 0, "starter config search_max_tokens must be positive"
    return StarterConfig(
        default_vault=normalize_vault_path(Path(default_vault)),
        search_max_results=search_max_results,
        search_max_tokens=search_max_tokens,
    )


@dataclass(frozen=True)
class MemoryDocument:
    metadata: dict[str, MetadataValue]
    body: str


@dataclass(frozen=True)
class NoteTimestamp:
    value: str

    @classmethod
    def from_metadata(cls, metadata: dict[str, MetadataValue], path: Path) -> NoteTimestamp:
        return cls(metadata_string_optional(metadata, "timestamp", path) or "")

    def is_present(self) -> bool:
        return self.value != ""

    def is_after(self, lower_bound: datetime) -> bool:
        return self.is_present() and parse_memory_timestamp(self.value) > lower_bound

    def json_value(self) -> JsonValue:
        if self.is_present():
            return self.value
        return None

    def sort_key(self) -> str:
        return self.value


@dataclass(frozen=True)
class NoteRecord:
    key: str
    path: Path
    title: str
    memory_type: MemoryType
    scope: MemoryScope
    tags: tuple[str, ...]
    timestamp: NoteTimestamp
    document: MemoryDocument


@dataclass(frozen=True)
class NoteFinding:
    path: Path
    key: str
    message: str


@dataclass(frozen=True)
class NoteScan:
    records: tuple[NoteRecord, ...]
    findings: tuple[NoteFinding, ...]


@dataclass(frozen=True)
class IndexScan:
    records: tuple[JsonObject, ...]
    findings: tuple[NoteFinding, ...]


@dataclass(frozen=True)
class InspectExportRecord:
    key: str
    path: Path
    document: MemoryDocument


@dataclass(frozen=True)
class InspectExportScan:
    records: tuple[InspectExportRecord, ...]
    findings: tuple[NoteFinding, ...]


@dataclass(frozen=True)
class ManagedCardListing:
    title: str
    card_type: str
    scope: MemoryScope
    path: Path
    key: str
    archived: bool


@dataclass(frozen=True)
class UnmigratedCardListing:
    title: str
    card_type: str
    scope: MemoryScope
    path: Path
    suggested_destination: str
    archived: bool


type CardListing = ManagedCardListing | UnmigratedCardListing


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


def default_cards_schema_text() -> str:
    return resources.files("agent_memory.defaults").joinpath("cards.yaml").read_text(encoding="utf-8")


def active_card_schema_path(config: ProjectConfig) -> Path:
    project_id = require_project_id(config)
    project_schema = config.vault / "projects" / project_id / "_meta" / "cards.yaml"
    if project_schema.is_file():
        return project_schema
    return config.vault / "_meta" / "cards.yaml"


def add_card_status_option(status_set_name: str, status: str, cwd: Path) -> JsonObject:
    config = load_project_config(cwd)
    path = active_card_schema_path(config)
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise MemoryOperationError(f"card schema must contain a mapping: {path}")
    statuses = payload.get("statuses")
    if not isinstance(statuses, list) or status not in statuses:
        raise MemoryOperationError(f"status {status!r} is not declared in the card schema catalog")
    status_sets = payload.get("status_sets")
    if not isinstance(status_sets, dict) or status_set_name not in status_sets:
        raise MemoryOperationError(f"unknown card schema status set: {status_set_name}")
    status_set = status_sets[status_set_name]
    if not isinstance(status_set, dict) or not isinstance(status_set.get("options"), list):
        raise MemoryOperationError(f"card schema status set {status_set_name!r} must declare an options list")
    options = status_set["options"]
    if status in options:
        return {"changed": False, "path": str(path), "status": status, "status_set": status_set_name}

    options.append(status)
    CardSystemConfig.model_validate(payload)
    rendered = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(path.stat().st_mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    commit_vault_changes(config.vault, f"Add {status!r} to card status set {status_set_name!r}", paths=[path])
    return {"changed": True, "path": str(path), "status": status, "status_set": status_set_name}


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
    write_new_file(vault / "_meta" / "cards.yaml", default_cards_schema_text())
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
    remote = git_remote_or_empty(git_root)
    project_id = validate_project_id(project_id) if project_id is not None else project_id_from_git_root(git_root, remote)
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
    if memory_type is MemoryType.PLAN:
        raise MemoryOperationError("plain plan memories are not supported; use agent-memory plan add so cards.yaml validates the task tree")
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
    source_path = memory_path_for_key(config, key)
    document = read_memory(source_path)
    old_title = metadata_string(document.metadata, "title", source_path)
    scope = metadata_memory_scope(document.metadata, source_path)
    old_type = metadata_memory_type(document.metadata, source_path)
    new_title = title if title is not None else old_title
    new_type = memory_type if memory_type is not None else old_type
    body = updated_memory_body(document.body, new_title, content)
    description = okf_description(content if content is not None else body)
    # An update owns the canonical OKF fields and nothing else. Keys the record already
    # carries survive because they are merged first; the tags this write does not own --
    # everything beyond the canonical scope/type pair -- are carried across explicitly,
    # since note_metadata rebuilds the tag list from scratch.
    canonical = note_metadata(config, scope, new_type, new_title, description)
    carried_tags: list[MetadataValue] = list(okf_tags(scope, new_type, unowned_tags(document.metadata, source_path, scope, old_type)))
    canonical["tags"] = carried_tags
    metadata = {**document.metadata, **canonical}
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
    if not slug:
        raise MemoryOperationError(f"title {title!r} has no letters or digits to build a filename from; give the record a title with word characters")
    return slug


def sync_memory_transition_indexes(transition: MemoryTransition) -> None:
    if transition.destination_path.parent == transition.source_path.parent:
        replace_index_link(
            transition.destination_path.parent / "index.md",
            transition.source_path.name,
            transition.new_title,
            transition.destination_path.name,
            transition.description,
        )
        return
    remove_index_link(transition.source_path.parent / "index.md", transition.source_path.name)
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
    config = config_for_key(key, cwd)
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


def update_plan_todo(
    *,
    key: str,
    todo_id: str,
    status: str | None,
    content: str | None,
    note: str | None,
    cwd: Path,
) -> JsonObject:
    if status is None and content is None and note is None:
        raise MemoryOperationError("todo set requires at least one of --status, --content, or --note")
    if status is not None and status not in PLAN_TODO_STATUSES:
        allowed = ", ".join(sorted(PLAN_TODO_STATUSES))
        raise MemoryOperationError(f"invalid todo status {status!r}; allowed statuses: {allowed}")
    if content is not None and not content.strip():
        raise MemoryOperationError("todo content must not be empty")
    if not todo_id.strip():
        raise MemoryOperationError("todo id must not be empty")

    config = config_for_key(key, cwd)
    path = (config.vault / f"{key}.md").resolve()
    if not path.is_relative_to(config.vault.resolve()) or not path.is_file():
        raise MemoryOperationError(f"plan memory not found: {key}")

    original_body = raw_memory_body(path)
    document = read_memory(path)
    if metadata_memory_type(document.metadata, path) is not MemoryType.PLAN:
        raise MemoryOperationError(f"todo set requires a plan memory key, got {metadata_string(document.metadata, 'type', path)!r}: {key}")
    todos_value = document.metadata.get("todos")
    if not isinstance(todos_value, list):
        raise MemoryOperationError(f"plan memory has no todos list: {key}")

    metadata = deepcopy(document.metadata)
    copied_todos = metadata["todos"]
    assert isinstance(copied_todos, list), "deep-copied todos must preserve list type"
    target = mutate_todo_tree(copied_todos, todo_id=todo_id, status=status, content=content, note=note, path=path)
    if target is None:
        raise MemoryOperationError(f"todo id not found: {todo_id}")

    write_memory(path, metadata, original_body)
    index_zk_notebook(config.vault)
    title = metadata_string(metadata, "title", path)
    try:
        commit_vault_changes(config.vault, f"Update todo {todo_id} in plan: {title}", paths=[path])
    except subprocess.CalledProcessError as e:
        git_stderr = e.stderr or ""
        raise VaultCommitError(vault_commit_error_message(git_stderr)) from e

    result: JsonObject = {"key": key, "path": str(path), "todo_id": todo_id}
    if status is not None:
        result["status"] = status
    if content is not None:
        result["content"] = content
    if note is not None:
        result["note"] = note
    return result


def plan_progress(scope: SearchScope, cwd: Path) -> JsonObject:
    config, scope = resolve_search_scope(scope, cwd)
    complete_statuses_by_scope: dict[MemoryScope, set[str]] = {}
    excluded_scopes: list[JsonObject] = []
    for memory_scope in search_scope_memory_scopes(config, scope, both_order=(MemoryScope.PROJECT, MemoryScope.GLOBAL)):
        schema_project_id = require_project_id(config) if memory_scope is MemoryScope.PROJECT else None
        cards_config = load_card_system_config(config.vault, schema_project_id)
        if not cards_config.workflow_roles:
            excluded_scopes.append({"scope": memory_scope.value, "reason": "active card schema has no workflow_roles"})
            continue
        complete_statuses_by_scope[memory_scope] = cards_config.statuses_with_role("complete")
    note_scan = scan_note_records(config, scope)
    findings = list(note_scan.findings)
    plans: list[JsonObject] = []
    unsupported_plans: list[JsonObject] = []
    total_todos = 0
    completed_todos = 0
    for record in note_scan.records:
        if record.memory_type is not MemoryType.PLAN:
            continue
        path = record.path
        if "todos" not in record.document.metadata:
            unsupported_plans.append({"key": record.key, "path": str(path), "title": record.title, "reason": "plan has no todos list"})
            continue
        todos = record.document.metadata["todos"]
        if not isinstance(todos, list):
            findings.append(note_finding_for_error(config, path, MalformedMemoryError(path, "plan todos must be a list")))
            continue
        try:
            status_counts = Counter(plan_todo_statuses(todos, path))
        except MalformedMemoryError as error:
            findings.append(note_finding_for_error(config, path, error))
            continue
        if record.scope not in complete_statuses_by_scope:
            continue
        plan_total = sum(status_counts.values())
        plan_completed = sum(count for status, count in status_counts.items() if status in complete_statuses_by_scope[record.scope])
        total_todos += plan_total
        completed_todos += plan_completed
        plans.append(
            {
                "key": record.key,
                "path": str(path),
                "title": record.title,
                "total_todos": plan_total,
                "completed_todos": plan_completed,
                "completion_percent": (100 * plan_completed / plan_total) if plan_total else 0,
                "status_counts": dict(sorted(status_counts.items())),
            }
        )
    aggregate_totals: JsonValue = total_todos if not excluded_scopes else None
    aggregate_completed: JsonValue = completed_todos if not excluded_scopes else None
    aggregate_percent: JsonValue = (100 * completed_todos / total_todos) if total_todos else 0
    if excluded_scopes:
        aggregate_percent = None
    return {
        "scope": scope.value,
        "plans": json_list(plans),
        "unsupported_plans": json_list(unsupported_plans),
        "findings": note_findings_json(findings),
        "excluded_scopes": json_list(excluded_scopes),
        "total_todos": aggregate_totals,
        "completed_todos": aggregate_completed,
        "completion_percent": aggregate_percent,
    }


def plan_todo_statuses(todos: list[MetadataValue], path: Path) -> list[str]:
    statuses: list[str] = []
    for item in todos:
        todo = todo_mapping(item, path)
        status = todo.get("status")
        if not isinstance(status, str) or not status.strip():
            raise MalformedMemoryError(path, f"todo {todo['id']!r} must have a nonempty string status")
        statuses.append(status)
        for child_key in PLAN_TODO_CHILD_KEYS:
            children = todo.get(child_key)
            if children is None:
                continue
            if not isinstance(children, list):
                raise MalformedMemoryError(path, f"todo field {child_key} must be a list")
            statuses.extend(plan_todo_statuses(children, path))
    return statuses


def mutate_todo_tree(
    todos: list[MetadataValue],
    *,
    todo_id: str,
    status: str | None,
    content: str | None,
    note: str | None,
    path: Path,
) -> dict[str, MetadataValue] | None:
    found: dict[str, MetadataValue] | None = None
    for item in todos:
        todo = todo_mapping(item, path)
        item_id = todo.get("id")
        if item_id == todo_id:
            if found is not None:
                raise MemoryOperationError(f"todo id is not unique: {todo_id}")
            if status is not None:
                todo["status"] = status
            if content is not None:
                todo["content"] = content
            if note is not None:
                todo["note"] = note
            found = todo
        for child_key in PLAN_TODO_CHILD_KEYS:
            children = todo.get(child_key)
            if children is None:
                continue
            if not isinstance(children, list):
                raise MalformedMemoryError(path, f"todo field {child_key} must be a list")
            child_match = mutate_todo_tree(children, todo_id=todo_id, status=status, content=content, note=note, path=path)
            if child_match is not None:
                if found is not None:
                    raise MemoryOperationError(f"todo id is not unique: {todo_id}")
                found = child_match
    return found


def todo_mapping(value: MetadataValue, path: Path) -> dict[str, MetadataValue]:
    if not isinstance(value, dict):
        raise MalformedMemoryError(path, "todo entries must be mappings")
    for key in value:
        if not isinstance(key, str):
            raise MalformedMemoryError(path, "todo entry keys must be strings")
    todo_id = value.get("id")
    if not isinstance(todo_id, str) or not todo_id.strip():
        raise MalformedMemoryError(path, "todo entries must contain a nonempty string id")
    return value


def raw_memory_body(path: Path) -> str:
    raw = path.read_text(encoding="utf-8")
    parts = raw.split("---\n", 2)
    if len(parts) != 3 or parts[0] != "":
        return read_memory(path).body
    return parts[2]


def delete_backlink_disposition_error(key: str, inbound_keys: Sequence[str]) -> str:
    return f"delete would orphan inbound wikilinks for {key}; inbound={', '.join(inbound_keys)}; rerun with --repoint <key-or-url> or --orphan-ok"


def delete_memory(key: str, cwd: Path) -> JsonObject:
    return _delete_memory(key, cwd, DeleteBacklinksBlocked())


def delete_memory_orphaning_backlinks(key: str, cwd: Path) -> JsonObject:
    return _delete_memory(key, cwd, DeleteBacklinksOrphaned())


def delete_memory_repointing_backlinks(key: str, cwd: Path, repoint: str) -> JsonObject:
    return _delete_memory(key, cwd, DeleteBacklinksRepointed(repoint))


def _delete_memory(key: str, cwd: Path, backlink_disposition: DeleteBacklinkDisposition) -> JsonObject:
    config = config_for_key(key, cwd)
    path = memory_path_for_key(config, key)
    inbound_keys = non_index_incoming_link_keys(config, key)
    if inbound_keys and isinstance(backlink_disposition, DeleteBacklinksBlocked):
        raise MemoryOperationError(delete_backlink_disposition_error(key, inbound_keys))
    try:
        document = read_memory(path)
    except MalformedMemoryError:
        remove_index_link_by_target(path.parent / "index.md", path.name)
        if path.exists():
            path.unlink()
        commit_message = f"Delete memory: {key}"
    else:
        title = metadata_string(document.metadata, "title", path)
        remove_index_link(path.parent / "index.md", path.name)
        commit_message = f"Delete memory: {title}"
        iwe.delete(config.vault, key)

    rewritten: list[JsonObject] = []
    if isinstance(backlink_disposition, DeleteBacklinksRepointed):
        rewritten = rewrite_wikilink_files(config, (wikilink_rewrite(key, backlink_disposition.target),))
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
    if isinstance(backlink_disposition, DeleteBacklinksRepointed):
        result["repointed_to"] = backlink_disposition.target
        result["rewritten"] = json_list(rewritten)
    if isinstance(backlink_disposition, DeleteBacklinksOrphaned):
        result["orphaned"] = json_list(inbound_keys)
    return result


def search_memories(scope: SearchScope, query: str, visibility: ArchiveVisibility, cwd: Path) -> JsonObject:
    config, scope = resolve_search_scope(scope, cwd)
    limit = starter_config().search_max_results
    note_scan = scan_note_records(config, scope)
    key_matches = key_search_records_from_notes(note_scan.records, query, limit)
    exact_matches = exact_content_records(config, scope, query)
    fuzzy_matches = fuzzy_content_records(config, scope, query)
    results, archived_matches = select_search_records(config, dedupe_records_by_key([*key_matches, *exact_matches, *fuzzy_matches])[:limit], visibility)
    key_matches, _key_archived = select_search_records(config, key_matches, visibility)
    exact_matches, _exact_archived = select_search_records(config, exact_matches, visibility)
    fuzzy_matches, _fuzzy_archived = select_search_records(config, fuzzy_matches, visibility)
    ranked_matches = search_content_ranked(scope, query, cwd, visibility=visibility)
    ranked_results = ranked_matches["results"]
    assert isinstance(ranked_results, list), "ranked search results must be a JSON list"
    return {
        "query": query,
        "scope": scope.value,
        "visibility": visibility.value,
        "results": json_list(results),
        "key_matches": json_list(key_matches),
        "exact_content_matches": json_list(exact_matches),
        "fuzzy_content_matches": json_list(fuzzy_matches),
        "ranked_content_matches": ranked_results,
        "archived_matches": json_list(archived_matches),
        "findings": note_findings_json(note_scan.findings),
    }


def search_keys(scope: SearchScope, query: str, visibility: ArchiveVisibility, cwd: Path) -> JsonObject:
    config, scope = resolve_search_scope(scope, cwd)
    note_scan = scan_note_records(config, scope)
    records, archived_matches = select_search_records(config, key_search_records_from_notes(note_scan.records, query, starter_config().search_max_results), visibility)
    return {
        "query": query,
        "scope": scope.value,
        "visibility": visibility.value,
        "results": json_list(records),
        "archived_matches": json_list(archived_matches),
        "findings": note_findings_json(note_scan.findings),
    }


def search_content_exact(scope: SearchScope, query: str, visibility: ArchiveVisibility, cwd: Path) -> JsonObject:
    config, scope = resolve_search_scope(scope, cwd)
    records, archived_matches = select_search_records(config, exact_content_records(config, scope, query), visibility)
    return {
        "query": query,
        "scope": scope.value,
        "visibility": visibility.value,
        "results": json_list(records),
        "archived_matches": json_list(archived_matches),
    }


def search_metadata(
    scope: SearchScope,
    memory_type: MemoryType | None,
    tag: str | None,
    created_after: str | None,
    visibility: ArchiveVisibility,
    cwd: Path,
) -> JsonObject:
    config, scope = resolve_search_scope(scope, cwd)
    limit = starter_config().search_max_results
    created_after_datetime = parse_created_after(created_after)
    note_scan = scan_note_records(config, scope)
    matching = [metadata_search_record_json(record) for record in note_scan.records if note_record_matches_metadata(record, memory_type, tag, created_after_datetime)]
    records, archived_matches = select_search_records(config, matching[:limit], visibility)
    return {
        "scope": scope.value,
        "visibility": visibility.value,
        "results": json_list(records),
        "archived_matches": json_list(archived_matches),
        "findings": note_findings_json(note_scan.findings),
    }


def key_search_records(config: ProjectConfig, scope: SearchScope, query: str) -> list[JsonObject]:
    return key_search_records_from_notes(inspect_note_records(config, scope), query, starter_config().search_max_results)


def key_search_records_from_notes(records: Sequence[NoteRecord], query: str, limit: int) -> list[JsonObject]:
    query_text = query.casefold()
    matched_records: list[JsonObject] = []
    for record in records:
        key_matches = query_text in record.key.casefold()
        title_matches = query_text in record.title.casefold()
        if key_matches or title_matches:
            json_record = note_record_json(record)
            json_record["source"] = "keys"
            matched_records.append(json_record)
    return dedupe_records_by_key(matched_records)[:limit]


def exact_content_records(config: ProjectConfig, scope: SearchScope, query: str) -> list[JsonObject]:
    limit = starter_config().search_max_results
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
    return dedupe_records_by_key(records)[:limit]


def zk_search_scope(config: ProjectConfig, scope: SearchScope, query: str) -> list[JsonObject]:
    limit = starter_config().search_max_results
    results: list[JsonObject] = []
    for root in search_roots(config, scope):
        results.extend(
            zk_search_root(
                vault=config.vault,
                root=root,
                query=query,
                limit=limit,
            )
        )
    return results


def fuzzy_content_records(config: ProjectConfig, scope: SearchScope, query: str) -> list[JsonObject]:
    return dedupe_records_by_key(zk_search_scope(config, scope, query))[: starter_config().search_max_results]


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


def search_record_path(record: JsonObject) -> Path:
    value = record["path"] if "path" in record else record["file"] if "file" in record else None
    if not isinstance(value, str):
        raise MemoryOperationError("search result has no file path")
    return Path(value)


def archived_search_identity(config: ProjectConfig, path: Path, title: str, card_type: str) -> JsonObject:
    return {
        "key": memory_key(config.vault, path),
        "path": str(path),
        "title": title,
        "type": card_type,
        "archived": True,
    }


def select_search_records(config: ProjectConfig, records: Sequence[JsonObject], visibility: ArchiveVisibility) -> tuple[list[JsonObject], list[JsonObject]]:
    selected: list[JsonObject] = []
    archived_matches: list[JsonObject] = []
    for record in records:
        path = search_record_path(record)
        fields = card_listing_fields_for_path(config, path)
        archived = fields is not None and fields[3]
        if archived_record_is_visible(archived, visibility):
            selected.append(record)
        if fields is not None and fields[3] and visibility is ArchiveVisibility.ACTIVE:
            title, card_type, _scope, _archived = fields
            archived_matches.append(archived_search_identity(config, path, title, card_type))
    return selected, dedupe_records_by_key(archived_matches)


def json_list(values: Sequence[JsonValue]) -> list[JsonValue]:
    # Widen a homogeneous JSON-value sequence to the list[JsonValue] shape required by
    # JsonObject slots. Sequence is covariant, so list[JsonObject] and list[str] inputs
    # satisfy Sequence[JsonValue]; the copy decouples the emitted payload from callers.
    return list(values)


@cache
def card_prefix_types(config: ProjectConfig) -> dict[str, str]:
    # Which id prefix names which card type, read from the active schema rather than a
    # hand-kept copy of it: a duplicate map makes every new card type invisible to `list`
    # until someone remembers to edit it. Cached because listings ask once per vault file.
    cards_config = load_card_system_config(config.vault, config.project_id)
    return {card_type.id_prefix: card_type.name for card_type in cards_config.card_types}


def card_listing_json(record: CardListing) -> JsonObject:
    payload: JsonObject = {
        "title": record.title,
        "type": record.card_type,
        "scope": record.scope.value,
        "path": str(record.path),
        "archived": record.archived,
    }
    if isinstance(record, ManagedCardListing):
        return {**payload, "managed": True, "key": record.key, "suggested_destination": None}
    return {**payload, "managed": False, "key": None, "suggested_destination": record.suggested_destination}


def list_cards(card_type: str | None, scope: SearchScope, visibility: ArchiveVisibility, cwd: Path) -> JsonObject:
    return _list_cards(card_type, scope, visibility, CardListingSource.MANAGED, cwd)


def list_cards_with_unmigrated(card_type: str | None, scope: SearchScope, visibility: ArchiveVisibility, cwd: Path) -> JsonObject:
    return _list_cards(card_type, scope, visibility, CardListingSource.MANAGED_AND_UNMIGRATED, cwd)


def listable_types(config: ProjectConfig, scope: SearchScope) -> tuple[str, ...]:
    # Everything a listing can hold: schema-backed card types plus the memory types that
    # unmigrated records still carry in their frontmatter. A global-scope listing reads the
    # vault schema only -- it never touches the project, so a malformed project schema must
    # not decide which types it will accept.
    project_id = None if scope is SearchScope.GLOBAL else config.project_id
    cards_config = load_card_system_config(config.vault, project_id)
    names = {card_type.name for card_type in cards_config.card_types} | {memory_type.value for memory_type in MemoryType}
    return tuple(sorted(names))


def _list_cards(card_type: str | None, scope: SearchScope, visibility: ArchiveVisibility, listing_source: CardListingSource, cwd: Path) -> JsonObject:
    config, scope = resolve_search_scope(scope, cwd)
    # No --type lists every type, so absence is valid. A supplied name that no type uses is
    # not: silently returning an empty list turns a typo into "the vault has none of these".
    if card_type is not None:
        known = listable_types(config, scope)
        if card_type not in known:
            raise MemoryOperationError(f"unknown type {card_type!r}; listable types: {', '.join(known)}. Omit --type to list every type.")
    records: list[CardListing] = [*managed_card_listings(config, scope)]
    include_unmigrated = listing_source is CardListingSource.MANAGED_AND_UNMIGRATED
    if listing_source is CardListingSource.MANAGED_AND_UNMIGRATED:
        records.extend(unmigrated_card_listings(config, scope))
    typed = [record for record in records if card_type is None or record.card_type == card_type]
    filtered = [record for record in typed if archived_record_is_visible(record.archived, visibility)]
    archived_matches = [record for record in typed if record.archived] if visibility is ArchiveVisibility.ACTIVE else []
    filtered.sort(
        key=lambda record: (
            isinstance(record, ManagedCardListing),
            record.scope.value,
            record.title,
            str(record.path),
        )
    )
    return {
        "type": card_type,
        "scope": scope.value,
        "visibility": visibility.value,
        "include_unmigrated": include_unmigrated,
        "results": json_list([card_listing_json(record) for record in filtered]),
        "archived_matches": json_list([card_listing_json(record) for record in archived_matches]),
    }


def search_content_ranked(scope: SearchScope, query: str, cwd: Path, visibility: ArchiveVisibility) -> JsonObject:
    config, scope = resolve_search_scope(scope, cwd)
    starter = starter_config()
    roots = search_roots(config, scope)
    per_root_tokens = starter.search_max_tokens // len(roots)
    assert per_root_tokens > 0, "ranked content search token budget must cover every selected scope root"
    payloads = [
        probe_search_root(
            root=root,
            query=query,
            max_results=starter.search_max_results,
            max_tokens=per_root_tokens,
            cwd=config.vault,
        )
        for root in roots
    ]
    merged = merge_probe_payloads(
        payloads,
        max_results=starter.search_max_results,
        max_tokens=starter.search_max_tokens,
    )
    payload = ranked_results_payload(config, merged, starter.search_max_results)
    raw_results = payload["results"]
    assert isinstance(raw_results, list), "ranked search results must be a JSON list"
    records = [record for record in raw_results if isinstance(record, dict)]
    selected, archived_matches = select_search_records(config, records, visibility)
    summary = json_child(payload, "summary")
    return {
        **payload,
        "scope": scope.value,
        "visibility": visibility.value,
        "results": json_list(selected),
        "archived_matches": json_list(archived_matches),
        "summary": {**summary, "count": len(selected)},
    }


def ranked_results_payload(config: ProjectConfig, payload: JsonObject, limit: int) -> JsonObject:
    # Three corrections to what Probe hands back, all on the default search path.
    #
    # Probe reports files it matched but could not excerpt in `skipped_files`, outside
    # `results`. Those are matches, and the strongest ones: block extraction gives up on
    # exactly the large records that mention a topic most. Left in a sidecar array, a search
    # returns ten weaker hits while an agent concludes the vault has nothing on the topic.
    #
    # Probe records name a file and no vault key, unlike every sibling search command, so
    # `.results[].key` came back null for callers who read it the way the siblings taught.
    #
    # A record that scored zero with no excerpt is not a match, and Probe emits it
    # inconsistently between identical runs. Dropping it makes ranked output reproducible.
    excerpted = [record for record in probe_results(payload) if is_scored_match(record)]
    skipped = sorted(probe_skipped_files(payload), key=lambda record: json_int(record, "all"), reverse=True)
    unexcerpted: list[JsonObject] = [{"file": record["file"], "lines": None, "code": None, "match_count": record["all"]} for record in skipped[:limit]]
    results: list[JsonValue] = [{**record, "key": memory_key(config.vault, Path(json_string(record, "file")))} for record in (*excerpted, *unexcerpted)]
    summary = json_child(payload, "summary")
    return {**payload, "results": json_list(results), "summary": {**summary, "count": len(results)}}


def is_scored_match(record: JsonObject) -> bool:
    return probe_score(record) > 0 or bool(record.get("code"))


def json_string(payload: JsonObject, key: str) -> str:
    value = payload[key]
    assert isinstance(value, str), f"probe {key} must be a string"
    return value


def search_content_fuzzy(scope: SearchScope, query: str, cwd: Path, visibility: ArchiveVisibility) -> JsonObject:
    config, scope = resolve_search_scope(scope, cwd)
    records, archived_matches = select_search_records(config, zk_search_scope(config, scope, query), visibility)
    return {
        "query": query,
        "scope": scope.value,
        "visibility": visibility.value,
        "results": json_list(records[: starter_config().search_max_results]),
        "archived_matches": json_list(archived_matches),
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
            "bunx",
            # Without --silent, a cold cache writes install progress into the stdout this
            # function parses as JSON, so the first search after a fresh install fails.
            "--silent",
            # Do not "simplify" this to a bare `probe`. A different package, @buger/probe,
            # installs a binary of that name globally, so a bare command name would swap
            # the ranking engine out from under this search with nothing to show for it.
            PROBE_PACKAGE,
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
    config = config_for_key(key, cwd)
    memory_path_for_key(config, key)
    return iwe.retrieve(config.vault, key)


def squash_memory(key: str, depth: int, cwd: Path) -> str:
    if depth < 1:
        raise MemoryOperationError(f"squash --depth must be 1 or more, got {depth}")
    config = config_for_key(key, cwd)
    memory_path_for_key(config, key)
    return iwe.squash(config.vault, key, depth)


def split_memory(key: str, section: str, cwd: Path) -> JsonObject:
    config = config_for_key(key, cwd)
    source_path = memory_path_for_key(config, key)
    source_document = read_memory(source_path)
    source_title = metadata_string(source_document.metadata, "title", source_path)
    memory_type = MemoryType(metadata_string(source_document.metadata, "type", source_path))
    scope = MemoryScope(metadata_string(source_document.metadata, "scope", source_path))
    try:
        affected_keys = iwe.extract(config.vault, key, section)
    except RuntimeError as error:
        raise MemoryOperationError(f"cannot split {key} at section {section!r}: {error}; `agent-memory inspect outline {key}` lists the headings it has") from error
    extracted_keys: list[str] = []
    for affected_key in affected_keys:
        if affected_key == key:
            continue
        extracted_path = memory_path_for_key(config, affected_key)
        extracted_body = extracted_path.read_text(encoding="utf-8")
        title = first_heading_title(extracted_path, extracted_body)
        description = f"Extracted from {source_title}."
        write_memory(extracted_path, note_metadata(config, scope, memory_type, title, description), extracted_body)
        append_index_link(extracted_path.parent / "index.md", title, extracted_path.name, description)
        extracted_keys.append(affected_key)
    if len(extracted_keys) != 1:
        raise MemoryOperationError(
            f"section {section!r} did not extract exactly one record from {key} "
            f"(extracted {len(extracted_keys)}); name a heading that appears once, "
            f"as `agent-memory inspect outline {key}` lists it"
        )
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
    config = config_for_key(key, cwd)
    reference_path = memory_path_for_key(config, reference)
    reference_document = read_memory(reference_path)
    reference_title = metadata_string(reference_document.metadata, "title", reference_path)
    try:
        affected_keys = iwe.inline(config.vault, key, reference)
    except RuntimeError as error:
        raise MemoryOperationError(f"cannot merge {reference} into {key}: {error}; `agent-memory inspect links {key}` lists the references it has") from error
    rewritten = rewrite_non_index_wikilink_files(
        config,
        (wikilink_rewrite(reference, f"{key}#{reference_title}"),),
    )
    index_zk_notebook(config.vault)
    # Build pathspecs without asserting existence -- iwe.inline deletes the
    # reference file, so memory_path_for_key() would fail on the merged-away key.
    affected_paths = [config.vault / f"{k}.md" for k in affected_keys]
    paths = list(set(affected_paths + [p.parent / "index.md" for p in affected_paths] + rewritten_record_paths(rewritten) + rewritten_record_parent_index_paths(rewritten)))
    commit_vault_changes(config.vault, f"Merge memory reference: {reference}", paths=paths)
    return {"key": key, "reference": reference, "output": json_list(affected_keys), "rewritten": json_list(rewritten)}


def move_memory(key: str, destination: str, cwd: Path) -> JsonObject:
    config = config_for_key(key, cwd)
    if not destination.startswith("global/"):
        raise MemoryOperationError(f"move destination must be a global directory such as `global/traps`, got {destination!r}")
    source_path = memory_path_for_key(config, key)
    destination_key = f"{destination}/{source_path.stem}"
    destination_path = config.vault / f"{destination_key}.md"
    if not destination_path.parent.is_dir():
        known = ", ".join(sorted(f"global/{directory}" for directory in MEMORY_TYPE_DIRECTORIES.values()))
        raise MemoryOperationError(f"no such destination directory {destination!r} in {config.vault}; global destinations are: {known}")

    source_document = read_memory(source_path)
    memory_type = metadata_memory_type(source_document.metadata, source_path)
    title = metadata_string(source_document.metadata, "title", source_path)
    description = metadata_string(source_document.metadata, "description", source_path)
    # A promotion pointer is an index artifact of the project note directory it replaces.
    # A source stranded outside that directory has no index entry to repoint and no project
    # to attribute the stub to, so the move leaves nothing behind (issue #107).
    leaves_pointer = source_path.parent == memory_directory(config, MemoryScope.PROJECT, memory_type)
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

    if leaves_pointer:
        pointer_description = f"Promoted to {destination_key}."
        pointer_metadata = ProjectNoteMetadata(
            type=memory_type,
            title=title,
            description=pointer_description,
            tags=okf_tags(MemoryScope.PROJECT, memory_type, (PROMOTION_POINTER_TAG,)),
            timestamp=okf_timestamp(),
            scope=MemoryScope.PROJECT,
            project_id=require_project_id(config),
        ).to_yaml_payload()
        pointer_body = f"# {title}\n\nPromoted to [[{destination_key}]].\n"
        write_new_memory(source_path, pointer_metadata, pointer_body)
        replace_index_link(source_path.parent / "index.md", source_path.name, title, source_path.name, pointer_description)
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


def basic_doctor(
    cwd: Path,
    dependencies: Sequence[DependencyCheck] = BASIC_DEPENDENCIES,
) -> JsonObject:
    checked = [check_dependency(dependency, cwd) for dependency in dependencies]
    return {
        "dependencies": json_list(checked),
        "tools": [dependency.name for dependency in dependencies],
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
        "unmigrated_cards": json_list([card_listing_json(record) for record in unmigrated_card_listings(config, SearchScope.BOTH)]),
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
    for required in (vault / ".zk" / "config.toml", vault / ".zk" / "templates" / "default.md"):
        if not required.is_file():
            raise MemoryOperationError(f"{vault} is missing its zk notebook file {required}; rerun `agent-memory maintain init-global --vault {vault}` to restore it")


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
    if not branch:
        raise MemoryOperationError(f"{repo} has a detached HEAD, so there is no branch to sync; run `git -C {repo} switch <branch>`")
    return branch


def git_upstream(repo: Path) -> str | None:
    result = run_checked_optional(["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"], cwd=repo)
    return result.stdout.strip() or None if result.returncode == 0 else None


def git_ahead_behind(repo: Path) -> tuple[int, int] | None:
    result = run_checked_optional(["git", "rev-list", "--left-right", "--count", "HEAD...@{upstream}"], cwd=repo)
    if result.returncode != 0:
        return None
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
        if not xdg_config_home:
            raise MemoryOperationError("XDG_CONFIG_HOME is set to an empty string; point it at a directory or unset it to use ~/.config")
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
        if not xdg_state_home:
            raise MemoryOperationError("XDG_STATE_HOME is set to an empty string; point it at a directory or unset it to use ~/.local/state")
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
    rendered = json.dumps(state, indent=2, sort_keys=True) + "\n"
    # ponytail: atomic replace so an interrupted/concurrent sync cannot leave a truncated
    # state file that wedges every later doctor/sync run in json.loads.
    temporary = state_path.with_name(f".{state_path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, state_path)
    finally:
        temporary.unlink(missing_ok=True)
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
    for unit in (paths.service, paths.timer):
        if not unit.is_file():
            raise MemoryOperationError(f"auto-sync unit {unit} is not installed; run `agent-memory sync install` first")
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
    # A vault with no remote or no upstream is unsynced, not broken: `maintain init-global`
    # creates exactly that, so status reports the absence instead of raising from git.
    tracking = git_ahead_behind(vault)
    return {
        "vault": str(vault),
        "initialized": True,
        "project_bound": project_bound,
        "git": {
            "remote": git_remote_or_empty(vault) or None,
            "branch": git_current_branch(vault),
            "head": git_head(vault),
            "upstream": git_upstream(vault),
            "ahead": tracking[0] if tracking is not None else None,
            "behind": tracking[1] if tracking is not None else None,
            "worktree_clean": not changes,
            "changes": changes,
        },
        "auto_sync": sync_auto_status(),
        "last_sync": sync_state(),
    }


def push_sync_conflict_branch(vault: Path, remote: str, branch: str, commit_state: SyncCommitState, conflict_head: str) -> JsonObject:
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
        "committed": commit_state is SyncCommitState.COMMITTED,
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
    remote = git_remote_or_empty(vault)
    if not remote:
        raise MemoryOperationError(
            f"vault {vault} has no origin remote to sync with; run `git -C {vault} remote add origin <url>` once, then `git -C {vault} push -u origin HEAD`"
        )
    status_before = git_status_entries(vault)
    committed = bool(status_before)
    if committed:
        commit_vault_changes(vault, "Auto-sync vault changes")
    sync_head = git_head(vault)
    run_checked(["git", "fetch", "origin", branch], cwd=vault)
    rebase = run_checked_optional(["git", "rebase", f"origin/{branch}"], cwd=vault)
    if rebase.returncode != 0:
        commit_state = SyncCommitState.COMMITTED if committed else SyncCommitState.CLEAN
        result = push_sync_conflict_branch(vault, remote, branch, commit_state, sync_head)
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
    if path.exists():
        raise MemoryOperationError(f"refusing to overwrite the existing {path}; move it aside and rerun")
    path.write_text(content, encoding="utf-8")


def install_project_agent_state_links(git_root: Path, project_dir: Path) -> None:
    for name in PROJECT_AGENT_STATE_DIRECTORIES:
        install_project_agent_state_link(git_root, project_dir, name)


def install_project_agent_state_link(git_root: Path, project_dir: Path, name: str) -> None:
    repo_path = git_root / name
    vault_path = project_dir
    if repo_path.is_symlink():
        if repo_path.resolve() != vault_path.resolve():
            raise MemoryOperationError(
                f"{repo_path} is already a symlink to {repo_path.resolve()}, not to this "
                f"project's {vault_path}; remove it and rerun `agent-memory init project` "
                "to bind this repository"
            )
        return
    if repo_path.exists():
        if not repo_path.is_dir():
            raise MemoryOperationError(f"{repo_path} is {describe_non_symlink_path(repo_path)} and binding needs that path for a vault symlink; move it aside and rerun")
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
        if target.exists() or target.is_symlink():
            raise MemoryOperationError(f"the vault already holds {target}, so migrating {child} would overwrite it; reconcile the two by hand and rerun")
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
    add_examples = "".join(f"agent-memory add --scope project --type {memory_type.value} --title <title> --content <content>\n" for memory_type in WRITABLE_MEMORY_TYPES)
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
        "Plan work is card-backed. Create and update plan cards with `agent-memory plan add` and `agent-memory plan update`, not `agent-memory add --type plan`.\n\n"
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
    if has_start != has_end:
        raise MemoryOperationError(
            f"{agents_path} has only one of the agent-memory section markers {AGENTS_SECTION_START} / {AGENTS_SECTION_END}; restore the missing one or delete both and rerun"
        )
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
    if not target.endswith(".md"):
        raise MemoryOperationError(f"index links target Markdown records, but {target!r} is not a .md file")
    return f"* [{title}]({target}) - {description}"


def okf_timestamp() -> str:
    return f"{date.today().isoformat()}T00:00:00Z"


def okf_description(content: str) -> str:
    content_lines = content.strip().splitlines()
    if not content_lines:
        raise MemoryOperationError("--content must not be empty: its first line becomes the record's description")
    return content_lines[0]


def okf_tags(
    scope: MemoryScope,
    memory_type: MemoryType,
    extra_tags: Sequence[str],
) -> list[str]:
    return [scope.value, memory_type.value, *extra_tags]


def unowned_tags(
    metadata: dict[str, MetadataValue],
    path: Path,
    scope: MemoryScope,
    memory_type: MemoryType,
) -> tuple[str, ...]:
    # The tags a rewrite must carry across: everything the canonical scope/type pair does
    # not account for.
    canonical = set(okf_tags(scope, memory_type, ()))
    return tuple(tag for tag in metadata_string_tuple(metadata, "tags", path) if tag not in canonical)


@dataclass(frozen=True)
class ParsedIndexEntry:
    line_index: int
    target: str


def _parse_index_entries(lines: list[str]) -> list[ParsedIndexEntry]:
    entries = []

    body_start = 0
    if lines and lines[0].startswith("---"):
        for i in range(1, len(lines)):
            if lines[i].startswith("---") or lines[i].startswith("..."):
                body_start = i + 1
                break

    body_content = "".join(lines[body_start:])
    tokens = MARKDOWN_PARSER.parse(body_content)

    current_section = None
    list_item_depth = 0

    for i, token in enumerate(tokens):
        if token.type == "heading_open":
            if i + 1 < len(tokens) and tokens[i + 1].type == "inline":
                heading_text = tokens[i + 1].content
                if heading_text in ("Concepts", "Subdirectories"):
                    current_section = heading_text
                else:
                    current_section = None
            continue

        if not current_section:
            continue

        if token.type == "bullet_list_open":
            list_item_depth += 1
        elif token.type == "bullet_list_close":
            list_item_depth -= 1
        elif token.type == "list_item_open":
            if list_item_depth != 1:
                continue
            if token.map is None:
                continue
            start_line = token.map[0] + body_start
            end_line = token.map[1] + body_start

            if end_line - start_line != 1:
                continue

            if (
                i + 4 < len(tokens)
                and tokens[i + 1].type == "paragraph_open"
                and tokens[i + 2].type == "inline"
                and tokens[i + 3].type == "paragraph_close"
                and tokens[i + 4].type == "list_item_close"
            ):
                inline_token = tokens[i + 2]
                children = inline_token.children
                if not children:
                    continue

                if children[0].type != "link_open":
                    continue

                target = str(children[0].attrGet("href") or "")

                link_close_idx = -1
                for j, child in enumerate(children):
                    if child.type == "link_close":
                        link_close_idx = j
                        break

                if link_close_idx == -1 or link_close_idx + 1 >= len(children):
                    continue

                next_child = children[link_close_idx + 1]
                if next_child.type != "text" or not next_child.content.startswith(" - "):
                    continue

                entries.append(ParsedIndexEntry(line_index=start_line, target=target))

    return entries


def append_index_link(index_path: Path, title: str, target: str, description: str) -> None:
    if not index_path.is_file():
        raise MemoryOperationError(f"the vault has no index at {index_path} to link this record into; run `agent-memory doctor` and repair the vault before writing")
    with index_path.open("a", encoding="utf-8") as index_file:
        index_file.write("\n" + okf_index_entry(title, target, description) + "\n")


def locate_index_link(index_path: Path, target: str) -> tuple[list[str], int | None]:
    if not index_path.is_file():
        return [], None

    with index_path.open("r", encoding="utf-8", newline="") as f:
        lines = f.readlines()

    entries = _parse_index_entries(lines)
    matching = [e.line_index for e in entries if e.target == target]

    if len(matching) > 1:
        raise MemoryOperationError(f"index {index_path} contains multiple links for target: {target}")
    if not matching:
        return lines, None
    return lines, matching[0]


def replace_index_link(index_path: Path, old_target: str, new_title: str, new_target: str, description: str) -> None:
    lines, entry_start = locate_index_link(index_path, old_target)
    if entry_start is None:
        line_ending = "\r\n" if any(line.endswith("\r\n") for line in lines) else "\n"
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines.append(line_ending)
        lines.append(okf_index_entry(new_title, new_target, description) + line_ending)
    else:
        original_line = lines[entry_start]
        ending = "\r\n" if original_line.endswith("\r\n") else ("\n" if original_line.endswith("\n") else "")
        lines[entry_start] = okf_index_entry(new_title, new_target, description) + ending
    with index_path.open("w", encoding="utf-8", newline="") as index_file:
        index_file.write("".join(lines))


def remove_index_link(index_path: Path, target: str) -> None:
    lines, entry_start = locate_index_link(index_path, target)
    if entry_start is None:
        return
    del lines[entry_start]
    with index_path.open("w", encoding="utf-8", newline="") as index_file:
        index_file.write("".join(lines))


def remove_index_link_by_target(index_path: Path, target: str) -> None:
    remove_index_link(index_path, target)


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


def metadata_bool(metadata: dict[str, MetadataValue], key: str, path: Path) -> bool:
    try:
        value = metadata[key]
    except KeyError as error:
        raise MalformedMemoryError(path, f"frontmatter missing required field: {key}") from error
    if not isinstance(value, bool):
        raise MalformedMemoryError(path, f"frontmatter field {key} must be a boolean")
    return value


def metadata_string_tuple(metadata: dict[str, MetadataValue], key: str, path: Path) -> tuple[str, ...]:
    try:
        value = metadata[key]
    except KeyError as error:
        raise MalformedMemoryError(path, f"frontmatter missing required field: {key}") from error
    if not isinstance(value, list):
        raise MalformedMemoryError(path, f"frontmatter field {key} must be a list")
    strings: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise MalformedMemoryError(path, f"frontmatter field {key} must be a list of strings")
        strings.append(item)
    return tuple(strings)


def metadata_memory_type(metadata: dict[str, MetadataValue], path: Path) -> MemoryType:
    value = metadata_string(metadata, "type", path)
    try:
        return MemoryType(value)
    except ValueError as error:
        known_types = ", ".join(memory_type.value for memory_type in MemoryType)
        raise MalformedMemoryError(path, f"frontmatter type {value!r} is not one of: {known_types}") from error


def metadata_memory_scope(metadata: dict[str, MetadataValue], path: Path) -> MemoryScope:
    value = metadata_string(metadata, "scope", path)
    try:
        return MemoryScope(value)
    except ValueError as error:
        known_scopes = ", ".join(scope.value for scope in MemoryScope)
        raise MalformedMemoryError(path, f"frontmatter scope {value!r} is not one of: {known_scopes}") from error


def updated_memory_body(current_body: str, title: str, content: str | None) -> str:
    if content is not None:
        return f"# {title}\n\n{content}\n"
    lines = current_body.splitlines(keepends=True)
    if not lines or not lines[0].startswith("# "):
        raise MemoryOperationError("this record's body does not start with a `# ` title, so --title has no heading to rewrite; pass --content to replace the body instead")
    lines[0] = f"# {title}\n"
    return "".join(lines)


def git_root_for(cwd: Path) -> Path:
    result = run_checked(["git", "rev-parse", "--show-toplevel"], cwd=cwd)
    root = Path(result.stdout.strip())
    assert root.is_dir(), "git root must be a directory"
    return root


def git_remote_or_empty(git_root: Path) -> str:
    result = subprocess.run(["git", "remote", "get-url", "origin"], cwd=git_root, check=False, text=True, capture_output=True)
    if result.returncode == 0:
        remote = result.stdout.strip()
        assert remote, "git origin remote must be nonempty when configured"
        return remote
    remotes = run_checked(["git", "remote"], cwd=git_root).stdout.splitlines()
    assert "origin" not in remotes, f"git origin remote lookup failed: {result.stderr}"
    return ""


def project_id_from_remote(remote: str) -> str:
    stripped = remote.removesuffix(".git")
    is_ssh_remote = stripped.startswith("git@github.com:")
    is_https_remote = stripped.startswith("https://github.com/")
    if not (is_ssh_remote or is_https_remote):
        raise MemoryOperationError(f"cannot derive a project id from remote {remote}: only github.com remotes are recognized; pass --project-id to name the project explicitly")
    repository = stripped.removeprefix("git@github.com:") if is_ssh_remote else stripped.removeprefix("https://github.com/")
    parts = repository.split("/")
    if len(parts) != 2 or not all(parts):
        raise MemoryOperationError(
            f"cannot derive a project id from remote {remote}: expected github.com/<owner>/<repository>; pass --project-id to name the project explicitly"
        )
    owner, repo = parts
    return f"github.com__{owner}__{repo}"


def project_id_from_git_root(git_root: Path, remote: str) -> str:
    if remote:
        return project_id_from_remote(remote)
    return validate_project_id(git_root.name)


def validate_project_id(project_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", project_id):
        raise MemoryOperationError(f"invalid project id {project_id!r}; run `agent-memory doctor` to see the project ids this vault knows")
    return project_id


def config_from_agent_state_link(git_root: Path) -> ProjectConfig | None:
    linked_project_dirs: list[Path] = []
    for name in PROJECT_AGENT_STATE_DIRECTORIES:
        repo_path = git_root / name
        if repo_path.is_symlink():
            linked_project_dirs.append(repo_path.resolve())
    if not linked_project_dirs:
        return None
    project_dir = linked_project_dirs[0]
    if any(path != project_dir for path in linked_project_dirs):
        targets = ", ".join(sorted(str(path) for path in linked_project_dirs))
        raise MemoryOperationError(
            f"{'/'.join(PROJECT_AGENT_STATE_DIRECTORIES)} in {git_root} point at different vault projects ({targets}); repoint them at one project directory"
        )
    if project_dir.parent.name != "projects":
        raise MemoryOperationError(
            f"the agent-state symlink in {git_root} points at {project_dir}, which is not under a vault `projects` directory; repoint it or rerun `agent-memory init project`"
        )
    vault = project_dir.parent.parent
    project_id = validate_project_id(project_dir.name)
    if not (vault / ".agents" / "memories" / "config.toml").is_file():
        raise GlobalVaultNotInitializedError(vault)
    return ProjectConfig(
        vault=normalize_vault_path(vault),
        project_id=project_id,
    )


def find_project_config(cwd: Path) -> ProjectConfig | None:
    # Return the cwd repo's vault-backed project binding, or None when the directory is
    # not a git repo or lacks the agent-state symlink installed by init project.
    try:
        git_root = git_root_for(cwd)
    except subprocess.CalledProcessError:
        return None
    config = config_from_agent_state_link(git_root)
    if config is not None:
        return config
    # Binding is repository identity, not checkout identity: a linked worktree carries no
    # agent-state symlink of its own, so it inherits the primary checkout's binding and
    # every worktree of one repository resolves to one project (issue #101).
    primary = primary_worktree_root(cwd)
    if primary is None or primary == git_root:
        return None
    return config_from_agent_state_link(primary)


def primary_worktree_root(cwd: Path) -> Path | None:
    # Every worktree of a repository shares one common git directory; the primary checkout
    # is its parent. git reports that directory relative to cwd in the primary checkout and
    # absolutely from a linked worktree, so resolve it against cwd either way. A bare
    # repository has no primary checkout, so there is none to report.
    result = run_checked(["git", "rev-parse", "--git-common-dir"], cwd=cwd)
    common_dir = (cwd / result.stdout.strip()).resolve()
    if common_dir.name != ".git":
        return None
    return common_dir.parent


def load_project_config(cwd: Path) -> ProjectConfig:
    config = find_project_config(cwd)
    if config is None:
        raise ProjectNotInitializedError(global_vault_path())
    return config


def require_project_id(config: ProjectConfig) -> str:
    # Project-scoped paths read project_id through here, so a config that resolved to no
    # project fails loud instead of silently composing a wrong vault path.
    if config.project_id is None:
        raise ProjectNotInitializedError(config.vault)
    return config.project_id


def global_vault_path() -> Path:
    # The vault a scope-global operation targets when the cwd is unbound. Resolved from
    # AGENT_MEMORY_VAULT when set, else the shipped default vault. Independent of any cwd
    # project binding (issue #25).
    override = os.environ.get("AGENT_MEMORY_VAULT")
    if override is not None:
        if not override:
            raise MemoryOperationError(f"AGENT_MEMORY_VAULT is set to an empty string; point it at a vault or unset it to use {starter_config().default_vault}")
        return normalize_vault_path(Path(override))
    return starter_config().default_vault


def global_only_config() -> ProjectConfig:
    # Config for operations whose scope is global only. The global vault is the known
    # location, so no cwd project binding is required; project_id stays None because the
    # global scope root never reads it.
    vault = global_vault_path()
    if not (vault / ".agents" / "memories" / "config.toml").is_file():
        raise GlobalVaultNotInitializedError(vault)
    return ProjectConfig(
        vault=vault,
        project_id=None,
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
    raise ProjectNotInitializedError(global_vault_path())


def resolve_search_scope(scope: SearchScope, cwd: Path) -> tuple[ProjectConfig, SearchScope]:
    # The resolution boundary for every scope-addressed read: it answers which vault to
    # read AND which scope survives, because an unbound directory has no project half to
    # read. BOTH degrades to GLOBAL there rather than failing, so search, list, and inspect
    # work from `~`, `/tmp`, and scratchpads. Only an explicit `--scope project` still
    # fails, and it names the working alternative.
    config = find_project_config(cwd)
    if config is not None:
        return config, scope
    if scope is SearchScope.PROJECT:
        raise ProjectNotInitializedError(global_vault_path(), read_route=True)
    global_config = global_only_config()
    return global_config, available_search_scope(global_config, scope)


def config_for_key(key: str, cwd: Path) -> ProjectConfig:
    # The resolution boundary for every key-addressed read or mutation. Precedence: a
    # fully-qualified key names its own project, so it is never reinterpreted or re-homed
    # through the cwd binding (issue #88); a `global/...` key needs no project at all
    # (issue #108); only an unqualified key falls through to the cwd binding.
    binding = find_project_config(cwd)
    vault = binding.vault if binding is not None else global_only_config().vault
    parts = key.split("/")
    if parts[0] != "projects":
        # `global/...`, `harnesses/...`: the key is not project-scoped, so no binding is
        # required to read or write it. project_id carries the binding only for the paths
        # that legitimately need one.
        return ProjectConfig(vault=vault, project_id=binding.project_id if binding is not None else None)
    if len(parts) < 3:
        raise MemoryOperationError(
            f'{key!r} names no record. A project key is `projects/<project-id>/<type>/<slug>`. Run `agent-memory search --scope both "<term>"` to discover keys.'
        )
    return ProjectConfig(vault=vault, project_id=validate_project_id(parts[1]))


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
    if not isinstance(projects, list):
        raise MemoryOperationError(f"{projects_file} must hold a `projects` array of tables, each with project_id, root, and remote")
    records: list[ProjectRecord] = []
    for project in projects:
        if not isinstance(project, dict):
            raise MemoryOperationError(f"{projects_file} lists {project!r}, which is not a project table; each entry needs project_id, root, and remote")
        project_id = project["project_id"]
        root = project["root"]
        remote = project["remote"]
        if not isinstance(project_id, str) or not isinstance(root, str) or not isinstance(remote, str):
            raise MemoryOperationError(f"{projects_file} entry {project!r} must give project_id, root, and remote as strings")
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
def search_scope_memory_scopes(config: ProjectConfig, scope: SearchScope, *, both_order: tuple[MemoryScope, MemoryScope]) -> tuple[MemoryScope, ...]:
    scopes = {
        SearchScope.PROJECT: (MemoryScope.PROJECT,),
        SearchScope.GLOBAL: (MemoryScope.GLOBAL,),
        SearchScope.BOTH: both_order,
    }
    return scopes[available_search_scope(config, scope)]


def available_search_scope(config: ProjectConfig, scope: SearchScope) -> SearchScope:
    # A config that resolved to no project has no project half to read, so BOTH means
    # GLOBAL there. Every root-listing walk asks here, so a caller that hardcodes BOTH
    # degrades the same way an unbound `--scope both` does (issue #25).
    if scope is SearchScope.BOTH and config.project_id is None:
        return SearchScope.GLOBAL
    return scope


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
    return tuple(scope_root(config, memory_scope) for memory_scope in search_scope_memory_scopes(config, scope, both_order=CONTENT_SCOPE_ORDER))


def memory_key(vault: Path, path: Path) -> str:
    return path.relative_to(vault).with_suffix("").as_posix()


def memory_files(config: ProjectConfig, scope: SearchScope) -> tuple[Path, ...]:
    return tuple(
        path
        for directory in memory_note_directories(config, scope)
        for path in sorted(directory.glob("*.md"))
        if path.name != "index.md" and path.name not in PLAN_DAG_FILENAMES
    )


def memory_note_directories(config: ProjectConfig, scope: SearchScope) -> tuple[Path, ...]:
    scope_order = search_scope_memory_scopes(config, scope, both_order=CONTENT_SCOPE_ORDER)
    return tuple(memory_directory(config, memory_scope, memory_type) for memory_scope in scope_order for memory_type in MemoryType)


def managed_card_listings(config: ProjectConfig, scope: SearchScope) -> list[ManagedCardListing]:
    listings: list[ManagedCardListing] = []
    for path in managed_card_paths(config, scope):
        listing = managed_card_listing_for_path(config, path)
        if listing is not None:
            listings.append(listing)
    return listings


def managed_card_paths(config: ProjectConfig, scope: SearchScope) -> tuple[Path, ...]:
    paths: set[Path] = set()
    for directory in memory_note_directories(config, scope):
        paths.update(path for path in directory.glob("*.md") if path.name != "index.md")
    if scope in (SearchScope.PROJECT, SearchScope.BOTH):
        plans_root = memory_directory(config, MemoryScope.PROJECT, MemoryType.PLAN)
        paths.update(path for path in plans_root.rglob("*.md") if path.name != "index.md" and path.name not in PLAN_DAG_FILENAMES)
    return tuple(sorted(paths))


def unmigrated_card_listings(config: ProjectConfig, scope: SearchScope) -> list[UnmigratedCardListing]:
    listings: list[UnmigratedCardListing] = []
    for path in unmigrated_card_candidate_paths(config, scope):
        listing = unmigrated_card_listing_for_path(config, path)
        if listing is not None:
            listings.append(listing)
    return listings


def unmigrated_card_candidate_paths(config: ProjectConfig, scope: SearchScope) -> tuple[Path, ...]:
    roots: list[Path] = []
    if scope in (SearchScope.PROJECT, SearchScope.BOTH):
        roots.append(scope_root(config, MemoryScope.PROJECT))
    if scope in (SearchScope.GLOBAL, SearchScope.BOTH):
        roots.extend([config.vault / "harnesses", config.vault / "global"])
    candidates: set[Path] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*.md"):
            if is_internal_vault_path(path) or is_managed_card_path(config, path) or path.name == "index.md":
                continue
            if is_promotion_pointer_path(path):
                continue
            candidates.add(path)
    return tuple(sorted(candidates))


def is_promotion_pointer_path(path: Path) -> bool:
    # A promotion pointer is `maintain move` output, not a stranded card: reporting it as
    # unmigrated makes the sanctioned migration route produce a finding that never clears,
    # with a project-scoped destination that is wrong for a pointer (issue #107).
    try:
        document = read_memory(path)
    except MalformedMemoryError:
        return False
    tags = document.metadata.get("tags")
    return isinstance(tags, list) and PROMOTION_POINTER_TAG in tags


def is_internal_vault_path(path: Path) -> bool:
    return any(part in (".git", ".zk") for part in path.parts)


def is_managed_card_path(config: ProjectConfig, path: Path) -> bool:
    for scope in (MemoryScope.GLOBAL, MemoryScope.PROJECT):
        for memory_type in MemoryType:
            directory = memory_directory(config, scope, memory_type)
            if path.is_relative_to(directory):
                return True
    return False


def metadata_is_archived(metadata: Mapping[str, MetadataValue], path: Path) -> bool:
    value = metadata["archived"] if "archived" in metadata else False
    if not isinstance(value, bool):
        raise MalformedMemoryError(path, "frontmatter archived must be true or false")
    return value


def card_record_is_archived(record: CardRecord) -> bool:
    value = record.metadata["archived"] if "archived" in record.metadata else False
    if not isinstance(value, bool):
        raise MalformedMemoryError(record.path, "frontmatter archived must be true or false")
    return value


def archived_record_is_visible(archived: bool, visibility: ArchiveVisibility) -> bool:
    if visibility is ArchiveVisibility.ALL:
        return True
    return archived is (visibility is ArchiveVisibility.ARCHIVED)


def card_listing_fields_for_path(config: ProjectConfig, path: Path) -> tuple[str, str, MemoryScope, bool] | None:
    try:
        document = read_memory(path)
    except MalformedMemoryError:
        return None
    card_type = card_type_from_metadata(config, document.metadata)
    if card_type is None:
        return None
    return (
        card_title_from_metadata(path, document.metadata),
        card_type,
        card_scope_for_path(config, path, document.metadata),
        metadata_is_archived(document.metadata, path),
    )


def managed_card_listing_for_path(config: ProjectConfig, path: Path) -> ManagedCardListing | None:
    fields = card_listing_fields_for_path(config, path)
    if fields is None:
        return None
    title, card_type, scope, archived = fields
    return ManagedCardListing(
        title=title,
        card_type=card_type,
        scope=scope,
        path=path,
        key=memory_key(config.vault, path),
        archived=archived,
    )


def unmigrated_card_listing_for_path(config: ProjectConfig, path: Path) -> UnmigratedCardListing | None:
    fields = card_listing_fields_for_path(config, path)
    if fields is None:
        return None
    title, card_type, scope, archived = fields
    return UnmigratedCardListing(
        title=title,
        card_type=card_type,
        scope=scope,
        path=path,
        suggested_destination=suggested_card_destination(config, scope, card_type),
        archived=archived,
    )


def card_type_from_metadata(config: ProjectConfig, metadata: Mapping[str, MetadataValue]) -> str | None:
    type_value = metadata.get("type")
    if isinstance(type_value, str):
        return type_value
    id_value = metadata.get("id")
    if isinstance(id_value, str):
        return card_prefix_types(config).get(id_value.split("-", 1)[0])
    return None


def card_title_from_metadata(path: Path, metadata: Mapping[str, MetadataValue]) -> str:
    title = metadata.get("title")
    if isinstance(title, str):
        return title
    card_id = metadata.get("id")
    if isinstance(card_id, str):
        return card_id
    return path.stem


def card_scope_for_path(config: ProjectConfig, path: Path, metadata: Mapping[str, MetadataValue]) -> MemoryScope:
    scope = metadata.get("scope")
    if isinstance(scope, str) and scope in (MemoryScope.PROJECT.value, MemoryScope.GLOBAL.value):
        return MemoryScope(scope)
    if path.is_relative_to(scope_root(config, MemoryScope.PROJECT)):
        return MemoryScope.PROJECT
    return MemoryScope.GLOBAL


def suggested_card_destination(config: ProjectConfig, scope: MemoryScope, card_type: str) -> str:
    try:
        directory = MEMORY_TYPE_DIRECTORIES[MemoryType(card_type)]
    except ValueError:
        directory = MEMORY_TYPE_DIRECTORIES[MemoryType.PLAN]
    if scope is MemoryScope.GLOBAL:
        return f"global/{directory}"
    return f"projects/{require_project_id(config)}/{directory}"


def inspect_note_records(config: ProjectConfig, scope: SearchScope) -> tuple[NoteRecord, ...]:
    return scan_note_records(config, scope).records


def scan_note_records(config: ProjectConfig, scope: SearchScope) -> NoteScan:
    records: list[NoteRecord] = []
    findings: list[NoteFinding] = []
    for path in memory_files(config, scope):
        try:
            records.append(note_record_for_path(config, path))
        except MalformedMemoryError as error:
            findings.append(note_finding_for_error(config, path, error))
    return NoteScan(records=tuple(records), findings=tuple(findings))


def note_finding_for_error(config: ProjectConfig, path: Path, error: MalformedMemoryError) -> NoteFinding:
    return NoteFinding(
        path=path,
        key=memory_key(config.vault, path),
        message=str(error),
    )


def note_record_for_path(config: ProjectConfig, path: Path) -> NoteRecord:
    document = read_memory(path)
    stored_scope = metadata_memory_scope(document.metadata, path)
    layout_scope = MemoryScope(inspect_scope_for_path(config, path))
    if stored_scope is not layout_scope:
        raise MalformedMemoryError(path, f"frontmatter scope {stored_scope.value!r} does not match vault layout {layout_scope.value!r}")
    tags = metadata_string_tuple(document.metadata, "tags", path)
    return NoteRecord(
        key=memory_key(config.vault, path),
        path=path,
        title=metadata_string(document.metadata, "title", path),
        memory_type=metadata_memory_type(document.metadata, path),
        scope=stored_scope,
        tags=tags,
        timestamp=NoteTimestamp.from_metadata(document.metadata, path),
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
        and (created_after is None or record.timestamp.is_after(created_after))
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
        "timestamp": record.timestamp.json_value(),
    }


def metadata_search_record_json(record: NoteRecord) -> JsonObject:
    return {
        **note_record_core(record),
        "tags": json_list(record.tags),
        "timestamp": record.timestamp.json_value(),
    }


def note_path_record_json(record: NoteRecord) -> JsonObject:
    return {
        **note_record_core(record),
        "scope": record.scope.value,
    }


def note_finding_json(finding: NoteFinding) -> JsonObject:
    return {
        "path": str(finding.path),
        "key": finding.key,
        "message": finding.message,
    }


def note_findings_json(findings: Sequence[NoteFinding]) -> list[JsonValue]:
    return json_list([note_finding_json(finding) for finding in findings])


def parse_created_after(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise MemoryOperationError(f"--created-after {value!r} needs a timezone; write it as 2026-01-31T00:00:00Z")
    return parsed


def parse_memory_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise MemoryOperationError(f"timestamp {value!r} needs a timezone; write it as 2026-01-31T00:00:00Z")
    return parsed


def write_new_memory(path: Path, metadata: dict[str, MetadataValue], body: str) -> None:
    write_new_file(path, render_memory(metadata, body))


def write_memory(path: Path, metadata: dict[str, MetadataValue], body: str) -> None:
    path.write_text(render_memory(metadata, body), encoding="utf-8")


def render_memory(metadata: dict[str, MetadataValue], body: str) -> str:
    frontmatter = yaml.safe_dump(metadata, sort_keys=False)
    return f"---\n{frontmatter}---\n{body}"


OKF_FRONTMATTER_KEYS = frozenset(BaseNoteMetadata.model_fields) | {"project_id", "origin_project_id"}


def yaml_metadata_value(path: Path, key: str, value: object) -> MetadataValue:
    if isinstance(value, datetime):
        if key == "timestamp":
            if value.tzinfo is None:
                raise MalformedMemoryError(path, "timestamp must include timezone information")
            return value.isoformat().replace("+00:00", "Z")
        return value.isoformat()
    if isinstance(value, date):
        if key == "timestamp":
            raise MalformedMemoryError(path, "timestamp must include timezone information")
        return value.isoformat()
    if value is None or isinstance(value, str | bool | int | float):
        return value
    if isinstance(value, list):
        return [yaml_metadata_value(path, key, item) for item in value]
    if isinstance(value, dict):
        normalized: dict[str, MetadataValue] = {}
        for nested_key, nested_value in value.items():
            if not isinstance(nested_key, str):
                raise MalformedMemoryError(path, "frontmatter mapping keys must be strings")
            normalized[nested_key] = yaml_metadata_value(path, nested_key, nested_value)
        return normalized
    raise MalformedMemoryError(path, "frontmatter values must be YAML scalars, lists, or mappings")


def extract_embedded_frontmatter_blocks(path: Path, body: str) -> tuple[str, list[dict[str, MetadataValue]]]:
    lines = body.splitlines(keepends=True)
    cleaned: list[str] = []
    extras: list[dict[str, MetadataValue]] = []
    index = 0
    while index < len(lines):
        if lines[index].strip() != "---":
            cleaned.append(lines[index])
            index += 1
            continue

        end = next((candidate for candidate in range(index + 1, len(lines)) if lines[candidate].strip() == "---"), None)
        if end is None:
            cleaned.append(lines[index])
            index += 1
            continue

        block_text = "".join(lines[index + 1 : end])
        try:
            parsed = yaml.safe_load(block_text)
        except yaml.YAMLError as exc:
            raise MalformedMemoryError(path, "embedded frontmatter block must be valid YAML") from exc
        if not isinstance(parsed, dict):
            cleaned.extend(lines[index : end + 1])
            index = end + 1
            continue

        extra: dict[str, MetadataValue] = {}
        for key, value in parsed.items():
            if not isinstance(key, str):
                raise MalformedMemoryError(path, "embedded frontmatter keys must be strings")
            extra[key] = yaml_metadata_value(path, key, value)
        extras.append(extra)
        index = end + 1
    return "".join(cleaned), extras


def reconcile_okf_frontmatter(
    path: Path,
    primary: Mapping[str, MetadataValue],
    extras: Sequence[Mapping[str, MetadataValue]],
) -> dict[str, MetadataValue]:
    merged = dict(primary)
    for extra in extras:
        for key, value in extra.items():
            if key not in OKF_FRONTMATTER_KEYS:
                raise MalformedMemoryError(path, f"unreconcilable extra frontmatter key: {key}")
            if key in merged:
                if merged[key] != value:
                    raise MalformedMemoryError(path, f"conflicting values for {key}: {merged[key]!r} vs {value!r}")
                continue
            merged[key] = value
    return canonical_okf_metadata(path, merged)


def validate_okf_contract_defaults(path: Path, metadata: dict[str, MetadataValue]) -> None:
    if "source" in metadata and metadata_string(metadata, "source", path) != "agent":
        raise MalformedMemoryError(path, "frontmatter source must be agent")
    if "confidence" in metadata and metadata_string(metadata, "confidence", path) != "high":
        raise MalformedMemoryError(path, "frontmatter confidence must be high")
    if "promotable" in metadata and metadata_bool(metadata, "promotable", path):
        raise MalformedMemoryError(path, "frontmatter promotable must be false")


def reject_unexpected_okf_keys(path: Path, metadata: dict[str, MetadataValue], allowed: frozenset[str]) -> None:
    for key in metadata:
        if key not in allowed:
            raise MalformedMemoryError(path, f"invalid OKF frontmatter field: {key}")


def canonical_okf_metadata(path: Path, metadata: Mapping[str, MetadataValue]) -> dict[str, MetadataValue]:
    payload = dict(metadata)
    memory_type = metadata_memory_type(payload, path)
    scope = metadata_memory_scope(payload, path)
    validate_okf_contract_defaults(path, payload)
    title = metadata_string(payload, "title", path)
    description = metadata_string(payload, "description", path)
    tags = list(metadata_string_tuple(payload, "tags", path))
    timestamp = metadata_string(payload, "timestamp", path)
    try:
        if "origin_project_id" in payload:
            reject_unexpected_okf_keys(path, payload, frozenset(PromotedNoteMetadata.model_fields))
            return PromotedNoteMetadata(
                type=memory_type,
                title=title,
                description=description,
                tags=tags,
                timestamp=timestamp,
                scope=scope,
                origin_project_id=metadata_string(payload, "origin_project_id", path),
            ).to_yaml_payload()
        if scope is MemoryScope.PROJECT:
            reject_unexpected_okf_keys(path, payload, frozenset(ProjectNoteMetadata.model_fields))
            return ProjectNoteMetadata(
                type=memory_type,
                title=title,
                description=description,
                tags=tags,
                timestamp=timestamp,
                scope=scope,
                project_id=metadata_string(payload, "project_id", path),
            ).to_yaml_payload()
        reject_unexpected_okf_keys(path, payload, frozenset(GlobalNoteMetadata.model_fields))
        return GlobalNoteMetadata(
            type=memory_type,
            title=title,
            description=description,
            tags=tags,
            timestamp=timestamp,
            scope=scope,
        ).to_yaml_payload()
    except ValidationError as exc:
        raise MalformedMemoryError(path, f"invalid OKF frontmatter: {exc}") from exc


def reconcile_memory_file(path: Path) -> None:
    metadata, body = reconciled_memory_contents(path)
    write_memory(path, metadata, body)


def reconciled_memory_contents(path: Path) -> tuple[dict[str, MetadataValue], str]:
    document = read_memory(path)
    body, extras = extract_embedded_frontmatter_blocks(path, document.body)
    metadata = reconcile_okf_frontmatter(path, document.metadata, extras)
    return metadata, body


def normalize_memories(scope: SearchScope, cwd: Path) -> JsonObject:
    config, scope = resolve_search_scope(scope, cwd)
    selected_paths = memory_files(config, scope)
    reconciled = [(path, *reconciled_memory_contents(path)) for path in selected_paths]
    for path, metadata, body in reconciled:
        write_memory(path, metadata, body)
    selected_keys = None if scope is SearchScope.BOTH else [memory_key(config.vault, path) for path in selected_paths]
    normalized_keys = iwe.normalize(config.vault, selected_keys)
    index_zk_notebook(config.vault)
    changed_paths = [config.vault / f"{key}.md" for key in normalized_keys] if scope is SearchScope.BOTH else list(selected_paths)
    commit_vault_changes(config.vault, "Normalize vault Markdown and reconcile OKF frontmatter", paths=changed_paths)
    return {"scope": scope.value, "normalized": json_list(normalized_keys)}


def read_memory(path: Path) -> MemoryDocument:
    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise MalformedMemoryError(path, "memory file must be valid UTF-8") from error
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
        metadata[key] = yaml_metadata_value(path, key, value)
    return MemoryDocument(metadata=metadata, body=body)


def inspect_overview(
    *,
    scope: SearchScope,
    output_format: InspectOutputFormat,
    cwd: Path,
) -> JsonObject:
    assert output_format is InspectOutputFormat.JSON, "inspect overview currently emits JSON"
    config, scope = resolve_search_scope(scope, cwd)
    note_scan = scan_note_records(config, scope)
    notes = note_scan.records
    index_scan = scan_index_records(config, scope)
    indexes = index_scan.records
    return {
        "vault": str(config.vault),
        "project_id": config.project_id,
        "scope": scope.value,
        "roots": json_list(inspect_root_keys(config, scope)),
        "totals": {
            "notes": len(notes),
            "indexes": len(indexes),
        },
        "notes_by_scope": inspect_counts([note.scope.value for note in notes]),
        "notes_by_type": inspect_counts([note.memory_type.value for note in notes]),
        "findings": note_findings_json((*note_scan.findings, *index_scan.findings)),
    }


def inspect_schema(*, output_format: InspectOutputFormat, cwd: Path) -> JsonObject:
    assert output_format is InspectOutputFormat.JSON, "inspect schema currently emits JSON"
    config = config_for_schema_advertisement(cwd)
    cards_config, card_model_by_type = load_card_system(config)
    commands: JsonObject = {
        "inspect": list(INSPECT_COMMAND_NAMES),
        "card": ["add", "update", "delete", "show", "validate", "dag", "migrate"],
        "todo": ["set"],
        "card_types": [card_type.name for card_type in cards_config.card_types],
    }
    if any(card_type.name == "plan" for card_type in cards_config.card_types):
        commands["plan"] = ["add", "update", "delete", "show", "validate", "dag", "migrate", "progress"]
    return {
        "commands": commands,
        "scopes": [scope.value for scope in SearchScope],
        "memory_types": [memory_type.value for memory_type in WRITABLE_MEMORY_TYPES],
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
                    "field_count": len(card_fields(card_type)),
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
    config, scope = resolve_search_scope(scope, cwd)
    root_records = inspect_root_records(config, scope)
    index_scan = scan_index_records(config, scope)
    index_records = index_scan.records
    note_scan = scan_note_records(config, scope)
    note_records = tuple(note_path_record_json(record) for record in note_scan.records)
    records_by_kind = {
        InspectPathKind.ROOTS: root_records,
        InspectPathKind.INDEXES: index_records,
        InspectPathKind.NOTES: note_records,
        InspectPathKind.ALL: (*root_records, *index_records, *note_records),
    }
    result: JsonObject = {
        "scope": scope.value,
        "kind": kind.value,
        "paths": json_list(records_by_kind[kind]),
        "findings": note_findings_json(
            {
                InspectPathKind.ROOTS: (),
                InspectPathKind.INDEXES: index_scan.findings,
                InspectPathKind.NOTES: note_scan.findings,
                InspectPathKind.ALL: (*index_scan.findings, *note_scan.findings),
            }[kind]
        ),
    }
    return result


def inspect_tree(
    *,
    scope: SearchScope,
    depth: int,
    output_format: InspectOutputFormat,
    cwd: Path,
) -> JsonObject:
    assert output_format is InspectOutputFormat.JSON, "inspect tree currently emits JSON"
    if depth < 0:
        raise MemoryOperationError(f"inspect tree --depth must be 0 or more, got {depth}")
    config, scope = resolve_search_scope(scope, cwd)
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
    if depth < 0:
        raise MemoryOperationError(f"inspect links --depth must be 0 or more, got {depth}")
    config = config_for_key(key, cwd)
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
    config, scope = resolve_search_scope(scope, cwd)
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
    config = config_for_key(key, cwd)
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
    config, scope = resolve_search_scope(scope, cwd)
    note_scan = scan_note_records(config, scope)
    notes = note_scan.records
    counts_by_group = {
        InspectStatsGroup.TYPE: inspect_counts([note.memory_type.value for note in notes]),
        InspectStatsGroup.SCOPE: inspect_counts([note.scope.value for note in notes]),
        InspectStatsGroup.DAY: inspect_day_counts(notes),
    }
    return {"scope": scope.value, "by": group.value, "counts": counts_by_group[group], "findings": note_findings_json(note_scan.findings)}


def inspect_recent(
    *,
    scope: SearchScope,
    since: str,
    output_format: InspectOutputFormat,
    cwd: Path,
) -> JsonObject:
    assert output_format is InspectOutputFormat.JSON, "inspect recent currently emits JSON"
    since_datetime = parse_memory_timestamp(since)
    config, scope = resolve_search_scope(scope, cwd)
    note_scan = scan_note_records(config, scope)
    records = [record for record in note_scan.records if record.timestamp.is_after(since_datetime)]
    records.sort(key=lambda record: record.timestamp.sort_key(), reverse=True)
    return {"scope": scope.value, "since": since, "results": json_list([note_record_json(record) for record in records]), "findings": note_findings_json(note_scan.findings)}


def inspect_export(
    *,
    scope: SearchScope,
    profile: InspectExportProfile,
    output_format: InspectExportFormat,
    cwd: Path,
) -> JsonObject:
    assert output_format is InspectExportFormat.GRAPH_JSON, "inspect export currently emits graph-json"
    config, scope = resolve_search_scope(scope, cwd)
    scan = scan_inspect_export_records(config, scope)
    records_by_key = {record.key: record for record in scan.records}
    nodes = [inspect_export_node(config, record.key, record.path, record.document, profile) for record in sorted(records_by_key.values(), key=lambda record: record.key)]
    edges: list[JsonObject] = []
    for key, record in sorted(records_by_key.items()):
        for target in outgoing_link_keys(config, record.path):
            if target in records_by_key:
                edges.append({"source": key, "target": target})
    return {
        "scope": scope.value,
        "profile": profile.value,
        "format": output_format.value,
        "nodes": json_list(nodes),
        "edges": json_list(edges),
        "findings": note_findings_json(scan.findings),
    }


def scan_inspect_export_records(config: ProjectConfig, scope: SearchScope) -> InspectExportScan:
    records: list[InspectExportRecord] = []
    findings: list[NoteFinding] = []
    for path in inspect_markdown_paths(config, scope):
        try:
            document = inspect_export_document(config, path)
        except MalformedMemoryError as error:
            findings.append(note_finding_for_error(config, path, error))
            continue
        records.append(InspectExportRecord(key=memory_key(config.vault, path), path=path, document=document))
    return InspectExportScan(records=tuple(records), findings=tuple(findings))


def inspect_export_document(config: ProjectConfig, path: Path) -> MemoryDocument:
    if is_direct_memory_note_path(config, path):
        return note_record_for_path(config, path).document
    return read_memory(path)


def is_direct_memory_note_path(config: ProjectConfig, path: Path) -> bool:
    if path.name == "index.md" or path.name in PLAN_DAG_FILENAMES:
        return False
    return any(path.parent == directory for directory in memory_note_directories(config, SearchScope.BOTH))


def inspect_root_paths(config: ProjectConfig, scope: SearchScope) -> tuple[Path, ...]:
    return tuple(scope_root(config, memory_scope) for memory_scope in search_scope_memory_scopes(config, scope, both_order=INSPECT_SCOPE_ORDER))


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
    return scan_index_records(config, scope).records


def scan_index_records(config: ProjectConfig, scope: SearchScope) -> IndexScan:
    records: list[JsonObject] = []
    findings: list[NoteFinding] = []
    for path in inspect_markdown_paths(config, scope):
        if path.name != "index.md":
            continue
        try:
            document = read_memory(path)
        except MalformedMemoryError as error:
            findings.append(note_finding_for_error(config, path, error))
            continue
        records.append(
            {
                "key": memory_key(config.vault, path),
                "path": str(path),
                "title": first_heading_title(path, document.body),
                "scope": inspect_scope_for_path(config, path),
            }
        )
    return IndexScan(records=tuple(records), findings=tuple(findings))


def inspect_path_note_records(config: ProjectConfig, scope: SearchScope) -> tuple[JsonObject, ...]:
    return tuple(note_path_record_json(record) for record in inspect_note_records(config, scope))


def inspect_counts(values: Sequence[str]) -> JsonObject:
    counts = Counter(values)
    return {key: counts[key] for key in sorted(counts)}


def inspect_day_counts(records: Sequence[NoteRecord]) -> JsonObject:
    counts = Counter(parse_memory_timestamp(record.timestamp.value).date().isoformat() for record in records if record.timestamp.is_present())
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
    if not path.is_file():
        raise MemoryOperationError(
            f"no record at key {key!r} in {config.vault}. "
            "Keys are vault-relative, such as `projects/<project-id>/decisions/parser-choice` or "
            "`projects/<project-id>/plans/features/FEATURE-ID/FEATURE-ID`. "
            'Run `agent-memory search --scope both "<term>"` to discover keys.'
        )
    card_command = card_command_for_path(config, path)
    if card_command is not None:
        raise MemoryOperationError(
            f"{key} is a schema-backed card, not a memory note, so the memory commands cannot read or write it. "
            f"Show it with `agent-memory {card_command} show {path.stem}` and change it with "
            f"`agent-memory {card_command} update {path.stem} --set <field>=<value>`."
        )
    return path


def card_command_for_path(config: ProjectConfig, path: Path) -> str | None:
    # Where a record lives decides its kind, never the current directory: cards live in
    # per-card subdirectories of the project's card tree, while memory notes sit directly
    # in their type's directory. Returns the generated command that owns the card.
    if config.project_id is None:
        return None
    cards_root = memory_directory(config, MemoryScope.PROJECT, MemoryType.PLAN)
    if not path.is_relative_to(cards_root) or path.parent == cards_root:
        return None
    return card_prefix_types(config).get(path.stem.split("-", 1)[0])


def first_heading_title(path: Path, markdown: str) -> str:
    tokens = MARKDOWN_PARSER.parse(markdown)
    for index, token in enumerate(tokens):
        if token.type != "heading_open" or token.tag != "h1":
            continue
        assert index + 1 < len(tokens), "markdown heading must have body content"
        body = tokens[index + 1]
        assert body.type == "inline", "markdown heading body must be inline"
        title = body.content.strip()
        if not title:
            raise MalformedMemoryError(path, "body begins with an empty `# ` heading, so the record has no title")
        return title
    raise MalformedMemoryError(path, "body has no `# ` title heading")


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


def markdown_link_targets(markdown: str) -> tuple[str, ...]:
    """Path portion of every markdown link target in document order.

    Text-level, caller-agnostic: fragments are stripped, empty targets dropped, and no
    judgement is made about whether a target is intra-vault, relative, or external. Callers
    own that classification. Works on markdown text rather than a file so callers holding
    emitted bytes (rather than a path) share the same markdown-it walk.
    """
    tokens = MARKDOWN_PARSER.parse(markdown)
    targets: list[str] = []
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
            targets.append(target)
    return tuple(targets)


def outgoing_link_keys(config: ProjectConfig, path: Path) -> tuple[str, ...]:
    keys: list[str] = []
    for target in markdown_link_targets(path.read_text(encoding="utf-8")):
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
    if not key:
        raise MemoryOperationError(f"wikilink {raw_target!r} names no target; a wikilink is [[projects/<project-id>/<type>/<slug>]]")
    if key.startswith(("http://", "https://")):
        raise MemoryOperationError(f"wikilink {raw_target!r} is a URL; wikilinks address vault keys, so write a URL as a Markdown link instead")
    return key


def wikilink_fragment(raw_target: str) -> str | None:
    target = raw_target.split("|", 1)[0].strip()
    if "#" not in target:
        return None
    fragment = " ".join(target.split("#", 1)[1].split())
    if not fragment:
        raise MemoryOperationError(f"wikilink {raw_target!r} ends in `#` with no heading after it; drop the `#` or name a heading")
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
    if not target_path.is_relative_to(vault):
        raise MemoryOperationError(f"wikilink target {key!r} climbs out of the vault to {target_path}; wikilinks address keys inside {vault}")
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


def rewrite_wikilink_files(config: ProjectConfig, rewrites: Sequence[WikilinkRewrite]) -> list[JsonObject]:
    return rewrite_wikilink_paths(inspect_markdown_paths(config, SearchScope.BOTH), rewrites)


def rewrite_non_index_wikilink_files(config: ProjectConfig, rewrites: Sequence[WikilinkRewrite]) -> list[JsonObject]:
    return rewrite_wikilink_paths(
        [path for path in inspect_markdown_paths(config, SearchScope.BOTH) if path.name != "index.md"],
        rewrites,
    )


def rewrite_wikilink_paths(paths: Sequence[Path], rewrites: Sequence[WikilinkRewrite]) -> list[JsonObject]:
    records: list[JsonObject] = []
    for path in paths:
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
    if not isinstance(rewrites, dict):
        raise MemoryOperationError(f"{map_path} has no [rewrites] table: a rewrite map is a [rewrites] table mapping each old key to its new key")
    records: list[WikilinkRewrite] = []
    for from_target, to_target in rewrites.items():
        if not isinstance(to_target, str):
            raise MemoryOperationError(
                f"{map_path} maps {from_target!r} to {to_target!r}, which is not a key: a rewrite map is a [rewrites] table mapping each old key to its new key"
            )
        records.append(wikilink_rewrite(from_target, to_target))
    if not records:
        raise MemoryOperationError(f"{map_path} lists no rewrites: a rewrite map is a [rewrites] table mapping each old key to its new key")
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
        if from_target is None or to_target is None:
            raise MemoryOperationError("links rewrite needs both --from and --to, or a --map file listing the rewrites")
        rewrite = wikilink_rewrite(from_target, to_target)
        return {
            "from": rewrite.from_key,
            "to": rewrite.to_target,
            "rewritten": json_list(rewrite_wikilink_files(config, (rewrite,))),
        }
    if from_target is not None or to_target is not None:
        raise MemoryOperationError("links rewrite takes --map or --from/--to, not both; the map file already names every rewrite")
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
    return first_heading_title(path, document.body)


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
    document: MemoryDocument,
    profile: InspectExportProfile,
) -> JsonObject:
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
    if value is None or isinstance(value, str | bool | int | float):
        return value
    if isinstance(value, list):
        return json_list([json_metadata_value(item) for item in value])
    return {key: json_metadata_value(item) for key, item in value.items()}


# --- Plan cards (issue #4): bridge the config-driven card engine to the project vault ---


def load_card_system(config: ProjectConfig | None = None) -> tuple[CardSystemConfig, dict[str, type[BaseModel]]]:
    project_id = None if config is None else config.project_id
    cards_config = load_card_system_config(config.vault if config is not None else None, project_id)
    return cards_config, build_card_models(cards_config)


def load_global_queue_card_system(config: ProjectConfig) -> tuple[CardSystemConfig, dict[str, type[BaseModel]]]:
    schema_path = config.vault / "_meta" / "cards.yaml"
    if not schema_path.is_file():
        raise MemoryOperationError(f"global queue requires vault card schema: {schema_path}")
    cards_config = load_card_system_config(config.vault, None)
    if not any(card_type.name == QUEUE_CARD_TYPE for card_type in cards_config.card_types):
        raise MemoryOperationError(f"global queue schema must declare card type {QUEUE_CARD_TYPE}")
    return cards_config, build_card_models(cards_config)


def project_plans_root(config: ProjectConfig, cards_config: CardSystemConfig) -> Path:
    return config.vault / "projects" / require_project_id(config) / cards_config.root


def global_queue_root(config: ProjectConfig) -> Path:
    return config.vault / QUEUE_DIRECTORY


def all_plans_roots(config: ProjectConfig, cards_config: CardSystemConfig) -> list[Path]:
    records = load_project_records(config.vault / "_meta" / "projects.toml")
    return [config.vault / "projects" / record["project_id"] / cards_config.root for record in records]


LIST_FIELD_TYPES = ("string_list", "wikilink_list")


def coerce_scalar_field(field_type: str, value: str) -> object:
    assert field_type not in LIST_FIELD_TYPES, "list fields must be appended, not coerced"
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
) -> dict[str, object]:
    spec = next((card_type for card_type in cards_config.card_types if card_type.name == type_name), None)
    if spec is None:
        known_types = ", ".join(card_type.name for card_type in cards_config.card_types)
        raise CardFieldError(f"unknown card type {type_name}; known card types: {known_types}")
    field_specs = {field.name: field for field in card_fields(spec)}
    fields: dict[str, object] = {}
    for assignment in assignments:
        if "=" not in assignment:
            raise CardFieldError(f"field assignment must be key=value: {assignment}")
        key, value = assignment.split("=", 1)
        field = field_specs.get(key)
        if field is None:
            raise CardFieldError(f"unknown field {key} for card type {type_name}")
        if value == "":
            # `--set <field>=` means unset: clear a list, drop an optional scalar, refuse a
            # required one. Without a single rule the empty value fell through to coercion
            # and wrote `''`, which reads as a value the caller never set and which no later
            # update could remove.
            if field.required:
                raise CardFieldError(f"field {key} is required for card type {type_name}; `--set {key}=` means unset, so give it a value instead")
            fields[key] = [] if field.type in LIST_FIELD_TYPES else UNSET_FIELD
            continue
        if field.type in LIST_FIELD_TYPES:
            append_list_field(fields, key, value)
            continue
        try:
            fields[key] = coerce_scalar_field(field.type, value)
        except ValueError as e:
            if field.type not in ("int", "number"):
                raise
            raise CardFieldError(f"field {key} expects {field.type} value, got {value}") from e
    return fields


def rollback_created_vault_path(vault: Path, path: Path) -> None:
    run_checked_optional(["git", "reset", "HEAD", "--", str(path.relative_to(vault))], cwd=vault)
    if not path.exists():
        return
    path.unlink()
    parent_dir = path.parent
    while parent_dir != vault:
        try:
            parent_dir.rmdir()
            parent_dir = parent_dir.parent
        except OSError:
            break


def add_card(
    type_name: str,
    card_id: str,
    parent_id: str | None,
    assignments: Sequence[str],
    body: str,
    cwd: Path,
) -> JsonObject:
    if type_name == QUEUE_CARD_TYPE:
        raise MemoryOperationError("queue-item cards are global queue records; use `agent-memory queue add`")
    config = load_project_config(cwd)
    cards_config, models = load_card_system(config)
    fields = {key: value for key, value in parse_card_fields(cards_config, type_name, assignments).items() if value is not UNSET_FIELD}
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
        rollback_created_vault_path(config.vault, path)
        git_stderr = e.stderr or ""
        raise VaultCommitError(vault_commit_error_message(git_stderr)) from e

    return {"id": card_id, "path": str(path)}


def new_queue_card_id() -> str:
    return f"{QUEUE_CARD_ID_PREFIX}-{uuid4().hex.upper()}"


def queue_assignments(
    *,
    project: str | None,
    agent: str | None,
    status: str | None,
    summary: str | None,
    timestamp: str | None,
    links: Sequence[str],
    extra_assignments: Sequence[str],
) -> list[str]:
    assignments: list[str] = []
    for key, value in (
        ("project", project),
        ("agent", agent),
        ("status", status),
        ("summary", summary),
        ("timestamp", timestamp),
    ):
        if value is not None:
            assignments.append(f"{key}={value}")
    assignments.extend(f"links={link}" for link in links)
    assignments.extend(extra_assignments)
    return assignments


def add_queue_item(
    *,
    project: str | None,
    agent: str | None,
    status: str | None,
    summary: str | None,
    timestamp: str | None,
    links: Sequence[str],
    extra_assignments: Sequence[str],
    cwd: Path,
) -> JsonObject:
    config = config_for_memory_scope(MemoryScope.GLOBAL, cwd)
    cards_config, models = load_global_queue_card_system(config)
    card_id = new_queue_card_id()
    fields = parse_card_fields(
        cards_config,
        QUEUE_CARD_TYPE,
        queue_assignments(
            project=project,
            agent=agent,
            status=status,
            summary=summary,
            timestamp=timestamp,
            links=links,
            extra_assignments=extra_assignments,
        ),
    )
    body_title = summary if summary is not None else card_id
    path = create_card(
        global_queue_root(config),
        cards_config,
        models,
        type_name=QUEUE_CARD_TYPE,
        card_id=card_id,
        parent_id=None,
        fields=fields,
        body=f"# {body_title}\n",
    )

    try:
        commit_vault_changes(config.vault, f"Add queue item: {card_id}", paths=[path])
    except subprocess.CalledProcessError as e:
        rollback_created_vault_path(config.vault, path)
        git_stderr = e.stderr or ""
        raise VaultCommitError(vault_commit_error_message(git_stderr)) from e

    return {"id": card_id, "path": str(path)}


def json_card_value(value: object) -> JsonValue:
    if value is None or isinstance(value, str | bool | int | float):
        return value
    if isinstance(value, list):
        return json_list([json_card_value(item) for item in value])
    if isinstance(value, dict):
        return {str(key): json_card_value(item) for key, item in value.items()}
    raise AssertionError(f"card metadata value is not JSON-serializable: {value!r}")


def queue_item_json(record: CardRecord) -> JsonObject:
    card_id = record.metadata.get("id")
    assert isinstance(card_id, str), f"queue item must have string id: {record.path}"
    return {
        "id": card_id,
        "path": str(record.path),
        "metadata": {str(key): json_card_value(value) for key, value in record.metadata.items()},
    }


def list_queue_items(cwd: Path) -> JsonObject:
    config = config_for_memory_scope(MemoryScope.GLOBAL, cwd)
    cards_config, models = load_global_queue_card_system(config)
    scan = scan_card_records([global_queue_root(config)], cards_config, models)
    queue_records = [queue_item_json(record) for _card_id, record in sorted(scan.records.items()) if record.type_name == QUEUE_CARD_TYPE]
    return {"items": json_list(queue_records), "findings": json_list([card_load_finding_json(config, finding) for finding in scan.findings])}


def update_card_record(card_id: str, assignments: Sequence[str], cwd: Path, body: str | None = None) -> JsonObject:
    # body is None when the caller passed neither --body nor --body-file, which leaves the
    # stored Markdown alone; add_card takes the same argument as a required string because
    # a new card has no stored body to keep.
    config = load_project_config(cwd)
    cards_config, models = load_card_system(config)
    type_name = card_type_for_id(cards_config, card_id).name
    updates = parse_card_fields(cards_config, type_name, assignments)
    path = write_card_updates(project_plans_root(config, cards_config), cards_config, models, card_id, updates, body=body)
    commit_vault_changes(config.vault, f"Update card: {card_id}", paths=[path])
    return {"id": card_id, "path": str(path)}


def delete_card_record(card_id: str, cwd: Path) -> JsonObject:
    config = load_project_config(cwd)
    cards_config, _models = load_card_system(config)
    plans_root = project_plans_root(config, cards_config)
    path = find_card_path(plans_root, card_id)
    path.unlink()
    commit_vault_changes(config.vault, f"Delete card: {card_id}", paths=[path])
    return {"deleted": card_id}


def show_card(card_id: str, cwd: Path) -> JsonObject:
    config = load_project_config(cwd)
    cards_config, models = load_card_system(config)
    card_type = card_type_for_id(cards_config, card_id)
    path = find_card_path(project_plans_root(config, cards_config), card_id)
    metadata, body = split_card(path.read_text(encoding="utf-8"), path)
    validated = models[card_type.name].model_validate(metadata)
    return {
        "id": card_id,
        "type": card_type.name,
        "path": str(path),
        "metadata": validated.model_dump(exclude_unset=True),
        "body": body,
    }


def card_project_id(config: ProjectConfig, path: Path) -> str:
    # The project that owns a card file, read from its vault path. Cards live under
    # <vault>/projects/<project_id>/...; anything else is vault-level, not project-owned.
    relative = path.relative_to(config.vault).parts
    if relative[0] == "projects" and len(relative) > 1:
        return relative[1]
    return relative[0]


def local_card_closure(records: Mapping[str, CardRecord], cards_config: CardSystemConfig, local_root: Path) -> set[str]:
    # The card ids a local operation actually depends on: the touched project's own cards
    # plus everything they reference, transitively. A defect inside this set is causally
    # connected to local work; a defect outside it is another project's debt (issue #102).
    reference_fields = reference_field_names(cards_config)
    closure: set[str] = set()
    pending = [card_id for card_id, record in records.items() if record.path.is_relative_to(local_root)]
    while pending:
        card_id = pending.pop()
        if card_id in closure:
            continue
        closure.add(card_id)
        record = records.get(card_id)
        if record is None:
            continue
        for field_name in reference_fields[record.type_name]:
            pending.extend(wikilink_ids(record.metadata.get(field_name) or []))
    return closure


def card_scan_for_project(config: ProjectConfig, cards_config: CardSystemConfig, models: Mapping[str, type[BaseModel]]) -> tuple[CardScan, set[str], Path]:
    scan = scan_card_records(all_plans_roots(config, cards_config), cards_config, dict(models))
    local_root = project_plans_root(config, cards_config)
    return scan, local_card_closure(scan.records, cards_config, local_root), local_root


def card_load_finding_json(config: ProjectConfig, finding: CardLoadFinding) -> JsonObject:
    return {"kind": "unreadable", "card": finding.path.stem, "project": card_project_id(config, finding.path), "path": str(finding.path), "detail": finding.detail}


def validate_card_records(cwd: Path) -> JsonObject:
    config = load_project_config(cwd)
    cards_config, models = load_card_system(config)
    scan, closure, local_root = card_scan_for_project(config, cards_config, models)
    problems: list[JsonValue] = []
    unrelated: list[JsonValue] = []
    for finding in scan.findings:
        record = card_load_finding_json(config, finding)
        blocking = finding.path.is_relative_to(local_root) or finding.path.stem in closure
        (problems if blocking else unrelated).append(record)
    for problem in validate_cards(scan.records, cards_config):
        record = {"kind": problem.kind, "card": problem.card_id, "detail": problem.detail}
        if problem.card_id in closure:
            problems.append(record)
        else:
            owner = scan.records.get(problem.card_id)
            unrelated.append({**record, "project": card_project_id(config, owner.path) if owner is not None else None})
    return {
        "project_id": require_project_id(config),
        "problems": json_list(problems),
        "unrelated_vault_debt": json_list(unrelated),
    }


def write_card_dag(visibility: ArchiveVisibility, cwd: Path) -> JsonObject:
    config = load_project_config(cwd)
    cards_config, models = load_card_system(config)
    scan, closure, plans_root = card_scan_for_project(config, cards_config, models)
    closure_records = {card_id: scan.records[card_id] for card_id in sorted(closure) if card_id in scan.records}
    records = {
        card_id: record
        for card_id, record in closure_records.items()
        if archived_record_is_visible(card_record_is_archived(record), visibility)
    }
    findings = [card_load_finding_json(config, finding) for finding in scan.findings if finding.path.is_relative_to(plans_root) or finding.path.stem in closure]
    plans_root.mkdir(parents=True, exist_ok=True)
    path = plans_root / plan_dag_filename(visibility)
    path.write_text(render_dag(records), encoding="utf-8")
    commit_vault_changes(config.vault, "Update plan DAG", paths=[path])
    return {"path": str(path), "visibility": visibility.value, "findings": json_list(findings)}


def migrate_cards(source: Path, cwd: Path) -> JsonObject:
    config = load_project_config(cwd)
    cards_config, models = load_card_system(config)
    paths = migrate_plans(source, project_plans_root(config, cards_config), cards_config, models)
    commit_vault_changes(config.vault, f"Migrate {len(paths)} plan cards", paths=paths)
    return {"migrated": json_list([str(path) for path in paths])}
