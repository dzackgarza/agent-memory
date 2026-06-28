from __future__ import annotations

import json
import os
import re
import runpy
import subprocess
import sys
import tempfile
import tomllib
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

import pytest
import yaml

from agent_memory.cli import app as agent_memory_app
from agent_memory.cli import main as cli_main
from agent_memory.models import MemoryType
from agent_memory.operations import (
    OKF_VERSION,
    DependencyCheck,
    DependencyError,
    ProjectNotInitializedError,
    basic_doctor,
    check_dependency,
    merge_probe_payloads,
    update_memory,
)
from agent_memory.operations import load_project_config as operations_load_project_config

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def just_value(name: str) -> str:
    return subprocess.run(
        ["just", "--justfile", str(PROJECT_ROOT / "justfile"), "--evaluate", name],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()


ZK_VERSION = just_value("ZK_VERSION")
ZK_ASSET = just_value("ZK_ASSET")
ZK_BIN_DIR = Path(tempfile.mkdtemp(prefix="agent-memory-zk-"))


def ensure_zk_binary() -> Path:
    # Fetch the real zk binary the integration tests put on PATH. This runs lazily on
    # first use (from agent_memory_env), not at module import: keeping the gh release download
    # out of collection means a zk-org/zk release-asset change or a GitHub outage breaks
    # the runs that actually need zk, fail-loud via check=True, instead of breaking
    # collection of the whole module. Idempotent: a present binary is reused.
    zk_path = ZK_BIN_DIR / "zk"
    if zk_path.is_file():
        return zk_path
    subprocess.run(
        [
            "gh",
            "release",
            "download",
            ZK_VERSION,
            "--repo",
            "zk-org/zk",
            "--pattern",
            ZK_ASSET,
            "--dir",
            str(ZK_BIN_DIR),
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    subprocess.run(
        ["tar", "-xzf", str(ZK_BIN_DIR / ZK_ASSET), "-C", str(ZK_BIN_DIR)],
        check=True,
        text=True,
        capture_output=True,
    )
    assert zk_path.is_file()
    return zk_path


type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]
type JsonArray = list[JsonValue]


@dataclass(frozen=True)
class CliWorkspace:
    repo: Path
    vault: Path
    project_id: str


@dataclass(frozen=True)
class GitRepo:
    path: Path
    project_id: str


def run_agent_memory_process(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    command = ["agent-memory", *args]
    command_env = agent_memory_env()
    original_cwd = Path.cwd()
    original_argv = sys.argv.copy()
    original_env = os.environ.copy()
    stdout = StringIO()
    stderr = StringIO()
    try:
        os.chdir(cwd)
        os.environ.clear()
        os.environ.update(command_env)
        sys.argv = command
        with redirect_stdout(stdout), redirect_stderr(stderr):
            basic_doctor(cwd)
            returncode = agent_memory_app(list(args), exit_on_error=False, result_action="return_int_as_exit_code_else_zero")
    finally:
        os.chdir(original_cwd)
        sys.argv = original_argv
        os.environ.clear()
        os.environ.update(original_env)
    assert isinstance(returncode, int), "cyclopts return_int_as_exit_code_else_zero must yield an int"
    return subprocess.CompletedProcess(command, returncode, stdout.getvalue(), stderr.getvalue())


def run_agent_memory(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    result = run_agent_memory_process(cwd, *args)
    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, result.args, result.stdout, result.stderr)
    return result


def run_agent_memory_subprocess(cwd: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    command_env = env if env is not None else agent_memory_env()
    command_env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
    return subprocess.run(
        [sys.executable, "-m", "agent_memory", *args],
        cwd=cwd,
        env=command_env,
        text=True,
        capture_output=True,
    )


def run_agent_memory_module(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    result = run_agent_memory_subprocess(cwd, *args)
    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, result.args, result.stdout, result.stderr)
    return result


def agent_memory_env() -> dict[str, str]:
    ensure_zk_binary()
    env = os.environ.copy()
    env["PATH"] = f"{ZK_BIN_DIR}:{env['PATH']}"
    return env


def inspect_tree_keys(node: JsonObject) -> set[str]:
    key = json_string(node["key"])
    children = json_array(node["children"])
    keys = {key}
    for child in children:
        keys.update(inspect_tree_keys(json_object(child)))
    return keys


def init_git_repo(repo: Path) -> str:
    subprocess.run(["git", "init"], cwd=repo, check=True, text=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "remote",
            "add",
            "origin",
            "git@github.com:dzackgarza/example-memory.git",
        ],
        cwd=repo,
        check=True,
        text=True,
        capture_output=True,
    )
    return "github.com__dzackgarza__example-memory"


def git_status_lines(repo: Path) -> set[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), "status", "--short"],
        check=True,
        text=True,
        capture_output=True,
    )
    return set(result.stdout.splitlines())


def git_commit_subjects(repo: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), "log", "--format=%s"],
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.splitlines()


def git_tracked_files(repo: Path) -> set[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), "ls-files"],
        check=True,
        text=True,
        capture_output=True,
    )
    return set(result.stdout.splitlines())


def parse_json_stdout(result: subprocess.CompletedProcess[str]) -> JsonObject:
    decoded: JsonValue = json.loads(result.stdout)
    return json_object(decoded)


def json_object(value: JsonValue) -> JsonObject:
    assert isinstance(value, dict)
    return value


def json_array(value: JsonValue) -> JsonArray:
    assert isinstance(value, list)
    return value


def json_string(value: JsonValue) -> str:
    assert isinstance(value, str)
    return value


def json_records(payload: JsonObject, key: str) -> list[JsonObject]:
    return [json_object(record) for record in json_array(payload[key])]


def records_by_key(payload: JsonObject, key: str) -> dict[str, JsonObject]:
    return {json_string(record["key"]): record for record in json_records(payload, key)}


def probe_result_files(result: JsonObject) -> set[Path]:
    records = json_array(result["results"])
    files: set[Path] = set()
    for record in records:
        record_object = json_object(record)
        files.add(Path(json_string(record_object["file"])).resolve())
    return files


def result_keys(result: JsonObject) -> set[str]:
    records = json_array(result["results"])
    keys: set[str] = set()
    for record in records:
        record_object = json_object(record)
        keys.add(json_string(record_object["key"]))
    return keys


def init_git_repo_without_remote(repo: Path) -> None:
    subprocess.run(["git", "init"], cwd=repo, check=True, text=True, capture_output=True)


def initialized_git_repo(tmp_path: Path) -> GitRepo:
    repo = tmp_path / "repo"
    repo.mkdir()
    return GitRepo(path=repo, project_id=init_git_repo(repo))


def initialized_project_workspace(tmp_path: Path, git_repo: GitRepo) -> CliWorkspace:
    vault = tmp_path / "vault"
    run_agent_memory(tmp_path, "maintain", "init-global", "--vault", str(vault))
    run_agent_memory(git_repo.path, "init", "project", "--vault", str(vault))
    return CliWorkspace(repo=git_repo.path, vault=vault, project_id=git_repo.project_id)


def initialized_workspace(tmp_path: Path) -> CliWorkspace:
    return initialized_project_workspace(tmp_path, initialized_git_repo(tmp_path))


def initialized_workspace_with_agents(tmp_path: Path, agents_text: str) -> CliWorkspace:
    git_repo = initialized_git_repo(tmp_path)
    (git_repo.path / "AGENTS.md").write_text(agents_text, encoding="utf-8")
    return initialized_project_workspace(tmp_path, git_repo)


def project_agent_state_path(workspace: CliWorkspace) -> Path:
    return workspace.vault / "projects" / workspace.project_id


def add_cli_memory(
    workspace: CliWorkspace,
    *,
    scope: str,
    memory_type: str,
    title: str,
    content: str,
) -> JsonObject:
    return parse_json_stdout(
        run_agent_memory(
            workspace.repo,
            "add",
            "--scope",
            scope,
            "--type",
            memory_type,
            "--title",
            title,
            "--content",
            content,
        )
    )


def project_memory_key(workspace: CliWorkspace, memory_type_directory: str, slug: str) -> str:
    return f"projects/{workspace.project_id}/{memory_type_directory}/{slug}"


def search_content(workspace: CliWorkspace, *, scope: str, mode: str, query: str) -> JsonObject:
    return parse_json_stdout(
        run_agent_memory(
            workspace.repo,
            "search",
            "content",
            "--scope",
            scope,
            "--mode",
            mode,
            query,
        )
    )


