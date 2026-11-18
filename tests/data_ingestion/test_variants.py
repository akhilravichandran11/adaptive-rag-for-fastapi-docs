"""
Prove the variant registry: which strategy × embedder pairs a stage selects, and
the one Qdrant collection name each pair gets.

``data_ingestion.variants`` is the only place a collection name is built (D32), so
stage 3, which writes the collections, and step 6, which reads them, cannot
disagree. These tests go through its three public seams:

- ``select_variants`` takes a strategy name or ``all`` and an embedder name or
  ``all``, keeps the registries' order, and raises ``ValueError`` naming every
  unknown name;
- ``Variant.collection`` gives ``<prefix>__<strategy>__<embedder>``, with the
  prefix defaulting to ``fastapi_docs``;
- ``check_prefix`` returns a prefix made of letters, digits, ``_`` and ``-``, and
  raises ``ValueError`` naming any other: empty, holding ``__``, ending in ``_``, or
  holding another character.

The module imports the embedder registry, whose builders could make paid calls, so
an autouse fixture refuses every outgoing socket connection (C006, no-network form).

Run offline::

    uv run pytest tests/data_ingestion/test_variants.py
"""

import socket
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))  # lets pytest import the project from any directory

from data_ingestion.embedders import EMBEDDERS  # noqa: E402  (needs the path set above)
from data_ingestion.variants import (  # noqa: E402
    DEFAULT_PREFIX,
    Variant,
    check_prefix,
    select_variants,
)

STRATEGY_ORDER = [
    "naive_small",
    "naive_medium",
    "naive_large",
    "sentence",
    "recursive",
    "semantic",
    "markdown_ast",
    "markdown_sections",
    "markdown_headers",
]


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the test on any outgoing connection (C006)."""

    def refuse(*args, **kwargs):
        raise AssertionError("a test tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def pairs(variants: list[Variant]) -> list[tuple[str, str]]:
    return [(v.strategy, v.embedder.name) for v in variants]


def test_all_by_all_gives_eighteen_variants_in_strategy_order() -> None:
    variants = select_variants("all", "all")

    assert len(variants) == 18
    assert pairs(variants) == [(s, e) for s in STRATEGY_ORDER for e in ("bge", "voyage")]


def test_a_single_name_gives_one_variant() -> None:
    variants = select_variants("naive_small", "bge")

    assert variants == [Variant("naive_small", EMBEDDERS["bge"])]


def test_one_name_and_all_gives_one_row_or_column() -> None:
    assert pairs(select_variants("recursive", "all")) == [
        ("recursive", "bge"),
        ("recursive", "voyage"),
    ]
    assert pairs(select_variants("all", "voyage")) == [(s, "voyage") for s in STRATEGY_ORDER]


def test_unknown_names_are_each_named_in_the_error() -> None:
    with pytest.raises(ValueError) as excinfo:
        select_variants("no_such_strategy", "no_such_embedder")

    message = str(excinfo.value)
    assert "no_such_strategy" in message
    assert "no_such_embedder" in message


@pytest.mark.parametrize(
    ("strategy", "embedder", "unknown", "known"),
    [
        ("no_such_strategy", "bge", "no_such_strategy", "bge"),
        ("sentence", "bogus", "bogus", "sentence"),
    ],
)
def test_one_unknown_name_is_refused(
    strategy: str, embedder: str, unknown: str, known: str
) -> None:
    with pytest.raises(ValueError, match=unknown) as excinfo:
        select_variants(strategy, embedder)

    # The message lists known names bare; only an unknown one is quoted.
    assert repr(known) not in str(excinfo.value)


def test_collection_uses_the_default_prefix() -> None:
    variant = Variant("naive_small", EMBEDDERS["bge"])

    assert DEFAULT_PREFIX == "fastapi_docs"
    assert variant.collection() == "fastapi_docs__naive_small__bge"


def test_a_prefix_replaces_the_default() -> None:
    variant = Variant("naive_small", EMBEDDERS["bge"])

    assert variant.collection("ix_verify_20261008") == "ix_verify_20261008__naive_small__bge"


@pytest.mark.parametrize(
    "prefix",
    ["fastapi_docs", "ix_verify_20261016", "PRT-09"],  # gitleaks:allow
)
def test_check_prefix_returns_a_prefix_it_accepts(prefix: str) -> None:
    assert check_prefix(prefix) == prefix


@pytest.mark.parametrize(
    "prefix",
    ["", "a__b", "a b", "fastapi_", "a/b", "dócs"],
    ids=["empty", "double-underscore", "space", "trailing-underscore", "slash", "non-ascii"],
)
def test_check_prefix_refuses_a_prefix_naming_it(prefix: str) -> None:
    with pytest.raises(ValueError) as excinfo:
        check_prefix(prefix)

    assert repr(prefix) in str(excinfo.value)
