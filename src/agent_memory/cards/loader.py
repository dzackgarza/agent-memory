from __future__ import annotations

from importlib import resources
from pathlib import Path

import yaml
from pydantic import BaseModel

from agent_memory.cards.config import CardSystemConfig
from agent_memory.cards.factory import build_card_models

_CARDS_SCHEMA_PATH = "cards.yaml"


def _load_cards_payload(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8")
    payload = yaml.safe_load(raw)
    assert isinstance(payload, dict), f"cards schema must be a mapping: {path}"
    return payload


def load_card_system_config(vault: Path | None = None, project_id: str | None = None) -> CardSystemConfig:
    if vault is not None:
        candidates = [vault / "_meta" / _CARDS_SCHEMA_PATH]
        if project_id is not None:
            candidates.insert(0, vault / "projects" / project_id / "_meta" / _CARDS_SCHEMA_PATH)
        for candidate in candidates:
            if candidate.is_file():
                payload = _load_cards_payload(candidate)
                return CardSystemConfig.model_validate(payload)

    payload = _load_cards_payload(Path(str(resources.files("agent_memory.defaults").joinpath(_CARDS_SCHEMA_PATH))))
    return CardSystemConfig.model_validate(payload)


def load_card_models() -> dict[str, type[BaseModel]]:
    return build_card_models(load_card_system_config())
