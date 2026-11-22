"""
Test ``crag/decomposer.py`` at its seams with a fake LLM, offline (C006, the no-network form).
The fake copies ``StructuredLLM.ask``'s contract: it validates the reply against the schema it
is given, returns ``(parsed, model)``, and raises what it is scripted to raise.
"""

import socket

import pytest
from haystack import Document
from pydantic import BaseModel

from crag.decomposer import QueryDecomposer

QUESTION = "How do I return a custom status code and set a cookie?"
MODEL = "gemini-flash-latest"


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every outgoing connection raise, so no test can reach an API."""

    def refuse(*args: object, **kwargs: object):
        raise AssertionError("a test tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


class FakeLLM:
    def __init__(self, reply: dict | Exception, model: str = MODEL) -> None:
        self.reply = reply
        self.model = model
        self.prompts: list[str] = []

    def ask(self, prompt: str, schema: type[BaseModel]) -> tuple[BaseModel, str]:
        self.prompts.append(prompt)
        if isinstance(self.reply, Exception):
            raise self.reply
        return schema.model_validate(self.reply), self.model


def analysis(aspect: str, vector_query: str, web_query: str) -> dict:
    return {
        "missing_aspect": aspect,
        "user_need": f"needs {aspect}",
        "retrieval_gap_reason": "the chunks cover a neighbouring topic",
        "target_document_type": "tutorial page",
        "vector_query": vector_query,
        "web_query": web_query,
    }


def chunk(name: str, content: str) -> Document:
    return Document(content=content, meta={"source_path": f"docs/{name}.md"})


def test_no_missing_aspects_makes_no_call() -> None:
    llm = FakeLLM(AssertionError("ask must not be called"))

    result = QueryDecomposer(llm).run(
        query=QUESTION, missing_aspects=[], relevant_documents=[chunk("a", "text")]
    )

    assert result == {"vector_queries": [], "web_queries": [], "model": None}
    assert llm.prompts == []


def test_construction_calls_nothing() -> None:
    llm = FakeLLM(AssertionError("ask must not be called"))

    QueryDecomposer(llm)

    assert llm.prompts == []


def test_one_call_for_several_aspects_keeps_reply_order() -> None:
    llm = FakeLLM(
        {
            "analyses": [
                analysis("status code", "set the response status code", "fastapi status_code"),
                analysis("cookie", "set a cookie on the response", "fastapi response set_cookie"),
            ]
        }
    )

    result = QueryDecomposer(llm).run(
        query=QUESTION,
        missing_aspects=["cookie", "status code"],
        relevant_documents=[chunk("a", "text")],
    )

    assert len(llm.prompts) == 1
    assert result == {
        "vector_queries": ["set the response status code", "set a cookie on the response"],
        "web_queries": ["fastapi status_code", "fastapi response set_cookie"],
        "model": MODEL,
    }


def test_reply_with_more_analyses_than_aspects_is_kept_as_is() -> None:
    llm = FakeLLM({"analyses": [analysis("a", "q1", "w1"), analysis("b", "q2", "w2")]})

    result = QueryDecomposer(llm).run(query=QUESTION, missing_aspects=["a"], relevant_documents=[])

    assert result["vector_queries"] == ["q1", "q2"]
    assert result["web_queries"] == ["w1", "w2"]


def test_prompt_holds_question_aspects_and_relevant_chunks() -> None:
    llm = FakeLLM({"analyses": []})
    documents = [
        chunk("tutorial/response-status-code", "Use the status_code parameter {braces}."),
        chunk("advanced/response-cookies", "Declare a Response parameter."),
    ]

    QueryDecomposer(llm).run(
        query=QUESTION,
        missing_aspects=["cookie lifetime", "status code for errors"],
        relevant_documents=documents,
    )

    prompt = llm.prompts[0]
    assert QUESTION in prompt
    assert "- cookie lifetime" in prompt
    assert "- status code for errors" in prompt
    for doc in documents:
        assert doc.meta["source_path"] in prompt
        assert doc.content in prompt


def test_llm_error_propagates() -> None:
    llm = FakeLLM(RuntimeError("both models failed"))

    with pytest.raises(RuntimeError, match="both models failed"):
        QueryDecomposer(llm).run(query=QUESTION, missing_aspects=["cookie"], relevant_documents=[])