def inspect_json(workspace: CliWorkspace, *args: str) -> JsonObject:
    return parse_json_stdout(run_agent_memory(workspace.repo, "inspect", *args))


def frontmatter(markdown: Path) -> dict[str, object]:
    lines = markdown.read_text().splitlines()
    assert lines[0] == "---"
    closing = lines.index("---", 1)
    parsed = yaml.safe_load("\n".join(lines[1:closing]))
    assert isinstance(parsed, dict)
    return parsed


def assert_okf_timestamp(value: object) -> None:
    assert isinstance(value, str)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed.tzinfo == UTC


def assert_okf_concept_metadata(
    metadata: dict[str, object],
    *,
    memory_type: str,
    title: str,
    description: str,
    tags: list[str],
) -> None:
    assert metadata["type"] == memory_type
    assert metadata["title"] == title
    assert metadata["description"] == description
    assert metadata["tags"] == tags
    assert "status" not in metadata
    assert "authority" not in metadata
    assert "expires" not in metadata
    assert "safe_to_act" not in metadata
    assert "requires_confirmation" not in metadata
    assert_okf_timestamp(metadata["timestamp"])


def test_maintain_init_global_creates_iwe_backed_layout(tmp_path: Path) -> None:
    vault = tmp_path / "vault"

    result = run_agent_memory(tmp_path, "maintain", "init-global", "--vault", str(vault))
    payload = parse_json_stdout(result)
    git_probe = subprocess.run(
        ["git", "-C", str(vault), "rev-parse", "--is-inside-work-tree"],
        check=True,
        text=True,
        capture_output=True,
    )
    status_lines = git_status_lines(vault)
    tracked_files = git_tracked_files(vault)

    assert Path(str(payload["vault"])) == vault
    assert git_probe.stdout.strip() == "true"
    assert git_commit_subjects(vault) == ["Initialize agent-memory vault"]
    assert status_lines == set()
    assert ".gitignore" in tracked_files
    assert ".zk/config.toml" in tracked_files
    assert ".zk/templates/default.md" in tracked_files
    assert ".zk/notebook.db" not in tracked_files
    assert "index.md" in tracked_files
    assert (vault / ".agents" / "memories" / "config.toml").is_file()
    assert (vault / "index.md").is_file()
    assert (vault / "global" / "index.md").is_file()
    assert (vault / "_meta" / "projects.toml").is_file()
    assert frontmatter(vault / "index.md") == {"okf_version": OKF_VERSION}
    assert "* [Global](global/index.md) - Global memory shared across projects." in (vault / "index.md").read_text()
    global_index = (vault / "global" / "index.md").read_text()
    assert frontmatter(vault / "global" / "index.md") == {"okf_version": OKF_VERSION}
    assert "* [Decisions](decisions/index.md) - Global decision memories." in global_index
    assert "* [Traps](traps/index.md) - Global trap memories." in global_index
    assert "* [Advice](advice/index.md) - Global advice memories." in global_index
    assert "* [Context](context/index.md) - Global context memories." in global_index
    assert "* [References](references/index.md) - Global reference memories." in global_index


def test_module_entrypoint_initializes_iwe_backed_vault(tmp_path: Path) -> None:
    vault = tmp_path / "module-vault"

    result = run_agent_memory_module(tmp_path, "maintain", "init-global", "--vault", str(vault))
    payload = parse_json_stdout(result)
    global_index = (vault / "global" / "index.md").read_text()

    assert Path(str(payload["vault"])) == vault
    assert (vault / ".agents" / "memories" / "config.toml").is_file()
    assert frontmatter(vault / "global" / "index.md") == {"okf_version": OKF_VERSION}
    assert "* [Decisions](decisions/index.md) - Global decision memories." in global_index
    assert "* [References](references/index.md) - Global reference memories." in global_index


def test_project_initialization_writes_config_indexes_and_agent_pointer(tmp_path: Path) -> None:
    workspace = initialized_workspace(tmp_path)

    assert not (workspace.repo / ".agent-memory.toml").exists()
    resolved = operations_load_project_config(workspace.repo)
    assert resolved.vault == workspace.vault
    assert resolved.project_id == workspace.project_id
    agents_pointer = (workspace.repo / "AGENTS.md").read_text()
    assert f"This repository uses the central agent memory vault at `{workspace.vault}`." in agents_pointer
    assert f"Project memory key: `projects/{workspace.project_id}/index`." in agents_pointer
    assert 'agent-memory search --scope both "<task or subsystem>"' in agents_pointer
    pointer_add_types = re.findall(r"agent-memory add --scope project --type (\S+) ", agents_pointer)
    assert pointer_add_types, "agent pointer must demonstrate agent-memory add invocations"
    assert [MemoryType(token) for token in pointer_add_types] == list(MemoryType)

    project_index_path = workspace.vault / "projects" / workspace.project_id / "index.md"
    project_index = project_index_path.read_text()
    assert frontmatter(project_index_path) == {"okf_version": OKF_VERSION}
    assert "* [Decisions](decisions/index.md) - Project decision memories." in project_index
    assert "* [Traps](traps/index.md) - Project trap memories." in project_index
    assert "* [Advice](advice/index.md) - Project advice memories." in project_index
    assert "* [Context](context/index.md) - Project context memories." in project_index
    assert "* [References](references/index.md) - Project reference memories." in project_index
    assert f"* [{workspace.project_id}](projects/{workspace.project_id}/index.md) - Project memory bundle." in (workspace.vault / "index.md").read_text()


def test_project_initialization_appends_https_remote_project_record(tmp_path: Path) -> None:
    ssh_repo = initialized_git_repo(tmp_path)
    https_repo = tmp_path / "https-repo"
    https_repo.mkdir()
    subprocess.run(["git", "init"], cwd=https_repo, check=True, text=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "remote",
            "add",
            "origin",
            "https://github.com/dzackgarza/https-memory.git",
        ],
        cwd=https_repo,
        check=True,
        text=True,
        capture_output=True,
    )
    vault = tmp_path / "vault"
    run_agent_memory(tmp_path, "maintain", "init-global", "--vault", str(vault))

    run_agent_memory(ssh_repo.path, "init", "project", "--vault", str(vault))
    run_agent_memory(https_repo, "init", "project", "--vault", str(vault))

    ssh_remote = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=ssh_repo.path,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    https_remote = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=https_repo,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()

    assert operations_load_project_config(ssh_repo.path).project_id == ssh_repo.project_id
    assert operations_load_project_config(https_repo).project_id == "github.com__dzackgarza__https-memory"
    project_records = tomllib.loads((vault / "_meta" / "projects.toml").read_text(encoding="utf-8"))["projects"]
    assert project_records == [
        {
            "project_id": ssh_repo.project_id,
            "root": str(ssh_repo.path),
            "remote": ssh_remote,
        },
        {
            "project_id": "github.com__dzackgarza__https-memory",
            "root": str(https_repo),
            "remote": https_remote,
        },
    ]


def test_init_project_with_explicit_project_id_preserves_no_origin_project_plan_scope(tmp_path: Path) -> None:
    repo = tmp_path / "vendor"
    repo.mkdir()
    init_git_repo_without_remote(repo)
    vault = tmp_path / "vault"
    project_id = "vendor.local__agent-memory__vendored-tool"
    run_agent_memory(tmp_path, "maintain", "init-global", "--vault", str(vault))

    initialized = parse_json_stdout(run_agent_memory(repo, "init", "project", "--vault", str(vault), "--project-id", project_id))
    run_agent_memory(
        repo,
        "plan",
        "add",
        "--type",
        "feature",
        "--id",
        "FEATURE-VENDOR",
        "--set",
        "title=Vendor",
        "--set",
        "status=in-progress",
        "--set",
        "description=vendor plan scope",
    )

    plan_path = vault / "projects" / project_id / "plans" / "features" / "FEATURE-VENDOR" / "FEATURE-VENDOR.md"
    assert initialized["project_id"] == project_id
    assert not (repo / ".agent-memory.toml").exists()
    assert plan_path.is_file()
    assert not (vault / "global" / "plans" / "FEATURE-VENDOR.md").exists()


