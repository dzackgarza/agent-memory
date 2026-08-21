from __future__ import annotations

import os
import subprocess
import sys
import zipfile
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from agent_memory.cards import CardSystemConfig, build_card_models, load_card_models, load_card_system_config

# Representative card-system config: mirrors the semantic structure of the real
# Nimbalyst feature/plan schemas (a select-backed status set, required fields, an
# int field with a max), without the UI-only noise. The factory must compile this
# into pydantic validators that enforce every declared constraint.
CONFIG: dict[str, Any] = {
    "statuses": ["unstarted", "in-progress", "complete", "needs-agent-review", "blocked"],
    "status_sets": {
        "standard": {
            "default": "unstarted",
            "options": ["unstarted", "in-progress", "complete", "needs-agent-review", "blocked"],
        },
    },
    "card_types": [
        {
            "name": "feature",
            "id_prefix": "FEATURE",
            "status_set": "standard",
            "parents": [],
            "own_dir": True,
            "fields": [
                {"name": "id", "type": "string", "required": True},
                {"name": "title", "type": "string", "required": True},
                {"name": "status", "type": "status", "required": True},
                {"name": "priority", "type": "select", "options": ["low", "medium", "high", "critical"]},
                {"name": "description", "type": "text"},
                {"name": "parents", "type": "wikilink_list"},
                {"name": "dependsOn", "type": "wikilink_list"},
                {"name": "plans", "type": "wikilink_list"},
            ],
        },
        {
            "name": "plan",
            "id_prefix": "PLAN",
            "status_set": "standard",
            "parents": ["feature"],
            "fields": [
                {"name": "id", "type": "string", "required": True},
                {"name": "title", "type": "string", "required": True},
                {"name": "status", "type": "status", "required": True},
                {"name": "time_estimate_seconds", "type": "int", "min": 0, "max": 10_000_000},
            ],
        },
    ],
}


def valid_feature_card() -> dict[str, Any]:
    return {
        "id": "FEATURE-CATEGORY-SPECS",
        "title": "Category specs and Sage-grounded operations",
        "status": "in-progress",
        "priority": "critical",
        "description": "Specify a Sage-compatible categorical language.",
        "parents": [],
        "dependsOn": [],
        "plans": ["[[PLAN-CATEGORY-SPEC-PROGRAM]]"],
    }


def test_config_rejects_card_type_referencing_unknown_status_set() -> None:
    bad = deepcopy(CONFIG)
    bad["card_types"][0]["status_set"] = "does-not-exist"
    with pytest.raises(ValidationError):
        CardSystemConfig.model_validate(bad)


def test_config_rejects_card_type_with_unknown_parent() -> None:
    bad = deepcopy(CONFIG)
    bad["card_types"][1]["parents"] = ["nonexistent-card-type"]
    with pytest.raises(ValidationError):
        CardSystemConfig.model_validate(bad)


def test_config_rejects_status_set_default_absent_from_catalog() -> None:
    bad = deepcopy(CONFIG)
    bad["status_sets"]["standard"]["default"] = "not-a-status"
    with pytest.raises(ValidationError):
        CardSystemConfig.model_validate(bad)


def test_built_feature_model_accepts_real_frontmatter_shape() -> None:
    models = build_card_models(CardSystemConfig.model_validate(CONFIG))
    card = models["feature"].model_validate(valid_feature_card()).model_dump()
    assert card["status"] == "in-progress"
    assert card["plans"] == ["[[PLAN-CATEGORY-SPEC-PROGRAM]]"]
    assert card["priority"] == "critical"


def test_built_model_rejects_status_outside_declared_status_set() -> None:
    models = build_card_models(CardSystemConfig.model_validate(CONFIG))
    card = valid_feature_card()
    card["status"] = "shipped"  # not in the standard status set
    with pytest.raises(ValidationError):
        models["feature"].model_validate(card)


def test_shipped_plan_status_set_accepts_canonical_unstarted() -> None:
    config = load_card_system_config()
    assert "unstarted" in config.status_sets["plan"].options

    models = build_card_models(config)
    validated = models["plan"].model_validate(
        {
            "id": "PLAN-X",
            "parents": ["[[FEATURE-X]]"],
            "title": "A plan",
            "status": "unstarted",
            "description": "A schema-valid plan.",
            "successCriteria": ["The plan validates."],
            "tasks": ["[[TASK-X]]"],
        }
    )

    assert validated.model_dump()["status"] == "unstarted"


def test_built_model_rejects_missing_required_field() -> None:
    models = build_card_models(CardSystemConfig.model_validate(CONFIG))
    card = valid_feature_card()
    del card["title"]
    with pytest.raises(ValidationError):
        models["feature"].model_validate(card)


