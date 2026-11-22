import copy
import hashlib
import json
import socket
from contextlib import contextmanager
from datetime import UTC, datetime
from email.utils import format_datetime
from importlib import metadata
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import openai
import pytest
import tiktoken
from openai.types.responses import Response

from data_curation.pipeline_v2 import analysis
from data_curation.pipeline_v2.analysis import analyze_run, retry_failed
from data_curation.pipeline_v2.recipe import build_analysis_recipe
from data_curation.pipeline_v2.runs import prepare_run


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network access forbidden")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


@pytest.fixture
def manifest(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    entries = []
    for name, text in [("a", "FastAPI café\r\n"), ("b", "Depends example\n")]:
        body = text.encode("utf-8")
        (docs / f"{name}.md").write_bytes(body)
        entries.append(
            dict(
                source_path=f"guide/{name}.md",
                output_filename=f"{name}.md",
                url=f"https://example.invalid/{name}",
                bucket="guide",
                bytes=len(body),
            )
        )
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            dict(
                schema_version=1,
                stage="normalized",
                source=dict(id="github-docs", type="git", ref="a" * 40),
                files=entries,
            )
        )
    )
    return path


def test_recipe_snapshots_survive_preparation(manifest, tmp_path, monkeypatch):

    monkeypatch.setattr(metadata, "version", lambda name: "0.13.0")
    recipe = build_analysis_recipe()
    assert (
        hashlib.sha256(recipe.prompt_template.encode()).hexdigest()
        == "75280d1dbca907d241385096b2c861da097c5e61a16be03322f1b35107fb2f6b"
    )
    schema_bytes = json.dumps(
        recipe.response_schema,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode()
    assert (
        hashlib.sha256(schema_bytes).hexdigest()
        == "622efb6094639d9dc26dec7393bded4f6a5b09344bc9c4e00399bf42f91a2f57"
    )
    run = prepare_run(manifest, recipe=recipe, output_root=tmp_path / "runs")
    saved = json.loads((run / "run_metadata.json").read_bytes())
    assert saved["prompt_template"] == recipe.prompt_template
    assert saved["response_schema"] == recipe.response_schema
    assert saved["input_policy"]["estimator"]["package_version"] == "0.13.0"
    assert set(p.name for p in run.iterdir()) == {"run_metadata.json", "source_inventory.json"}
    assert recipe.response_schema["properties"].keys() == {
        "scores",
        "topics",
        "injection_suspected",
        "injection_evidence",
        "knowledge_domain",
        "content_type",
        "content_kind",
        "code_examples_present",
        "one_sentence_summary",
        "key_concepts",
        "fastapi_features",
    }


@pytest.fixture
def run_dir(manifest, tmp_path):

    return prepare_run(manifest, recipe=build_analysis_recipe(), output_root=tmp_path / "runs")


@pytest.mark.parametrize("change", ["source", "inventory", "prompt", "schema", "version", "run_id"])
def test_analysis_rejects_changed_prepared_inputs(run_dir, manifest, change):

    if change == "source":
        (manifest.parent / "docs/a.md").write_bytes(b"changed")
    elif change == "inventory":
        with (run_dir / "source_inventory.json").open("ab") as stream:
            stream.write(b" ")
    else:
        path = run_dir / "run_metadata.json"
        value = json.loads(path.read_bytes())
        if change == "prompt":
            value["prompt_template"] += "changed"
        elif change == "schema":
            value["response_schema"]["description"] = "changed"
        elif change == "version":
            value["input_policy"]["estimator"]["package_version"] = "old"
        else:
            value["run_id"] = "20000101_000000_abcd"
        path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        analyze_run(run_dir)
    assert not (run_dir / "analysis_results.json").exists()


def test_minimum_request_must_fit_before_any_call(run_dir, monkeypatch):

    monkeypatch.setattr(
        tiktoken,
        "get_encoding",
        lambda name: SimpleNamespace(encode_ordinary=lambda text: range(30001)),
    )
    with pytest.raises(ValueError, match="fit"):
        analyze_run(run_dir)
    assert not (run_dir / "analysis_results.json").exists()


@pytest.fixture
def prediction():
    return dict(
        scores=dict(
            information_density=8, practical_value=9, clarity=7, completeness=8, self_contained=8
        ),
        topics=["dependencies"],
        injection_suspected=False,
        injection_evidence="",
        knowledge_domain="core_concepts",
        content_type="reference",
        content_kind="docs",
        code_examples_present=False,
        one_sentence_summary="Describes dependencies.",
        key_concepts=["dependency injection"],
        fastapi_features=["Depends"],
    )


@pytest.fixture
def provider(monkeypatch, prediction):

    responses = []
    requests = []
    snapshots = []
    counted = []
    state = SimpleNamespace(
        responses=responses,
        requests=requests,
        snapshots=snapshots,
        counted=counted,
        path=None,
        before_call=None,
        count=lambda text: 1000,
        headers={},
    )

    def encode(text):
        counted.append(text)
        return range(state.count(text))

    def create(**request):
        requests.append(request)
        if state.path:
            snapshots.append(json.loads(state.path.read_bytes()))
        if state.before_call:
            state.before_call()
        value = (
            responses.pop(0)
            if responses
            else dict(
                status="completed",
                model="returned-model",
                error=None,
                incomplete_details=None,
                output=[
                    dict(
                        type="message",
                        role="assistant",
                        status="completed",
                        content=[dict(type="output_text", text=json.dumps(prediction))],
                    )
                ],
            )
        )
        if isinstance(value, BaseException):
            raise value
        return SimpleNamespace(
            headers=state.headers, parse=lambda: Response.model_construct(**value)
        )

    client = SimpleNamespace(
        responses=SimpleNamespace(with_raw_response=SimpleNamespace(create=create)), close=Mock()
    )
    state.constructor = Mock(return_value=client)
    state.client = client
    monkeypatch.setattr(openai, "OpenAI", state.constructor)
    monkeypatch.setattr(
        tiktoken, "get_encoding", lambda name: SimpleNamespace(encode_ordinary=encode)
    )
    return state


def test_analysis_reserves_then_saves_complete_results(run_dir, provider, prediction):

    provider.path = run_dir / "analysis_results.json"
    originals = {p.name: p.read_bytes() for p in run_dir.iterdir()}
    assert analyze_run(run_dir) == provider.path
    saved = json.loads(provider.path.read_bytes())
    assert [d["source_id"] for d in saved["documents"]] == ["guide/a.md", "guide/b.md"]
    assert len(provider.requests) == 2
    for index, doc in enumerate(saved["documents"]):
        cycle = doc["cycles"][0]
        assert cycle["outcome"] == "ready"
        assert cycle["model_analysis"] == prediction
        assert cycle["analysis_error"] is None
        assert cycle["attempts"][0]["state"] == "succeeded"
        assert cycle["attempts"][0]["response_model"] == "returned-model"
        assert (
            provider.snapshots[index]["documents"][index]["cycles"][0]["attempts"][0]["state"]
            == "reserved"
        )
    assert provider.constructor.call_args.kwargs == {"timeout": 240, "max_retries": 0}
    provider.client.close.assert_called_once()
    request = provider.requests[0]
    assert request["model"] == "gpt-5.4-mini-2026-03-17"
    assert request["max_output_tokens"] == 8000
    assert request["reasoning"] == {"effort": "low"}
    assert request["text"]["format"]["strict"] is True
    assert len(request["input"]) == 1
    assert request["input"][0]["content"][1]["text"] == "FastAPI café\r\n"
    assert (
        json.dumps(
            request, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        )
        in provider.counted
    )
    assert all((run_dir / name).read_bytes() == body for name, body in originals.items())


def test_complete_run_reuses_results_without_client(run_dir, provider):

    path = analyze_run(run_dir)
    before = path.read_bytes()
    provider.constructor.reset_mock()
    assert analyze_run(run_dir) == path
    assert path.read_bytes() == before
    provider.constructor.assert_not_called()
    assert len(provider.requests) == 2


@pytest.mark.parametrize(
    "change",
    [
        "missing_document",
        "extra_document",
        "input_hash",
        "attempt_number",
        "attempts_four",
        "success_without_prediction",
        "wrong_outcome",
        "provenance",
        "unknown_field",
    ],
)
def test_malformed_checkpoint_is_never_reset(run_dir, provider, change):

    path = analyze_run(run_dir)
    saved = json.loads(path.read_bytes())
    cycle = saved["documents"][0]["cycles"][0]
    if change == "missing_document":
        saved["documents"].pop()
    elif change == "extra_document":
        saved["documents"].append(saved["documents"][0])
    elif change == "input_hash":
        saved["input_hashes"]["run_metadata"] = "0" * 64
    elif change == "attempt_number":
        cycle["attempts"][0]["attempt"] = 2
    elif change == "attempts_four":
        cycle["attempts"] *= 4
    elif change == "success_without_prediction":
        cycle["model_analysis"] = None
    elif change == "wrong_outcome":
        cycle["outcome"] = "quarantined"
    elif change == "provenance":
        saved["documents"][0]["supplied_document"]["text_sha256"] = "0" * 64
    else:
        saved["unexpected"] = 1
    path.write_text(json.dumps(saved))
    before = path.read_bytes()
    with pytest.raises(ValueError):
        analyze_run(run_dir)
    assert path.read_bytes() == before
    assert len(provider.requests) == 2


def response_with(prediction, **overrides):
    return dict(
        status="completed",
        model="returned-model",
        error=None,
        incomplete_details=None,
        output=[
            dict(
                type="message",
                role="assistant",
                status="completed",
                content=[dict(type="output_text", text=json.dumps(prediction))],
            )
        ],
        **overrides,
    )


@pytest.mark.parametrize(
    "case,code,stops",
    [
        ("invalid_json", "invalid_json", False),
        ("missing_field", "invalid_analysis", False),
        ("bool_score", "invalid_analysis", False),
        ("false_evidence", "invalid_analysis", False),
        ("missing_quote", "invalid_analysis", False),
        ("max_output_tokens", "output_token_limit", False),
        ("content_filter", "content_filtered", False),
        ("refusal", "model_refusal", False),
        ("empty", "empty_output", False),
        ("unknown_status", "unexpected_provider_response", True),
        ("bad_output", "unexpected_provider_response", True),
        ("unexplained", "unexpected_provider_response", True),
    ],
)
def test_bad_output_is_not_repaired(run_dir, provider, prediction, case, code, stops):

    response = response_with(prediction)
    content = response["output"][0]["content"][0]
    if case == "invalid_json":
        content["text"] = "```json\n" + content["text"] + "\n```"
    elif case in ("missing_field", "bool_score", "false_evidence", "missing_quote"):
        if case == "missing_field":
            prediction.pop("fastapi_features")
        elif case == "bool_score":
            prediction["scores"]["clarity"] = True
        elif case == "false_evidence":
            prediction["injection_evidence"] = "FastAPI"
        else:
            prediction.update(injection_suspected=True, injection_evidence="absent quote")
        content["text"] = json.dumps(prediction)
    elif case in ("max_output_tokens", "content_filter", "unexplained"):
        response.update(status="incomplete", incomplete_details={"reason": case})
    elif case == "refusal":
        response["output"][0]["content"] = [{"type": "refusal", "refusal": "No"}]
    elif case == "empty":
        response["output"] = []
    elif case == "unknown_status":
        response["status"] = "unknown"
    else:
        response["output"] = "not a list"
    provider.responses.append(response)
    if stops:
        if case == "bad_output":
            with (
                pytest.warns(UserWarning, match="Pydantic serializer warnings"),
                pytest.raises(RuntimeError, match=code),
            ):
                analyze_run(run_dir)
        else:
            with pytest.raises(RuntimeError, match=code):
                analyze_run(run_dir)
    else:
        analyze_run(run_dir)
    saved = json.loads((run_dir / "analysis_results.json").read_bytes())
    cycle = saved["documents"][0]["cycles"][0]
    assert cycle["analysis_error"]["code"] == code
    assert cycle["analysis_error"]["attempts"] == 1
    assert cycle["model_analysis"] is None
    assert cycle["attempts"][0]["response_model"] == "returned-model"
    assert len(provider.requests) == (1 if stops else 2)
    if stops:
        assert saved["documents"][1]["cycles"] == []
    provider.client.close.assert_called_once()


@pytest.fixture
def clock(monkeypatch):

    state = SimpleNamespace(now=1800000000.0, sleeps=[])

    def sleep(seconds):
        state.sleeps.append(seconds)
        state.now += seconds

    monkeypatch.setattr(analysis, "time", SimpleNamespace(time=lambda: state.now, sleep=sleep))
    return state


def sdk_error(status, code=None, headers=None):

    response = httpx.Response(
        status, headers=headers, request=httpx.Request("POST", "https://example.invalid")
    )
    return openai.APIStatusError("secret-token raw error", response=response, body={"code": code})


@pytest.mark.parametrize(
    "status,code,expected,temporary",
    [
        (408, None, "provider_transient_error", True),
        (409, None, "provider_transient_error", True),
        (429, "rate_limit_exceeded", "provider_rate_limited", True),
        (503, None, "provider_server_error", True),
        (401, None, "authentication_error", False),
        (403, None, "permission_denied", False),
        (429, "insufficient_quota", "quota_exhausted", False),
        (429, "billing_hard_limit_reached", "quota_exhausted", False),
        (429, "unknown", "request_rejected", False),
        (404, "model_not_found", "model_unavailable", False),
        (400, "context_length_exceeded", "input_too_large", False),
        (400, "invalid_schema", "request_rejected", False),
    ],
)
def test_provider_error_mapping_and_bounded_attempts(
    run_dir, provider, clock, status, code, expected, temporary
):

    provider.responses.extend([sdk_error(status, code)] * (3 if temporary else 1))
    if temporary:
        analyze_run(run_dir)
    else:
        with pytest.raises(RuntimeError, match=expected):
            analyze_run(run_dir)
    saved = json.loads((run_dir / "analysis_results.json").read_bytes())
    cycle = saved["documents"][0]["cycles"][0]
    assert cycle["analysis_error"]["code"] == expected
    assert cycle["analysis_error"]["attempts"] == (3 if temporary else 1)
    assert len(provider.requests) == (4 if temporary else 1)
    assert all(a["response_model"] is None for a in cycle["attempts"])
    assert "secret-token" not in json.dumps(saved)
    assert clock.sleeps == ([5, 15] if temporary else [])


@pytest.mark.parametrize(
    "headers,wait",
    [
        ({"retry-after-ms": "7000", "Retry-After": "40"}, 7),
        ({"retry-after-ms": "bad", "Retry-After": "8"}, 8),
        ({"Retry-After": "-3"}, 5),
        ({"retry-after-ms": "nan", "Retry-After": "inf"}, 5),
        ({"Retry-After": "0"}, 5),
        ({"Retry-After": "15"}, 15),
    ],
)
def test_retry_headers_and_fallback(run_dir, provider, clock, headers, wait):
    provider.responses.append(sdk_error(429, "rate_limit_exceeded", headers))
    analyze_run(run_dir)
    assert clock.sleeps == [wait]
    assert len(provider.requests) == 3


def test_long_cooldown_survives_restart_without_reserving(run_dir, provider, clock):
    provider.responses.append(sdk_error(429, "rate_limit_exceeded", {"Retry-After": "120"}))
    with pytest.raises(RuntimeError, match="cooldown"):
        analyze_run(run_dir)
    path = run_dir / "analysis_results.json"
    saved = json.loads(path.read_bytes())
    assert len(saved["documents"][0]["cycles"][0]["attempts"]) == 1
    assert saved["documents"][0]["cycles"][0]["outcome"] is None
    assert saved["documents"][1]["cycles"] == []
    assert saved["retry_not_before"] is not None
    clock.now += 100
    analyze_run(run_dir)
    assert clock.sleeps == [20]
    assert len(provider.requests) == 3


def test_interrupted_reservations_consume_budget(run_dir, provider, clock):
    for number in (1, 2, 3):
        provider.responses.append(KeyboardInterrupt())
        with pytest.raises(KeyboardInterrupt):
            analyze_run(run_dir)
        saved = json.loads((run_dir / "analysis_results.json").read_bytes())
        attempts = saved["documents"][0]["cycles"][0]["attempts"]
        assert len(attempts) == number
        assert attempts[-1]["state"] == "reserved"
    analyze_run(run_dir)
    saved = json.loads((run_dir / "analysis_results.json").read_bytes())
    cycle = saved["documents"][0]["cycles"][0]
    assert cycle["analysis_error"]["code"] == "interrupted_attempts_exhausted"
    assert all(a["state"] == "interrupted" and a["finished_at"] is None for a in cycle["attempts"])
    assert len(provider.requests) == 4
    assert clock.sleeps == [5, 15]


def test_explicit_retry_grants_one_cycle_and_analyze_continues_it(run_dir, provider, clock):

    provider.responses.extend([sdk_error(503)] * 3)
    path = analyze_run(run_dir)
    first = json.loads(path.read_bytes())
    assert analyze_run(run_dir) == path
    assert len(provider.requests) == 4
    provider.responses.append(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        retry_failed(run_dir)
    saved = json.loads(path.read_bytes())
    assert saved["documents"][0]["cycles"][0] == first["documents"][0]["cycles"][0]
    assert saved["documents"][0]["cycles"][1]["attempts"][0]["state"] == "reserved"
    analyze_run(run_dir)
    saved = json.loads(path.read_bytes())
    assert len(saved["documents"][0]["cycles"]) == 2
    assert saved["documents"][0]["cycles"][1]["outcome"] == "ready"
    assert saved["documents"][0]["cycles"][1]["attempts"][0]["state"] == "interrupted"
    assert len(provider.requests) == 6


def test_retry_failure_receives_no_second_cycle_in_invocation(run_dir, provider, clock):

    provider.responses.extend([sdk_error(503)] * 3)
    path = analyze_run(run_dir)
    provider.responses.extend([sdk_error(503)] * 3)
    retry_failed(run_dir)
    saved = json.loads(path.read_bytes())
    assert len(saved["documents"][0]["cycles"]) == 2
    assert len(provider.requests) == 7
    assert saved["documents"][0]["cycles"][-1]["analysis_error"]["attempts"] == 3


def test_retry_does_not_seek_new_quarantine_or_invalid_output_verdict(
    run_dir, provider, prediction, clock
):

    prediction.update(injection_suspected=True, injection_evidence="FastAPI")
    provider.responses.append(response_with(prediction))
    provider.responses.append(
        dict(status="completed", model=None, error=None, incomplete_details=None, output=[])
    )
    path = analyze_run(run_dir)
    before = path.read_bytes()
    retry_failed(run_dir)
    assert path.read_bytes() == before
    assert len(provider.requests) == 2


@pytest.mark.parametrize(
    "change",
    [
        "unknown_error",
        "unsafe_message",
        "unfinished_terminal",
        "exhausted_unfinished",
        "retry_after_success",
        "retry_after_ineligible",
        "two_reservations",
        "changed_evidence",
        "bad_timestamp",
    ],
)
def test_checkpoint_history_consistency(run_dir, provider, clock, change):

    provider.responses.extend([sdk_error(503)] * 3)
    path = analyze_run(run_dir)
    saved = json.loads(path.read_bytes())
    cycle = saved["documents"][0]["cycles"][0]
    if change in ("unknown_error", "unsafe_message"):
        key, value = (
            ("code", "unknown") if change == "unknown_error" else ("message", "secret-token")
        )
        cycle["analysis_error"][key] = value
        cycle["attempts"][-1]["error"][key] = value
    elif change in ("unfinished_terminal", "exhausted_unfinished"):
        cycle.update(outcome=None, analysis_error=None, finished_at=None)
        if change == "unfinished_terminal":
            cycle["attempts"] = cycle["attempts"][:1]
            cycle["attempts"][0]["error"] = {
                "code": "invalid_json",
                "message": "The analysis output was not valid JSON.",
            }
    elif change == "retry_after_success":
        doc = saved["documents"][1]
        second = copy.deepcopy(doc["cycles"][0])
        second["cycle"] = 2
        doc["cycles"].append(second)
    elif change == "retry_after_ineligible":
        cycle["analysis_error"].update(
            code="invalid_json", message="The analysis output was not valid JSON."
        )
        cycle["attempts"][-1]["error"] = {
            k: cycle["analysis_error"][k] for k in ("code", "message")
        }
        second = copy.deepcopy(cycle)
        second["cycle"] = 2
        saved["documents"][0]["cycles"].append(second)
    elif change == "two_reservations":
        for doc in saved["documents"]:
            cyc = doc["cycles"][-1]
            cyc.update(outcome=None, model_analysis=None, analysis_error=None, finished_at=None)
            cyc["attempts"][-1].update(
                state="reserved", finished_at=None, response_model=None, error=None
            )
    elif change == "changed_evidence":
        saved["documents"][1]["cycles"][0]["model_analysis"]["injection_evidence"] = "fabricated"
    else:
        cycle["finished_at"] = "not-time"
    path.write_text(json.dumps(saved))
    before = path.read_bytes()
    with pytest.raises(ValueError):
        analyze_run(run_dir)
    assert path.read_bytes() == before
    assert len(provider.requests) == 4


@pytest.mark.parametrize("full_count,nonmonotonic", [(30000, False), (30001, False), (30001, True)])
def test_full_prefix_counting_and_unicode_provenance(
    manifest, tmp_path, provider, full_count, nonmonotonic
):
    original = "é🙂\r\n<|endoftext|> FastAPI " * 4
    path = manifest.parent / "docs/a.md"
    path.write_bytes(original.encode())
    values = json.loads(manifest.read_bytes())
    values["files"][0]["bytes"] = len(original.encode())
    manifest.write_text(json.dumps(values))
    run = prepare_run(
        manifest,
        recipe=build_analysis_recipe(),
        output_root=tmp_path / "runs",
        selected_source_paths=("guide/a.md",),
    )

    def count(serialized):
        request = json.loads(serialized)
        context = json.loads(request["input"][0]["content"][0]["text"])
        if context["selection"] == "full":
            return full_count
        selected = context["selected_characters"]
        return 30000 if selected <= 20 or (nonmonotonic and selected == 40) else 30001

    provider.count = count
    checkpoint = json.loads(analyze_run(run).read_bytes())
    request = provider.requests[0]
    context = json.loads(request["input"][0]["content"][0]["text"])
    supplied = request["input"][0]["content"][1]["text"]
    provenance = checkpoint["documents"][0]["supplied_document"]
    disclosure = "\n\n[Pipeline disclosure: only a prefix of the original document is supplied. The remainder was omitted to fit the saved input budget. Assess only the supplied excerpt.]\n"
    assert len(disclosure) == 168
    if full_count == 30000:
        assert supplied == original
        assert context["selection"] == "full"
        assert not provenance["truncated"]
    else:
        assert supplied == original[: context["selected_characters"]] + disclosure
        assert provenance["submitted_characters"] == context["selected_characters"] + 168
        assert context["selected_characters"] < len(original)
        assert provenance["truncated"]
    assert provenance["text_sha256"] == hashlib.sha256(supplied.encode()).hexdigest()
    assert provenance["original_characters"] == len(original)
    assert count(json.dumps(request)) <= 30000
    assert path.read_bytes() == original.encode()
    assert len(provider.counted) < 40
    assert (
        provider.counted.count(
            json.dumps(
                request, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
            )
        )
        >= 2
    )


def test_final_whole_request_recount_is_required(run_dir, provider):
    calls = 0

    def count(text):
        nonlocal calls
        calls += 1
        return 30000 if calls == 1 else 30001

    provider.count = count
    with pytest.raises(ValueError, match="final request cannot fit"):
        analyze_run(run_dir)
    assert provider.requests == []
    provider.constructor.assert_not_called()


def test_disclosure_is_not_source_injection_evidence(run_dir, provider, prediction):
    prediction.update(injection_suspected=True, injection_evidence="Pipeline disclosure")
    provider.responses.append(response_with(prediction))

    def count(text):
        context = json.loads(json.loads(text)["input"][0]["content"][0]["text"])
        return 30000 if context["selection"] == "prefix" else 30001

    provider.count = count
    saved = json.loads(analyze_run(run_dir).read_bytes())
    assert saved["documents"][0]["cycles"][0]["analysis_error"]["code"] == "invalid_analysis"


@pytest.mark.parametrize(
    "failure,save_number",
    [
        ("serialize", 1),
        ("serialize", 2),
        ("write", 1),
        ("write", 2),
        ("close", 1),
        ("close", 2),
        ("replace", 1),
        ("replace", 2),
    ],
)
def test_checkpoint_failures_stop_requests_and_preserve_previous(
    run_dir, provider, monkeypatch, failure, save_number
):

    calls = 0
    original_dumps = json.dumps
    original_replace = analysis.os.replace
    original_temp = analysis.tempfile.NamedTemporaryFile

    def fail_now():
        nonlocal calls
        calls += 1
        return calls == save_number

    if failure == "serialize":

        def dumps(value, *args, **kwargs):
            if (
                isinstance(value, dict)
                and "documents" in value
                and "input_hashes" in value
                and fail_now()
            ):
                raise ValueError("serialization failed")
            return original_dumps(value, *args, **kwargs)

        monkeypatch.setattr(json, "dumps", dumps)
    elif failure == "replace":

        def replace(source, destination):
            if fail_now():
                raise OSError("replacement failed")
            original_replace(source, destination)

        monkeypatch.setattr(analysis.os, "replace", replace)
    else:

        @contextmanager
        def temporary(*args, **kwargs):
            fail = fail_now()
            with original_temp(*args, **kwargs) as stream:

                def write(content):
                    if fail and failure == "write":
                        stream.write(content[:10])
                        raise OSError("write failed")
                    return stream.write(content)

                yield SimpleNamespace(name=stream.name, write=write)
            if fail and failure == "close":
                raise OSError("close failed")

        monkeypatch.setattr(analysis.tempfile, "NamedTemporaryFile", temporary)
    with pytest.raises((ValueError, OSError)):
        analyze_run(run_dir)
    assert len(provider.requests) == save_number - 1
    path = run_dir / "analysis_results.json"
    if save_number == 1:
        assert not path.exists()
    else:
        saved = json.loads(path.read_bytes())
        assert saved["documents"][0]["cycles"][0]["attempts"][0]["state"] == "reserved"
        assert saved["documents"][1]["cycles"] == []
    if failure != "serialize":
        assert list(run_dir.glob(".analysis-*"))
    provider.client.close.assert_called_once()


def test_invalid_proposed_result_checkpoint_stops_before_replacement(run_dir, provider, clock):
    provider.before_call = lambda: setattr(clock, "now", clock.now - 10)
    with pytest.raises(ValueError, match="finishes before"):
        analyze_run(run_dir)
    saved = json.loads((run_dir / "analysis_results.json").read_bytes())
    assert saved["documents"][0]["cycles"][0]["attempts"][0]["state"] == "reserved"
    assert len(provider.requests) == 1


def test_checkpoint_temporary_is_not_read_back(run_dir, provider, monkeypatch):

    read = Path.read_bytes

    def read_bytes(path):
        assert not path.name.startswith(".analysis-"), "checkpoint readback is not required"
        return read(path)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    analyze_run(run_dir)
    assert len(provider.requests) == 2


def test_builder_has_no_encoding_or_client_side_effect_and_returns_independent_recipe(
    monkeypatch, provider
):
    monkeypatch.setattr(
        tiktoken, "get_encoding", lambda name: pytest.fail("encoding loaded by recipe builder")
    )
    first = build_analysis_recipe()
    first.response_schema["properties"]["topics"]["description"] = "mutated by caller"
    second = build_analysis_recipe()
    schema = json.dumps(
        second.response_schema,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )
    assert (
        hashlib.sha256(schema.encode()).hexdigest()
        == "622efb6094639d9dc26dec7393bded4f6a5b09344bc9c4e00399bf42f91a2f57"
    )
    provider.constructor.assert_not_called()


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("timeout", "provider_timeout"),
        ("connection", "provider_connection_error"),
        ("envelope", "unexpected_provider_response"),
    ],
)
def test_sdk_exception_types_and_envelope_failure(run_dir, provider, clock, kind, expected):
    request = httpx.Request("POST", "https://example.invalid")
    if kind == "timeout":
        error = openai.APITimeoutError(request)
    elif kind == "connection":
        error = openai.APIConnectionError(request=request)
    else:
        error = openai.APIResponseValidationError(
            httpx.Response(200, request=request), {"secret": "no persist"}
        )
    provider.responses.extend([error] * (1 if kind == "envelope" else 3))
    if kind == "envelope":
        with pytest.raises(RuntimeError, match=expected):
            analyze_run(run_dir)
    else:
        analyze_run(run_dir)
    saved = json.loads((run_dir / "analysis_results.json").read_bytes())
    assert saved["documents"][0]["cycles"][0]["analysis_error"]["code"] == expected
    assert "no persist" not in json.dumps(saved)


