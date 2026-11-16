"""
markdown_headers chunker: Haystack's own markdown splitter, configured and not extended.

Splits at ``#`` and ``##`` headings; deeper headings stay inside their ``##``
section. A section over 300 words is cut every 300 words. The heading stays in
the first piece's text. This is the off-the-shelf baseline for the
``markdown_sections`` strategy: what the framework does with no code of ours.

    uv run python -m data_ingestion.chunkers.markdown_headers    # demo, from the project root
"""

import logging

from haystack import Document, component
from haystack.components.preprocessors import MarkdownHeaderSplitter

from data_ingestion.chunkers.base import (
    WORD_UNIT,
    ChunkSettings,
    configure_demo_logging,
    demo_document,
    log_chunk_summary,
    split_documents,
)

logger = logging.getLogger(__name__)

# 300 words is about 2,000 characters on this corpus, the size the other
# structure-aware strategies aim at.
MARKDOWN_HEADERS = ChunkSettings(strategy="markdown_headers", size=300, overlap=0, unit=WORD_UNIT)


@component
class MarkdownHeadersChunker:
    """Splits at # and ## headings, then every 300 words within a section."""

    def __init__(self) -> None:
        self.settings = MARKDOWN_HEADERS
        self._splitter = MarkdownHeaderSplitter(
            keep_headers=True,
            header_split_levels=[1, 2],
            secondary_split="word",
            split_length=self.settings.size,
            split_overlap=self.settings.overlap,
        )

    def warm_up(self) -> None:
        self._splitter.warm_up()

    @component.output_types(documents=list[Document])
    def run(self, documents: list[Document]) -> dict[str, list[Document]]:
        return {"documents": split_documents(documents, self._splitter, self.settings)}


if __name__ == "__main__":
    configure_demo_logging()
    log_chunk_summary(MarkdownHeadersChunker().run([demo_document()])["documents"], log=logger)