def test_built_model_applies_defaults_for_omitted_optional_fields() -> None:
    models = build_card_models(CardSystemConfig.model_validate(CONFIG))
    dumped = models["feature"].model_validate({"id": "FEATURE-X", "title": "T", "status": "in-progress"}).model_dump()
    assert dumped["priority"] is None  # optional select -> None default
    assert dumped["description"] is None  # optional scalar -> None default
    assert dumped["parents"] == []  # optional list -> empty default


def test_built_model_rejects_undeclared_field() -> None:
    models = build_card_models(CardSystemConfig.model_validate(CONFIG))
    card = valid_feature_card()
    card["sprint"] = "Q3"  # not declared in the feature card type
    with pytest.raises(ValidationError):
        models["feature"].model_validate(card)


def test_built_plan_model_enforces_int_max_for_time_estimate() -> None:
    models = build_card_models(CardSystemConfig.model_validate(CONFIG))
    base = {"id": "PLAN-X", "title": "A plan", "status": "unstarted"}
    accepted = models["plan"].model_validate({**base, "time_estimate_seconds": 9_999_999}).model_dump()
    assert accepted["time_estimate_seconds"] == 9_999_999
    with pytest.raises(ValidationError):
        models["plan"].model_validate({**base, "time_estimate_seconds": 10_000_001})


def test_config_rejects_status_set_option_absent_from_catalog() -> None:
    bad = deepcopy(CONFIG)
    bad["status_sets"]["standard"]["options"].append("not-a-status")
    with pytest.raises(ValidationError):
        CardSystemConfig.model_validate(bad)


def test_config_rejects_parent_that_does_not_own_a_directory() -> None:
    # the plan card type lists feature as a parent; a parent must own a directory to
    # contain children, so flipping feature.own_dir off must be rejected.
    bad = deepcopy(CONFIG)
    bad["card_types"][0]["own_dir"] = False
    with pytest.raises(ValidationError):
        CardSystemConfig.model_validate(bad)


def test_config_rejects_duplicate_field_names() -> None:
    bad = deepcopy(CONFIG)
    bad["card_types"][0]["fields"].append({"name": "title", "type": "string"})
    with pytest.raises(ValidationError):
        CardSystemConfig.model_validate(bad)


def test_config_rejects_select_field_without_options() -> None:
    bad = deepcopy(CONFIG)
    bad["card_types"][0]["fields"].append({"name": "tier", "type": "select"})
    with pytest.raises(ValidationError):
        CardSystemConfig.model_validate(bad)


def test_built_model_requires_required_int_field() -> None:
    # a required numeric field must compile to a bare (no-default) field that enforces
    # presence and the declared min/max range.
    cfg = deepcopy(CONFIG)
    cfg["card_types"][1]["fields"].append({"name": "weight", "type": "int", "required": True, "min": 0, "max": 100})
    models = build_card_models(CardSystemConfig.model_validate(cfg))
    base = {"id": "PLAN-X", "title": "A plan", "status": "unstarted"}
    assert models["plan"].model_validate({**base, "weight": 50}).model_dump()["weight"] == 50
    with pytest.raises(ValidationError):
        models["plan"].model_validate(base)
    with pytest.raises(ValidationError):
        models["plan"].model_validate({**base, "weight": 200})


# --- shipped starter config + loader (captured real card frontmatter, trackerStatus
# stripped because migration determines the card type from storage location, not a field) ---


def test_shipped_config_covers_core_card_types() -> None:
    config = load_card_system_config()
    names = {card_type.name for card_type in config.card_types}
    assert {"feature", "plan", "phase", "task"} <= names


def test_shipped_feature_model_validates_real_feature_frontmatter() -> None:
    models = load_card_models()
    card = {
        "id": "FEATURE-CATEGORY-SPECS-AND-SAGE-SURFACES",
        "parents": [],
        "dependsOn": [],
        "plans": ["[[PLAN-CATEGORY-SPEC-PROGRAM]]", "[[PLAN-SPEC-CORE-VERTICAL-SLICE]]"],
        "title": "Category specs and Sage-grounded operations",
        "status": "in-progress",
        "priority": "critical",
        "description": "Specify a Sage-compatible categorical language.",
    }
    validated = models["feature"].model_validate(card).model_dump()
    assert validated["status"] == "in-progress"
    assert validated["plans"][0] == "[[PLAN-CATEGORY-SPEC-PROGRAM]]"


def test_shipped_feature_model_rejects_status_outside_set() -> None:
    models = load_card_models()
    card = {
        "id": "FEATURE-X",
        "title": "t",
        "status": "shipped",
        "description": "d",
    }
    with pytest.raises(ValidationError):
        models["feature"].model_validate(card)


def test_shipped_plan_model_requires_success_criteria() -> None:
    models = load_card_models()
    base = {
        "id": "PLAN-CATEGORY-SPEC-PROGRAM",
        "parents": ["[[FEATURE-CATEGORY-SPECS-AND-SAGE-SURFACES]]"],
        "title": "Category spec program",
        "status": "approved-and-unstarted",
        "description": "Drive the category spec program.",
        "successCriteria": ["The vertical slice compiles."],
        "tasks": ["[[TASK-CATEGORY-SPEC-FIRST-SLICE]]"],
    }
    assert models["plan"].model_validate(base).model_dump()["status"] == "approved-and-unstarted"
    missing = {key: value for key, value in base.items() if key != "successCriteria"}
    with pytest.raises(ValidationError):
        models["plan"].model_validate(missing)


