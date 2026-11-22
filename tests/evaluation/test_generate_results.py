"""
Tests at the agreed seams only, ``load_questions(paths)`` and ``main(argv)``. No test
reaches Qdrant, OpenRouter, Voyage or Gemini (C006), and the real ``.env`` is not loaded.
"""

import hashlib
import json
import logging
import os
import socket
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from google.genai import errors
from haystack.core.errors import PipelineRuntimeError

from data_ingestion import pipeline as ingestion_pipeline
from data_ingestion.chunkers import STRATEGIES
from data_ingestion.embedders import EMBEDDERS
from data_ingestion.variants import select_variants
from evaluation import generate_results
from evaluation.schemas import EvalQuestion

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# The module's own question files, read before the ``isolated`` fixture replaces them.
REAL_QUESTION_FILES = generate_results.QUESTION_FILES
RUN_ID = "20261114_120000"
ALL_SYSTEMS = [
    f"baseline__{variant.strategy}__{variant.embedder.name}"
    for variant in select_variants("all", "all")
]
ALL_COLLECTIONS = [variant.collection() for variant in select_variants("all", "all")]
BGE = "fastapi_docs__markdown_ast__bge"
VOYAGE = "fastapi_docs__markdown_ast__voyage"
USAGE = {"prompt_tokens": 120, "completion_tokens": 40, "thoughts_token_count": 30}


def question_entry(question_id: str, origin: str = "handwritten") -> dict:
    """One question file entry, as ``evaluation/questions/*.json`` holds them."""
    return {
        "question_id": question_id,
        "question": f"What does {question_id} ask?",
        "reference_answer": f"The answer to {question_id}.",
        "gold_sources": ["tutorial/path-params.md"],
        "origin": origin,
        "paraphrase": None,
    }


class FakeVoyageTextEmbedder:
    """Carries ``input_type``, as ``VoyageTextEmbedder`` built for queries does."""

    input_type = "query"


class FakeOpenRouterTextEmbedder:
    """Has no ``input_type`` attribute at all, as ``OpenRouterTextEmbedder`` has none."""


class FakePipeline:
    """The parts of a built Haystack ``Pipeline`` the script reads.

    ``metadata`` holds the collection and ``top_k`` as ``build_rag_pipeline`` sets them,
    and ``get_component`` raises ``ValueError`` for a name the pipeline does not hold.
    """

    def __init__(
        self, collection: str, top_k: int, retrieve_only: bool, embedder: str, retrieval: str
    ) -> None:
        self.metadata = {"collection": collection, "top_k": top_k}
        self.retrieve_only = retrieve_only
        self.retrieval = retrieval
        self.components = {
            "query_embedder": FakeVoyageTextEmbedder()
            if embedder == "voyage"
            else FakeOpenRouterTextEmbedder()
        }

    def get_component(self, name: str):
        if name not in self.components:
            raise ValueError(f"Component named {name} not found in the pipeline.")
        return self.components[name]


class FakeBaselineRag:
    """Stands in for ``baseline_rag.build_rag_pipeline`` and ``ask``. A failing ``ask`` may
    raise any ``Exception``: the real one raises Haystack's ``PipelineRuntimeError``, which
    is not a ``RuntimeError``, when a component fails. Like the real ``ask``, the record's
    ``index_run`` is the dense search's (``None`` when it found nothing), and under hybrid
    the chunks are BM25's copies from the saved chunk run, which carry no run ids."""

    def __init__(self) -> None:
        self.missing: set[str] = set()  # collections that do not exist
        self.failing: set[tuple[str, str]] = set()  # (collection, question) pairs
        # what a failing pair raises: by default, ask's error for a reply cut short
        self.error: Exception = RuntimeError(
            "Gemini returned no complete answer; finish reason: length"
        )
        # collection -> the index_run its dense search names; None: it names none
        self.index_runs: dict[str, str | None] = {}
        self.chunks_runs: dict[str, str] = {}  # collection -> the chunks_run its chunks carry
        self.empty: set[str] = set()  # collections that hold no points
        self.with_usage = True  # False: the record as it was before ``usage`` was added
        self.built: list[dict] = []
        self.asked: list[tuple[str, str]] = []
        self.on_ask = lambda: None  # called at the start of every ask

    def build_rag_pipeline(self, variant, *, prefix, top_k, retrieve_only, retrieval):
        collection = variant.collection(prefix)
        self.built.append({"collection": collection, "top_k": top_k, "retrieval": retrieval})
        if collection in self.missing:
            raise ValueError(f"Collection {collection} does not exist; index it with stage 3 first")
        if not retrieve_only and not os.getenv("GEMINI_MODEL"):
            raise ValueError("GEMINI_MODEL is not set; set it in .env, or retrieve only")
        return FakePipeline(collection, top_k, retrieve_only, variant.embedder.name, retrieval)

    def ask(self, pipeline: FakePipeline, question: str) -> dict:
        self.on_ask()
        collection = pipeline.metadata["collection"]
        self.asked.append((collection, question))
        if (collection, question) in self.failing:
            raise self.error
        meta = {
            "source_path": "tutorial/path-params.md",
            "bucket": "tutorial",
            "embedder": collection.rsplit("__", 1)[-1],
            "index_run": self.index_runs.get(collection, "index_test"),
            "chunks_run": self.chunks_runs.get(collection, "chunks_test"),
        }
        index_run = None if collection in self.empty else meta["index_run"]
        if meta["index_run"] is None:
            del meta["index_run"]
        if pipeline.retrieval == "hybrid":
            meta = {field: meta[field] for field in ("source_path", "bucket")}
        record = {
            "question": question,
            "collection": collection,
            "top_k": pipeline.metadata["top_k"],
            "index_run": index_run,
            "chunks": [
                {
                    "rank": rank,
                    "score": 1.0 / rank,
                    "content": f"Chunk {rank} of {collection}.",
                    "meta": {**meta, "chunk_index": rank - 1},
                }
                for rank in (() if collection in self.empty else (1, 2))
            ],
            "answer": None if pipeline.retrieve_only else f"The answer to: {question} [1]",
            "usage": None if pipeline.retrieve_only else dict(USAGE),
        }
        if not self.with_usage:
            del record["usage"]
        return record