@pytest.mark.parametrize(
    "code,expected",
    [("server_error", "provider_server_error"), ("rate_limit_exceeded", "provider_rate_limited")],
)
def test_failed_response_objects_retry_with_returned_identity(
    run_dir, provider, clock, code, expected
):
    provider.responses.extend(
        [
            dict(
                status="failed",
                model="returned-failure-model",
                error={"code": code, "message": "secret"},
                output=[],
            )
        ]
        * 3
    )
    saved = json.loads(analyze_run(run_dir).read_bytes())
    cycle = saved["documents"][0]["cycles"][0]
    assert cycle["analysis_error"]["code"] == expected
    assert cycle["attempts"][0]["response_model"] == "returned-failure-model"
    assert len(provider.requests) == 4
    assert clock.sleeps == [5, 15]


def test_exhausted_document_cooldown_applies_to_next_document(run_dir, provider, clock):
    provider.responses.extend(
        [sdk_error(503), sdk_error(503), sdk_error(503, headers={"Retry-After": "30"})]
    )
    analyze_run(run_dir)
    assert clock.sleeps == [5, 15, 30]
    assert len(provider.requests) == 4


def test_missing_local_credentials_never_reserve_attempt(run_dir, provider):
    provider.constructor.side_effect = openai.OpenAIError("secret local configuration")
    with pytest.raises(ValueError, match="client setup") as caught:
        analyze_run(run_dir)
    assert "secret" not in str(caught.value)
    assert not (run_dir / "analysis_results.json").exists()
    assert provider.requests == []


