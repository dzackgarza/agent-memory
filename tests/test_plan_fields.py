from __future__ import annotations

import pytest

from iwe2.cards import CardSystemConfig
from iwe2.operations import parse_card_fields

# Minimal config exercising every scalar coercion branch plus list accumulation.
CONFIG = CardSystemConfig.model_validate(
    {
        "statuses": ["unstarted"],
        "status_sets": {"standard": {"default": "unstarted", "options": ["unstarted"]}},
        "card_types": [
            {
                "name": "widget",
                "id_prefix": "WIDGET",
                "status_set": "standard",
                "own_dir": True,
                "fields": [
                    {"name": "id", "type": "string", "required": True},
                    {"name": "count", "type": "int", "min": 0, "max": 100},
                    {"name": "ratio", "type": "number"},
                    {"name": "flag", "type": "bool"},
                    {"name": "label", "type": "string"},
                    {"name": "tags", "type": "string_list"},
                ],
            }
        ],
    }
)


def test_parse_card_fields_coerces_each_scalar_type() -> None:
    fields = parse_card_fields(CONFIG, "widget", ["count=5", "ratio=1.5", "flag=true", "label=hi"])
    assert fields == {"count": 5, "ratio": 1.5, "flag": True, "label": "hi"}


def test_parse_card_fields_bool_is_false_for_non_truthy_token() -> None:
    assert parse_card_fields(CONFIG, "widget", ["flag=no"]) == {"flag": False}


def test_parse_card_fields_accumulates_repeated_list_values() -> None:
    assert parse_card_fields(CONFIG, "widget", ["tags=alpha", "tags=beta"]) == {"tags": ["alpha", "beta"]}


def test_parse_card_fields_rejects_unknown_field() -> None:
    with pytest.raises(AssertionError):
        parse_card_fields(CONFIG, "widget", ["bogus=x"])


def test_parse_card_fields_rejects_assignment_without_equals() -> None:
    with pytest.raises(AssertionError):
        parse_card_fields(CONFIG, "widget", ["count"])