class FakeGemini:
    """Stands in for ``google.genai.Client``, as the model probe uses it."""

    def __init__(self) -> None:
        self.model_version: str | None = "gemini-test-001"
        self.error: Exception | None = None
        self.calls: list[dict] = []
        self.models = self

    def client(self, *, api_key: str) -> "FakeGemini":
        self.api_key = api_key
        return self

    def generate_content(self, *, model: str, contents: str) -> SimpleNamespace:
        self.calls.append({"api_key": self.api_key, "model": model, "contents": contents})
        if self.error is not None:
            raise self.error
        return SimpleNamespace(model_version=self.model_version)


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the test on any outgoing connection (C006)."""

    def refuse(*args, **kwargs):
        raise AssertionError("a test tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.fixture
def question_files(tmp_path: Path) -> list[Path]:
    """Two question files in the shapes the real ones have: 3 handwritten, 2 synthetic."""
    handwritten = tmp_path / "questions" / "handwritten.json"
    synthetic = tmp_path / "questions" / "synthetic.json"
    handwritten.parent.mkdir()
    handwritten.write_text(
        json.dumps({"questions": [question_entry(f"hw-0{n}") for n in (1, 2, 3)]}), encoding="utf-8"
    )
    synthetic.write_text(
        json.dumps(
            {
                "source_run": "20261110_000000",
                "model": "gpt-test",
                "seed": 7,
                "questions": [question_entry(f"syn-0{n}", "synthetic") for n in (1, 2)],
            }
        ),
        encoding="utf-8",
    )
    return [handwritten, synthetic]


def write_index_report(
    tmp_path: Path, index_run: str, statuses: dict[str, str], chunks_run: str = "chunks_test"
) -> Path:
    """Write ``index_run``'s report as stage 3 does: a list with one entry per collection."""
    path = tmp_path / "ingestion_outputs" / index_run / "index_report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    report = [
        {
            "collection": collection,
            "strategy": collection.split("__")[1],
            "embedder": collection.split("__")[2],
            "chunks_run": chunks_run,
            "chunks": 100,
            "final_count": 100 if status == "complete" else 64,
            "status": status,
        }
        for collection, status in statuses.items()
    ]
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, question_files: list[Path]) -> None:
    """Point every path the script reads or writes at ``tmp_path``, and keep the real .env
    and the shell's keys out. ``main``'s ``basicConfig(force=True)`` is stopped because it
    would remove pytest's log capture."""
    monkeypatch.setattr(ingestion_pipeline, "OUTPUTS_DIR", tmp_path / "ingestion_outputs")
    write_index_report(tmp_path, "index_test", dict.fromkeys(ALL_COLLECTIONS, "complete"))
    monkeypatch.setattr(generate_results, "OUTPUTS_DIR", tmp_path / "outputs")
    monkeypatch.setattr(generate_results, "QUESTION_FILES", tuple(question_files))
    monkeypatch.setattr(generate_results, "generate_run_id", lambda: RUN_ID)
    monkeypatch.setattr(generate_results, "cached_tokenizer_path", lambda name: tmp_path)
    monkeypatch.setattr(generate_results, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr(logging, "basicConfig", lambda *args, **kwargs: None)
    monkeypatch.setattr(logging.getLogger("httpx"), "setLevel", lambda level: None)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_MODEL", "gemini-test")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("VOYAGE_API_KEY", "test-key")


@pytest.fixture(autouse=True)
def rag(monkeypatch: pytest.MonkeyPatch) -> FakeBaselineRag:
    """Every collection exists and every question is answered, unless a test says otherwise."""
    fake = FakeBaselineRag()
    monkeypatch.setattr(generate_results, "build_rag_pipeline", fake.build_rag_pipeline)
    monkeypatch.setattr(generate_results, "ask", fake.ask)
    return fake


@pytest.fixture(autouse=True)
def gemini(monkeypatch: pytest.MonkeyPatch) -> FakeGemini:
    """The probe's Gemini client; ``gemini-test`` answers as ``gemini-test-001``."""
    fake = FakeGemini()
    monkeypatch.setattr(generate_results.genai, "Client", fake.client)
    return fake


def run_folder(tmp_path: Path) -> Path:
    return tmp_path / "outputs" / RUN_ID


def read_lines(tmp_path: Path) -> list[dict]:
    with (run_folder(tmp_path) / "results.jsonl").open(encoding="utf-8") as lines:
        return [json.loads(line) for line in lines]


def pairs_on_disk(tmp_path: Path) -> list[tuple[str, str]]:
    return [(line["question_id"], line["system"]) for line in read_lines(tmp_path)]


def read_run_meta(tmp_path: Path) -> dict:
    return json.loads((run_folder(tmp_path) / "run_meta.json").read_text(encoding="utf-8"))


def exit_code(argv: list[str]) -> int:
    with pytest.raises(SystemExit) as exit_info:
        generate_results.main(argv)
    return exit_info.value.code


# --- load_questions -------------------------------------------------------------


def test_load_questions_reads_both_files_in_the_order_given(question_files: list[Path]):
    questions = generate_results.load_questions(question_files)

    assert all(isinstance(entry, EvalQuestion) for entry in questions)
    assert [entry.question_id for entry in questions] == [
        "hw-01",
        "hw-02",
        "hw-03",
        "syn-01",
        "syn-02",
    ]
    assert questions[0].question == "What does hw-01 ask?"
    assert [entry.origin for entry in questions] == ["handwritten"] * 3 + ["synthetic"] * 2


def test_the_real_question_files_load_handwritten_first():
    questions = generate_results.load_questions(REAL_QUESTION_FILES)

    assert [path.relative_to(PROJECT_ROOT).as_posix() for path in REAL_QUESTION_FILES] == [
        "evaluation/questions/handwritten.json",
        "evaluation/questions/synthetic.json",
    ]
    assert questions[0].question_id == "hw-01"  # so --limit keeps handwritten questions
    assert len(questions) == len({entry.question_id for entry in questions})


def test_load_questions_refuses_an_id_used_in_two_files(question_files: list[Path]):
    handwritten, synthetic = question_files
    synthetic.write_text(
        json.dumps({"questions": [question_entry("syn-01", "synthetic"), question_entry("hw-02")]}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="question_id used more than once: hw-02$"):
        generate_results.load_questions(question_files)


def test_load_questions_names_every_repeated_id(tmp_path: Path):
    path = tmp_path / "questions.json"
    entries = [
        question_entry("hw-01"),
        question_entry("hw-02"),
        question_entry("hw-01"),
        question_entry("hw-02"),
    ]
    path.write_text(json.dumps({"questions": entries}), encoding="utf-8")

    with pytest.raises(ValueError, match="question_id used more than once: hw-01, hw-02$"):
        generate_results.load_questions([path])


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        json.dumps([question_entry("hw-01")]),
        json.dumps({"items": [question_entry("hw-01")]}),
        json.dumps({"questions": [{**question_entry("hw-01"), "origin": "llm"}]}),
        json.dumps({"questions": [{"question_id": "hw-01"}]}),
    ],
    ids=["bad-json", "bare-list", "no-questions-key", "unknown-origin", "missing-fields"],
)
def test_load_questions_refuses_a_malformed_file_and_names_it(tmp_path: Path, content: str):
    path = tmp_path / "broken.json"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match="broken.json is not a question file"):
        generate_results.load_questions([path])


# --- main: a fresh run ------------------------------------------------------------


def test_a_fresh_run_writes_one_line_per_pair_question_first(tmp_path: Path):
    generate_results.main([])

    lines = read_lines(tmp_path)
    question_ids = ["hw-01", "hw-02", "hw-03", "syn-01", "syn-02"]
    assert len(ALL_SYSTEMS) == 18
    assert [(line["question_id"], line["system"]) for line in lines] == [
        (question_id, system) for question_id in question_ids for system in ALL_SYSTEMS
    ]
    assert sorted(path.name for path in run_folder(tmp_path).iterdir()) == [
        "results.jsonl",
        "run_meta.json",
    ]


