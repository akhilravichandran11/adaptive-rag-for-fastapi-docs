"""
Test ``backend/app/main.py`` at its seams: ``GET /health``, ``POST /query`` through a fake
orchestrator put in with ``app.dependency_overrides[get_crag]``, and the startup ``lifespan``
against recording fakes. The real ``.env`` is not loaded. No network (C006, the no-network form).
"""

import asyncio
import importlib
import os
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

import dotenv
import pytest
from fastapi.testclient import TestClient

from backend.app import main
from crag.orchestrator import CragResult

PROJECT_ROOT = Path(__file__).resolve().parents[2]

REQUIRED = (
    "GOOGLE_API_KEY",
    "TAVILY_API_KEY",
    "GEMINI_MODEL",
    "GEMINI_LIGHT_MODEL",
    "VOYAGE_API_KEY",
)
OPTIONAL = ("GEMINI_FALLBACK_MODEL", "GEMINI_LIGHT_FALLBACK_MODEL")
# Top-level packages whose module code the import test re-runs.
PROJECT_PACKAGES = ("backend", "crag", "retrieval", "data_ingestion")

RESULT = CragResult(
    question="How do I declare a query parameter?",
    route="correct",
    answer="Declare it as a function parameter that is not part of the path.",
    sources=[
        {
            "rank": 1,
            "origin": "internal",
            "source": "docs/en/docs/tutorial/query-params.md",
            "content": "Query parameters ...",
            "score": 0.91,
        },
        {
            "rank": 2,
            "origin": "web",
            "source": "https://example.com/query-params",
            "content": None,
            "score": None,
        },
    ],
    missing_aspects=["defaults"],
    steps=[
        {
            "step": "retrieve",
            "started": "2026-10-26T10:00:00+00:00",
            "ended": "2026-10-26T10:00:01+00:00",
            "counts": {"documents": 10},
        },
        {
            "step": "grade",
            "started": "2026-10-26T10:00:01+00:00",
            "ended": "2026-10-26T10:00:02+00:00",
            "counts": {"relevant": 3, "total": 10},
            "model": "gemini-2.5-pro",
        },
    ],
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every outgoing connection raise, so no test can reach an API."""

    def refuse(*args: object, **kwargs: object):
        raise AssertionError("a test tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


class FakeCrag:
    """Records each question; returns ``RESULT`` or raises ``error``, as ``CragOrchestrator.run``
    returns a result or lets an adapter's error through."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.questions: list[str] = []

    def run(self, question: str) -> CragResult:
        self.questions.append(question)
        if self.error is not None:
            raise self.error
        return RESULT


@pytest.fixture
def crag() -> FakeCrag:
    fake = FakeCrag()
    main.app.dependency_overrides[main.get_crag] = lambda: fake
    yield fake
    main.app.dependency_overrides.clear()


def test_health_returns_ok() -> None:
    response = TestClient(main.app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_query_returns_every_result_field_from_the_orchestrator(crag: FakeCrag) -> None:
    response = TestClient(main.app).post("/query", json={"question": "  Query params?  "})

    assert response.status_code == 200
    assert crag.questions == ["Query params?"]
    body = response.json()
    assert set(body) == {"question", "route", "answer", "sources", "missing_aspects", "steps"}
    assert body["route"] == "correct"
    assert body["answer"] == RESULT.answer
    assert body["sources"] == RESULT.sources
    assert body["missing_aspects"] == ["defaults"]
    assert body["steps"][0] == {**RESULT.steps[0], "model": None}
    assert body["steps"][1] == RESULT.steps[1]


@pytest.mark.parametrize(
    "payload",
    [{"question": ""}, {"question": "   \n\t "}, {}, {"question": "x" * 1001}],
    ids=["empty", "blank", "missing", "over-long"],
)
def test_query_rejects_a_bad_question_before_the_orchestrator(
    crag: FakeCrag, payload: dict[str, str]
) -> None:
    response = TestClient(main.app).post("/query", json=payload)

    assert response.status_code == 422
    assert crag.questions == []


def test_query_accepts_a_question_of_exactly_1000_characters(crag: FakeCrag) -> None:
    response = TestClient(main.app).post("/query", json={"question": "x" * 1000})

    assert response.status_code == 200
    assert crag.questions == ["x" * 1000]


def test_query_returns_500_when_the_orchestrator_raises(crag: FakeCrag) -> None:
    crag.error = ValueError("grade: unknown decision 'maybe'")

    response = TestClient(main.app, raise_server_exceptions=False).post(
        "/query", json={"question": "Query params?"}
    )

    assert response.status_code == 500
    assert crag.questions == ["Query params?"]


def test_get_crag_returns_the_orchestrator_on_app_state() -> None:
    class FakeRequest:
        app = main.app

    sentinel = object()
    main.app.state.crag = sentinel
    try:
        assert main.get_crag(FakeRequest()) is sentinel
    finally:
        del main.app.state.crag


# --- the page ------------------------------------------------------------------------------


def test_root_serves_the_page_with_the_question_box_and_its_script() -> None:
    response = TestClient(main.app).get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert '<textarea id="question"' in response.text
    assert 'maxlength="1000"' in response.text
    assert '<script src="app.js"></script>' in response.text


def test_app_js_is_served_as_javascript() -> None:
    response = TestClient(main.app).get("/app.js")

    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]
    assert response.text == (main.FRONTEND_DIR / "app.js").read_text(encoding="utf-8")


# --- startup -------------------------------------------------------------------------------


def run_lifespan(app: object) -> None:
    async def enter() -> None:
        async with main.lifespan(app):
            pass

    asyncio.run(enter())


class FakeApp:
    """Only the ``state`` the lifespan writes to; the real app's state is left alone."""

    def __init__(self) -> None:
        self.state = SimpleNamespace()


PIPELINE = SimpleNamespace(metadata={"collection": "fake__markdown_ast__voyage"})


@pytest.fixture
def startup(monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:
    """Stand in for ``.env`` and every client, recording what the lifespan builds."""
    calls: dict[str, list] = {"dotenv": [], "pipeline": [], "retrieve": [], "genai": []}
    monkeypatch.setattr(main, "load_dotenv", lambda *a, **k: calls["dotenv"].append(a))
    for name in REQUIRED:
        monkeypatch.setenv(name, f"test-{name.lower()}")
    for name in OPTIONAL:
        monkeypatch.delenv(name, raising=False)

    def build(variant, **kwargs):
        calls["pipeline"].append((variant.strategy, variant.embedder.name))
        return PIPELINE

    def retrieve(pipe, query, top_k=None):
        calls["retrieve"].append((pipe, query, top_k))
        return []

    monkeypatch.setattr(main.hybrid, "build_hybrid_pipeline", build)
    monkeypatch.setattr(main.hybrid, "retrieve", retrieve)
    monkeypatch.setattr(main.genai, "Client", lambda **k: calls["genai"].append(k) or "client")
    monkeypatch.setattr(main, "TavilyClient", lambda **k: ("tavily", k["api_key"]))
    return calls


@pytest.mark.parametrize("name", REQUIRED)
def test_startup_with_a_setting_missing_raises_naming_it(
    startup: dict[str, list], monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.delenv(name)

    with pytest.raises(RuntimeError, match=name):
        run_lifespan(FakeApp())

    assert startup["dotenv"] == [(PROJECT_ROOT / ".env",)]
    assert startup["pipeline"] == []
    assert startup["genai"] == []


def test_startup_builds_one_orchestrator_on_hybrid_retrieval(startup: dict[str, list]) -> None:
    app = FakeApp()

    run_lifespan(app)

    crag = app.state.crag
    assert startup["pipeline"] == [("markdown_ast", "voyage")]
    assert startup["genai"] == [{"api_key": "test-google_api_key"}]
    assert crag.grader.llm.primary == "test-gemini_model"
    assert crag.grader.llm.fallback == main.FALLBACK_MODEL
    assert crag.generator.llm is crag.grader.llm
    assert crag.refinement.llm.primary == "test-gemini_light_model"
    assert crag.web_search.client == ("tavily", "test-tavily_api_key")

    assert crag.retrieve("Query params?", 10) == []
    assert startup["retrieve"] == [(PIPELINE, "Query params?", 10)]


def test_startup_uses_the_fallback_models_when_set(
    startup: dict[str, list], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GEMINI_FALLBACK_MODEL", "pro-fallback")
    monkeypatch.setenv("GEMINI_LIGHT_FALLBACK_MODEL", "light-fallback")
    app = FakeApp()

    run_lifespan(app)

    assert app.state.crag.grader.llm.fallback == "pro-fallback"
    assert app.state.crag.refinement.llm.fallback == "light-fallback"


def test_importing_main_reads_no_setting_and_opens_no_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-run this project's module code with no settings, no network (the autouse guard) and
    ``load_dotenv`` recording. Third-party packages stay imported, so this takes well under a second;
    a fresh interpreter took 15-40 s under load and timed out in a busy full-suite run."""
    for name in [n for n in sys.modules if n.split(".")[0] in PROJECT_PACKAGES]:
        monkeypatch.delitem(sys.modules, name)  # restored after the test
    for name in list(os.environ):
        monkeypatch.delenv(name)
    loaded: list[tuple] = []
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: loaded.append(a))

    fresh = importlib.import_module("backend.app.main")

    assert fresh is not main
    assert loaded == []
    assert not hasattr(fresh.app.state, "crag")
