from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

FieldType = Literal[
    "string",
    "text",
    "int",
    "number",
    "bool",
    "select",
    "status",
    "string_list",
    "wikilink",
    "wikilink_list",
    "user",
]


class FieldSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    type: FieldType
    required: bool = False
    default: str | int | bool | None = None
    options: list[str] = []
    min: int | None = None
    max: int | None = None


class StatusSetSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default: str
    required: bool = False
    options: list[str]


class CardTypeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    id_prefix: str
    status_set: str
    parents: list[str] = []
    fields: list[FieldSpec]
    # Storage layout: a card of this type lives under <container> beneath its parent
    # card's own directory (or the system root when it has no parent). own_dir=True means
    # the card occupies a directory named by its id (so it can contain child cards).
    container: str = ""
    own_dir: bool = False


def _validate_status_sets(statuses: list[str], status_sets: dict[str, StatusSetSpec]) -> None:
    status_values = set(statuses)
    for set_name, status_set in status_sets.items():
        if status_set.default not in status_values:
            raise ValueError(f"status set {set_name} default not in catalog: {status_set.default}")
        for option in status_set.options:
            if option not in status_values:
                raise ValueError(f"status set {set_name} option not in catalog: {option}")


def _validate_card_parents(card_type: CardTypeSpec, by_name: dict[str, CardTypeSpec]) -> None:
    for parent in card_type.parents:
        if parent not in by_name:
            raise ValueError(f"card type {card_type.name} references unknown parent: {parent}")
        if not by_name[parent].own_dir:
            raise ValueError(f"card type {card_type.name} parent {parent} must own a directory to contain children")


def _validate_card_fields(card_type: CardTypeSpec) -> None:
    field_names = [field.name for field in card_type.fields]
    if len(field_names) != len(set(field_names)):
        raise ValueError(f"card type {card_type.name} has duplicate field names")
    for field in card_type.fields:
        if field.type == "select" and not field.options:
            raise ValueError(f"select field {card_type.name}.{field.name} must declare options")


def _validate_card_types(card_types: list[CardTypeSpec], status_sets: dict[str, StatusSetSpec]) -> None:
    by_name = {card_type.name: card_type for card_type in card_types}
    for card_type in card_types:
        if card_type.status_set not in status_sets:
            raise ValueError(f"card type {card_type.name} references unknown status set: {card_type.status_set}")
        _validate_card_parents(card_type, by_name)
        _validate_card_fields(card_type)


class CardSystemConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statuses: list[str]
    status_sets: dict[str, StatusSetSpec]
    card_types: list[CardTypeSpec]
    root: str = "plans"

    @model_validator(mode="after")
    def check_references(self) -> CardSystemConfig:
        # Every cross-reference in the config must resolve, so a malformed config fails
        # loudly at load instead of producing a card model that silently drops or
        # mis-validates fields.
        _validate_status_sets(self.statuses, self.status_sets)
        _validate_card_types(self.card_types, self.status_sets)
        return self