def test_shipped_plan_model_accepts_a_plan_with_no_parent_and_no_tasks() -> None:
    # The real shape of 122 existing cards: a workstream plan with no task cards, and no
    # containment parent. Both fields must survive validation as empty lists rather than be
    # demanded or dropped, so re-requiring either one goes red here.
    models = load_card_models()
    workstream = {
        "id": "PLAN-WORKSTREAM",
        "title": "Workstream with no task cards",
        "status": "approved-and-unstarted",
        "description": "Runs as prose, not as task cards.",
        "successCriteria": ["The workstream reports out."],
    }
    dumped = models["plan"].model_validate(workstream).model_dump()
    assert dumped["parents"] == []
    assert dumped["tasks"] == []
    assert dumped["title"] == workstream["title"]
    assert models["plan"].model_validate({**workstream, "tasks": []}).model_dump()["tasks"] == []
    assert models["plan"].model_validate({**workstream, "tasks": ["[[TASK-ONE]]"]}).model_dump()["tasks"] == ["[[TASK-ONE]]"]


def test_shipped_task_model_enforces_complexity_range() -> None:
    models = load_card_models()
    base = {
        "id": "TASK-CATEGORY-OBLIGATION-PLAN-FIX-SCOPE",
        "parents": ["[[PHASE-CATEGORY-ASSERTION-REPAIR]]"],
        "title": "Narrow plan description to match actual phase inventory",
        "status": "complete",
        "description": "Resolve a scope gap.",
        "successCriteria": ["Description narrowed."],
    }
    assert models["task"].model_validate({**base, "complexity": 42}).model_dump()["complexity"] == 42
    with pytest.raises(ValidationError):
        models["task"].model_validate({**base, "complexity": 150})


def test_load_card_system_config_prefers_project_cards_yaml(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    project_id = "example-project"

    def payload_for(name: str, id_prefix: str, container: str) -> dict[str, Any]:
        return {
            "root": "plans",
            "statuses": ["todo", "in-progress", "complete", "blocked"],
            "status_sets": {
                "standard": {
                    "default": "todo",
                    "options": ["todo", "in-progress", "complete", "blocked"],
                },
            },
            "card_types": [
                {
                    "name": name,
                    "id_prefix": id_prefix,
                    "status_set": "standard",
                    "parents": [],
                    "own_dir": True,
                    "container": container,
                    "fields": [
                        {"name": "id", "type": "string", "required": True},
                        {"name": "title", "type": "string", "required": True},
                        {"name": "status", "type": "status", "required": True},
                    ],
                },
            ],
        }

    vault_cards_path = vault / "_meta" / "cards.yaml"
    vault_cards_path.parent.mkdir(parents=True)
    vault_cards_path.write_text(yaml.safe_dump(payload_for("global_signal", "GSIG", "global-signals")), encoding="utf-8")

    cards_path = vault / "projects" / project_id / "_meta" / "cards.yaml"
    cards_path.parent.mkdir(parents=True)
    cards_path.write_text(yaml.safe_dump(payload_for("signal", "SIG", "signals")), encoding="utf-8")

    config = load_card_system_config(vault, project_id)
    assert config.root == "plans"
    type_names = {card_type.name for card_type in config.card_types}
    assert type_names == {"signal"}


def test_load_card_system_config_falls_back_to_packaged_defaults_if_project_cards_yaml_missing(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    config = load_card_system_config(vault)
    assert {card_type.name for card_type in config.card_types} >= {"feature", "plan", "phase", "task"}


def test_load_card_system_config_reads_packaged_defaults_from_zip_resource(tmp_path: Path) -> None:
    package_zip = tmp_path / "agent_memory_pkg.zip"
    source_root = Path(__file__).resolve().parents[1] / "src"
    packaged_files = (
        "agent_memory/__init__.py",
        "agent_memory/cards/__init__.py",
        "agent_memory/cards/config.py",
        "agent_memory/cards/factory.py",
        "agent_memory/cards/loader.py",
        "agent_memory/defaults/__init__.py",
        "agent_memory/defaults/cards.yaml",
    )
    with zipfile.ZipFile(package_zip, "w") as archive:
        for relative_path in packaged_files:
            archive.write(source_root / relative_path, relative_path)

    env = os.environ.copy()
    env["PYTHONPATH"] = str(package_zip)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from agent_memory.cards.loader import load_card_system_config; "
                "config = load_card_system_config(); "
                "print('\\n'.join(card_type.name for card_type in config.card_types))"
            ),
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert {"feature", "plan", "task"}.issubset(set(result.stdout.splitlines()))
