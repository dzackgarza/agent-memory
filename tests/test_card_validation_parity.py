from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from agent_memory.cards import CardSystemConfig, build_card_models, load_card_system_config
from agent_memory.cards.storage import create_card, update_card
from agent_memory.cards.validation import load_card_records, validate_cards


def models_and_config() -> tuple[CardSystemConfig, dict[str, type[BaseModel]]]:
    config = load_card_system_config()
    return config, build_card_models(config)


def make_feature(root: Path, config: CardSystemConfig, models: dict[str, type[BaseModel]], suffix: str, status: str) -> None:
    create_card(
        root,
        config,
        models,
        type_name="feature",
        card_id=f"FEATURE-{suffix}",
        parent_id=None,
        fields={"title": "F", "status": status, "description": "d"},
        body="# F\n",
    )


def make_plan(root: Path, config: CardSystemConfig, models: dict[str, type[BaseModel]], suffix: str, status: str, parent: str) -> None:
    create_card(
        root,
        config,
        models,
        type_name="plan",
        card_id=f"PLAN-{suffix}",
        parent_id=parent,
        fields={"title": "P", "status": status, "description": "d", "parents": [f"[[{parent}]]"], "successCriteria": ["c"]},
        body="# P\n",
    )


def make_phase(root: Path, config: CardSystemConfig, models: dict[str, type[BaseModel]], suffix: str, status: str, parent: str) -> None:
    create_card(
        root,
        config,
        models,
        type_name="phase",
        card_id=f"PHASE-{suffix}",
        parent_id=parent,
        fields={"title": "PH", "status": status, "description": "d", "parents": [f"[[{parent}]]"], "successCriteria": ["c"]},
        body="# PH\n",
    )


def make_task(root: Path, config: CardSystemConfig, models: dict[str, type[BaseModel]], suffix: str, status: str, parent: str) -> None:
    create_card(
        root,
        config,
        models,
        type_name="task",
        card_id=f"TASK-{suffix}",
        parent_id=parent,
        fields={"title": "T", "status": status, "description": "d", "parents": [f"[[{parent}]]"], "successCriteria": ["c"]},
        body="# T\n",
    )


# --- status hierarchy -------------------------------------------------------


def test_unstarted_parent_with_started_child_flagged(tmp_path: Path) -> None:
    config, models = models_and_config()
    root = tmp_path / "p" / "plans"
    # FEATURE complete -> PLAN approved-and-unstarted (unstarted role) with started PHASE
    make_feature(root, config, models, "ONE", "complete")
    make_plan(root, config, models, "ONE", "approved-and-unstarted", "FEATURE-ONE")
    make_phase(root, config, models, "ONE", "in-progress", "PLAN-ONE")
    update_card(root, config, models, "FEATURE-ONE", {"plans": ["[[PLAN-ONE]]"]})
    problems = validate_cards(load_card_records([root], config, models), config)
    assert any(p.kind == "status-hierarchy" and p.card_id == "PLAN-ONE" and "started child 'PHASE-ONE'" in p.detail for p in problems)


def test_in_progress_parent_all_unstarted_children_flagged(tmp_path: Path) -> None:
    config, models = models_and_config()
    root = tmp_path / "p" / "plans"
    make_feature(root, config, models, "ONE", "in-progress")
    make_plan(root, config, models, "ONE", "approved-and-unstarted", "FEATURE-ONE")
    update_card(root, config, models, "FEATURE-ONE", {"plans": ["[[PLAN-ONE]]"]})
    problems = validate_cards(load_card_records([root], config, models), config)
    # in-progress feature with only an unstarted child: both "only unstarted" and "no started child"
    assert any(p.kind == "status-hierarchy" and p.card_id == "FEATURE-ONE" and "only unstarted children" in p.detail for p in problems)
    assert any(p.kind == "status-hierarchy" and p.card_id == "FEATURE-ONE" and "at least one started child" in p.detail for p in problems)


def test_complete_parent_with_incomplete_child_flagged(tmp_path: Path) -> None:
    config, models = models_and_config()
    root = tmp_path / "p" / "plans"
    make_feature(root, config, models, "ONE", "complete")
    make_plan(root, config, models, "ONE", "in-progress", "FEATURE-ONE")
    update_card(root, config, models, "FEATURE-ONE", {"plans": ["[[PLAN-ONE]]"]})
    problems = validate_cards(load_card_records([root], config, models), config)
    assert any(p.kind == "status-hierarchy" and p.card_id == "FEATURE-ONE" and "while child 'PLAN-ONE'" in p.detail for p in problems)


