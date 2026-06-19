from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError

from agent_memory.cards import CardSystemConfig, build_card_models, load_card_models, load_card_system_config

# A card type with an object_list field: a list of nested objects, each validated
# against its own declared schema. Mirrors the real spec.constructorNameInventories
# shape (owner + two string lists) without the UI noise.
CONFIG: dict[str, Any] = {
    "statuses": ["unstarted", "complete"],
    "status_sets": {"standard": {"default": "unstarted", "options": ["unstarted", "complete"]}},
    "card_types": [
        {
            "name": "spec",
            "id_prefix": "SPEC",
            "status_set": "standard",
            "parents": [],
            "own_dir": True,
            "fields": [
                {"name": "id", "type": "string", "required": True},
                {"name": "title", "type": "string", "required": True},
                {"name": "status", "type": "status"},
                {
                    "name": "inventories",
                    "type": "object_list",
                    "schema": [
                        {"name": "owner", "type": "string", "required": True},
                        {"name": "sageNames", "type": "string_list"},
                        {"name": "projectNames", "type": "string_list"},
                    ],
                },
            ],
        },
    ],
}


def spec_model() -> Any:
    return build_card_models(CardSystemConfig.model_validate(CONFIG))["spec"]


def base_card() -> dict[str, Any]:
    return {"id": "SPEC-X", "title": "A spec", "status": "complete"}


def test_object_list_validates_nested_items() -> None:
    inventory = {"owner": "category_specs.cat.Cat.Constructors", "sageNames": [], "projectNames": ["EmptyCategory"]}
    dumped = spec_model().model_validate({**base_card(), "inventories": [inventory]}).model_dump()
    assert dumped["inventories"][0]["owner"] == "category_specs.cat.Cat.Constructors"
    assert dumped["inventories"][0]["projectNames"] == ["EmptyCategory"]


def test_object_list_defaults_to_empty_when_omitted() -> None:
    assert spec_model().model_validate(base_card()).model_dump()["inventories"] == []


def test_object_list_rejects_undeclared_nested_key() -> None:
    bad: dict[str, Any] = {"owner": "o", "sageNames": [], "projectNames": [], "sprint": "Q3"}
    with pytest.raises(ValidationError):
        spec_model().model_validate({**base_card(), "inventories": [bad]})


def test_object_list_rejects_missing_required_nested_field() -> None:
    bad: dict[str, Any] = {"sageNames": [], "projectNames": []}  # missing required owner
    with pytest.raises(ValidationError):
        spec_model().model_validate({**base_card(), "inventories": [bad]})


def test_object_list_required_field_must_be_present() -> None:
    cfg = deepcopy(CONFIG)
    cfg["card_types"][0]["fields"][3]["required"] = True
    model = build_card_models(CardSystemConfig.model_validate(cfg))["spec"]
    item = {"owner": "o", "sageNames": [], "projectNames": []}
    assert model.model_validate({**base_card(), "inventories": [item]}).model_dump()["inventories"][0]["owner"] == "o"
    with pytest.raises(ValidationError):
        model.model_validate(base_card())  # required object_list omitted


def test_config_rejects_object_list_without_schema() -> None:
    bad = deepcopy(CONFIG)
    bad["card_types"][0]["fields"].append({"name": "extra", "type": "object_list"})
    with pytest.raises(ValidationError):
        CardSystemConfig.model_validate(bad)


def test_config_rejects_schema_on_non_object_list_field() -> None:
    bad = deepcopy(CONFIG)
    bad["card_types"][0]["fields"].append({"name": "extra", "type": "string", "schema": [{"name": "x", "type": "string"}]})
    with pytest.raises(ValidationError):
        CardSystemConfig.model_validate(bad)


def test_config_rejects_nested_select_without_options() -> None:
    bad = deepcopy(CONFIG)
    bad["card_types"][0]["fields"][3]["schema"].append({"name": "tier", "type": "select"})
    with pytest.raises(ValidationError):
        CardSystemConfig.model_validate(bad)


