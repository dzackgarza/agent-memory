from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    "object_list",
]


class FieldSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str
    type: FieldType
    required: bool = False
    default: str | int | bool | None = None
    options: list[str] = []
    min: int | None = None
    max: int | None = None
    min_items: int | None = None
    # For an object_list field, the schema of each nested item. Aliased to "schema"
    # (the config key) to avoid shadowing BaseModel.schema. Empty for scalar fields.
    item_schema: list[FieldSpec] = Field(default_factory=list, alias="schema")


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


def _validate_workflow_roles(statuses: list[str], workflow_roles: dict[str, list[str]]) -> None:
    status_values = set(statuses)
    for role, members in workflow_roles.items():
        for status in members:
            if status not in status_values:
                raise ValueError(f"workflow role {role} lists status not in catalog: {status}")


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


def _validate_field(field: FieldSpec, context: str) -> None:
    if field.type == "select" and not field.options:
        raise ValueError(f"select field {context}.{field.name} must declare options")
    if field.type == "object_list":
        if not field.item_schema:
            raise ValueError(f"object_list field {context}.{field.name} must declare a nested schema")
        _validate_fields(field.item_schema, f"{context}.{field.name}")
    elif field.item_schema:
        raise ValueError(f"field {context}.{field.name} declares a nested schema but is not an object_list")


def _validate_fields(fields: list[FieldSpec], context: str) -> None:
    field_names = [field.name for field in fields]
    if len(field_names) != len(set(field_names)):
        raise ValueError(f"{context} has duplicate field names")
    for field in fields:
        _validate_field(field, context)


def _validate_card_fields(card_type: CardTypeSpec) -> None:
    _validate_fields(card_type.fields, card_type.name)


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
    # Status lifecycle roles (started / complete / unstarted) used for rollups and
    # transitions; each role maps to the catalog statuses that count as being in it.
    workflow_roles: dict[str, list[str]] = {}
    root: str = "plans"

    @model_validator(mode="after")
    def check_references(self) -> CardSystemConfig:
        # Every cross-reference in the config must resolve, so a malformed config fails
        # loudly at load instead of producing a card model that silently drops or
        # mis-validates fields.
        _validate_status_sets(self.statuses, self.status_sets)
        _validate_workflow_roles(self.statuses, self.workflow_roles)
        _validate_card_types(self.card_types, self.status_sets)
        return self

    def statuses_with_role(self, role: str) -> set[str]:
        assert role in self.workflow_roles, f"unknown workflow role: {role}"
        return set(self.workflow_roles[role])