def test_a_line_is_the_record_ask_returned_after_system_and_question_id(tmp_path: Path):
    generate_results.main(["--strategy", "markdown_ast", "--embedder", "voyage", "--limit", "1"])

    [line] = read_lines(tmp_path)
    assert list(line) == [
        "system",
        "question_id",
        "question",
        "collection",
        "top_k",
        "index_run",
        "chunks",
        "answer",
        "usage",
    ]
    assert "first_pass_chunks" not in line
    assert line["system"] == "baseline__markdown_ast__voyage"
    assert line["question_id"] == "hw-01"
    assert line["question"] == "What does hw-01 ask?"
    assert line["collection"] == "fastapi_docs__markdown_ast__voyage"
    assert line["top_k"] == 10
    assert line["index_run"] == "index_test"
    assert line["answer"] == "The answer to: What does hw-01 ask? [1]"
    assert line["usage"] == USAGE
    assert [chunk["rank"] for chunk in line["chunks"]] == [1, 2]
    assert line["chunks"][0] == {
        "rank": 1,
        "score": 1.0,
        "content": "Chunk 1 of fastapi_docs__markdown_ast__voyage.",
        "meta": {
            "source_path": "tutorial/path-params.md",
            "bucket": "tutorial",
            "embedder": "voyage",
            "index_run": "index_test",
            "chunks_run": "chunks_test",
            "chunk_index": 0,
        },
    }


def test_a_record_without_usage_is_written_as_it_came(tmp_path: Path, rag: FakeBaselineRag):
    rag.with_usage = False

    generate_results.main(["--strategy", "markdown_ast", "--embedder", "voyage", "--limit", "1"])

    [line] = read_lines(tmp_path)
    assert "usage" not in line
    assert line["answer"] == "The answer to: What does hw-01 ask? [1]"


def test_limit_keeps_the_first_n_questions(tmp_path: Path):
    generate_results.main(["--strategy", "recursive", "--limit", "4"])

    lines = read_lines(tmp_path)
    assert [(line["question_id"], line["system"]) for line in lines] == [
        (question_id, f"baseline__recursive__{embedder}")
        for question_id in ("hw-01", "hw-02", "hw-03", "syn-01")
        for embedder in ("bge", "voyage")
    ]
    assert read_run_meta(tmp_path)["settings"]["limit"] == 4


def test_run_meta_is_on_disk_before_the_first_ask_and_each_line_as_it_completes(
    tmp_path: Path, rag: FakeBaselineRag
):
    seen: list[tuple[dict, int]] = []

    def look() -> None:
        seen.append((read_run_meta(tmp_path)["settings"], len(read_lines(tmp_path))))

    rag.on_ask = look

    generate_results.main(["--strategy", "sentence", "--embedder", "bge", "--limit", "3"])

    settings = read_run_meta(tmp_path)["settings"]
    assert seen == [(settings, 0), (settings, 1), (settings, 2)]


def test_run_meta_records_the_settings_the_question_files_and_the_query_embedders(
    tmp_path: Path, question_files: list[Path]
):
    generate_results.main(["--strategy", "naive_small", "--limit", "2"])

    run_meta = read_run_meta(tmp_path)
    assert list(run_meta) == [
        "stage",
        "run_id",
        "started_at",
        "settings",
        "question_files",
        "embedders",
        "collections",
        "failed",
        "model_versions",
    ]
    assert run_meta["stage"] == "generate_results"
    assert run_meta["run_id"] == RUN_ID
    assert run_meta["started_at"].endswith("+00:00")
    assert run_meta["settings"] == {
        "strategies": ["naive_small"],
        "embedders": ["bge", "voyage"],
        "collection_prefix": "fastapi_docs",
        "retrieve_only": False,
        "limit": 2,
        "top_k": 10,
        "answer_model": "gemini-test",
        "retrieval": "dense",
        "hybrid": None,
    }
    assert [(entry["count"], Path(entry["path"]).name) for entry in run_meta["question_files"]] == [
        (3, "handwritten.json"),
        (2, "synthetic.json"),
    ]
    assert not Path(run_meta["question_files"][0]["path"]).is_absolute()
    assert [entry["sha256"] for entry in run_meta["question_files"]] == [
        hashlib.sha256(path.read_bytes()).hexdigest() for path in question_files
    ]
    assert run_meta["embedders"] == {
        "bge": {
            "model": "BAAI/bge-large-en-v1.5",
            "backend": "openrouter",
            "api_model": "baai/bge-large-en-v1.5",
            "dimension": 1024,
            "query_prefix": "Represent this sentence for searching relevant passages: ",
            "input_type": None,
        },
        "voyage": {
            "model": "voyage-4-lite",
            "backend": "voyage",
            "api_model": None,
            "dimension": 2048,
            "query_prefix": "",
            "input_type": "query",
        },
    }
    assert run_meta["failed"] == []


def test_the_default_run_covers_every_strategy_and_embedder(tmp_path: Path):
    generate_results.main(["--limit", "1"])

    settings = read_run_meta(tmp_path)["settings"]
    assert settings["strategies"] == list(STRATEGIES)
    assert len(settings["strategies"]) == 9
    assert settings["embedders"] == ["bge", "voyage"]
    assert settings["limit"] == 1


def test_another_prefix_points_the_run_at_other_collections(tmp_path: Path, rag: FakeBaselineRag):
    write_index_report(tmp_path, "index_test", {"ix_verify__sentence__bge": "complete"})

    generate_results.main(
        ["--strategy", "sentence", "--embedder", "bge", "--collection-prefix", "ix_verify"]
    )

    assert {line["collection"] for line in read_lines(tmp_path)} == {"ix_verify__sentence__bge"}
    assert {line["system"] for line in read_lines(tmp_path)} == {"baseline__sentence__bge"}
    assert rag.built == [
        {"collection": "ix_verify__sentence__bge", "top_k": 10, "retrieval": "dense"}
    ]


# --- main: retrieve-only ------------------------------------------------------------


def test_retrieve_only_writes_null_answers_and_makes_no_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gemini: FakeGemini
):
    monkeypatch.delenv("GEMINI_MODEL")
    monkeypatch.delenv("GOOGLE_API_KEY")

    generate_results.main(["--strategy", "markdown_ast", "--retrieve-only", "--limit", "2"])

    lines = read_lines(tmp_path)
    assert len(lines) == 4
    assert all(line["answer"] is None and line["usage"] is None for line in lines)
    assert all(len(line["chunks"]) == 2 for line in lines)
    run_meta = read_run_meta(tmp_path)
    assert run_meta["settings"]["retrieve_only"] is True
    assert run_meta["settings"]["answer_model"] is None
    assert gemini.calls == []
    assert run_meta["model_versions"] == []


# --- main: a run that cannot start --------------------------------------------------


def test_a_missing_collection_stops_the_run_before_its_folder_exists(
    tmp_path: Path, rag: FakeBaselineRag, gemini: FakeGemini, caplog: pytest.LogCaptureFixture
):
    last = ALL_SYSTEMS[-1].replace("baseline", "fastapi_docs", 1)  # the last of the 18 built
    rag.missing = {last}

    assert exit_code([]) == 1

    assert len(rag.built) == 18
    assert rag.asked == []
    assert gemini.calls == []  # the free checks come before the paid probe
    assert not (tmp_path / "outputs").exists()
    assert f"Collection {last} does not exist" in caplog.text


def test_answering_without_gemini_model_stops_the_run_before_its_folder_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rag: FakeBaselineRag
):
    monkeypatch.delenv("GEMINI_MODEL")

    assert exit_code(["--strategy", "sentence"]) == 1

    assert rag.asked == []
    assert not (tmp_path / "outputs").exists()


