"""
Offline tests for the ``markdown_ast`` chunking strategy.

They run on small synthetic markdown built here and never touch the corpus, a
model, or the network. They go through the public seams only — the strategy
registry (``get_chunker("markdown_ast")``) and the chunker's ``run(documents)`` —
and check each of the strategy's rules (D26) by what comes out:

1. sections follow the headings, and the heading path is metadata, not text;
2. a Python block that fits stays whole; one that does not is split by its syntax
   tree, between top-level statements and between a class's methods, never inside
   a function;
3. any other code block is never split;
4. a section over 2,000 characters is split at paragraph breaks, never inside a
   code fence.

Rule 5 (no token target) has nothing to test. A block kept whole above 2,000
characters is checked in the log, where the strategy reports it with its source,
size and reason. Every Python piece is checked to parse on its own with ``ast``.

    uv run pytest tests/data_ingestion/test_chunkers_markdown_ast.py
"""

import ast
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from haystack import Document

from data_ingestion.chunkers import get_chunker, strategy_names

PROJECT_ROOT = Path(__file__).resolve().parents[2]

LOGGER = "data_ingestion.chunkers.markdown_ast"
LIMIT = 2_000
SOURCE = "docs/tutorial__body.md"
PARENT_META = {
    "source_path": SOURCE,
    "bucket": "tutorial",
    "url": "https://example.invalid/tutorial/body/",
    "overall_quality": 4.5,
}
SHARED_FIELDS = {
    "strategy",
    "chunk_index",
    "chunk_count",
    "source_path",
    "chunk_size",
    "chunk_overlap",
    "chunk_unit",
}

# A fence line as the strategy reads one: three or more backticks or tildes at column 0.
FENCE_LINE_RE = re.compile(r"^(?:`{3,}|~{3,})", re.MULTILINE)
FENCE_RE = re.compile(r"^(`{3,}|~{3,})(.*)$")


# --- fixture builders --------------------------------------------------------


