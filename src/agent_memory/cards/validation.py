from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from agent_memory.cards.config import CardSystemConfig, CardTypeSpec
from agent_memory.cards.storage import card_file_path, card_type_for_id, split_card

# Ancestor card types whose ids accumulate into a descendant's tags, ported from
# TAGGED_ANCESTOR_TYPES in ~/ai/planning/justfile.
TAGGED_ANCESTOR_TYPES = ("feature", "plan", "phase")

# The ordered child-link field a parent of each type must declare for its children
# of the given child type, ported from validate_sibling_ordering in the source.
ORDERED_CHILD_FIELDS: dict[str, tuple[str, str]] = {
    "feature": ("plan", "plans"),
    "plan": ("phase", "phases"),
    "phase": ("task", "tasks"),
}


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


def parent_ids(record: CardRecord) -> list[str]:
    return wikilink_ids(record.metadata.get("parents") or [])


def children_by_parent(records: dict[str, CardRecord]) -> dict[str, list[str]]:
    # Group every card under each of its (resolvable) containment parents, preserving the
    # id-sorted record order so emitted problems are deterministic. Every record id is a key
    # (empty list when it has no children) so callers use subscript access, never get/default.
    children: dict[str, list[str]] = {card_id: [] for card_id in sorted(records)}
    for card_id in children:
        for parent_id in parent_ids(records[card_id]):
            if parent_id in children:
                children[parent_id].append(card_id)
    return children


def card_status(record: CardRecord) -> str:
    status = record.metadata.get("status")
    assert isinstance(status, str) and status, f"card status must be a non-empty string: {record.path}"
    return status


@dataclass(frozen=True)
class StatusRoles:
    started: set[str]
    complete: set[str]
    unstarted: set[str]


def _unstarted_parent_problems(parent_id: str, parent_status: str, statuses: dict[str, str], roles: StatusRoles) -> list[Problem]:
    if parent_status not in roles.unstarted:
        return []
    started_children = _members_in_role(statuses, roles.started)
    if not started_children:
        return []
    child_id = started_children[0]
    return [Problem("status-hierarchy", parent_id, f"status '{parent_status}' cannot contain started child '{child_id}' status '{statuses[child_id]}'")]


def _members_in_role(statuses: dict[str, str], role: set[str]) -> list[str]:
    return [child_id for child_id, status in statuses.items() if status in role]


def _in_progress_parent_problems(parent_id: str, parent_status: str, statuses: dict[str, str], roles: StatusRoles) -> list[Problem]:
    if parent_status != "in-progress":
        return []
    started_children = _members_in_role(statuses, roles.started)
    unstarted_children = _members_in_role(statuses, roles.unstarted)
    problems: list[Problem] = []
    if len(unstarted_children) == len(statuses):
        child_id = unstarted_children[0]
        detail = f"status '{parent_status}' cannot contain only unstarted children; example child '{child_id}' is '{statuses[child_id]}'"
        problems.append(Problem("status-hierarchy", parent_id, detail))
    if not started_children:
        problems.append(Problem("status-hierarchy", parent_id, f"status '{parent_status}' requires at least one started child"))
    return problems


def _complete_parent_problems(parent_id: str, parent_status: str, statuses: dict[str, str], roles: StatusRoles) -> list[Problem]:
    if parent_status != "complete":
        return []
    incomplete_children = [child_id for child_id, status in statuses.items() if status not in roles.complete]
    if not incomplete_children:
        return []
    child_id = incomplete_children[0]
    return [Problem("status-hierarchy", parent_id, f"status '{parent_status}' cannot be complete while child '{child_id}' is '{statuses[child_id]}'")]


def status_hierarchy_problems(records: dict[str, CardRecord], config: CardSystemConfig) -> list[Problem]:
    # Port of validate_status_hierarchy: a parent's status must be consistent with the
    # workflow roles of its children. Statuses are the iwe2 hyphenated values; the role
    # sets come from unit D's workflow_roles rather than the source's status catalog.
    roles = StatusRoles(config.statuses_with_role("started"), config.statuses_with_role("complete"), config.statuses_with_role("unstarted"))
    problems: list[Problem] = []
    for parent_id, child_ids in children_by_parent(records).items():
        if not child_ids:
            continue  # a card with no children imposes no hierarchy constraint
        parent_status = card_status(records[parent_id])
        statuses = {child_id: card_status(records[child_id]) for child_id in child_ids}
        problems.extend(_unstarted_parent_problems(parent_id, parent_status, statuses, roles))
        problems.extend(_in_progress_parent_problems(parent_id, parent_status, statuses, roles))
        problems.extend(_complete_parent_problems(parent_id, parent_status, statuses, roles))
    return problems


@dataclass(frozen=True)
class OrderedChildren:
    sequence: list[str]
    problems: list[Problem]


def _mismatch_problem(parent_id: str, child_type: str, field_name: str, label: str, ids: list[str]) -> list[Problem]:
    if not ids:
        return []
    return [Problem("sibling-ordering", parent_id, f"'{field_name}' {label} {child_type} ids: {', '.join(ids)}")]


def _difference(items: list[str], exclude: set[str]) -> list[str]:
    return [item for item in items if item not in exclude]


def _reconcile_declared_order(parent_id: str, child_type: str, field_name: str, declared: list[str], children: list[str]) -> OrderedChildren:
    missing = _difference(children, set(declared))
    extras = _difference(declared, set(children))
    problems = _mismatch_problem(parent_id, child_type, field_name, "omits", missing) + _mismatch_problem(parent_id, child_type, field_name, "references non-child", extras)
    if missing or extras:
        return OrderedChildren(children, problems)
    # declared and children are now the same set, so declaration order is the canonical order
    return OrderedChildren(declared, problems)


