from __future__ import annotations

from agent_memory.cards.validation import CardRecord, wikilink_ids

# Filename of the generated plan dependency/containment DAG. It is a rendered artifact,
# not a card or a memory note, so enumerators that read frontmatter must skip it.
PLAN_DAG_FILENAME = "plan-dag.md"


def mermaid_block(title: str, nodes: list[str], edges: list[str]) -> str:
    lines = ["graph LR", *[f"  {node}" for node in nodes], *edges]
    return f"## {title}\n\n```mermaid\n" + "\n".join(lines) + "\n```\n"


def dependency_edges_for(records: dict[str, CardRecord], nodes: list[str]) -> list[str]:
    edges: list[str] = []
    for card_id in nodes:
        for target in wikilink_ids(records[card_id].metadata.get("dependsOn") or []):
            if target in records:
                edges.append(f"  {card_id} --> {target}")
    return edges


def containment_edges_for(records: dict[str, CardRecord], nodes: list[str]) -> list[str]:
    edges: list[str] = []
    for card_id in nodes:
        for parent in wikilink_ids(records[card_id].metadata.get("parents") or []):
            if parent in records:
                edges.append(f"  {parent} --> {card_id}")
    return edges


def render_dag(records: dict[str, CardRecord]) -> str:
    nodes = sorted(records)
    dependency_edges = dependency_edges_for(records, nodes)
    containment_edges = containment_edges_for(records, nodes)
    return mermaid_block("Dependencies", nodes, dependency_edges) + "\n" + mermaid_block("Containment", nodes, containment_edges)
