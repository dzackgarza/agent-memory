from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from iwe2.cards import CardSystemConfig, build_card_models, load_card_system_config
from iwe2.cards.migration import migrate_plans
from iwe2.cards.storage import read_card
from iwe2.cards.validation import load_card_records, validate_cards


def models_and_config() -> tuple[CardSystemConfig, dict[str, type[BaseModel]]]:
    config = load_card_system_config()
    return config, build_card_models(config)


def write_card(path: Path, frontmatter: dict[str, Any], body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{yaml.safe_dump(frontmatter, sort_keys=False)}---\n{body}", encoding="utf-8")


def build_nimbalyst_source(source_root: Path) -> None:
    feature_dir = source_root / "features" / "FEATURE-M"
    write_card(
        feature_dir / "FEATURE-M.md",
        {"id": "FEATURE-M", "trackerStatus": {"type": "feature"}, "title": "Migrated feature", "status": "in-progress", "description": "d"},
        "# Migrated feature\n",
    )
    plan_dir = feature_dir / "plans" / "PLAN-M"
    write_card(
        plan_dir / "PLAN-M.md",
        {
            "id": "PLAN-M",
            "trackerStatus": {"type": "plan"},
            "parents": ["[[FEATURE-M]]"],
            "title": "Migrated plan",
            "status": "approved-and-unstarted",
            "description": "d",
            "successCriteria": ["compiles"],
        },
        "# Migrated plan\n",
    )
    phase_dir = plan_dir / "PHASE-M"
    write_card(
        phase_dir / "PHASE-M.md",
        {
            "id": "PHASE-M",
            "trackerStatus": {"type": "phase"},
            "parents": ["[[PLAN-M]]"],
            "title": "Migrated phase",
            "status": "in-progress",
            "description": "d",
            "successCriteria": ["done"],
        },
        "# Migrated phase\n",
    )
    write_card(
        phase_dir / "tasks" / "TASK-M.md",
        {
            "id": "TASK-M",
            "trackerStatus": {"type": "task"},
            "parents": ["[[PHASE-M]]"],
            "title": "Migrated task",
            "status": "complete",
            "description": "d",
            "successCriteria": ["shipped"],
        },
        "# Migrated task\n",
    )


def test_migrate_strips_tracker_status_and_mirrors_hierarchy(tmp_path: Path) -> None:
    config, models = models_and_config()
    source = tmp_path / "repo" / ".agents" / "plans"
    build_nimbalyst_source(source)
    vault = tmp_path / "vault" / "projects" / "proj" / "plans"
    migrated = migrate_plans(source, vault, config, models)
    assert len(migrated) == 4
    assert (vault / "features" / "FEATURE-M" / "plans" / "PLAN-M" / "PHASE-M" / "tasks" / "TASK-M.md").is_file()
    for path in migrated:
        assert "trackerStatus" not in path.read_text(encoding="utf-8")


def test_migrated_tree_validates_clean_and_preserves_fields(tmp_path: Path) -> None:
    config, models = models_and_config()
    source = tmp_path / "repo" / ".agents" / "plans"
    build_nimbalyst_source(source)
    vault = tmp_path / "vault" / "projects" / "proj" / "plans"
    migrate_plans(source, vault, config, models)
    assert validate_cards(load_card_records([vault], config, models), config) == []
    task = read_card(vault, config, models, "TASK-M").model_dump()
    assert task["successCriteria"] == ["shipped"]
    assert task["status"] == "complete"
    assert task["parents"] == ["[[PHASE-M]]"]


def test_migration_skips_plan_dag_and_falls_back_to_id_prefix(tmp_path: Path) -> None:
    # plan-dag.md is a generated artifact and must be skipped; a card lacking
    # trackerStatus must have its type inferred from the id prefix (the else branch).
    config, models = models_and_config()
    source = tmp_path / "repo" / ".agents" / "plans"
    write_card(
        source / "features" / "FEATURE-N" / "FEATURE-N.md",
        {"id": "FEATURE-N", "title": "No tracker feature", "status": "in-progress", "description": "d"},
        "# No tracker feature\n",
    )
    (source / "plans").mkdir(parents=True, exist_ok=True)
    (source / "plans" / "plan-dag.md").write_text("# dag\n", encoding="utf-8")
    vault = tmp_path / "vault" / "projects" / "proj" / "plans"

    migrated = migrate_plans(source, vault, config, models)

    assert all(path.name != "plan-dag.md" for path in migrated)
    assert (vault / "features" / "FEATURE-N" / "FEATURE-N.md").is_file()
