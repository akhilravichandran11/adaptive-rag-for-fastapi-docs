"""
Prove ``crag/llm.py``'s retry, single fallback and fail-fast rules (D5), offline (C006).
The fake client copies the SDK's keyword-only call, ``.text`` and ``.model_version`` reply
(``model_version`` may be ``None``) and real error types.
"""

import json
import logging
import socket
from types import SimpleNamespace

import pytest
from google.genai import errors, types
from pydantic import BaseModel, ValidationError
from tenacity import wait_none

from crag import llm
from observability.costs import create_recorder

PRIMARY = "gemini-pro-latest"
FALLBACK = "fallback-model"
VERSIONS = {PRIMARY: "gemini-3-pro-preview", FALLBACK: "fallback-model-002"}
GOOD_REPLY = '{"verdict": "yes", "score": 0.9}'


class Grade(BaseModel):
    verdict: str
    score: float


def rate_limit() -> errors.ClientError:
    return errors.ClientError(
        429, {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}}
    )


def server_error() -> errors.ServerError:
    return errors.ServerError(
        503, {"error": {"code": 503, "message": "overloaded", "status": "UNAVAILABLE"}}
    )


def bad_request() -> errors.ClientError:
    return errors.ClientError(
        400, {"error": {"code": 400, "message": "bad schema", "status": "INVALID_ARGUMENT"}}
    )


class FakeModels:
    """Replies from a per-model script, in order; an exception in the script is raised.
    Every reply from a model carries that model's version from ``versions``."""

    def __init__(self, scripts: dict[str, list], versions: dict[str, str | None]) -> None:
        self.scripts = {model: list(script) for model, script in scripts.items()}
        self.versions = versions
        self.calls: list[dict] = []

    def generate_content(self, *, model, contents, config=None):
        self.calls.append({"model": model, "contents": contents, "config": config})
        result = self.scripts[model].pop(0)
        if isinstance(result, Exception):
            raise result
        if isinstance(result, types.GenerateContentResponse):
            return result
        return SimpleNamespace(text=result, model_version=self.versions[model])

    def models_called(self) -> list[str]:
        return [call["model"] for call in self.calls]


def fake_client(
    scripts: dict[str, list], versions: dict[str, str | None] = VERSIONS
) -> SimpleNamespace:
    return SimpleNamespace(models=FakeModels(scripts, versions))


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the test on any outgoing connection (C006)."""

    def refuse(*args, **kwargs):
        raise AssertionError("a test tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.fixture(autouse=True)
def no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retries run at once; each call copies the decorator's ``Retrying``, wait included."""
    monkeypatch.setattr(llm.StructuredLLM._generate.retry, "wait", wait_none())


def test_good_reply_returns_parsed_and_model_version_after_one_call():
    client = fake_client({PRIMARY: [GOOD_REPLY]})

    parsed, model = llm.StructuredLLM(client, primary=PRIMARY).ask("Is it relevant?", Grade)

    assert parsed == Grade(verdict="yes", score=0.9)
    assert model == VERSIONS[PRIMARY]
    assert client.models.calls == [
        {
            "model": PRIMARY,
            "contents": "Is it relevant?",
            "config": {
                "response_mime_type": "application/json",
                "response_schema": Grade,
                "temperature": 0,
            },
        }
    ]


@pytest.mark.parametrize("missing_version", [None, ""])
def test_reply_without_model_version_returns_the_alias_called(missing_version):
    client = fake_client({PRIMARY: [GOOD_REPLY]}, versions={PRIMARY: missing_version})

    parsed, model = llm.StructuredLLM(client, primary=PRIMARY).ask("q", Grade)

    assert parsed == Grade(verdict="yes", score=0.9)
    assert model == PRIMARY


def test_fallback_without_model_version_returns_the_fallback_alias():
    client = fake_client(
        {PRIMARY: [server_error()] * 3, FALLBACK: [GOOD_REPLY]},
        versions={PRIMARY: VERSIONS[PRIMARY], FALLBACK: None},
    )

    _, model = llm.StructuredLLM(client, primary=PRIMARY, fallback=FALLBACK).ask("q", Grade)

    assert model == FALLBACK


def test_fallback_defaults_to_flash_lite():
    assert (
        llm.StructuredLLM(fake_client({}), primary=PRIMARY).fallback == "gemini-flash-lite-latest"
    )


