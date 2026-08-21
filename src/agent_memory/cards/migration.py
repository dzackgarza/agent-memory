from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from agent_memory.cards.config import CardSystemConfig
from agent_memory.cards.dag import PLAN_DAG_FILENAME
from agent_memory.cards.storage import CardLookupError, CardPlacementError, card_type_for_id, render_card, split_card


def migrate_plans(
    source_plans_root: Path,
    vault_plans_root: Path,
    config: CardSystemConfig,
    models: dict[str, type[BaseModel]],
) -> list[Path]:
    # Ingest an in-repo Nimbalyst card tree into the vault: drop the trackerStatus field
    # (the type now comes from storage location), validate each card against its model, and
    # write it to the mirrored path so the existing hierarchy is preserved verbatim.
    if not source_plans_root.is_dir():
        raise CardLookupError(f"no card tree at {source_plans_root}; point --from at an in-repo directory of card Markdown files, such as .agents/plans")
    migrated: list[Path] = []
    for source in sorted(source_plans_root.rglob("*.md")):
        if source.name == PLAN_DAG_FILENAME:
            continue
        metadata, body = split_card(source.read_text(encoding="utf-8"), source)
        tracker = metadata.pop("trackerStatus", None)
        if isinstance(tracker, dict) and "type" in tracker:
            type_name = tracker["type"]
        else:
            type_name = card_type_for_id(config, source.stem).name
        if type_name not in models:
            raise CardLookupError(f"card {source} declares unknown card type {type_name}; known card types: {', '.join(sorted(models))}")
        models[type_name].model_validate(metadata)
        target = vault_plans_root / source.relative_to(source_plans_root)
        if target.exists():
            raise CardPlacementError(f"card {target} was already migrated; delete the vault copy or migrate a tree that does not overlap it")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_card(metadata, body), encoding="utf-8")
        migrated.append(target)
    if not migrated:
        raise CardLookupError(f"no card files under {source_plans_root}; point --from at a directory holding <PREFIX>-<name>.md card files")
    return migrated