def test_config_rejects_duplicate_nested_field_names() -> None:
    bad = deepcopy(CONFIG)
    bad["card_types"][0]["fields"][3]["schema"].append({"name": "owner", "type": "string"})
    with pytest.raises(ValidationError):
        CardSystemConfig.model_validate(bad)


# --- shipped starter config: spec + decision card types (trackerStatus stripped,
# matching migration which derives the card type from storage location) ---


def test_shipped_config_covers_spec_and_decision() -> None:
    names = {card_type.name for card_type in load_card_system_config().card_types}
    assert {"spec", "decision"} <= names


def test_shipped_spec_model_validates_real_frontmatter_with_inventories() -> None:
    # Captured from category-specs SPEC-MAPPING-CAT.md (trackerStatus stripped).
    card = {
        "id": "SPEC-MAPPING-CAT",
        "parents": ["[[FEATURE-CATEGORY-SPECS-AND-SAGE-SURFACES]]"],
        "dependsOn": ["[[PHASE-MAPPING-DOC-SPEC-CONVERSION-AND-MATHEMATICAL-AUDIT]]"],
        "title": "Track cat mapping spec",
        "status": "complete",
        "priority": "critical",
        "requirement": "Convert category_specs/cat/docs/MAPPING.md into a tracked spec surface.",
        "acceptanceCriteria": ["Source paths are reviewed.", "Every admitted row states evidence."],
        "complexity": 80,
        "tags": ["FEATURE-CATEGORY-SPECS-AND-SAGE-SURFACES"],
        "constructorNameInventories": [
            {
                "owner": "category_specs.cat.Cat.Constructors",
                "sageConstructorNames": [],
                "projectOwnedConstructionNames": ["EmptyCategory"],
            }
        ],
    }
    validated = load_card_models()["spec"].model_validate(card).model_dump()
    assert validated["status"] == "complete"
    assert validated["constructorNameInventories"][0]["projectOwnedConstructionNames"] == ["EmptyCategory"]


def test_shipped_decision_model_validates_real_frontmatter_with_options() -> None:
    # Captured from category-specs DECISION-ALGEBRA-STANDARD-INVOLUTION-OWNER.md.
    card = {
        "id": "DECISION-ALGEBRA-STANDARD-INVOLUTION-OWNER",
        "parents": ["[[FEATURE-CATEGORY-SPECS-AND-SAGE-SURFACES]]"],
        "dependsOn": [],
        "title": "Decide algebra standard-involution method owner",
        "status": "decided",
        "chosen": "Reject as public project method for now",
        "options": [
            {
                "name": "Quaternion-algebra refinement owner",
                "pros": ["Matches the Sage source note."],
                "cons": ["Requires mining quaternion-algebra sources."],
            },
            {
                "name": "Reject as public project method for now",
                "pros": ["Keeps the inventory free of a weakly grounded predicate."],
                "cons": ["Defers a Sage-visible surface."],
            },
        ],
        "tags": ["FEATURE-CATEGORY-SPECS-AND-SAGE-SURFACES"],
    }
    validated = load_card_models()["decision"].model_validate(card).model_dump()
    assert validated["status"] == "decided"
    assert validated["chosen"] == "Reject as public project method for now"
    assert validated["options"][0]["name"] == "Quaternion-algebra refinement owner"


def test_shipped_decision_status_set_rejects_status_outside_set() -> None:
    card = {
        "id": "DECISION-X",
        "parents": ["[[FEATURE-CATEGORY-SPECS-AND-SAGE-SURFACES]]"],
        "title": "t",
        "status": "approved-and-unstarted",  # not in the decision status set
    }
    with pytest.raises(ValidationError):
        load_card_models()["decision"].model_validate(card)


def test_shipped_spec_model_rejects_malformed_inventory_item() -> None:
    card = {
        "id": "SPEC-MAPPING-CAT",
        "parents": ["[[FEATURE-CATEGORY-SPECS-AND-SAGE-SURFACES]]"],
        "title": "t",
        "status": "complete",
        "constructorNameInventories": [{"owner": "o", "unexpectedKey": True}],
    }
    with pytest.raises(ValidationError):
        load_card_models()["spec"].model_validate(card)
