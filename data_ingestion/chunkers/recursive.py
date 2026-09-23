"""
Recursive chunker: 2,000-character chunks cut at the most natural boundary available.

The separator list is tried in order — paragraph breaks first, then line breaks,
then sentence ends, then single spaces. A piece that still exceeds 2,000
characters is handed to the next separator down, so the cut lands on a paragraph
break where one is close enough and falls back to a smaller boundary only where
it has to. Chunks overlap by 200 characters.

Design note — why there is no heading separator. Trying headings first, ahead of
paragraphs, is the obvious thing to want here. Haystack's
``RecursiveDocumentSplitter`` appends a matched separator to the end of the
*preceding* chunk, where LangChain's recursive splitter keeps it at the start of
the following one, and its separators are matched literally, not as patterns. A
``"\\n#"`` separator therefore ends one chunk on a stray ``#`` and starts the
next on a heading that has lost a level — on this corpus that happened at 359 of
688 chunk boundaries, and ``##`` then survives in neither chunk. Paragraph
breaks are the next boundary down and cost nothing, so they lead the list here;
splitting on the document's own headings is the job of the ``markdown_ast``
strategy, which is built for it.

Sentence ends come from NLTK's ``punkt_tab`` tokenizer, read from the project's
own model cache (see :mod:`data_ingestion.chunkers.sentence`).

    uv run python -m data_ingestion.chunkers.recursive    # demo, from the project root

Run ``scripts/download_models.py`` once beforehand to fill the cache.
"""

from __future__ import annotations

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

# Tried in order, coarsest boundary first. "sentence" is the tokenizer, not a
# literal string; the others are matched literally.
RECURSIVE_SEPARATORS = ["\n\n", "\n", "sentence", " "]


@component
class RecursiveChunker:
    """Splits into 2,000-character chunks overlapping by 200, cutting at the best boundary."""

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
            separators=list(RECURSIVE_SEPARATORS),
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
    log_chunk_summary(RecursiveChunker().run([demo_document()])["documents"], log=logger)
