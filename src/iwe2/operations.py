from __future__ import annotations

import json
import re
import shutil
import subprocess
import tomllib
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from importlib import resources
from pathlib import Path

import tomli_w
import yaml

from iwe2.models import (
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
    ProjectConfigFile,
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


OKF_VERSION = "0.1"
MARKDOWN_LINK_PATTERN = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
AGENTS_SECTION_START = "<!-- iwe2:agent-memory:start -->"
AGENTS_SECTION_END = "<!-- iwe2:agent-memory:end -->"
ZK_NOTEBOOK_DB_IGNORE = ".zk/notebook.db"
VAULT_GIT_USER_NAME = "iwe2"
VAULT_GIT_USER_EMAIL = "iwe2@localhost"
ROOT_INDEX_ENTRIES: tuple[IndexEntry, ...] = (("Global", "global/index.md", "Global memory shared across projects."),)
GLOBAL_INDEX_DESCRIPTIONS: dict[str, str] = {
    "decisions": "Global decision memories.",
    "traps": "Global traps memories.",
    "advice": "Global advice memories.",
    "context": "Global context memories.",
    "references": "Global reference memories.",
}
PROJECT_INDEX_DESCRIPTIONS: dict[str, str] = {
    "decisions": "Project decision memories.",
    "traps": "Project trap memories.",
    "advice": "Project advice memories.",
    "context": "Project context memories.",
    "references": "Project reference memories.",
}

MEMORY_TYPE_DIRECTORIES: dict[MemoryType, str] = {
    MemoryType.DECISION: "decisions",
    MemoryType.TRAP: "traps",
    MemoryType.ADVICE: "advice",
    MemoryType.CONTEXT: "context",
    MemoryType.REFERENCE: "references",
}

GLOBAL_INDEX_DIRECTORIES: tuple[str, ...] = (
    "decisions",
    "traps",
    "advice",
    "context",
    "references",
)

VAULT_DIRECTORIES: tuple[Path, ...] = (
    Path("global/decisions"),
    Path("global/traps"),
    Path("global/advice"),
    Path("global/context"),
    Path("global/references"),
    Path("projects"),
    Path("inbox/unsorted"),
    Path("inbox/project"),
    Path("inbox/global"),
    Path("templates"),
    Path("_meta"),
)

PROJECT_DIRECTORIES: tuple[str, ...] = (
    "decisions",
    "traps",
    "advice",
    "context",
    "references",
)
BASIC_DEPENDENCIES: tuple[DependencyCheck, ...] = (
    DependencyCheck(
        "git",
        ("git", "--version"),
        "run `just setup` from the iwe2 checkout; manual install: install Git from your OS package manager.",
    ),
    DependencyCheck(
        "iwe",
        ("iwe", "--version"),
        "run `just setup` from the iwe2 checkout; manual install: run `cargo install iwe iwes iwec`.",
    ),
    DependencyCheck(
        "rg",
        ("rg", "--version"),
        "run `just setup` from the iwe2 checkout; manual install: run `cargo install ripgrep`.",
    ),
    DependencyCheck(
        "npx",
        ("npx", "--version"),
        "run `just setup` from the iwe2 checkout; manual install: install Node.js with npm/npx.",
    ),
    DependencyCheck(
        "@probelabs/probe",
        ("npx", "-y", "@probelabs/probe@latest", "--version"),
        "run `just setup` from the iwe2 checkout; manual install: run `npx -y @probelabs/probe@latest --version`.",
    ),
    DependencyCheck(
        "zk",
        ("zk", "--version"),
        "run `just setup` from the iwe2 checkout; manual install: install zk v0.15.5 to a directory on PATH.",
    ),
)


@dataclass(frozen=True)
class StarterConfig:
    default_vault: Path
    global_scopes: tuple[str, ...]
    search_max_results: int
    search_max_tokens: int


class UsageError(RuntimeError):
    """Raised for invalid user setup state at the CLI boundary."""


def starter_config() -> StarterConfig:
    payload = tomllib.loads(resources.files("iwe2.defaults").joinpath("global.toml").read_text(encoding="utf-8"))
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
        default_vault=Path(default_vault).expanduser(),
        global_scopes=tuple(global_scopes),
        search_max_results=search_max_results,
        search_max_tokens=search_max_tokens,
    )


