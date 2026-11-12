"""
The two embedders the chunking study compares, and how much text each one loses.

Models and dimensions are fixed here, not in ``.env``, so a stray edit cannot make runs
incomparable. Nothing here calls an API or downloads anything: components hold settings
until run, and BGE's tokenizer is read from the cache ``scripts/download_models.py`` fills.
"""

import logging
import math
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from data_ingestion.model_cache import fastembed_cache_dir

logger = logging.getLogger(__name__)

# Tokens per word used for Voyage, whose tokenizer is not available offline. The
# same ratio produced the chunk-size estimates in the build plan, so the numbers
# reported here stay comparable with those.
VOYAGE_TOKENS_PER_WORD = 1.3


@dataclass
class TokenizerSource:
    repo: str
    filename: str
    revision: str
    sha256: str


@dataclass
class EmbedderSpec:
    """``backend`` has no default, so a new entry must name what runs it. ``exact_tokens``
    is carried into every ``TruncationStats``, so an estimate is never read as exact.
    """

    name: str
    model: str
    dimension: int
    max_tokens: int
    backend: str
    exact_tokens: bool
    api_model: str | None = None  # the provider's name for ``model``, where it differs
    tokenizer: TokenizerSource | None = None
    query_prefix: str = ""  # sent before every query, never before a document


EMBEDDERS: dict[str, EmbedderSpec] = {
    "bge": EmbedderSpec(
        name="bge",
        model="BAAI/bge-large-en-v1.5",
        dimension=1024,
        max_tokens=512,  # dropping the rest is deliberate: DECISIONS.md, 2026-09-20
        backend="openrouter",
        exact_tokens=True,
        # Ran locally through fastembed until IDX-12; a full semantic pass took 106
        # minutes on a laptop CPU (F016), so it now runs on OpenRouter.
        api_model="baai/bge-large-en-v1.5",
        # The tokenizer fastembed used, from the snapshot already cached, so token
        # counts do not change with the switch.
        tokenizer=TokenizerSource(
            repo="qdrant/bge-large-en-v1.5-onnx",
            filename="tokenizer.json",
            revision="dc76b2c078fc38f0d243233d0ab0b51de925557e",
            sha256="d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66",
        ),
        # BGE's model card asks for this before queries (huggingface.co/BAAI/bge-large-en-v1.5).
        query_prefix="Represent this sentence for searching relevant passages: ",
    ),
    "voyage": EmbedderSpec(
        name="voyage",
        model="voyage-4-lite",
        dimension=2048,
        # voyage-4-lite takes 32k tokens and supports 256/512/1024/2048 output
        # dimensions (default 1024): https://docs.voyageai.com/docs/embeddings
        max_tokens=32_000,
        backend="voyage",
        exact_tokens=False,
    ),
}


def _unsupported_backend(spec: EmbedderSpec) -> ValueError:
    """Better a loud failure than a silent fallback: guessing would mean building a
    Voyage component for a local model, and a paid call with a model Voyage has
    never heard of.
    """
    return ValueError(
        f"Embedder {spec.name!r} names backend {spec.backend!r}, which this module "
        "does not build; supported backends: openrouter, voyage"
    )


def get_spec(name: str) -> EmbedderSpec:
    try:
        return EMBEDDERS[name]
    except KeyError:
        known = ", ".join(sorted(EMBEDDERS))
        raise ValueError(f"Unknown embedder {name!r}; known embedders: {known}") from None


def build_document_embedder(name: str):
    spec = get_spec(name)
    if spec.backend == "openrouter":
        from data_ingestion.openrouter_embeddings import OpenRouterDocumentEmbedder

        return OpenRouterDocumentEmbedder(
            model=spec.api_model or spec.model, dimension=spec.dimension
        )

    if spec.backend == "voyage":
        from haystack_integrations.components.embedders.voyage_embedders import (
            VoyageDocumentEmbedder,
        )

        # truncate defaults to True: Voyage silently cuts anything past its 32k limit
        # rather than failing. truncation_report says how often that would happen.
        # "document" here pairs with "query" in build_query_embedder (D39).
        return VoyageDocumentEmbedder(
            model=spec.model, output_dimension=spec.dimension, input_type="document"
        )

    raise _unsupported_backend(spec)


def build_query_embedder(name: str):
    """Must be the same model as ``build_document_embedder(name)``, or queries land in
    a different space from the documents they search.
    """
    spec = get_spec(name)
    if spec.backend == "openrouter":
        from data_ingestion.openrouter_embeddings import OpenRouterTextEmbedder

        return OpenRouterTextEmbedder(
            model=spec.api_model or spec.model,
            dimension=spec.dimension,
            prefix=spec.query_prefix,
        )

    if spec.backend == "voyage":
        from haystack_integrations.components.embedders.voyage_embedders import VoyageTextEmbedder

        return VoyageTextEmbedder(
            model=spec.model, output_dimension=spec.dimension, input_type="query"
        )

    raise _unsupported_backend(spec)