def test_source_changed_during_invocation_stops_before_next_request(run_dir, provider, manifest):
    provider.before_call = lambda: (manifest.parent / "docs/b.md").write_bytes(b"changed")
    with pytest.raises(ValueError, match="source hash changed"):
        analyze_run(run_dir)
    saved = json.loads((run_dir / "analysis_results.json").read_bytes())
    assert saved["documents"][0]["cycles"][0]["outcome"] == "ready"
    assert saved["documents"][1]["cycles"] == []
    assert len(provider.requests) == 1


def test_retry_waits_without_adding_cycle_and_leaves_never_started_documents(
    run_dir, provider, clock
):
    provider.responses.extend(
        [sdk_error(503), sdk_error(503), sdk_error(503, headers={"Retry-After": "120"})]
    )
    with pytest.raises(RuntimeError, match="cooldown"):
        analyze_run(run_dir)
    path = run_dir / "analysis_results.json"
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="cooldown"):
        retry_failed(run_dir)
    assert path.read_bytes() == before
    assert len(provider.requests) == 3
    clock.now += 110
    retry_failed(run_dir)
    saved = json.loads(path.read_bytes())
    assert len(saved["documents"][0]["cycles"]) == 2
    assert saved["documents"][0]["cycles"][-1]["outcome"] == "ready"
    assert saved["documents"][1]["cycles"] == []
    assert clock.sleeps == [5, 15, 10]


