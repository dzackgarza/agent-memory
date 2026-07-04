from __future__ import annotations

from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError

from agent_memory.cards.config import CardSystemConfig
from agent_memory.cards.factory import build_card_models

_CARDS_SCHEMA_PATH = "cards.yaml"


class CardConfigError(ValueError):
    """Raised when a card schema cannot be loaded."""

    def __init__(self, path: Traversable, detail: str) -> None:
        super().__init__(f"Malformed card schema file {path}: {detail}")


def _load_cards_payload(path: Traversable) -> dict:
    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise CardConfigError(path, "schema file must be valid UTF-8") from error
    try:
        payload = yaml.safe_load(raw)
    except yaml.YAMLError as error:
        raise CardConfigError(path, "schema file must be valid YAML") from error
    if not isinstance(payload, dict):
        raise CardConfigError(path, "schema file must contain a mapping")
    return payload


def load_card_system_config(vault: Path | None = None, project_id: str | None = None) -> CardSystemConfig:
    if vault is not None:
        candidates = [vault / "_meta" / _CARDS_SCHEMA_PATH]
        if project_id is not None:
            candidates.insert(0, vault / "projects" / project_id / "_meta" / _CARDS_SCHEMA_PATH)
        for candidate in candidates:
            if candidate.is_file():
                payload = _load_cards_payload(candidate)
                try:
                    return CardSystemConfig.model_validate(payload)
                except ValidationError as error:
                    raise CardConfigError(candidate, f"schema validation failed: {error}") from error

    default_schema = resources.files("agent_memory.defaults").joinpath(_CARDS_SCHEMA_PATH)
    payload = _load_cards_payload(default_schema)
    try:
        return CardSystemConfig.model_validate(payload)
    except ValidationError as error:
        raise CardConfigError(default_schema, f"schema validation failed: {error}") from error


def load_card_models() -> dict[str, type[BaseModel]]:
    return build_card_models(load_card_system_config())
