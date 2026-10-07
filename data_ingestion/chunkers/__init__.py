"""
The chunking strategies the FastAPI Q&A RAG study compares.

Every strategy is a Haystack component with the same interface — ``run(documents)``
in, chunk documents carrying the shared fields from :mod:`.base` out — so indexing
can swap one for another without knowing which it has. :data:`STRATEGIES` maps
each strategy's name to the factory that builds it, and that name is what the
variant, its Qdrant collection, and every chunk's ``strategy`` field are called.

    from data_ingestion.chunkers import STRATEGIES, get_chunker

    chunker = get_chunker("naive_medium")
    chunks = chunker.run(documents)["documents"]

Each factory imports its own module when it is called, not when this package is
imported: a caller that wants one strategy does not pay for loading the models
another one needs, and a strategy module stays runnable on its own with
``python -m``.

Five strategies split by size (words, sentences, or characters); ``semantic``
cuts where BGE embeddings say the topic changes; ``markdown_ast`` follows the
document's own headings and never cuts inside a code fence or a function.

Build indexing pipelines in code rather than loading them from a saved
dictionary or YAML: a pipeline holding one of these components serializes, but
Haystack 3.0 will not deserialize a class from a module outside its trusted
allowlist without being told to trust this package explicitly.
"""

from __future__ import annotations

from collections.abc import Callable

from data_ingestion.chunkers.base import (
    CHAR_UNIT,
    SENTENCE_UNIT,
    WORD_UNIT,
    Chunker,
    ChunkSettings,
    build_chunks,
    check_source_paths,
    split_documents,
)

__all__ = [
    "CHAR_UNIT",
    "SENTENCE_UNIT",
    "STRATEGIES",
    "WORD_UNIT",
    "ChunkSettings",
    "Chunker",
    "build_chunks",
    "build_markdown_ast",
    "build_naive_large",
    "build_naive_medium",
    "build_naive_small",
    "build_recursive",
    "build_semantic",
    "build_sentence",
    "check_source_paths",
    "get_chunker",
    "split_documents",
    "strategy_names",
]


def build_naive_small() -> Chunker:
    """200-word chunks, 25 words of overlap."""
    from data_ingestion.chunkers.naive import NaiveSmallChunker

    return NaiveSmallChunker()


def build_naive_medium() -> Chunker:
    """400-word chunks, 50 words of overlap — the study's reference variant."""
    from data_ingestion.chunkers.naive import NaiveMediumChunker

    return NaiveMediumChunker()


def build_naive_large() -> Chunker:
    """800-word chunks, 100 words of overlap."""
    from data_ingestion.chunkers.naive import NaiveLargeChunker

    return NaiveLargeChunker()


def build_sentence() -> Chunker:
    """6-sentence chunks, 2 sentences of overlap."""
    from data_ingestion.chunkers.sentence import SentenceChunker

    return SentenceChunker()


def build_recursive() -> Chunker:
    """2,000-character chunks, 200 characters of overlap, cut at the best boundary."""
    from data_ingestion.chunkers.recursive import RecursiveChunker

    return RecursiveChunker()


def build_semantic() -> Chunker:
    """Cut where the topic changes, found with BGE-large; chunks aim for 1,000 characters or fewer."""
    from data_ingestion.chunkers.semantic import SemanticChunker

    return SemanticChunker()


def build_markdown_ast() -> Chunker:
    """Cut at headings, then between paragraphs past 2,000; never inside a fence or a function."""
    from data_ingestion.chunkers.markdown_ast import MarkdownAstChunker

    return MarkdownAstChunker()


# Strategy name -> factory. The order is the one the study reports them in.
STRATEGIES: dict[str, Callable[[], Chunker]] = {
    "naive_small": build_naive_small,
    "naive_medium": build_naive_medium,
    "naive_large": build_naive_large,
    "sentence": build_sentence,
    "recursive": build_recursive,
    "semantic": build_semantic,
    "markdown_ast": build_markdown_ast,
}


def strategy_names() -> list[str]:
    """Every strategy name, in the order the study reports them."""
    return list(STRATEGIES)


def get_chunker(name: str) -> Chunker:
    """Build the chunker registered under ``name``.

    Raises ``ValueError`` naming the unknown strategy and listing the known ones,
    so a typo in a ``--strategy`` argument fails with something readable rather
    than a bare ``KeyError``.
    """
    try:
        factory = STRATEGIES[name]
    except KeyError:
        raise ValueError(
            f"Unknown chunking strategy {name!r}; available strategies: "
            f"{', '.join(strategy_names())}"
        ) from None
    return factory()