def test_two_rate_limits_then_good_reply_retries_primary():
    client = fake_client({PRIMARY: [rate_limit(), rate_limit(), GOOD_REPLY]})

    parsed, model = llm.StructuredLLM(client, primary=PRIMARY).ask("q", Grade)

    assert parsed == Grade(verdict="yes", score=0.9)
    assert model == VERSIONS[PRIMARY]
    assert client.models.models_called() == [PRIMARY] * 3


@pytest.mark.parametrize("bad_text", ["not json", '{"verdict": "yes"}', None])
def test_invalid_reply_is_retried_like_a_rate_limit(bad_text):
    client = fake_client({PRIMARY: [bad_text, GOOD_REPLY]})

    parsed, model = llm.StructuredLLM(client, primary=PRIMARY).ask("q", Grade)

    assert parsed == Grade(verdict="yes", score=0.9)
    assert model == VERSIONS[PRIMARY]
    assert client.models.models_called() == [PRIMARY] * 2


def test_primary_exhausted_falls_back_and_warns_naming_both_models(caplog):
    client = fake_client(
        {
            PRIMARY: [rate_limit(), server_error(), "not json"],
            FALLBACK: [GOOD_REPLY],
        }
    )

    with caplog.at_level(logging.WARNING, logger=llm.__name__):
        parsed, model = llm.StructuredLLM(client, primary=PRIMARY, fallback=FALLBACK).ask(
            "q", Grade
        )

    assert parsed == Grade(verdict="yes", score=0.9)
    assert model == VERSIONS[FALLBACK]
    assert client.models.models_called() == [PRIMARY] * llm.MAX_ATTEMPTS + [FALLBACK]
    fallback_warnings = [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING and "falling back" in record.getMessage()
    ]
    assert len(fallback_warnings) == 1
    assert PRIMARY in fallback_warnings[0]
    assert FALLBACK in fallback_warnings[0]
    assert VERSIONS[PRIMARY] not in fallback_warnings[0]
    assert VERSIONS[FALLBACK] not in fallback_warnings[0]


def test_both_models_fail_the_last_error_propagates():
    last_error = server_error()
    client = fake_client(
        {
            PRIMARY: [rate_limit()] * 3,
            FALLBACK: [rate_limit(), "not json", last_error],
        }
    )

    with pytest.raises(errors.ServerError) as raised:
        llm.StructuredLLM(client, primary=PRIMARY, fallback=FALLBACK).ask("q", Grade)

    assert raised.value is last_error
    assert client.models.models_called() == [PRIMARY] * 3 + [FALLBACK] * 3


def test_both_models_return_invalid_json_raises_validation_error():
    client = fake_client({PRIMARY: ["not json"] * 3, FALLBACK: ["not json"] * 3})

    with pytest.raises(ValidationError):
        llm.StructuredLLM(client, primary=PRIMARY, fallback=FALLBACK).ask("q", Grade)

    assert len(client.models.calls) == 6


def test_bad_request_raises_after_one_call_without_fallback():
    error = bad_request()
    client = fake_client({PRIMARY: [error], FALLBACK: [GOOD_REPLY]})

    with pytest.raises(errors.ClientError) as raised:
        llm.StructuredLLM(client, primary=PRIMARY, fallback=FALLBACK).ask("q", Grade)

    assert raised.value is error
    assert client.models.models_called() == [PRIMARY]


def test_bad_request_on_fallback_raises_after_one_fallback_call():
    error = bad_request()
    client = fake_client({PRIMARY: [server_error()] * 3, FALLBACK: [error, GOOD_REPLY]})

    with pytest.raises(errors.ClientError) as raised:
        llm.StructuredLLM(client, primary=PRIMARY, fallback=FALLBACK).ask("q", Grade)

    assert raised.value is error
    assert client.models.models_called() == [PRIMARY] * 3 + [FALLBACK]


def test_plain_value_error_raises_after_one_call_without_fallback():
    error = ValueError("Unsupported schema type")
    client = fake_client({PRIMARY: [error], FALLBACK: [GOOD_REPLY]})

    with pytest.raises(ValueError) as raised:
        llm.StructuredLLM(client, primary=PRIMARY, fallback=FALLBACK).ask("q", Grade)

    assert raised.value is error
    assert client.models.models_called() == [PRIMARY]


