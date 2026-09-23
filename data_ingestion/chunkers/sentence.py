"""
Sentence chunker: six sentences per chunk, overlapping by two.

Where the naive strategies cut in the middle of whatever they are reading, this
one only ever cuts between sentences, so every chunk is made of whole ones. Six
sentences is a small chunk for prose — that is the trade being measured: cleaner
boundaries against less context per chunk.

Sentence boundaries come from NLTK's ``punkt_tab`` tokenizer, which Haystack
loads behind ``DocumentSplitter(split_by="sentence")``. The model is read from
the project's own cache: :func:`register_nltk_data_dir` puts that folder on
``nltk.data.path`` first, without which NLTK 3.10 refuses to read it and
Haystack quietly downloads its own copy instead.

    uv run python -m data_ingestion.chunkers.sentence    # demo, from the project root

Run ``scripts/download_models.py`` once beforehand to fill the cache.
"""

from __future__ import annotations

import logging

from haystack import Document, component
from haystack.components.preprocessors import DocumentSplitter

from data_ingestion.chunkers.base import (
    SENTENCE_UNIT,
    ChunkSettings,
    configure_demo_logging,
    demo_document,
    log_chunk_summary,
    split_documents,
)
from data_ingestion.model_cache import register_nltk_data_dir

logger = logging.getLogger(__name__)

SENTENCE = ChunkSettings(strategy="sentence", size=6, overlap=2, unit=SENTENCE_UNIT)


@component
class SentenceChunker:
    """Splits into 6-sentence chunks overlapping by 2 sentences."""

    def __init__(self) -> None:
        self.settings = SENTENCE
        # Before the splitter is built, not just before it runs: the tokenizer
        # is looked up on the NLTK search path as it stands at lookup time.
        data_dir = register_nltk_data_dir()
        logger.debug("NLTK data directory registered for sentence splitting: %s", data_dir)
        self._splitter = DocumentSplitter(
            split_by=SENTENCE_UNIT,
            split_length=self.settings.size,
            split_overlap=self.settings.overlap,
        )

    def warm_up(self) -> None:
        """Load the sentence tokenizer (Haystack also does this on first run)."""
        self._splitter.warm_up()

    @component.output_types(documents=list[Document])
    def run(self, documents: list[Document]) -> dict[str, list[Document]]:
        """Chunk every document and return the chunks with the shared chunk fields."""
        return {"documents": split_documents(documents, self._splitter, self.settings)}


if __name__ == "__main__":
    configure_demo_logging()
    log_chunk_summary(SentenceChunker().run([demo_document()])["documents"], log=logger)
