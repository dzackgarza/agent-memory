from __future__ import annotations

from pathlib import Path

import pytest

from agent_memory.operations import (
    MalformedMemoryError,
    extract_embedded_frontmatter_blocks,
    read_memory,
    reconcile_memory_file,
    reconcile_okf_frontmatter,
)


PROJECT_ID = "github.com__dzackgarza__agent-memory"
OKF_VALUES = {
    "type": "plan",
    "description": "Preserve normalization-owned OKF metadata.",
    "timestamp": "2026-07-02T00:00:00Z",
    "scope": "project",
    "source": "agent",
    "confidence": "high",
    "promotable": False,
    "project_id": PROJECT_ID,
}
PRIMARY_COLLAPSED_HEADER = {
    "title": "Legacy Double Frontmatter Plan",
    "tags": ["project", "plan"],
}


def double_frontmatter_note(extra: dict[str, object] | None = None) -> str:
    extra_payload = OKF_VALUES if extra is None else extra
    lines = [
        "---",
        "title: Legacy Double Frontmatter Plan",
        "tags:",
        "  - project",
        "  - plan",
        "---",
        "This body should survive normalization.",
        "",
        "---",
    ]
    for key, value in extra_payload.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            lines.extend(f"  - {item}" for item in value)
        elif isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
        else:
            lines.append(f"{key}: {value}")
    lines.extend(["---", "", "Trailing prose should remain."])
    return "\n".join(lines) + "\n"


def assert_okf_fields(metadata: dict[str, object]) -> None:
    for key, value in OKF_VALUES.items():
        assert metadata[key] == value
    assert metadata["title"] == PRIMARY_COLLAPSED_HEADER["title"]
    assert metadata["tags"] == PRIMARY_COLLAPSED_HEADER["tags"]


def test_reconcile_okf_frontmatter_preserves_embedded_okf_fields() -> None:
    reconciled = reconcile_okf_frontmatter(Path("legacy-plan.md"), PRIMARY_COLLAPSED_HEADER, [OKF_VALUES])

    assert_okf_fields(reconciled)


def test_extract_embedded_frontmatter_blocks_removes_only_duplicate_block(tmp_path: Path) -> None:
    document = read_memory_from_text(tmp_path, double_frontmatter_note())
    cleaned_body, extras = extract_embedded_frontmatter_blocks(Path("legacy-plan.md"), document.body)

    assert extras == [OKF_VALUES]
    assert "This body should survive normalization." in cleaned_body
    assert "Trailing prose should remain." in cleaned_body
    assert "project_id:" not in cleaned_body


def test_reconcile_memory_file_writes_single_canonical_okf_header(tmp_path: Path) -> None:
    note_path = tmp_path / "legacy-plan.md"
    note_path.write_text(double_frontmatter_note(), encoding="utf-8")

    reconcile_memory_file(note_path)

    document = read_memory(note_path)
    assert_okf_fields(document.metadata)
    assert "---\ntype: plan" not in document.body
    assert "This body should survive normalization." in document.body


def test_reconcile_memory_file_fails_loudly_without_rewriting_conflicts(tmp_path: Path) -> None:
    note_path = tmp_path / "conflicting-plan.md"
    original = double_frontmatter_note({**OKF_VALUES, "scope": "global"})
    note_path.write_text(original, encoding="utf-8")

    with pytest.raises(MalformedMemoryError, match="conflicting values for scope"):
        reconcile_memory_file(note_path)

    assert note_path.read_text(encoding="utf-8") == original


def test_reconcile_memory_file_fails_loudly_on_unknown_extra_key(tmp_path: Path) -> None:
    note_path = tmp_path / "unknown-extra.md"
    original = double_frontmatter_note({**OKF_VALUES, "legacy_status": "active"})
    note_path.write_text(original, encoding="utf-8")

    with pytest.raises(MalformedMemoryError, match="unreconcilable extra frontmatter key: legacy_status"):
        reconcile_memory_file(note_path)

    assert note_path.read_text(encoding="utf-8") == original


def read_memory_from_text(tmp_path: Path, text: str):
    note_path = tmp_path / "okf-reconciliation-inline.md"
    note_path.write_text(text, encoding="utf-8")
    return read_memory(note_path)
