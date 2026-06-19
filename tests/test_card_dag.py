from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from agent_memory.cards import CardSystemConfig, build_card_models, load_card_system_config
from agent_memory.cards.dag import render_dag
from agent_memory.cards.storage import create_card, update_card
from agent_memory.cards.validation import load_card_records


def models_and_config() -> tuple[CardSystemConfig, dict[str, type[BaseModel]]]:
    config = load_card_system_config()
    return config, build_card_models(config)


def seed_feature_chain(root: Path, suffix: str, config: CardSystemConfig, models: dict[str, type[BaseModel]]) -> None:
    create_card(
        root, config, models, type_name="feature", card_id=f"FEATURE-{suffix}", parent_id=None, fields={"title": "F", "status": "in-progress", "description": "d"}, body="# F\n"
    )
    create_card(
        root,
        config,
        models,
        type_name="plan",
        card_id=f"PLAN-{suffix}",
        parent_id=f"FEATURE-{suffix}",
        fields={"title": "P", "status": "approved-and-unstarted", "description": "d", "parents": [f"[[FEATURE-{suffix}]]"], "successCriteria": ["c"]},
        body="# P\n",
    )
    create_card(
        root,
        config,
        models,
        type_name="phase",
        card_id=f"PHASE-{suffix}",
        parent_id=f"PLAN-{suffix}",
        fields={"title": "PH", "status": "in-progress", "description": "d", "parents": [f"[[PLAN-{suffix}]]"], "successCriteria": ["c"]},
        body="# PH\n",
    )
    create_card(
        root,
        config,
        models,
        type_name="task",
        card_id=f"TASK-{suffix}",
        parent_id=f"PHASE-{suffix}",
        fields={"title": "T", "status": "in-progress", "description": "d", "parents": [f"[[PHASE-{suffix}]]"], "successCriteria": ["c"]},
        body="# T\n",
    )


def test_dag_renders_dependency_and_containment_edges(tmp_path: Path) -> None:
    config, models = models_and_config()
    root1 = tmp_path / "p1" / "plans"
    root2 = tmp_path / "p2" / "plans"
    seed_feature_chain(root1, "ONE", config, models)
    seed_feature_chain(root2, "TWO", config, models)
    update_card(root1, config, models, "TASK-ONE", {"dependsOn": ["[[PLAN-TWO]]"]})
    dag = render_dag(load_card_records([root1, root2], config, models))
    assert "```mermaid" in dag
    # cross-project dependency edge appears in the dependency graph
    assert "TASK-ONE --> PLAN-TWO" in dag
    # containment edge parent -> child appears in the containment graph
    assert "PHASE-ONE --> TASK-ONE" in dag
    assert "FEATURE-TWO --> PLAN-TWO" in dag


def test_dag_omits_edges_to_nonexistent_targets(tmp_path: Path) -> None:
    # dependency/containment edges are emitted only when the target card exists; a
    # reference to a missing id contributes no edge (the `if target in records` /
    # `if parent in records` false branches).
    config, models = models_and_config()
    root = tmp_path / "p" / "plans"
    seed_feature_chain(root, "ONE", config, models)
    update_card(root, config, models, "TASK-ONE", {"dependsOn": ["[[TASK-GHOST]]"]})
    update_card(root, config, models, "PHASE-ONE", {"parents": ["[[PLAN-ONE]]", "[[PLAN-GHOST]]"]})
    dag = render_dag(load_card_records([root], config, models))
    assert "TASK-GHOST" not in dag
    assert "PLAN-GHOST" not in dag
