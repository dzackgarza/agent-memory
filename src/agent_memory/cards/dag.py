from __future__ import annotations

from agent_memory.cards.config import CardSystemConfig
from agent_memory.cards.validation import CardRecord, ordered_child_fields, wikilink_ids
from agent_memory.models import ArchiveVisibility

# Filename of the generated plan dependency/containment DAG. It is a rendered artifact,
# not a card or a memory note, so enumerators that read frontmatter must skip it.
PLAN_DAG_FILENAME = "plan-dag.md"
PLAN_DAG_FILENAMES = frozenset((PLAN_DAG_FILENAME, "plan-dag-archived.md", "plan-dag-all.md"))


def plan_dag_filename(visibility: ArchiveVisibility) -> str:
    return {
        ArchiveVisibility.ACTIVE: PLAN_DAG_FILENAME,
        ArchiveVisibility.ARCHIVED: "plan-dag-archived.md",
        ArchiveVisibility.ALL: "plan-dag-all.md",
    }[visibility]


def mermaid_block(title: str, nodes: list[str], edges: list[str]) -> str:
    lines = ["graph LR", *[f"  {node}" for node in nodes], *edges]
    return f"## {title}\n\n```mermaid\n" + "\n".join(lines) + "\n```\n"


def dependency_edges_for(records: dict[str, CardRecord], nodes: list[str]) -> list[str]:
    edges: list[str] = []
    for card_id in nodes:
        for prerequisite in wikilink_ids(records[card_id].metadata.get("dependsOn") or []):
            if prerequisite in records:
                edges.append(f"  {prerequisite} --> {card_id}")
    return edges


def sequence_edges_for(records: dict[str, CardRecord], config: CardSystemConfig) -> list[str]:
    edges: list[str] = []
    order_fields = ordered_child_fields(config)
    for parent_id in sorted(records):
        ordering = order_fields.get(records[parent_id].type_name)
        if ordering is None:
            continue
        child_type, field_name = ordering
        declared = wikilink_ids(records[parent_id].metadata.get(field_name) or [])
        visible = [child_id for child_id in declared if child_id in records and records[child_id].type_name == child_type]
        edges.extend(f"  {previous_id} --> {child_id}" for previous_id, child_id in zip(visible, visible[1:], strict=False))
    return edges


def containment_edges_for(records: dict[str, CardRecord], nodes: list[str]) -> list[str]:
    edges: list[str] = []
    for card_id in nodes:
        for parent in wikilink_ids(records[card_id].metadata.get("parents") or []):
            if parent in records:
                edges.append(f"  {parent} --> {card_id}")
    return edges


def render_dag(records: dict[str, CardRecord], config: CardSystemConfig) -> str:
    nodes = sorted(records)
    sequence_edges = sequence_edges_for(records, config)
    dependency_edges = dependency_edges_for(records, nodes)
    containment_edges = containment_edges_for(records, nodes)
    return (
        mermaid_block("Sequence", nodes, sequence_edges)
        + "\n"
        + mermaid_block("Dependencies", nodes, dependency_edges)
        + "\n"
        + mermaid_block("Containment", nodes, containment_edges)
    )
