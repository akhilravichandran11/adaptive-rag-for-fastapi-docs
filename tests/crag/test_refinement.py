"""
Test ``crag/refinement.py`` through ``KnowledgeRefinement.run`` with a fake LLM. The fake is
called from worker threads, so it records under a lock and picks each reply by a key found in
the prompt (a page url), never by call order. No network (C006, the no-network form).
"""

import socket
import sys
import threading
from pathlib import Path

import pytest
from haystack import Document

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))  # lets pytest import the project from any directory

from crag import refinement  # noqa: E402  (needs the path set above)
from crag.refinement import KnowledgeRefinement  # noqa: E402

QUESTION = "How do I declare a path parameter with a type?"
REGRADE_KEY = "[Document 1]"
ASPECTS = [
    {"aspect": "path parameters", "covered": True, "verbatim_quote": "q", "source_doc": 1},
    {"aspect": "types", "covered": False, "verbatim_quote": "", "source_doc": 0},
]
MISSING = ["types"]


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every outgoing connection raise, so no test can reach an API."""

    def refuse(*args: object, **kwargs: object):
        raise AssertionError("a test tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


class FakeLLM:
    """Like ``StructuredLLM.ask``: validates the scripted dict against the schema it is given
    and returns ``(parsed, model)``, or raises the scripted error. Safe to call from threads.
    ``hooks[key]`` runs before the reply; ``after[key]`` runs once the key is in ``completed``."""

    def __init__(
        self, replies: dict[str, tuple[dict | Exception, str]], hooks=None, after=None
    ) -> None:
        self.replies = replies
        self.hooks = hooks or {}
        self.after = after or {}
        self.calls: list[tuple[str, type]] = []
        self.completed: list[str] = []
        self.lock = threading.Lock()

    def ask(self, prompt, schema):
        with self.lock:
            self.calls.append((prompt, schema))
        key = next(key for key in self.replies if key in prompt)
        if key in self.hooks:
            self.hooks[key]()
        reply, model = self.replies[key]
        if isinstance(reply, Exception):
            raise reply
        parsed = schema.model_validate(reply)
        with self.lock:
            self.completed.append(key)
        if key in self.after:
            self.after[key]()
        return parsed, model

    def schemas(self) -> list[str]:
        return [schema.__name__ for _, schema in self.calls]


def internal(doc_id: str) -> Document:
    return Document(
        id=doc_id,
        content=f"chunk {doc_id}",
        meta={"source_path": f"docs/{doc_id}.md", "url": f"https://fastapi.tiangolo.com/{doc_id}/"},
        score=0.5,
    )


def web(name: str, score: float = 0.7) -> Document:
    return Document(
        content=f"page {name} content",
        meta={"url": f"https://example.com/{name}", "title": f"Title {name}", "web_query": "q"},
        score=score,
    )


def grade(index: int, relevant: bool) -> dict:
    return {
        "doc_index": index,
        "relevant": relevant,
        "addresses_aspect": "types" if relevant else "",
        "verbatim_quote": "quote" if relevant else "",
        "reasoning": "why",
    }


def grades(*items: dict) -> dict:
    return {"grades": list(items)}


def extraction(*passages: tuple[str, list[str]], useful: bool = True) -> dict:
    return {
        "extracts": [
            {"verbatim_passage": passage, "aspects_addressed": aspects, "reasoning": "why"}
            for passage, aspects in passages
        ],
        "document_useful": useful,
    }


def url(name: str) -> str:
    return f"https://example.com/{name}"


def run(llm, relevant=(), reretrieved=(), web_documents=(), aspects=ASPECTS, missing=MISSING):
    return KnowledgeRefinement(llm).run(
        query=QUESTION,
        aspects=aspects,
        missing_aspects=missing,
        relevant_documents=list(relevant),
        reretrieval_documents=list(reretrieved),
        web_documents=list(web_documents),
    )


def test_constructing_calls_nothing():
    llm = FakeLLM({})
    KnowledgeRefinement(llm)
    assert llm.calls == []


def test_relevant_chunks_pass_through_as_the_same_objects_with_no_call():
    llm = FakeLLM({})
    relevant = [internal("a"), internal("b")]
    meta_before = [dict(doc.meta) for doc in relevant]

    result = run(llm, relevant=relevant)

    assert llm.calls == []
    assert all(kept is doc for kept, doc in zip(result["refined_context"], relevant, strict=True))
    assert [doc.meta for doc in relevant] == meta_before
    assert result == {
        "refined_context": relevant,
        "internal_kept": 2,
        "reretrieval_kept": 0,
        "web_kept": 0,
        "model": None,
    }


def test_reretrieved_chunks_are_regraded_in_one_call_keeping_relevant_ones_once_in_order():
    reretrieved = [internal("c"), internal("d"), internal("e"), internal("f")]
    llm = FakeLLM(
        {
            REGRADE_KEY: (
                # Out of order, a repeat, an out-of-range index, and a not-relevant grade.
                grades(
                    grade(4, True), grade(1, True), grade(4, True), grade(9, True), grade(2, False)
                ),
                "regrade-model",
            )
        }
    )
    meta_before = [dict(doc.meta) for doc in reretrieved]

    result = run(llm, reretrieved=reretrieved)

    assert llm.schemas() == ["ChunkGrades"]
    prompt = llm.calls[0][0]
    assert QUESTION in prompt
    assert "- types" in prompt
    assert "[Document 4] Source: docs/f.md\nchunk f" in prompt
    kept = result["refined_context"]
    assert [doc.id for doc in kept] == ["c", "f"]
    assert kept[0] is reretrieved[0] and kept[1] is reretrieved[3]
    assert [doc.meta for doc in reretrieved] == meta_before
    assert result["reretrieval_kept"] == 2
    assert result["model"] == "regrade-model"


def test_no_regrade_call_when_there_are_no_reretrieved_chunks():
    llm = FakeLLM({url("one"): (extraction(("passage", ["types"])), "web-model")})

    run(llm, web_documents=[web("one")])

    assert llm.schemas() == ["PageExtraction"]


def test_a_useful_page_becomes_one_new_document_with_the_extract_layout():
    page = web("one", score=0.83)
    llm = FakeLLM(
        {
            url("one"): (
                extraction(
                    ("Use `item_id: int`.", ["types"]),
                    ("```python\n@app.get('/items/{item_id}')\n```", ["path parameters", "types"]),
                ),
                "web-model",
            )
        }
    )

    result = run(llm, web_documents=[page])

    (extracted,) = result["refined_context"]
    assert extracted is not page
    assert extracted.content == (
        "[Extract 1] Addresses: types\nUse `item_id: int`.\n\n"
        "[Extract 2] Addresses: path parameters, types\n"
        "```python\n@app.get('/items/{item_id}')\n```"
    )
    assert extracted.meta == {"url": url("one"), "title": "Title one"}
    assert "source_path" not in extracted.meta
    assert extracted.score == 0.83
    assert result["web_kept"] == 1
    assert result["model"] == "web-model"


def test_the_extraction_prompt_lists_covered_and_missing_aspects_and_the_page():
    llm = FakeLLM({url("one"): (extraction(useful=False), "web-model")})

    run(llm, web_documents=[web("one")])

    ((prompt, schema),) = llm.calls
    assert schema is refinement.PageExtraction
    covered_part, missing_part = prompt.split("Aspects still missing:")
    assert "- path parameters" in covered_part and "- types" not in covered_part
    assert "- types" in missing_part and "- path parameters" not in missing_part
    assert url("one") in prompt
    assert "Title one" in prompt
    assert "page one content" in prompt


def test_with_no_aspects_the_extraction_prompt_says_none_were_identified():
    llm = FakeLLM({url("one"): (extraction(("passage", [])), "web-model")})

    result = run(llm, web_documents=[web("one")], aspects=[], missing=[])

    ((prompt, _),) = llm.calls
    assert prompt.count("(none identified)") == 2
    assert QUESTION in prompt
    assert result["refined_context"][0].content == "[Extract 1] Addresses: \npassage"


@pytest.mark.parametrize(
    "reply",
    [extraction(useful=False), extraction(), extraction(("passage", ["types"]), useful=False)],
)
def test_a_page_that_is_not_useful_or_has_no_extracts_is_dropped(reply):
    llm = FakeLLM({url("one"): (reply, "web-model")})

    result = run(llm, web_documents=[web("one")])

    assert result["refined_context"] == []
    assert result["web_kept"] == 0
    assert result["model"] == "web-model"


@pytest.mark.parametrize("blank", ["", "  \n\t "])
def test_a_useful_page_whose_only_extract_is_blank_is_dropped(blank):
    llm = FakeLLM({url("one"): (extraction((blank, ["types"])), "web-model")})

    result = run(llm, web_documents=[web("one")])

    assert result["refined_context"] == []
    assert result["web_kept"] == 0


def test_blank_extracts_are_left_out_and_the_rest_renumbered():
    llm = FakeLLM(
        {url("one"): (extraction(("   ", ["types"]), ("real passage", ["types"])), "web-model")}
    )

    result = run(llm, web_documents=[web("one")])

    (page,) = result["refined_context"]
    assert page.content == "[Extract 1] Addresses: types\nreal passage"
    assert result["web_kept"] == 1


def test_context_order_is_relevant_then_reretrieved_then_web_in_input_order():
    relevant = [internal("a")]
    reretrieved = [internal("c"), internal("d")]
    pages = [web("one"), web("two"), web("three")]
    third_page_finished = threading.Event()
    llm = FakeLLM(
        {
            REGRADE_KEY: (grades(grade(2, True)), "regrade-model"),
            url("one"): (extraction(("from one", ["types"])), "web-model"),
            url("two"): (extraction(useful=False), "web-model"),
            url("three"): (extraction(("from three", ["types"])), "web-model"),
        },
        # Page one waits until page three has finished, so completion order is not input order.
        hooks={url("one"): lambda: third_page_finished.wait(timeout=5)},
        after={url("three"): third_page_finished.set},
    )

    result = run(llm, relevant=relevant, reretrieved=reretrieved, web_documents=pages)

    context = result["refined_context"]
    assert context[0] is relevant[0]
    assert context[1] is reretrieved[1]
    assert [doc.meta["url"] for doc in context[2:]] == [url("one"), url("three")]
    web_completed = [key for key in llm.completed if key != REGRADE_KEY]
    assert web_completed.index(url("three")) < web_completed.index(url("one"))
    assert result["internal_kept"] == 1
    assert result["reretrieval_kept"] == 1
    assert result["web_kept"] == 2
    assert len(context) == sum(
        result[key] for key in ("internal_kept", "reretrieval_kept", "web_kept")
    )


def test_each_web_page_gets_one_call_and_at_most_three_run_at_once():
    names = ["p1", "p2", "p3", "p4", "p5", "p6"]
    # The barrier needs three calls at once, so fewer workers time out. Each group of three
    # then stays open briefly, so a fourth worker would start a call and raise most_active.
    three_at_once = threading.Barrier(3, timeout=5)
    fourth_started = threading.Event()
    active = 0
    most_active = 0
    lock = threading.Lock()

    def track():
        nonlocal active, most_active
        with lock:
            active += 1
            most_active = max(most_active, active)
            if active > 3:
                fourth_started.set()
        three_at_once.wait()
        fourth_started.wait(timeout=0.3)
        with lock:
            active -= 1

    llm = FakeLLM(
        {url(name): (extraction(("passage", ["types"])), "web-model") for name in names},
        hooks={url(name): track for name in names},
    )

    result = run(llm, web_documents=[web(name) for name in names])

    assert refinement.EXTRACT_WORKERS == 3
    assert most_active == 3
    assert len(llm.calls) == len(names)
    for name in names:
        assert sum(url(name) in prompt for prompt, _ in llm.calls) == 1
    assert result["web_kept"] == len(names)


def test_model_joins_the_distinct_models_in_call_order():
    llm = FakeLLM(
        {
            REGRADE_KEY: (grades(grade(1, True)), "flash"),
            url("one"): (extraction(("passage", ["types"])), "flash-lite"),
            url("two"): (extraction(("passage", ["types"])), "flash"),
            url("three"): (extraction(("passage", ["types"])), "pro"),
        }
    )

    result = run(
        llm, reretrieved=[internal("c")], web_documents=[web("one"), web("two"), web("three")]
    )

    assert result["model"] == "flash, flash-lite, pro"


def test_a_regrade_error_propagates():
    llm = FakeLLM({REGRADE_KEY: (RuntimeError("regrade failed"), "m")})

    with pytest.raises(RuntimeError, match="regrade failed"):
        run(llm, reretrieved=[internal("c")], web_documents=[web("one")])
    assert llm.schemas() == ["ChunkGrades"]


def test_an_extraction_error_in_a_worker_thread_propagates():
    llm = FakeLLM(
        {
            url("one"): (extraction(("passage", ["types"])), "web-model"),
            url("two"): (RuntimeError("extraction of two failed"), "web-model"),
        }
    )

    with pytest.raises(RuntimeError, match="extraction of two failed"):
        run(llm, web_documents=[web("one"), web("two")])


def test_no_input_document_or_list_is_changed():
    relevant = [internal("a")]
    reretrieved = [internal("c"), internal("d")]
    pages = [web("one"), web("two")]
    aspects = [dict(aspect) for aspect in ASPECTS]
    missing = list(MISSING)
    snapshot = [
        (doc.id, doc.content, dict(doc.meta), doc.score)
        for doc in [*relevant, *reretrieved, *pages]
    ]
    lists_before = [list(relevant), list(reretrieved), list(pages)]
    llm = FakeLLM(
        {
            REGRADE_KEY: (grades(grade(1, True), grade(2, False)), "m"),
            url("one"): (extraction(("passage", ["types"])), "m"),
            url("two"): (extraction(useful=False), "m"),
        }
    )

    KnowledgeRefinement(llm).run(
        query=QUESTION,
        aspects=aspects,
        missing_aspects=missing,
        relevant_documents=relevant,
        reretrieval_documents=reretrieved,
        web_documents=pages,
    )

    assert [
        (doc.id, doc.content, dict(doc.meta), doc.score)
        for doc in [*relevant, *reretrieved, *pages]
    ] == snapshot
    assert [relevant, reretrieved, pages] == lists_before
    assert aspects == ASPECTS
    assert missing == MISSING
