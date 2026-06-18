from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

FieldType = Literal[
    "string",
    "text",
    "int",
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


class CardSystemConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statuses: dict[str, StatusSpec]
    status_sets: dict[str, StatusSetSpec]
    card_types: list[CardTypeSpec]
    subdirectories: list[str] = []
