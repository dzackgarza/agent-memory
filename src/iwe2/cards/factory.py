from __future__ import annotations

from pydantic import BaseModel

from iwe2.cards.config import CardSystemConfig


def build_card_models(config: CardSystemConfig) -> dict[str, type[BaseModel]]:
    raise NotImplementedError