@dataclass(frozen=True)
class MemoryDocument:
    metadata: dict[str, MetadataValue]
    body: str

    def metadata_str(self, key: str) -> str:
        value = self.metadata[key]
        assert isinstance(value, str), f"metadata field must be a string: {key}"
        return value


@dataclass(frozen=True)
class NoteRecord:
    key: str
    path: Path
    title: str
    memory_type: MemoryType
    scope: MemoryScope
    tags: tuple[str, ...]
    timestamp: str
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


def init_global_vault(vault: Path) -> JsonObject:
    vault.mkdir(parents=True)
    run_checked(["git", "init"], cwd=vault)
    configure_vault_git(vault)
    write_new_file(vault / ".gitignore", f"{ZK_NOTEBOOK_DB_IGNORE}\n")
    run_checked(["zk", "--no-input", "init", str(vault)], cwd=vault)
    run_checked(["iwe", "init"], cwd=vault)
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
                directory_index_entries(GLOBAL_INDEX_DIRECTORIES, GLOBAL_INDEX_DESCRIPTIONS),
            ),
        ),
    )
    write_section_indexes(vault / "global", GLOBAL_INDEX_DIRECTORIES)
    write_new_file(vault / "_meta" / "projects.toml", tomli_w.dumps({"projects": []}))
    index_zk_notebook(vault)
    commit_vault_changes(vault, "Initialize iwe2 vault")
    return {"vault": str(vault)}


def init_project(vault: Path, cwd: Path) -> JsonObject:
    assert (vault / ".iwe" / "config.toml").is_file(), "vault must be initialized with IWE"
    starter = starter_config()
    git_root = git_root_for(cwd)
    remote = git_remote(git_root)
    project_id = project_id_from_remote(remote)
    project_dir = vault / "projects" / project_id
    project_dir.mkdir(parents=True)
    write_new_file(
        project_dir / "index.md",
        render_memory(
            {"okf_version": OKF_VERSION},
            parent_index_body(
                project_id,
                directory_index_entries(
                    PROJECT_DIRECTORIES,
                    PROJECT_INDEX_DESCRIPTIONS,
                ),
            ),
        ),
    )
    for directory in PROJECT_DIRECTORIES:
        (project_dir / directory).mkdir()
    write_section_indexes(project_dir, PROJECT_DIRECTORIES)
    append_index_link(
        vault / "index.md",
        project_id,
        f"projects/{project_id}/index.md",
        "Project memory bundle.",
    )

    config = ProjectConfig(
        vault=vault,
        project_id=project_id,
        project_root_strategy="git-root",
        global_scopes=starter.global_scopes,
        search_max_results=starter.search_max_results,
        search_max_tokens=starter.search_max_tokens,
    )
    write_new_file(git_root / ".agent-memory.toml", tomli_w.dumps(config.to_toml_payload()))
    write_agents_pointer(git_root, vault, project_id)
    append_project_record(
        vault / "_meta" / "projects.toml",
        {"project_id": project_id, "root": str(git_root), "remote": remote},
    )
    index_zk_notebook(vault)
    commit_vault_changes(vault, f"Register project {project_id}")
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
    config = load_project_config(cwd)
    slug = memory_slug(title)
    directory = memory_directory(config, scope, memory_type)
    path = directory / f"{slug}.md"
    key = memory_key(config.vault, path)
    description = okf_description(content)
    metadata = note_metadata(config, scope, memory_type, title, description)
    body = f"# {title}\n\n{content}\n"
    write_new_memory(path, metadata, body)
    append_index_link(directory / "index.md", title, path.name, description)
    index_zk_notebook(config.vault)
    commit_vault_changes(config.vault, f"Record {scope.value} {memory_type.value} memory: {title}")
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
    old_title = metadata_string(document.metadata, "title")
    scope = MemoryScope(metadata_string(document.metadata, "scope"))
    old_type = MemoryType(metadata_string(document.metadata, "type"))
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
        raise UsageError("Update requires at least one of --title, --type, or --content.")
    config = load_project_config(cwd)
    transition = memory_transition(config, key, title, memory_type, content)
    if transition.new_key != transition.old_key:
        run_checked(["iwe", "rename", transition.old_key, transition.new_key], cwd=config.vault)
    write_memory(transition.destination_path, transition.metadata, transition.body)
    sync_memory_transition_indexes(transition)
    index_zk_notebook(config.vault)
    commit_vault_changes(
        config.vault,
        f"Update {transition.scope.value} {transition.memory_type.value} memory: {transition.new_title}",
    )
    return {"key": transition.new_key, "path": str(transition.destination_path)}


