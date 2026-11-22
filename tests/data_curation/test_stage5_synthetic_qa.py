"""
Offline tests for curation stage 5: the seeded per-bucket sample, and what ``main``
writes or refuses, with ``chat_structured`` faked where the stage looks it up.

The stage could call OpenAI, so the guard below refuses every network call (C006).
"""

import importlib
import json
import os
import random
import socket
import subprocess
import sys
from collections import Counter
from pathlib import Path

import dotenv
import openai
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_CURATION = PROJECT_ROOT / "data_curation"
REAL_ANALYSIS = DATA_CURATION / "outputs" / "20260925_032632" / "content_analysis.json"

D54_COUNTS = {
    "tutorial": 16,
    "advanced": 11,
    "reference": 4,
    "how-to": 4,
    "deployment": 3,
    "top-level": 2,
}
SHARED_QUESTION = "How do I implement OAuth2 authentication in FastAPI?"


@pytest.fixture
def stage5(monkeypatch):
    """Import stage 5 with network access and client construction forbidden."""

    def refuse(*args, **kwargs):
        raise AssertionError("offline test attempted API or network activity")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(openai.OpenAI, "__init__", refuse)
    monkeypatch.setattr(openai.AsyncOpenAI, "__init__", refuse)
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.syspath_prepend(str(DATA_CURATION))

    module = importlib.import_module("stage5_synthetic_qa")
    monkeypatch.setattr(module, "chat_structured", refuse)
    monkeypatch.setattr(module, "load_dotenv", lambda *args, **kwargs: False)
    # configure_logging replaces the root handlers, which would hide caplog's
    monkeypatch.setattr(module, "configure_logging", lambda: None)
    return module


def make_corpus() -> tuple[list[dict], list[dict]]:
    """Every bucket has spare docs and one quarantined doc; top-level has no spare.

    Every doc's first example question is the same, as five real docs share one.
    """
    analyses, manifest_files = [], []
    for bucket, count in D54_COUNTS.items():
        spare = 0 if bucket == "top-level" else 4
        for n in range(count + spare + 1):
            source_path = f"{bucket}/page-{n:02d}.md"
            output_filename = source_path.replace("/", "__")
            manifest_files.append(
                {"source_path": source_path, "output_filename": output_filename, "bucket": bucket}
            )
            analyses.append(
                {
                    "source_path": source_path,
                    "output_filename": output_filename,
                    "example_questions": [
                        SHARED_QUESTION,
                        f"What does {source_path} teach?",
                        f"Why read {source_path}?",
                    ],
                    "suspected_prompt_injection": n == 0,
                }
            )
    return analyses, manifest_files


def as_rows(pairs: list[tuple[dict, str]]) -> list[list[str]]:
    return [[record["source_path"], question] for record, question in pairs]


def test_sample_takes_forty_distinct_docs_at_the_bucket_counts(stage5):
    analyses, manifest_files = make_corpus()
    bucket_of = {entry["source_path"]: entry["bucket"] for entry in manifest_files}

    paths = [record["source_path"] for record, _ in stage5.sample_docs(analyses, manifest_files)]

    assert len(set(paths)) == 40
    assert Counter(bucket_of[path] for path in paths) == D54_COUNTS
    assert paths == sorted(paths)


def test_sample_never_takes_a_quarantined_doc(stage5):
    analyses, manifest_files = make_corpus()

    pairs = stage5.sample_docs(analyses, manifest_files)
    paths = [record["source_path"] for record, _ in pairs]

    assert not any(record["suspected_prompt_injection"] for record, _ in pairs)
    # top-level has two usable docs for its two places, so both must be taken
    assert [path for path in paths if path.startswith("top-level/")] == [
        "top-level/page-01.md",
        "top-level/page-02.md",
    ]


def test_sample_uses_no_question_twice(stage5):
    analyses, manifest_files = make_corpus()

    rows = as_rows(stage5.sample_docs(analyses, manifest_files))

    assert len({question for _, question in rows}) == 40
    # the first doc in source_path order keeps the shared question; the rest take their second
    assert rows[0][1] == SHARED_QUESTION
    assert all(question == f"What does {path} teach?" for path, question in rows[1:])


def test_sample_refuses_a_doc_whose_questions_are_all_taken(stage5):
    analyses, manifest_files = make_corpus()
    [doc] = [record for record in analyses if record["source_path"] == "top-level/page-02.md"]
    doc["example_questions"] = [SHARED_QUESTION]

    with pytest.raises(ValueError, match="top-level/page-02.md"):
        stage5.sample_docs(analyses, manifest_files)


def test_sample_does_not_depend_on_input_order(stage5):
    analyses, manifest_files = make_corpus()
    expected = as_rows(stage5.sample_docs(analyses, manifest_files))

    rng = random.Random(7)
    rng.shuffle(analyses)
    rng.shuffle(manifest_files)

    assert as_rows(stage5.sample_docs(analyses, manifest_files)) == expected