def cached_tokenizer_path(name: str) -> Path | None:
    """A filesystem lookup only, never a download or a model load, so it is the cheap way
    to ask whether the offline tokenizer is there. ``None`` means "not cached", and for
    Voyage, which has no local tokenizer, "there is none to have".
    """
    spec = get_spec(name)
    if spec.backend == "voyage":
        return None
    if spec.backend != "openrouter":
        raise _unsupported_backend(spec)
    if spec.tokenizer is None:
        raise ValueError(
            f"Embedder {spec.name!r} runs on {spec.backend} but names no tokenizer source"
        )

    from huggingface_hub import try_to_load_from_cache

    path = try_to_load_from_cache(
        repo_id=spec.tokenizer.repo,
        filename=spec.tokenizer.filename,
        revision=spec.tokenizer.revision,
        cache_dir=fastembed_cache_dir(),
    )
    return Path(path) if isinstance(path, str) else None


def _local_tokenizer(spec: EmbedderSpec):
    path = cached_tokenizer_path(spec.name)
    if path is None:
        raise FileNotFoundError(
            f"{spec.model}'s tokenizer is not in the model cache ({fastembed_cache_dir()}). "
            "Run 'uv run python scripts/download_models.py' from the project root first."
        )
    from tokenizers import Tokenizer

    tokenizer = Tokenizer.from_file(str(path))
    # The text's true length is what the report needs, not the window the model
    # would see, so neither truncation nor padding is enabled here.
    tokenizer.no_truncation()
    tokenizer.no_padding()
    logger.debug("Loaded a tokenizer from %s", path)
    return tokenizer


def bge_tokenizer():
    """Each call loads a new tokenizer, so a caller may turn on truncation or padding
    without changing what ``count_tokens`` reports. Load it once and pass it along.
    """
    return _local_tokenizer(EMBEDDERS["bge"])


def count_tokens(name: str, texts: Iterable[str]) -> list[int]:
    """BGE's counts include ``[CLS]`` and ``[SEP]``; Voyage's are an estimate."""
    spec = get_spec(name)
    texts = list(texts)
    if spec.backend == "openrouter":
        if not texts:
            return []
        encodings = _local_tokenizer(spec).encode_batch(texts)
        return [len(encoding.ids) for encoding in encodings]
    if spec.backend == "voyage":
        return [math.ceil(len(text.split()) * VOYAGE_TOKENS_PER_WORD) for text in texts]
    raise _unsupported_backend(spec)


@dataclass
class TruncationStats:
    embedder: str
    model: str
    max_tokens: int
    exact: bool
    chunk_count: int
    chunks_over_limit: int
    tokens_lost: int
    largest_chunk_tokens: int

    @property
    def percent_over_limit(self) -> float:
        if not self.chunk_count:
            return 0.0
        return 100.0 * self.chunks_over_limit / self.chunk_count

    def summary(self) -> str:
        basis = "exact token counts" if self.exact else "estimated token counts"
        return (
            f"{self.embedder} ({self.model}, limit {self.max_tokens} tokens): "
            f"{self.chunks_over_limit}/{self.chunk_count} chunks over the limit "
            f"({self.percent_over_limit:.1f}%), {self.tokens_lost} tokens dropped, "
            f"largest chunk {self.largest_chunk_tokens} tokens — {basis}"
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "embedder": self.embedder,
            "model": self.model,
            "max_tokens": self.max_tokens,
            "exact": self.exact,
            "chunk_count": self.chunk_count,
            "chunks_over_limit": self.chunks_over_limit,
            "percent_over_limit": round(self.percent_over_limit, 2),
            "tokens_lost": self.tokens_lost,
            "largest_chunk_tokens": self.largest_chunk_tokens,
        }


def truncation_stats(name: str, texts: Iterable[str]) -> TruncationStats:
    spec = get_spec(name)
    counts = count_tokens(spec.name, texts)
    over = [count for count in counts if count > spec.max_tokens]
    return TruncationStats(
        embedder=spec.name,
        model=spec.model,
        max_tokens=spec.max_tokens,
        exact=spec.exact_tokens,
        chunk_count=len(counts),
        chunks_over_limit=len(over),
        tokens_lost=sum(count - spec.max_tokens for count in over),
        largest_chunk_tokens=max(counts, default=0),
    )


def truncation_report(
    texts: Iterable[str], names: Iterable[str] | None = None
) -> dict[str, TruncationStats]:
    """The texts are read once, so a generator of chunks can be passed in. An empty
    ``names`` means an empty report, not every embedder: a caller that filtered its
    list down to nothing asked for nothing.
    """
    texts = list(texts)
    selected = EMBEDDERS if names is None else list(names)
    return {name: truncation_stats(name, texts) for name in selected}