def test_a_missing_bge_tokenizer_stops_the_run_before_any_pipeline_is_built(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rag: FakeBaselineRag
):
    monkeypatch.setattr(generate_results, "cached_tokenizer_path", lambda name: None)

    assert exit_code(["--strategy", "sentence"]) == 1

    assert rag.built == []
    assert not (tmp_path / "outputs").exists()


def test_a_voyage_only_run_needs_no_bge_tokenizer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(generate_results, "cached_tokenizer_path", lambda name: None)

    generate_results.main(["--strategy", "sentence", "--embedder", "voyage", "--limit", "1"])

    assert len(read_lines(tmp_path)) == 1


@pytest.mark.parametrize(
    ("embedder", "variable"), [("bge", "OPENROUTER_API_KEY"), ("voyage", "VOYAGE_API_KEY")]
)
@pytest.mark.parametrize("unset", [True, False], ids=["unset", "empty"])
def test_a_missing_embedder_key_stops_the_run_before_the_probe_and_its_folder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rag: FakeBaselineRag,
    gemini: FakeGemini,
    caplog: pytest.LogCaptureFixture,
    embedder: str,
    variable: str,
    unset: bool,
):
    if unset:
        monkeypatch.delenv(variable)
    else:
        monkeypatch.setenv(variable, "")

    assert exit_code(["--strategy", "sentence", "--limit", "1"]) == 1

    assert rag.built == []
    assert gemini.calls == []
    assert not (tmp_path / "outputs").exists()
    assert f"{variable} is not set; {embedder} needs it" in caplog.text


def test_a_voyage_only_run_needs_no_openrouter_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("OPENROUTER_API_KEY")

    generate_results.main(["--strategy", "sentence", "--embedder", "voyage", "--limit", "1"])

    assert len(read_lines(tmp_path)) == 1


def test_a_malformed_question_file_stops_the_run_before_any_pipeline_is_built(
    tmp_path: Path, question_files: list[Path], rag: FakeBaselineRag
):
    question_files[1].write_text("{not json", encoding="utf-8")

    assert exit_code([]) == 1

    assert rag.built == []
    assert not (tmp_path / "outputs").exists()


def test_a_fresh_run_refuses_a_run_folder_that_already_exists(
    tmp_path: Path, rag: FakeBaselineRag, gemini: FakeGemini, caplog: pytest.LogCaptureFixture
):
    run_folder(tmp_path).mkdir(parents=True)
    (run_folder(tmp_path) / "results.jsonl").write_text('{"kept": true}\n{"ha', encoding="utf-8")

    assert exit_code(["--strategy", "sentence", "--limit", "1"]) == 1

    assert rag.asked == []
    assert rag.built == []  # refused before any pipeline is built
    assert gemini.calls == []  # and before the paid probe
    assert f"Run folder {run_folder(tmp_path)} already exists" in caplog.text
    assert sorted(path.name for path in run_folder(tmp_path).iterdir()) == ["results.jsonl"]
    assert (run_folder(tmp_path) / "results.jsonl").read_text(encoding="utf-8") == (
        '{"kept": true}\n{"ha'
    )


def test_a_fresh_run_refuses_a_run_folder_made_after_its_first_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rag: FakeBaselineRag
):
    def build_and_make_the_folder(variant, **settings):
        run_folder(tmp_path).mkdir(parents=True, exist_ok=True)  # another run, meanwhile
        return rag.build_rag_pipeline(variant, **settings)

    monkeypatch.setattr(generate_results, "build_rag_pipeline", build_and_make_the_folder)

    assert exit_code(["--strategy", "sentence", "--limit", "1"]) == 1

    assert rag.asked == []
    assert list(run_folder(tmp_path).iterdir()) == []


@pytest.mark.parametrize(
    "argv",
    [
        ["--strategy", "naive_tiny"],
        ["--embedder", "openai"],
        ["--collection-prefix", "a__b"],
        ["--collection-prefix", ""],
        ["--limit", "0"],
        ["--limit", "two"],
        ["--top-k", "5"],
        ["--questions", "mine.json"],
        ["--run-id", "mine"],
        ["--retrieval", "sparse"],
    ],
    ids=[
        "unknown-strategy",
        "unknown-embedder",
        "bad-prefix",
        "empty-prefix",
        "limit-0",
        "limit-not-a-number",
        "no-top-k-option",
        "no-questions-option",
        "no-run-id-option",
        "unknown-retrieval",
    ],
)
def test_main_exits_2_for_a_bad_command_line(argv: list[str], tmp_path: Path, rag: FakeBaselineRag):
    assert exit_code(argv) == 2

    assert rag.built == []
    assert not (tmp_path / "outputs").exists()


# --- main: a question that fails ------------------------------------------------------


COMPONENT_FAILURE = PipelineRuntimeError.from_exception(
    "query_embedder", FakeOpenRouterTextEmbedder, RuntimeError("OpenRouter returned 401")
)


@pytest.mark.parametrize(
    ("error", "recorded"),
    [
        (None, "RuntimeError: Gemini returned no complete answer; finish reason: length"),
        (COMPONENT_FAILURE, f"PipelineRuntimeError: {COMPONENT_FAILURE}"),
    ],
    ids=["reply-cut-short", "component-failed"],
)
def test_a_failed_pair_is_listed_and_left_out_and_the_run_goes_on(
    tmp_path: Path,
    rag: FakeBaselineRag,
    caplog: pytest.LogCaptureFixture,
    error: Exception | None,
    recorded: str,
):
    rag.failing = {("fastapi_docs__sentence__bge", "What does hw-01 ask?")}
    if error is not None:
        rag.error = error

    assert exit_code(["--strategy", "sentence", "--limit", "2"]) == 1

    assert [(line["question_id"], line["system"]) for line in read_lines(tmp_path)] == [
        ("hw-01", "baseline__sentence__voyage"),
        ("hw-02", "baseline__sentence__bge"),
        ("hw-02", "baseline__sentence__voyage"),
    ]
    assert len(rag.asked) == 4
    assert read_run_meta(tmp_path)["failed"] == [
        {
            "system": "baseline__sentence__bge",
            "question_id": "hw-01",
            "error": recorded,
        }
    ]
    [failure] = [record for record in caplog.records if "hw-01 failed" in record.getMessage()]
    assert failure.exc_info is not None  # logged with its traceback
    assert f"--resume {RUN_ID}" in caplog.text


def test_run_meta_is_rewritten_when_the_run_is_interrupted(tmp_path: Path, rag: FakeBaselineRag):
    # Both collections pass the gate on hw-01, so only the `finally` can save hw-02's failure.
    rag.failing = {("fastapi_docs__sentence__bge", "What does hw-02 ask?")}
    calls = []

    def interrupt_the_fourth_ask() -> None:
        calls.append(None)
        if len(calls) == 4:
            raise KeyboardInterrupt

    rag.on_ask = interrupt_the_fourth_ask

    with pytest.raises(KeyboardInterrupt):
        generate_results.main(["--strategy", "sentence", "--limit", "2"])

    assert pairs_on_disk(tmp_path) == [
        ("hw-01", "baseline__sentence__bge"),
        ("hw-01", "baseline__sentence__voyage"),
    ]
    assert [entry["question_id"] for entry in read_run_meta(tmp_path)["failed"]] == ["hw-02"]