def test_init_project_without_remote_or_project_id_fails_before_global_write(tmp_path: Path) -> None:
    repo = tmp_path / "vendor"
    repo.mkdir()
    init_git_repo_without_remote(repo)
    vault = tmp_path / "vault"
    run_agent_memory(tmp_path, "maintain", "init-global", "--vault", str(vault))

    result = run_agent_memory_subprocess(repo, "init", "project", "--vault", str(vault))

    assert result.returncode != 0
    assert not (repo / ".agent-memory.toml").exists()
    assert tomllib.loads((vault / "_meta" / "projects.toml").read_text(encoding="utf-8"))["projects"] == []


def test_init_global_normalizes_literal_tilde_vault_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    cwd = tmp_path / "cwd"
    cwd.mkdir()

    initialized = parse_json_stdout(run_agent_memory(cwd, "maintain", "init-global", "--vault", "~/.agent-memory-vault"))

    assert initialized["vault"] == str(home / ".agent-memory-vault")
    assert (home / ".agent-memory-vault" / ".agents" / "memories" / "config.toml").is_file()
    assert not (cwd / "~").exists()


def test_project_memory_crud_and_search_cross_real_scopes(tmp_path: Path) -> None:
    workspace = initialized_workspace(tmp_path)
    project_note = add_cli_memory(
        workspace,
        scope="project",
        memory_type="decision",
        title="Project Alpha",
        content="project-signal-7dcbd96d belongs only to this repository",
    )
    global_note = add_cli_memory(
        workspace,
        scope="global",
        memory_type="advice",
        title="Global Beta",
        content="global-signal-cde4b9f6 belongs to shared agent practice",
    )

    project_path = Path(str(project_note["path"]))
    global_path = Path(str(global_note["path"]))
    project_key = project_memory_key(workspace, "decisions", "project-alpha")
    global_key = "global/advice/global-beta"
    assert project_note["key"] == project_key
    assert global_note["key"] == global_key
    assert project_path == workspace.vault / "projects" / workspace.project_id / "decisions" / "project-alpha.md"
    assert global_path == workspace.vault / "global" / "advice" / "global-beta.md"
    assert git_status_lines(workspace.vault) == set()
    assert git_commit_subjects(workspace.vault)[:4] == [
        "Record global advice memory: Global Beta",
        "Record project decision memory: Project Alpha",
        f"Register project {workspace.project_id}",
        "Initialize agent-memory vault",
    ]

    assert_okf_concept_metadata(
        frontmatter(project_path),
        memory_type="decision",
        title="Project Alpha",
        description="project-signal-7dcbd96d belongs only to this repository",
        tags=["project", "decision"],
    )
    assert frontmatter(project_path)["scope"] == "project"
    assert frontmatter(project_path)["project_id"] == workspace.project_id
    assert_okf_concept_metadata(
        frontmatter(global_path),
        memory_type="advice",
        title="Global Beta",
        description="global-signal-cde4b9f6 belongs to shared agent practice",
        tags=["global", "advice"],
    )
    assert frontmatter(global_path)["scope"] == "global"
    assert "* [Project Alpha](project-alpha.md) - project-signal-7dcbd96d belongs only to this repository" in (project_path.parent / "index.md").read_text()
    assert "* [Global Beta](global-beta.md) - global-signal-cde4b9f6 belongs to shared agent practice" in (global_path.parent / "index.md").read_text()

    project_search = parse_json_stdout(run_agent_memory(workspace.repo, "search", "--scope", "project", "signal"))
    assert project_key in result_keys(project_search)
    assert global_key not in result_keys(project_search)
    global_search = parse_json_stdout(run_agent_memory(workspace.repo, "search", "--scope", "global", "signal"))
    assert global_key in result_keys(global_search)
    assert project_key not in result_keys(global_search)
    combined_search = parse_json_stdout(run_agent_memory(workspace.repo, "search", "--scope", "both", "signal"))
    combined_keys = result_keys(combined_search)
    assert project_key in combined_keys
    assert global_key in combined_keys

    retrieved = run_agent_memory(workspace.repo, "retrieve", str(project_note["key"]))
    assert "project-signal-7dcbd96d belongs only to this repository" in retrieved.stdout
    updated = parse_json_stdout(
        run_agent_memory(
            workspace.repo,
            "update",
            str(project_note["key"]),
            "--content",
            "project-signal-7dcbd96d updated with durable next step",
        )
    )
    assert updated["key"] == project_note["key"]
    assert "durable next step" in run_agent_memory(workspace.repo, "retrieve", str(project_note["key"])).stdout

    basename_miss = run_agent_memory_subprocess(workspace.repo, "retrieve", "project-alpha")
    assert basename_miss.returncode != 0
    assert "retrieve expects a full vault-relative key" in basename_miss.stderr
    assert "projects/<project-id>/decisions/parser-choice" in basename_miss.stderr
    assert "projects/<project-id>/plans/features/FEATURE-ID/FEATURE-ID" in basename_miss.stderr
    assert "agent-memory search --scope both" in basename_miss.stderr

    deleted = parse_json_stdout(run_agent_memory(workspace.repo, "delete", str(global_note["key"])))
    assert deleted["deleted"] == global_key
    after_delete = parse_json_stdout(run_agent_memory(workspace.repo, "search", "--scope", "both", "global-signal-cde4b9f6"))
    assert global_key not in result_keys(after_delete)


def test_project_memory_update_moves_title_and_type_indexes(tmp_path: Path) -> None:
    workspace = initialized_workspace(tmp_path)
    project_note = add_cli_memory(
        workspace,
        scope="project",
        memory_type="decision",
        title="Project Transition",
        content="transition-signal-0d1f body stays attached to the moved memory",
    )
    original_key = project_memory_key(workspace, "decisions", "project-transition")
    assert project_note["key"] == original_key

    renamed_key = project_memory_key(workspace, "decisions", "project-transition-renamed")
    renamed = parse_json_stdout(run_agent_memory(workspace.repo, "update", original_key, "--title", "Project Transition Renamed"))
    assert renamed["key"] == renamed_key
    renamed_text = run_agent_memory(workspace.repo, "retrieve", renamed_key).stdout
    assert "# Project Transition Renamed" in renamed_text
    assert "transition-signal-0d1f body stays attached to the moved memory" in renamed_text
    decisions_index = (workspace.vault / "projects" / workspace.project_id / "decisions" / "index.md").read_text()
    assert "[Project Transition](project-transition.md)" not in decisions_index
    assert "[Project Transition Renamed](project-transition-renamed.md)" in decisions_index

    retagged_key = project_memory_key(workspace, "traps", "project-transition-renamed")
    retagged = parse_json_stdout(run_agent_memory(workspace.repo, "update", renamed_key, "--type", "trap"))
    assert retagged["key"] == retagged_key
    retagged_text = run_agent_memory(workspace.repo, "retrieve", retagged_key).stdout
    assert "# Project Transition Renamed" in retagged_text
    assert "transition-signal-0d1f body stays attached to the moved memory" in retagged_text
    assert not (workspace.vault / f"{renamed_key}.md").exists()
    updated_decisions_index = (workspace.vault / "projects" / workspace.project_id / "decisions" / "index.md").read_text()
    traps_index = (workspace.vault / "projects" / workspace.project_id / "traps" / "index.md").read_text()
    assert "[Project Transition Renamed](project-transition-renamed.md)" not in updated_decisions_index
    assert "[Project Transition Renamed](project-transition-renamed.md)" in traps_index

    no_update = run_agent_memory_subprocess(workspace.repo, "update", retagged_key)
    assert no_update.returncode != 0
    assert "update requires at least one of --title, --type, or --content" in no_update.stderr
    assert "AssertionError" in no_update.stderr
    assert "Traceback" in no_update.stderr