def delete_memory(key: str, cwd: Path) -> JsonObject:
    config = load_project_config(cwd)
    path = config.vault / f"{key}.md"
    document = read_memory(path)
    title = metadata_string(document.metadata, "title")
    remove_index_link(path.parent / "index.md", title)
    run_checked(["iwe", "delete", key, "-f", "keys"], cwd=config.vault)
    index_zk_notebook(config.vault)
    commit_vault_changes(config.vault, f"Delete memory: {title}")
    return {"deleted": key}


def search_memories(scope: SearchScope, query: str, cwd: Path) -> JsonObject:
    config = load_project_config(cwd)
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
        "results": json_record_list(results),
        "key_matches": json_record_list(key_matches),
        "exact_content_matches": json_record_list(exact_matches),
        "fuzzy_content_matches": json_record_list(fuzzy_matches),
        "ranked_content_matches": ranked_results,
    }


def search_keys(scope: SearchScope, query: str, cwd: Path) -> JsonObject:
    config = load_project_config(cwd)
    return {
        "query": query,
        "scope": scope.value,
        "results": json_record_list(key_search_records(config, scope, query)),
    }


def search_content_exact(scope: SearchScope, query: str, cwd: Path) -> JsonObject:
    config = load_project_config(cwd)
    return {
        "query": query,
        "scope": scope.value,
        "results": json_record_list(exact_content_records(config, scope, query)),
    }


