"""
Test ``crag/grader.py`` through its seam, ``DocumentGrader(llm).run(query, documents)``, with a
fake LLM. No network (C006, the no-network form).
"""

import socket
import sys
from pathlib import Path

import pytest
from haystack import Document

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))  # lets pytest import the project from any directory

from crag.grader import DocumentGrader  # noqa: E402  (needs the path set above)

QUESTION = "How do I declare a path parameter with a type?"
PRIMARY = "gemini-flash-latest"
FALLBACK = "gemini-flash-lite-latest"


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every outgoing connection raise, so no test can reach an API."""

    def refuse(*args: object, **kwargs: object):
        raise AssertionError("a test tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


class FakeLLM:
    """Copies ``StructuredLLM.ask``: validates the scripted reply against the schema it is
    given (so a reply the schema rejects fails here too) and returns it with a model name."""

    def __init__(self, script: list[tuple[dict, str] | Exception]) -> None:
        self.script = list(script)
        self.prompts: list[str] = []

    def ask(self, prompt, schema):
        self.prompts.append(prompt)
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        reply, model = step
        return schema.model_validate(reply), model


def chunk(name: str) -> Document:
    return Document(
        id=name,
        content=f"text of {name}",
        meta={"source_path": f"docs/{name}.md", "url": f"https://fastapi.tiangolo.com/{name}/"},
    )


def grade(index: int, verdict: str) -> dict:
    return {
        "doc_index": index,
        "reasoning": "r",
        "verbatim_quote": "q" if verdict == "relevant" else "",
        "verdict": verdict,
    }


def relevance(*grades: dict) -> dict:
    return {"grades": list(grades)}


def aspect(name: str, covered: bool, source_doc: int = 1) -> dict:
    return {
        "aspect": name,
        "covered": covered,
        "verbatim_quote": "q" if covered else "",
        "source_doc": source_doc if covered else 0,
    }


def coverage(all_covered: bool, *aspects: dict) -> dict:
    return {"aspects": list(aspects), "all_covered": all_covered}


def test_constructing_calls_nothing():
    llm = FakeLLM([])

    DocumentGrader(llm)

    assert llm.prompts == []


def test_no_relevant_chunk_is_incorrect_after_one_call():
    documents = [chunk("a"), chunk("b")]
    llm = FakeLLM([(relevance(grade(1, "not_relevant"), grade(2, "not_relevant")), PRIMARY)])

    result = DocumentGrader(llm).run(query=QUESTION, documents=documents)

    assert result == {
        "decision": "incorrect",
        "relevant_documents": [],
        "aspects": [],
        "missing_aspects": [],
        "model": PRIMARY,
    }
    assert len(llm.prompts) == 1
    assert QUESTION in llm.prompts[0]
    assert "[Document 1] Source: docs/a.md\ntext of a" in llm.prompts[0]
    assert "[Document 2] Source: docs/b.md\ntext of b" in llm.prompts[0]


def test_verdict_must_match_exactly():
    """Ported as is (D7): the verdict is a free string, so "Relevant" does not count."""
    llm = FakeLLM([(relevance(grade(1, "Relevant")), PRIMARY)])

    result = DocumentGrader(llm).run(query=QUESTION, documents=[chunk("a")])

    assert result["decision"] == "incorrect"
    assert len(llm.prompts) == 1


def test_error_from_relevance_call_propagates():
    llm = FakeLLM([RuntimeError("gemini down")])

    with pytest.raises(RuntimeError, match="gemini down"):
        DocumentGrader(llm).run(query=QUESTION, documents=[chunk("a")])


def test_relevant_and_all_covered_is_correct_with_relevant_chunks_only():
    documents = [chunk("a"), chunk("b"), chunk("c")]
    metas_before = [dict(doc.meta) for doc in documents]
    llm = FakeLLM(
        [
            (
                relevance(grade(3, "relevant"), grade(2, "not_relevant"), grade(1, "relevant")),
                PRIMARY,
            ),
            (coverage(True, aspect("path parameter", True)), PRIMARY),
        ]
    )

    result = DocumentGrader(llm).run(query=QUESTION, documents=documents)

    assert result["decision"] == "correct"
    assert len(llm.prompts) == 2
    assert result["relevant_documents"][0] is documents[0]
    assert result["relevant_documents"][1] is documents[2]
    assert len(result["relevant_documents"]) == 2
    assert [doc.meta for doc in documents] == metas_before
    second = llm.prompts[1]
    assert QUESTION in second
    assert "[Document 1] Source: docs/a.md\ntext of a" in second
    assert "[Document 2] Source: docs/c.md\ntext of c" in second
    assert "docs/b.md" not in second and "text of b" not in second
    assert result["aspects"] == [aspect("path parameter", True)]
    assert result["missing_aspects"] == []
    assert result["model"] == PRIMARY


def test_not_all_covered_is_ambiguous_and_lists_missing_aspects():
    llm = FakeLLM(
        [
            (relevance(grade(1, "relevant")), PRIMARY),
            (
                coverage(
                    False,
                    aspect("declare the parameter", True),
                    aspect("type conversion", False),
                    aspect("validation errors", False),
                ),
                PRIMARY,
            ),
        ]
    )

    result = DocumentGrader(llm).run(query=QUESTION, documents=[chunk("a")])

    assert result["decision"] == "ambiguous"
    assert result["missing_aspects"] == ["type conversion", "validation errors"]
    assert result["aspects"] == [
        aspect("declare the parameter", True),
        aspect("type conversion", False),
        aspect("validation errors", False),
    ]


def test_all_covered_is_trusted_over_the_aspects():
    """Ported as is (D7): all_covered decides even when an aspect says it is not covered."""
    llm = FakeLLM(
        [
            (relevance(grade(1, "relevant")), PRIMARY),
            (coverage(True, aspect("type conversion", False)), PRIMARY),
        ]
    )

    result = DocumentGrader(llm).run(query=QUESTION, documents=[chunk("a")])

    assert result["decision"] == "correct"
    assert result["missing_aspects"] == ["type conversion"]


@pytest.mark.parametrize("index", [0, -1, 3, 99])
def test_out_of_range_index_adds_nothing(index):
    documents = [chunk("a"), chunk("b")]
    llm = FakeLLM(
        [
            (relevance(grade(index, "relevant"), grade(2, "relevant")), PRIMARY),
            (coverage(True, aspect("x", True)), PRIMARY),
        ]
    )

    result = DocumentGrader(llm).run(query=QUESTION, documents=documents)

    assert len(result["relevant_documents"]) == 1
    assert result["relevant_documents"][0] is documents[1]


def test_only_out_of_range_indices_is_incorrect():
    llm = FakeLLM([(relevance(grade(5, "relevant")), PRIMARY)])

    result = DocumentGrader(llm).run(query=QUESTION, documents=[chunk("a")])

    assert result["decision"] == "incorrect"
    assert len(llm.prompts) == 1


def test_repeated_index_counts_once():
    documents = [chunk("a"), chunk("b")]
    llm = FakeLLM(
        [
            (
                relevance(grade(1, "relevant"), grade(1, "relevant"), grade(1, "not_relevant")),
                PRIMARY,
            ),
            (coverage(True, aspect("x", True)), PRIMARY),
        ]
    )

    result = DocumentGrader(llm).run(query=QUESTION, documents=documents)

    assert len(result["relevant_documents"]) == 1
    assert result["relevant_documents"][0] is documents[0]
    assert "text of b" not in llm.prompts[1]


def test_different_models_are_joined_in_call_order():
    llm = FakeLLM(
        [
            (relevance(grade(1, "relevant")), FALLBACK),
            (coverage(True, aspect("x", True)), PRIMARY),
        ]
    )

    result = DocumentGrader(llm).run(query=QUESTION, documents=[chunk("a")])

    assert result["model"] == f"{FALLBACK}, {PRIMARY}"


def test_error_from_coverage_call_propagates():
    llm = FakeLLM([(relevance(grade(1, "relevant")), PRIMARY), ValueError("bad reply")])

    with pytest.raises(ValueError, match="bad reply"):
        DocumentGrader(llm).run(query=QUESTION, documents=[chunk("a")])
