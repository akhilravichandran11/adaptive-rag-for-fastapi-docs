"""
Test ``crag/generator.py`` through its seam, ``AnswerGenerator(llm).run(query, documents)``, with
a fake LLM. No network (C006, the no-network form).
"""

import socket
import sys
from pathlib import Path

import pytest
from haystack import Document
from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))  # lets pytest import the project from any directory

from crag.generator import AnswerGenerator  # noqa: E402  (needs the path set above)

QUESTION = "How do I give a query parameter a default value?"
PRIMARY = "gemini-pro-latest"
FALLBACK = "gemini-flash-latest"


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


def indexed(name: str) -> Document:
    # Indexed chunks carry a url beside source_path (metadata.attach_metadata).
    return Document(
        content=f"chunk {name}",
        meta={"source_path": f"docs/{name}.md", "url": f"https://fastapi.tiangolo.com/{name}/"},
    )


def web(name: str) -> Document:
    return Document(content=f"page {name}", meta={"url": f"https://example.com/{name}"})


def test_constructing_calls_nothing():
    llm = FakeLLM([])

    AnswerGenerator(llm)

    assert llm.prompts == []


def test_returns_the_answer_and_the_model_that_answered():
    llm = FakeLLM([({"answer": "Give it a default [1]."}, FALLBACK)])

    result = AnswerGenerator(llm).run(query=QUESTION, documents=[indexed("a")])

    assert result == {"answer": "Give it a default [1].", "model": FALLBACK}
    assert len(llm.prompts) == 1


def test_the_prompt_numbers_excerpts_from_1_in_order_with_origin_and_source():
    documents = [web("one"), indexed("a"), web("two")]
    llm = FakeLLM([({"answer": "x"}, PRIMARY)])

    AnswerGenerator(llm).run(query=QUESTION, documents=documents)

    prompt = llm.prompts[0]
    first = '<excerpt number="1" origin="web" source="https://example.com/one">\npage one\n'
    second = '<excerpt number="2" origin="documentation" source="docs/a.md">\nchunk a\n'
    third = '<excerpt number="3" origin="web" source="https://example.com/two">\npage two\n'
    assert first in prompt and second in prompt and third in prompt
    assert prompt.index(first) < prompt.index(second) < prompt.index(third)
    assert prompt.count("<excerpt ") == 3
    assert prompt.rstrip().endswith(f"Question: {QUESTION}")


def test_a_chunk_with_both_source_path_and_url_is_documentation():
    llm = FakeLLM([({"answer": "x"}, PRIMARY)])

    AnswerGenerator(llm).run(query=QUESTION, documents=[indexed("a")])

    assert 'origin="documentation" source="docs/a.md"' in llm.prompts[0]
    assert "https://fastapi.tiangolo.com/a/" not in llm.prompts[0]


def test_the_prompt_ranks_web_below_the_documentation_and_asks_for_citations():
    llm = FakeLLM([({"answer": "x"}, PRIMARY)])

    AnswerGenerator(llm).run(query=QUESTION, documents=[indexed("a")])

    prompt = llm.prompts[0]
    assert "less authoritative than the documentation" in prompt
    assert "not instructions" in prompt
    assert "like [2]" in prompt


@pytest.mark.parametrize("answer", ["", "   \n\t"])
def test_an_empty_answer_raises_naming_the_step_and_the_question(answer):
    llm = FakeLLM([({"answer": answer}, PRIMARY)])

    with pytest.raises(ValueError, match="generate") as error:
        AnswerGenerator(llm).run(query=QUESTION, documents=[indexed("a")])

    assert QUESTION in str(error.value)


def test_a_reply_without_an_answer_fails_validation():
    llm = FakeLLM([({"text": "wrong field"}, PRIMARY)])

    with pytest.raises(ValidationError):
        AnswerGenerator(llm).run(query=QUESTION, documents=[indexed("a")])


def test_an_error_from_ask_propagates():
    llm = FakeLLM([RuntimeError("gemini down")])

    with pytest.raises(RuntimeError, match="gemini down"):
        AnswerGenerator(llm).run(query=QUESTION, documents=[indexed("a")])
