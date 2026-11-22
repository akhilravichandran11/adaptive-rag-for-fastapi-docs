"""
Sentence chunker: unlike the naive strategies, it only cuts between sentences, so every chunk is
made of whole ones. Six sentences is small for prose: the trade measured is cleaner boundaries
against less context per chunk.

    uv run python -m data_ingestion.chunkers.sentence    # demo, from the project root

Run ``scripts/download_models.py`` once beforehand to fill the model cache.
"""

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
    def __init__(self) -> None:
        self.settings = SENTENCE
        # NLTK 3.10+ reads the project cache only once it is on the search path, so register it
        # here, before warm_up() or the first run loads punkt_tab; else Haystack downloads its own.
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
        return {"documents": split_documents(documents, self._splitter, self.settings)}


if __name__ == "__main__":
    configure_demo_logging()
    log_chunk_summary(SentenceChunker().run([demo_document()])["documents"], log=logger)
