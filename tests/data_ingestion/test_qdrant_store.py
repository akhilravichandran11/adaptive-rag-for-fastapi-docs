"""
Prove that the shared Qdrant settings honour per-collection overrides, that a
document store carries its payload indexes and vector size, and that every Qdrant
client gets the request timeout.

``data_ingestion.qdrant_store`` holds the Qdrant code stage 1 used to own, so stage 3
can import it (a stage never imports another stage). These tests go through its three
public seams:

- ``load_settings`` takes the collection and vector size from its overrides when
  given, falls back to ``.env`` otherwise, rejects a size that is not positive, and
  names every missing variable. It reads the optional ``QDRANT_TIMEOUT`` (default
  60 s) and rejects one that is not a positive integer;
- ``build_document_store`` hands its settings and payload indexes to the Haystack
  store. That store connects lazily, so its attributes are read with no server;
- ``collection_exists`` hands the timeout to its own client, here a fake.

The real ``.env`` is never read: the module's ``load_dotenv`` is replaced with a
no-op and each test sets its own variables. An autouse fixture refuses every
outgoing socket connection (C006, no-network form). The timeout tests replace the
store and the client where ``qdrant_store`` looks them up, so no client is built.

Run offline::

    uv run pytest tests/data_ingestion/test_qdrant_store.py
"""

import socket
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))  # lets pytest import the project from any directory

from data_ingestion import qdrant_store  # noqa: E402  (needs the path set above)
from data_ingestion.qdrant_store import (  # noqa: E402
    HNSW_TUNING,
    QdrantSettings,
    build_document_store,
    collection_exists,
    load_settings,
)

QDRANT_VARS = ("QDRANT_URL", "QDRANT_API_KEY", "QDRANT_COLLECTION", "EMBEDDING_DIMENSION")
PAYLOAD_INDEXES = [
    {"field_name": "meta.bucket", "field_schema": "keyword"},
    {"field_name": "meta.knowledge_domain", "field_schema": "keyword"},
]


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the test on any outgoing connection (C006)."""

    def refuse(*args, **kwargs):
        raise AssertionError("a test tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the real .env out: no dotenv loading, and no Qdrant variables set."""
    monkeypatch.setattr(qdrant_store, "load_dotenv", lambda *args, **kwargs: None)
    for name in (*QDRANT_VARS, "QDRANT_TIMEOUT"):
        monkeypatch.delenv(name, raising=False)


def set_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QDRANT_URL", "http://qdrant.invalid:6333")
    monkeypatch.setenv("QDRANT_API_KEY", "test-key")


# --- load_settings ---------------------------------------------------------------


def test_both_overrides_mean_collection_and_dimension_are_not_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_connection(monkeypatch)

    settings = load_settings(collection_override="ix_bge", dimension_override=1024)

    assert settings == QdrantSettings(
        url="http://qdrant.invalid:6333", collection="ix_bge", embedding_dim=1024
    )


def test_overrides_win_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    set_connection(monkeypatch)
    monkeypatch.setenv("QDRANT_COLLECTION", "from_env")
    monkeypatch.setenv("EMBEDDING_DIMENSION", "1024")

    settings = load_settings(collection_override="ix_voyage", dimension_override=2048)

    assert settings.collection == "ix_voyage"
    assert settings.embedding_dim == 2048


def test_no_overrides_fall_back_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    set_connection(monkeypatch)
    monkeypatch.setenv("QDRANT_COLLECTION", "from_env")
    monkeypatch.setenv("EMBEDDING_DIMENSION", "1024")

    settings = load_settings(collection_override=None, dimension_override=None)

    assert settings.collection == "from_env"
    assert settings.embedding_dim == 1024


@pytest.mark.parametrize("bad_dimension", [0, -1, -2048])
def test_dimension_override_that_is_not_positive_is_rejected(
    monkeypatch: pytest.MonkeyPatch, bad_dimension: int
) -> None:
    set_connection(monkeypatch)
    # A valid .env size must not rescue a bad override.
    monkeypatch.setenv("EMBEDDING_DIMENSION", "1024")

    with pytest.raises(ValueError, match="positive integer"):
        load_settings(collection_override="ix_bge", dimension_override=bad_dimension)


