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
    pattern: str | None = None


class StatusSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str
    label: str


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
    validators: list[str] = []
    # Storage layout: a card of this type lives under <container> beneath its parent
    # card's own directory (or the system root when it has no parent). own_dir=True means
    # the card occupies a directory named by its id (so it can contain child cards).
    container: str = ""
    own_dir: bool = False


class CardSystemConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statuses: dict[str, StatusSpec]
    status_sets: dict[str, StatusSetSpec]
    card_types: list[CardTypeSpec]
    root: str = "plans"
    subdirectories: list[str] = []

    @model_validator(mode="after")
    def check_references(self) -> CardSystemConfig:
        # Every cross-reference in the config must resolve, so a malformed config fails
        # loudly at load instead of producing a card model that silently drops or
        # mis-validates fields.
        status_values = set(self.statuses)
        for set_name, status_set in self.status_sets.items():
            if status_set.default not in status_values:
                raise ValueError(f"status set {set_name} default not in catalog: {status_set.default}")
            for option in status_set.options:
                if option not in status_values:
                    raise ValueError(f"status set {set_name} option not in catalog: {option}")
        by_name = {card_type.name: card_type for card_type in self.card_types}
        for card_type in self.card_types:
            if card_type.status_set not in self.status_sets:
                raise ValueError(f"card type {card_type.name} references unknown status set: {card_type.status_set}")
            for parent in card_type.parents:
                if parent not in by_name:
                    raise ValueError(f"card type {card_type.name} references unknown parent: {parent}")
                if not by_name[parent].own_dir:
                    raise ValueError(f"card type {card_type.name} parent {parent} must own a directory to contain children")
            field_names = [field.name for field in card_type.fields]
            if len(field_names) != len(set(field_names)):
                raise ValueError(f"card type {card_type.name} has duplicate field names")
            for field in card_type.fields:
                if field.type == "select" and not field.options:
                    raise ValueError(f"select field {card_type.name}.{field.name} must declare options")
        return self
