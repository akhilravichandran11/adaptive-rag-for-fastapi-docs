"""
Test ``crag/run.py``'s command line at its seams: ``parse_args``, the ``main`` paths that exit
before any client is built, and a ``main`` run end to end on each retriever against recording
fakes. The real ``.env`` is not loaded. No network (C006, the no-network form).
"""

import json
import logging
import socket
from datetime import UTC, datetime
from pathlib import Path

import pytest
from haystack import Document

from crag import run
from crag.orchestrator import CragResult

REQUIRED = (
    "GOOGLE_API_KEY",
    "TAVILY_API_KEY",
    "VOYAGE_API_KEY",
    "GEMINI_MODEL",
    "GEMINI_LIGHT_MODEL",
)
OPTIONAL = ("GEMINI_FALLBACK_MODEL", "GEMINI_LIGHT_FALLBACK_MODEL")


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every outgoing connection raise, so no test can reach an API."""

    def refuse(*args: object, **kwargs: object):
        raise AssertionError("a test tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def fail(name: str, built: list[str]) -> None:
    built.append(name)
    raise AssertionError(f"main built {name} before exiting")


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """``main`` configures the root logger with ``force=True``, which would remove pytest's log
    capture, and quiets ``httpx`` for the rest of the session."""
    monkeypatch.setattr(run, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr(logging, "basicConfig", lambda *args, **kwargs: None)
    monkeypatch.setattr(logging.getLogger("httpx"), "setLevel", lambda level: None)
    for name in REQUIRED:
        monkeypatch.setenv(name, f"test-{name.lower()}")
    for name in OPTIONAL:
        monkeypatch.delenv(name, raising=False)

    built: list[str] = []
    monkeypatch.setattr(run, "build_rag_pipeline", lambda *a, **k: fail("pipeline", built))
    monkeypatch.setattr(
        run.hybrid, "build_hybrid_pipeline", lambda *a, **k: fail("hybrid pipeline", built)
    )
    monkeypatch.setattr(run.genai, "Client", lambda *a, **k: fail("genai.Client", built))
    monkeypatch.setattr(run, "TavilyClient", lambda *a, **k: fail("TavilyClient", built))
    monkeypatch.setattr(run, "StructuredLLM", lambda *a, **k: fail("StructuredLLM", built))
    return built


def test_parse_args_takes_a_question():
    args = run.parse_args(["--question", "How do I declare a path parameter?"])

    assert args.question == "How do I declare a path parameter?"
    assert args.route_check is False
    assert args.retriever == "hybrid"


def test_parse_args_takes_the_route_check():
    args = run.parse_args(["--route-check"])

    assert args.route_check is True
    assert args.question is None


def test_parse_args_takes_the_dense_retriever():
    args = run.parse_args(["--route-check", "--retriever", "dense"])

    assert args.retriever == "dense"


def test_parse_args_exits_2_for_an_unknown_retriever():
    with pytest.raises(SystemExit) as exit_info:
        run.parse_args(["--question", "q", "--retriever", "bm25"])

    assert exit_info.value.code == 2


@pytest.mark.parametrize("question", ["", "   "], ids=["empty", "blank"])
def test_parse_args_exits_2_for_an_empty_question(question: str):
    with pytest.raises(SystemExit) as exit_info:
        run.parse_args(["--question", question])

    assert exit_info.value.code == 2


@pytest.mark.parametrize(
    "argv",
    [[], ["--question", "q", "--route-check"], ["--top-k", "3", "--question", "q"]],
    ids=["neither", "both", "unknown-flag"],
)
def test_parse_args_exits_2_unless_given_exactly_one_mode(argv: list[str]):
    with pytest.raises(SystemExit) as exit_info:
        run.parse_args(argv)

    assert exit_info.value.code == 2


@pytest.mark.parametrize(
    "missing",
    [(name,) for name in REQUIRED] + [REQUIRED, ("TAVILY_API_KEY", "GEMINI_LIGHT_MODEL")],
    ids=[*REQUIRED, "all", "two"],
)
def test_main_names_every_missing_setting_and_exits_1_before_building_anything(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    isolated: list[str],
    missing: tuple[str, ...],
):
    for name in missing:
        monkeypatch.delenv(name)

    with (
        caplog.at_level(logging.ERROR, logger=run.__name__),
        pytest.raises(SystemExit) as exit_info,
    ):
        run.main(["--question", "q"])

    assert exit_info.value.code == 1
    assert isolated == []
    [record] = caplog.records
    for name in REQUIRED:
        assert (name in record.getMessage()) == (name in missing)


def test_main_counts_an_empty_setting_as_missing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, isolated: list[str]
):
    monkeypatch.setenv("GEMINI_MODEL", "")

    with (
        caplog.at_level(logging.ERROR, logger=run.__name__),
        pytest.raises(SystemExit) as exit_info,
    ):
        run.main(["--route-check"])

    assert exit_info.value.code == 1
    assert isolated == []
    assert "GEMINI_MODEL" in caplog.text


def test_main_exits_1_for_a_missing_collection_before_any_client_is_built(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, isolated: list[str]
):
    def missing_collection(variant, **kwargs):
        assert variant.collection("x") == "x__markdown_ast__voyage"
        assert kwargs == {"retrieve_only": True}
        raise ValueError("Collection fastapi_docs__markdown_ast__voyage does not exist")

    monkeypatch.setattr(run, "build_rag_pipeline", missing_collection)

    with (
        caplog.at_level(logging.ERROR, logger=run.__name__),
        pytest.raises(SystemExit) as exit_info,
    ):
        run.main(["--question", "q", "--retriever", "dense"])

    assert exit_info.value.code == 1
    assert isolated == []
    assert "does not exist" in caplog.text


def test_main_exits_1_for_a_missing_chunk_run_on_the_default_hybrid_retriever(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, isolated: list[str]
):
    def missing_chunks(variant, **kwargs):
        assert variant.collection("x") == "x__markdown_ast__voyage"
        assert kwargs == {}
        raise ValueError("Chunk run full_chunks_20261015 has no markdown_ast chunks")

    monkeypatch.setattr(run.hybrid, "build_hybrid_pipeline", missing_chunks)

    with (
        caplog.at_level(logging.ERROR, logger=run.__name__),
        pytest.raises(SystemExit) as exit_info,
    ):
        run.main(["--question", "q"])

    assert exit_info.value.code == 1
    assert isolated == []
    assert "full_chunks_20261015" in caplog.text


def test_route_check_holds_eight_questions_with_the_route_mix():
    assert len(run.ROUTE_CHECK) == 8
    assert [expected for _, expected, _ in run.ROUTE_CHECK] == [
        "correct",
        "correct",
        "correct",
        "correct",
        "ambiguous",
        "ambiguous",
        "incorrect",
        "incorrect",
    ]


class FakePipeline:
    """Stands in for ``build_rag_pipeline``'s retrieve-only pipeline: records how it was built
    and each ``run`` input, and returns the retriever's documents as Haystack does."""

    built: list = []

    def __init__(self, variant, **kwargs) -> None:
        self.variant = variant
        self.kwargs = kwargs
        self.inputs: list[dict] = []
        FakePipeline.built.append(self)

    def run(self, data: dict) -> dict:
        self.inputs.append(data)
        return {"retriever": {"documents": [Document(content="c", meta={"source_path": "a.md"})]}}