def test_status_hierarchy_clean_tree_silent(tmp_path: Path) -> None:
    config, models = models_and_config()
    root = tmp_path / "p" / "plans"
    # in-progress feature with one in-progress (started) plan child: satisfies all branches
    make_feature(root, config, models, "ONE", "in-progress")
    make_plan(root, config, models, "ONE", "in-progress", "FEATURE-ONE")
    update_card(root, config, models, "FEATURE-ONE", {"plans": ["[[PLAN-ONE]]"]})
    problems = validate_cards(load_card_records([root], config, models), config)
    assert [p for p in problems if p.kind == "status-hierarchy"] == []


def test_status_hierarchy_unstarted_parent_with_unstarted_child_silent(tmp_path: Path) -> None:
    config, models = models_and_config()
    root = tmp_path / "p" / "plans"
    # an unstarted feature over an unstarted plan is consistent (the unstarted parent has no
    # started child), so the unstarted-parent rule stays silent.
    make_feature(root, config, models, "ONE", "unstarted")
    make_plan(root, config, models, "ONE", "approved-and-unstarted", "FEATURE-ONE")
    update_card(root, config, models, "FEATURE-ONE", {"plans": ["[[PLAN-ONE]]"]})
    problems = validate_cards(load_card_records([root], config, models), config)
    assert [p for p in problems if p.kind == "status-hierarchy"] == []


def test_status_hierarchy_complete_parent_with_complete_child_silent(tmp_path: Path) -> None:
    config, models = models_and_config()
    root = tmp_path / "p" / "plans"
    # a complete feature over a complete plan is consistent (no incomplete child), so the
    # complete-parent rule stays silent.
    make_feature(root, config, models, "ONE", "complete")
    make_plan(root, config, models, "ONE", "complete", "FEATURE-ONE")
    update_card(root, config, models, "FEATURE-ONE", {"plans": ["[[PLAN-ONE]]"]})
    update_card(root, config, models, "PLAN-ONE", {"tags": ["FEATURE-ONE"]})
    problems = validate_cards(load_card_records([root], config, models), config)
    assert [p for p in problems if p.kind == "status-hierarchy"] == []


# --- sibling ordering -------------------------------------------------------


def test_sibling_ordering_missing_dependson_flagged(tmp_path: Path) -> None:
    config, models = models_and_config()
    root = tmp_path / "p" / "plans"
    make_feature(root, config, models, "ONE", "in-progress")
    make_plan(root, config, models, "A", "in-progress", "FEATURE-ONE")
    make_plan(root, config, models, "B", "in-progress", "FEATURE-ONE")
    # declare ordered plans but PLAN-B does not dependsOn PLAN-A
    update_card(root, config, models, "FEATURE-ONE", {"plans": ["[[PLAN-A]]", "[[PLAN-B]]"]})
    problems = validate_cards(load_card_records([root], config, models), config)
    assert any(p.kind == "sibling-ordering" and p.card_id == "PLAN-B" and "dependsOn 'PLAN-A'" in p.detail for p in problems)


def test_sibling_ordering_undeclared_children_flagged(tmp_path: Path) -> None:
    config, models = models_and_config()
    root = tmp_path / "p" / "plans"
    make_feature(root, config, models, "ONE", "in-progress")
    make_plan(root, config, models, "A", "in-progress", "FEATURE-ONE")
    make_plan(root, config, models, "B", "in-progress", "FEATURE-ONE")
    # feature has 2 plan children but declares no 'plans' order
    problems = validate_cards(load_card_records([root], config, models), config)
    assert any(p.kind == "sibling-ordering" and p.card_id == "FEATURE-ONE" and "ordered 'plans' links" in p.detail for p in problems)


def test_sibling_ordering_omitted_child_flagged(tmp_path: Path) -> None:
    config, models = models_and_config()
    root = tmp_path / "p" / "plans"
    make_feature(root, config, models, "ONE", "in-progress")
    make_plan(root, config, models, "A", "in-progress", "FEATURE-ONE")
    make_plan(root, config, models, "B", "in-progress", "FEATURE-ONE")
    # declared 'plans' omits PLAN-B
    update_card(root, config, models, "FEATURE-ONE", {"plans": ["[[PLAN-A]]"]})
    problems = validate_cards(load_card_records([root], config, models), config)
    assert any(p.kind == "sibling-ordering" and p.card_id == "FEATURE-ONE" and "omits" in p.detail and "PLAN-B" in p.detail for p in problems)


