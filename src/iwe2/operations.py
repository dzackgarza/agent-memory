from __future__ import annotations

import json
import subprocess
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import tomli_w
import yaml
from slugify import slugify

from iwe2.models import (
    GLOBAL_SCOPES,
    GlobalNoteMetadata,
    MemoryScope,
    MemoryType,
    MetadataValue,
    ProjectConfig,
    ProjectConfigFile,
    ProjectNoteMetadata,
    PromotedNoteMetadata,
    SearchScope,
)

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
JsonObject = dict[str, JsonValue]
IndexEntry = tuple[str, str, str]
ProjectRecord = dict[str, str]

OKF_VERSION = "0.1"
AGENTS_SECTION_START = "<!-- iwe2:agent-memory:start -->"
AGENTS_SECTION_END = "<!-- iwe2:agent-memory:end -->"
ZK_NOTEBOOK_DB_IGNORE = ".zk/notebook.db"
ROOT_INDEX_ENTRIES: tuple[IndexEntry, ...] = (("Global", "global/index.md", "Global memory shared across projects."),)
GLOBAL_INDEX_DESCRIPTIONS: dict[str, str] = {
    "advice": "Global advice memories.",
    "traps": "Global traps memories.",
    "workflows": "Global workflow memories.",
    "tools": "Global tool memories.",
    "style": "Global style memories.",
    "facts": "Global fact memories.",
    "conventions": "Global convention memories.",
}
PROJECT_INDEX_DESCRIPTIONS: dict[str, str] = {
    "decisions": "Project decision memories.",
    "traps": "Project trap memories.",
    "workflows": "Project workflow memories.",
    "sessions": "Project session memories.",
    "facts": "Project fact memories.",
    "advice": "Project advice memories.",
    "conventions": "Project convention memories.",
}

MEMORY_TYPE_DIRECTORIES: dict[MemoryType, str] = {
    MemoryType.DECISION: "decisions",
    MemoryType.TRAP: "traps",
    MemoryType.WORKFLOW: "workflows",
    MemoryType.FACT: "facts",
    MemoryType.ADVICE: "advice",
    MemoryType.CONVENTION: "conventions",
}

GLOBAL_INDEX_DIRECTORIES: tuple[str, ...] = (
    "advice",
    "traps",
    "workflows",
    "tools",
    "style",
    "facts",
    "conventions",
)

VAULT_DIRECTORIES: tuple[Path, ...] = (
    Path("global/advice"),
    Path("global/traps"),
    Path("global/workflows"),
    Path("global/tools"),
    Path("global/style"),
    Path("global/facts"),
    Path("global/conventions"),
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
    "workflows",
    "sessions",
    "facts",
    "advice",
    "conventions",
)


@dataclass(frozen=True)
class MemoryDocument:
    metadata: dict[str, MetadataValue]
    body: str

    def metadata_str(self, key: str) -> str:
        value = self.metadata[key]
        assert isinstance(value, str), f"metadata field must be a string: {key}"
        return value


def init_vault(vault: Path) -> JsonObject:
    vault.mkdir(parents=True)
    run_checked(["git", "init"], cwd=vault)
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
        parent_index_body(
            "Global Memory",
            directory_index_entries(GLOBAL_INDEX_DIRECTORIES, GLOBAL_INDEX_DESCRIPTIONS),
        ),
    )
    write_section_indexes(vault / "global", GLOBAL_INDEX_DIRECTORIES)
    write_new_file(vault / "_meta" / "projects.toml", tomli_w.dumps({"projects": []}))
    index_zk_notebook(vault)
    stage_vault_changes(vault)
    return {"vault": str(vault)}


