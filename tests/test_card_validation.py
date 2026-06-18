from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from iwe2.cards import CardSystemConfig, build_card_models, load_card_system_config
from iwe2.cards.storage import create_card, update_card
from iwe2.cards.validation import load_card_records, validate_cards, wikilink_ids


def models_and_config() -> tuple[CardSystemConfig, dict[str, type[BaseModel]]]:
    config = load_card_system_config()
    return config, build_card_models(config)


def seed_feature_chain(root: Path, suffix: str, config: CardSystemConfig, models: dict[str, type[BaseModel]]) -> None:
    create_card(root, config, models, type_name="feature", card_id=f"FEATURE-{suffix}", parent_id=None,
                fields={"title": "F", "status": "in-progress", "description": "d"}, body="# F\n")
    create_card(root, config, models, type_name="plan", card_id=f"PLAN-{suffix}", parent_id=f"FEATURE-{suffix}",
                fields={"title": "P", "status": "approved-and-unstarted", "description": "d",
                        "parents": [f"[[FEATURE-{suffix}]]"], "successCriteria": ["c"]}, body="# P\n")
    create_card(root, config, models, type_name="phase", card_id=f"PHASE-{suffix}", parent_id=f"PLAN-{suffix}",
                fields={"title": "PH", "status": "in-progress", "description": "d",
                        "parents": [f"[[PLAN-{suffix}]]"], "successCriteria": ["c"]}, body="# PH\n")
    create_card(root, config, models, type_name="task", card_id=f"TASK-{suffix}", parent_id=f"PHASE-{suffix}",
                fields={"title": "T", "status": "in-progress", "description": "d",
                        "parents": [f"[[PHASE-{suffix}]]"], "successCriteria": ["c"]}, body="# T\n")


def test_wikilink_ids_strips_brackets() -> None:
    assert wikilink_ids(["[[PLAN-A]]", "[[TASK-B]]"]) == ["PLAN-A", "TASK-B"]
    assert wikilink_ids("[[FEATURE-X]]") == ["FEATURE-X"]


def test_cross_project_dependency_resolves(tmp_path: Path) -> None:
    config, models = models_and_config()
    root1 = tmp_path / "p1" / "plans"
    root2 = tmp_path / "p2" / "plans"
    seed_feature_chain(root1, "ONE", config, models)
    seed_feature_chain(root2, "TWO", config, models)
    # TASK-ONE in project 1 depends on PLAN-TWO in project 2 (the capability the in-repo tool lacks)
    update_card(root1, config, models, "TASK-ONE", {"dependsOn": ["[[PLAN-TWO]]"]})
    records = load_card_records([root1, root2], config, models)
    problems = validate_cards(records, config)
    assert problems == []
    assert "PLAN-TWO" in records


def test_dangling_dependency_reported(tmp_path: Path) -> None:
    config, models = models_and_config()
    root = tmp_path / "p" / "plans"
    seed_feature_chain(root, "ONE", config, models)
    update_card(root, config, models, "TASK-ONE", {"dependsOn": ["[[TASK-GHOST]]"]})
    problems = validate_cards(load_card_records([root], config, models), config)
    assert any(problem.kind == "reference" and problem.card_id == "TASK-ONE" and "TASK-GHOST" in problem.detail for problem in problems)


def test_dependency_cycle_reported(tmp_path: Path) -> None:
    config, models = models_and_config()
    root = tmp_path / "p" / "plans"
    seed_feature_chain(root, "ONE", config, models)
    seed_feature_chain(root, "TWO", config, models)
    update_card(root, config, models, "TASK-ONE", {"dependsOn": ["[[TASK-TWO]]"]})
    update_card(root, config, models, "TASK-TWO", {"dependsOn": ["[[TASK-ONE]]"]})
    problems = validate_cards(load_card_records([root], config, models), config)
    assert any(problem.kind == "cycle" for problem in problems)


def test_containment_violation_reported(tmp_path: Path) -> None:
    config, models = models_and_config()
    root = tmp_path / "p" / "plans"
    seed_feature_chain(root, "ONE", config, models)
    # point the task's containment parent at a feature (allowed parent is phase)
    update_card(root, config, models, "TASK-ONE", {"parents": ["[[FEATURE-ONE]]"]})
    problems = validate_cards(load_card_records([root], config, models), config)
    assert any(problem.kind == "containment" and problem.card_id == "TASK-ONE" for problem in problems)


def test_load_skips_non_card_files(tmp_path: Path) -> None:
    config, models = models_and_config()
    root = tmp_path / "p" / "plans"
    seed_feature_chain(root, "ONE", config, models)
    # a generated artifact with no card frontmatter must be ignored, not parsed as a card
    (root / "plan-dag.md").write_text("## Dependencies\n\n```mermaid\ngraph LR\n```\n", encoding="utf-8")
    records = load_card_records([root], config, models)
    assert set(records) == {"FEATURE-ONE", "PLAN-ONE", "PHASE-ONE", "TASK-ONE"}


def test_clean_multi_project_tree_has_no_problems(tmp_path: Path) -> None:
    config, models = models_and_config()
    root1 = tmp_path / "p1" / "plans"
    root2 = tmp_path / "p2" / "plans"
    seed_feature_chain(root1, "ONE", config, models)
    seed_feature_chain(root2, "TWO", config, models)
    assert validate_cards(load_card_records([root1, root2], config, models), config) == []
