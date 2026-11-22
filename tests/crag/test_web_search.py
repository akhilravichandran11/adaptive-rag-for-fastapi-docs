"""
Test ``crag/web_search.py`` at its seams, ``WebSearch(client, ...)`` and ``run(queries)``, with a
fake Tavily client. The fake copies the SDK's keyword call, its always-present ``results`` list,
``raw_content`` that can be ``None``, and its real error type. No network (C006).
"""

import json
import socket

import pytest
from tavily.errors import UsageLimitExceededError

from crag.web_search import WebSearch
from observability.costs import create_recorder


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every outgoing connection raise, so no test can reach an API."""

    def refuse(*args: object, **kwargs: object):
        raise AssertionError("a test tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


class FakeTavilyClient:
    """Replies per query from a script; a query with no script gets no results."""

    def __init__(self, replies: dict[str, list[dict] | Exception] | None = None) -> None:
        self.replies = replies or {}
        self.calls: list[dict] = []

    def search(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        reply = self.replies.get(kwargs["query"], [])
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, dict):
            return reply
        return {"query": kwargs["query"], "results": reply}


def result(name: str, raw: str | None = "", content: str = "", score: float = 0.8) -> dict:
    return {
        "url": f"https://example.com/{name}",
        "title": f"Title {name}",
        "content": content,
        "raw_content": raw,
        "score": score,
    }


def test_one_search_per_query_in_order_with_default_settings() -> None:
    client = FakeTavilyClient()

    WebSearch(client).run(queries=["first", "second"])

    assert client.calls == [
        {
            "query": "first",
            "search_depth": "advanced",
            "max_results": 3,
            "include_raw_content": "markdown",
        },
        {
            "query": "second",
            "search_depth": "advanced",
            "max_results": 3,
            "include_raw_content": "markdown",
        },
    ]


def test_constructor_arguments_override_depth_and_max_results() -> None:
    client = FakeTavilyClient()

    WebSearch(client, search_depth="basic", max_results=7).run(queries=["q"])

    assert client.calls[0]["search_depth"] == "basic"
    assert client.calls[0]["max_results"] == 7


def test_documents_carry_raw_content_meta_and_score() -> None:
    client = FakeTavilyClient(
        {"q": [result("a", raw="full page a", content="snippet a", score=0.9)]}
    )

    documents = WebSearch(client).run(queries=["q"])["documents"]

    assert len(documents) == 1
    doc = documents[0]
    assert doc.content == "full page a"
    assert doc.meta == {"url": "https://example.com/a", "title": "Title a", "web_query": "q"}
    assert "source_path" not in doc.meta
    assert doc.score == 0.9


def test_content_falls_back_to_snippet_and_blank_results_are_skipped() -> None:
    client = FakeTavilyClient(
        {
            "q": [
                result("none", raw=None, content="snippet none"),
                result("empty", raw="", content="snippet empty"),
                result("blank", raw=None, content="   \n"),
                result("nothing", raw=None, content=""),
            ]
        }
    )

    documents = WebSearch(client).run(queries=["q"])["documents"]

    assert [doc.content for doc in documents] == ["snippet none", "snippet empty"]


def test_whitespace_raw_content_falls_back_to_snippet() -> None:
    client = FakeTavilyClient({"q": [result("ws", raw="   ", content="snippet ws")]})

    documents = WebSearch(client).run(queries=["q"])["documents"]

    assert [doc.content for doc in documents] == ["snippet ws"]


def test_null_title_becomes_empty_string() -> None:
    client = FakeTavilyClient({"q": [{**result("a", raw="page a"), "title": None}]})

    documents = WebSearch(client).run(queries=["q"])["documents"]

    assert documents[0].meta["title"] == ""


def test_url_found_by_two_queries_appears_once_with_the_first_query() -> None:
    client = FakeTavilyClient(
        {
            "first": [result("a", raw="page a"), result("b", raw="page b")],
            "second": [result("b", raw="page b again"), result("c", raw="page c")],
        }
    )

    documents = WebSearch(client).run(queries=["first", "second"])["documents"]

    assert [(doc.meta["url"], doc.meta["web_query"]) for doc in documents] == [
        ("https://example.com/a", "first"),
        ("https://example.com/b", "first"),
        ("https://example.com/c", "second"),
    ]
    assert documents[1].content == "page b"


def test_no_queries_makes_no_call() -> None:
    client = FakeTavilyClient()

    assert WebSearch(client).run(queries=[]) == {"documents": []}
    assert client.calls == []


def test_client_error_propagates() -> None:
    client = FakeTavilyClient(
        {"first": [result("a", raw="page a")], "second": UsageLimitExceededError("quota")}
    )

    with pytest.raises(UsageLimitExceededError, match="quota"):
        WebSearch(client).run(queries=["first", "second", "third"])
    assert [call["query"] for call in client.calls] == ["first", "second"]


class UntouchableClient:
    def __getattr__(self, name: str):
        raise AssertionError(f"construction used client.{name}")


def test_constructing_calls_nothing() -> None:
    web_search = WebSearch(UntouchableClient())

    assert web_search.search_depth == "advanced"


def test_recorded_search_keeps_measured_credits_and_documents(tmp_path):
    replies = {
        "first": {"results": [result("a", raw="page a")], "usage": {"credits": 2}},
        "second": {
            "results": [result("a", raw="duplicate"), result("b", content="snippet b")],
            "usage": {"credits": 1},
        },
    }
    plain = FakeTavilyClient(replies)
    recorded = FakeTavilyClient(replies)
    directory = tmp_path / "costs"
    record, finish = create_recorder(directory)
    expected = WebSearch(plain).run(["first", "second"])
    actual = WebSearch(recorded, record=record).run(["first", "second"])
    finish()

    assert actual == expected
    assert recorded.calls == [{**call, "include_usage": True} for call in plain.calls]
    rows = [json.loads(line) for line in (directory / "calls.jsonl").read_text().splitlines()]
    assert [row["usage"] for row in rows] == [{"credits": 2}, {"credits": 1}]
    assert [row["usd"] for row in rows] == [0.016, 0.008]
    assert all(row["outcome"] == "response" and row["model"] is None for row in rows)
    assert json.loads((directory / "summary.json").read_text())["known_estimated_usd"] == 0.024


@pytest.mark.parametrize(
    "reply, error, outcome",
    [
        (UsageLimitExceededError("private quota"), UsageLimitExceededError, "error"),
        ({"usage": {"credits": 2}}, KeyError, "response"),
    ],
)
def test_failed_search_keeps_one_record_before_document_mapping(reply, error, outcome, tmp_path):
    client = FakeTavilyClient({"q": reply})
    directory = tmp_path / "costs"
    record, finish = create_recorder(directory)
    with pytest.raises(error):
        WebSearch(client, record=record).run(["q", "not dispatched"])
    finish(run_failed=True)
    row = json.loads((directory / "calls.jsonl").read_text())
    assert row["outcome"] == outcome
    assert row["usd"] == (None if outcome == "error" else 0.016)
    assert len(client.calls) == 1
    assert "private quota" not in (directory / "calls.jsonl").read_text()


@pytest.mark.parametrize(
    "reply", [{"results": [], "usage": {"credits": 2}}, UsageLimitExceededError("quota")]
)
def test_search_accounting_failure_stops_before_another_query(reply):
    client = FakeTavilyClient({"q": reply})
    rows = []

    def fail_record(**row):
        rows.append(row)
        raise OSError("cost file cannot be written")

    with pytest.raises(OSError, match="cost file"):
        WebSearch(client, record=fail_record).run(["q", "not dispatched"])
    assert len(client.calls) == len(rows) == 1


def test_missing_search_usage_is_unknown_and_empty_queries_record_nothing(tmp_path):
    client = FakeTavilyClient()
    directory = tmp_path / "costs"
    record, finish = create_recorder(directory)
    search = WebSearch(client, record=record)
    assert search.run([]) == {"documents": []}
    assert (directory / "calls.jsonl").read_text() == ""
    search.run(["q"])
    finish()
    row = json.loads((directory / "calls.jsonl").read_text())
    assert row["usd"] is None and row["reason"] == "missing_or_invalid_usage"
