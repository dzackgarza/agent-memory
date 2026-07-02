from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from agent_memory.cards import CardSystemConfig, build_card_models, load_card_system_config
from agent_memory.cards.storage import create_card, update_card
from agent_memory.cards.validation import load_card_records, validate_cards, wikilink_ids


def models_and_config() -> tuple[CardSystemConfig, dict[str, type[BaseModel]]]:
    config = load_card_system_config()
    return config, build_card_models(config)


def seed_feature_chain(root: Path, suffix: str, config: CardSystemConfig, models: dict[str, type[BaseModel]]) -> None:
    # A single-child feature->plan->phase->task chain that is valid under every validation
    # rule: every level is in-progress (a started child for each started parent), the
    # feature declares its lone plan in `plans`, and each card's tags equal its ancestor
    # chain. Single children need no further ordering.
    feature = f"FEATURE-{suffix}"
    plan = f"PLAN-{suffix}"
    phase = f"PHASE-{suffix}"
    task = f"TASK-{suffix}"
    create_card(
        root,
        config,
        models,
        type_name="feature",
        card_id=feature,
        parent_id=None,
        fields={"title": "F", "status": "in-progress", "description": "d", "plans": [f"[[{plan}]]"]},
        body="# F\n",
    )
    create_card(
        root,
        config,
        models,
        type_name="plan",
        card_id=plan,
        parent_id=feature,
        fields={"title": "P", "status": "in-progress", "description": "d", "parents": [f"[[{feature}]]"], "successCriteria": ["c"], "tasks": [f"[[{task}]]"], "tags": [feature]},
        body="# P\n",
    )
    create_card(
        root,
        config,
        models,
        type_name="phase",
        card_id=phase,
        parent_id=plan,
        fields={"title": "PH", "status": "in-progress", "description": "d", "parents": [f"[[{plan}]]"], "successCriteria": ["c"], "tags": [feature, plan]},
        body="# PH\n",
    )
    create_card(
        root,
        config,
        models,
        type_name="task",
        card_id=task,
        parent_id=phase,
        fields={"title": "T", "status": "in-progress", "description": "d", "parents": [f"[[{phase}]]"], "successCriteria": ["c"], "tags": [feature, plan, phase]},
        body="# T\n",
    )


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


def test_wikilink_ids_passes_through_unbracketed() -> None:
    # a value without the [[...]] wrapper is already a bare id and must pass through
    # unchanged (the false branch of the bracket check).
    assert wikilink_ids(["PLAIN-ID"]) == ["PLAIN-ID"]
    assert wikilink_ids("BARE") == ["BARE"]


def test_load_card_records_tolerates_missing_root(tmp_path: Path) -> None:
    # a plans root that does not exist must be skipped, not crash (the `if not
    # root.exists(): continue` branch); real roots still load fully.
    config, models = models_and_config()
    real_root = tmp_path / "p" / "plans"
    seed_feature_chain(real_root, "ONE", config, models)
    records = load_card_records([tmp_path / "missing" / "plans", real_root], config, models)
    assert set(records) == {"FEATURE-ONE", "PLAN-ONE", "PHASE-ONE", "TASK-ONE"}


def test_dependency_cycles_diamond_is_acyclic() -> None:
    # a diamond (A->B, A->C, B->D, C->D) has a shared sink but no cycle; revisiting D
    # via the second path hits the color==2 (already finished) branch, not a cycle.
    from agent_memory.cards.validation import dependency_cycles

    assert dependency_cycles({"A": ["B", "C"], "B": ["D"], "C": ["D"], "D": []}) == []