def ordered_children(parent_id: str, child_type: str, field_name: str, children: list[str], records: dict[str, CardRecord]) -> OrderedChildren:
    # Reconcile a parent's declared ordered child-link field against its actual children of
    # the given type; report omissions/extras and fall back to declaration order. Port of
    # the source's ordered_children closure.
    if len(children) <= 1:
        return OrderedChildren(children, [])
    declared = wikilink_ids(records[parent_id].metadata.get(field_name) or [])
    if not declared:
        return OrderedChildren(children, [Problem("sibling-ordering", parent_id, f"parent with multiple {child_type} children must declare ordered '{field_name}' links")])
    return _reconcile_declared_order(parent_id, child_type, field_name, declared, children)


def _dependson_order_problems(sequence: list[str], records: dict[str, CardRecord]) -> list[Problem]:
    return [
        Problem("sibling-ordering", child_id, f"sibling order requires dependsOn '{previous_id}'")
        for previous_id, child_id in zip(sequence, sequence[1:], strict=False)
        if previous_id not in depends_targets(records[child_id], records)
    ]


def sibling_ordering_problems(records: dict[str, CardRecord]) -> list[Problem]:
    # Port of validate_sibling_ordering: a parent's like-typed children must be declared in
    # the parent's ordered link field and each sibling must dependsOn its predecessor.
    children = children_by_parent(records)
    problems: list[Problem] = []
    for parent_id in sorted(records):
        ordering = ORDERED_CHILD_FIELDS.get(records[parent_id].type_name)
        if ordering is None:
            continue
        child_type, field_name = ordering
        typed = [child_id for child_id in children[parent_id] if records[child_id].type_name == child_type]
        resolved = ordered_children(parent_id, child_type, field_name, typed, records)
        problems.extend(resolved.problems)
        problems.extend(_dependson_order_problems(resolved.sequence, records))
    return problems


def plans_root_for(card_id: str, record: CardRecord, records: dict[str, CardRecord]) -> Path:
    # Derive the project plans root that contains this card. A correctly-placed parent
    # anchors the root for a child; a root feature anchors it from the `features` segment of
    # its own path. Used to feed card_file_path so the canonical layout is config-driven.
    parents = [parent_id for parent_id in parent_ids(record) if parent_id in records]
    if parents:
        return plans_root_for(parents[0], records[parents[0]], records)
    parts = record.path.parts
    assert "features" in parts, f"root card not under a features directory: {record.path}"
    return Path(*parts[: parts.index("features")])


def _filesystem_problem(card_id: str, record: CardRecord, card_type: CardTypeSpec, records: dict[str, CardRecord]) -> Problem | None:
    parents = [parent_id for parent_id in parent_ids(record) if parent_id in records]
    is_root = not card_type.parents
    if not is_root and len(parents) != 1:
        return None  # malformed parent count is reported by the containment check
    parent_id = None if is_root else parents[0]
    expected = card_file_path(plans_root_for(card_id, record, records), card_type, card_id, parent_id).resolve()
    if record.path.resolve() == expected:
        return None
    return Problem("filesystem-hierarchy", card_id, f"expected path {expected}, found {record.path.resolve()}")


def filesystem_hierarchy_problems(records: dict[str, CardRecord], config: CardSystemConfig) -> list[Problem]:
    # Port of validate_filesystem_hierarchy: each card's on-disk path must equal the
    # canonical path computed by card_file_path from its type + single containment parent.
    by_type = {card_type.name: card_type for card_type in config.card_types}
    problems = [_filesystem_problem(card_id, records[card_id], by_type[records[card_id].type_name], records) for card_id in sorted(records)]
    return [problem for problem in problems if problem is not None]


def ancestor_chain(card_id: str, records: dict[str, CardRecord], active: frozenset[str]) -> list[str]:
    # Ordered ancestor ids (nearest-root first) reachable through parents links, mirroring
    # the source ancestor_chain. Cycles are impossible here because validate_cards reports
    # dependency cycles separately and parents form a tree, but guard against revisits.
    assert card_id not in active, f"cycle detected through {card_id}"
    chain: list[str] = []
    for parent_id in parent_ids(records[card_id]):
        if parent_id not in records:
            continue
        for ancestor_id in ancestor_chain(parent_id, records, active | {card_id}):
            if ancestor_id not in chain:
                chain.append(ancestor_id)
        if parent_id not in chain:
            chain.append(parent_id)
    return chain


def tags_from_ancestry_problems(records: dict[str, CardRecord]) -> list[Problem]:
    # Port of the derive-tags logic: a card's tags must equal the chain of its ancestor ids
    # whose type is in TAGGED_ANCESTOR_TYPES.
    problems: list[Problem] = []
    for card_id in sorted(records):
        derived = [ancestor_id for ancestor_id in ancestor_chain(card_id, records, frozenset()) if records[ancestor_id].type_name in TAGGED_ANCESTOR_TYPES]
        tags = records[card_id].metadata.get("tags")
        if derived:
            if tags != derived:
                problems.append(Problem("tags-from-ancestry", card_id, f"tags must equal ancestor chain {derived}, found {tags}"))
        elif tags is not None:
            problems.append(Problem("tags-from-ancestry", card_id, f"card has no tagged ancestors but declares tags {tags}"))
    return problems


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
    problems.extend(status_hierarchy_problems(records, config))
    problems.extend(sibling_ordering_problems(records))
    problems.extend(filesystem_hierarchy_problems(records, config))
    problems.extend(tags_from_ancestry_problems(records))
    return problems
