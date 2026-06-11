"""Unit tests for the heading-aware chunker (spec 0014)."""

from __future__ import annotations

import pytest

from ai_sre.core.knowledge.chunker import Chunker, default_count_tokens


@pytest.mark.unit
def test_blank_document_yields_no_chunks() -> None:
    assert Chunker().chunk("") == []
    assert Chunker().chunk("   \n\n  ") == []


@pytest.mark.unit
def test_no_headings_single_section() -> None:
    chunks = Chunker().chunk("Just a paragraph of prose.\nSecond line.")
    assert len(chunks) == 1
    assert chunks[0].headings == []
    assert chunks[0].ord == 0
    assert "paragraph" in chunks[0].text


@pytest.mark.unit
def test_heading_path_tracks_nesting() -> None:
    doc = (
        "# Runbook\n"
        "intro text\n"
        "## Rollback\n"
        "rollback body\n"
        "### Steps\n"
        "step one\n"
        "## Verification\n"
        "verify body\n"
    )
    chunks = Chunker().chunk(doc)
    paths = [c.headings for c in chunks]
    assert ["Runbook"] in paths
    assert ["Runbook", "Rollback"] in paths
    assert ["Runbook", "Rollback", "Steps"] in paths
    # Verification is a sibling of Rollback (h2), so Steps (h3) is popped.
    assert ["Runbook", "Verification"] in paths


@pytest.mark.unit
def test_ords_are_contiguous_and_ordered() -> None:
    doc = "# A\nbody a\n## B\nbody b\n## C\nbody c\n"
    chunks = Chunker().chunk(doc)
    assert [c.ord for c in chunks] == list(range(len(chunks)))


@pytest.mark.unit
def test_heading_with_no_body_is_skipped() -> None:
    # A heading immediately followed by a deeper heading has no body of its own.
    doc = "# Parent\n## Child\nchild body\n"
    chunks = Chunker().chunk(doc)
    # Only the child section has body text.
    assert len(chunks) == 1
    assert chunks[0].headings == ["Parent", "Child"]


@pytest.mark.unit
def test_long_section_is_split_with_small_target() -> None:
    body = "\n\n".join(f"Paragraph number {i} with several words here." for i in range(20))
    doc = f"# Big\n{body}\n"
    chunks = Chunker(target_tokens=20, overlap_tokens=5).chunk(doc)
    assert len(chunks) > 1
    # Every chunk stays under the heading it belongs to.
    assert all(c.headings == ["Big"] for c in chunks)
    # No chunk wildly exceeds the target (allow some slack for unit packing).
    assert all(c.tokens <= 20 * 3 for c in chunks)


@pytest.mark.unit
def test_fixed_token_mode_ignores_headings() -> None:
    # is_markdown=False: '#' lines are treated as plain text, not headings.
    doc = "# Not A Heading\nsome body\n"
    chunks = Chunker().chunk(doc, is_markdown=False)
    assert len(chunks) == 1
    assert chunks[0].headings == []
    assert "# Not A Heading" in chunks[0].text


@pytest.mark.unit
def test_oversized_single_paragraph_is_word_windowed() -> None:
    para = " ".join(f"word{i}" for i in range(200))
    chunks = Chunker(target_tokens=15, overlap_tokens=3).chunk(para, is_markdown=False)
    assert len(chunks) > 1


@pytest.mark.unit
def test_overlap_must_be_smaller_than_target() -> None:
    with pytest.raises(ValueError):
        Chunker(target_tokens=10, overlap_tokens=10)


@pytest.mark.unit
def test_default_count_tokens_is_positive() -> None:
    assert default_count_tokens("") == 1
    assert default_count_tokens("a" * 40) == 10