def paragraph(topic: str, length: int) -> str:
    """One paragraph of about ``length`` characters, wrapped like markdown source."""
    sentence = f"The {topic} paragraph explains one idea about handling a request. "
    text = (sentence * (length // len(sentence) + 1))[:length].rsplit(" ", 1)[0] + "."
    return textwrap.fill(text, width=80)


def python_function(name: str, statements: int) -> str:
    lines = [f"def {name}(request):", f'    """Handle {name}."""']
    lines += [
        f"    value_{i} = request.query_params.get('key_{i}', {i})" for i in range(statements)
    ]
    lines.append("    return value_0")
    return "\n".join(lines) + "\n"


def python_method(number: int, statements: int) -> str:
    lines = [f"    def method_{number}(self, request):"]
    lines += [
        f"        value_{i} = request.headers.get('x-{number}-{i}', {i})" for i in range(statements)
    ]
    lines.append("        return value_0")
    return "\n".join(lines) + "\n"


def python_class(name: str, methods: int, statements: int) -> str:
    return f"class {name}:\n" + "".join("\n" + python_method(m, statements) for m in range(methods))


def fenced(lang: str, body: str, fence: str = "```") -> str:
    """A fenced code block; ``body`` ends with a newline."""
    return f"{fence}{lang}\n{body}{fence}"


def document(*blocks: str, meta: dict | None = None) -> Document:
    return Document(content="\n\n".join(blocks) + "\n", meta=dict(meta or PARENT_META))


# --- checks ------------------------------------------------------------------


def fences_balanced(text: str) -> bool:
    """True when ``text`` has an even number of fence lines, so each fence it opens it closes.

    Counted rather than tracked line by line (C003); no fixture here nests one
    fence inside another.
    """
    return len(FENCE_LINE_RE.findall(text)) % 2 == 0


def fenced_bodies(text: str, lang: str) -> list[str]:
    """The bodies of the code blocks tagged ``lang`` in ``text``, in order."""
    pattern = re.compile(
        rf"^(`{{3,}}|~{{3,}}){re.escape(lang)}\n(.*?)^\1[ \t]*$", re.MULTILINE | re.DOTALL
    )
    return [match.group(2) for match in pattern.finditer(text)]


def without_whitespace(text: str) -> str:
    return "".join(text.split())


def containing(chunks: list[Document], text: str) -> list[Document]:
    return [chunk for chunk in chunks if text in (chunk.content or "")]


def logged(caplog: pytest.LogCaptureFixture, *parts: str) -> bool:
    """True when one record from the strategy's logger mentions every one of ``parts``."""
    return any(
        all(part in record.getMessage() for part in parts)
        for record in caplog.records
        if record.name == LOGGER
    )


@pytest.fixture(scope="module")
def chunker():
    return get_chunker("markdown_ast")


def run(chunker, *documents: Document) -> list[Document]:
    return chunker.run(list(documents))["documents"]


# --- registry ----------------------------------------------------------------


def test_markdown_ast_is_registered_after_semantic() -> None:
    names = strategy_names()
    assert "markdown_ast" in names
    assert names.index("markdown_ast") > names.index("semantic")


def test_importing_the_package_does_not_load_the_markdown_ast_module() -> None:
    code = (
        "import sys\n"
        "import data_ingestion.chunkers\n"
        "assert 'data_ingestion.chunkers.markdown_ast' not in sys.modules\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=PROJECT_ROOT, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


# --- shared fields and rule 1: headings --------------------------------------


def sectioned() -> Document:
    return document(
        "# Tutorial { #tutorial }",
        paragraph("tutorial", 300),
        "## Body { #body }",
        paragraph("body", 300),
        "### Nested Models { #nested-models }",
        paragraph("nested", 300),
    )


def test_chunks_carry_the_shared_fields_and_a_heading_path(chunker) -> None:
    other_meta = {**PARENT_META, "source_path": "docs/other.md"}
    chunks = run(
        chunker, sectioned(), document("# Other", paragraph("other", 200), meta=other_meta)
    )

    first = [c for c in chunks if c.meta["source_path"] == SOURCE]
    second = [c for c in chunks if c.meta["source_path"] == "docs/other.md"]
    assert [c.meta["chunk_index"] for c in first] == [0, 1, 2]
    assert [c.meta["chunk_index"] for c in second] == [0]
    assert {c.meta["chunk_count"] for c in first} == {3}

    for chunk in chunks:
        assert set(chunk.meta) == set(PARENT_META) | SHARED_FIELDS | {"heading_path"}
        assert chunk.meta["strategy"] == "markdown_ast"
        assert chunk.meta["chunk_size"] == LIMIT
        assert chunk.meta["chunk_overlap"] == 0
        assert chunk.meta["chunk_unit"] == "char"
        assert chunk.meta["bucket"] == "tutorial"


def test_each_heading_starts_a_chunk_and_its_path_is_metadata_only(chunker) -> None:
    chunks = run(chunker, sectioned())

    assert [c.meta["heading_path"] for c in chunks] == [
        "Tutorial",
        "Tutorial > Body",
        "Tutorial > Body > Nested Models",
    ]
    # The heading line itself stays in the text, exactly as written; the path does not.
    assert chunks[1].content.startswith("## Body { #body }")
    assert all(" > " not in c.content for c in chunks)


def test_an_empty_heading_is_kept_with_the_section_after_it(chunker) -> None:
    parent = document("# Title { #title }", "## First { #first }", paragraph("first", 200))
    chunks = run(chunker, parent)

    assert [c.meta["heading_path"] for c in chunks] == ["Title > First"]
    assert without_whitespace(chunks[0].content) == without_whitespace(parent.content)


def test_a_heading_that_is_only_an_anchor_adds_nothing_to_the_path(chunker) -> None:
    parent = document("# A { #a }", paragraph("a", 200), "## { #x }", paragraph("x", 200))

    assert [c.meta["heading_path"] for c in run(chunker, parent)] == ["A", "A"]


def test_text_without_headings_is_one_chunk_with_an_empty_path(chunker) -> None:
    parent = document(paragraph("plain", 400))
    chunks = run(chunker, parent)

    assert len(chunks) == 1
    assert chunks[0].meta["heading_path"] == ""
    assert chunks[0].content == parent.content


def test_hash_lines_inside_code_fences_are_not_headings(chunker) -> None:
    python = "# This comment is not a heading\n## Neither is this\nimport os\n"
    shell = "# install the package\npip install fastapi\n"
    parent = document(
        "# Settings { #settings }",
        paragraph("settings", 200),
        fenced("python", python),
        fenced("bash", shell, fence="~~~"),
        paragraph("after", 200),
    )
    chunks = run(chunker, parent)

    assert len(chunks) == 1
    assert chunks[0].meta["heading_path"] == "Settings"
    assert chunks[0].content == parent.content


# --- rule 4: long sections split at paragraphs --------------------------------


def test_a_long_section_splits_between_paragraphs_and_loses_nothing(chunker) -> None:
    paragraphs = [paragraph(f"topic {i}", 450) for i in range(6)]
    parent = document("# Long { #long }", *paragraphs)
    chunks = run(chunker, parent)

    assert len(chunks) >= 2
    assert {c.meta["heading_path"] for c in chunks} == {"Long"}
    assert all(len(c.content) <= LIMIT for c in chunks)
    for chunk in chunks:
        assert chunk.content in parent.content
    for text in paragraphs:
        assert len(containing(chunks, text)) == 1
    assert without_whitespace("".join(c.content for c in chunks)) == without_whitespace(
        parent.content
    )


def test_a_section_under_the_limit_is_not_split(chunker) -> None:
    parent = document("# Short { #short }", *(paragraph(f"short {i}", 300) for i in range(5)))
    assert len(parent.content) <= LIMIT

    assert len(run(chunker, parent)) == 1


def test_a_paragraph_over_the_limit_stays_whole_and_is_logged(chunker, caplog) -> None:
    rows = "\n".join(f"| field_{i} | str | The value of field number {i}. |" for i in range(60))
    table = "| Parameter | Type | Description |\n|---|---|---|\n" + rows
    assert len(table) > LIMIT
    parent = document("# Metadata { #metadata }", paragraph("intro", 300), table)

    with caplog.at_level("INFO", logger=LOGGER):
        chunks = run(chunker, parent)

    assert len(containing(chunks, table)) == 1
    assert logged(caplog, SOURCE, "paragraph", str(len(table)))


def test_no_chunk_cuts_through_a_code_fence(chunker) -> None:
    # The fence straddles the 2,000 mark, and the blank line inside it is where a
    # splitter that ignored fences would cut.
    shell = (
        "".join(f"# step {i}\nrun --option-{i} value_{i}\n" for i in range(8))
        + "\n"
        + "".join(f"# step {i}\nrun --option-{i} value_{i}\n" for i in range(8, 16))
    )
    block = fenced("bash", shell)
    parent = document("# Mixed { #mixed }", paragraph("one", 1_600), block, paragraph("two", 700))
    chunks = run(chunker, parent)

    assert len(chunks) >= 2
    assert all(fences_balanced(c.content) for c in chunks)
    assert len(containing(chunks, block)) == 1


def test_an_unclosed_code_block_over_the_limit_is_kept_whole_and_logged(chunker, caplog) -> None:
    output = "".join(f"line {i} of output from a fence that never closes\n" for i in range(60))
    parent = Document(
        content="# Output { #output }\n\nIntro.\n\n```console\n" + output,
        meta=dict(PARENT_META),
    )

    with caplog.at_level("INFO", logger=LOGGER):
        chunks = run(chunker, parent)

    assert len(containing(chunks, output)) == 1
    assert logged(caplog, SOURCE, "console code block")


def test_a_block_just_under_the_limit_is_not_logged_as_kept_whole(chunker, caplog) -> None:
    # Exactly 1,999 characters; the blank line after it must not count toward the size.
    body = ("x" * 99 + "\n") * 19 + "y" * 84 + "\n"
    block = fenced("console", body)
    assert len(block) == 1_999
    parent = document("# Near { #near }", paragraph("intro", 600), block, paragraph("after", 600))

    with caplog.at_level("INFO", logger=LOGGER):
        chunks = run(chunker, parent)

    assert len(containing(chunks, block)) == 1
    assert not logged(caplog, "kept")


# --- rule 2: Python blocks ----------------------------------------------------


def test_a_python_block_under_the_limit_stays_in_one_chunk(chunker) -> None:
    # It straddles the 2,000 mark; the blank lines between its two functions are
    # where a splitter that ignored fences would cut.
    block = fenced(
        "python", python_function("read_items", 10) + "\n\n" + python_function("read_user", 10)
    )
    assert 1_000 < len(block) < LIMIT
    parent = document(
        "# Handlers { #handlers }",
        paragraph("before", 1_000),
        block,
        paragraph("after", 600),
    )
    chunks = run(chunker, parent)

    assert len(chunks) >= 2
    assert len(containing(chunks, block)) == 1


@pytest.mark.parametrize("lang", ["python", "py"])
def test_an_oversized_python_block_splits_between_top_level_statements(chunker, lang) -> None:
    functions = [python_function(f"handler_{i}", 9) for i in range(4)]
    cls = python_class("Settings", methods=2, statements=4)
    body = (
        "from fastapi import FastAPI\n\napp = FastAPI()\n\n\n"
        + "\n\n".join(functions)
        + "\n\n"
        + cls
    )
    block = fenced(lang, body)
    assert len(block) > LIMIT
    parent = document("# Handlers { #handlers }", paragraph("intro", 200), block)

    chunks = run(chunker, parent)
    pieces = [b for c in chunks for b in fenced_bodies(c.content, lang)]

    assert len(pieces) >= 2
    assert "".join(pieces) == body
    assert all(fences_balanced(c.content) for c in chunks)
    for piece in pieces:
        ast.parse(piece)  # a cut inside a function leaves a fragment that does not parse
    for unit in (*functions, cls):
        assert len(containing(chunks, unit)) == 1


def test_an_oversized_class_splits_between_its_methods(chunker) -> None:
    cls = python_class("Big", methods=6, statements=8)
    assert len(cls) > LIMIT
    parent = document(
        "# Big class { #big-class }", fenced("python", "from fastapi import Request\n\n\n" + cls)
    )

    chunks = run(chunker, parent)
    pieces = [b for c in chunks for b in fenced_bodies(c.content, "python")]

    assert len(pieces) >= 2
    assert all(fences_balanced(c.content) for c in chunks)
    for piece in pieces:
        ast.parse(piece)
    for number in range(6):
        assert len(containing(chunks, python_method(number, 8))) == 1
    # A piece that starts inside the class repeats its class line, so a method
    # still reads as part of the class.
    assert all("class Big:" in piece for piece in pieces if "def method_" in piece)


def test_a_function_over_the_limit_is_never_cut(chunker) -> None:
    big = python_function("read_everything", 45)
    assert len(big) > LIMIT
    body = "from fastapi import Request\n\n\n" + big + "\n\n" + python_function("helper", 2)
    parent = document("# Big function { #big-function }", fenced("python", body))

    chunks = run(chunker, parent)

    assert len(containing(chunks, big)) == 1
    assert "def helper(" not in containing(chunks, big)[0].content  # the block was split
    assert all(fences_balanced(c.content) for c in chunks)


def test_an_oversized_function_alone_stays_whole_and_is_logged(chunker, caplog) -> None:
    block = fenced("python", python_function("read_everything", 45))
    assert len(block) > LIMIT
    parent = document("# One function { #one-function }", block)

    with caplog.at_level("INFO", logger=LOGGER):
        chunks = run(chunker, parent)

    assert len(containing(chunks, block)) == 1
    assert logged(caplog, SOURCE, "Python block", str(len(block)))


def test_python_that_does_not_parse_stays_whole_and_is_logged(chunker, caplog) -> None:
    # Doc snippets often elide code with "..." in a place Python does not allow it.
    body = "".join(f"commons_{i}: CommonQueryParams ...\n" for i in range(80))
    block = fenced("python", body)
    assert len(block) > LIMIT
    parent = document("# Fragments { #fragments }", block)

    with caplog.at_level("INFO", logger=LOGGER):
        chunks = run(chunker, parent)

    assert len(containing(chunks, block)) == 1
    assert logged(caplog, SOURCE, "parse", str(len(block)))


def test_a_python_block_ending_the_document_splits_cleanly(chunker) -> None:
    # Loading trims the trailing newline, so the closing fence is the last line.
    body = "\n\n".join(python_function(f"handler_{i}", 9) for i in range(6))
    parent = Document(content="# End { #end }\n\n" + fenced("python", body), meta=dict(PARENT_META))

    chunks = run(chunker, parent)
    pieces = [b for c in chunks for b in fenced_bodies(c.content, "python")]

    assert len(pieces) >= 2
    assert "".join(pieces) == body
    assert all(fences_balanced(c.content) for c in chunks)


def test_statements_sharing_a_line_leave_no_empty_code_block(chunker) -> None:
    body = "x = '" + "y" * 2_100 + "'\na = 1; b = 2\n"
    parent = document("# Shared line { #shared-line }", fenced("python", body))

    chunks = run(chunker, parent)
    pieces = [b for c in chunks for b in fenced_bodies(c.content, "python")]

    assert all(piece.strip() for piece in pieces)
    assert "".join(pieces) == body


# --- rule 3: other code blocks -------------------------------------------------


@pytest.mark.parametrize("lang", ["json", "bash", ""])
def test_an_oversized_non_python_block_stays_whole_and_is_logged(chunker, caplog, lang) -> None:
    # Blank lines and "#" lines inside the fence must not become cut points or headings.
    body = "".join(f"# step {i}\nrun --option-{i} value_{i} --flag\n\n" for i in range(50))
    block = fenced(lang, body)
    assert len(block) > LIMIT
    parent = document("# Output { #output }", paragraph("intro", 300), block)

    with caplog.at_level("INFO", logger=LOGGER):
        chunks = run(chunker, parent)

    assert len(containing(chunks, block)) == 1
    assert all(c.meta["heading_path"] == "Output" for c in chunks)
    assert logged(caplog, SOURCE, lang or "untagged", str(len(block)))


# --- input handling ------------------------------------------------------------


@pytest.mark.parametrize("content", ["", None, "   \n\n  "], ids=["empty", "none", "whitespace"])
def test_a_document_without_content_produces_no_chunks(chunker, content) -> None:
    assert run(chunker, Document(content=content, meta=dict(PARENT_META))) == []


def test_two_documents_sharing_a_source_path_are_refused(chunker) -> None:
    with pytest.raises(ValueError, match=SOURCE):
        run(chunker, sectioned(), sectioned())