def usage_reply(text, model="gemini-3.1-pro-preview"):
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(parts=[types.Part(text=text)]))],
        model_version=model,
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=100,
            cached_content_token_count=20,
            candidates_token_count=10,
            thoughts_token_count=5,
            total_token_count=115,
        ),
    )


def test_recording_keeps_usage_for_invalid_replies_and_fallback():
    client = fake_client(
        {
            PRIMARY: [usage_reply("not json")] * 3,
            FALLBACK: [usage_reply(GOOD_REPLY, "gemini-3.5-flash-lite")],
        }
    )
    rows = []
    parsed, model = llm.StructuredLLM(
        client, primary=PRIMARY, fallback=FALLBACK, record=lambda **row: rows.append(row)
    ).ask("private prompt", Grade)

    assert parsed == Grade(verdict="yes", score=0.9)
    assert model == "gemini-3.5-flash-lite"
    assert client.models.models_called() == [PRIMARY] * 3 + [FALLBACK]
    assert len(rows) == 4
    assert [row["model"] for row in rows] == ["gemini-3.1-pro-preview"] * 3 + [model]
    assert all(row["provider"] == "gemini" and row["outcome"] == "response" for row in rows)
    assert all(
        row["usage"]
        == {
            "prompt_token_count": 100,
            "cached_content_token_count": 20,
            "candidates_token_count": 10,
            "thoughts_token_count": 5,
            "total_token_count": 115,
        }
        for row in rows
    )
    assert "private prompt" not in str(rows)


@pytest.mark.parametrize(
    "error", [rate_limit(), server_error(), bad_request(), ValueError("bad request")]
)
def test_recorded_provider_errors_keep_unknown_cost_and_existing_retry_rules(error, tmp_path):
    client = fake_client({PRIMARY: [error] * 3, FALLBACK: [usage_reply(GOOD_REPLY)]})
    directory = tmp_path / "costs"
    record, finish = create_recorder(directory)
    model = llm.StructuredLLM(client, primary=PRIMARY, fallback=FALLBACK, record=record)
    retryable = isinstance(error, errors.ServerError) or getattr(error, "code", None) == 429
    if retryable:
        model.ask("private prompt", Grade)
    else:
        with pytest.raises(type(error)) as raised:
            model.ask("private prompt", Grade)
        assert raised.value is error
    finish()

    rows = [json.loads(line) for line in (directory / "calls.jsonl").read_text().splitlines()]
    failed = rows[:-1] if retryable else rows
    assert len(failed) == (3 if retryable else 1)
    assert all(row["outcome"] == "error" and row["usd"] is None for row in failed)
    assert all(row["usage"] == {} and row["reason"] == "missing_or_invalid_usage" for row in failed)
    assert len(rows) == len(client.models.calls)
    assert "private prompt" not in (directory / "calls.jsonl").read_text()


@pytest.mark.parametrize("reply", [usage_reply("not json"), rate_limit()])
def test_accounting_io_error_does_not_retry_or_fall_back(reply):
    client = fake_client({PRIMARY: [reply, GOOD_REPLY], FALLBACK: [GOOD_REPLY]})
    rows = []

    def fail_record(**row):
        rows.append(row)
        raise OSError("cost file cannot be written")

    with pytest.raises(OSError, match="cost file"):
        llm.StructuredLLM(client, primary=PRIMARY, fallback=FALLBACK, record=fail_record).ask(
            "q", Grade
        )
    assert client.models.models_called() == [PRIMARY]
    assert len(rows) == 1


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        types.GenerateContentResponseUsageMetadata(prompt_token_count=10, candidates_token_count=2),
    ],
)
def test_unresolved_model_alias_is_never_priced(metadata, tmp_path):
    response = usage_reply(GOOD_REPLY, model=None)
    response.usage_metadata = metadata
    client = fake_client({PRIMARY: [response]})
    directory = tmp_path / "costs"
    record, finish = create_recorder(directory)
    _, model = llm.StructuredLLM(client, primary=PRIMARY, record=record).ask("q", Grade)
    finish()
    row = json.loads((directory / "calls.jsonl").read_text())
    assert model == row["model"] == PRIMARY
    assert row["usd"] is None
    assert row["reason"] == (
        "missing_or_invalid_usage" if metadata is None else "unsupported_operation_or_model"
    )
