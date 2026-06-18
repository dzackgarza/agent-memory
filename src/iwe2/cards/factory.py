from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, create_model

from iwe2.cards.config import CardSystemConfig, FieldSpec, StatusSetSpec


def membership_validator(options: list[str]) -> Callable[[str], str]:
    allowed = set(options)

    def check(value: str) -> str:
        if value not in allowed:
            raise ValueError(f"value must be one of {sorted(allowed)}")
        return value

    return check


def field_definition(field: FieldSpec, status_set: StatusSetSpec) -> tuple[Any, Any]:
    # Map a declared field spec onto a (type, FieldInfo) pair for pydantic.create_model.
    # Constraints declared in config become real pydantic validation; nothing is advisory.
    if field.type in ("status", "select"):
        options = status_set.options if field.type == "status" else field.options
        annotation: Any = Annotated[str, AfterValidator(membership_validator(options))]
        if field.required:
            return (annotation, Field())
        default = field.default
        if default is None and field.type == "status":
            default = status_set.default
        return (annotation | None, Field(default=default))
    if field.type in ("int", "number"):
        scalar_number: Any = int if field.type == "int" else float
        if field.required:
            return (scalar_number, Field(ge=field.min, le=field.max))
        return (scalar_number | None, Field(default=field.default, ge=field.min, le=field.max))
    if field.type in ("string_list", "wikilink_list"):
        if field.required:
            return (list[str], Field())
        return (list[str], Field(default_factory=list))
    scalar: Any = bool if field.type == "bool" else str
    if field.required:
        return (scalar, Field())
    return (scalar | None, Field(default=field.default))


def build_card_models(config: CardSystemConfig) -> dict[str, type[BaseModel]]:
    models: dict[str, type[BaseModel]] = {}
    for card_type in config.card_types:
        status_set = config.status_sets[card_type.status_set]
        definitions: dict[str, Any] = {field.name: field_definition(field, status_set) for field in card_type.fields}
        models[card_type.name] = create_model(
            f"{card_type.name.capitalize()}Card",
            __config__=ConfigDict(extra="forbid"),
            **definitions,
        )
    return models
