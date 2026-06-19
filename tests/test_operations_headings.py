from __future__ import annotations

import pytest

from iwe2.operations import first_heading_title


def test_first_heading_title_skips_non_top_level_headings() -> None:
    # a leading subheading must not be mistaken for the document title; the scan
    # continues to the first level-one heading.
    assert first_heading_title("## Sub\n\n# Real\n") == "Real"


def test_first_heading_title_requires_a_top_level_heading() -> None:
    # a document with only subheadings has no title and must fail loudly.
    with pytest.raises(AssertionError):
        first_heading_title("## only\n")