def test_search_keys_uses_scoped_title_key_matches(tmp_path: Path) -> None:
    workspace = initialized_workspace(tmp_path)
    project_note = add_cli_memory(
        workspace,
        scope="project",
        memory_type="decision",
        title="Project Graph Beacon",
        content="body-only-project-content-9c0f8c2e",
    )
    global_note = add_cli_memory(
        workspace,
        scope="global",
        memory_type="advice",
        title="Global Graph Beacon",
        content="body-only-global-content-6f3cbdb4",
    )
    project_key = project_memory_key(workspace, "decisions", "project-graph-beacon")
    global_key = "global/advice/global-graph-beacon"
    assert project_note["key"] == project_key
    assert global_note["key"] == global_key

    project_search = parse_json_stdout(run_agent_memory(workspace.repo, "search", "keys", "--scope", "project", "Graph Beacon"))
    assert result_keys(project_search) == {project_key}
    global_search = parse_json_stdout(run_agent_memory(workspace.repo, "search", "keys", "--scope", "global", "Graph Beacon"))
    assert result_keys(global_search) == {global_key}
    combined_search = parse_json_stdout(run_agent_memory(workspace.repo, "search", "keys", "--scope", "both", "Graph Beacon"))
    assert result_keys(combined_search) == {project_key, global_key}


def test_search_content_ranked_uses_scope_roots(tmp_path: Path) -> None:
    workspace = initialized_workspace(tmp_path)
    project_note = add_cli_memory(
        workspace,
        scope="project",
        memory_type="decision",
        title="Probe Project Context",
        content="ranked-context-token-48a4 project-only probe evidence",
    )
    global_note = add_cli_memory(
        workspace,
        scope="global",
        memory_type="advice",
        title="Probe Global Context",
        content="ranked-context-token-48a4 global-only probe evidence",
    )
    project_path = Path(str(project_note["path"])).resolve()
    global_path = Path(str(global_note["path"])).resolve()

    exact_project = search_content(workspace, scope="project", mode="exact", query="ranked-context-token-48a4")
    assert str(project_note["key"]) in result_keys(exact_project)
    assert str(global_note["key"]) not in result_keys(exact_project)
    exact_global = search_content(workspace, scope="global", mode="exact", query="ranked-context-token-48a4")
    assert str(global_note["key"]) in result_keys(exact_global)
    assert str(project_note["key"]) not in result_keys(exact_global)

    project_files = probe_result_files(search_content(workspace, scope="project", mode="ranked", query="ranked-context-token-48a4"))
    assert project_path in project_files
    assert global_path not in project_files
    global_files = probe_result_files(search_content(workspace, scope="global", mode="ranked", query="ranked-context-token-48a4"))
    assert global_path in global_files
    assert project_path not in global_files
    combined_files = probe_result_files(search_content(workspace, scope="both", mode="ranked", query="ranked-context-token-48a4"))
    assert project_path in combined_files
    assert global_path in combined_files


def test_search_content_fuzzy_uses_scope_roots(tmp_path: Path) -> None:
    workspace = initialized_workspace(tmp_path)
    project_note = add_cli_memory(
        workspace,
        scope="project",
        memory_type="decision",
        title="Fuzzy Project Context",
        content="fuzzy-search-token-3b9a project-only indexed evidence",
    )
    global_note = add_cli_memory(
        workspace,
        scope="global",
        memory_type="advice",
        title="Fuzzy Global Context",
        content="fuzzy-search-token-3b9a global-only indexed evidence",
    )
    unrelated_project_note = add_cli_memory(
        workspace,
        scope="project",
        memory_type="decision",
        title="Fuzzy Project Irrelevant",
        content="different indexed evidence",
    )

    project_key = project_memory_key(workspace, "decisions", "fuzzy-project-context")
    global_key = "global/advice/fuzzy-global-context"
    unrelated_project_key = project_memory_key(workspace, "decisions", "fuzzy-project-irrelevant")
    assert project_note["key"] == project_key
    assert global_note["key"] == global_key
    assert unrelated_project_note["key"] == unrelated_project_key

    project_keys = result_keys(search_content(workspace, scope="project", mode="fuzzy", query="fuzzy-search-token-3b9a"))
    assert project_key in project_keys
    assert global_key not in project_keys
    assert unrelated_project_key not in project_keys
    assert all(key.startswith(f"projects/{workspace.project_id}/") for key in project_keys)

    global_keys = result_keys(search_content(workspace, scope="global", mode="fuzzy", query="fuzzy-search-token-3b9a"))
    assert global_key in global_keys
    assert project_key not in global_keys
    assert unrelated_project_key not in global_keys
    assert all(key.startswith("global/") for key in global_keys)

    combined_keys = result_keys(search_content(workspace, scope="both", mode="fuzzy", query="fuzzy-search-token-3b9a"))
    assert project_key in combined_keys
    assert global_key in combined_keys
    assert unrelated_project_key not in combined_keys


def test_search_metadata_filters_real_frontmatter(tmp_path: Path) -> None:
    workspace = initialized_workspace(tmp_path)
    project_note = add_cli_memory(
        workspace,
        scope="project",
        memory_type="decision",
        title="Metadata Project",
        content="metadata project body",
    )
    global_note = add_cli_memory(
        workspace,
        scope="global",
        memory_type="trap",
        title="Metadata Global",
        content="metadata global body",
    )

    project_key = project_memory_key(workspace, "decisions", "metadata-project")
    global_key = "global/traps/metadata-global"
    assert project_note["key"] == project_key
    assert global_note["key"] == global_key

    all_metadata = parse_json_stdout(run_agent_memory(workspace.repo, "search", "metadata", "--scope", "both"))
    assert result_keys(all_metadata) == {project_key, global_key}

    project_results = parse_json_stdout(
        run_agent_memory(
            workspace.repo,
            "search",
            "metadata",
            "--scope",
            "project",
            "--type",
            "decision",
            "--tag",
            "project",
            "--created-after",
            "1970-01-01T00:00:00+00:00",
        )
    )
    assert result_keys(project_results) == {project_key}

    global_results = parse_json_stdout(
        run_agent_memory(
            workspace.repo,
            "search",
            "metadata",
            "--scope",
            "global",
            "--type",
            "trap",
            "--tag",
            "global",
            "--created-after",
            "1970-01-01T00:00:00+00:00",
        )
    )
    assert result_keys(global_results) == {global_key}


def test_maintain_squash_returns_project_index_scope_with_iwe(tmp_path: Path) -> None:
    workspace = initialized_workspace(tmp_path)
    add_cli_memory(
        workspace,
        scope="project",
        memory_type="decision",
        title="Squash Project Signal",
        content="squash-project-signal-2d4f1c7a must appear in project consolidation",
    )
    add_cli_memory(
        workspace,
        scope="global",
        memory_type="advice",
        title="Squash Global Signal",
        content="squash-global-signal-88ec672b must stay outside project consolidation",
    )

    squashed = run_agent_memory(workspace.repo, "maintain", "squash", f"projects/{workspace.project_id}/index", "--depth", "3")

    assert f"# {workspace.project_id}" in squashed.stdout
    assert "- [Decisions](decisions/index) - Project decision memories." in squashed.stdout
    assert "- [Traps](traps/index) - Project trap memories." in squashed.stdout
    assert "squash-global-signal-88ec672b" not in squashed.stdout


def test_maintain_split_merge_and_doctor_real_memory_graph(tmp_path: Path) -> None:
    workspace = initialized_workspace(tmp_path)
    source_note = add_cli_memory(
        workspace,
        scope="project",
        memory_type="decision",
        title="Split Source",
        content="Introductory context.\n\n## Extracted Plan\nSplit details stay recoverable.",
    )
    source_key = project_memory_key(workspace, "decisions", "split-source")
    assert source_note["key"] == source_key

    split = parse_json_stdout(run_agent_memory(workspace.repo, "maintain", "split", source_key, "--section", "Extracted Plan"))
    assert split["key"] == source_key
    assert split["section"] == "Extracted Plan"
    extracted_keys = {json_string(key) for key in json_array(split["extracted"])}
    assert len(extracted_keys) == 1
    extracted_key = next(iter(extracted_keys))
    assert extracted_key != source_key
    split_search = parse_json_stdout(run_agent_memory(workspace.repo, "search", "keys", "--scope", "project", "Extracted Plan"))
    assert extracted_key in result_keys(split_search)
    assert (workspace.vault / f"{extracted_key}.md").is_file()
    split_source_text = (workspace.vault / f"{source_key}.md").read_text(encoding="utf-8")
    assert "Split details stay recoverable." not in split_source_text
    assert "Extracted Plan" in split_source_text

    merged = parse_json_stdout(run_agent_memory(workspace.repo, "maintain", "merge", source_key, "--reference", extracted_key))
    assert merged["key"] == source_key
    assert merged["reference"] == extracted_key
    assert not (workspace.vault / f"{extracted_key}.md").exists()
    merged_source_text = run_agent_memory(workspace.repo, "retrieve", source_key).stdout
    assert "## Extracted Plan" in merged_source_text
    assert "Split details stay recoverable." in merged_source_text

    validation = parse_json_stdout(run_agent_memory(workspace.repo, "doctor"))
    assert validation["vault"] == str(workspace.vault)
    assert validation["project_id"] == workspace.project_id
    assert validation["project_root"] == str(workspace.repo)


