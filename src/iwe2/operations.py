from __future__ import annotations

import subprocess
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
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

JsonValue = str | list[str]
JsonObject = dict[str, JsonValue]
ProjectRecord = dict[str, str]

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


def init_vault(vault: Path) -> JsonObject:
    vault.mkdir(parents=True)
    run_checked(["iwe", "init"], cwd=vault)
    for relative_dir in VAULT_DIRECTORIES:
        (vault / relative_dir).mkdir(parents=True)
    write_new_file(vault / "index.md", parent_index_body("Agent Memory Vault", ("global",)))
    write_new_file(vault / "global" / "index.md", parent_index_body("Global Memory", GLOBAL_INDEX_DIRECTORIES))
    write_section_indexes(vault / "global", GLOBAL_INDEX_DIRECTORIES)
    write_new_file(vault / "_meta" / "projects.toml", tomli_w.dumps({"projects": []}))
    return {"vault": str(vault)}


def init_project(vault: Path, cwd: Path) -> JsonObject:
    assert (vault / ".iwe" / "config.toml").is_file(), "vault must be initialized with IWE"
    git_root = git_root_for(cwd)
    remote = git_remote(git_root)
    project_id = project_id_from_remote(remote)
    project_dir = vault / "projects" / project_id
    project_dir.mkdir(parents=True)
    write_new_file(project_dir / "index.md", parent_index_body(project_id, PROJECT_DIRECTORIES))
    for directory in PROJECT_DIRECTORIES:
        (project_dir / directory).mkdir()
    write_section_indexes(project_dir, PROJECT_DIRECTORIES)
    append_index_link(vault / "index.md", project_id, f"projects/{project_id}/index.md")

    config = ProjectConfig(
        vault=vault,
        project_id=project_id,
        project_root_strategy="git-root",
        global_scopes=GLOBAL_SCOPES,
    )
    write_new_file(git_root / ".agent-memory.toml", tomli_w.dumps(config.to_toml_payload()))
    append_project_record(
        vault / "_meta" / "projects.toml",
        {"project_id": project_id, "root": str(git_root), "remote": remote},
    )
    return {"project_id": project_id, "vault": str(vault), "project_root": str(git_root)}


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
    metadata = note_metadata(config, scope, memory_type)
    body = f"# {title}\n\n{content}\n"
    write_new_memory(path, metadata, body)
    return {"key": key, "path": str(path)}


def search_notes(scope: SearchScope, query: str, cwd: Path) -> str:
    config = load_project_config(cwd)
    roots = search_roots(config, scope)
    result = run_checked(
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
    return result.stdout


def retrieve_note(key: str, cwd: Path) -> str:
    config = load_project_config(cwd)
    result = run_checked(["iwe", "retrieve", "-k", key], cwd=config.vault)
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
    memory_type = MemoryType(str(source_document.metadata["type"]))
    run_checked(["iwe", "rename", key, destination_key], cwd=config.vault)

    promoted_document = read_memory(destination_path)
    promoted_metadata = PromotedNoteMetadata(
        type=memory_type,
        scope=MemoryScope.GLOBAL,
        status="active",
        source="agent",
        confidence="high",
        promotable=False,
        origin_project_id=config.project_id,
    ).to_yaml_payload()
    write_memory(destination_path, promoted_metadata, promoted_document.body)

    pointer_metadata = ProjectNoteMetadata(
        type=memory_type,
        scope=MemoryScope.PROJECT,
        status="active",
        source="agent",
        confidence="high",
        promotable=False,
        project_id=config.project_id,
    ).to_yaml_payload()
    pointer_body = f"# {source_path.stem}\n\nPromoted to [[{destination_key}]].\n"
    if source_path.parent.exists():
        assert source_path.parent.is_dir(), "project memory parent must be a directory"
    else:
        source_path.parent.mkdir(parents=True)
    write_new_memory(source_path, pointer_metadata, pointer_body)
    return {"key": destination_key, "path": str(destination_path)}


def doctor(cwd: Path) -> JsonObject:
    config = load_project_config(cwd)
    git_root = git_root_for(cwd)
    run_checked(["iwe", "--help"], cwd=config.vault)
    run_checked(["rg", "--version"], cwd=config.vault)
    return {
        "vault": str(config.vault),
        "project_id": config.project_id,
        "project_root": str(git_root),
        "tools": ["git", "iwe", "rg"],
    }


def run_checked(args: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, text=True, capture_output=True)


def write_new_file(path: Path, content: str) -> None:
    assert not path.exists(), f"refusing to overwrite {path}"
    path.write_text(content, encoding="utf-8")


def write_section_indexes(root: Path, sections: Sequence[str]) -> None:
    for section in sections:
        write_new_file(root / section / "index.md", leaf_index_body(section_title(section)))


def parent_index_body(title: str, children: Sequence[str]) -> str:
    assert children, "parent index must include at least one child"
    links = "\n\n".join(f"[{section_title(child)}]({child}/index.md)" for child in children)
    return f"# {title}\n\n{links}\n"


def leaf_index_body(title: str) -> str:
    return f"# {title}\n"


def section_title(section: str) -> str:
    return section.replace("-", " ").title()


def append_index_link(index_path: Path, title: str, target: str) -> None:
    assert index_path.is_file(), "parent index must exist before linking project"
    with index_path.open("a", encoding="utf-8") as index_file:
        index_file.write(f"[{title}]({target})\n")


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
) -> dict[str, MetadataValue]:
    if scope is MemoryScope.PROJECT:
        return ProjectNoteMetadata(
            type=memory_type,
            scope=MemoryScope.PROJECT,
            status="active",
            source="agent",
            confidence="high",
            promotable=False,
            project_id=config.project_id,
        ).to_yaml_payload()
    if scope is MemoryScope.GLOBAL:
        return GlobalNoteMetadata(
            type=memory_type,
            scope=MemoryScope.GLOBAL,
            status="active",
            source="agent",
            confidence="high",
            promotable=False,
        ).to_yaml_payload()
    raise AssertionError(f"unsupported note scope: {scope}")


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
        assert isinstance(value, str | bool), "frontmatter values must be strings or booleans"
        metadata[key] = value
    body = "".join(lines[closing_index + 1 :])
    return MemoryDocument(metadata=metadata, body=body)
