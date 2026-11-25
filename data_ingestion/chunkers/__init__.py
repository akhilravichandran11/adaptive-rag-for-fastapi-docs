"""
The chunking strategies the FastAPI Q&A RAG study compares. Each is a Haystack component
whose ``run(documents)`` returns chunks with ``base.py``'s shared fields, so indexing can swap
them. A strategy's name is in its Qdrant collections' names and each chunk's ``strategy`` field.

``get_chunker`` imports a strategy's module only when that strategy is asked for, so a caller
loads no other strategy's module or model, and each module still runs with ``python -m``.

Build pipelines in code: Haystack will not deserialize a class from a module outside its
allowlist unless told to trust this package, and nothing here does that at import (C004).
"""

from data_ingestion.chunkers.base import (
    CHAR_UNIT,
    SENTENCE_UNIT,
    WORD_UNIT,
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
    "build_chunks",
    "check_source_paths",
    "get_chunker",
    "split_documents",
    "strategy_names",
]

# Every strategy name, in the order the study reports them.
STRATEGIES = (
    "naive_small",
    "naive_medium",
    "naive_large",
    "sentence",
    "recursive",
    "semantic",
    "markdown_ast",
    "markdown_sections",
    "markdown_headers",
)


def strategy_names() -> list[str]:
    return list(STRATEGIES)


def get_chunker(name: str):
    """Every chunker has its :class:`ChunkSettings` as ``settings``, a ``warm_up()`` and a
    ``run(documents)``."""
    if name in ("naive_small", "naive_medium", "naive_large"):
        from data_ingestion.chunkers.naive import NaiveChunker

        return NaiveChunker(name)
    if name == "sentence":
        from data_ingestion.chunkers.sentence import SentenceChunker

        return SentenceChunker()
    if name == "recursive":
        from data_ingestion.chunkers.recursive import RecursiveChunker

        return RecursiveChunker()
    if name == "semantic":
        from data_ingestion.chunkers.semantic import SemanticChunker

        return SemanticChunker()
    if name == "markdown_ast":
        from data_ingestion.chunkers.markdown_ast import MarkdownAstChunker

        return MarkdownAstChunker()
    if name == "markdown_sections":
        from data_ingestion.chunkers.markdown_sections import MarkdownSectionsChunker

        return MarkdownSectionsChunker()
    if name == "markdown_headers":
        from data_ingestion.chunkers.markdown_headers import MarkdownHeadersChunker

        return MarkdownHeadersChunker()
    raise ValueError(
        f"Unknown chunking strategy {name!r}; available strategies: {', '.join(STRATEGIES)}"
    )
