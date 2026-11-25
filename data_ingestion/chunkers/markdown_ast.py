"""
markdown_ast chunker: split at the document's headings, never inside a code fence or a function.

Each heading starts a chunk, which records the headings above it as
``heading_path`` (``Tutorial > Body > Nested Models``). A section over 2,000
characters is cut between paragraphs, never inside a code fence. A Python example
too big for one chunk is parsed and cut between its functions, classes and
methods; any other code block stays whole. Haystack does both kinds of parsing —
``MarkdownHeaderSplitter`` for headings, ``PythonCodeSplitter`` (built on
``ast``) for Python — and this module only decides where the paragraph cuts go.

    uv run python -m data_ingestion.chunkers.markdown_ast    # demo, from the project root

Design notes

Rule 1 — headings first, path in metadata only. Text under one heading is usually
one idea. The path stays out of the chunk text because every strategy is compared
on the same text.

Rule 2 — Python is split by its syntax tree. A reader copies a whole function, so
half of one is no use: ``PythonCodeSplitter`` cuts only between top-level
statements and between a class's methods (later pieces repeat the class line).

Rule 3 — other code blocks are never split: a shell session or a JSON body cut in
two leaves two wrong halves.

Rule 4 — long sections are cut between paragraphs, but a fenced block is one unit.
Splitters that cut at blank lines cut code too (data_ingestion/DECISIONS.md, 2026-11-16).

Rule 5 — no token target: Voyage is the primary embedder, and BGE truncating a
long chunk is reported, not designed around.
"""

import logging
import re
from dataclasses import dataclass

from haystack import Document, component
from haystack.components.preprocessors import MarkdownHeaderSplitter, PythonCodeSplitter

from data_ingestion.chunkers.base import (
    CHAR_UNIT,
    ChunkSettings,
    build_chunks,
    check_source_paths,
    configure_demo_logging,
    demo_document,
    log_chunk_summary,
    source_path_of,
)

logger = logging.getLogger(__name__)

# Where splitting starts, matching ``recursive``; a block that cannot be split
# stays whole and may be longer.
SPLIT_THRESHOLD = 2_000
MARKDOWN_AST = ChunkSettings(
    strategy="markdown_ast", size=SPLIT_THRESHOLD, overlap=0, unit=CHAR_UNIT
)
PYTHON_LANGUAGES = frozenset({"python", "py"})

# A fence as MarkdownHeaderSplitter reads one: three or more backticks or tildes
# at the start of a line, closed by the same run alone on a later line.
_FENCE_RE = re.compile(r"^(`{3,}|~{3,})(.*)$")
# The "{ #anchor }" the docs put after most headings; not part of the path.
_HEADING_ANCHOR_RE = re.compile(r"\s*\{\s*#[^}]*\}\s*$")


@dataclass
class _Block:
    """A paragraph or a fenced code block, with the blank lines after it."""

    text: str
    fence: str | None = None  # the code block's opening run; None for a paragraph
    language: str = ""
    open: bool = False  # still waiting for its closing fence


def _blocks(section: str) -> list[_Block]:
    """Cut a section into paragraphs and code blocks; joined back, they are the section."""
    blocks: list[_Block] = []
    for line in section.splitlines(keepends=True):
        last = blocks[-1] if blocks else None
        opening = _FENCE_RE.match(line)
        if last is not None and last.open:
            last.text += line
            last.open = line.rstrip() != last.fence
        elif opening:
            info = opening.group(2).split()
            language = info[0].lower() if info else ""
            blocks.append(_Block(line, opening.group(1), language, open=True))
        elif last is not None and (not line.strip() or _paragraph_goes_on(last)):
            # Blank lines travel with the block before them.
            last.text += line
        else:
            blocks.append(_Block(line))
    return blocks


def _paragraph_goes_on(block: _Block) -> bool:
    return block.fence is None and bool(block.text.splitlines()[-1].strip())


def _pack(units: list[str]) -> list[str]:
    pieces: list[str] = []
    for unit in units:
        if pieces and len(pieces[-1]) + len(unit) <= SPLIT_THRESHOLD:
            pieces[-1] += unit
        else:
            pieces.append(unit)
    return pieces