def init_project(vault: Path, cwd: Path) -> JsonObject:
    assert (vault / ".iwe" / "config.toml").is_file(), "vault must be initialized with IWE"
    git_root = git_root_for(cwd)
    remote = git_remote(git_root)
    project_id = project_id_from_remote(remote)
    project_dir = vault / "projects" / project_id
    project_dir.mkdir(parents=True)
    write_new_file(
        project_dir / "index.md",
        parent_index_body(
            project_id,
            directory_index_entries(
                PROJECT_DIRECTORIES,
                PROJECT_INDEX_DESCRIPTIONS,
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
        global_scopes=GLOBAL_SCOPES,
    )
    write_new_file(git_root / ".agent-memory.toml", tomli_w.dumps(config.to_toml_payload()))
    write_agents_pointer(git_root, vault, project_id)
    append_project_record(
        vault / "_meta" / "projects.toml",
        {"project_id": project_id, "root": str(git_root), "remote": remote},
    )
    index_zk_notebook(vault)
    stage_vault_changes(vault)
    return {
        "project_id": project_id,
        "vault": str(vault),
        "project_root": str(git_root),
    }


def create_note(
    scope: MemoryScope,
    memory_type: MemoryType,
    title: str,
    content: str,
    cwd: Path,
) -> JsonObject:
    config = load_project_config(cwd)
    slug = slugify(title)
    assert slug, "title must produce a nonempty slug"
    directory = memory_directory(config, scope, memory_type)
    path = directory / f"{slug}.md"
    key = memory_key(config.vault, path)
    description = okf_description(content)
    metadata = note_metadata(config, scope, memory_type, title, description)
    body = f"# {title}\n\n{content}\n"
    write_new_memory(path, metadata, body)
    append_index_link(directory / "index.md", title, path.name, description)
    index_zk_notebook(config.vault)
    stage_vault_changes(config.vault)
    return {"key": key, "path": str(path)}


def search_notes(scope: SearchScope, query: str, cwd: Path) -> str:
    config = load_project_config(cwd)
    graph_outputs = [
        run_checked(
            ["iwe", "find", query, "--included-by", anchor, "--format", "keys"],
            cwd=config.vault,
        ).stdout
        for anchor in search_anchors(config, scope)
    ]
    roots = search_roots(config, scope)
    body_output = run_ripgrep_search(
        [
            "rg",
            "--line-number",
            "--with-filename",
            "--fixed-strings",
            query,
            *[str(root) for root in roots],
        ],
        cwd=config.vault,
    )
    return "".join([*graph_outputs, body_output])


def search_context(
    scope: SearchScope,
    query: str,
    max_results: int,
    max_tokens: int,
    cwd: Path,
) -> str:
    assert max_results > 0, f"probe max results must be positive: {max_results}"
    assert max_tokens > 0, f"probe max tokens must be positive: {max_tokens}"
    config = load_project_config(cwd)
    roots = search_roots(config, scope)
    per_root_tokens = max_tokens // len(roots)
    assert per_root_tokens > 0, "probe max tokens must cover every selected scope root"
    payloads = [
        probe_search_root(
            root=root,
            query=query,
            max_results=max_results,
            max_tokens=per_root_tokens,
            cwd=config.vault,
        )
        for root in roots
    ]
    return json.dumps(
        merge_probe_payloads(
            payloads,
            max_results=max_results,
            max_tokens=max_tokens,
        ),
        sort_keys=True,
    )


def search_index(scope: SearchScope, query: str, limit: int, cwd: Path) -> str:
    assert limit > 0, f"zk indexed search limit must be positive: {limit}"
    config = load_project_config(cwd)
    roots = search_roots(config, scope)
    results: list[JsonValue] = []
    for root in roots:
        results.extend(zk_search_root(vault=config.vault, root=root, query=query, limit=limit))
    return json.dumps({"results": results[:limit]}, sort_keys=True)


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


def retrieve_note(key: str, cwd: Path) -> str:
    config = load_project_config(cwd)
    result = run_checked(["iwe", "retrieve", "-k", key], cwd=config.vault)
    return result.stdout


def squash_note(key: str, depth: int, cwd: Path) -> str:
    assert depth > 0, f"squash depth must be positive: {depth}"
    config = load_project_config(cwd)
    result = run_checked(["iwe", "squash", key, "--depth", str(depth)], cwd=config.vault)
    return result.stdout


def promote_note(key: str, destination: str, cwd: Path) -> JsonObject:
    config = load_project_config(cwd)
    assert destination.startswith("global/"), "promotion destination must be global"
    source_path = config.vault / f"{key}.md"
    assert source_path.is_file(), "memory to promote must exist"
    destination_key = f"{destination}/{source_path.stem}"
    destination_path = config.vault / f"{destination_key}.md"
    assert destination_path.parent.is_dir(), "promotion destination directory must exist"

    source_document = read_memory(source_path)
    memory_type = MemoryType(source_document.metadata_str("type"))
    title = source_document.metadata_str("title")
    description = source_document.metadata_str("description")
    run_checked(["iwe", "rename", key, destination_key], cwd=config.vault)

    promoted_document = read_memory(destination_path)
    promoted_metadata = PromotedNoteMetadata(
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
    write_memory(destination_path, promoted_metadata, promoted_document.body)
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
    replace_index_link(source_path.parent / "index.md", title, source_path.name, pointer_description)
    index_zk_notebook(config.vault)
    stage_vault_changes(config.vault)
    return {"key": destination_key, "path": str(destination_path)}


def doctor(cwd: Path) -> JsonObject:
    config = load_project_config(cwd)
    git_root = git_root_for(cwd)
    run_checked(["iwe", "--help"], cwd=config.vault)
    run_checked(["rg", "--version"], cwd=config.vault)
    run_checked(["npx", "--version"], cwd=config.vault)
    run_checked(["npx", "-y", "@probelabs/probe@latest", "search", "--help"], cwd=config.vault)
    run_checked(["zk", "--help"], cwd=config.vault)
    assert (config.vault / ".zk" / "config.toml").is_file(), "vault must be initialized with zk"
    assert (config.vault / ".zk" / "templates" / "default.md").is_file(), "zk default template must exist"
    return {
        "vault": str(config.vault),
        "project_id": config.project_id,
        "project_root": str(git_root),
        "tools": ["git", "iwe", "rg", "npx", "@probelabs/probe", "zk"],
    }


def run_checked(args: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, text=True, capture_output=True)


def stage_vault_changes(vault: Path) -> None:
    run_checked(["git", "add", "--all", "."], cwd=vault)


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
        "iwe2 note --scope project --type decision --title <title> --content <content>\n"
        "iwe2 note --scope project --type trap --title <title> --content <content>\n"
        "iwe2 note --scope project --type workflow --title <title> --content <content>\n"
        "```\n\n"
        "Promote reusable lessons with:\n\n"
        "```bash\n"
        "iwe2 promote <note-key> --to global/advice\n"
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
        write_new_file(root / section / "index.md", leaf_index_body(section_title(section)))


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
    # IWE follows paragraph links, while OKF index listings are bullets.
    return f"* [{title}]({target}) - {description}\n\n[{title}]({target})"


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


def replace_index_link(index_path: Path, title: str, target: str, description: str) -> None:
    assert index_path.is_file(), "index must exist before replacing a link"
    # IWE rewrites the OKF bullet marker to "-" when it renames linked notes.
    link_prefixes = (f"* [{title}](", f"- [{title}](")
    lines = index_path.read_text(encoding="utf-8").splitlines()
    matching_indexes = [index for index, line in enumerate(lines) if any(line.startswith(prefix) for prefix in link_prefixes)]
    assert len(matching_indexes) == 1, "index must contain exactly one link for the title"
    entry_start = matching_indexes[0]
    assert lines[entry_start + 1] == "", "index entry must separate OKF and IWE links"
    assert lines[entry_start + 2].startswith(f"[{title}]("), "index entry must include an IWE graph link"
    lines[entry_start : entry_start + 3] = okf_index_entry(title, target, description).splitlines()
    index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


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
    if stripped.startswith("git@github.com:"):
        repository = stripped.removeprefix("git@github.com:")
    elif stripped.startswith("https://github.com/"):
        repository = stripped.removeprefix("https://github.com/")
    else:
        raise ValueError(f"unsupported remote URL: {remote}")
    parts = repository.split("/")
    assert len(parts) == 2, "GitHub remote must have owner and repository"
    owner, repo = parts
    assert owner and repo, "GitHub remote owner and repository must be nonempty"
    return f"github.com__{owner}__{repo}"


def load_project_config(cwd: Path) -> ProjectConfig:
    git_root = git_root_for(cwd)
    config_path = git_root / ".agent-memory.toml"
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
    if scope is MemoryScope.PROJECT:
        return config.vault / "projects" / config.project_id / directory_name
    if scope is MemoryScope.GLOBAL:
        return config.vault / "global" / directory_name
    raise AssertionError(f"unsupported note scope: {scope}")


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
    if scope is MemoryScope.GLOBAL:
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
    raise AssertionError(f"unsupported note scope: {scope}")


def run_ripgrep_search(args: Sequence[str], cwd: Path) -> str:
    result = subprocess.run(args, cwd=cwd, check=False, text=True, capture_output=True)
    if result.returncode == 0:
        return result.stdout
    if result.returncode == 1:
        assert result.stdout == ""
        assert result.stderr == ""
        return ""
    raise subprocess.CalledProcessError(
        result.returncode,
        args,
        output=result.stdout,
        stderr=result.stderr,
    )


def search_anchors(config: ProjectConfig, scope: SearchScope) -> tuple[str, ...]:
    project_anchor = f"projects/{config.project_id}/index:0"
    global_anchor = "global/index:0"
    if scope is SearchScope.PROJECT:
        return (project_anchor,)
    if scope is SearchScope.GLOBAL:
        return (global_anchor,)
    if scope is SearchScope.BOTH:
        return (project_anchor, global_anchor)
    raise AssertionError(f"unsupported search scope: {scope}")


def search_roots(config: ProjectConfig, scope: SearchScope) -> tuple[Path, ...]:
    project_root = config.vault / "projects" / config.project_id
    global_root = config.vault / "global"
    if scope is SearchScope.PROJECT:
        return (project_root,)
    if scope is SearchScope.GLOBAL:
        return (global_root,)
    if scope is SearchScope.BOTH:
        return (project_root, global_root)
    raise AssertionError(f"unsupported search scope: {scope}")


def memory_key(vault: Path, path: Path) -> str:
    return path.relative_to(vault).with_suffix("").as_posix()


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