# --- main: the index report gate ------------------------------------------------------


def test_collections_record_the_index_run_the_chunks_carry_and_its_status(
    tmp_path: Path, rag: FakeBaselineRag
):
    # Two stage 3 runs built markdown_ast × voyage: the first left it partial, and the
    # rebuild, whose id sorts first, completed it. The chunks name the rebuild.
    write_index_report(
        tmp_path, "index_test", {**dict.fromkeys(ALL_COLLECTIONS, "complete"), VOYAGE: "partial"}
    )
    write_index_report(tmp_path, "a_rebuild", {VOYAGE: "complete"})
    rag.index_runs = {VOYAGE: "a_rebuild"}

    generate_results.main(["--strategy", "markdown_ast", "--limit", "2"])

    assert len(read_lines(tmp_path)) == 4
    assert read_run_meta(tmp_path)["collections"] == {
        BGE: {"index_run": "index_test", "chunks_run": "chunks_test", "index_status": "complete"},
        VOYAGE: {"index_run": "a_rebuild", "chunks_run": "chunks_test", "index_status": "complete"},
    }


def test_a_partial_collection_stops_the_run_at_its_first_record(
    tmp_path: Path, rag: FakeBaselineRag, caplog: pytest.LogCaptureFixture
):
    # The run the chunks name left it partial; a later run that completed another copy
    # of the collection must not be the one that is read.
    write_index_report(tmp_path, "index_partial", {VOYAGE: "partial"})
    write_index_report(tmp_path, "z_later", {VOYAGE: "complete"})
    rag.index_runs = {VOYAGE: "index_partial"}

    assert exit_code(["--strategy", "markdown_ast", "--limit", "2"]) == 1

    assert [line["system"] for line in read_lines(tmp_path)] == ["baseline__markdown_ast__bge"]
    assert rag.asked == [(BGE, "What does hw-01 ask?"), (VOYAGE, "What does hw-01 ask?")]
    run_meta = read_run_meta(tmp_path)
    assert list(run_meta["collections"]) == [BGE]
    assert run_meta["failed"] == []
    assert f"{VOYAGE}: " in caplog.text
    assert "records it as 'partial', not 'complete'" in caplog.text
    assert f"--resume {RUN_ID}" in caplog.text


@pytest.mark.parametrize(
    ("report", "message"),
    [
        (None, "cannot read the index report of run index_gone: FileNotFoundError"),
        ("{not json", "cannot read the index report of run index_gone: JSONDecodeError"),
        ('{"status": "complete"}', "cannot read the index report of run index_gone: TypeError"),
        ('[{"collection": "x"}]', "cannot read the index report of run index_gone: KeyError"),
        ("[]", "records it as None, not 'complete'"),
    ],
    ids=["missing", "bad-json", "not-a-list", "entry-without-status", "collection-not-listed"],
)
def test_a_missing_or_unreadable_index_report_stops_the_run(
    tmp_path: Path,
    rag: FakeBaselineRag,
    caplog: pytest.LogCaptureFixture,
    report: str | None,
    message: str,
):
    rag.index_runs = {BGE: "index_gone"}
    if report is not None:
        path = write_index_report(tmp_path, "index_gone", {})
        path.write_text(report, encoding="utf-8")

    assert exit_code(["--strategy", "markdown_ast", "--limit", "2"]) == 1

    assert read_lines(tmp_path) == []
    assert len(rag.asked) == 1
    assert read_run_meta(tmp_path)["collections"] == {}
    assert message in caplog.text


@pytest.mark.parametrize("case", ["no-chunks", "no-index-run", "index-run-not-a-folder-name"])
def test_a_first_record_that_cannot_name_its_index_run_stops_the_run(
    tmp_path: Path, rag: FakeBaselineRag, caplog: pytest.LogCaptureFixture, case: str
):
    if case == "no-chunks":
        rag.empty = {BGE}
    else:
        rag.index_runs = {BGE: None if case == "no-index-run" else "../index_test"}

    assert exit_code(["--strategy", "markdown_ast", "--retrieve-only"]) == 1

    assert read_lines(tmp_path) == []
    assert len(rag.asked) == 1
    assert f"{BGE}: " in caplog.text


def test_a_collections_entry_is_on_disk_before_its_first_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    real_write_run_meta = generate_results.write_run_meta
    # collection -> the collections of the lines on disk when its entry first reached disk
    lines_when_saved: dict[str, list[str]] = {}

    def write_and_look(run_dir: Path, run_meta: dict) -> None:
        real_write_run_meta(run_dir, run_meta)
        on_disk = read_lines(tmp_path) if (run_dir / "results.jsonl").exists() else []
        for collection in read_run_meta(tmp_path)["collections"]:
            lines_when_saved.setdefault(collection, [line["collection"] for line in on_disk])

    monkeypatch.setattr(generate_results, "write_run_meta", write_and_look)

    generate_results.main(["--strategy", "markdown_ast", "--limit", "2"])

    assert lines_when_saved == {BGE: [], VOYAGE: [BGE]}


def test_the_gate_reads_each_collections_report_once(tmp_path: Path, rag: FakeBaselineRag):
    report = tmp_path / "ingestion_outputs" / "index_test" / "index_report.json"

    def remove_the_report_after_the_first_question() -> None:
        if len(rag.asked) == 2:
            report.unlink()

    rag.on_ask = remove_the_report_after_the_first_question

    generate_results.main(["--strategy", "markdown_ast", "--limit", "3"])

    assert len(read_lines(tmp_path)) == 6


# --- main: hybrid retrieval -----------------------------------------------------------

# retrieval/hybrid.py's constants, as run_meta.json records them.
HYBRID = {"chunks_run": "full_chunks_20261015", "candidates": 50, "rerank_model": "rerank-2.5"}
HYBRID_BGE = (BGE, "What does hw-01 ask?")


@pytest.fixture
def bm25_chunk_run(tmp_path: Path) -> None:
    """index_test's report lists every collection as indexed from the run BM25 searches."""
    write_index_report(
        tmp_path,
        "index_test",
        dict.fromkeys(ALL_COLLECTIONS, "complete"),
        chunks_run="full_chunks_20261015",
    )


def test_a_hybrid_run_names_its_systems_hybrid_and_records_its_settings(
    tmp_path: Path, rag: FakeBaselineRag, bm25_chunk_run: None
):
    generate_results.main(["--retrieval", "hybrid", "--limit", "1"])

    lines = read_lines(tmp_path)
    assert [line["system"] for line in lines] == [
        system.replace("baseline", "hybrid", 1) for system in ALL_SYSTEMS
    ]
    assert {entry["retrieval"] for entry in rag.built} == {"hybrid"}
    # The gate read each record's index_run: no chunk names one.
    assert all("index_run" not in chunk["meta"] for line in lines for chunk in line["chunks"])
    run_meta = read_run_meta(tmp_path)
    assert run_meta["settings"]["retrieval"] == "hybrid"
    assert run_meta["settings"]["hybrid"] == HYBRID
    assert run_meta["collections"][BGE] == {
        "index_run": "index_test",
        "chunks_run": "full_chunks_20261015",
        "index_status": "complete",
    }


