import pytest
from pathlib import Path

from agent_memory.operations import (
    locate_index_link,
    remove_index_link,
    remove_index_link_by_target,
    MemoryOperationError,
)

def test_locate_and_remove_index_links_ignores_prose(tmp_path: Path):
    index_file = tmp_path / "index.md"
    content = """# Index

Here is a paragraph that mentions [Test Link](test-link.md) in prose.

## [Test Link](test-link.md)

Here is a list of links:
- [Test Link](test-link.md) - The actual test link.
- [Other Link](other-link.md) - Something else.
"""
    index_file.write_text(content, encoding="utf-8")

    # Locate link
    lines, idx = locate_index_link(index_file, "Test Link")
    assert idx is not None
    # Ensure it found the one in the list, which is line index 8 (0-indexed: # Index is 0)
    # 0: # Index
    # 1: 
    # 2: Here is a paragraph...
    # 3: 
    # 4: ## [Test Link]...
    # 5: 
    # 6: Here is a list...
    # 7: - [Test Link](test-link.md) - The actual test link.
    assert "The actual test link" in lines[idx]

    # Remove link by title
    remove_index_link(index_file, "Test Link")
    new_content = index_file.read_text(encoding="utf-8")
    assert "- [Test Link](test-link.md)" not in new_content
    # The prose and heading links should remain untouched
    assert "mentions [Test Link](test-link.md) in prose" in new_content
    assert "## [Test Link](test-link.md)" in new_content
    assert "- [Other Link]" in new_content

def test_remove_index_link_by_target_ignores_prose(tmp_path: Path):
    index_file = tmp_path / "index.md"
    content = """# Index

Prose with [Hidden Link](target.md).

### [Hidden Link](target.md)

- [Visible Link](target.md) - In a list.
"""
    index_file.write_text(content, encoding="utf-8")

    # Remove link by target
    remove_index_link_by_target(index_file, "target.md")
    new_content = index_file.read_text(encoding="utf-8")
    
    assert "- [Visible Link](target.md)" not in new_content
    # Prose and heading links must remain untouched
    assert "Prose with [Hidden Link](target.md)." in new_content
    assert "### [Hidden Link](target.md)" in new_content

def test_locate_index_link_multiple_matches_in_list(tmp_path: Path):
    index_file = tmp_path / "index.md"
    content = """# Index
- [Dupe](target1.md)
- [Dupe](target2.md)
"""
    index_file.write_text(content, encoding="utf-8")

    with pytest.raises(MemoryOperationError) as exc_info:
        locate_index_link(index_file, "Dupe")
    assert "contains multiple links for title: Dupe" in str(exc_info.value)

def test_locate_index_link_no_matches(tmp_path: Path):
    index_file = tmp_path / "index.md"
    content = """# Index
Prose [Link](target.md)
"""
    index_file.write_text(content, encoding="utf-8")

    lines, idx = locate_index_link(index_file, "Link")
    assert idx is None

def test_remove_index_link_by_target_no_matches(tmp_path: Path):
    index_file = tmp_path / "index.md"
    content = """# Index
Prose [Link](target.md)
"""
    index_file.write_text(content, encoding="utf-8")

    # Should safely do nothing and not remove the prose link
    remove_index_link_by_target(index_file, "target.md")
    assert "Prose [Link](target.md)" in index_file.read_text(encoding="utf-8")