def test_init_project_replaces_existing_agents_memory_pointer(tmp_path: Path) -> None:
    workspace = initialized_workspace_with_agents(
        tmp_path,
        "# Existing repo instructions\n\n"
        "Preserve local setup rules before memory.\n\n"
        "<!-- agent-memory:start -->\n"
        "stale vault: /tmp/not-the-current-vault\n"
        "<!-- agent-memory:end -->\n\n"
        "Preserve local setup rules after memory.\n",
    )

    agents_pointer = (workspace.repo / "AGENTS.md").read_text(encoding="utf-8")
    assert "Preserve local setup rules before memory." in agents_pointer
    assert "Preserve local setup rules after memory." in agents_pointer
    assert "/tmp/not-the-current-vault" not in agents_pointer
    assert agents_pointer.count("<!-- agent-memory:start -->") == 1
    assert agents_pointer.count("<!-- agent-memory:end -->") == 1
    assert f"This repository uses the central agent memory vault at `{workspace.vault}`." in agents_pointer
    assert f"Project memory key: `projects/{workspace.project_id}/index`." in agents_pointer


def test_init_project_appends_agents_memory_pointer_to_unmarked_agents(
    tmp_path: Path,
) -> None:
    workspace = initialized_workspace_with_agents(
        tmp_path,
        "# Existing repo instructions\n\nPreserve instructions that are not managed by agent-memory.\n",
    )

    agents_pointer = (workspace.repo / "AGENTS.md").read_text(encoding="utf-8")
    assert "Preserve instructions that are not managed by agent-memory." in agents_pointer
    assert agents_pointer.count("<!-- agent-memory:start -->") == 1
    assert agents_pointer.count("<!-- agent-memory:end -->") == 1
    assert f"This repository uses the central agent memory vault at `{workspace.vault}`." in agents_pointer
    assert f"Project memory key: `projects/{workspace.project_id}/index`." in agents_pointer


def test_init_project_symlinks_agent_state_directories_to_vault_project(tmp_path: Path) -> None:
    workspace = initialized_workspace(tmp_path)
    vault_path = project_agent_state_path(workspace)

    for name in (".agents", ".hermes"):
        repo_path = workspace.repo / name
        assert vault_path.is_dir()
        assert repo_path.is_symlink()
        assert repo_path.resolve() == vault_path.resolve()


def test_init_project_migrates_existing_agent_state_into_vault_project(tmp_path: Path) -> None:
    git_repo = initialized_git_repo(tmp_path)
    local_agents = git_repo.path / ".agents"
    local_agents.mkdir()
    (local_agents / "justfile").write_text("_private:\n    true\n", encoding="utf-8")
    local_hermes_plans = git_repo.path / ".hermes" / "plans"
    local_hermes_plans.mkdir(parents=True)
    (local_hermes_plans / "existing-plan.md").write_text("# Existing Plan\n", encoding="utf-8")

    workspace = initialized_project_workspace(tmp_path, git_repo)

    assert (project_agent_state_path(workspace) / "justfile").read_text(encoding="utf-8") == "_private:\n    true\n"
    assert (project_agent_state_path(workspace) / "plans" / "existing-plan.md").read_text(encoding="utf-8") == "# Existing Plan\n"
    assert (workspace.repo / ".agents").is_symlink()
    assert (workspace.repo / ".hermes").is_symlink()
    assert (workspace.repo / ".agents").resolve() == project_agent_state_path(workspace).resolve()
    assert (workspace.repo / ".hermes").resolve() == project_agent_state_path(workspace).resolve()


def test_maintain_move_memory_to_global_leaves_project_pointer(
    tmp_path: Path,
) -> None:
    workspace = initialized_workspace(tmp_path)
    note = add_cli_memory(
        workspace,
        scope="project",
        memory_type="trap",
        title="Promotion Trap",
        content="promote-signal-f88f0a72 must become shared knowledge",
    )

    moved = parse_json_stdout(run_agent_memory(workspace.repo, "maintain", "move", str(note["key"]), "--to", "global/traps"))

    destination = Path(str(moved["path"]))
    pointer = Path(str(note["path"]))
    assert destination == workspace.vault / "global" / "traps" / "promotion-trap.md"
    assert pointer == workspace.vault / "projects" / workspace.project_id / "traps" / "promotion-trap.md"
    assert "promote-signal-f88f0a72 must become shared knowledge" in destination.read_text()
    assert_okf_concept_metadata(
        frontmatter(destination),
        memory_type="trap",
        title="Promotion Trap",
        description="promote-signal-f88f0a72 must become shared knowledge",
        tags=["global", "trap", "promoted"],
    )
    assert frontmatter(destination)["origin_project_id"] == workspace.project_id
    assert frontmatter(destination)["scope"] == "global"
    assert_okf_concept_metadata(
        frontmatter(pointer),
        memory_type="trap",
        title="Promotion Trap",
        description="Promoted to global/traps/promotion-trap.",
        tags=["project", "trap", "promotion-pointer"],
    )
    assert frontmatter(pointer)["scope"] == "project"
    assert "global/traps/promotion-trap" in pointer.read_text()
    assert "* [Promotion Trap](promotion-trap.md) - promote-signal-f88f0a72 must become shared knowledge" in (destination.parent / "index.md").read_text()
    assert "* [Promotion Trap](promotion-trap.md) - Promoted to global/traps/promotion-trap." in (pointer.parent / "index.md").read_text()


def test_project_commands_without_config_fail_with_first_time_setup_guidance(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)

    result = run_agent_memory_subprocess(
        repo,
        "add",
        "--scope",
        "project",
        "--type",
        "decision",
        "--title",
        "Missing Config",
        "--content",
        "this command cannot run without project setup",
    )

    assert result.returncode != 0
    assert "No project memory config found" in result.stderr
    assert "agent-memory init project --vault" in result.stderr
    assert "ProjectNotInitializedError" in result.stderr
    assert "Traceback" in result.stderr


