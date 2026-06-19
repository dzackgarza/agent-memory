from __future__ import annotations

from pathlib import Path

from iwe2.operations import OKF_VERSION, remove_index_link, render_memory


def test_remove_index_link_drops_emptied_list_block(tmp_path: Path) -> None:
    # Removing the sole entry of an index list must delete the whole List block, not
    # leave a stray "*" bullet behind (the apply_remove_item empty-section branch).
    body = "# Decisions\n\n# Concepts\n\n* [Only Note](only-note.md) - the sole entry\n"
    index = tmp_path / "index.md"
    index.write_text(render_memory({"okf_version": OKF_VERSION}, body), encoding="utf-8")

    remove_index_link(index, "Only Note")

    rewritten = index.read_text(encoding="utf-8")
    assert "only-note.md" not in rewritten
    # the body region (after frontmatter) must contain no bullet at all
    region = rewritten.split("---\n", 2)[-1]
    assert "*" not in region


def test_remove_index_link_keeps_block_with_remaining_items(tmp_path: Path) -> None:
    # Removing the first of two entries must keep the List block and the surviving
    # entry, then strip the BlankLine now trailing the new final item so the list
    # does not end with a loose-list gap (the apply_remove_item if-items branch).
    body = "# Decisions\n\n# Concepts\n\n* [First](first.md) - one\n\n* [Second](second.md) - two\n"
    index = tmp_path / "index.md"
    index.write_text(render_memory({"okf_version": OKF_VERSION}, body), encoding="utf-8")

    remove_index_link(index, "First")

    rewritten = index.read_text(encoding="utf-8")
    region = rewritten.split("---\n", 2)[-1]
    assert "first.md" not in region
    assert "[Second](second.md)" in region
    # the surviving final item must not leave a trailing blank-line gap
    assert region.rstrip("\n").endswith("[Second](second.md) - two")