def test_a_hybrid_record_without_index_run_stops_the_run(
    tmp_path: Path, rag: FakeBaselineRag, bm25_chunk_run: None, caplog: pytest.LogCaptureFixture
):
    rag.index_runs = {BGE: None}

    assert exit_code(["--retrieval", "hybrid", "--strategy", "markdown_ast"]) == 1

    assert read_lines(tmp_path) == []
    assert rag.asked == [HYBRID_BGE]
    assert f"{BGE}: its first record names no index_run" in caplog.text


def test_a_hybrid_run_stops_when_the_collection_was_indexed_from_another_chunk_run(
    tmp_path: Path, rag: FakeBaselineRag, caplog: pytest.LogCaptureFixture
):
    assert exit_code(["--retrieval", "hybrid", "--strategy", "markdown_ast"]) == 1

    assert read_lines(tmp_path) == []
    assert rag.asked == [HYBRID_BGE]
    assert read_run_meta(tmp_path)["collections"] == {}
    assert (
        "records it as indexed from chunk run 'chunks_test', and hybrid retrieval's BM25 "
        "searches 'full_chunks_20261015'"
    ) in caplog.text


def test_resume_keeps_a_hybrid_runs_retrieval(
    tmp_path: Path, rag: FakeBaselineRag, bm25_chunk_run: None
):
    rag.failing = {HYBRID_BGE}
    assert exit_code(["--retrieval", "hybrid", "--strategy", "markdown_ast", "--limit", "2"]) == 1
    rag.failing = set()
    rag.asked.clear()
    rag.built.clear()

    generate_results.main(["--resume", RUN_ID])

    assert rag.asked == [HYBRID_BGE]
    assert {entry["retrieval"] for entry in rag.built} == {"hybrid"}
    assert pairs_on_disk(tmp_path)[-1] == ("hw-01", "hybrid__markdown_ast__bge")
    assert read_run_meta(tmp_path)["settings"]["hybrid"] == HYBRID


def test_resume_refuses_a_hybrid_run_whose_hybrid_settings_changed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rag: FakeBaselineRag,
    bm25_chunk_run: None,
    caplog: pytest.LogCaptureFixture,
):
    rag.failing = {HYBRID_BGE}
    assert exit_code(["--retrieval", "hybrid", "--strategy", "markdown_ast", "--limit", "2"]) == 1
    rag.failing = set()
    rag.asked.clear()
    rag.built.clear()
    monkeypatch.setitem(generate_results.HYBRID_SETTINGS, "candidates", 20)

    assert exit_code(["--resume", RUN_ID]) == 1

    assert rag.asked == []
    assert rag.built == []
    assert "its hybrid retrieval used" in caplog.text


def test_resume_reads_a_run_meta_saved_before_hybrid_retrieval_as_dense(
    tmp_path: Path, rag: FakeBaselineRag
):
    rag.failing = {SENTENCE_BGE}
    assert exit_code(["--strategy", "sentence", "--limit", "2"]) == 1
    run_meta = read_run_meta(tmp_path)
    del run_meta["settings"]["retrieval"], run_meta["settings"]["hybrid"]
    generate_results.write_run_meta(run_folder(tmp_path), run_meta)
    rag.failing = set()
    rag.asked.clear()
    rag.built.clear()

    generate_results.main(["--resume", RUN_ID])

    assert rag.asked == [SENTENCE_BGE]
    assert {entry["retrieval"] for entry in rag.built} == {"dense"}
    assert pairs_on_disk(tmp_path)[-1] == ("hw-01", "baseline__sentence__bge")
    settings = read_run_meta(tmp_path)["settings"]
    assert (settings["retrieval"], settings["hybrid"]) == ("dense", None)


def test_a_hybrid_run_without_a_voyage_key_stops_before_the_probe_and_its_folder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rag: FakeBaselineRag,
    gemini: FakeGemini,
    caplog: pytest.LogCaptureFixture,
):
    monkeypatch.delenv("VOYAGE_API_KEY")
    argv = ["--embedder", "bge", "--strategy", "sentence", "--limit", "1"]

    assert exit_code(["--retrieval", "hybrid", *argv]) == 1

    assert rag.built == []
    assert gemini.calls == []
    assert not (tmp_path / "outputs").exists()
    assert "VOYAGE_API_KEY is not set; hybrid retrieval needs it to rerank" in caplog.text
    generate_results.main(argv)  # dense retrieval with bge needs no Voyage key
    assert len(read_lines(tmp_path)) == 1


# --- main: the model probe ------------------------------------------------------------


def test_an_answering_run_probes_once_before_the_first_ask(
    tmp_path: Path, rag: FakeBaselineRag, gemini: FakeGemini
):
    probes_before_each_ask = []
    rag.on_ask = lambda: probes_before_each_ask.append(len(gemini.calls))
    before = datetime.now(UTC).replace(microsecond=0)

    generate_results.main(["--strategy", "sentence", "--limit", "2"])

    [call] = gemini.calls
    assert (call["model"], call["api_key"]) == ("gemini-test", "test-key")
    assert probes_before_each_ask == [1, 1, 1, 1]
    [probe] = read_run_meta(tmp_path)["model_versions"]
    assert list(probe) == ["at", "model_version"]
    assert probe["model_version"] == "gemini-test-001"
    assert before <= datetime.fromisoformat(probe["at"]) <= datetime.now(UTC)


def test_a_probe_that_reports_no_version_is_recorded_as_null(tmp_path: Path, gemini: FakeGemini):
    gemini.model_version = None

    generate_results.main(["--strategy", "sentence", "--limit", "1"])

    [probe] = read_run_meta(tmp_path)["model_versions"]
    assert probe["model_version"] is None


@pytest.mark.parametrize("case", ["refused-key", "no-key"])
def test_a_failed_probe_stops_the_run_before_its_folder_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rag: FakeBaselineRag,
    gemini: FakeGemini,
    caplog: pytest.LogCaptureFixture,
    case: str,
):
    if case == "no-key":
        monkeypatch.delenv("GOOGLE_API_KEY")
    else:
        reply = {"error": {"code": 400, "message": "API key not valid.", "status": "INVALID"}}
        gemini.error = errors.ClientError(400, reply)

    assert exit_code(["--strategy", "sentence", "--limit", "1"]) == 1

    assert rag.asked == []
    assert not (tmp_path / "outputs").exists()
    assert "The model probe failed, so no question was asked" in caplog.text


# --- main: --resume -------------------------------------------------------------------

SENTENCE_BGE = ("fastapi_docs__sentence__bge", "What does hw-01 ask?")


def test_resume_asks_only_the_pairs_not_yet_written(
    tmp_path: Path, rag: FakeBaselineRag, caplog: pytest.LogCaptureFixture
):
    rag.failing = {SENTENCE_BGE}
    assert exit_code(["--strategy", "sentence", "--limit", "2"]) == 1
    first = read_run_meta(tmp_path)
    rag.failing = set()
    rag.asked.clear()
    caplog.set_level(logging.INFO)

    generate_results.main(["--resume", RUN_ID])

    assert rag.asked == [SENTENCE_BGE]
    last_line = caplog.records[-1].getMessage()
    assert "this attempt added 1 record(s)" in last_line
    assert last_line.endswith("results.jsonl now holds 4")
    assert pairs_on_disk(tmp_path) == [
        ("hw-01", "baseline__sentence__voyage"),
        ("hw-02", "baseline__sentence__bge"),
        ("hw-02", "baseline__sentence__voyage"),
        ("hw-01", "baseline__sentence__bge"),
    ]
    resumed = read_run_meta(tmp_path)
    assert len(first["failed"]) == 1
    assert resumed["failed"] == []
    assert len(resumed["model_versions"]) == 2
    for field in ("run_id", "started_at", "settings", "question_files", "embedders"):
        assert resumed[field] == first[field]
    assert sorted(path.name for path in run_folder(tmp_path).iterdir()) == [
        "results.jsonl",
        "run_meta.json",
    ]