def test_sibling_ordering_extra_declared_flagged(tmp_path: Path) -> None:
    config, models = models_and_config()
    root = tmp_path / "p" / "plans"
    make_feature(root, config, models, "ONE", "in-progress")
    make_plan(root, config, models, "A", "in-progress", "FEATURE-ONE")
    make_plan(root, config, models, "B", "in-progress", "FEATURE-ONE")
    update_card(root, config, models, "PLAN-B", {"dependsOn": ["[[PLAN-A]]"]})
    # declared 'plans' lists a non-child id PLAN-GHOST (must exist as a record so it is not a dangling reference)
    make_plan(root, config, models, "GHOST", "in-progress", "FEATURE-ONE")
    update_card(root, config, models, "FEATURE-ONE", {"plans": ["[[PLAN-A]]", "[[PLAN-B]]"]})
    # re-point PLAN-GHOST's parent away so it is not a feature child, then list it
    update_card(root, config, models, "FEATURE-ONE", {"plans": ["[[PLAN-A]]", "[[PLAN-B]]", "[[TASK-GHOST]]"]})
    make_phase(root, config, models, "ONE", "in-progress", "PLAN-A")
    make_task(root, config, models, "GHOST", "in-progress", "PHASE-ONE")
    problems = validate_cards(load_card_records([root], config, models), config)
    assert any(p.kind == "sibling-ordering" and p.card_id == "FEATURE-ONE" and "non-child" in p.detail and "TASK-GHOST" in p.detail for p in problems)


def test_sibling_ordering_phase_to_task_level(tmp_path: Path) -> None:
    config, models = models_and_config()
    root = tmp_path / "p" / "plans"
    make_feature(root, config, models, "ONE", "in-progress")
    make_plan(root, config, models, "ONE", "in-progress", "FEATURE-ONE")
    make_phase(root, config, models, "ONE", "in-progress", "PLAN-ONE")
    make_task(root, config, models, "A", "in-progress", "PHASE-ONE")
    make_task(root, config, models, "B", "in-progress", "PHASE-ONE")
    update_card(root, config, models, "PHASE-ONE", {"tasks": ["[[TASK-A]]", "[[TASK-B]]"]})
    # TASK-B does not dependsOn TASK-A
    problems = validate_cards(load_card_records([root], config, models), config)
    assert any(p.kind == "sibling-ordering" and p.card_id == "TASK-B" and "dependsOn 'TASK-A'" in p.detail for p in problems)


def test_sibling_ordering_clean_tree_silent(tmp_path: Path) -> None:
    config, models = models_and_config()
    root = tmp_path / "p" / "plans"
    make_feature(root, config, models, "ONE", "in-progress")
    make_plan(root, config, models, "A", "in-progress", "FEATURE-ONE")
    make_plan(root, config, models, "B", "in-progress", "FEATURE-ONE")
    update_card(root, config, models, "PLAN-B", {"dependsOn": ["[[PLAN-A]]"]})
    update_card(root, config, models, "FEATURE-ONE", {"plans": ["[[PLAN-A]]", "[[PLAN-B]]"]})
    problems = validate_cards(load_card_records([root], config, models), config)
    assert [p for p in problems if p.kind == "sibling-ordering"] == []


def test_sibling_ordering_single_child_needs_no_order(tmp_path: Path) -> None:
    config, models = models_and_config()
    root = tmp_path / "p" / "plans"
    make_feature(root, config, models, "ONE", "in-progress")
    make_plan(root, config, models, "ONE", "in-progress", "FEATURE-ONE")
    update_card(root, config, models, "FEATURE-ONE", {"plans": ["[[PLAN-ONE]]"]})
    problems = validate_cards(load_card_records([root], config, models), config)
    assert [p for p in problems if p.kind == "sibling-ordering"] == []


# --- filesystem hierarchy ---------------------------------------------------


