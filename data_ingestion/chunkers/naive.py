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
NAIVE_SMALL = ChunkSettings(strategy="naive_small", size=200, overlap=25, unit=WORD_UNIT)
NAIVE_MEDIUM = ChunkSettings(strategy="naive_medium", size=400, overlap=50, unit=WORD_UNIT)
NAIVE_LARGE = ChunkSettings(strategy="naive_large", size=800, overlap=100, unit=WORD_UNIT)


def _word_splitter(settings: ChunkSettings) -> DocumentSplitter:
    """A Haystack word splitter configured from ``settings``."""
    return DocumentSplitter(
        split_by=WORD_UNIT,
        split_length=settings.size,
        split_overlap=settings.overlap,
    )


@component
class NaiveSmallChunker:
    """Splits into 200-word chunks overlapping by 25 words."""

    def __init__(self) -> None:
        self.settings = NAIVE_SMALL
        self._splitter = _word_splitter(self.settings)

    def warm_up(self) -> None:
        self._splitter.warm_up()

    @component.output_types(documents=list[Document])
    def run(self, documents: list[Document]) -> dict[str, list[Document]]:
        """Chunk every document and return the chunks with the shared chunk fields."""
        return {"documents": split_documents(documents, self._splitter, self.settings)}


@component
class NaiveMediumChunker:
    """Splits into 400-word chunks overlapping by 50 words — the study's reference size."""

    def __init__(self) -> None:
        self.settings = NAIVE_MEDIUM
        self._splitter = _word_splitter(self.settings)

    def warm_up(self) -> None:
        self._splitter.warm_up()

    @component.output_types(documents=list[Document])
    def run(self, documents: list[Document]) -> dict[str, list[Document]]:
        """Chunk every document and return the chunks with the shared chunk fields."""
        return {"documents": split_documents(documents, self._splitter, self.settings)}


@component
class NaiveLargeChunker:
    """Splits into 800-word chunks overlapping by 100 words."""

    def __init__(self) -> None:
        self.settings = NAIVE_LARGE
        self._splitter = _word_splitter(self.settings)

    def warm_up(self) -> None:
        self._splitter.warm_up()

    @component.output_types(documents=list[Document])
    def run(self, documents: list[Document]) -> dict[str, list[Document]]:
        """Chunk every document and return the chunks with the shared chunk fields."""
        return {"documents": split_documents(documents, self._splitter, self.settings)}


if __name__ == "__main__":
    configure_demo_logging()
    sample = demo_document()
    for chunker in (NaiveSmallChunker(), NaiveMediumChunker(), NaiveLargeChunker()):
        log_chunk_summary(chunker.run([sample])["documents"], log=logger)
