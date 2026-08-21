from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import yaml
from pydantic import BaseModel

from agent_memory.cards.config import CardSystemConfig, CardTypeSpec


class CardPlacementError(ValueError):
    """Raised when card filesystem placement is invalid for its type."""


class CardLookupError(ValueError):
    """Raised when a card id does not resolve to exactly one card of a configured type."""


class MalformedCardError(ValueError):
    """Raised when a stored card file cannot be read as a card."""


# A field assigned this is being removed, not set: None is a value a card field may hold, so
# it cannot mean "unset". It lives here because operations imports cards, never the reverse.
UNSET_FIELD = "__agent_memory_unset__"


def card_type_for_id(config: CardSystemConfig, card_id: str) -> CardTypeSpec:
    matches = [card_type for card_type in config.card_types if card_id.startswith(f"{card_type.id_prefix}-")]
    if not matches:
        known_prefixes = ", ".join(card_type.id_prefix for card_type in config.card_types)
        raise CardLookupError(f"no card type matches id prefix: {card_id} (known prefixes: {known_prefixes})")
    return max(matches, key=lambda card_type: len(card_type.id_prefix))


def searched_project(plans_root: Path) -> str:
    # Name the project whose card tree was searched, so a miss says where it looked. Vault
    # card trees live at <vault>/projects/<project_id>/<root>; any other root names itself.
    parts = plans_root.parts
    if "projects" in parts and parts.index("projects") + 1 < len(parts):
        return parts[parts.index("projects") + 1]
    return str(plans_root)


def find_card_path(plans_root: Path, card_id: str) -> Path:
    project = searched_project(plans_root)
    if not plans_root.is_dir():
        raise CardLookupError(f"project {project} has no cards yet ({plans_root} does not exist); create its first card with `agent-memory <type> add <ID>` from that project")
    matches = sorted(plans_root.rglob(f"{card_id}.md"))
    if not matches:
        raise CardLookupError(f"no card {card_id} in project {project}; run this command from the repository bound to the project that owns {card_id}")
    if len(matches) > 1:
        found = ", ".join(str(match) for match in matches)
        raise CardLookupError(f"card id {card_id} is ambiguous in project {project}: {found}; delete or rename all but one")
    return matches[0]


def card_file_path(plans_root: Path, card_type: CardTypeSpec, card_id: str, parent_id: str | None) -> Path:
    if parent_id is None:
        if card_type.parents:
            raise CardPlacementError(f"card type {card_type.name} requires --parent for filesystem placement")
        parent_dir = plans_root
        if card_type.container and card_type.container != plans_root.name:
            base = parent_dir / card_type.container
        else:
            base = parent_dir
    else:
        # A parent always lives in the same project as its child, so a lookup miss here is a
        # placement problem, not an invitation to run the command from another project.
        try:
            parent_dir = find_card_path(plans_root, parent_id).parent
        except CardLookupError as error:
            allowed = " or ".join(card_type.parents) or "root"
            project = searched_project(plans_root)
            raise CardPlacementError(f"parent {parent_id} is not a card in project {project}; add it first, or pass --parent an existing {allowed} card id") from error
        base = parent_dir / card_type.container if card_type.container else parent_dir
    if card_type.own_dir:
        return base / card_id / f"{card_id}.md"
    return base / f"{card_id}.md"


def render_card(metadata: dict[str, object], body: str) -> str:
    return f"---\n{yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True)}---\n{body}"


def split_card(text: str, source: Path | None = None) -> tuple[dict[str, object], str]:
    # source is only used to name the offending file in the error; callers that read a card
    # from disk pass it so a hand-edited vault file reports its own path.
    where = f" {source}" if source is not None else ""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise MalformedCardError(f"card{where} does not open with a --- frontmatter line; restore the --- delimited YAML block above the body")
    closing = next((index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"), None)
    if closing is None:
        raise MalformedCardError(f"card{where} frontmatter is never closed; add the --- line that ends the YAML block above the body")
    try:
        metadata = yaml.safe_load("".join(lines[1:closing]))
    except yaml.YAMLError as error:
        raise MalformedCardError(f"card{where} frontmatter is not valid YAML ({error.__class__.__name__}); repair the --- delimited block above the body") from error
    if not isinstance(metadata, dict):
        raise MalformedCardError(f"card{where} frontmatter is a {type(metadata).__name__}, not a mapping; write it as field: value lines")
    return metadata, "".join(lines[closing + 1 :])


def create_card(
    plans_root: Path,
    config: CardSystemConfig,
    models: dict[str, type[BaseModel]],
    *,
    type_name: str,
    card_id: str,
    parent_id: str | None,
    fields: Mapping[str, object],
    body: str,
) -> Path:
    card_type = next((candidate for candidate in config.card_types if candidate.name == type_name), None)
    if card_type is None:
        known = ", ".join(candidate.name for candidate in config.card_types)
        raise CardLookupError(f"unknown card type {type_name}; known card types: {known}")
    if not card_id.startswith(f"{card_type.id_prefix}-"):
        raise CardLookupError(f"card id {card_id} does not match card type {type_name}; give it an id starting with {card_type.id_prefix}-")
    # One parent, one meaning: --parent both places the card and is the containment edge the
    # graph records. An explicit parents assignment wins, so a card whose graph parent differs
    # from its container is still expressible.
    if parent_id is not None and "parents" not in fields and any(field.name == "parents" for field in card_type.fields):
        fields = {**fields, "parents": [f"[[{parent_id}]]"]}
    validated = models[type_name].model_validate({**fields, "id": card_id})
    path = card_file_path(plans_root, card_type, card_id, parent_id)
    if path.exists():
        raise CardPlacementError(f"card {card_id} already exists at {path}; change it with `agent-memory {type_name} update {card_id}` or delete it first")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_card(validated.model_dump(exclude_unset=True), body), encoding="utf-8")
    return path


def read_card(
    plans_root: Path,
    config: CardSystemConfig,
    models: dict[str, type[BaseModel]],
    card_id: str,
) -> BaseModel:
    path = find_card_path(plans_root, card_id)
    metadata, _body = split_card(path.read_text(encoding="utf-8"), path)
    return models[card_type_for_id(config, card_id).name].model_validate(metadata)


def update_card(
    plans_root: Path,
    config: CardSystemConfig,
    models: dict[str, type[BaseModel]],
    card_id: str,
    updates: dict[str, object],
    *,
    body: str | None = None,
) -> Path:
    # A supplied body replaces the stored Markdown in the same write as the field updates, so
    # revising a card is never delete-and-re-add or a hand edit of the vault file.
    path = find_card_path(plans_root, card_id)
    metadata, stored_body = split_card(path.read_text(encoding="utf-8"), path)
    merged = {key: value for key, value in {**metadata, **updates}.items() if value != UNSET_FIELD}
    validated = models[card_type_for_id(config, card_id).name].model_validate(merged)
    path.write_text(render_card(validated.model_dump(exclude_unset=True), stored_body if body is None else body), encoding="utf-8")
    return path


def delete_card(plans_root: Path, card_id: str) -> None:
    find_card_path(plans_root, card_id).unlink()