def test_env_dimension_that_is_not_an_integer_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    set_connection(monkeypatch)
    monkeypatch.setenv("QDRANT_COLLECTION", "from_env")
    monkeypatch.setenv("EMBEDDING_DIMENSION", "large")

    with pytest.raises(ValueError, match="EMBEDDING_DIMENSION.*'large'"):
        load_settings()


def test_every_missing_variable_is_named() -> None:
    with pytest.raises(ValueError) as excinfo:
        load_settings()

    for name in QDRANT_VARS:
        assert name in str(excinfo.value)


def test_overrides_do_not_excuse_missing_connection_variables() -> None:
    with pytest.raises(ValueError) as excinfo:
        load_settings(collection_override="ix_bge", dimension_override=1024)

    message = str(excinfo.value)
    assert "QDRANT_URL" in message
    assert "QDRANT_API_KEY" in message
    assert "QDRANT_COLLECTION" not in message
    assert "EMBEDDING_DIMENSION" not in message


@pytest.mark.parametrize("unset", [None, ""])
def test_timeout_defaults_to_60_seconds_when_unset_or_empty(
    monkeypatch: pytest.MonkeyPatch, unset: str | None
) -> None:
    set_connection(monkeypatch)
    if unset is not None:
        monkeypatch.setenv("QDRANT_TIMEOUT", unset)

    settings = load_settings(collection_override="ix_bge", dimension_override=1024)

    assert settings.timeout == 60


def test_timeout_is_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    set_connection(monkeypatch)
    monkeypatch.setenv("QDRANT_TIMEOUT", "120")

    settings = load_settings(collection_override="ix_voyage", dimension_override=2048)

    assert settings.timeout == 120


@pytest.mark.parametrize("bad_timeout", ["0", "-5", "slow", "1.5"])
def test_timeout_that_is_not_a_positive_integer_is_rejected(
    monkeypatch: pytest.MonkeyPatch, bad_timeout: str
) -> None:
    set_connection(monkeypatch)
    monkeypatch.setenv("QDRANT_TIMEOUT", bad_timeout)

    with pytest.raises(ValueError) as excinfo:
        load_settings(collection_override="ix_bge", dimension_override=1024)

    message = str(excinfo.value)
    assert "QDRANT_TIMEOUT" in message
    assert repr(bad_timeout) in message


# --- build_document_store ----------------------------------------------------------


def test_store_receives_settings_and_payload_indexes(monkeypatch: pytest.MonkeyPatch) -> None:
    set_connection(monkeypatch)
    settings = QdrantSettings(
        url="http://qdrant.invalid:6333", collection="ix_voyage", embedding_dim=2048
    )

    store = build_document_store(
        settings,
        recreate=True,
        return_embedding=False,
        payload_fields_to_index=PAYLOAD_INDEXES,
    )

    assert store.index == "ix_voyage"
    assert store.embedding_dim == 2048
    assert store.recreate_index is True
    assert store.payload_fields_to_index == PAYLOAD_INDEXES
    assert store.hnsw_config == HNSW_TUNING


def test_store_defaults_attach_without_payload_indexes(monkeypatch: pytest.MonkeyPatch) -> None:
    set_connection(monkeypatch)
    settings = QdrantSettings(
        url="http://qdrant.invalid:6333", collection="ix_bge", embedding_dim=1024
    )

    store = build_document_store(settings)

    assert store.recreate_index is False
    assert store.payload_fields_to_index is None


def test_store_receives_the_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    received = {}
    monkeypatch.setattr(
        qdrant_store, "QdrantDocumentStore", lambda **kwargs: received.update(kwargs)
    )
    settings = QdrantSettings(
        url="http://qdrant.invalid:6333", collection="ix_voyage", embedding_dim=2048, timeout=90
    )

    build_document_store(settings)

    assert received["timeout"] == 90


# --- collection_exists -------------------------------------------------------------


def test_collection_check_client_receives_the_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    set_connection(monkeypatch)
    received = {}

    class FakeClient:
        def __init__(self, **kwargs):
            received.update(kwargs)

        def collection_exists(self, name: str) -> bool:
            return name == "ix_voyage"

        def close(self) -> None:
            pass

    monkeypatch.setattr(qdrant_store, "QdrantClient", FakeClient)
    settings = QdrantSettings(
        url="http://qdrant.invalid:6333", collection="ix_voyage", embedding_dim=2048, timeout=90
    )

    assert collection_exists(settings) is True
    assert received["timeout"] == 90
