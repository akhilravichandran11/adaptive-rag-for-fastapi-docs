"""
Fixed-size word chunkers: the size sweep the chunking study is measured against.

Three strategies that differ only in how much text they put in a chunk — 200,
400, and 800 words, each overlapping its neighbour by an eighth of its size.
They cut wherever the word count runs out, with no regard for headings, code
fences, or sentence ends, which is the point: they are the baseline the
structure-aware strategies have to beat, and the middle one (``naive_medium``)
is the study's reference variant.

Each size is written here rather than read from the environment, so a variant
that was indexed as ``naive_small`` means the same thing in every run.

    uv run python -m data_ingestion.chunkers.naive    # demo, from the project root
"""

import logging

from haystack import Document, component
from haystack.components.preprocessors import DocumentSplitter

from data_ingestion.chunkers.base import (
    WORD_UNIT,
    ChunkSettings,
    configure_demo_logging,
    demo_document,
    log_chunk_summary,
    split_documents,
)

logger = logging.getLogger(__name__)

# DocumentSplitter counts a "word" as a space-separated run of characters, so
# newlines and indentation travel inside the words: each chunk is a verbatim
# stretch of its source document, and consecutive chunks repeat only the overlap.
NAIVE_SIZES = {
    "naive_small": (200, 25),
    "naive_medium": (400, 50),
    "naive_large": (800, 100),
}


@component
class NaiveChunker:
    """Splits into fixed-size word chunks; ``strategy`` picks the size from ``NAIVE_SIZES``."""

    def __init__(self, strategy: str) -> None:
        size, overlap = NAIVE_SIZES[strategy]
        self.strategy = strategy  # Haystack serializes a component from its init arguments
        self.settings = ChunkSettings(strategy=strategy, size=size, overlap=overlap, unit=WORD_UNIT)
        self._splitter = DocumentSplitter(
            split_by=WORD_UNIT, split_length=size, split_overlap=overlap
        )

    def warm_up(self) -> None:
        self._splitter.warm_up()

    @component.output_types(documents=list[Document])
    def run(self, documents: list[Document]) -> dict[str, list[Document]]:
        """Chunk every document and return the chunks with the shared chunk fields."""
        return {"documents": split_documents(documents, self._splitter, self.settings)}


if __name__ == "__main__":
    configure_demo_logging()
    sample = demo_document()
    for strategy in NAIVE_SIZES:
        log_chunk_summary(NaiveChunker(strategy).run([sample])["documents"], log=logger)
