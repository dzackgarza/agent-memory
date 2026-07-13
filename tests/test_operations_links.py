from pathlib import Path

import pytest

from agent_memory.operations import (
    MemoryOperationError,
    remove_index_link,
    remove_index_link_by_target,
    replace_index_link,
)


def test_1_frontmatter_is_ignored(tmp_path: Path) -> None:
    index_file = tmp_path / "index.md"
    content = """---
aliases:
  - "[Victim](victim.md)"
---
# Concepts

* [Other](other.md) - Other entry.
"""
    index_file.write_text(content, encoding="utf-8")
    remove_index_link(index_file, "victim.md")
    assert index_file.read_text(encoding="utf-8") == content


def test_2_prose_inside_list_item(tmp_path: Path) -> None:
    index_file = tmp_path / "index.md"
    content = """# Concepts

* Discussion of [Victim](victim.md), not an index entry.
"""
    index_file.write_text(content, encoding="utf-8")
    remove_index_link(index_file, "victim.md")
    assert index_file.read_text(encoding="utf-8") == content


def test_3_secondary_link_in_canonical_entry(tmp_path: Path) -> None:
    index_file = tmp_path / "index.md"
    content = """# Concepts

* [Other](other.md) - See [Victim](victim.md) for background.
"""
    index_file.write_text(content, encoding="utf-8")
    remove_index_link(index_file, "victim.md")
    assert index_file.read_text(encoding="utf-8") == content
    remove_index_link(index_file, "other.md")
    assert "* [Other]" not in index_file.read_text(encoding="utf-8")


def test_4_multiline_item_behavior(tmp_path: Path) -> None:
    index_file = tmp_path / "index.md"
    content = """# Concepts

* [Victim](victim.md) - First line
  continuation line
"""
    index_file.write_text(content, encoding="utf-8")
    remove_index_link(index_file, "victim.md")
    # Multiline items should be ignored as they are not exactly the managed one-line shape
    assert index_file.read_text(encoding="utf-8") == content


def test_5_nested_and_unrelated_list_contexts(tmp_path: Path) -> None:
    index_file = tmp_path / "index.md"
    content = """# Unrelated Section

* [Victim](victim.md) - Should be ignored.

# Concepts

* Parent item
  * [Victim](victim.md) - Nested list ignored.
"""
    index_file.write_text(content, encoding="utf-8")
    remove_index_link(index_file, "victim.md")
    assert index_file.read_text(encoding="utf-8") == content


def test_6_replace_index_link_behavior(tmp_path: Path) -> None:
    index_file = tmp_path / "index.md"
    content = """# Concepts

* [Old Title](old.md) - Old desc
"""
    index_file.write_text(content, encoding="utf-8")
    replace_index_link(index_file, "old.md", "New Title", "new.md", "New desc")
    new_content = index_file.read_text(encoding="utf-8")
    assert "* [New Title](new.md) - New desc" in new_content
    assert "Old Title" not in new_content

    # Appending when missing
    replace_index_link(index_file, "missing.md", "Appended", "appended.md", "Desc")
    assert "* [Appended](appended.md) - Desc" in index_file.read_text(encoding="utf-8")


def test_replace_index_link_preserves_crlf_terminators(tmp_path: Path) -> None:
    index_file = tmp_path / "index.md"
    index_file.write_bytes(b"# Concepts\r\n\r\n* [Old Title](old.md) - Old desc\r\n")

    replace_index_link(index_file, "old.md", "New Title", "new.md", "New desc")

    assert index_file.read_bytes() == b"# Concepts\r\n\r\n* [New Title](new.md) - New desc\r\n"


def test_replace_index_link_appends_after_unterminated_content(tmp_path: Path) -> None:
    index_file = tmp_path / "index.md"
    original = "# Concepts\n\nUnrelated final prose"
    index_file.write_text(original, encoding="utf-8")

    replace_index_link(index_file, "missing.md", "Added", "added.md", "Description")
    assert index_file.read_text(encoding="utf-8") == original + "\n* [Added](added.md) - Description\n"

    remove_index_link(index_file, "added.md")
    assert index_file.read_text(encoding="utf-8") == original + "\n"


def test_replace_index_link_appends_crlf_after_unterminated_crlf_content(tmp_path: Path) -> None:
    index_file = tmp_path / "index.md"
    original = b"# Concepts\r\n\r\nUnrelated final prose"
    index_file.write_bytes(original)

    replace_index_link(index_file, "missing.md", "Added", "added.md", "Description")

    assert index_file.read_bytes() == original + b"\r\n* [Added](added.md) - Description\r\n"


def test_7_duplicate_targets(tmp_path: Path) -> None:
    index_file = tmp_path / "index.md"
    content = """# Concepts

* [Title 1](target.md) - Desc 1
* [Title 2](target.md) - Desc 2
"""
    index_file.write_text(content, encoding="utf-8")
    with pytest.raises(MemoryOperationError):
        remove_index_link(index_file, "target.md")


def test_8_production_syntax(tmp_path: Path) -> None:
    index_file = tmp_path / "index.md"
    content = """# Concepts

* [Victim](victim.md) - Description
"""
    index_file.write_text(content, encoding="utf-8")
    remove_index_link(index_file, "victim.md")
    assert "* [Victim]" not in index_file.read_text(encoding="utf-8")


def test_9_special_title_round_trips(tmp_path: Path) -> None:
    index_file = tmp_path / "index.md"
    content = """# Concepts

* [A *B* `code` [link]](victim.md) - Special characters in title
"""
    index_file.write_text(content, encoding="utf-8")
    replace_index_link(index_file, "victim.md", "New Title", "new.md", "New desc")
    new_content = index_file.read_text(encoding="utf-8")
    assert "* [New Title](new.md) - New desc" in new_content
    assert "A *B*" not in new_content


def test_10_exact_output_assertions(tmp_path: Path) -> None:
    index_file = tmp_path / "index.md"
    content = b"---\nfoo: bar\n---\n# Concepts\n\n* [Victim](victim.md) - Desc\n\nSome prose.\n"
    index_file.write_bytes(content)

    # Try removing a non-existent link
    remove_index_link(index_file, "missing.md")
    assert index_file.read_bytes() == content


def test_11_idempotence(tmp_path: Path) -> None:
    index_file = tmp_path / "index.md"
    content = """# Concepts

* [Victim](victim.md) - Desc
"""
    index_file.write_text(content, encoding="utf-8")
    remove_index_link(index_file, "victim.md")
    content_after = index_file.read_text(encoding="utf-8")
    assert "* [Victim]" not in content_after

    # Second call should do nothing
    remove_index_link(index_file, "victim.md")
    assert index_file.read_text(encoding="utf-8") == content_after


def test_remove_index_link_by_target(tmp_path: Path) -> None:
    index_file = tmp_path / "index.md"
    content = """# Concepts

* [Victim](victim.md) - Desc
"""
    index_file.write_text(content, encoding="utf-8")
    remove_index_link_by_target(index_file, "victim.md")
    assert "* [Victim]" not in index_file.read_text(encoding="utf-8")
