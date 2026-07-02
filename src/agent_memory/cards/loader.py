from __future__ import annotations

from importlib import resources
from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError

from agent_memory.cards.config import CardSystemConfig
from agent_memory.cards.factory import build_card_models


class CardConfigError(ValueError):
    """Raised when the packaged card schema cannot be loaded."""

    def __init__(self, path: Path, detail: str) -> None:
        super().__init__(f"Malformed card schema file {path}: {detail}")


def load_card_system_config() -> CardSystemConfig:
    path = resources.files("agent_memory.defaults").joinpath("cards.yaml")
    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise CardConfigError(Path(str(path)), "schema file must be valid UTF-8") from error
    try:
        payload = yaml.safe_load(raw)
    except yaml.YAMLError as error:
        raise CardConfigError(Path(str(path)), "schema file must be valid YAML") from error
    try:
        return CardSystemConfig.model_validate(payload)
    except ValidationError as error:
        raise CardConfigError(Path(str(path)), f"schema validation failed: {error}") from error


def load_card_models() -> dict[str, type[BaseModel]]:
    return build_card_models(load_card_system_config())