def test_filesystem_hierarchy_misplaced_card_flagged(tmp_path: Path) -> None:
    config, models = models_and_config()
    root = tmp_path / "p" / "plans"
    make_feature(root, config, models, "ONE", "in-progress")
    make_plan(root, config, models, "ONE", "in-progress", "FEATURE-ONE")
    update_card(root, config, models, "FEATURE-ONE", {"plans": ["[[PLAN-ONE]]"]})
    records = load_card_records([root], config, models)
    # physically move PLAN-ONE to a wrong location, keeping the in-memory metadata
    plan_record = records["PLAN-ONE"]
    wrong = root / "WRONG.md"
    wrong.write_text(plan_record.path.read_text(encoding="utf-8"), encoding="utf-8")
    from agent_memory.cards.validation import CardRecord

    records["PLAN-ONE"] = CardRecord(type_name=plan_record.type_name, path=wrong, metadata=plan_record.metadata)
    problems = validate_cards(records, config)
    assert any(p.kind == "filesystem-hierarchy" and p.card_id == "PLAN-ONE" for p in problems)


def test_filesystem_hierarchy_clean_tree_silent(tmp_path: Path) -> None:
    config, models = models_and_config()
    root = tmp_path / "p" / "plans"
    make_feature(root, config, models, "ONE", "in-progress")
    make_plan(root, config, models, "ONE", "in-progress", "FEATURE-ONE")
    make_phase(root, config, models, "ONE", "in-progress", "PLAN-ONE")
    make_task(root, config, models, "ONE", "in-progress", "PHASE-ONE")
    update_card(root, config, models, "FEATURE-ONE", {"plans": ["[[PLAN-ONE]]"]})
    records = load_card_records([root], config, models)
    problems = validate_cards(records, config)
    assert [p for p in problems if p.kind == "filesystem-hierarchy"] == []


# --- tags from ancestry -----------------------------------------------------


def test_tags_from_ancestry_mismatch_flagged(tmp_path: Path) -> None:
    config, models = models_and_config()
    root = tmp_path / "p" / "plans"
    make_feature(root, config, models, "ONE", "in-progress")
    make_plan(root, config, models, "ONE", "in-progress", "FEATURE-ONE")
    make_phase(root, config, models, "ONE", "in-progress", "PLAN-ONE")
    make_task(root, config, models, "ONE", "in-progress", "PHASE-ONE")
    update_card(root, config, models, "FEATURE-ONE", {"plans": ["[[PLAN-ONE]]"]})
    # TASK-ONE tags should be [FEATURE-ONE, PLAN-ONE, PHASE-ONE]; set a wrong value
    update_card(root, config, models, "TASK-ONE", {"tags": ["WRONG"]})
    problems = validate_cards(load_card_records([root], config, models), config)
    assert any(p.kind == "tags-from-ancestry" and p.card_id == "TASK-ONE" for p in problems)


def test_tags_from_ancestry_missing_flagged(tmp_path: Path) -> None:
    config, models = models_and_config()
    root = tmp_path / "p" / "plans"
    make_feature(root, config, models, "ONE", "in-progress")
    make_plan(root, config, models, "ONE", "in-progress", "FEATURE-ONE")
    update_card(root, config, models, "FEATURE-ONE", {"plans": ["[[PLAN-ONE]]"]})
    # PLAN-ONE has an ancestor FEATURE-ONE but no tags at all
    problems = validate_cards(load_card_records([root], config, models), config)
    assert any(p.kind == "tags-from-ancestry" and p.card_id == "PLAN-ONE" for p in problems)


def test_tags_from_ancestry_correct_tags_silent(tmp_path: Path) -> None:
    config, models = models_and_config()
    root = tmp_path / "p" / "plans"
    make_feature(root, config, models, "ONE", "in-progress")
    make_plan(root, config, models, "ONE", "in-progress", "FEATURE-ONE")
    make_phase(root, config, models, "ONE", "in-progress", "PLAN-ONE")
    make_task(root, config, models, "ONE", "in-progress", "PHASE-ONE")
    update_card(root, config, models, "FEATURE-ONE", {"plans": ["[[PLAN-ONE]]"]})
    update_card(root, config, models, "PLAN-ONE", {"tags": ["FEATURE-ONE"]})
    update_card(root, config, models, "PHASE-ONE", {"tags": ["FEATURE-ONE", "PLAN-ONE"]})
    update_card(root, config, models, "TASK-ONE", {"tags": ["FEATURE-ONE", "PLAN-ONE", "PHASE-ONE"]})
    problems = validate_cards(load_card_records([root], config, models), config)
    assert [p for p in problems if p.kind == "tags-from-ancestry"] == []


