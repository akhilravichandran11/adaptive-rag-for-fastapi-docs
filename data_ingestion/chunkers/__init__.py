"""
The chunking strategies the FastAPI Q&A RAG study compares.

Every strategy is a Haystack component with the same interface — ``run(documents)``
in, chunk documents carrying the shared fields from :mod:`.base` out — so indexing
can swap one for another without knowing which it has. :data:`STRATEGIES` lists
every strategy's name, and that name is what the variant, its Qdrant collection,
and every chunk's ``strategy`` field are called.

    from data_ingestion.chunkers import STRATEGIES, get_chunker

    chunker = get_chunker("naive_medium")
    chunks = chunker.run(documents)["documents"]

``get_chunker`` imports a strategy's module only when that strategy is asked for,
not when this package is imported: a caller that wants one strategy does not pay
for loading the models another one needs, and a strategy module stays runnable on
its own with ``python -m``.

Five strategies split by size (words, sentences, or characters); ``semantic``
cuts where BGE embeddings say the topic changes; ``markdown_ast`` follows the
document's own headings and never cuts inside a code fence or a function;
``markdown_sections`` makes one chunk per heading section and never cuts a code
block; ``markdown_headers`` is Haystack's own markdown splitter, unextended.

Build indexing pipelines in code rather than loading them from a saved
dictionary or YAML: a pipeline holding one of these components serializes, but
Haystack 3.0 will not deserialize a class from a module outside its trusted
allowlist without being told to trust this package explicitly.
"""

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
    """Every strategy name, in the order the study reports them."""
    return list(STRATEGIES)


def get_chunker(name: str) -> Chunker:
    """Build the chunker called ``name``, importing only that strategy's module.

    Raises ``ValueError`` naming the unknown strategy and listing the known ones,
    so a typo in a ``--strategy`` argument fails with something readable.
    """
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