def search_metadata(
    scope: SearchScope,
    memory_type: MemoryType | None,
    tag: str | None,
    created_after: str | None,
    cwd: Path,
) -> JsonObject:
    config = load_project_config(cwd)
    created_after_datetime = parse_created_after(created_after)
    records = [
        metadata_search_record_json(record) for record in inspect_note_records(config, scope) if note_record_matches_metadata(record, memory_type, tag, created_after_datetime)
    ]
    return {
        "scope": scope.value,
        "results": json_record_list(records[: config.search_max_results]),
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
    output = run_ripgrep_search(
        [
            "rg",
            "--line-number",
            "--with-filename",
            "--fixed-strings",
            query,
            *[str(root) for root in search_roots(config, scope)],
        ],
        cwd=config.vault,
    )
    records: list[JsonObject] = []
    for line in output.splitlines():
        path_text, line_number_text, text = line.split(":", 2)
        path = Path(path_text).resolve()
        records.append(
            {
                "key": memory_key(config.vault, path),
                "path": str(path),
                "line": int(line_number_text),
                "text": text,
                "source": "exact",
            }
        )
    return dedupe_records_by_key(records)[: config.search_max_results]


def fuzzy_content_records(config: ProjectConfig, scope: SearchScope, query: str) -> list[JsonObject]:
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
    return dedupe_records_by_key(results)[: config.search_max_results]


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


def json_record_list(records: Sequence[JsonObject]) -> list[JsonValue]:
    return [record for record in records]


def json_string_list(values: Sequence[str]) -> list[JsonValue]:
    return [value for value in values]


def search_content_ranked(scope: SearchScope, query: str, cwd: Path) -> JsonObject:
    config = load_project_config(cwd)
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
    config = load_project_config(cwd)
    results: list[JsonValue] = []
    for root in search_roots(config, scope):
        results.extend(
            zk_search_root(
                vault=config.vault,
                root=root,
                query=query,
                limit=config.search_max_results,
            )
        )
    return {
        "query": query,
        "scope": scope.value,
        "results": results[: config.search_max_results],
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
    json_results: list[JsonValue] = []
    for result in ranked_results:
        json_results.append(result)
    json_skipped_files: list[JsonValue] = []
    for skipped_file in skipped_files:
        json_skipped_files.append(skipped_file)
    return {
        "limits": {
            "max_bytes": None,
            "max_results": max_results,
            "max_tokens": max_tokens,
            "total_bytes": total_bytes,
            "total_tokens": total_tokens,
        },
        "results": json_results,
        "skipped_files": json_skipped_files,
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
    if "skipped_files" not in payload:
        return []
    raw_skipped = payload["skipped_files"]
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
    result = run_checked(["iwe", "retrieve", "-k", key], cwd=config.vault)
    return result.stdout


def squash_memory(key: str, depth: int, cwd: Path) -> str:
    assert depth > 0, f"squash depth must be positive: {depth}"
    config = load_project_config(cwd)
    result = run_checked(["iwe", "squash", key, "--depth", str(depth)], cwd=config.vault)
    return result.stdout


def split_memory(key: str, section: str, cwd: Path) -> JsonObject:
    config = load_project_config(cwd)
    source_document = read_memory(memory_path_for_key(config, key))
    source_title = source_document.metadata_str("title")
    memory_type = MemoryType(source_document.metadata_str("type"))
    scope = MemoryScope(source_document.metadata_str("scope"))
    result = run_checked(["iwe", "extract", key, "--section", section, "-f", "keys"], cwd=config.vault)
    extracted_keys: list[str] = []
    for affected_key in result.stdout.splitlines():
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
    index_zk_notebook(config.vault)
    commit_vault_changes(config.vault, f"Split memory section: {section}")
    return {"key": key, "section": section, "output": result.stdout, "extracted": json_string_list(extracted_keys)}


def merge_memory(key: str, reference: str, cwd: Path) -> JsonObject:
    config = load_project_config(cwd)
    result = run_checked(["iwe", "inline", key, "--reference", reference, "-f", "keys"], cwd=config.vault)
    index_zk_notebook(config.vault)
    commit_vault_changes(config.vault, f"Merge memory reference: {reference}")
    return {"key": key, "reference": reference, "output": result.stdout}


def validate_memory_vault(cwd: Path) -> JsonObject:
    return doctor(cwd)


def move_memory(key: str, destination: str, cwd: Path) -> JsonObject:
    config = load_project_config(cwd)
    assert destination.startswith("global/"), "move destination must be global"
    source_path = config.vault / f"{key}.md"
    assert source_path.is_file(), "memory to move must exist"
    destination_key = f"{destination}/{source_path.stem}"
    destination_path = config.vault / f"{destination_key}.md"
    assert destination_path.parent.is_dir(), "move destination directory must exist"

    source_document = read_memory(source_path)
    memory_type = MemoryType(source_document.metadata_str("type"))
    title = source_document.metadata_str("title")
    description = source_document.metadata_str("description")
    run_checked(["iwe", "rename", key, destination_key], cwd=config.vault)

    moved_document = read_memory(destination_path)
    moved_metadata = PromotedNoteMetadata(
        type=memory_type,
        title=title,
        description=description,
        tags=okf_tags(MemoryScope.GLOBAL, memory_type, ("promoted",)),
        timestamp=okf_timestamp(),
        scope=MemoryScope.GLOBAL,
        source="agent",
        confidence="high",
        promotable=False,
        origin_project_id=config.project_id,
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
        source="agent",
        confidence="high",
        promotable=False,
        project_id=config.project_id,
    ).to_yaml_payload()
    pointer_body = f"# {title}\n\nPromoted to [[{destination_key}]].\n"
    assert source_path.parent.is_dir(), "project memory parent must be a directory"
    write_new_memory(source_path, pointer_metadata, pointer_body)
    replace_index_link(source_path.parent / "index.md", title, title, source_path.name, pointer_description)
    index_zk_notebook(config.vault)
    commit_vault_changes(config.vault, f"Move memory {key} to {destination_key}")
    return {"key": destination_key, "path": str(destination_path)}


def check_dependency(dependency: DependencyCheck, cwd: Path) -> JsonObject:
    if shutil.which(dependency.command[0]) is None:
        raise UsageError(f"Missing required dependency: {dependency.name}.\nInstall instructions: {dependency.install_instructions}")
    result = subprocess.run(dependency.command, cwd=cwd, check=False, text=True, capture_output=True)
    if result.returncode != 0:
        message_parts = [
            f"Dependency check failed: {dependency.name}.",
            f"Command: {' '.join(dependency.command)}",
            f"Install instructions: {dependency.install_instructions}",
            f"stdout: {result.stdout.strip()}",
            f"stderr: {result.stderr.strip()}",
        ]
        raise UsageError("\n".join(message_parts))
    return {"name": dependency.name, "command": list(dependency.command), "status": "ok"}


def basic_doctor(cwd: Path) -> JsonObject:
    dependencies = [check_dependency(dependency, cwd) for dependency in BASIC_DEPENDENCIES]
    return {
        "dependencies": json_record_list(dependencies),
        "tools": [dependency.name for dependency in BASIC_DEPENDENCIES],
    }


def doctor(cwd: Path) -> JsonObject:
    basic = basic_doctor(cwd)
    config = load_project_config(cwd)
    git_root = git_root_for(cwd)
    assert (config.vault / ".zk" / "config.toml").is_file(), "vault must be initialized with zk"
    assert (config.vault / ".zk" / "templates" / "default.md").is_file(), "zk default template must exist"
    return {
        "vault": str(config.vault),
        "project_id": config.project_id,
        "project_root": str(git_root),
        "tools": basic["tools"],
        "dependencies": basic["dependencies"],
    }


def run_checked(args: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, text=True, capture_output=True)


def commit_vault_changes(vault: Path, message: str) -> None:
    run_checked(["git", "add", "--all", "."], cwd=vault)
    run_checked(["git", "commit", "-m", message], cwd=vault)


def configure_vault_git(vault: Path) -> None:
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


def agents_pointer_section(vault: Path, project_id: str) -> str:
    return (
        f"{AGENTS_SECTION_START}\n"
        "# Agent memory\n\n"
        f"This repository uses the central agent memory vault at `{vault}`.\n\n"
        f"Project memory key: `projects/{project_id}/index`.\n\n"
        "Before changing architecture, search both project and global memory:\n\n"
        "```bash\n"
        'iwe2 search --scope both "<task or subsystem>"\n'
        "```\n\n"
        "Record durable repo-specific lessons with:\n\n"
        "```bash\n"
        "iwe2 add --scope project --type decision --title <title> --content <content>\n"
        "iwe2 add --scope project --type trap --title <title> --content <content>\n"
        "iwe2 add --scope project --type workflow --title <title> --content <content>\n"
        "```\n\n"
        "Use `iwe2 retrieve <key>`, `iwe2 update <key>`, and `iwe2 delete <key>` for memory CRUD.\n\n"
        "Move reusable lessons during maintenance with:\n\n"
        "```bash\n"
        "iwe2 maintain move <key> --to global/advice\n"
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
    assert has_start == has_end, f"malformed iwe2 AGENTS section in {agents_path}"
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


def replace_index_link(index_path: Path, existing_title: str, new_title: str, target: str, description: str) -> None:
    assert index_path.is_file(), "index must exist before replacing a link"
    # IWE rewrites the OKF bullet marker to "-" when it renames linked notes.
    link_prefixes = (f"* [{existing_title}](", f"- [{existing_title}](")
    lines = index_path.read_text(encoding="utf-8").splitlines()
    matching_indexes = [index for index, line in enumerate(lines) if any(line.startswith(prefix) for prefix in link_prefixes)]
    assert len(matching_indexes) == 1, "index must contain exactly one link for the title"
    entry_start = matching_indexes[0]
    lines[entry_start] = okf_index_entry(new_title, target, description)
    index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def remove_index_link(index_path: Path, title: str) -> None:
    assert index_path.is_file(), "index must exist before removing a link"
    link_prefixes = (f"* [{title}](", f"- [{title}](")
    lines = index_path.read_text(encoding="utf-8").splitlines()
    matching_indexes = [index for index, line in enumerate(lines) if any(line.startswith(prefix) for prefix in link_prefixes)]
    assert len(matching_indexes) == 1, "index must contain exactly one removable link for the title"
    entry_start = matching_indexes[0]
    del lines[entry_start]
    index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def metadata_string(metadata: dict[str, MetadataValue], key: str) -> str:
    value = metadata[key]
    assert isinstance(value, str), f"metadata field {key} must be a string"
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


def load_project_config(cwd: Path) -> ProjectConfig:
    git_root = git_root_for(cwd)
    config_path = git_root / ".agent-memory.toml"
    if not config_path.is_file():
        starter = starter_config()
        raise UsageError(
            "No project memory config found. Run `iwe2 maintain init-global --vault "
            f"{starter.default_vault}` once if the global vault does not exist, then run "
            "`iwe2 init project --vault <path-to-global-vault>` from this repository."
        )
    raw = ProjectConfigFile.model_validate(tomllib.loads(config_path.read_text(encoding="utf-8")))
    return ProjectConfig.from_file_payload(raw)


def append_project_record(projects_file: Path, record: ProjectRecord) -> None:
    records = load_project_records(projects_file)
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


def memory_directory(config: ProjectConfig, scope: MemoryScope, memory_type: MemoryType) -> Path:
    directory_name = MEMORY_TYPE_DIRECTORIES[memory_type]
    directories = {
        MemoryScope.PROJECT: config.vault / "projects" / config.project_id / directory_name,
        MemoryScope.GLOBAL: config.vault / "global" / directory_name,
    }
    return directories[scope]


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
            source="agent",
            confidence="high",
            promotable=False,
            project_id=config.project_id,
        ).to_yaml_payload()
    assert scope is MemoryScope.GLOBAL, f"unsupported note scope: {scope}"
    return GlobalNoteMetadata(
        type=memory_type,
        title=title,
        description=description,
        tags=okf_tags(scope, memory_type, ()),
        timestamp=timestamp,
        scope=MemoryScope.GLOBAL,
        source="agent",
        confidence="high",
        promotable=False,
    ).to_yaml_payload()


def run_ripgrep_search(args: Sequence[str], cwd: Path) -> str:
    result = subprocess.run(args, cwd=cwd, check=False, text=True, capture_output=True)
    if result.returncode == 0:
        return result.stdout
    assert result.returncode == 1, f"ripgrep search failed with exit code {result.returncode}: {result.stderr}"
    assert result.stdout == ""
    assert result.stderr == ""
    return ""


def search_roots(config: ProjectConfig, scope: SearchScope) -> tuple[Path, ...]:
    project_root = config.vault / "projects" / config.project_id
    global_root = config.vault / "global"
    roots = {
        SearchScope.PROJECT: (project_root,),
        SearchScope.GLOBAL: (global_root,),
        SearchScope.BOTH: (project_root, global_root),
    }
    return roots[scope]


def memory_key(vault: Path, path: Path) -> str:
    return path.relative_to(vault).with_suffix("").as_posix()


def memory_files(config: ProjectConfig, scope: SearchScope) -> tuple[Path, ...]:
    return tuple(path for directory in memory_note_directories(config, scope) for path in sorted(directory.glob("*.md")) if path.name != "index.md")


def memory_note_directories(config: ProjectConfig, scope: SearchScope) -> tuple[Path, ...]:
    scope_order = {
        SearchScope.PROJECT: (MemoryScope.PROJECT,),
        SearchScope.GLOBAL: (MemoryScope.GLOBAL,),
        SearchScope.BOTH: (MemoryScope.PROJECT, MemoryScope.GLOBAL),
    }[scope]
    return tuple(memory_directory(config, memory_scope, memory_type) for memory_scope in scope_order for memory_type in MemoryType)


def inspect_note_records(config: ProjectConfig, scope: SearchScope) -> tuple[NoteRecord, ...]:
    return tuple(note_record_for_path(config, path) for path in memory_files(config, scope))


def note_record_for_path(config: ProjectConfig, path: Path) -> NoteRecord:
    document = read_memory(path)
    stored_scope = MemoryScope(metadata_string(document.metadata, "scope"))
    layout_scope = MemoryScope(inspect_scope_for_path(config, path))
    assert stored_scope is layout_scope, "memory note metadata scope must match vault layout"
    tags = document.metadata["tags"]
    assert isinstance(tags, list), "memory tags must be a list"
    assert all(isinstance(tag_value, str) for tag_value in tags), "memory tags must contain strings"
    return NoteRecord(
        key=memory_key(config.vault, path),
        path=path,
        title=metadata_string(document.metadata, "title"),
        memory_type=MemoryType(metadata_string(document.metadata, "type")),
        scope=stored_scope,
        tags=tuple(tags),
        timestamp=metadata_string(document.metadata, "timestamp"),
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
        and (created_after is None or parse_memory_timestamp(record.timestamp) > created_after)
    )


def note_record_json(record: NoteRecord) -> JsonObject:
    return {
        "key": record.key,
        "path": str(record.path),
        "title": record.title,
        "type": record.memory_type.value,
        "scope": record.scope.value,
        "tags": json_string_list(record.tags),
        "timestamp": record.timestamp,
    }


def metadata_search_record_json(record: NoteRecord) -> JsonObject:
    return {
        "key": record.key,
        "path": str(record.path),
        "title": record.title,
        "type": record.memory_type.value,
        "tags": json_string_list(record.tags),
        "timestamp": record.timestamp,
    }


def note_path_record_json(record: NoteRecord) -> JsonObject:
    return {
        "key": record.key,
        "path": str(record.path),
        "title": record.title,
        "type": record.memory_type.value,
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
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    assert lines[0].strip() == "---", "memory must start with frontmatter"
    closing_index = next(index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    parsed = yaml.safe_load("".join(lines[1:closing_index]))
    assert isinstance(parsed, dict), "frontmatter must be a mapping"
    metadata: dict[str, MetadataValue] = {}
    for key, value in parsed.items():
        assert isinstance(key, str), "frontmatter keys must be strings"
        if isinstance(value, datetime):
            assert key == "timestamp", "only timestamp may be parsed as a YAML datetime"
            assert value.tzinfo is not None, "timestamp must include timezone information"
            metadata[key] = value.isoformat().replace("+00:00", "Z")
        elif isinstance(value, list):
            assert all(isinstance(item, str) for item in value), "frontmatter lists must contain strings"
            metadata[key] = value
        else:
            assert isinstance(value, str | bool), "frontmatter values must be strings, booleans, datetimes, or string lists"
            metadata[key] = value
    body = "".join(lines[closing_index + 1 :])
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
        "project_id": config.project_id,
        "scope": scope.value,
        "roots": json_string_list(inspect_root_keys(config, scope)),
        "totals": {
            "notes": len(notes),
            "indexes": len(indexes),
        },
        "notes_by_scope": inspect_counts([note.scope.value for note in notes]),
        "notes_by_type": inspect_counts([note.memory_type.value for note in notes]),
    }


def inspect_schema(*, output_format: InspectOutputFormat) -> JsonObject:
    assert output_format is InspectOutputFormat.JSON, "inspect schema currently emits JSON"
    return {
        "commands": {
            "inspect": [
                "overview",
                "schema",
                "paths",
                "tree",
                "links",
                "outline",
                "stats",
                "recent",
                "export",
            ]
        },
        "scopes": [scope.value for scope in SearchScope],
        "memory_types": [memory_type.value for memory_type in MemoryType],
        "path_kinds": [kind.value for kind in InspectPathKind],
        "link_directions": [direction.value for direction in InspectLinkDirection],
        "stats_groups": [group.value for group in InspectStatsGroup],
        "export_profiles": [profile.value for profile in InspectExportProfile],
        "formats": {
            "inspect": [InspectOutputFormat.JSON.value],
            "export": [InspectExportFormat.GRAPH_JSON.value],
        },
        "metadata_fields": [
            "type",
            "title",
            "description",
            "tags",
            "timestamp",
            "scope",
            "source",
            "confidence",
            "promotable",
            "project_id",
        ],
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
        "paths": json_record_list(records_by_kind[kind]),
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
    return {"scope": scope.value, "depth": depth, "roots": json_record_list(roots)}


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
        "links": json_record_list([link_record_json(record) for record in records]),
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
        "headings": json_record_list(markdown_headings(document.body)),
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
    records = [record for record in inspect_note_records(config, scope) if parse_memory_timestamp(record.timestamp) > since_datetime]
    records.sort(key=lambda record: record.timestamp, reverse=True)
    return {"scope": scope.value, "since": since, "results": json_record_list([note_record_json(record) for record in records])}


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
        "nodes": json_record_list(nodes),
        "edges": json_record_list(edges),
    }


def inspect_root_paths(config: ProjectConfig, scope: SearchScope) -> tuple[Path, ...]:
    global_root = config.vault / "global"
    project_root = config.vault / "projects" / config.project_id
    roots = {
        SearchScope.PROJECT: (project_root,),
        SearchScope.GLOBAL: (global_root,),
        SearchScope.BOTH: (global_root, project_root),
    }
    return roots[scope]


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
    counts = Counter(parse_memory_timestamp(record.timestamp).date().isoformat() for record in records)
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
    headings = [line[2:].strip() for line in markdown.splitlines() if line.startswith("# ")]
    assert headings, "markdown document must contain a top-level heading"
    title = headings[0]
    assert title, "heading title must be nonempty"
    return title


def markdown_headings(markdown: str) -> tuple[JsonObject, ...]:
    headings: list[JsonObject] = []
    for line_number, line in enumerate(markdown.splitlines(), start=1):
        stripped = line.lstrip()
        marker_length = len(stripped) - len(stripped.lstrip("#"))
        if marker_length == 0:
            continue
        assert marker_length <= 6, "markdown heading level must be between 1 and 6"
        assert stripped[marker_length : marker_length + 1] == " ", "markdown heading marker must be followed by a space"
        title = stripped[marker_length + 1 :].strip()
        assert title, "markdown heading title must be nonempty"
        headings.append({"level": marker_length, "title": title, "line": line_number})
    return tuple(headings)


def outgoing_link_keys(config: ProjectConfig, path: Path) -> tuple[str, ...]:
    text = path.read_text(encoding="utf-8")
    keys: list[str] = []
    for match in MARKDOWN_LINK_PATTERN.finditer(text):
        target = match.group(1).split("#", 1)[0]
        assert target.endswith(".md"), f"markdown link target must point to a Markdown file: {target}"
        target_path = (path.parent / target).resolve()
        vault = config.vault.resolve()
        assert target_path.is_relative_to(vault), f"markdown link leaves memory vault: {target}"
        keys.append(target_path.relative_to(vault).with_suffix("").as_posix())
    return tuple(keys)


def incoming_link_keys(config: ProjectConfig, target_key: str) -> tuple[str, ...]:
    keys: list[str] = []
    for path in inspect_markdown_paths(config, SearchScope.BOTH):
        source_key = memory_key(config.vault, path)
        if source_key == target_key:
            continue
        if target_key in outgoing_link_keys(config, path):
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
        "title": inspect_title_for_document(document),
        "children": json_record_list(children),
    }


def inspect_title_for_document(document: MemoryDocument) -> str:
    if "title" in document.metadata:
        return metadata_string(document.metadata, "title")
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
                records.append(LinkRecord(related_key, related_path, inspect_title_for_document(related_document), record_depth))
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
        "title": inspect_title_for_document(document),
        "scope": inspect_scope_for_path(config, path),
    }
    if "type" in document.metadata:
        node["type"] = metadata_string(document.metadata, "type")
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
    return json_string_list(value)
