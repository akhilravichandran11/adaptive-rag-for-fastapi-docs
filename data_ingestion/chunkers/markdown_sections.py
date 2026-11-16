"""
markdown_sections chunker: one chunk per heading section, with its heading path in metadata.

Five rules:

1. Split at every heading (Haystack's ``MarkdownHeaderSplitter``).
2. A section under 400 characters is joined to the one after it (the one before,
   if it is last) when the two fit in 2,000 characters, so a heading with one
   sentence under it is not a chunk of its own.
3. A section over 2,000 characters is cut between paragraphs. A fenced code
   block is never cut, whatever its size.
4. The heading path (``Tutorial > Body > Nested Models``) is the ``heading_path``
   metadata field only, as in ``markdown_ast``; the chunk text is the section's own.
5. When a section over 2,000 characters is cut, a paragraph directly followed by a
   code block stays with it, so the text that introduces the code is in its chunk.
   A code block after a heading or another code block is packed on its own.

    uv run python -m data_ingestion.chunkers.markdown_sections    # demo, from the project root
"""

import logging
import os
import re

from haystack import Document, component
from haystack.components.preprocessors import MarkdownHeaderSplitter

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

MIN_SECTION = 400
MAX_SECTION = 2_000
MARKDOWN_SECTIONS = ChunkSettings(
    strategy="markdown_sections", size=MAX_SECTION, overlap=0, unit=CHAR_UNIT
)

_FENCE_RE = re.compile(r"^(`{3,}|~{3,})")
# The "{ #anchor }" the docs put after most headings; not part of the path.
_HEADING_ANCHOR_RE = re.compile(r"\s*\{\s*#[^}]*\}\s*$")


def _heading_path(section: Document) -> list[str]:
    """The headings above a section and its own, without anchors."""
    headings = [*section.meta.get("parent_headers", []), section.meta.get("header") or ""]
    names = [_HEADING_ANCHOR_RE.sub("", heading).strip() for heading in headings]
    return [name for name in names if name]


def _can_join(short: str, other: str) -> bool:
    """True when ``short`` is under MIN_SECTION and the two still fit in one chunk, unsplit."""
    return len(short) < MIN_SECTION and len(short) + len(other) <= MAX_SECTION


def _join(first: tuple[list[str], str], second: tuple[list[str], str]) -> tuple[list[str], str]:
    # A chunk holding two sections is labelled with the headings they share.
    # commonprefix compares any lists, not only file paths.
    return os.path.commonprefix([first[0], second[0]]), first[1] + second[1]


def _join_short_sections(
    sections: list[tuple[list[str], str]],
) -> list[tuple[list[str], str]]:
    """Join a short section to the next one; a short last one joins the one before."""
    joined: list[tuple[list[str], str]] = []
    for section in sections:
        if joined and _can_join(joined[-1][1], section[1]):
            joined[-1] = _join(joined[-1], section)
        else:
            joined.append(section)
    if len(joined) > 1 and _can_join(joined[-1][1], joined[-2][1]):
        last = joined.pop()
        joined[-1] = _join(joined[-1], last)
    return joined


def _is_paragraph(block: str) -> bool:
    """True when a block is not a heading and does not end on a code block."""
    lines = block.strip().splitlines()
    return not lines[0].startswith("#") and not _FENCE_RE.match(lines[-1])


def _blocks(text: str) -> list[str]:
    """Cut text at blank lines, except inside a code fence and between a paragraph and the code
    block after it (rule 5); joined back, the blocks are the text."""
    blocks = [""]
    fence = None
    for line in text.splitlines(keepends=True):
        opening = _FENCE_RE.match(line) if fence is None else None
        if opening and len(blocks) > 1 and not blocks[-1].strip() and _is_paragraph(blocks[-2]):
            blank_lines = blocks.pop()
            blocks[-1] += blank_lines
        blocks[-1] += line
        if opening:
            fence = opening.group(1)
        elif fence is not None and line.rstrip() == fence:
            fence = None
        elif fence is None and not line.strip() and blocks[-1].strip():
            # More blank lines in a row go to the next block, so each block cut off has text.
            blocks.append("")
    return [block for block in blocks if block]


def _pack(blocks: list[str], limit: int) -> list[str]:
    """Fill pieces with whole blocks in order, starting a new piece before one would overflow."""
    pieces: list[str] = []
    for block in blocks:
        if pieces and len(pieces[-1]) + len(block) <= limit:
            pieces[-1] += block
        else:
            pieces.append(block)
    return pieces


@component
class MarkdownSectionsChunker:
    """One chunk per heading section: short ones joined, long ones cut between paragraphs."""

    def __init__(self) -> None:
        self.settings = MARKDOWN_SECTIONS
        self._header_splitter = MarkdownHeaderSplitter(keep_headers=True)

    def warm_up(self) -> None:
        self._header_splitter.warm_up()

    @component.output_types(documents=list[Document])
    def run(self, documents: list[Document]) -> dict[str, list[Document]]:
        if not isinstance(documents, list):
            raise TypeError(f"Expected a list of Documents, got {type(documents).__name__}")
        check_source_paths(documents, self.settings.strategy)

        chunks: list[Document] = []
        for parent in documents:
            if not parent.content or not parent.content.strip():
                name = source_path_of(parent) or parent.id
                logger.warning("%s: %s has no content; skipping it", self.settings.strategy, name)
                continue

            sections = [
                (_heading_path(section), section.content or "")
                for section in self._header_splitter.run([parent])["documents"]
            ]
            texts: list[str] = []
            heading_paths: list[dict[str, str]] = []
            for path, text in _join_short_sections(sections):
                breadcrumb = " > ".join(path)
                pieces = _pack(_blocks(text), MAX_SECTION) if len(text) > MAX_SECTION else [text]
                for piece in pieces:
                    if not piece.strip():
                        continue
                    texts.append(piece)
                    heading_paths.append({"heading_path": breadcrumb})
            chunks.extend(build_chunks(parent, texts, self.settings, per_chunk_meta=heading_paths))
        return {"documents": chunks}


if __name__ == "__main__":
    configure_demo_logging()
    log_chunk_summary(MarkdownSectionsChunker().run([demo_document()])["documents"], log=logger)