def test_tags_from_ancestry_root_feature_no_tags_silent(tmp_path: Path) -> None:
    config, models = models_and_config()
    root = tmp_path / "p" / "plans"
    make_feature(root, config, models, "ONE", "in-progress")
    # a root feature has no tagged ancestors; absent tags must NOT be flagged
    problems = validate_cards(load_card_records([root], config, models), config)
    assert [p for p in problems if p.kind == "tags-from-ancestry"] == []


def test_tags_from_ancestry_no_ancestors_but_declares_tags_flagged(tmp_path: Path) -> None:
    config, models = models_and_config()
    root = tmp_path / "p" / "plans"
    make_feature(root, config, models, "ONE", "in-progress")
    # a root feature with no tagged ancestors must not declare any tags (the elif branch)
    update_card(root, config, models, "FEATURE-ONE", {"tags": ["STRAY"]})
    problems = validate_cards(load_card_records([root], config, models), config)
    assert any(p.kind == "tags-from-ancestry" and p.card_id == "FEATURE-ONE" and "no tagged ancestors" in p.detail for p in problems)


def dangling_parent_records() -> dict[str, object]:
    # A single phase whose only parent link points at a plan id that is not in the record
    # set, exercising the "parent not a record" branches of the child map and ancestor chain.
    from agent_memory.cards.validation import CardRecord

    path = Path("/tmp/plans/features/FEATURE-X/PHASE-X/PHASE-X.md")
    return {"PHASE-X": CardRecord(type_name="phase", path=path, metadata={"id": "PHASE-X", "status": "in-progress", "parents": ["[[PLAN-GONE]]"]})}


def test_dangling_parent_links_are_skipped_in_hierarchy_and_tags() -> None:
    # A parent id that resolves to no record must be skipped by children_by_parent and by
    # ancestor_chain (the `parent_id not in records` / `not in children` branches), so a card
    # whose sole parent is missing produces no status-hierarchy, sibling, or tags problem.
    config = load_card_system_config()
    records = dangling_parent_records()
    problems = validate_cards(records, config)  # type: ignore[arg-type]
    kinds = {p.kind for p in problems}
    assert "status-hierarchy" not in kinds
    assert "tags-from-ancestry" not in kinds
    assert "sibling-ordering" not in kinds


def test_filesystem_hierarchy_skips_card_with_no_resolvable_parent() -> None:
    # A non-root card whose single parent link is unresolvable has len(parents) != 1, so the
    # filesystem check skips it (the early-return branch) and emits no filesystem problem.
    config = load_card_system_config()
    records = dangling_parent_records()
    problems = validate_cards(records, config)  # type: ignore[arg-type]
    assert [p for p in problems if p.kind == "filesystem-hierarchy"] == []


def test_ancestor_chain_dedups_shared_ancestors() -> None:
    # A diamond ancestry (a phase reachable through two plans that share one feature) must
    # appear once in the derived tag chain, exercising ancestor_chain's "already in chain"
    # dedup branches. Built directly because create_card enforces a single containment parent.
    from agent_memory.cards.validation import CardRecord, ancestor_chain

    base = Path("/tmp/plans")
    records: dict[str, CardRecord] = {
        "FEATURE-D": CardRecord("feature", base / "features/FEATURE-D/FEATURE-D.md", {"id": "FEATURE-D"}),
        "PLAN-A": CardRecord("plan", base / "features/FEATURE-D/plans/PLAN-A/PLAN-A.md", {"id": "PLAN-A", "parents": ["[[FEATURE-D]]"]}),
        "PLAN-B": CardRecord("plan", base / "features/FEATURE-D/plans/PLAN-B/PLAN-B.md", {"id": "PLAN-B", "parents": ["[[FEATURE-D]]"]}),
        # PHASE-D lists PLAN-A, PLAN-B (both children of FEATURE-D) and FEATURE-D directly.
        # Processing PLAN-B re-yields FEATURE-D, already in the chain from PLAN-A (the inner
        # "ancestor already in chain" branch); processing FEATURE-D directly finds it already
        # present (the outer "parent already in chain" branch).
        "PHASE-D": CardRecord("phase", base / "PHASE-D/PHASE-D.md", {"id": "PHASE-D", "parents": ["[[PLAN-A]]", "[[PLAN-B]]", "[[FEATURE-D]]"]}),
    }
    chain = ancestor_chain("PHASE-D", records, frozenset())
    # FEATURE-D is reachable three ways but must appear exactly once
    assert chain.count("FEATURE-D") == 1
    assert set(chain) == {"FEATURE-D", "PLAN-A", "PLAN-B"}