def test_resume_lists_a_pair_that_fails_again_once(tmp_path: Path, rag: FakeBaselineRag):
    rag.failing = {SENTENCE_BGE}
    assert exit_code(["--strategy", "sentence", "--limit", "2"]) == 1

    assert exit_code(["--resume", RUN_ID]) == 1

    assert len(read_lines(tmp_path)) == 3
    assert [
        (entry["system"], entry["question_id"]) for entry in read_run_meta(tmp_path)["failed"]
    ] == [("baseline__sentence__bge", "hw-01")]


def test_resume_takes_every_setting_from_the_runs_run_meta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rag: FakeBaselineRag, gemini: FakeGemini
):
    write_index_report(tmp_path, "index_test", {"ix_verify__sentence__voyage": "complete"})
    argv = ["--strategy", "sentence", "--embedder", "voyage", "--collection-prefix", "ix_verify"]

    def interrupt_the_second_ask() -> None:
        if rag.asked:
            raise KeyboardInterrupt

    rag.on_ask = interrupt_the_second_ask
    with pytest.raises(KeyboardInterrupt):
        generate_results.main([*argv, "--retrieve-only", "--limit", "3"])
    rag.on_ask = lambda: None
    rag.built.clear()
    # Not needed by a retrieve-only run, and not what the run was started with.
    monkeypatch.setenv("GEMINI_MODEL", "another-model")
    monkeypatch.setattr(generate_results, "cached_tokenizer_path", lambda name: None)

    generate_results.main(["--resume", RUN_ID])

    assert rag.built == [
        {"collection": "ix_verify__sentence__voyage", "top_k": 10, "retrieval": "dense"}
    ]
    assert pairs_on_disk(tmp_path) == [
        (question_id, "baseline__sentence__voyage") for question_id in ("hw-01", "hw-02", "hw-03")
    ]
    assert all(line["answer"] is None for line in read_lines(tmp_path))
    assert gemini.calls == []
    assert read_run_meta(tmp_path)["settings"]["limit"] == 3


def test_resume_drops_a_half_written_last_line_and_asks_its_pair_again(
    tmp_path: Path, rag: FakeBaselineRag, caplog: pytest.LogCaptureFixture
):
    generate_results.main(["--strategy", "sentence", "--limit", "2"])
    results = run_folder(tmp_path) / "results.jsonl"
    whole = results.read_bytes().splitlines(keepends=True)
    results.write_bytes(b"".join(whole[:2]) + whole[2][: len(whole[2]) // 2])
    rag.asked.clear()

    generate_results.main(["--resume", RUN_ID])

    assert rag.asked == [
        ("fastapi_docs__sentence__bge", "What does hw-02 ask?"),
        ("fastapi_docs__sentence__voyage", "What does hw-02 ask?"),
    ]
    assert results.read_bytes().splitlines(keepends=True) == whole
    assert "ends in a half-written line" in caplog.text


def test_resume_keeps_a_line_whose_text_holds_a_unicode_line_separator(
    tmp_path: Path, rag: FakeBaselineRag
):
    generate_results.main(["--strategy", "sentence", "--embedder", "bge", "--limit", "1"])
    results = run_folder(tmp_path) / "results.jsonl"
    [line] = read_lines(tmp_path)
    line["answer"] = "First line.\u2028Second line."
    results.write_text(json.dumps(line, ensure_ascii=False) + "\n", encoding="utf-8")
    rag.asked.clear()

    generate_results.main(["--resume", RUN_ID])

    assert rag.asked == []


def test_resume_refuses_a_results_line_that_is_not_a_record(tmp_path: Path, rag: FakeBaselineRag):
    generate_results.main(["--strategy", "sentence", "--limit", "2"])
    results = run_folder(tmp_path) / "results.jsonl"
    damaged = results.read_bytes().replace(b'{"system"', b'{"sys', 1)
    results.write_bytes(damaged)
    rag.asked.clear()

    assert exit_code(["--resume", RUN_ID]) == 1

    assert rag.asked == []
    assert results.read_bytes() == damaged


def test_resume_refuses_a_question_file_that_changed(
    tmp_path: Path,
    question_files: list[Path],
    rag: FakeBaselineRag,
    caplog: pytest.LogCaptureFixture,
):
    rag.failing = {SENTENCE_BGE}
    assert exit_code(["--strategy", "sentence", "--limit", "2"]) == 1
    with (run_folder(tmp_path) / "results.jsonl").open("ab") as results:
        results.write(b'{"system": "baseline__sen')  # a refused resume changes no file
    before = (run_folder(tmp_path) / "results.jsonl").read_bytes()
    reworded = {**question_entry("syn-01", "synthetic"), "question": "A different question?"}
    question_files[1].write_text(
        json.dumps({"questions": [reworded, question_entry("syn-02", "synthetic")]}),
        encoding="utf-8",
    )
    rag.failing = set()
    rag.asked.clear()
    rag.built.clear()

    assert exit_code(["--resume", RUN_ID]) == 1

    assert rag.asked == []
    assert rag.built == []
    assert (run_folder(tmp_path) / "results.jsonl").read_bytes() == before
    assert "synthetic.json changed since it started" in caplog.text
    assert "handwritten.json" not in caplog.text


def test_resume_refuses_another_answer_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rag: FakeBaselineRag
):
    rag.failing = {SENTENCE_BGE}
    assert exit_code(["--strategy", "sentence", "--limit", "2"]) == 1
    rag.asked.clear()
    monkeypatch.setenv("GEMINI_MODEL", "another-model")

    assert exit_code(["--resume", RUN_ID]) == 1

    assert rag.asked == []


def test_resume_warns_when_the_alias_answers_as_another_version(
    tmp_path: Path, rag: FakeBaselineRag, gemini: FakeGemini, caplog: pytest.LogCaptureFixture
):
    rag.failing = {SENTENCE_BGE}
    assert exit_code(["--strategy", "sentence", "--limit", "2"]) == 1
    assert exit_code(["--resume", RUN_ID]) == 1
    assert "more than one model version" not in caplog.text
    gemini.model_version = "gemini-test-002"
    rag.failing = set()

    generate_results.main(["--resume", RUN_ID])

    assert [entry["model_version"] for entry in read_run_meta(tmp_path)["model_versions"]] == [
        "gemini-test-001",
        "gemini-test-001",
        "gemini-test-002",
    ]
    [warning] = [record for record in caplog.records if record.levelname == "WARNING"]
    assert "gemini-test now answers as gemini-test-002" in warning.getMessage()
    assert "recorded gemini-test-001" in warning.getMessage()


def test_resuming_a_finished_run_asks_nothing_and_makes_no_probe(
    tmp_path: Path, rag: FakeBaselineRag, gemini: FakeGemini
):
    generate_results.main(["--strategy", "sentence", "--limit", "2"])
    before = (run_folder(tmp_path) / "results.jsonl").read_bytes()
    rag.asked.clear()

    generate_results.main(["--resume", RUN_ID])

    assert rag.asked == []
    assert len(gemini.calls) == 1
    assert (run_folder(tmp_path) / "results.jsonl").read_bytes() == before
    assert len(read_run_meta(tmp_path)["model_versions"]) == 1


def test_resume_checks_each_collections_index_report_again(tmp_path: Path, rag: FakeBaselineRag):
    rag.failing = {SENTENCE_BGE}
    assert exit_code(["--strategy", "sentence", "--limit", "2"]) == 1
    rag.failing = set()
    write_index_report(tmp_path, "index_test", {"fastapi_docs__sentence__bge": "partial"})

    assert exit_code(["--resume", RUN_ID]) == 1

    assert len(read_lines(tmp_path)) == 3
    # The entry still describes the build the three saved lines came from.
    collections = read_run_meta(tmp_path)["collections"]
    assert collections["fastapi_docs__sentence__bge"]["index_status"] == "complete"


@pytest.mark.parametrize(
    ("field", "earlier", "now"),
    [("index_run", "index_test", "index_rebuilt"), ("chunks_run", "chunks_test", "chunks_rebuilt")],
)
def test_resume_stops_when_a_collection_was_rebuilt_after_an_earlier_attempt(
    tmp_path: Path,
    rag: FakeBaselineRag,
    caplog: pytest.LogCaptureFixture,
    field: str,
    earlier: str,
    now: str,
):
    sentence_bge = "fastapi_docs__sentence__bge"
    rag.failing = {SENTENCE_BGE}
    assert exit_code(["--strategy", "sentence", "--limit", "2"]) == 1
    before = (run_folder(tmp_path) / "results.jsonl").read_bytes()
    rag.failing = set()
    rag.asked.clear()
    # A complete rebuild: its own report says complete, so only the comparison can stop it.
    write_index_report(tmp_path, "index_rebuilt", {sentence_bge: "complete"})
    if field == "index_run":
        rag.index_runs = {sentence_bge: now}
    else:
        rag.chunks_runs = {sentence_bge: now}

    assert exit_code(["--resume", RUN_ID]) == 1

    assert rag.asked == [SENTENCE_BGE]  # its record came back and was not saved
    assert (run_folder(tmp_path) / "results.jsonl").read_bytes() == before
    assert read_run_meta(tmp_path)["collections"][sentence_bge][field] == earlier
    assert f"{sentence_bge} was rebuilt after an earlier attempt of run {RUN_ID}" in caplog.text
    assert f"{field} was {earlier} and is now {now}" in caplog.text
    assert "One run cannot mix two builds" in caplog.text


@pytest.mark.parametrize(
    ("field", "change"),
    [
        ("query_prefix", lambda mp: mp.setattr(EMBEDDERS["voyage"], "query_prefix", "query: ")),
        ("input_type", lambda mp: mp.setattr(FakeVoyageTextEmbedder, "input_type", "document")),
    ],
    ids=["spec-changed", "component-changed"],
)
def test_resume_stops_before_the_probe_when_an_embedder_changed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rag: FakeBaselineRag,
    gemini: FakeGemini,
    caplog: pytest.LogCaptureFixture,
    field: str,
    change,
):
    rag.failing = {SENTENCE_BGE}
    assert exit_code(["--strategy", "sentence", "--limit", "2"]) == 1
    before = {path.name: path.read_bytes() for path in run_folder(tmp_path).iterdir()}
    rag.failing = set()
    rag.asked.clear()
    change(monkeypatch)

    assert exit_code(["--resume", RUN_ID]) == 1

    assert rag.asked == []
    assert len(gemini.calls) == 1  # the first attempt's probe only
    assert {path.name: path.read_bytes() for path in run_folder(tmp_path).iterdir()} == before
    assert "Embedder voyage changed since this run started" in caplog.text
    assert f"{field} was " in caplog.text
    assert "One run cannot mix two query embedders" in caplog.text


