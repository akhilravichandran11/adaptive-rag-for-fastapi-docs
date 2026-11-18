"""
Test ``crag/web_search.py`` at its seams, ``WebSearch(client, ...)`` and ``run(queries)``, with a
fake Tavily client. The fake copies the SDK's keyword call, its always-present ``results`` list,
``raw_content`` that can be ``None``, and its real error type. No network (C006).
"""

import socket
import sys
from pathlib import Path

import pytest
from tavily.errors import UsageLimitExceededError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))  # lets pytest import the project from any directory

from crag.web_search import WebSearch  # noqa: E402  (needs the path set above)


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