def _heading_path(section: Document) -> str:
    headings = [*section.meta.get("parent_headers", []), section.meta.get("header") or ""]
    names = [_HEADING_ANCHOR_RE.sub("", heading).strip() for heading in headings]
    return " > ".join(name for name in names if name)


def _kept_whole(text: str, what: str, where: str) -> None:
    logger.info(
        "%s: kept a %d-character %s whole in %s",
        MARKDOWN_AST.strategy,
        len(text.strip()),
        what,
        where,
    )


@component
class MarkdownAstChunker:
    def __init__(self) -> None:
        self.settings = MARKDOWN_AST
        # Every heading level starts a section, and its line stays in the text.
        self._header_splitter = MarkdownHeaderSplitter(keep_headers=True)
        # Sized in characters (one "effective line" per character) to aim at the
        # same 2,000. A function longer than that is kept whole: the factor is
        # set so its line-based fallback, which cuts inside a function, never runs.
        self._python_splitter = PythonCodeSplitter(
            min_effective_lines=1,
            max_effective_lines=SPLIT_THRESHOLD,
            expected_chars_per_line=1,
            oversized_factor=1_000,
        )

    def warm_up(self) -> None:
        self._header_splitter.warm_up()

    def _split_python(self, block: _Block, where: str) -> list[str]:
        """An oversized Python block cut by its syntax tree, each piece re-fenced."""
        lines = block.text.splitlines(keepends=True)
        closing = max(i for i, line in enumerate(lines) if i and line.rstrip() == block.fence)
        body = Document(content="".join(lines[1:closing]))
        try:
            split = self._python_splitter.run([body])["documents"]
        except SyntaxError:
            _kept_whole(block.text, "Python block that does not parse on its own", where)
            return [block.text]
        # A statement sharing a line with the one before it comes back empty.
        pieces = [doc.content for doc in split if doc.content and doc.content.strip()]
        if len(pieces) < 2:
            _kept_whole(block.text, "Python block the splitter returned as one piece", where)
            return [block.text]
        opening = lines[0]
        closer = lines[closing].rstrip("\n") + "\n"
        tail = "".join(lines[closing:])
        return [opening + p + closer for p in pieces[:-1]] + [opening + pieces[-1] + tail]

    def _block_units(self, block: _Block, where: str) -> list[str]:
        """The pieces a block is packed as: itself, unless it is oversized Python."""
        if len(block.text.rstrip()) <= SPLIT_THRESHOLD:
            return [block.text]
        if block.fence is None:
            _kept_whole(block.text, "paragraph", where)
        elif block.language in PYTHON_LANGUAGES and not block.open:
            return self._split_python(block, where)
        else:
            _kept_whole(block.text, f"{block.language or 'untagged'} code block", where)
        return [block.text]

    @component.output_types(documents=list[Document])
    def run(self, documents: list[Document]) -> dict[str, list[Document]]:
        if not isinstance(documents, list):
            raise TypeError(f"Expected a list of Documents, got {type(documents).__name__}")
        check_source_paths(documents, self.settings.strategy)

        chunks: list[Document] = []
        for parent in documents:
            name = source_path_of(parent) or parent.id
            if not parent.content or not parent.content.strip():
                logger.warning("%s: %s has no content; skipping it", self.settings.strategy, name)
                continue

            texts: list[str] = []
            heading_paths: list[dict[str, str]] = []
            for section in self._header_splitter.run([parent])["documents"]:
                path = _heading_path(section)
                where = f"{name} ({path})" if path else name
                content = section.content or ""
                units = [u for b in _blocks(content) for u in self._block_units(b, where)]
                for piece in _pack(units):
                    if piece.strip():
                        texts.append(piece)
                        heading_paths.append({"heading_path": path})
            chunks.extend(build_chunks(parent, texts, self.settings, per_chunk_meta=heading_paths))
        return {"documents": chunks}


if __name__ == "__main__":
    configure_demo_logging()
    log_chunk_summary(MarkdownAstChunker().run([demo_document()])["documents"], log=logger)
