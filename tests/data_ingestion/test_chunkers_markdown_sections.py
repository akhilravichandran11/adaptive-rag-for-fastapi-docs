"""
Offline tests for the ``markdown_sections`` and ``markdown_headers`` chunkers.

Small synthetic markdown only: no corpus, model or network. Both chunkers are built
through the registry (``get_chunker``), and each test checks one of a strategy's
rules by what ``run(documents)`` returns.

    uv run pytest tests/data_ingestion/test_chunkers_markdown_sections.py
"""

import importlib
import re
import sys
import textwrap
from pathlib import Path

import pytest
from haystack import Document

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from data_ingestion.chunkers import STRATEGIES, get_chunker  # noqa: E402

SOURCE = "docs/tutorial__body.md"
PARENT_META = {"source_path": SOURCE, "bucket": "tutorial"}
SHARED_FIELDS = {
    "strategy",
    "chunk_index",
    "chunk_count",
    "source_path",
    "chunk_size",
    "chunk_overlap",
    "chunk_unit",
}
FENCE_LINE_RE = re.compile(r"^(?:`{3,}|~{3,})", re.MULTILINE)


def paragraph(topic: str, length: int) -> str:
    """One paragraph of about ``length`` characters, wrapped like markdown source."""
    sentence = f"The {topic} paragraph explains one idea about handling a request. "
    text = (sentence * (length // len(sentence) + 1))[:length].rsplit(" ", 1)[0] + "."
    return textwrap.fill(text, width=80)


def document(*blocks: str, source: str = SOURCE) -> Document:
    return Document(content="\n\n".join(blocks) + "\n", meta={**PARENT_META, "source_path": source})


def without_whitespace(text: str) -> str:
    return "".join(text.split())


def fences_balanced(text: str) -> bool:
    return len(FENCE_LINE_RE.findall(text)) % 2 == 0


@pytest.fixture(scope="module")
def sections():
    return get_chunker("markdown_sections")


@pytest.fixture(scope="module")
def headers():
    return get_chunker("markdown_headers")


# --- registry ----------------------------------------------------------------


def test_both_are_registered_after_markdown_ast() -> None:
    assert STRATEGIES[STRATEGIES.index("markdown_ast") + 1 :] == (
        "markdown_sections",
        "markdown_headers",
    )


def test_importing_the_package_loads_neither_module(monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-import the project's modules in this process; a fresh interpreter times out under load."""
    for name in [n for n in sys.modules if n.split(".")[0] == "data_ingestion"]:
        monkeypatch.delitem(sys.modules, name)  # restored after the test

    importlib.import_module("data_ingestion.chunkers")

    assert "data_ingestion.chunkers.markdown_sections" not in sys.modules
    assert "data_ingestion.chunkers.markdown_headers" not in sys.modules


# --- markdown_sections ---------------------------------------------------------


def test_chunks_carry_the_shared_fields_and_a_heading_path(sections) -> None:
    parents = [
        document("# A { #a }", paragraph("a", 500), "## B { #b }", paragraph("b", 500)),
        document("# Other", paragraph("other", 500), source="docs/other.md"),
    ]
    chunks = sections.run(parents)["documents"]

    assert [c.meta["chunk_index"] for c in chunks] == [0, 1, 0]
    for chunk in chunks:
        assert set(chunk.meta) == set(PARENT_META) | SHARED_FIELDS | {"heading_path"}
        assert chunk.meta["strategy"] == "markdown_sections"
        assert chunk.meta["chunk_unit"] == "char"


def test_the_heading_path_is_metadata_only_and_has_no_anchors(sections) -> None:
    parent = document(
        "# Tutorial { #tutorial }",
        paragraph("tutorial", 500),
        "## Body { #body }",
        paragraph("body", 500),
        "### Nested Models { #nested-models }",
        paragraph("nested", 500),
        "## Files { #files }",
        "Upload a file.",
        "## Forms { #forms }",
        paragraph("forms", 500),
    )
    chunks = sections.run([parent])["documents"]

    # The last chunk joins "Files" and "Forms", so it is labelled with the heading they share.
    assert [c.meta["heading_path"] for c in chunks] == [
        "Tutorial",
        "Tutorial > Body",
        "Tutorial > Body > Nested Models",
        "Tutorial",
    ]
    assert chunks[2].content.startswith("### Nested Models { #nested-models }")
    assert chunks[3].content.startswith("## Files { #files }")
    assert "".join(c.content for c in chunks) == parent.content


def test_a_short_section_joins_the_next_under_their_shared_headings(sections) -> None:
    parent = document(
        "# Responses",
        paragraph("intro", 500),
        "## HTMLResponse",
        "Takes some text and returns HTML.",
        "## PlainTextResponse",
        paragraph("plain", 500),
    )
    chunks = sections.run([parent])["documents"]

    assert len(chunks) == 2
    assert chunks[1].meta["heading_path"] == "Responses"
    assert "## HTMLResponse" in chunks[1].content
    assert "## PlainTextResponse" in chunks[1].content


def test_a_short_last_section_joins_the_one_before(sections) -> None:
    parent = document("# A", paragraph("a", 500), "## Version", "Available since 0.67.0.")
    chunks = sections.run([parent])["documents"]

    assert len(chunks) == 1
    assert chunks[0].meta["heading_path"] == "A"
    assert "Available since 0.67.0." in chunks[0].content


def test_a_short_section_is_not_joined_to_one_that_must_be_split(sections) -> None:
    # Joined, the long section's pieces would be labelled only "WebSockets".
    long_section = "\n\n".join(paragraph(f"client {i}", 450) for i in range(6))
    parent = document("# WebSockets", "Intro.", "## Client", long_section)
    chunks = sections.run([parent])["documents"]

    assert chunks[0].meta["heading_path"] == "WebSockets"
    assert len(chunks) >= 3
    assert {c.meta["heading_path"] for c in chunks[1:]} == {"WebSockets > Client"}


def test_a_short_document_is_one_chunk(sections) -> None:
    chunks = sections.run([document("# Tiny", "One line.")])["documents"]

    assert len(chunks) == 1
    assert chunks[0].meta["heading_path"] == "Tiny"


def test_a_long_section_splits_between_paragraphs_and_loses_nothing(sections) -> None:
    paragraphs = [paragraph(f"topic {i}", 450) for i in range(6)]
    parent = document("# Long", *paragraphs)
    chunks = sections.run([parent])["documents"]

    assert len(chunks) >= 2
    assert {c.meta["heading_path"] for c in chunks} == {"Long"}
    assert all(len(c.content) <= 2_000 for c in chunks)
    assert without_whitespace("".join(c.content for c in chunks)) == without_whitespace(
        parent.content
    )


def test_no_chunk_cuts_through_a_code_fence(sections) -> None:
    # The blank line inside the fence is where a splitter that ignored fences would cut.
    code = "".join(f"value_{i} = {i}\n" for i in range(60)) + "\n" + "result = 1\n" * 60
    block = f"```python\n{code}```"
    parent = document("# Mixed", paragraph("one", 1_600), block, paragraph("two", 700))
    chunks = sections.run([parent])["documents"]

    assert len(chunks) >= 2
    assert all(fences_balanced(c.content) for c in chunks)
    assert sum(block in c.content for c in chunks) == 1


def test_a_paragraph_stays_with_the_code_block_after_it(sections) -> None:
    lead_in = paragraph("lead-in", 300)
    block = "```python\n" + "".join(f"item_{i} = {i}\n" for i in range(60)) + "```"
    parent = document("# Guide", paragraph("one", 1_200), lead_in, block, paragraph("after", 500))
    chunks = sections.run([parent])["documents"]

    # The lead-in fits after the first paragraph, so packing block by block would cut before the code.
    assert len(chunks[0].content) + len(lead_in) <= 2_000
    assert len(chunks) == 2
    assert chunks[1].content.startswith(f"{lead_in}\n\n{block}")
    assert "".join(c.content for c in chunks) == parent.content


def test_a_paragraph_keeps_its_code_block_across_several_blank_lines(sections) -> None:
    lead_in = paragraph("lead-in", 300)
    block = "```python\n" + "".join(f"item_{i} = {i}\n" for i in range(60)) + "```"
    # The extra "\n" makes two blank lines between the lead-in and the code.
    parent = document(
        "# Guide", paragraph("one", 1_200), lead_in + "\n", block, paragraph("after", 500)
    )
    chunks = sections.run([parent])["documents"]

    assert len(chunks) == 2
    assert chunks[1].content.startswith(f"{lead_in}\n\n\n{block}")
    assert "".join(c.content for c in chunks) == parent.content


def test_a_code_block_right_after_a_heading_is_not_glued_to_it(sections) -> None:
    block = "```python\n" + "".join(f"value_{i} = {i}\n" for i in range(150)) + "```"
    parent = document("# Guide", block, paragraph("after", 500))
    chunks = sections.run([parent])["documents"]

    # The heading and the code do not fit in one chunk, so the cut falls between them.
    assert chunks[1].content.startswith(block)
    assert "".join(c.content for c in chunks) == parent.content


def test_a_code_block_right_after_another_is_not_glued_to_it(sections) -> None:
    first = "```python\n" + "".join(f"first_{i} = {i}\n" for i in range(75)) + "```"
    second = "```python\n" + "".join(f"second_{i} = {i}\n" for i in range(75)) + "```"
    parent = document("# Guide", first, second)
    chunks = sections.run([parent])["documents"]

    # The two blocks do not fit in one chunk, so the cut falls between them.
    assert len(chunks) == 2
    assert chunks[0].content.startswith(f"# Guide\n\n{first}")
    assert chunks[1].content.startswith(second)
    assert "".join(c.content for c in chunks) == parent.content


def test_hash_lines_inside_code_fences_are_not_headings(sections) -> None:
    parent = document(
        "# Settings", paragraph("settings", 300), "```python\n# not a heading\nimport os\n```"
    )
    chunks = sections.run([parent])["documents"]

    assert [c.meta["heading_path"] for c in chunks] == ["Settings"]


def test_text_without_headings_has_no_heading_path(sections) -> None:
    parent = document(paragraph("plain", 500))
    chunks = sections.run([parent])["documents"]

    assert len(chunks) == 1
    assert chunks[0].meta["heading_path"] == ""
    assert chunks[0].content == parent.content


@pytest.mark.parametrize("content", [None, "", "   \n"])
def test_a_document_without_content_produces_no_chunks(sections, content) -> None:
    parent = Document(content=content, meta=dict(PARENT_META))

    assert sections.run([parent])["documents"] == []


def test_two_documents_sharing_a_source_path_are_refused(sections) -> None:
    with pytest.raises(ValueError, match=SOURCE):
        sections.run([document("# A", "a"), document("# B", "b")])


# --- markdown_headers ----------------------------------------------------------


def test_headers_split_at_h1_and_h2_and_keep_deeper_headings_inside(headers) -> None:
    parent = document(
        "# A", paragraph("a", 200), "## B", paragraph("b", 200), "### C", paragraph("c", 200)
    )
    chunks = headers.run([parent])["documents"]

    assert len(chunks) == 2
    assert chunks[1].content.startswith("## B")
    assert "### C" in chunks[1].content
    assert chunks[0].meta["strategy"] == "markdown_headers"
    assert set(chunks[0].meta) == set(PARENT_META) | SHARED_FIELDS


def test_headers_cut_a_long_section_every_300_words(headers) -> None:
    parent = document("# Long", " ".join(f"word{i}" for i in range(700)))
    chunks = headers.run([parent])["documents"]

    assert len(chunks) == 3
    assert " ".join(c.content for c in chunks).split() == parent.content.split()
