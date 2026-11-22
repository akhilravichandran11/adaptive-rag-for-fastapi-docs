import json
import socket
from types import SimpleNamespace

import pytest
from haystack import Document

from observability.costs import create_recorder
from retrieval import voyage_usage


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a test tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setenv("VOYAGE_API_KEY", "test-key")
    monkeypatch.delenv("VOYAGE_TIMEOUT", raising=False)
    monkeypatch.delenv("VOYAGE_MAX_RETRIES", raising=False)


def test_embedder_preserves_query_request_output_and_records_usage(monkeypatch, tmp_path):
    calls = []
    built = []

    def embed(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(embeddings=[[0.1, 0.2]], total_tokens=7)

    def client(**kwargs):
        built.append(kwargs)
        return SimpleNamespace(embed=embed)

    monkeypatch.setattr(voyage_usage, "Client", client)
    directory = tmp_path / "costs"
    record, finish = create_recorder(directory)
    embedder = voyage_usage.UsageVoyageTextEmbedder(
        model="voyage-4-lite", output_dimension=2048, record=record
    )
    assert built == []

    result = embedder.run("query")
    finish()

    assert result == {"embedding": [0.1, 0.2], "meta": {"total_tokens": 7}}
    assert built == [{"api_key": "test-key", "timeout": 30, "max_retries": 5}]
    assert calls == [
        {
            "texts": ["query"],
            "model": "voyage-4-lite",
            "input_type": "query",
            "truncation": True,
            "output_dtype": "float",
            "output_dimension": 2048,
        }
    ]
    row = json.loads((directory / "calls.jsonl").read_text())
    assert row == {
        "provider": "voyage",
        "operation": "embedding",
        "model": "voyage-4-lite",
        "outcome": "response",
        "usage": {"total_tokens": 7},
        "usd": 0.00000014,
        "reason": None,
    }


@pytest.mark.parametrize("operation", ["embedding", "rerank"])
def test_empty_input_dispatch_and_environment_settings_are_preserved(operation, monkeypatch):
    built, calls, records = [], [], []
    monkeypatch.setenv("VOYAGE_TIMEOUT", "12")
    monkeypatch.setenv("VOYAGE_MAX_RETRIES", "3")

    def invoke(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(embeddings=[[0.0]], results=[], total_tokens=0)

    def client(**kwargs):
        built.append(kwargs)
        return SimpleNamespace(embed=invoke, rerank=invoke)

    monkeypatch.setattr(voyage_usage, "Client", client)
    if operation == "embedding":
        component = voyage_usage.UsageVoyageTextEmbedder(
            model="voyage-4-lite",
            output_dimension=2048,
            record=lambda **kwargs: records.append(kwargs),
        )
        inputs = {"text": ""}
        expected = {"embedding": [0.0], "meta": {"total_tokens": 0}}
    else:
        component = voyage_usage.UsageVoyageRanker(
            model="rerank-2.5",
            top_k=2,
            record=lambda **kwargs: records.append(kwargs),
        )
        inputs = {"query": "", "documents": []}
        expected = {"documents": []}
    assert component.run(**inputs) == expected
    assert component.run(**inputs) == expected
    assert built == [{"api_key": "test-key", "timeout": 12, "max_retries": 3}]
    assert len(calls) == len(records) == 2
    if operation == "embedding":
        assert calls[0]["texts"] == [""]
    else:
        assert calls[0]["documents"] == []
        assert calls[0]["top_k"] == 2


def test_ranker_preserves_the_1000_document_limit_and_empty_content(monkeypatch):
    calls = []

    def rerank(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(results=[], total_tokens=10)

    monkeypatch.setattr(voyage_usage, "Client", lambda **kwargs: SimpleNamespace(rerank=rerank))
    ranker = voyage_usage.UsageVoyageRanker(
        model="rerank-2.5", top_k=2, record=lambda **kwargs: None
    )
    ranker.run("query", [Document()] + [Document(content=str(i)) for i in range(1001)])
    assert len(calls[0]["documents"]) == 1000
    assert calls[0]["documents"][0] == ""
    assert calls[0]["documents"][-1] == "998"


def test_embedder_records_one_unknown_cost_when_provider_raises(monkeypatch, tmp_path):
    calls = []

    def embed(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("provider failed with secret details")

    monkeypatch.setattr(voyage_usage, "Client", lambda **kwargs: SimpleNamespace(embed=embed))
    directory = tmp_path / "costs"
    record, finish = create_recorder(directory)
    embedder = voyage_usage.UsageVoyageTextEmbedder(
        model="voyage-4-lite", output_dimension=2048, record=record
    )
    with pytest.raises(RuntimeError, match="provider failed"):
        embedder.run("private query")
    finish(run_failed=True)

    row = json.loads((directory / "calls.jsonl").read_text())
    assert row["outcome"] == "error"
    assert row["usage"] == {}
    assert row["usd"] is None
    assert "secret" not in (directory / "calls.jsonl").read_text()
    assert len(calls) == 1


def test_ranker_preserves_order_metadata_scores_and_top_k_override(monkeypatch, tmp_path):
    calls = []

    def rerank(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            results=[SimpleNamespace(index=1, relevance_score=0.9)], total_tokens=20
        )

    monkeypatch.setattr(voyage_usage, "Client", lambda **kwargs: SimpleNamespace(rerank=rerank))
    directory = tmp_path / "costs"
    record, finish = create_recorder(directory)
    ranker = voyage_usage.UsageVoyageRanker(model="rerank-2.5", top_k=2, record=record)
    documents = [Document(content="first"), Document(content="second", meta={"source": "x"})]

    result = ranker.run("query", documents, top_k=1)
    finish()

    assert calls == [
        {
            "model": "rerank-2.5",
            "query": "query",
            "documents": ["first", "second"],
            "top_k": 1,
        }
    ]
    [document] = result["documents"]
    assert document.id == documents[1].id
    assert document.content == "second"
    assert document.meta == {"source": "x"}
    assert document.score == 0.9
    assert documents[1].score is None
    row = json.loads((directory / "calls.jsonl").read_text())
    assert row["operation"] == "rerank"
    assert row["usage"] == {"total_tokens": 20}
    assert row["usd"] == 0.000001


@pytest.mark.parametrize("operation", ["embedding", "rerank"])
@pytest.mark.parametrize("failure", ["provider", "mapping", "record"])
def test_one_invocation_is_never_recorded_twice(operation, failure, monkeypatch, tmp_path):
    calls = []
    records = []
    directory = tmp_path / "costs"
    record, finish = create_recorder(directory)

    def invoke(**kwargs):
        calls.append(kwargs)
        if failure == "provider":
            raise RuntimeError("provider failed")
        return SimpleNamespace(
            embeddings=[],
            total_tokens=7,
            results=[SimpleNamespace(index=99, relevance_score=0.9)],
        )

    def record_call(**kwargs):
        records.append(kwargs)
        if failure == "record":
            raise OSError("accounting failed")
        record(**kwargs)

    monkeypatch.setattr(
        voyage_usage, "Client", lambda **kwargs: SimpleNamespace(embed=invoke, rerank=invoke)
    )
    if operation == "embedding":
        component = voyage_usage.UsageVoyageTextEmbedder(
            model="voyage-4-lite", output_dimension=2048, record=record_call
        )
        inputs = {"text": "query"}
    else:
        component = voyage_usage.UsageVoyageRanker(model="rerank-2.5", top_k=2, record=record_call)
        inputs = {"query": "query", "documents": [Document(content="first")]}

    error = {"provider": RuntimeError, "mapping": IndexError, "record": OSError}[failure]
    with pytest.raises(error):
        component.run(**inputs)
    finish(run_failed=True)

    assert len(calls) == len(records) == 1
    assert records[0]["outcome"] == ("error" if failure == "provider" else "response")
    rows = [json.loads(line) for line in (directory / "calls.jsonl").read_text().splitlines()]
    assert len(rows) == (0 if failure == "record" else 1)


@pytest.mark.parametrize("operation", ["embedding", "rerank"])
def test_invalid_input_is_rejected_before_dispatch(operation, monkeypatch):
    built = []
    monkeypatch.setattr(voyage_usage, "Client", lambda **kwargs: built.append(kwargs))
    if operation == "embedding":
        component = voyage_usage.UsageVoyageTextEmbedder(
            model="voyage-4-lite", output_dimension=2048, record=lambda **kwargs: None
        )
        inputs, error = {"text": []}, TypeError
    else:
        component = voyage_usage.UsageVoyageRanker(
            model="rerank-2.5", top_k=2, record=lambda **kwargs: None
        )
        inputs, error = {"query": "query", "documents": [], "top_k": 0}, ValueError
    with pytest.raises(error):
        component.run(**inputs)
    assert built == []
