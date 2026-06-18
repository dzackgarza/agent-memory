from __future__ import annotations

from iwe2.cards.validation import CardRecord, wikilink_ids


def mermaid_block(title: str, nodes: list[str], edges: list[str]) -> str:
    lines = ["graph LR", *[f"  {node}" for node in nodes], *edges]
    return f"## {title}\n\n```mermaid\n" + "\n".join(lines) + "\n```\n"


def render_dag(records: dict[str, CardRecord]) -> str:
    nodes = sorted(records)
    dependency_edges: list[str] = []
    containment_edges: list[str] = []
    for card_id in nodes:
        metadata = records[card_id].metadata
        for target in wikilink_ids(metadata.get("dependsOn") or []):
            if target in records:
                dependency_edges.append(f"  {card_id} --> {target}")
        for parent in wikilink_ids(metadata.get("parents") or []):
            if parent in records:
                containment_edges.append(f"  {parent} --> {card_id}")
    return (
        mermaid_block("Dependencies", nodes, dependency_edges)
        + "\n"
        + mermaid_block("Containment", nodes, containment_edges)
    )