def test_sample_is_the_same_in_fresh_processes(stage5, tmp_path):
    analyses, manifest_files = make_corpus()
    corpus = tmp_path / "corpus.json"
    corpus.write_text(json.dumps([analyses, manifest_files]), encoding="utf-8")
    script = (
        "import json, sys; sys.path.insert(0, sys.argv[1]); import stage5_synthetic_qa as stage5; "
        "analyses, files = json.loads(open(sys.argv[2]).read()); "
        "print(json.dumps([[r['source_path'], q] for r, q in stage5.sample_docs(analyses, files)]))"
    )

    runs = []
    for hash_seed in ("1", "2"):
        result = subprocess.run(
            [sys.executable, "-c", script, str(DATA_CURATION), str(corpus)],
            env={**os.environ, "PYTHONHASHSEED": hash_seed},
            capture_output=True,
            text=True,
            check=True,
        )
        runs.append(json.loads(result.stdout))

    assert runs[0] == runs[1] == as_rows(stage5.sample_docs(analyses, manifest_files))


@pytest.mark.skipif(
    not REAL_ANALYSIS.exists(), reason="the curation run (git-ignored) is not on this machine"
)
def test_the_real_run_samples_forty_distinct_questions_at_the_bucket_counts(stage5):
    if not stage5.MANIFEST_PATH.exists():
        pytest.skip("the normalized corpus (git-ignored) is not on this machine")
    analyses = json.loads(REAL_ANALYSIS.read_text(encoding="utf-8"))
    manifest_files = json.loads(stage5.MANIFEST_PATH.read_text(encoding="utf-8"))["files"]
    bucket_of = {entry["source_path"]: entry["bucket"] for entry in manifest_files}

    rows = as_rows(stage5.sample_docs(analyses, manifest_files))

    assert Counter(bucket_of[path] for path, _ in rows) == D54_COUNTS
    assert len({path for path, _ in rows}) == 40
    assert len({question for _, question in rows}) == 40


@pytest.fixture
def run_dir(stage5, tmp_path, monkeypatch):
    """A run folder holding the fake corpus's content analysis, with its docs on disk."""
    analyses, manifest_files = make_corpus()
    corpus = tmp_path / "corpus"
    (corpus / "docs").mkdir(parents=True)
    (corpus / "manifest.json").write_text(json.dumps({"files": manifest_files}), encoding="utf-8")
    for entry in manifest_files:
        page = corpus / "docs" / entry["output_filename"]
        page.write_text(f"Text of {entry['source_path']}.", encoding="utf-8")

    run = tmp_path / "outputs" / "run-1"
    run.mkdir(parents=True)
    (run / "content_analysis.json").write_text(json.dumps(analyses), encoding="utf-8")

    monkeypatch.setattr(stage5, "MANIFEST_PATH", corpus / "manifest.json")
    monkeypatch.setattr(stage5, "resolve_run_dir", lambda run_id: tmp_path / "outputs" / run_id)
    return run


def test_main_answers_each_doc_once_and_writes_the_forty(stage5, run_dir, monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "model-under-test")
    calls = []

    async def fake_chat_structured(prompt, schema, **kwargs):
        calls.append((prompt, schema, kwargs))
        return schema(answer=f"Answer {len(calls)}.")

    monkeypatch.setattr(stage5, "chat_structured", fake_chat_structured)

    stage5.main(["--run-id", "run-1"])

    data = json.loads((run_dir / "synthetic_qa.json").read_text(encoding="utf-8"))
    entries = data["questions"]
    assert list(data) == ["source_run", "model", "seed", "questions"]
    assert (data["source_run"], data["model"], data["seed"]) == (
        "run-1",
        "model-under-test",
        stage5.SEED,
    )
    assert [entry["question_id"] for entry in entries] == [f"syn-{n:02d}" for n in range(1, 41)]

    assert len(calls) == 40
    gold = []
    for number, (entry, (prompt, schema, kwargs)) in enumerate(zip(entries, calls, strict=True), 1):
        question = stage5.EvalQuestion.model_validate(entry)
        assert question.origin == "synthetic"
        assert question.reference_answer == f"Answer {number}."
        [source] = question.gold_sources
        gold.append(source)
        assert schema is stage5.SyntheticAnswer
        assert kwargs == {"temperature": 0.0, "seed": stage5.SEED}
        assert f"Text of {source}." in prompt
        assert question.question in prompt
    assert gold == sorted(set(gold))


def test_main_exits_1_naming_a_missing_content_analysis(stage5, run_dir, tmp_path, caplog):
    with pytest.raises(SystemExit) as exit_info:
        stage5.main(["--run-id", "no-such-run"])

    assert exit_info.value.code == 1
    assert str(tmp_path / "outputs" / "no-such-run" / "content_analysis.json") in caplog.text


def test_main_exits_2_without_a_run_id(stage5):
    with pytest.raises(SystemExit) as exit_info:
        stage5.main([])

    assert exit_info.value.code == 2


def test_main_exits_1_and_writes_nothing_when_a_call_fails(stage5, run_dir, monkeypatch):
    calls = []

    async def failing_chat_structured(prompt, schema, **kwargs):
        calls.append(prompt)
        if len(calls) == 3:
            raise RuntimeError("still failing after chat_structured's retries")
        return schema(answer="An answer.")

    monkeypatch.setattr(stage5, "chat_structured", failing_chat_structured)

    with pytest.raises(SystemExit) as exit_info:
        stage5.main(["--run-id", "run-1"])

    assert exit_info.value.code == 1
    assert len(calls) == 3
    assert not (run_dir / "synthetic_qa.json").exists()
