"""
markdown_sections chunker: one chunk per heading section, with its heading path in metadata.
Short sections join a neighbour; long ones are cut between paragraphs, never inside a code
block, and a paragraph stays with the code block after it.

    uv run python -m data_ingestion.chunkers.markdown_sections    # demo, from the project root

Why: data_ingestion/DECISIONS.md, 2026-11-16.
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
    headings = [*section.meta.get("parent_headers", []), section.meta.get("header") or ""]
    names = [_HEADING_ANCHOR_RE.sub("", heading).strip() for heading in headings]
    return [name for name in names if name]


def _can_join(short: str, other: str) -> bool:
    return len(short) < MIN_SECTION and len(short) + len(other) <= MAX_SECTION


def _join(first: tuple[list[str], str], second: tuple[list[str], str]) -> tuple[list[str], str]:
    # A chunk holding two sections is labelled with the headings they share.
    # commonprefix compares any lists, not only file paths.
    return os.path.commonprefix([first[0], second[0]]), first[1] + second[1]


def _join_short_sections(
    sections: list[tuple[list[str], str]],
) -> list[tuple[list[str], str]]:
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


def _pack(blocks: list[str]) -> list[str]:
    pieces: list[str] = []
    for block in blocks:
        if pieces and len(pieces[-1]) + len(block) <= MAX_SECTION:
            pieces[-1] += block
        else:
            pieces.append(block)
    return pieces


@component
class MarkdownSectionsChunker:
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
                for piece in _pack(_blocks(text)):
                    if not piece.strip():
                        continue
                    texts.append(piece)
                    heading_paths.append({"heading_path": breadcrumb})
            chunks.extend(build_chunks(parent, texts, self.settings, per_chunk_meta=heading_paths))
        return {"documents": chunks}


if __name__ == "__main__":
    configure_demo_logging()
    log_chunk_summary(MarkdownSectionsChunker().run([demo_document()])["documents"], log=logger)