def test_explicit_retry_after_access_correction(run_dir, provider, clock):
    provider.responses.append(sdk_error(401))
    with pytest.raises(RuntimeError, match="authentication_error"):
        analyze_run(run_dir)
    path = retry_failed(run_dir)
    saved = json.loads(path.read_bytes())
    assert [cycle["outcome"] for cycle in saved["documents"][0]["cycles"]] == [
        "analysis_failed",
        "ready",
    ]
    assert saved["documents"][1]["cycles"] == []
    assert len(provider.requests) == 2


def test_http_date_cooldown(run_dir, provider, clock):

    header = format_datetime(datetime.fromtimestamp(clock.now + 12, UTC), usegmt=True)
    provider.responses.append(sdk_error(429, "rate_limit_exceeded", {"Retry-After": header}))
    analyze_run(run_dir)
    assert clock.sleeps == [12]


@pytest.mark.parametrize("version", ["0.12.0", None])
def test_recipe_requires_supported_installed_version(monkeypatch, version):
    def installed(name):
        if version is None:
            raise metadata.PackageNotFoundError(name)
        return version

    monkeypatch.setattr(metadata, "version", installed)
    with pytest.raises(ValueError, match="tiktoken"):
        build_analysis_recipe()


def test_malformed_incomplete_reason_is_a_recorded_provider_stop(run_dir, provider):
    provider.responses.append(
        dict(status="incomplete", model=None, incomplete_details={"reason": []}, output=[])
    )
    with pytest.raises(RuntimeError, match="unexpected_provider_response"):
        analyze_run(run_dir)
    saved = json.loads((run_dir / "analysis_results.json").read_bytes())
    assert (
        saved["documents"][0]["cycles"][0]["analysis_error"]["code"]
        == "unexpected_provider_response"
    )
    assert len(provider.requests) == 1


