from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel

from agent_memory.cards.config import CardSystemConfig, CardTypeSpec


def card_type_for_id(config: CardSystemConfig, card_id: str) -> CardTypeSpec:
    matches = [card_type for card_type in config.card_types if card_id.startswith(f"{card_type.id_prefix}-")]
    assert matches, f"no card type matches id prefix: {card_id}"
    return max(matches, key=lambda card_type: len(card_type.id_prefix))


def find_card_path(plans_root: Path, card_id: str) -> Path:
    assert plans_root.is_dir(), f"plans root does not exist: {plans_root}"
    matches = sorted(plans_root.rglob(f"{card_id}.md"))
    assert len(matches) == 1, f"expected exactly one card file for {card_id}, found {len(matches)}"
    return matches[0]


def card_file_path(plans_root: Path, card_type: CardTypeSpec, card_id: str, parent_id: str | None) -> Path:
    if parent_id is None:
        parent_dir = plans_root
        if card_type.container and card_type.container != plans_root.name:
            base = parent_dir / card_type.container
        else:
            base = parent_dir
    else:
        parent_dir = find_card_path(plans_root, parent_id).parent
        base = parent_dir / card_type.container if card_type.container else parent_dir
    if card_type.own_dir:
        return base / card_id / f"{card_id}.md"
    return base / f"{card_id}.md"


def render_card(metadata: dict[str, object], body: str) -> str:
    return f"---\n{yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True)}---\n{body}"


def split_card(text: str) -> tuple[dict[str, object], str]:
    lines = text.splitlines(keepends=True)
    assert lines and lines[0].strip() == "---", "card must start with frontmatter"
    closing = next(index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    metadata = yaml.safe_load("".join(lines[1:closing]))
    assert isinstance(metadata, dict), "card frontmatter must be a mapping"
    return metadata, "".join(lines[closing + 1 :])


def create_card(
    plans_root: Path,
    config: CardSystemConfig,
    models: dict[str, type[BaseModel]],
    *,
    type_name: str,
    card_id: str,
    parent_id: str | None,
    fields: dict[str, object],
    body: str,
) -> Path:
    card_type = next((candidate for candidate in config.card_types if candidate.name == type_name), None)
    assert card_type is not None, f"unknown card type: {type_name}"
    assert card_id.startswith(f"{card_type.id_prefix}-"), f"id {card_id} must start with {card_type.id_prefix}-"
    validated = models[type_name].model_validate({**fields, "id": card_id})
    path = card_file_path(plans_root, card_type, card_id, parent_id)
    assert not path.exists(), f"card already exists: {path}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_card(validated.model_dump(exclude_unset=True), body), encoding="utf-8")
    return path


def read_card(
    plans_root: Path,
    config: CardSystemConfig,
    models: dict[str, type[BaseModel]],
    card_id: str,
) -> BaseModel:
    metadata, _body = split_card(find_card_path(plans_root, card_id).read_text(encoding="utf-8"))
    return models[card_type_for_id(config, card_id).name].model_validate(metadata)


def update_card(
    plans_root: Path,
    config: CardSystemConfig,
    models: dict[str, type[BaseModel]],
    card_id: str,
    updates: dict[str, object],
) -> Path:
    path = find_card_path(plans_root, card_id)
    metadata, body = split_card(path.read_text(encoding="utf-8"))
    validated = models[card_type_for_id(config, card_id).name].model_validate({**metadata, **updates})
    path.write_text(render_card(validated.model_dump(exclude_unset=True), body), encoding="utf-8")
    return path


def delete_card(plans_root: Path, card_id: str) -> None:
    find_card_path(plans_root, card_id).unlink()
