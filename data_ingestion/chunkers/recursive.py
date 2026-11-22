"""
Recursive chunker: each cut lands on a paragraph break where one is close enough and falls
back to a smaller boundary only where it has to.

    uv run python -m data_ingestion.chunkers.recursive    # demo, from the project root

Run ``scripts/download_models.py`` once beforehand to fill the model cache.
"""

import logging

from haystack import Document, component
from haystack.components.preprocessors import RecursiveDocumentSplitter

from data_ingestion.chunkers.base import (
    CHAR_UNIT,
    ChunkSettings,
    configure_demo_logging,
    demo_document,
    log_chunk_summary,
    split_documents,
)
from data_ingestion.model_cache import register_nltk_data_dir

logger = logging.getLogger(__name__)

RECURSIVE = ChunkSettings(strategy="recursive", size=2_000, overlap=200, unit=CHAR_UNIT)

# Tried in order, coarsest first; "sentence" is NLTK's tokenizer, the rest match literally.
# No "\n#": Haystack keeps a matched separator at the end of the earlier chunk, so 359 chunks
# on this corpus would end on a stray "#". Headings are the markdown_ast strategy's job.
RECURSIVE_SEPARATORS = ["\n\n", "\n", "sentence", " "]


@component
class RecursiveChunker:
    def __init__(self) -> None:
        self.settings = RECURSIVE
        # The "sentence" separator reaches for the same NLTK tokenizer the
        # sentence strategy uses, so the project cache has to be registered.
        data_dir = register_nltk_data_dir()
        logger.debug("NLTK data directory registered for sentence separators: %s", data_dir)
        self._splitter = RecursiveDocumentSplitter(
            split_length=self.settings.size,
            split_overlap=self.settings.overlap,
            split_unit=CHAR_UNIT,
            separators=RECURSIVE_SEPARATORS,
        )

    def warm_up(self) -> None:
        """Load the sentence tokenizer (Haystack also does this on first run)."""
        self._splitter.warm_up()

    @component.output_types(documents=list[Document])
    def run(self, documents: list[Document]) -> dict[str, list[Document]]:
        return {"documents": split_documents(documents, self._splitter, self.settings)}


if __name__ == "__main__":
    configure_demo_logging()
    log_chunk_summary(RecursiveChunker().run([demo_document()])["documents"], log=logger)
