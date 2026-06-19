from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from iwe2.cards.config import CardSystemConfig, CardTypeSpec
from iwe2.cards.storage import card_type_for_id, split_card


@dataclass(frozen=True)
class CardRecord:
    type_name: str
    path: Path
    metadata: dict[str, object]


@dataclass(frozen=True)
class Problem:
    kind: str
    card_id: str
    detail: str


def wikilink_ids(value: object) -> list[str]:
    items = value if isinstance(value, list) else [value]
    ids: list[str] = []
    for item in items:
        assert isinstance(item, str), "wikilink value must be a string"
        text = item.strip()
        if text.startswith("[[") and text.endswith("]]"):
            text = text[2:-2]
        ids.append(text)
    return ids


def load_card_records(
    plans_roots: Sequence[Path],
    config: CardSystemConfig,
    models: dict[str, type[BaseModel]],
) -> dict[str, CardRecord]:
    # Load every card across all given project plan roots into one id-keyed map; building
    # the index vault-wide is what lets cross-project [[ID]] references resolve.
    records: dict[str, CardRecord] = {}
    prefixes = tuple(f"{card_type.id_prefix}-" for card_type in config.card_types)
    for root in plans_roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.md")):
            if not path.stem.startswith(prefixes):
                continue  # skip non-card files such as the generated plan-dag.md
            metadata, _body = split_card(path.read_text(encoding="utf-8"))
            card_id = path.stem
            assert metadata.get("id") == card_id, f"card id must match filename: {path}"
            assert card_id not in records, f"duplicate card id across vault: {card_id}"
            card_type = card_type_for_id(config, card_id)
            models[card_type.name].model_validate(metadata)
            records[card_id] = CardRecord(type_name=card_type.name, path=path, metadata=metadata)
    return records


def dependency_cycles(graph: dict[str, list[str]]) -> list[Problem]:
    color: dict[str, int] = dict.fromkeys(graph, 0)
    stack: list[str] = []
    problems: list[Problem] = []

    def visit(node: str) -> None:
        color[node] = 1
        stack.append(node)
        for nxt in graph[node]:
            if color[nxt] == 1:
                cycle = [*stack[stack.index(nxt) :], nxt]
                problems.append(Problem("cycle", nxt, " -> ".join(cycle)))
            elif color[nxt] == 0:
                visit(nxt)
        stack.pop()
        color[node] = 2

    for node in graph:
        if color[node] == 0:
            visit(node)
    return problems


def reference_field_names(config: CardSystemConfig) -> dict[str, list[str]]:
    return {card_type.name: [field.name for field in card_type.fields if field.type in ("wikilink", "wikilink_list")] for card_type in config.card_types}


def reference_problems(
    card_id: str,
    record: CardRecord,
    ref_field_names: list[str],
    records: dict[str, CardRecord],
) -> list[Problem]:
    problems: list[Problem] = []
    for field_name in ref_field_names:
        for target in wikilink_ids(record.metadata.get(field_name) or []):
            if target not in records:
                problems.append(Problem("reference", card_id, f"{field_name} references missing card: {target}"))
    return problems


def containment_problems(
    card_id: str,
    record: CardRecord,
    card_type: CardTypeSpec,
    records: dict[str, CardRecord],
) -> list[Problem]:
    problems: list[Problem] = []
    for parent_id in wikilink_ids(record.metadata.get("parents") or []):
        if parent_id in records and records[parent_id].type_name not in card_type.parents:
            problems.append(Problem("containment", card_id, f"parent {parent_id} is a {records[parent_id].type_name}, not in allowed {card_type.parents}"))
    return problems


def depends_targets(record: CardRecord, records: dict[str, CardRecord]) -> list[str]:
    return [target for target in wikilink_ids(record.metadata.get("dependsOn") or []) if target in records]


def validate_cards(records: dict[str, CardRecord], config: CardSystemConfig) -> list[Problem]:
    by_type = {card_type.name: card_type for card_type in config.card_types}
    ref_fields = reference_field_names(config)
    problems: list[Problem] = []
    depends: dict[str, list[str]] = {}
    for card_id, record in records.items():
        card_type = by_type[record.type_name]
        problems.extend(reference_problems(card_id, record, ref_fields[record.type_name], records))
        problems.extend(containment_problems(card_id, record, card_type, records))
        depends[card_id] = depends_targets(record, records)
    problems.extend(dependency_cycles(depends))
    return problems