class FakeHybridPipeline:
    """Stands in for ``build_hybrid_pipeline``'s pipeline: records how it was built and each
    ``run`` call, and returns the ranker's documents as Haystack does, so the real
    ``hybrid.retrieve`` runs against it."""

    built: list = []

    def __init__(self, variant, **kwargs) -> None:
        self.variant = variant
        self.kwargs = kwargs
        self.calls: list[tuple[dict, set]] = []
        FakeHybridPipeline.built.append(self)

    def run(self, data: dict, include_outputs_from: set) -> dict:
        self.calls.append((data, include_outputs_from))
        return {"ranker": {"documents": [Document(content="c", meta={"source_path": "a.md"})]}}


class FakeClient:
    def __init__(self, *, api_key: str) -> None:
        self.api_key = api_key


class FakeStructuredLLM:
    def __init__(self, client, *, primary: str, fallback: str, record=None) -> None:
        self.client = client
        self.primary = primary
        self.fallback = fallback
        self.record = record


class FakeOrchestrator:
    """Records the adapters ``main`` wires in, and runs ``retrieve`` once when asked a question."""

    built: list = []

    def __init__(self, retrieve, grader, decomposer, web_search, refinement, generator) -> None:
        self.retrieve = retrieve
        self.grader = grader
        self.decomposer = decomposer
        self.web_search = web_search
        self.refinement = refinement
        self.generator = generator
        self.questions: list[str] = []
        FakeOrchestrator.built.append(self)

    def run(self, question: str) -> CragResult:
        self.questions.append(question)
        documents = self.retrieve(question, 7)
        return CragResult(question, "correct", "answer [1]", [], [], [{"documents": documents}])


