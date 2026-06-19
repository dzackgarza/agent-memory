from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from agent_memory.cards.config import CardSystemConfig
from agent_memory.cards.dag import PLAN_DAG_FILENAME
from agent_memory.cards.storage import card_type_for_id, render_card, split_card


def migrate_plans(
    source_plans_root: Path,
    vault_plans_root: Path,
    config: CardSystemConfig,
    models: dict[str, type[BaseModel]],
) -> list[Path]:
    # Ingest an in-repo Nimbalyst card tree into the vault: drop the trackerStatus field
    # (the type now comes from storage location), validate each card against its model, and
    # write it to the mirrored path so the existing hierarchy is preserved verbatim.
    assert source_plans_root.is_dir(), f"source plans root does not exist: {source_plans_root}"
    migrated: list[Path] = []
    for source in sorted(source_plans_root.rglob("*.md")):
        if source.name == PLAN_DAG_FILENAME:
            continue
        metadata, body = split_card(source.read_text(encoding="utf-8"))
        tracker = metadata.pop("trackerStatus", None)
        if isinstance(tracker, dict) and "type" in tracker:
            type_name = tracker["type"]
        else:
            type_name = card_type_for_id(config, source.stem).name
        assert type_name in models, f"unknown card type during migration: {type_name}"
        models[type_name].model_validate(metadata)
        target = vault_plans_root / source.relative_to(source_plans_root)
        assert not target.exists(), f"migration target already exists: {target}"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_card(metadata, body), encoding="utf-8")
        migrated.append(target)
    assert migrated, "migration found no cards to migrate"
    return migrated
