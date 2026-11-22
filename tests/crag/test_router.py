"""
Test ``crag/router.py`` through its seams, ``Router(llm).route(query)`` and
``Router(llm).chitchat(query)``, with a fake LLM. No network (C006, the no-network form).
"""

import re
import socket

import pytest
from pydantic import ValidationError

from crag.router import CHITCHAT_PROMPT, ROUTE_PROMPT, Router

QUESTION = "How do I read a query parameter as a list?"
GREETING = "hi there, thanks for the help earlier"
LIGHT = "gemini-flash-latest"
LIGHT_FALLBACK = "gemini-flash-lite-latest"


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


def test_constructing_calls_nothing():
    llm = FakeLLM([])

    Router(llm)

    assert llm.prompts == []


@pytest.mark.parametrize("label", ["fastapi", "chitchat", "out_of_scope"])
def test_route_returns_the_label_and_the_model_that_answered(label):
    llm = FakeLLM([({"route": label}, LIGHT_FALLBACK)])

    result = Router(llm).route(QUESTION)

    assert result == {"route": label, "model": LIGHT_FALLBACK}
    assert len(llm.prompts) == 1


@pytest.mark.parametrize("label", ["unsure", "FastAPI", ""])
def test_a_label_outside_the_three_fails_validation(label):
    llm = FakeLLM([({"route": label}, LIGHT)])

    with pytest.raises(ValidationError):
        Router(llm).route(QUESTION)


def test_chitchat_returns_the_answer_and_the_model_that_answered():
    answer = "Glad it helped! Ask me anything about FastAPI."
    llm = FakeLLM([({"answer": answer}, LIGHT)])

    result = Router(llm).chitchat(GREETING)

    assert result == {"answer": answer, "model": LIGHT}
    assert len(llm.prompts) == 1


@pytest.mark.parametrize("answer", ["", "   \n\t"])
def test_an_empty_chitchat_answer_raises_naming_the_model_and_the_question(answer):
    llm = FakeLLM([({"answer": answer}, LIGHT)])

    with pytest.raises(ValueError, match="chitchat") as error:
        Router(llm).chitchat(GREETING)

    assert LIGHT in str(error.value)
    assert GREETING in str(error.value)


def test_each_prompt_ends_with_the_question():
    llm = FakeLLM([({"route": "fastapi"}, LIGHT), ({"answer": "Hello!"}, LIGHT)])
    router = Router(llm)

    router.route(QUESTION)
    router.chitchat(GREETING)

    route_prompt, chitchat_prompt = llm.prompts
    assert route_prompt.rstrip().endswith(f"Message: {QUESTION}")
    assert chitchat_prompt.rstrip().endswith(f"Message: {GREETING}")


def test_the_route_prompt_names_the_three_routes_and_keeps_unsure_questions_in_scope():
    llm = FakeLLM([({"route": "fastapi"}, LIGHT)])

    Router(llm).route(QUESTION)

    prompt = llm.prompts[0]
    assert "- fastapi:" in prompt and "- chitchat:" in prompt and "- out_of_scope:" in prompt
    assert "even when it never names FastAPI" in prompt
    assert "When you are unsure, choose fastapi." in prompt
    assert "never chitchat" in prompt


def test_the_chitchat_prompt_forbids_technical_answers_and_ends_on_an_invitation():
    llm = FakeLLM([({"answer": "Hello!"}, LIGHT)])

    Router(llm).chitchat(GREETING)

    prompt = llm.prompts[0]
    assert "Never answer a technical question" in prompt
    assert "never state a fact about FastAPI" in prompt
    assert "one or two friendly sentences" in prompt
    assert "inviting the user to ask a question about FastAPI" in prompt


def test_neither_prompt_mentions_the_held_out_route_check_question():
    # The route check's off-topic question was seen before the prompts were written (D17).
    for prompt in (ROUTE_PROMPT, CHITCHAT_PROMPT):
        assert not re.search(r"\b(rockets?|space)\b", prompt, re.IGNORECASE)


def test_an_error_from_ask_propagates():
    llm = FakeLLM([RuntimeError("gemini down")])

    with pytest.raises(RuntimeError, match="gemini down"):
        Router(llm).route(QUESTION)
