"""
Semantic chunker: cut where the topic changes, found by comparing BGE embeddings.

The text is split into sentences and the sentences are grouped three at a time.
Each group is embedded with BGE-large, and a chunk ends wherever the cosine
distance between two neighbouring groups is above the 95th percentile of all such
distances in that document. A piece over 1,000 characters is split again the
same way, and a piece under 50 characters is merged into the one after it (a
short last piece, into the one before it, since Haystack 3.2.0). Neither is a
guarantee: Haystack returns a long piece it cannot split further (with a
warning), skips a merge that would reach 1,000, and never merges the pieces a
re-split produces, so a chunk can be shorter than 50 or longer than 1,000.

The embedding model only finds the cut points; the chunks never carry its
vectors. ``semantic__bge`` and ``semantic__voyage`` are built from the same
chunks, so the boundaries here always come from BGE, whichever embedder later
indexes them.

BGE runs on OpenRouter (see :mod:`data_ingestion.openrouter_embeddings`), so
splitting needs ``OPENROUTER_API_KEY`` and costs a few cents per full corpus.
Each sentence group is truncated to BGE's 512 tokens before it is sent, with
BGE's tokenizer from the project model cache. Sentence boundaries come from
NLTK's ``punkt_tab`` in the same cache (see :mod:`data_ingestion.chunkers.sentence`).

    uv run python -m data_ingestion.chunkers.semantic    # demo (a paid call)

Run ``scripts/download_models.py`` once beforehand to fill the cache.
"""

from __future__ import annotations

import logging
from typing import Any

from haystack import Document, component
from haystack.components.preprocessors import EmbeddingBasedDocumentSplitter

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
from data_ingestion.embedders import build_document_embedder
from data_ingestion.model_cache import register_nltk_data_dir

logger = logging.getLogger(__name__)

BOUNDARY_EMBEDDER = "bge"
PERCENTILE = 0.95
SENTENCES_PER_GROUP = 3
MIN_LENGTH = 50
MAX_LENGTH = 1_000

# There is no target size: a chunk over 1,000 characters is split again where
# it can be (not always — see the module docstring), and chunks never overlap.
SEMANTIC = ChunkSettings(strategy="semantic", size=MAX_LENGTH, overlap=0, unit=CHAR_UNIT)

# The splitter settings that are not a size, recorded on every chunk beside the
# shared fields so a chunk says how its boundaries were found.
SPLITTER_META: dict[str, Any] = {
    "boundary_embedder": BOUNDARY_EMBEDDER,
    "percentile": PERCENTILE,
    "sentences_per_group": SENTENCES_PER_GROUP,
    "min_length": MIN_LENGTH,
}


@component
class SemanticChunker:
    """Splits where neighbouring sentence groups drift apart in BGE embedding space."""

    def __init__(self) -> None:
        self.settings = SEMANTIC
        # Before the splitter is built: its sentence tokenizer is looked up on
        # the NLTK search path as it stands at lookup time.
        data_dir = register_nltk_data_dir()
        logger.debug("NLTK data directory registered for semantic splitting: %s", data_dir)
        self._splitter = EmbeddingBasedDocumentSplitter(
            document_embedder=build_document_embedder(BOUNDARY_EMBEDDER),
            sentences_per_group=SENTENCES_PER_GROUP,
            percentile=PERCENTILE,
            min_length=MIN_LENGTH,
            max_length=MAX_LENGTH,
        )

    def warm_up(self) -> None:
        """Load the sentence tokenizer (Haystack also does this on first run)."""
        self._splitter.warm_up()

    @component.output_types(documents=list[Document])
    def run(self, documents: list[Document]) -> dict[str, list[Document]]:
        """Chunk every document and return the chunks with the shared chunk fields.

        Splits one parent at a time, like ``base.split_documents``, but builds
        the chunks itself so each one can also carry :data:`SPLITTER_META`.
        """
        if not isinstance(documents, list):
            raise TypeError(f"Expected a list of Documents, got {type(documents).__name__}")
        check_source_paths(documents, self.settings.strategy)

        chunks: list[Document] = []
        for parent in documents:
            name = source_path_of(parent) or parent.id
            # Whitespace-only content is skipped here too: the splitter would
            # otherwise embed an empty batch just to return one blank chunk.
            if not parent.content or not parent.content.strip():
                logger.warning("%s: %s has no content; skipping it", self.settings.strategy, name)
                continue

            split = self._splitter.run([parent])["documents"]
            texts = [chunk.content for chunk in split if chunk.content and chunk.content.strip()]
            if len(texts) < len(split):
                logger.info(
                    "%s: dropped %d blank chunk(s) from %s",
                    self.settings.strategy,
                    len(split) - len(texts),
                    name,
                )
            if not texts:
                logger.warning("%s produced no chunks for %s", self.settings.strategy, name)
                continue
            chunks.extend(
                build_chunks(
                    parent, texts, self.settings, per_chunk_meta=[SPLITTER_META] * len(texts)
                )
            )
        return {"documents": chunks}


if __name__ == "__main__":
    configure_demo_logging()
    log_chunk_summary(SemanticChunker().run([demo_document()])["documents"], log=logger)
