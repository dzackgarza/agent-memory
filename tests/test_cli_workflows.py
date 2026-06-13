from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GLOBAL_GRAPH_KEYS = [
    "global/index",
    "global/advice/index",
    "global/traps/index",
    "global/workflows/index",
    "global/tools/index",
    "global/style/index",
    "global/facts/index",
    "global/conventions/index",
]
PROJECT_GRAPH_CHILDREN = [
    "decisions/index",
    "traps/index",
    "workflows/index",
    "sessions/index",
    "facts/index",
    "advice/index",
    "conventions/index",
]


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
    )


def run_iwe(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["iwe", *args], cwd=cwd, check=True, text=True, capture_output=True)


def tree_keys(cwd: Path, key: str) -> list[str]:
    result = run_iwe(cwd, "tree", "-k", key, "-f", "keys")
    return [line.lstrip("\t") for line in result.stdout.splitlines()]


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


def parse_json_stdout(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    decoded = json.loads(result.stdout)
    assert isinstance(decoded, dict)
    return decoded


def load_project_config(repo: Path) -> dict[str, object]:
    return tomllib.loads((repo / ".agent-memory.toml").read_text())


def frontmatter(markdown: Path) -> dict[str, object]:
    lines = markdown.read_text().splitlines()
    assert lines[0] == "---"
    closing = lines.index("---", 1)
    parsed = yaml.safe_load("\n".join(lines[1:closing]))
    assert isinstance(parsed, dict)
    return parsed


def test_vault_init_creates_iwe_backed_layout(tmp_path: Path) -> None:
    vault = tmp_path / "vault"

    result = run_iwe2(tmp_path, "vault", "init", str(vault))
    payload = parse_json_stdout(result)

    assert Path(str(payload["vault"])) == vault
    assert (vault / ".iwe" / "config.toml").is_file()
    assert (vault / "index.md").is_file()
    assert (vault / "global" / "index.md").is_file()
    assert (vault / "_meta" / "projects.toml").is_file()
    assert_tree("global/index", GLOBAL_GRAPH_KEYS[1:], tree_keys(vault, "global/index"))


def test_module_entrypoint_initializes_iwe_backed_vault(tmp_path: Path) -> None:
    vault = tmp_path / "module-vault"

    result = run_iwe2_module(tmp_path, "vault", "init", str(vault))
    payload = parse_json_stdout(result)

    assert Path(str(payload["vault"])) == vault
    assert (vault / ".iwe" / "config.toml").is_file()
    assert_tree("global/index", GLOBAL_GRAPH_KEYS[1:], tree_keys(vault, "global/index"))


def test_project_note_search_and_retrieve_cross_real_scopes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    project_id = init_git_repo(repo)
    vault = tmp_path / "vault"
    run_iwe2(tmp_path, "vault", "init", str(vault))
    run_iwe2(repo, "project", "init", "--vault", str(vault))

    config = load_project_config(repo)
    assert config == {
        "vault": str(vault),
        "project_id": project_id,
        "project_root_strategy": "git-root",
        "global_scopes": [
            "global/advice",
            "global/traps",
            "global/workflows",
            "global/tools",
        ],
    }
    expected_project_tree = [
        f"projects/{project_id}/index",
        *[f"projects/{project_id}/{child}" for child in PROJECT_GRAPH_CHILDREN],
    ]
    assert_tree(
        expected_project_tree[0],
        expected_project_tree[1:],
        tree_keys(vault, f"projects/{project_id}/index"),
    )

    project_note = parse_json_stdout(
        run_iwe2(
            repo,
            "note",
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
            "note",
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
    assert frontmatter(project_path)["scope"] == "project"
    assert frontmatter(global_path)["scope"] == "global"

    project_search = run_iwe2(repo, "search", "--scope", "project", "signal")
    assert "project-signal-7dcbd96d" in project_search.stdout
    assert "global-signal-cde4b9f6" not in project_search.stdout

    global_search = run_iwe2(repo, "search", "--scope", "global", "signal")
    assert "global-signal-cde4b9f6" in global_search.stdout
    assert "project-signal-7dcbd96d" not in global_search.stdout

    combined_search = run_iwe2(repo, "search", "--scope", "both", "signal")
    assert "project-signal-7dcbd96d" in combined_search.stdout
    assert "global-signal-cde4b9f6" in combined_search.stdout

    retrieved = run_iwe2(repo, "retrieve", str(project_note["key"]))
    assert "project-signal-7dcbd96d belongs only to this repository" in retrieved.stdout


def test_promote_moves_memory_to_global_and_leaves_project_pointer(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    project_id = init_git_repo(repo)
    vault = tmp_path / "vault"
    run_iwe2(tmp_path, "vault", "init", str(vault))
    run_iwe2(repo, "project", "init", "--vault", str(vault))
    note = parse_json_stdout(
        run_iwe2(
            repo,
            "note",
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

    promoted = parse_json_stdout(run_iwe2(repo, "promote", str(note["key"]), "--to", "global/traps"))

    destination = Path(str(promoted["path"]))
    pointer = Path(str(note["path"]))
    assert destination == vault / "global" / "traps" / "promotion-trap.md"
    assert pointer == vault / "projects" / project_id / "traps" / "promotion-trap.md"
    assert "promote-signal-f88f0a72 must become shared knowledge" in destination.read_text()
    assert frontmatter(destination)["origin_project_id"] == project_id
    assert frontmatter(destination)["scope"] == "global"
    assert "global/traps/promotion-trap" in pointer.read_text()


def test_doctor_reports_declared_project_contract(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    project_id = init_git_repo(repo)
    vault = tmp_path / "vault"
    run_iwe2(tmp_path, "vault", "init", str(vault))
    run_iwe2(repo, "project", "init", "--vault", str(vault))

    doctor = parse_json_stdout(run_iwe2(repo, "doctor"))

    assert doctor["vault"] == str(vault)
    assert doctor["project_id"] == project_id
    assert doctor["project_root"] == str(repo)
    assert doctor["tools"] == ["git", "iwe", "rg"]
