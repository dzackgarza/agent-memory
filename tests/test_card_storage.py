from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from agent_memory.cards import CardSystemConfig, build_card_models, load_card_system_config
from agent_memory.cards.storage import create_card, delete_card, find_card_path, read_card, update_card


def make_models() -> tuple[CardSystemConfig, dict[str, type[BaseModel]]]:
    config = load_card_system_config()
    return config, build_card_models(config)


def create_feature_plan_phase_task(plans_root: Path) -> dict[str, Path]:
    config, models = make_models()
    feature = create_card(
        plans_root,
        config,
        models,
        type_name="feature",
        card_id="FEATURE-DEMO",
        parent_id=None,
        fields={"title": "Demo feature", "status": "in-progress", "description": "demo"},
        body="# Demo feature\n",
    )
    plan = create_card(
        plans_root,
        config,
        models,
        type_name="plan",
        card_id="PLAN-DEMO",
        parent_id="FEATURE-DEMO",
        fields={"title": "Demo plan", "status": "approved-and-unstarted", "description": "demo", "parents": ["[[FEATURE-DEMO]]"], "successCriteria": ["plan compiles"]},
        body="# Demo plan\n",
    )
    phase = create_card(
        plans_root,
        config,
        models,
        type_name="phase",
        card_id="PHASE-DEMO",
        parent_id="PLAN-DEMO",
        fields={"title": "Demo phase", "status": "in-progress", "description": "demo", "parents": ["[[PLAN-DEMO]]"], "successCriteria": ["phase done"]},
        body="# Demo phase\n",
    )
    task = create_card(
        plans_root,
        config,
        models,
        type_name="task",
        card_id="TASK-DEMO",
        parent_id="PHASE-DEMO",
        fields={"title": "Demo task", "status": "in-progress", "description": "demo", "parents": ["[[PHASE-DEMO]]"], "successCriteria": ["task done"], "complexity": 30},
        body="# Demo task\n",
    )
    return {"feature": feature, "plan": plan, "phase": phase, "task": task}


def test_created_cards_follow_the_configured_hierarchy(tmp_path: Path) -> None:
    root = tmp_path / "plans"
    paths = create_feature_plan_phase_task(root)
    assert paths["feature"] == root / "features" / "FEATURE-DEMO" / "FEATURE-DEMO.md"
    assert paths["plan"] == root / "features" / "FEATURE-DEMO" / "plans" / "PLAN-DEMO" / "PLAN-DEMO.md"
    assert paths["phase"] == root / "features" / "FEATURE-DEMO" / "plans" / "PLAN-DEMO" / "PHASE-DEMO" / "PHASE-DEMO.md"
    assert paths["task"] == root / "features" / "FEATURE-DEMO" / "plans" / "PLAN-DEMO" / "PHASE-DEMO" / "tasks" / "TASK-DEMO.md"


def test_created_card_roundtrips_through_validated_model(tmp_path: Path) -> None:
    root = tmp_path / "plans"
    create_feature_plan_phase_task(root)
    config, models = make_models()
    task = read_card(root, config, models, "TASK-DEMO").model_dump()
    assert task["id"] == "TASK-DEMO"
    assert task["complexity"] == 30
    assert task["parents"] == ["[[PHASE-DEMO]]"]


def test_create_child_with_missing_parent_fails(tmp_path: Path) -> None:
    root = tmp_path / "plans"
    config, models = make_models()
    with pytest.raises(AssertionError):
        create_card(
            root,
            config,
            models,
            type_name="task",
            card_id="TASK-ORPHAN",
            parent_id="PHASE-ABSENT",
            fields={"title": "Orphan", "status": "in-progress", "description": "x", "successCriteria": ["y"]},
            body="# Orphan\n",
        )


def test_create_rejects_invalid_field_values(tmp_path: Path) -> None:
    root = tmp_path / "plans"
    config, models = make_models()
    with pytest.raises(ValidationError):
        create_card(
            root,
            config,
            models,
            type_name="feature",
            card_id="FEATURE-BAD",
            parent_id=None,
            fields={"title": "Bad", "status": "shipped", "description": "x"},
            body="# Bad\n",
        )


def test_update_card_persists_changed_field(tmp_path: Path) -> None:
    root = tmp_path / "plans"
    create_feature_plan_phase_task(root)
    config, models = make_models()
    update_card(root, config, models, "TASK-DEMO", {"status": "complete"})
    assert read_card(root, config, models, "TASK-DEMO").model_dump()["status"] == "complete"


def test_delete_card_removes_file(tmp_path: Path) -> None:
    root = tmp_path / "plans"
    create_feature_plan_phase_task(root)
    delete_card(root, "TASK-DEMO")
    with pytest.raises(AssertionError):
        find_card_path(root, "TASK-DEMO")