def test_startup_doctor_gate_reports_missing_dependency_before_command_logic(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    empty_path = tmp_path / "empty-bin"
    empty_path.mkdir()
    env = os.environ.copy()
    env["PATH"] = str(empty_path)

    result = run_agent_memory_subprocess(repo, "doctor", env=env)

    assert result.returncode != 0
    assert "Missing required dependency: git" in result.stderr
    assert "Install instructions: run `just setup` from the agent-memory checkout" in result.stderr
    assert "DependencyError" in result.stderr
    assert "Traceback" in result.stderr


def test_startup_doctor_gate_reports_failed_dependency_output(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    broken_bin = tmp_path / "broken-bin"
    broken_bin.mkdir()
    broken_git = broken_bin / "git"
    broken_git.write_text("#!/bin/sh\nprintf 'bad git stdout\\n'\nprintf 'bad git stderr\\n' >&2\nexit 7\n", encoding="utf-8")
    broken_git.chmod(0o755)
    env = agent_memory_env()
    env["PATH"] = f"{broken_bin}:{env['PATH']}"

    result = run_agent_memory_subprocess(repo, "doctor", env=env)

    assert result.returncode != 0
    assert "Dependency check failed: git" in result.stderr
    assert "Command: git --version" in result.stderr
    assert "bad git stdout" in result.stderr
    assert "bad git stderr" in result.stderr
    assert "DependencyError" in result.stderr
    assert "Traceback" in result.stderr


def test_load_project_config_raises_project_not_initialized(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    with pytest.raises(ProjectNotInitializedError) as excinfo:
        operations_load_project_config(repo)
    message = str(excinfo.value)
    assert "No project memory config found" in message
    assert "agent-memory init project --vault" in message


def test_check_dependency_raises_for_missing_binary(tmp_path: Path) -> None:
    empty_path = tmp_path / "empty-bin"
    empty_path.mkdir()
    dependency = DependencyCheck("absent-tool", ("absent-tool", "--version"), "install absent-tool from somewhere")
    original_env = os.environ.copy()
    try:
        os.environ.clear()
        os.environ.update({"PATH": str(empty_path)})
        with pytest.raises(DependencyError) as excinfo:
            check_dependency(dependency, tmp_path)
    finally:
        os.environ.clear()
        os.environ.update(original_env)
    error = excinfo.value
    assert error.name == "absent-tool"
    assert error.stdout is None
    assert error.stderr is None
    assert "Missing required dependency: absent-tool" in str(error)
    assert "install absent-tool from somewhere" in str(error)


def test_check_dependency_raises_for_failed_command(tmp_path: Path) -> None:
    broken_bin = tmp_path / "broken-bin"
    broken_bin.mkdir()
    broken_tool = broken_bin / "broken-tool"
    broken_tool.write_text(
        "#!/bin/sh\nprintf 'broken stdout\\n'\nprintf 'broken stderr\\n' >&2\nexit 3\n",
        encoding="utf-8",
    )
    broken_tool.chmod(0o755)
    dependency = DependencyCheck("broken-tool", ("broken-tool", "check"), "reinstall broken-tool")
    env = agent_memory_env()
    env["PATH"] = f"{broken_bin}:{env['PATH']}"
    original_env = os.environ.copy()
    try:
        os.environ.clear()
        os.environ.update(env)
        with pytest.raises(DependencyError) as excinfo:
            check_dependency(dependency, tmp_path)
    finally:
        os.environ.clear()
        os.environ.update(original_env)
    error = excinfo.value
    assert error.name == "broken-tool"
    assert error.stdout is not None and "broken stdout" in error.stdout
    assert error.stderr is not None and "broken stderr" in error.stderr
    assert "Dependency check failed: broken-tool" in str(error)
    assert "Command: broken-tool check" in str(error)


def test_update_memory_requires_at_least_one_field(tmp_path: Path) -> None:
    workspace = initialized_workspace(tmp_path)
    with pytest.raises(AssertionError) as excinfo:
        update_memory("nonexistent-key", None, None, None, workspace.repo)
    assert "update requires at least one of --title, --type, or --content" in str(excinfo.value)


def test_doctor_reports_declared_project_contract(tmp_path: Path) -> None:
    workspace = initialized_workspace(tmp_path)

    doctor = parse_json_stdout(run_agent_memory(workspace.repo, "doctor"))

    assert doctor["vault"] == str(workspace.vault)
    assert doctor["project_id"] == workspace.project_id
    assert doctor["project_root"] == str(workspace.repo)
    assert doctor["agent_state"] == [
        {
            "name": ".agents",
            "repo_path": str(workspace.repo / ".agents"),
            "vault_path": str(project_agent_state_path(workspace)),
        },
        {
            "name": ".hermes",
            "repo_path": str(workspace.repo / ".hermes"),
            "vault_path": str(project_agent_state_path(workspace)),
        },
    ]
    assert doctor["tools"] == ["git", "rg", "npx", "@probelabs/probe", "zk"]
    assert doctor["dependencies"] == [
        {"name": "git", "command": ["git", "--version"], "status": "ok"},
        {"name": "rg", "command": ["rg", "--version"], "status": "ok"},
        {"name": "npx", "command": ["npx", "--version"], "status": "ok"},
        {"name": "@probelabs/probe", "command": ["npx", "-y", "@probelabs/probe@latest", "--version"], "status": "ok"},
        {"name": "zk", "command": ["zk", "--version"], "status": "ok"},
    ]


def test_cli_main_runs_doctor_gate_then_dispatches_and_exits_zero(tmp_path: Path) -> None:
    workspace = initialized_workspace(tmp_path)
    command_env = agent_memory_env()
    original_cwd = Path.cwd()
    original_argv = sys.argv.copy()
    original_env = os.environ.copy()
    stdout = StringIO()
    try:
        os.chdir(workspace.repo)
        os.environ.clear()
        os.environ.update(command_env)
        sys.argv = ["agent-memory", "doctor"]
        with redirect_stdout(stdout), pytest.raises(SystemExit) as excinfo:
            cli_main()
    finally:
        os.chdir(original_cwd)
        sys.argv = original_argv
        os.environ.clear()
        os.environ.update(original_env)
    assert excinfo.value.code == 0
    payload = json.loads(stdout.getvalue())
    assert payload["project_root"] == str(workspace.repo)


def test_python_dash_m_agent_memory_module_entrypoint_runs_doctor(tmp_path: Path) -> None:
    workspace = initialized_workspace(tmp_path)
    command_env = agent_memory_env()
    original_cwd = Path.cwd()
    original_argv = sys.argv.copy()
    original_env = os.environ.copy()
    stdout = StringIO()
    try:
        os.chdir(workspace.repo)
        os.environ.clear()
        os.environ.update(command_env)
        sys.argv = ["agent-memory", "doctor"]
        with redirect_stdout(stdout), pytest.raises(SystemExit) as excinfo:
            runpy.run_module("agent_memory.__main__", run_name="__main__")
    finally:
        os.chdir(original_cwd)
        sys.argv = original_argv
        os.environ.clear()
        os.environ.update(original_env)
    assert excinfo.value.code == 0
    payload = json.loads(stdout.getvalue())
    assert payload["project_root"] == str(workspace.repo)


def test_inspect_overview_schema_and_paths_map_real_vault(tmp_path: Path) -> None:
    workspace = initialized_workspace(tmp_path)
    project_note = add_cli_memory(
        workspace,
        scope="project",
        memory_type="decision",
        title="Inspect Project",
        content="Project navigation memory.",
    )
    global_note = add_cli_memory(
        workspace,
        scope="global",
        memory_type="advice",
        title="Inspect Global",
        content="Global navigation memory.",
    )
    project_key = project_memory_key(workspace, "decisions", "inspect-project")
    global_key = "global/advice/inspect-global"
    assert project_note["key"] == project_key
    assert global_note["key"] == global_key

    overview = inspect_json(workspace, "overview", "--scope", "both", "--format", "json")
    assert overview["vault"] == str(workspace.vault)
    assert overview["project_id"] == workspace.project_id
    assert overview["scope"] == "both"
    assert overview["roots"] == ["global/index", f"projects/{workspace.project_id}/index"]
    assert overview["totals"] == {"notes": 2, "indexes": 14}
    assert overview["notes_by_scope"] == {"global": 1, "project": 1}
    assert overview["notes_by_type"] == {"advice": 1, "decision": 1}

    schema = inspect_json(workspace, "schema", "--format", "json")
    commands = json_object(schema["commands"])
    assert commands["inspect"] == [
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
    assert schema["scopes"] == ["project", "global", "both"]
    assert schema["memory_types"] == ["decision", "trap", "advice", "context", "reference", "plan"]

    paths = inspect_json(workspace, "paths", "--scope", "project", "--kind", "notes", "--format", "json")
    assert paths["scope"] == "project"
    assert paths["kind"] == "notes"
    assert paths["paths"] == [
        {
            "key": project_key,
            "path": str(workspace.vault / "projects" / workspace.project_id / "decisions" / "inspect-project.md"),
            "title": "Inspect Project",
            "type": "decision",
            "scope": "project",
        }
    ]
    root_paths = inspect_json(workspace, "paths", "--scope", "global", "--kind", "roots", "--format", "json")
    assert root_paths["paths"] == [
        {
            "key": "global/index",
            "path": str(workspace.vault / "global" / "index.md"),
            "scope": "global",
        }
    ]
    index_paths = inspect_json(workspace, "paths", "--scope", "project", "--kind", "indexes", "--format", "json")
    index_keys = {json_string(json_object(record)["key"]) for record in json_array(index_paths["paths"])}
    assert {
        f"projects/{workspace.project_id}/index",
        f"projects/{workspace.project_id}/decisions/index",
        f"projects/{workspace.project_id}/advice/index",
    }.issubset(index_keys)
    all_paths = inspect_json(workspace, "paths", "--scope", "both", "--kind", "all", "--format", "json")
    all_keys = {json_string(json_object(record)["key"]) for record in json_array(all_paths["paths"])}
    assert {"global/index", f"projects/{workspace.project_id}/index", project_key, global_key}.issubset(all_keys)


def test_inspect_tree_maps_project_memory_hierarchy(tmp_path: Path) -> None:
    workspace = initialized_workspace(tmp_path)
    project_note = add_cli_memory(
        workspace,
        scope="project",
        memory_type="decision",
        title="Inspect Project",
        content="Project navigation memory.",
    )
    project_key = project_memory_key(workspace, "decisions", "inspect-project")
    assert project_note["key"] == project_key

    tree = inspect_json(workspace, "tree", "--scope", "project", "--depth", "2", "--format", "json")
    tree_roots = json_array(tree["roots"])
    tree_root = json_object(tree_roots[0])
    assert tree["scope"] == "project"
    assert tree_root["key"] == f"projects/{workspace.project_id}/index"
    assert inspect_tree_keys(tree_root) == {
        f"projects/{workspace.project_id}/index",
        f"projects/{workspace.project_id}/advice/index",
        f"projects/{workspace.project_id}/context/index",
        f"projects/{workspace.project_id}/decisions/index",
        f"projects/{workspace.project_id}/plans/index",
        project_key,
        f"projects/{workspace.project_id}/references/index",
        f"projects/{workspace.project_id}/traps/index",
    }
    decision_indexes = [
        json_object(raw_child) for raw_child in json_array(tree_root["children"]) if json_object(raw_child)["key"] == f"projects/{workspace.project_id}/decisions/index"
    ]
    decision_children = json_array(decision_indexes[0]["children"])
    assert [json_string(json_object(child)["key"]) for child in decision_children] == [project_key]


def linked_inspect_workspace(tmp_path: Path) -> tuple[CliWorkspace, str, str]:
    workspace = initialized_workspace(tmp_path)
    global_note = add_cli_memory(
        workspace,
        scope="global",
        memory_type="advice",
        title="Inspect Advice",
        content="Use read-only inspection before maintenance.",
    )
    global_key = "global/advice/inspect-advice"
    project_note = add_cli_memory(
        workspace,
        scope="project",
        memory_type="decision",
        title="Inspect Linked",
        content=("## Investigation\nUse outline and graph inspection.\n\nSee [Inspect Advice](../../../global/advice/inspect-advice.md).\nIgnore empty [placeholder]()."),
    )
    project_key = project_memory_key(workspace, "decisions", "inspect-linked")
    assert global_note["key"] == global_key
    assert project_note["key"] == project_key
    return workspace, project_key, global_key


def test_inspect_links_real_vault(tmp_path: Path) -> None:
    workspace, project_key, global_key = linked_inspect_workspace(tmp_path)
    no_depth_children = inspect_json(
        workspace,
        "links",
        project_key,
        "--direction",
        "children",
        "--depth",
        "0",
        "--format",
        "json",
    )
    assert no_depth_children["links"] == []
    children = inspect_json(
        workspace,
        "links",
        project_key,
        "--direction",
        "children",
        "--depth",
        "1",
        "--format",
        "json",
    )
    assert children["links"] == [
        {
            "key": global_key,
            "path": str(workspace.vault / "global" / "advice" / "inspect-advice.md"),
            "title": "Inspect Advice",
            "depth": 1,
        }
    ]
    parents = inspect_json(
        workspace,
        "links",
        project_key,
        "--direction",
        "parents",
        "--depth",
        "1",
        "--format",
        "json",
    )
    assert parents["links"] == [
        {
            "key": f"projects/{workspace.project_id}/decisions/index",
            "path": str(workspace.vault / "projects" / workspace.project_id / "decisions" / "index.md"),
            "title": "Decisions",
            "depth": 1,
        }
    ]
    both = inspect_json(
        workspace,
        "links",
        project_key,
        "--direction",
        "both",
        "--depth",
        "1",
        "--format",
        "json",
    )
    both_keys = set(records_by_key(both, "links"))
    assert both_keys == {f"projects/{workspace.project_id}/decisions/index", global_key}


def test_inspect_links_dedupes_reciprocal_index_links(tmp_path: Path) -> None:
    workspace = initialized_workspace(tmp_path)
    project_note = add_cli_memory(
        workspace,
        scope="project",
        memory_type="decision",
        title="Inspect Cycle",
        content="See [Decision Index](index.md) before traversing children.",
    )
    project_key = project_memory_key(workspace, "decisions", "inspect-cycle")
    decision_index_key = f"projects/{workspace.project_id}/decisions/index"
    decision_index_record = {
        "key": decision_index_key,
        "path": str(workspace.vault / "projects" / workspace.project_id / "decisions" / "index.md"),
        "title": "Decisions",
        "depth": 1,
    }
    assert project_note["key"] == project_key

    children = inspect_json(
        workspace,
        "links",
        project_key,
        "--direction",
        "children",
        "--depth",
        "2",
        "--format",
        "json",
    )
    assert children["links"] == [decision_index_record]

    both = inspect_json(
        workspace,
        "links",
        project_key,
        "--direction",
        "both",
        "--depth",
        "1",
        "--format",
        "json",
    )
    assert both["links"] == [decision_index_record]


def test_inspect_outline_and_recent_real_vault(tmp_path: Path) -> None:
    workspace, project_key, _global_key = linked_inspect_workspace(tmp_path)
    outline = inspect_json(workspace, "outline", project_key, "--format", "json")
    outline_headings = json_records(outline, "headings")
    assert [(json_string(heading["title"]), heading["level"]) for heading in outline_headings] == [
        ("Inspect Linked", 1),
        ("Investigation", 2),
    ]

    recent = inspect_json(
        workspace,
        "recent",
        "--scope",
        "project",
        "--since",
        "1970-01-01T00:00:00+00:00",
        "--format",
        "json",
    )
    assert result_keys(recent) == {project_key}


def test_inspect_stats_real_vault(tmp_path: Path) -> None:
    workspace, _project_key, _global_key = linked_inspect_workspace(tmp_path)
    stats = inspect_json(workspace, "stats", "--scope", "both", "--by", "type", "--format", "json")
    assert stats["counts"] == {"advice": 1, "decision": 1}
    stats_by_scope = inspect_json(workspace, "stats", "--scope", "both", "--by", "scope", "--format", "json")
    assert stats_by_scope["counts"] == {"global": 1, "project": 1}
    stats_by_day = inspect_json(workspace, "stats", "--scope", "both", "--by", "day", "--format", "json")
    day_counts = json_object(stats_by_day["counts"])
    assert list(day_counts.values()) == [2]


def test_inspect_export_profiles_real_vault(tmp_path: Path) -> None:
    workspace, project_key, global_key = linked_inspect_workspace(tmp_path)
    exported = inspect_json(workspace, "export", "--scope", "project", "--profile", "map", "--format", "graph-json")
    assert exported["profile"] == "map"
    exported_nodes = records_by_key(exported, "nodes")
    assert exported_nodes[project_key]["title"] == "Inspect Linked"
    assert "content" not in exported_nodes[project_key]
    exported_edges = json_records(exported, "edges")
    assert [(json_string(edge["source"]), json_string(edge["target"])) for edge in exported_edges].count((f"projects/{workspace.project_id}/decisions/index", project_key)) == 1
    context_export = inspect_json(workspace, "export", "--scope", "project", "--profile", "context", "--format", "graph-json")
    context_nodes = records_by_key(context_export, "nodes")
    assert "Use outline and graph inspection." in json_string(context_nodes[project_key]["content"])
    archive_export = inspect_json(workspace, "export", "--scope", "global", "--profile", "archive", "--format", "graph-json")
    archive_nodes = records_by_key(archive_export, "nodes")
    archive_metadata = json_object(archive_nodes[global_key]["metadata"])
    assert archive_metadata["promotable"] is False
    assert archive_metadata["tags"] == ["global", "advice"]


def test_merge_probe_payloads_treats_absent_skipped_files_as_no_skips() -> None:
    # Probe 0.6.0's --format json contract emits "skipped_files" only when it skips
    # files under the token budget; when nothing is skipped it omits the key entirely.
    # agent-memory must interpret that omission as the documented "no files skipped" outcome and
    # produce an empty skipped-files section, not raise.
    payload_no_skips: JsonObject = {
        "limits": {"total_bytes": 685, "total_tokens": 201},
        "results": [{"file": "/vault/note.md", "score": 0.42}],
        "version": "0.6.0",
    }
    merged = merge_probe_payloads([payload_no_skips], max_results=5, max_tokens=500)
    assert merged["skipped_files"] == []
    assert merged["results"] == [{"file": "/vault/note.md", "score": 0.42}]


def test_merge_probe_payloads_rejects_null_skipped_files() -> None:
    # When the key is present it must carry a real list. A null or non-list value is a
    # malformed/incompatible Probe payload, not a "no skips" outcome, and must fail
    # loudly instead of being silently coerced to an empty section.
    payload_null_skips: JsonObject = {
        "limits": {"total_bytes": 685, "total_tokens": 201},
        "results": [{"file": "/vault/note.md", "score": 0.42}],
        "skipped_files": None,
        "version": "0.6.0",
    }
    with pytest.raises(AssertionError):
        merge_probe_payloads([payload_null_skips], max_results=5, max_tokens=500)


def test_plan_cli_lifecycle_and_unified_search(tmp_path: Path) -> None:
    workspace = initialized_workspace(tmp_path)
    run_agent_memory(
        workspace.repo,
        "plan",
        "add",
        "--type",
        "feature",
        "--id",
        "FEATURE-DEMO",
        "--set",
        "title=Demo",
        "--set",
        "status=in-progress",
        "--set",
        "description=plan-card-signal-9c1f",
        "--set",
        "plans=[[PLAN-DEMO]]",
    )
    # The plan is in-progress (a started child for the in-progress feature) and tags equal
    # its sole ancestor, so the tree is valid under every validation rule.
    run_agent_memory(
        workspace.repo,
        "plan",
        "add",
        "--type",
        "plan",
        "--id",
        "PLAN-DEMO",
        "--parent",
        "FEATURE-DEMO",
        "--set",
        "title=Plan",
        "--set",
        "status=in-progress",
        "--set",
        "description=demo plan",
        "--set",
        "parents=[[FEATURE-DEMO]]",
        "--set",
        "successCriteria=ships",
        "--set",
        "tags=FEATURE-DEMO",
    )
    plan_path = workspace.vault / "projects" / workspace.project_id / "plans" / "features" / "FEATURE-DEMO" / "plans" / "PLAN-DEMO" / "PLAN-DEMO.md"
    assert plan_path.is_file()

    clean = parse_json_stdout(run_agent_memory(workspace.repo, "plan", "validate"))
    assert json_array(clean["problems"]) == []

    dag = parse_json_stdout(run_agent_memory(workspace.repo, "plan", "dag"))
    dag_text = Path(json_string(dag["path"])).read_text(encoding="utf-8")
    assert dag_text.count("```mermaid") == 2

    feature_key = f"projects/{workspace.project_id}/plans/features/FEATURE-DEMO/FEATURE-DEMO"
    search = parse_json_stdout(run_agent_memory(workspace.repo, "search", "--scope", "project", "plan-card-signal-9c1f"))
    assert feature_key in result_keys(search)
    feature_text = run_agent_memory(workspace.repo, "retrieve", feature_key).stdout
    assert f"#projects/{workspace.project_id}/plans/features/FEATURE-DEMO/FEATURE-DEMO" in feature_text
    assert "# FEATURE-DEMO" in feature_text

    # migrate an in-repo card tree (carrying trackerStatus) into the vault
    source = tmp_path / "incoming" / "plans" / "features" / "FEATURE-MIG"
    source.mkdir(parents=True)
    (source / "FEATURE-MIG.md").write_text(
        "---\n"
        + yaml.safe_dump(
            {"id": "FEATURE-MIG", "trackerStatus": {"type": "feature"}, "title": "Migrated", "status": "in-progress", "description": "migrated"},
            sort_keys=False,
        )
        + "---\n# Migrated\n",
        encoding="utf-8",
    )
    run_agent_memory(workspace.repo, "plan", "migrate", "--from", str(tmp_path / "incoming" / "plans"))
    migrated_path = workspace.vault / "projects" / workspace.project_id / "plans" / "features" / "FEATURE-MIG" / "FEATURE-MIG.md"
    assert migrated_path.is_file()
    assert "trackerStatus" not in migrated_path.read_text(encoding="utf-8")
    assert json_array(parse_json_stdout(run_agent_memory(workspace.repo, "plan", "validate"))["problems"]) == []

    run_agent_memory(workspace.repo, "plan", "delete", "FEATURE-MIG")
    assert not migrated_path.exists()

    run_agent_memory(workspace.repo, "plan", "update", "PLAN-DEMO", "--set", "dependsOn=[[TASK-GHOST]]")
    flagged = parse_json_stdout(run_agent_memory(workspace.repo, "plan", "validate"))
    problems = [json_object(item) for item in json_array(flagged["problems"])]
    assert any(json_string(problem["kind"]) == "reference" for problem in problems)


def unbound_dir(tmp_path: Path) -> Path:
    # A directory with no project binding and no git repository at all, modeling the
    # `$HOME`/unbound-repo case from issue #25 where global operations must still work.
    loose = tmp_path / "loose"
    loose.mkdir()
    return loose


def test_global_add_and_search_run_without_project_binding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Issue #25: storing or searching a *global* memory must not require the cwd to be a
    # bound project. The global vault is resolved from AGENT_MEMORY_VAULT (falling back to
    # the shipped default) independent of any cwd `.agent-memory.toml`.
    vault = tmp_path / "vault"
    run_agent_memory(tmp_path, "maintain", "init-global", "--vault", str(vault))
    monkeypatch.setenv("AGENT_MEMORY_VAULT", str(vault))
    loose = unbound_dir(tmp_path)

    added = parse_json_stdout(
        run_agent_memory(
            loose,
            "add",
            "--scope",
            "global",
            "--type",
            "advice",
            "--title",
            "Unbound Global Note",
            "--content",
            "unbound-global-token-7a1c evidence body",
        )
    )
    assert added["key"] == "global/advice/unbound-global-note"
    assert (vault / "global" / "advice" / "unbound-global-note.md").is_file()

    found = parse_json_stdout(run_agent_memory(loose, "search", "--scope", "global", "unbound-global-token-7a1c"))
    assert "global/advice/unbound-global-note" in result_keys(found)


def test_global_doctor_runs_without_project_binding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Issue #25: `doctor` must not crash from an unbound directory; it reports global vault
    # and tool health and marks the absence of a project binding instead of raising.
    vault = tmp_path / "vault"
    run_agent_memory(tmp_path, "maintain", "init-global", "--vault", str(vault))
    monkeypatch.setenv("AGENT_MEMORY_VAULT", str(vault))
    loose = unbound_dir(tmp_path)

    report = parse_json_stdout(run_agent_memory(loose, "doctor"))
    assert report["vault"] == str(vault)
    assert report["project_bound"] is False
    assert report["project_id"] is None


def test_global_op_error_names_init_global_only_when_vault_missing(tmp_path: Path) -> None:
    # Issue #25: when the global vault genuinely does not exist, the error names that
    # condition and the `maintain init-global` remedy only -- never `init project` in the
    # unrelated cwd. Run as a real subprocess so an uncaught error surfaces as a nonzero
    # exit with the message on stderr.
    missing_vault = tmp_path / "no-such-vault"
    env = agent_memory_env()
    env["AGENT_MEMORY_VAULT"] = str(missing_vault)
    loose = unbound_dir(tmp_path)

    result = run_agent_memory_subprocess(
        loose,
        "add",
        "--scope",
        "global",
        "--type",
        "advice",
        "--title",
        "Doomed",
        "--content",
        "body",
        env=env,
    )
    assert result.returncode != 0
    assert "maintain init-global" in result.stderr
    assert "init project" not in result.stderr


def test_search_defaults_to_both_scopes(tmp_path: Path) -> None:
    # Issue #22: a bare `search <term>` with no --scope searches both project and global.
    workspace = initialized_workspace(tmp_path)
    project_note = add_cli_memory(
        workspace,
        scope="project",
        memory_type="decision",
        title="Default Scope Project",
        content="default-scope-token-5e2b project body",
    )
    global_note = add_cli_memory(
        workspace,
        scope="global",
        memory_type="advice",
        title="Default Scope Global",
        content="default-scope-token-5e2b global body",
    )

    defaulted = parse_json_stdout(run_agent_memory(workspace.repo, "search", "default-scope-token-5e2b"))
    keys = result_keys(defaulted)
    assert str(project_note["key"]) in keys
    assert str(global_note["key"]) in keys
