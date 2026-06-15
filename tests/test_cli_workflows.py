from __future__ import annotations

import json
import os
import subprocess
import tempfile
import tomllib
from datetime import UTC, datetime
from pathlib import Path

import yaml

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
ZK_BIN_DIR = Path(tempfile.mkdtemp(prefix="iwe2-zk-"))
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
assert (ZK_BIN_DIR / "zk").is_file()
GLOBAL_GRAPH_KEYS = [
    "global/index",
    "global/decisions/index",
    "global/traps/index",
    "global/advice/index",
    "global/context/index",
    "global/references/index",
]
PROJECT_GRAPH_CHILDREN = [
    "decisions/index",
    "traps/index",
    "advice/index",
    "context/index",
    "references/index",
]


type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]
type JsonArray = list[JsonValue]


def run_iwe2(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "uv",
            "run",
            "--project",
            str(PROJECT_ROOT),
            "--directory",
            str(cwd),
            "iwe2",
            *args,
        ],
        check=True,
        text=True,
        capture_output=True,
        env=iwe2_env(),
    )


def run_iwe2_unchecked(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "uv",
            "run",
            "--project",
            str(PROJECT_ROOT),
            "--directory",
            str(cwd),
            "iwe2",
            *args,
        ],
        check=False,
        text=True,
        capture_output=True,
        env=iwe2_env(),
    )


def run_iwe2_module(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "uv",
            "run",
            "--project",
            str(PROJECT_ROOT),
            "--directory",
            str(cwd),
            "python",
            "-m",
            "iwe2",
            *args,
        ],
        check=True,
        text=True,
        capture_output=True,
        env=iwe2_env(),
    )


def run_iwe(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["iwe", *args], cwd=cwd, check=True, text=True, capture_output=True)


def iwe2_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PATH"] = f"{ZK_BIN_DIR}:{env['PATH']}"
    return env


def tree_keys(cwd: Path, key: str) -> list[str]:
    result = run_iwe(cwd, "tree", "-k", key, "-f", "keys")
    return [line.lstrip("\t") for line in result.stdout.splitlines()]


def inspect_tree_keys(node: JsonObject) -> set[str]:
    key = json_string(node["key"])
    children = json_array(node["children"])
    keys = {key}
    for child in children:
        keys.update(inspect_tree_keys(json_object(child)))
    return keys


def assert_tree(root: str, expected_children: list[str], actual_keys: list[str]) -> None:
    assert actual_keys[0] == root
    assert set(actual_keys[1:]) == set(expected_children)


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


def load_project_config(repo: Path) -> dict[str, object]:
    return tomllib.loads((repo / ".agent-memory.toml").read_text())


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

    result = run_iwe2(tmp_path, "maintain", "init-global", "--vault", str(vault))
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
    assert git_commit_subjects(vault) == ["Initialize iwe2 vault"]
    assert status_lines == set()
    assert ".gitignore" in tracked_files
    assert ".zk/config.toml" in tracked_files
    assert ".zk/templates/default.md" in tracked_files
    assert ".zk/notebook.db" not in tracked_files
    assert "index.md" in tracked_files
    assert (vault / ".iwe" / "config.toml").is_file()
    assert (vault / "index.md").is_file()
    assert (vault / "global" / "index.md").is_file()
    assert (vault / "_meta" / "projects.toml").is_file()
    assert frontmatter(vault / "index.md") == {"okf_version": "0.1"}
    assert "* [Global](global/index.md) - Global memory shared across projects." in (vault / "index.md").read_text()
    global_index = (vault / "global" / "index.md").read_text()
    assert frontmatter(vault / "global" / "index.md") == {"okf_version": "0.1"}
    assert "* [Advice](advice/index.md) - Global advice memories." in global_index
    assert "* [Traps](traps/index.md) - Global traps memories." in global_index
    assert_tree("global/index", GLOBAL_GRAPH_KEYS[1:], tree_keys(vault, "global/index"))


