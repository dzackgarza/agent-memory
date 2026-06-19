from __future__ import annotations

from importlib import resources

import yaml
from pydantic import BaseModel

from agent_memory.cards.config import CardSystemConfig
from agent_memory.cards.factory import build_card_models


def load_card_system_config() -> CardSystemConfig:
    payload = yaml.safe_load(resources.files("agent_memory.defaults").joinpath("cards.yaml").read_text(encoding="utf-8"))
    return CardSystemConfig.model_validate(payload)


def load_card_models() -> dict[str, type[BaseModel]]:
    return build_card_models(load_card_system_config())
