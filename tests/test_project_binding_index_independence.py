"""Project registration remains usable while the shared search index is busy."""

import sqlite3
from pathlib import Path

from test_cli_workflows import (
    initialized_git_repo,
    parse_json_stdout,
    run_agent_memory,
    run_agent_memory_subprocess,
)


def test_binding_completes_while_shared_search_index_is_locked(tmp_path: Path) -> None:
    repository = initialized_git_repo(tmp_path)
    vault = tmp_path / "vault"
    run_agent_memory(tmp_path, "maintain", "init-global", "--vault", str(vault))

    with sqlite3.connect(vault / ".zk" / "notebook.db") as index:
        index.execute("BEGIN EXCLUSIVE")
        result = run_agent_memory_subprocess(repository.path, "init", "project", "--vault", str(vault))
        assert result.returncode == 0, result.stderr
        binding = parse_json_stdout(result)
        assert binding["project_id"] == repository.project_id
        assert binding["project_root"] == str(repository.path)

    key = f"projects/{repository.project_id}/index"
    retrieved = run_agent_memory(repository.path, "retrieve", key)
    assert f"````markdown #{key}\n" in retrieved.stdout