@pytest.mark.parametrize(
    ("fallbacks", "pro_fallback", "light_fallback"),
    [
        ({}, run.FALLBACK_MODEL, run.FALLBACK_MODEL),
        (
            {"GEMINI_FALLBACK_MODEL": "pro-fb", "GEMINI_LIGHT_FALLBACK_MODEL": "light-fb"},
            "pro-fb",
            "light-fb",
        ),
    ],
    ids=["defaults", "set"],
)
def test_main_on_the_dense_path_shares_the_pro_llm_and_reads_fallbacks(
    monkeypatch: pytest.MonkeyPatch,
    fallbacks: dict[str, str],
    pro_fallback: str,
    light_fallback: str,
):
    for name, value in fallbacks.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(FakePipeline, "built", [])
    monkeypatch.setattr(FakeOrchestrator, "built", [])
    monkeypatch.setattr(run, "build_rag_pipeline", FakePipeline)
    monkeypatch.setattr(run.genai, "Client", FakeClient)
    monkeypatch.setattr(run, "TavilyClient", FakeClient)
    monkeypatch.setattr(run, "StructuredLLM", FakeStructuredLLM)
    monkeypatch.setattr(run, "CragOrchestrator", FakeOrchestrator)

    run.main(["--question", "How do I declare a path parameter?", "--retriever", "dense"])

    [pipeline] = FakePipeline.built
    [crag] = FakeOrchestrator.built
    assert pipeline.variant.collection("x") == "x__markdown_ast__voyage"
    assert pipeline.kwargs == {"retrieve_only": True}
    assert pipeline.inputs == [
        {
            "query_embedder": {"text": "How do I declare a path parameter?"},
            "retriever": {"top_k": 7},
        }
    ]
    assert crag.questions == ["How do I declare a path parameter?"]

    pro = crag.grader.llm
    assert crag.decomposer.llm is pro and crag.generator.llm is pro
    assert (pro.primary, pro.fallback) == ("test-gemini_model", pro_fallback)
    light = crag.refinement.llm
    assert light is not pro
    assert (light.primary, light.fallback) == ("test-gemini_light_model", light_fallback)
    assert pro.client is light.client
    assert pro.client.api_key == "test-google_api_key"
    assert crag.web_search.web_search.client.api_key == "test-tavily_api_key"


def test_main_answers_through_the_hybrid_pipeline_by_default(
    monkeypatch: pytest.MonkeyPatch, isolated: list[str]
):
    monkeypatch.setattr(FakeHybridPipeline, "built", [])
    monkeypatch.setattr(FakeOrchestrator, "built", [])
    monkeypatch.setattr(run.hybrid, "build_hybrid_pipeline", FakeHybridPipeline)
    monkeypatch.setattr(run.genai, "Client", FakeClient)
    monkeypatch.setattr(run, "TavilyClient", FakeClient)
    monkeypatch.setattr(run, "StructuredLLM", FakeStructuredLLM)
    monkeypatch.setattr(run, "CragOrchestrator", FakeOrchestrator)

    run.main(["--question", "How do I declare a path parameter?"])

    assert isolated == []  # the dense builder was not called
    [pipeline] = FakeHybridPipeline.built
    [crag] = FakeOrchestrator.built
    assert pipeline.variant.collection("x") == "x__markdown_ast__voyage"
    assert pipeline.kwargs == {}
    query = "How do I declare a path parameter?"
    assert pipeline.calls == [
        (
            {
                "bm25": {"query": query},
                "query_embedder": {"text": query},
                "ranker": {"query": query, "top_k": 7},
            },
            set(),
        )
    ]
    assert crag.questions == [query]


