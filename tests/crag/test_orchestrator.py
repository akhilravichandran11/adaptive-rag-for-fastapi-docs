"""
Test ``crag/orchestrator.py``'s routes through its seams with fake adapters. They share one
call log so call order across adapters is checked. No network (C006, the no-network form).
"""

import dataclasses
import json
import socket
from datetime import datetime

import pytest
from haystack import Document

from crag import orchestrator
from crag.orchestrator import CragOrchestrator

QUESTION = "How do I declare a path parameter?"


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every outgoing connection raise, so no test can reach an API."""

    def refuse(*args: object, **kwargs: object):
        raise AssertionError("a test tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def internal(doc_id: str, score: float = 0.5) -> Document:
    return Document(
        id=doc_id,
        content=f"chunk {doc_id}",
        # Indexed chunks carry a url beside source_path (metadata.attach_metadata).
        meta={"source_path": f"docs/{doc_id}.md", "url": f"https://fastapi.tiangolo.com/{doc_id}/"},
        score=score,
    )


def web(name: str) -> Document:
    return Document(content=f"page {name}", meta={"url": f"https://example.com/{name}"})


class FakeAdapter:
    def __init__(self, name: str, log: list, reply: dict | Exception) -> None:
        self.name = name
        self.log = log
        self.reply = reply

    def run(self, **kwargs):
        self.log.append((self.name, kwargs))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def fake_retrieve(log: list, results: dict[str, list[Document]]):
    def retrieve(query: str, top_k: int) -> list[Document]:
        log.append(("retrieve", {"query": query, "top_k": top_k}))
        return results.get(query, [])

    return retrieve


def build(log: list, results: dict[str, list[Document]], **replies) -> CragOrchestrator:
    defaults = {
        "grader": {
            "decision": "correct",
            "relevant_documents": [],
            "aspects": [],
            "missing_aspects": [],
            "model": "grade-model",
        },
        "decomposer": {"vector_queries": [], "web_queries": [], "model": "decompose-model"},
        "web_search": {"documents": []},
        "refinement": {
            "refined_context": [],
            "internal_kept": 0,
            "reretrieval_kept": 0,
            "web_kept": 0,
            "model": "refine-model",
        },
        "generator": {"answer": "Declare it in the path [1].", "model": "generate-model"},
    }
    defaults.update(replies)
    return CragOrchestrator(
        fake_retrieve(log, results),
        **{name: FakeAdapter(name, log, reply) for name, reply in defaults.items()},
    )


def names(log: list) -> list[str]:
    return [name for name, _ in log]


def step_names(result) -> list[str]:
    return [step["step"] for step in result.steps]


# The real grader's aspect dicts (CoverageReply), which refinement reads.
ASPECTS = [
    {"aspect": "path parameters", "covered": True, "verbatim_quote": "q", "source_doc": 1},
    {"aspect": "types", "covered": False, "verbatim_quote": "", "source_doc": 0},
]


def grade(decision: str, relevant: list[Document], missing: list[str] | None = None) -> dict:
    return {
        "decision": decision,
        "relevant_documents": relevant,
        "aspects": ASPECTS,
        "missing_aspects": missing or [],
        "model": "grade-model",
    }


def test_constructing_calls_no_adapter():
    log: list = []
    build(log, {})
    assert log == []


def test_correct_generates_from_the_relevant_documents():
    log: list = []
    retrieved = [internal("a", 0.9), internal("b", 0.4), internal("c", 0.2)]
    relevant = retrieved[:2]
    crag = build(log, {QUESTION: retrieved}, grader=grade("correct", relevant))

    result = crag.run(QUESTION)

    assert names(log) == ["retrieve", "grader", "generator"]
    assert log[0][1] == {"query": QUESTION, "top_k": orchestrator.RETRIEVE_TOP_K}
    assert log[1][1] == {"query": QUESTION, "documents": retrieved}
    assert log[2][1] == {"query": QUESTION, "documents": relevant}
    assert result.route == "correct"
    assert result.answer == "Declare it in the path [1]."
    assert result.sources == [
        {
            "rank": 1,
            "origin": "internal",
            "source": "docs/a.md",
            "content": "chunk a",
            "score": 0.9,
        },
        {
            "rank": 2,
            "origin": "internal",
            "source": "docs/b.md",
            "content": "chunk b",
            "score": 0.4,
        },
    ]
    assert step_names(result) == ["retrieve", "grade", "generate"]


def test_ambiguous_decomposes_reretrieves_searches_refines_and_generates_in_order():
    log: list = []
    retrieved = [internal("a"), internal("b")]
    relevant = [retrieved[0]]
    reretrieved_1 = [internal("a"), internal("d")]  # "a" is already held
    reretrieved_2 = [internal("d"), internal("e")]  # "d" came back for the first query
    web_documents = [web("one")]
    refined = [relevant[0], internal("d"), web("one")]
    crag = build(
        log,
        {QUESTION: retrieved, "vector one": reretrieved_1, "vector two": reretrieved_2},
        grader=grade("ambiguous", relevant, missing=["types"]),
        decomposer={
            "vector_queries": ["vector one", "vector two"],
            "web_queries": ["web one"],
            "model": "decompose-model",
        },
        web_search={"documents": web_documents},
        refinement={
            "refined_context": refined,
            "internal_kept": 1,
            "reretrieval_kept": 1,
            "web_kept": 1,
            "model": "refine-model",
        },
    )

    result = crag.run(QUESTION)

    assert names(log) == [
        "retrieve",
        "grader",
        "decomposer",
        "retrieve",
        "retrieve",
        "web_search",
        "refinement",
        "generator",
    ]
    assert log[2][1] == {
        "query": QUESTION,
        "missing_aspects": ["types"],
        "relevant_documents": relevant,
    }
    assert [call["query"] for name, call in log if name == "retrieve"] == [
        QUESTION,
        "vector one",
        "vector two",
    ]
    assert log[5][1] == {"queries": ["web one"]}
    refine_call = log[6][1]
    assert refine_call["aspects"] == ASPECTS
    assert refine_call["missing_aspects"] == ["types"]
    assert refine_call["relevant_documents"] == relevant
    assert [doc.id for doc in refine_call["reretrieval_documents"]] == ["d", "e"]
    assert refine_call["web_documents"] == web_documents
    assert log[7][1] == {"query": QUESTION, "documents": refined}

    assert result.route == "ambiguous"
    assert result.missing_aspects == ["types"]
    assert [source["origin"] for source in result.sources] == ["internal", "internal", "web"]
    assert result.sources[2]["source"] == "https://example.com/one"
    assert step_names(result) == [
        "retrieve",
        "grade",
        "decompose",
        "reretrieve",
        "web_search",
        "refine",
        "generate",
    ]
    assert result.steps[3]["counts"] == {"queries": 2, "retrieved": 4, "new": 2}
    assert result.steps[5]["counts"] == {
        "relevant": 1,
        "reretrieved": 2,
        "web": 1,
        "refined": 3,
        "internal_kept": 1,
        "reretrieval_kept": 1,
        "web_kept": 1,
    }


@pytest.mark.parametrize(
    ("vector_queries", "web_queries", "expected_calls", "expected_steps"),
    [
        ([], ["web one"], ["web_search"], ["web_search"]),
        (["vector one"], [], ["retrieve"], ["reretrieve"]),
        ([], [], [], []),
    ],
)
def test_ambiguous_skips_the_part_with_no_decomposed_queries(
    vector_queries, web_queries, expected_calls, expected_steps
):
    log: list = []
    retrieved = [internal("a")]
    crag = build(
        log,
        {QUESTION: retrieved, "vector one": [internal("d")]},
        grader=grade("ambiguous", retrieved),
        decomposer={"vector_queries": vector_queries, "web_queries": web_queries, "model": "m"},
        refinement={
            "refined_context": retrieved,
            "internal_kept": 1,
            "reretrieval_kept": 0,
            "web_kept": 0,
            "model": "m",
        },
    )

    result = crag.run(QUESTION)

    assert names(log) == [
        "retrieve",
        "grader",
        "decomposer",
        *expected_calls,
        "refinement",
        "generator",
    ]
    assert step_names(result) == [
        "retrieve",
        "grade",
        "decompose",
        *expected_steps,
        "refine",
        "generate",
    ]
    assert result.steps[2]["counts"] == {
        "vector_queries": len(vector_queries),
        "web_queries": len(web_queries),
    }


def test_incorrect_searches_the_web_with_the_question_and_refines_web_only():
    log: list = []
    web_documents = [web("one"), web("two")]
    refined = [web("one")]
    crag = build(
        log,
        {QUESTION: [internal("a")]},
        grader=grade("incorrect", []),
        web_search={"documents": web_documents},
        refinement={
            "refined_context": refined,
            "internal_kept": 0,
            "reretrieval_kept": 0,
            "web_kept": 1,
            "model": "refine-model",
        },
    )

    result = crag.run(QUESTION)

    assert names(log) == ["retrieve", "grader", "web_search", "refinement", "generator"]
    assert log[2][1] == {"queries": [QUESTION]}
    assert log[3][1] == {
        "query": QUESTION,
        "aspects": [],
        "missing_aspects": [],
        "relevant_documents": [],
        "reretrieval_documents": [],
        "web_documents": web_documents,
    }
    assert log[4][1] == {"query": QUESTION, "documents": refined}
    assert result.route == "incorrect"
    assert [source["origin"] for source in result.sources] == ["web"]
    assert step_names(result) == ["retrieve", "grade", "web_search", "refine", "generate"]
    assert result.steps[3]["counts"] == {"web": 2, "refined": 1, "web_kept": 1}


def test_incorrect_with_no_web_documents_falls_back_without_generating():
    log: list = []
    crag = build(log, {QUESTION: [internal("a")]}, grader=grade("incorrect", []))

    result = crag.run(QUESTION)

    assert names(log) == ["retrieve", "grader", "web_search"]
    assert result.route == "incorrect"
    assert result.answer == orchestrator.FALLBACK_ANSWER
    assert result.sources == []
    assert step_names(result) == ["retrieve", "grade", "web_search", "fallback"]


def test_incorrect_with_an_empty_refined_context_falls_back_without_generating():
    log: list = []
    crag = build(
        log,
        {QUESTION: [internal("a")]},
        grader=grade("incorrect", []),
        web_search={"documents": [web("one")]},
    )

    result = crag.run(QUESTION)

    assert names(log) == ["retrieve", "grader", "web_search", "refinement"]
    assert result.route == "incorrect"
    assert result.answer == orchestrator.FALLBACK_ANSWER
    assert step_names(result) == ["retrieve", "grade", "web_search", "refine", "fallback"]


def test_no_retrieved_documents_falls_back_without_grading_or_searching():
    log: list = []
    crag = build(log, {})

    result = crag.run(QUESTION)

    assert names(log) == ["retrieve"]
    assert result.route == orchestrator.NO_DOCUMENTS_ROUTE == "no_documents"
    assert result.answer == orchestrator.FALLBACK_ANSWER
    assert "web" not in result.answer.lower()
    assert step_names(result) == ["retrieve", "fallback"]
    assert result.steps[0]["counts"] == {"documents": 0}


def test_correct_with_no_relevant_documents_falls_back_without_generating():
    log: list = []
    crag = build(log, {QUESTION: [internal("a")]}, grader=grade("correct", []))

    result = crag.run(QUESTION)

    assert names(log) == ["retrieve", "grader"]
    assert result.route == "correct"
    assert result.answer == orchestrator.FALLBACK_ANSWER
    assert result.sources == []
    assert step_names(result) == ["retrieve", "grade", "fallback"]


def test_ambiguous_with_an_empty_refined_context_falls_back_without_generating():
    log: list = []
    retrieved = [internal("a")]
    crag = build(
        log,
        {QUESTION: retrieved, "vector one": [internal("d")]},
        grader=grade("ambiguous", retrieved, missing=["types"]),
        decomposer={"vector_queries": ["vector one"], "web_queries": ["web one"], "model": "m"},
        web_search={"documents": [web("one")]},
    )

    result = crag.run(QUESTION)

    assert "generator" not in names(log)
    assert result.route == "ambiguous"
    assert result.answer == orchestrator.FALLBACK_ANSWER
    assert result.missing_aspects == ["types"]
    assert step_names(result)[-2:] == ["refine", "fallback"]


def test_steps_carry_times_counts_and_the_model_that_answered():
    log: list = []
    retrieved = [internal("a")]
    crag = build(log, {QUESTION: retrieved}, grader=grade("correct", retrieved))

    result = crag.run(QUESTION)

    for step in result.steps:
        assert datetime.fromisoformat(step["started"]) <= datetime.fromisoformat(step["ended"])
        assert isinstance(step["counts"], dict)
    retrieve_step, grade_step, generate_step = result.steps
    assert "model" not in retrieve_step
    assert grade_step["counts"] == {"relevant": 1, "total": 1}
    assert grade_step["model"] == "grade-model"
    assert generate_step["model"] == "generate-model"


def test_the_ambiguous_result_converts_to_json():
    log: list = []
    retrieved = [internal("a")]
    crag = build(
        log,
        {QUESTION: retrieved, "vector one": [internal("d")]},
        grader=grade("ambiguous", retrieved, missing=["types"]),
        decomposer={"vector_queries": ["vector one"], "web_queries": ["web one"], "model": "m"},
        web_search={"documents": [web("one")]},
        refinement={
            "refined_context": [retrieved[0], web("one")],
            "internal_kept": 1,
            "reretrieval_kept": 0,
            "web_kept": 1,
            "model": "m",
        },
    )

    record = dataclasses.asdict(crag.run(QUESTION))

    assert json.loads(json.dumps(record))["route"] == "ambiguous"


@pytest.mark.parametrize(
    ("failing", "decision"),
    [
        ("grader", "correct"),
        ("generator", "correct"),
        ("decomposer", "ambiguous"),
        ("web_search", "ambiguous"),
        ("refinement", "ambiguous"),
        ("web_search", "incorrect"),
        ("refinement", "incorrect"),
    ],
)
def test_an_adapter_exception_propagates(failing, decision):
    log: list = []
    retrieved = [internal("a")]
    replies = {
        "grader": grade(decision, retrieved),
        "decomposer": {"vector_queries": ["vector one"], "web_queries": ["web one"], "model": "m"},
        "web_search": {"documents": [web("one")]},
        "refinement": {
            "refined_context": retrieved,
            "internal_kept": 1,
            "reretrieval_kept": 0,
            "web_kept": 0,
            "model": "m",
        },
    }
    replies[failing] = RuntimeError(f"{failing} failed")
    crag = build(log, {QUESTION: retrieved}, **replies)

    with pytest.raises(RuntimeError, match=f"{failing} failed"):
        crag.run(QUESTION)


def test_a_retrieve_exception_propagates():
    def retrieve(query: str, top_k: int) -> list[Document]:
        raise RuntimeError("qdrant down")

    adapter = FakeAdapter("unused", [], {})
    crag = CragOrchestrator(retrieve, adapter, adapter, adapter, adapter, adapter)

    with pytest.raises(RuntimeError, match="qdrant down"):
        crag.run(QUESTION)


def test_a_reretrieval_exception_propagates():
    log: list = []
    retrieved = [internal("a")]

    def retrieve(query: str, top_k: int) -> list[Document]:
        log.append(("retrieve", {"query": query, "top_k": top_k}))
        if query == "vector one":
            raise RuntimeError("qdrant down on re-retrieval")
        return retrieved

    crag = CragOrchestrator(
        retrieve,
        FakeAdapter("grader", log, grade("ambiguous", retrieved)),
        FakeAdapter(
            "decomposer", log, {"vector_queries": ["vector one"], "web_queries": [], "model": "m"}
        ),
        FakeAdapter("web_search", log, {"documents": []}),
        FakeAdapter(
            "refinement",
            log,
            {
                "refined_context": retrieved,
                "internal_kept": 1,
                "reretrieval_kept": 0,
                "web_kept": 0,
                "model": "m",
            },
        ),
        FakeAdapter("generator", log, {"answer": "unused", "model": "m"}),
    )

    with pytest.raises(RuntimeError, match="re-retrieval"):
        crag.run(QUESTION)
    assert names(log) == ["retrieve", "grader", "decomposer", "retrieve"]


def test_an_unknown_decision_raises_naming_it():
    log: list = []
    crag = build(log, {QUESTION: [internal("a")]}, grader=grade("maybe", []))

    with pytest.raises(ValueError, match="'maybe'"):
        crag.run(QUESTION)