def test_module_entrypoint_initializes_iwe_backed_vault(tmp_path: Path) -> None:
    vault = tmp_path / "module-vault"

    result = run_iwe2_module(tmp_path, "maintain", "init-global", "--vault", str(vault))
    payload = parse_json_stdout(result)

    assert Path(str(payload["vault"])) == vault
    assert (vault / ".iwe" / "config.toml").is_file()
    assert_tree("global/index", GLOBAL_GRAPH_KEYS[1:], tree_keys(vault, "global/index"))


def test_project_memory_crud_and_search_cross_real_scopes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    project_id = init_git_repo(repo)
    vault = tmp_path / "vault"
    run_iwe2(tmp_path, "maintain", "init-global", "--vault", str(vault))
    run_iwe2(repo, "init", "project", "--vault", str(vault))

    config = load_project_config(repo)
    assert config == {
        "vault": str(vault),
        "project_id": project_id,
        "project_root_strategy": "git-root",
        "global_scopes": [
            "global/decisions",
            "global/traps",
            "global/advice",
            "global/context",
            "global/references",
        ],
        "search_max_results": 10,
        "search_max_tokens": 4000,
    }
    agents_pointer = (repo / "AGENTS.md").read_text()
    assert f"This repository uses the central agent memory vault at `{vault}`." in agents_pointer
    assert f"Project memory key: `projects/{project_id}/index`." in agents_pointer
    assert 'iwe2 search --scope both "<task or subsystem>"' in agents_pointer
    expected_project_tree = [
        f"projects/{project_id}/index",
        *[f"projects/{project_id}/{child}" for child in PROJECT_GRAPH_CHILDREN],
    ]
    assert_tree(
        expected_project_tree[0],
        expected_project_tree[1:],
        tree_keys(vault, f"projects/{project_id}/index"),
    )
    project_index = (vault / "projects" / project_id / "index.md").read_text()
    assert frontmatter(vault / "projects" / project_id / "index.md") == {"okf_version": "0.1"}
    assert "* [Decisions](decisions/index.md) - Project decision memories." in project_index
    assert f"* [{project_id}](projects/{project_id}/index.md) - Project memory bundle." in (vault / "index.md").read_text()

    project_note = parse_json_stdout(
        run_iwe2(
            repo,
            "add",
            "--scope",
            "project",
            "--type",
            "decision",
            "--title",
            "Project Alpha",
            "--content",
            "project-signal-7dcbd96d belongs only to this repository",
        )
    )
    global_note = parse_json_stdout(
        run_iwe2(
            repo,
            "add",
            "--scope",
            "global",
            "--type",
            "advice",
            "--title",
            "Global Beta",
            "--content",
            "global-signal-cde4b9f6 belongs to shared agent practice",
        )
    )

    project_path = Path(str(project_note["path"]))
    global_path = Path(str(global_note["path"]))
    assert project_path == vault / "projects" / project_id / "decisions" / "project-alpha.md"
    assert global_path == vault / "global" / "advice" / "global-beta.md"
    assert git_status_lines(vault) == set()
    assert git_commit_subjects(vault)[:4] == [
        "Record global advice memory: Global Beta",
        "Record project decision memory: Project Alpha",
        f"Register project {project_id}",
        "Initialize iwe2 vault",
    ]
    assert_okf_concept_metadata(
        frontmatter(project_path),
        memory_type="decision",
        title="Project Alpha",
        description="project-signal-7dcbd96d belongs only to this repository",
        tags=["project", "decision"],
    )
    assert frontmatter(project_path)["scope"] == "project"
    assert frontmatter(project_path)["project_id"] == project_id
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

    project_key = f"projects/{project_id}/decisions/project-alpha"
    project_search = parse_json_stdout(run_iwe2(repo, "search", "--scope", "project", "signal"))
    assert project_key in result_keys(project_search)
    assert "global/advice/global-beta" not in result_keys(project_search)

    global_search = parse_json_stdout(run_iwe2(repo, "search", "--scope", "global", "signal"))
    assert "global/advice/global-beta" in result_keys(global_search)
    assert project_key not in result_keys(global_search)

    combined_search = parse_json_stdout(run_iwe2(repo, "search", "--scope", "both", "signal"))
    assert project_key in result_keys(combined_search)
    assert "global/advice/global-beta" in result_keys(combined_search)

    retrieved = run_iwe2(repo, "retrieve", str(project_note["key"]))
    assert "project-signal-7dcbd96d belongs only to this repository" in retrieved.stdout

    updated = parse_json_stdout(
        run_iwe2(
            repo,
            "update",
            str(project_note["key"]),
            "--content",
            "project-signal-7dcbd96d updated with durable next step",
        )
    )
    assert updated["key"] == project_note["key"]
    assert "durable next step" in run_iwe2(repo, "retrieve", str(project_note["key"])).stdout

    deleted = parse_json_stdout(run_iwe2(repo, "delete", str(global_note["key"])))
    assert deleted["deleted"] == global_note["key"]
    after_delete = parse_json_stdout(run_iwe2(repo, "search", "--scope", "both", "global-signal-cde4b9f6"))
    assert "global/advice/global-beta" not in result_keys(after_delete)