def test_recorded_cli_opens_cost_file_before_building_and_shares_one_recorder(
    monkeypatch, tmp_path
):
    directory = tmp_path / "costs"
    monkeypatch.setattr(FakeHybridPipeline, "built", [])
    monkeypatch.setattr(FakeOrchestrator, "built", [])

    def build(variant, **kwargs):
        assert (directory / "calls.jsonl").exists()
        return FakeHybridPipeline(variant, **kwargs)

    monkeypatch.setattr(run.hybrid, "build_hybrid_pipeline", build)
    monkeypatch.setattr(run.genai, "Client", FakeClient)
    monkeypatch.setattr(run, "TavilyClient", FakeClient)
    monkeypatch.setattr(run, "StructuredLLM", FakeStructuredLLM)
    monkeypatch.setattr(run, "CragOrchestrator", FakeOrchestrator)
    run.main(["--question", "q", "--costs-dir", str(directory)])

    [pipeline] = FakeHybridPipeline.built
    [crag] = FakeOrchestrator.built
    record = pipeline.kwargs["record"]
    assert (
        crag.grader.llm.record
        is crag.refinement.llm.record
        is crag.web_search.web_search.record
        is record
    )
    assert json.loads((directory / "summary.json").read_text())["run_failed"] is False


@pytest.mark.parametrize("mode", [["--route-check"], ["--question", "q", "--retriever", "dense"]])
def test_recorded_unsupported_modes_exit_before_dispatch(mode, tmp_path, isolated, capsys):
    directory = tmp_path / "costs"
    with pytest.raises(SystemExit) as raised:
        run.main([*mode, "--costs-dir", str(directory)])
    assert raised.value.code == 2
    assert "--costs-dir requires --question and --retriever hybrid" in capsys.readouterr().err
    assert isolated == []
    assert not directory.exists()


@pytest.mark.parametrize("failure", ["existing-directory", "open-file", "build", "finish"])
def test_cost_file_failures_and_build_failure_finalize_without_dispatch(
    failure, monkeypatch, tmp_path, isolated
):
    directory = tmp_path / "costs"
    if failure == "existing-directory":
        directory.mkdir()
    elif failure == "open-file":
        original_open = Path.open

        def fail_open(path, *args, **kwargs):
            if path == directory / "calls.jsonl":
                raise OSError("cannot open costs")
            return original_open(path, *args, **kwargs)

        monkeypatch.setattr(Path, "open", fail_open)
    else:

        def fail_build(*args, **kwargs):
            raise ValueError("missing test collection")

        monkeypatch.setattr(run.hybrid, "build_hybrid_pipeline", fail_build)
        if failure == "finish":
            original_write = Path.write_text

            def fail_finish(path, *args, **kwargs):
                if path == directory / "summary.json":
                    raise OSError("cannot write summary")
                return original_write(path, *args, **kwargs)

            monkeypatch.setattr(Path, "write_text", fail_finish)

    before_run = datetime.now(UTC)
    with pytest.raises(SystemExit if failure == "build" else OSError):
        run.main(["--question", "q", "--costs-dir", str(directory)])
    assert isolated == []
    if failure == "build":
        summary = json.loads((directory / "summary.json").read_text())
        assert summary["run_failed"] is True
        assert summary["terminal_run_failed"] is True
        assert summary["call_count"] == 0
        assert before_run <= datetime.fromisoformat(summary["started_at"])
        assert (
            datetime.fromisoformat(summary["started_at"])
            <= datetime.fromisoformat(summary["ended_at"])
            <= datetime.now(UTC)
        )