def test_unknown_local_exception_preserves_reservation_and_stops(run_dir, provider):
    provider.responses.append(RuntimeError("unexpected SDK code failure"))
    with pytest.raises(RuntimeError, match="unexpected SDK code failure"):
        analyze_run(run_dir)
    saved = json.loads((run_dir / "analysis_results.json").read_bytes())
    cycle = saved["documents"][0]["cycles"][0]
    assert cycle["outcome"] is None
    assert cycle["attempts"][0]["state"] == "reserved"
    assert cycle["attempts"][0]["error"] is None
    assert saved["documents"][1]["cycles"] == []
    provider.client.close.assert_called_once()


@pytest.mark.parametrize(
    "content_type,body",
    [
        ("text/plain", "secret-token upstream failure"),
        ("text/html", "<html>secret-token upstream failure</html>"),
        ("application/json", '"secret-token scalar"'),
        ("application/json", '["secret-token list"]'),
        ("application/json", "null"),
        ("application/json", "42"),
        ("application/json", '{"secret-token":'),
        ("application/json", b"\xff"),
    ],
)
@pytest.mark.parametrize("selected", [("guide/a.md",), None])
def test_real_sdk_unexpected_envelope_is_final(
    manifest, tmp_path, monkeypatch, content_type, body, selected
):
    run_dir = prepare_run(
        manifest,
        recipe=build_analysis_recipe(),
        output_root=tmp_path / "runs",
        selected_source_paths=selected,
    )
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            200,
            content=body,
            headers={"content-type": content_type, "retry-after-ms": "120000"},
        )

    http_client = httpx.Client(transport=httpx.MockTransport(respond))
    client = openai.OpenAI(api_key="offline-placeholder", http_client=http_client, max_retries=0)
    constructor = Mock(return_value=client)
    monkeypatch.setattr(openai, "OpenAI", constructor)
    monkeypatch.setattr(
        tiktoken,
        "get_encoding",
        lambda name: SimpleNamespace(encode_ordinary=lambda text: range(1000)),
    )
    with pytest.raises(RuntimeError, match="unexpected_provider_response") as error:
        analyze_run(run_dir)
    assert "secret-token" not in str(error.value)
    assert len(requests) == 1
    assert http_client.is_closed
    path = run_dir / "analysis_results.json"
    saved_bytes = path.read_bytes()
    assert b"secret-token" not in saved_bytes
    saved = json.loads(saved_bytes)
    cycle = saved["documents"][0]["cycles"][0]
    assert cycle["outcome"] == "analysis_failed"
    assert cycle["analysis_error"]["code"] == "unexpected_provider_response"
    assert cycle["analysis_error"]["attempts"] == 1
    assert cycle["model_analysis"] is None
    assert cycle["attempts"][0]["state"] == "failed"
    assert cycle["attempts"][0]["response_model"] is None
    assert saved["retry_not_before"] is not None
    if selected is None:
        assert saved["documents"][1]["cycles"] == []
        assert saved["documents"][1]["supplied_document"] is None
    else:
        assert analyze_run(run_dir) == path
        assert analyze_run(run_dir) == path
    assert retry_failed(run_dir) == path
    assert path.read_bytes() == saved_bytes
    assert len(requests) == 1
    assert constructor.call_count == 1