def test_search_keys_uses_scoped_title_key_matches(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    project_id = init_git_repo(repo)
    vault = tmp_path / "vault"
    run_iwe2(tmp_path, "maintain", "init-global", "--vault", str(vault))
    run_iwe2(repo, "init", "project", "--vault", str(vault))

    project_note = parse_json_stdout(
        run_iwe2(
            repo,
            "add",
            "--scope",
            "project",
            "--type",
            "decision",
            "--title",
            "Project Graph Beacon",
            "--content",
            "body-only-project-content-9c0f8c2e",
        )
    )
    global_note = parse_json_stdout(
        run_iwe2(
            repo,
            "add",
            "--scope",
            "global",
            "--type",
            "advice",
            "--title",
            "Global Graph Beacon",
            "--content",
            "body-only-global-content-6f3cbdb4",
        )
    )
    project_key = str(project_note["key"])
    global_key = str(global_note["key"])
    assert project_key == f"projects/{project_id}/decisions/project-graph-beacon"
    assert global_key == "global/advice/global-graph-beacon"

    project_search = parse_json_stdout(run_iwe2(repo, "search", "keys", "--scope", "project", "GB"))
    assert result_keys(project_search) == {project_key}

    global_search = parse_json_stdout(run_iwe2(repo, "search", "keys", "--scope", "global", "GB"))
    assert result_keys(global_search) == {global_key}

    combined_search = parse_json_stdout(run_iwe2(repo, "search", "keys", "--scope", "both", "GB"))
    assert result_keys(combined_search) == {project_key, global_key}


def test_search_content_ranked_uses_scope_roots(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    vault = tmp_path / "vault"
    run_iwe2(tmp_path, "maintain", "init-global", "--vault", str(vault))
    run_iwe2(repo, "init", "project", "--vault", str(vault))

    project_note = parse_json_stdout(
        run_iwe2(
            repo,
            "add",
            "--scope",
            "project",
            "--type",
            "decision",
            "--title",
            "Probe Project Context",
            "--content",
            "ranked-context-token-48a4 project-only probe evidence",
        )
    )
    global_note = parse_json_stdout(
        run_iwe2(
            repo,
            "add",
            "--scope",
            "global",
            "--type",
            "advice",
            "--title",
            "Probe Global Context",
            "--content",
            "ranked-context-token-48a4 global-only probe evidence",
        )
    )

    project_search = parse_json_stdout(
        run_iwe2(
            repo,
            "search",
            "content",
            "--scope",
            "project",
            "--mode",
            "ranked",
            "ranked-context-token-48a4",
        )
    )
    project_files = probe_result_files(project_search)
    assert Path(str(project_note["path"])).resolve() in project_files
    assert Path(str(global_note["path"])).resolve() not in project_files

    global_search = parse_json_stdout(
        run_iwe2(
            repo,
            "search",
            "content",
            "--scope",
            "global",
            "--mode",
            "ranked",
            "ranked-context-token-48a4",
        )
    )
    global_files = probe_result_files(global_search)
    assert Path(str(global_note["path"])).resolve() in global_files
    assert Path(str(project_note["path"])).resolve() not in global_files

    combined_search = parse_json_stdout(
        run_iwe2(
            repo,
            "search",
            "content",
            "--scope",
            "both",
            "--mode",
            "ranked",
            "ranked-context-token-48a4",
        )
    )
    combined_files = probe_result_files(combined_search)
    assert Path(str(project_note["path"])).resolve() in combined_files
    assert Path(str(global_note["path"])).resolve() in combined_files


def test_search_content_fuzzy_uses_scope_roots(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    project_id = init_git_repo(repo)
    vault = tmp_path / "vault"
    run_iwe2(tmp_path, "maintain", "init-global", "--vault", str(vault))
    run_iwe2(repo, "init", "project", "--vault", str(vault))

    project_note = parse_json_stdout(
        run_iwe2(
            repo,
            "add",
            "--scope",
            "project",
            "--type",
            "decision",
            "--title",
            "Fuzzy Project Context",
            "--content",
            "fuzzy-search-token-3b9a project-only indexed evidence",
        )
    )
    global_note = parse_json_stdout(
        run_iwe2(
            repo,
            "add",
            "--scope",
            "global",
            "--type",
            "advice",
            "--title",
            "Fuzzy Global Context",
            "--content",
            "fuzzy-search-token-3b9a global-only indexed evidence",
        )
    )
    unrelated_project_note = parse_json_stdout(
        run_iwe2(
            repo,
            "add",
            "--scope",
            "project",
            "--type",
            "decision",
            "--title",
            "Fuzzy Project Irrelevant",
            "--content",
            "different indexed evidence",
        )
    )

    project_key = f"projects/{project_id}/decisions/fuzzy-project-context"
    global_key = "global/advice/fuzzy-global-context"
    unrelated_project_key = f"projects/{project_id}/decisions/fuzzy-project-irrelevant"
    assert project_note["key"] == project_key
    assert global_note["key"] == global_key
    assert unrelated_project_note["key"] == unrelated_project_key

    project_search = parse_json_stdout(
        run_iwe2(
            repo,
            "search",
            "content",
            "--scope",
            "project",
            "--mode",
            "fuzzy",
            "fuzzy-search-token-3b9a",
        )
    )
    project_keys = result_keys(project_search)
    assert project_key in project_keys
    assert global_key not in project_keys
    assert unrelated_project_key not in project_keys
    assert all(key.startswith(f"projects/{project_id}/") for key in project_keys)

    global_search = parse_json_stdout(
        run_iwe2(
            repo,
            "search",
            "content",
            "--scope",
            "global",
            "--mode",
            "fuzzy",
            "fuzzy-search-token-3b9a",
        )
    )
    global_keys = result_keys(global_search)
    assert global_key in global_keys
    assert project_key not in global_keys
    assert unrelated_project_key not in global_keys
    assert all(key.startswith("global/") for key in global_keys)

    combined_search = parse_json_stdout(
        run_iwe2(
            repo,
            "search",
            "content",
            "--scope",
            "both",
            "--mode",
            "fuzzy",
            "fuzzy-search-token-3b9a",
        )
    )
    combined_keys = result_keys(combined_search)
    assert project_key in combined_keys
    assert global_key in combined_keys
    assert unrelated_project_key not in combined_keys


def test_search_metadata_filters_real_frontmatter(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    project_id = init_git_repo(repo)
    vault = tmp_path / "vault"
    run_iwe2(tmp_path, "maintain", "init-global", "--vault", str(vault))
    run_iwe2(repo, "init", "project", "--vault", str(vault))

    project_note = parse_json_stdout(
        run_iwe2(
            repo,
            "add",
            "--scope",
            "project",
            "--type",
            "decision",
            "--title",
            "Metadata Project",
            "--content",
            "metadata project body",
        )
    )
    global_note = parse_json_stdout(
        run_iwe2(
            repo,
            "add",
            "--scope",
            "global",
            "--type",
            "trap",
            "--title",
            "Metadata Global",
            "--content",
            "metadata global body",
        )
    )

    project_key = f"projects/{project_id}/decisions/metadata-project"
    global_key = "global/traps/metadata-global"
    assert project_note["key"] == project_key
    assert global_note["key"] == global_key

    project_results = parse_json_stdout(
        run_iwe2(
            repo,
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
        run_iwe2(
            repo,
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


def test_maintain_squash_consolidates_project_graph_with_iwe(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    project_id = init_git_repo(repo)
    vault = tmp_path / "vault"
    run_iwe2(tmp_path, "maintain", "init-global", "--vault", str(vault))
    run_iwe2(repo, "init", "project", "--vault", str(vault))
    run_iwe2(
        repo,
        "add",
        "--scope",
        "project",
        "--type",
        "decision",
        "--title",
        "Squash Project Signal",
        "--content",
        "squash-project-signal-2d4f1c7a must appear in project consolidation",
    )
    run_iwe2(
        repo,
        "add",
        "--scope",
        "global",
        "--type",
        "advice",
        "--title",
        "Squash Global Signal",
        "--content",
        "squash-global-signal-88ec672b must stay outside project consolidation",
    )

    squashed = run_iwe2(repo, "maintain", "squash", f"projects/{project_id}/index", "--depth", "3")

    assert "# Squash Project Signal" in squashed.stdout
    assert "squash-project-signal-2d4f1c7a must appear in project consolidation" in squashed.stdout
    assert "squash-global-signal-88ec672b" not in squashed.stdout


def test_init_project_replaces_existing_agents_memory_pointer(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    project_id = init_git_repo(repo)
    (repo / "AGENTS.md").write_text(
        "# Existing repo instructions\n\n"
        "Preserve local setup rules before memory.\n\n"
        "<!-- iwe2:agent-memory:start -->\n"
        "stale vault: /tmp/not-the-current-vault\n"
        "<!-- iwe2:agent-memory:end -->\n\n"
        "Preserve local setup rules after memory.\n",
        encoding="utf-8",
    )
    vault = tmp_path / "vault"
    run_iwe2(tmp_path, "maintain", "init-global", "--vault", str(vault))

    run_iwe2(repo, "init", "project", "--vault", str(vault))

    agents_pointer = (repo / "AGENTS.md").read_text(encoding="utf-8")
    assert "Preserve local setup rules before memory." in agents_pointer
    assert "Preserve local setup rules after memory." in agents_pointer
    assert "/tmp/not-the-current-vault" not in agents_pointer
    assert agents_pointer.count("<!-- iwe2:agent-memory:start -->") == 1
    assert agents_pointer.count("<!-- iwe2:agent-memory:end -->") == 1
    assert f"This repository uses the central agent memory vault at `{vault}`." in agents_pointer
    assert f"Project memory key: `projects/{project_id}/index`." in agents_pointer


def test_init_project_appends_agents_memory_pointer_to_unmarked_agents(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    project_id = init_git_repo(repo)
    (repo / "AGENTS.md").write_text(
        "# Existing repo instructions\n\nPreserve instructions that are not managed by iwe2.\n",
        encoding="utf-8",
    )
    vault = tmp_path / "vault"
    run_iwe2(tmp_path, "maintain", "init-global", "--vault", str(vault))

    run_iwe2(repo, "init", "project", "--vault", str(vault))

    agents_pointer = (repo / "AGENTS.md").read_text(encoding="utf-8")
    assert "Preserve instructions that are not managed by iwe2." in agents_pointer
    assert agents_pointer.count("<!-- iwe2:agent-memory:start -->") == 1
    assert agents_pointer.count("<!-- iwe2:agent-memory:end -->") == 1
    assert f"This repository uses the central agent memory vault at `{vault}`." in agents_pointer
    assert f"Project memory key: `projects/{project_id}/index`." in agents_pointer


def test_maintain_move_memory_to_global_leaves_project_pointer(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    project_id = init_git_repo(repo)
    vault = tmp_path / "vault"
    run_iwe2(tmp_path, "maintain", "init-global", "--vault", str(vault))
    run_iwe2(repo, "init", "project", "--vault", str(vault))
    note = parse_json_stdout(
        run_iwe2(
            repo,
            "add",
            "--scope",
            "project",
            "--type",
            "trap",
            "--title",
            "Promotion Trap",
            "--content",
            "promote-signal-f88f0a72 must become shared knowledge",
        )
    )

    moved = parse_json_stdout(run_iwe2(repo, "maintain", "move", str(note["key"]), "--to", "global/traps"))

    destination = Path(str(moved["path"]))
    pointer = Path(str(note["path"]))
    assert destination == vault / "global" / "traps" / "promotion-trap.md"
    assert pointer == vault / "projects" / project_id / "traps" / "promotion-trap.md"
    assert "promote-signal-f88f0a72 must become shared knowledge" in destination.read_text()
    assert_okf_concept_metadata(
        frontmatter(destination),
        memory_type="trap",
        title="Promotion Trap",
        description="promote-signal-f88f0a72 must become shared knowledge",
        tags=["global", "trap", "promoted"],
    )
    assert frontmatter(destination)["origin_project_id"] == project_id
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

    result = run_iwe2_unchecked(
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

    assert result.returncode == 2
    assert "No project memory config found" in result.stderr
    assert "iwe2 init project --vault" in result.stderr
    assert "Traceback" not in result.stderr


def test_doctor_reports_declared_project_contract(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    project_id = init_git_repo(repo)
    vault = tmp_path / "vault"
    run_iwe2(tmp_path, "maintain", "init-global", "--vault", str(vault))
    run_iwe2(repo, "init", "project", "--vault", str(vault))

    doctor = parse_json_stdout(run_iwe2(repo, "doctor"))

    assert doctor["vault"] == str(vault)
    assert doctor["project_id"] == project_id
    assert doctor["project_root"] == str(repo)
    assert doctor["tools"] == ["git", "iwe", "rg", "npx", "@probelabs/probe", "zk"]


def test_inspect_overview_schema_paths_and_tree_map_real_vault(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    project_id = init_git_repo(repo)
    vault = tmp_path / "vault"
    run_iwe2(tmp_path, "maintain", "init-global", "--vault", str(vault))
    run_iwe2(repo, "init", "project", "--vault", str(vault))

    project_note = parse_json_stdout(
        run_iwe2(
            repo,
            "add",
            "--scope",
            "project",
            "--type",
            "decision",
            "--title",
            "Inspect Project",
            "--content",
            "Project navigation memory.",
        )
    )
    global_note = parse_json_stdout(
        run_iwe2(
            repo,
            "add",
            "--scope",
            "global",
            "--type",
            "advice",
            "--title",
            "Inspect Global",
            "--content",
            "Global navigation memory.",
        )
    )
    project_key = f"projects/{project_id}/decisions/inspect-project"
    global_key = "global/advice/inspect-global"
    assert project_note["key"] == project_key
    assert global_note["key"] == global_key

    overview = parse_json_stdout(run_iwe2(repo, "inspect", "overview", "--scope", "both", "--format", "json"))
    assert overview["vault"] == str(vault)
    assert overview["project_id"] == project_id
    assert overview["scope"] == "both"
    assert overview["roots"] == ["global/index", f"projects/{project_id}/index"]
    assert overview["totals"] == {"notes": 2, "indexes": 12}
    assert overview["notes_by_scope"] == {"global": 1, "project": 1}
    assert overview["notes_by_type"] == {"advice": 1, "decision": 1}

    schema = parse_json_stdout(run_iwe2(repo, "inspect", "schema", "--format", "json"))
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
    assert schema["memory_types"] == [
        "decision",
        "trap",
        "advice",
        "context",
        "reference",
    ]

    paths = parse_json_stdout(
        run_iwe2(
            repo,
            "inspect",
            "paths",
            "--scope",
            "project",
            "--kind",
            "notes",
            "--format",
            "json",
        )
    )
    assert paths["scope"] == "project"
    assert paths["kind"] == "notes"
    assert paths["paths"] == [
        {
            "key": project_key,
            "path": str(vault / "projects" / project_id / "decisions" / "inspect-project.md"),
            "title": "Inspect Project",
            "type": "decision",
            "scope": "project",
        }
    ]

    tree = parse_json_stdout(
        run_iwe2(
            repo,
            "inspect",
            "tree",
            "--scope",
            "project",
            "--depth",
            "2",
            "--format",
            "json",
        )
    )
    tree_roots = json_array(tree["roots"])
    tree_root = json_object(tree_roots[0])
    assert tree["scope"] == "project"
    assert tree_root["key"] == f"projects/{project_id}/index"
    assert inspect_tree_keys(tree_root) == {
        f"projects/{project_id}/index",
        f"projects/{project_id}/advice/index",
        f"projects/{project_id}/context/index",
        f"projects/{project_id}/decisions/index",
        project_key,
        f"projects/{project_id}/references/index",
        f"projects/{project_id}/traps/index",
    }


def test_inspect_links_outline_recent_stats_and_export_real_vault(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    project_id = init_git_repo(repo)
    vault = tmp_path / "vault"
    run_iwe2(tmp_path, "maintain", "init-global", "--vault", str(vault))
    run_iwe2(repo, "init", "project", "--vault", str(vault))

    project_note = parse_json_stdout(
        run_iwe2(
            repo,
            "add",
            "--scope",
            "project",
            "--type",
            "decision",
            "--title",
            "Inspect Linked",
            "--content",
            "## Investigation\nUse outline and graph inspection.",
        )
    )
    global_note = parse_json_stdout(
        run_iwe2(
            repo,
            "add",
            "--scope",
            "global",
            "--type",
            "advice",
            "--title",
            "Inspect Advice",
            "--content",
            "Use read-only inspection before maintenance.",
        )
    )
    project_key = f"projects/{project_id}/decisions/inspect-linked"
    global_key = "global/advice/inspect-advice"
    assert project_note["key"] == project_key
    assert global_note["key"] == global_key

    parents = parse_json_stdout(
        run_iwe2(
            repo,
            "inspect",
            "links",
            project_key,
            "--direction",
            "parents",
            "--depth",
            "1",
            "--format",
            "json",
        )
    )
    assert parents["links"] == [
        {
            "key": f"projects/{project_id}/decisions/index",
            "path": str(vault / "projects" / project_id / "decisions" / "index.md"),
            "title": "Decisions",
            "depth": 1,
        }
    ]

    outline = parse_json_stdout(run_iwe2(repo, "inspect", "outline", project_key, "--format", "json"))
    outline_headings = [json_object(heading) for heading in json_array(outline["headings"])]
    assert [(json_string(heading["title"]), heading["level"]) for heading in outline_headings] == [
        ("Inspect Linked", 1),
        ("Investigation", 2),
    ]

    recent = parse_json_stdout(
        run_iwe2(
            repo,
            "inspect",
            "recent",
            "--scope",
            "project",
            "--since",
            "1970-01-01T00:00:00+00:00",
            "--format",
            "json",
        )
    )
    assert result_keys(recent) == {project_key}

    stats = parse_json_stdout(
        run_iwe2(
            repo,
            "inspect",
            "stats",
            "--scope",
            "both",
            "--by",
            "type",
            "--format",
            "json",
        )
    )
    assert stats["counts"] == {"advice": 1, "decision": 1}

    exported = parse_json_stdout(
        run_iwe2(
            repo,
            "inspect",
            "export",
            "--scope",
            "project",
            "--profile",
            "map",
            "--format",
            "graph-json",
        )
    )
    assert exported["profile"] == "map"
    exported_nodes = {json_string(node["key"]): node for node in (json_object(node) for node in json_array(exported["nodes"]))}
    assert exported_nodes[project_key]["title"] == "Inspect Linked"
    assert "content" not in exported_nodes[project_key]
    exported_edges = [json_object(edge) for edge in json_array(exported["edges"])]
    assert {(json_string(edge["source"]), json_string(edge["target"])) for edge in exported_edges} >= {(f"projects/{project_id}/decisions/index", project_key)}
