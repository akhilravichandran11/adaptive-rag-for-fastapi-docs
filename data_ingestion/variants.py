"""
The study's variants: each chunking strategy paired with each embedder, and the
Qdrant collection each pair is indexed into.

A collection is named here and nowhere else (D32), so stage 3, which writes the
collections, and step 6, which reads them, cannot disagree::

    from data_ingestion.variants import select_variants

    for variant in select_variants("all", "bge"):
        print(variant.collection())  # fastapi_docs__naive_small__bge, ...

Selecting variants builds no chunker and no embedder: the registries hold factories
and specs, and nothing loads a model or opens a connection until a stage builds one.
A prefix given on a command line goes through ``check_prefix`` first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from data_ingestion.chunkers import STRATEGIES
from data_ingestion.embedders import EMBEDDERS, EmbedderSpec

DEFAULT_PREFIX = "fastapi_docs"

# Letters, digits, "_" and "-"; check_prefix also refuses "__" and a trailing "_".
_PREFIX_PATTERN = re.compile(r"[A-Za-z0-9_-]+")


@dataclass(frozen=True)
class Variant:
    """One strategy × embedder pair."""

    strategy: str
    embedder: EmbedderSpec

    def collection(self, prefix: str = DEFAULT_PREFIX) -> str:
        """The Qdrant collection for this pair: ``<prefix>__<strategy>__<embedder>``.

        Another prefix points a whole run at other collections, e.g. throwaway
        ``ix_verify_<date>`` ones.
        """
        return f"{prefix}__{self.strategy}__{self.embedder.name}"


def check_prefix(prefix: str) -> str:
    """Return ``prefix`` if it can start a collection name; otherwise raise ``ValueError``.

    A prefix is letters, digits, ``_`` and ``-``, with no ``__`` and no ``_`` at its
    end: ``__`` joins the parts of a name, so ``a__b`` or ``a_`` (``a___naive_small__bge``)
    would make it ambiguous to split. Any other character is refused here, not by
    Qdrant at its first call. The error names the prefix.
    """
    if not _PREFIX_PATTERN.fullmatch(prefix) or "__" in prefix or prefix.endswith("_"):
        raise ValueError(
            f"Collection prefix {prefix!r} must be letters, digits, '_' and '-', with no "
            "'__' and no '_' at its end ('__' joins the parts of a collection name)"
        )
    return prefix


def select_variants(strategy: str, embedder: str) -> list[Variant]:
    """The variants for a strategy name or ``all`` and an embedder name or ``all``.

    Variants keep the registries' order, strategy first, so ``all`` × ``all`` gives
    each strategy with ``bge`` and then ``voyage``. Raises ``ValueError`` naming every
    unknown name at once, so a command with two typos fails once, not twice.
    """
    strategies = list(STRATEGIES) if strategy == "all" else [strategy]
    embedders = list(EMBEDDERS) if embedder == "all" else [embedder]
    unknown = [f"strategy {s!r}" for s in strategies if s not in STRATEGIES] + [
        f"embedder {e!r}" for e in embedders if e not in EMBEDDERS
    ]
    if unknown:
        raise ValueError(
            f"Unknown {' and '.join(unknown)}; strategies: {', '.join(STRATEGIES)}; "
            f"embedders: {', '.join(EMBEDDERS)}; or 'all' for either"
        )
    return [Variant(s, EMBEDDERS[e]) for s in strategies for e in embedders]
