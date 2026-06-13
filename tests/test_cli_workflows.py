from __future__ import annotations

import json
import subprocess
import tomllib
from datetime import UTC, datetime
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


def probe_result_files(result: dict[str, object]) -> set[Path]:
    records = result["results"]
    assert isinstance(records, list)
    files: set[Path] = set()
    for record in records:
        assert isinstance(record, dict)
        file_value = record["file"]
        assert isinstance(file_value, str)
        files.add(Path(file_value).resolve())
    return files


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


def test_vault_init_creates_iwe_backed_layout(tmp_path: Path) -> None:
    vault = tmp_path / "vault"

    result = run_iwe2(tmp_path, "vault", "init", str(vault))
    payload = parse_json_stdout(result)
    git_probe = subprocess.run(
        ["git", "-C", str(vault), "rev-parse", "--is-inside-work-tree"],
        check=True,
        text=True,
        capture_output=True,
    )

    assert Path(str(payload["vault"])) == vault
    assert git_probe.stdout.strip() == "true"
    assert (vault / ".iwe" / "config.toml").is_file()
    assert (vault / "index.md").is_file()
    assert (vault / "global" / "index.md").is_file()
    assert (vault / "_meta" / "projects.toml").is_file()
    assert frontmatter(vault / "index.md") == {"okf_version": "0.1"}
    assert "* [Global](global/index.md) - Global memory shared across projects." in (vault / "index.md").read_text()
    global_index = (vault / "global" / "index.md").read_text()
    assert not global_index.startswith("---\n")
    assert "* [Advice](advice/index.md) - Global advice memories." in global_index
    assert "* [Traps](traps/index.md) - Global traps memories." in global_index
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
    assert not project_index.startswith("---\n")
    assert "* [Decisions](decisions/index.md) - Project decision memories." in project_index
    assert f"* [{project_id}](projects/{project_id}/index.md) - Project memory bundle." in (vault / "index.md").read_text()

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


def test_search_uses_iwe_graph_filters_for_title_matches(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    project_id = init_git_repo(repo)
    vault = tmp_path / "vault"
    run_iwe2(tmp_path, "vault", "init", str(vault))
    run_iwe2(repo, "project", "init", "--vault", str(vault))

    project_note = parse_json_stdout(
        run_iwe2(
            repo,
            "note",
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
            "note",
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

    project_search = run_iwe2(repo, "search", "--scope", "project", "GB")
    assert project_key in project_search.stdout
    assert global_key not in project_search.stdout

    global_search = run_iwe2(repo, "search", "--scope", "global", "GB")
    assert global_key in global_search.stdout
    assert project_key not in global_search.stdout

    combined_search = run_iwe2(repo, "search", "--scope", "both", "GB")
    assert project_key in combined_search.stdout
    assert global_key in combined_search.stdout


def test_search_context_uses_probe_with_scope_roots(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    vault = tmp_path / "vault"
    run_iwe2(tmp_path, "vault", "init", str(vault))
    run_iwe2(repo, "project", "init", "--vault", str(vault))

    project_note = parse_json_stdout(
        run_iwe2(
            repo,
            "note",
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
            "note",
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
            "search-context",
            "--scope",
            "project",
            "--max-results",
            "5",
            "--max-tokens",
            "2000",
            "ranked-context-token-48a4",
        )
    )
    project_files = probe_result_files(project_search)
    assert Path(str(project_note["path"])).resolve() in project_files
    assert Path(str(global_note["path"])).resolve() not in project_files

    global_search = parse_json_stdout(
        run_iwe2(
            repo,
            "search-context",
            "--scope",
            "global",
            "--max-results",
            "5",
            "--max-tokens",
            "2000",
            "ranked-context-token-48a4",
        )
    )
    global_files = probe_result_files(global_search)
    assert Path(str(global_note["path"])).resolve() in global_files
    assert Path(str(project_note["path"])).resolve() not in global_files

    combined_search = parse_json_stdout(
        run_iwe2(
            repo,
            "search-context",
            "--scope",
            "both",
            "--max-results",
            "20",
            "--max-tokens",
            "4000",
            "ranked-context-token-48a4",
        )
    )
    combined_files = probe_result_files(combined_search)
    assert Path(str(project_note["path"])).resolve() in combined_files
    assert Path(str(global_note["path"])).resolve() in combined_files


def test_squash_consolidates_project_graph_with_iwe(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    project_id = init_git_repo(repo)
    vault = tmp_path / "vault"
    run_iwe2(tmp_path, "vault", "init", str(vault))
    run_iwe2(repo, "project", "init", "--vault", str(vault))
    run_iwe2(
        repo,
        "note",
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
        "note",
        "--scope",
        "global",
        "--type",
        "advice",
        "--title",
        "Squash Global Signal",
        "--content",
        "squash-global-signal-88ec672b must stay outside project consolidation",
    )

    squashed = run_iwe2(repo, "squash", f"projects/{project_id}/index", "--depth", "3")

    assert "# Squash Project Signal" in squashed.stdout
    assert "squash-project-signal-2d4f1c7a must appear in project consolidation" in squashed.stdout
    assert "squash-global-signal-88ec672b" not in squashed.stdout


def test_project_init_replaces_existing_agents_memory_pointer(tmp_path: Path) -> None:
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
    run_iwe2(tmp_path, "vault", "init", str(vault))

    run_iwe2(repo, "project", "init", "--vault", str(vault))

    agents_pointer = (repo / "AGENTS.md").read_text(encoding="utf-8")
    assert "Preserve local setup rules before memory." in agents_pointer
    assert "Preserve local setup rules after memory." in agents_pointer
    assert "/tmp/not-the-current-vault" not in agents_pointer
    assert agents_pointer.count("<!-- iwe2:agent-memory:start -->") == 1
    assert agents_pointer.count("<!-- iwe2:agent-memory:end -->") == 1
    assert f"This repository uses the central agent memory vault at `{vault}`." in agents_pointer
    assert f"Project memory key: `projects/{project_id}/index`." in agents_pointer


def test_project_init_appends_agents_memory_pointer_to_unmarked_agents(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    project_id = init_git_repo(repo)
    (repo / "AGENTS.md").write_text(
        "# Existing repo instructions\n\nPreserve instructions that are not managed by iwe2.\n",
        encoding="utf-8",
    )
    vault = tmp_path / "vault"
    run_iwe2(tmp_path, "vault", "init", str(vault))

    run_iwe2(repo, "project", "init", "--vault", str(vault))

    agents_pointer = (repo / "AGENTS.md").read_text(encoding="utf-8")
    assert "Preserve instructions that are not managed by iwe2." in agents_pointer
    assert agents_pointer.count("<!-- iwe2:agent-memory:start -->") == 1
    assert agents_pointer.count("<!-- iwe2:agent-memory:end -->") == 1
    assert f"This repository uses the central agent memory vault at `{vault}`." in agents_pointer
    assert f"Project memory key: `projects/{project_id}/index`." in agents_pointer


def test_promote_moves_memory_to_global_and_leaves_project_pointer(
    tmp_path: Path,
) -> None:
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
    assert doctor["tools"] == ["git", "iwe", "rg", "npx", "@probelabs/probe"]