NOT_THIS_STAGE = "its run_meta.json does not record a generate_results run"


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (None, "No such file or directory"),
        ("{not json", "Expecting property name"),
        ("{}", NOT_THIS_STAGE),
        ("null", NOT_THIS_STAGE),
        ("[]", NOT_THIS_STAGE),
        ('{"stage": "stage3_index_chunks", "settings": {}}', NOT_THIS_STAGE),
        ('{"stage": "generate_results"}', "'settings'"),
        ('{"stage": "generate_results", "settings": {}, "question_files": null}', "NoneType"),
    ],
    ids=[
        "missing",
        "bad-json",
        "empty-object",
        "null",
        "list",
        "another-stage",
        "no-settings",
        "question-files-not-a-list",
    ],
)
def test_resume_refuses_a_run_folder_without_a_readable_run_meta(
    tmp_path: Path,
    rag: FakeBaselineRag,
    caplog: pytest.LogCaptureFixture,
    content: str | None,
    reason: str,
):
    run_folder(tmp_path).mkdir(parents=True)
    if content is not None:
        (run_folder(tmp_path) / "run_meta.json").write_text(content, encoding="utf-8")

    assert exit_code(["--resume", RUN_ID]) == 1

    assert rag.built == []
    assert f"Run {RUN_ID} cannot be resumed: " in caplog.text
    assert reason in caplog.text


@pytest.mark.parametrize(
    "option",
    [
        ["--strategy", "all"],
        ["--embedder", "bge"],
        ["--collection-prefix", "fastapi_docs"],
        ["--retrieve-only"],
        ["--limit", "2"],
        ["--retrieval", "dense"],
    ],
    ids=["strategy", "embedder", "collection-prefix", "retrieve-only", "limit", "retrieval"],
)
def test_resume_takes_no_other_option(tmp_path: Path, rag: FakeBaselineRag, option: list[str]):
    generate_results.main(["--strategy", "sentence", "--limit", "1"])
    rag.built.clear()

    assert exit_code(["--resume", RUN_ID, *option]) == 2
    assert exit_code([*option, "--resume", RUN_ID]) == 2

    assert rag.built == []


@pytest.mark.parametrize(
    "name",
    ["20261114_999999", f"../outputs/{RUN_ID}", f"{RUN_ID}/", "nested/run", ".", "..", "a_file"],
    ids=["no-such-run", "parent-path", "trailing-slash", "nested", "dot", "dot-dot", "a-file"],
)
def test_resume_accepts_only_the_name_of_a_folder_directly_under_outputs(
    tmp_path: Path, rag: FakeBaselineRag, name: str
):
    generate_results.main(["--strategy", "sentence", "--limit", "1"])
    (tmp_path / "outputs" / "nested" / "run").mkdir(parents=True)
    (tmp_path / "outputs" / "a_file").write_text("", encoding="utf-8")
    rag.built.clear()

    assert exit_code(["--resume", name]) == 2
    assert exit_code(["--resume", str(run_folder(tmp_path))]) == 2

    assert rag.built == []


def test_resume_exits_2_when_no_run_has_been_made(tmp_path: Path):
    assert exit_code(["--resume", RUN_ID]) == 2

    assert not (tmp_path / "outputs").exists()
