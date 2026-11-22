"""
Prove that ``EvalQuestion`` holds the agreed fields, and that each question file
loads as ``EvalQuestion``, names only manifest pages and covers every bucket.

Nothing here can reach a paid API, so this module installs no network guard (C006).
"""

import json
from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError

from evaluation.schemas import EvalQuestion

PROJECT_ROOT = Path(__file__).resolve().parents[2]

QUESTIONS_DIR = PROJECT_ROOT / "evaluation" / "questions"
QUESTION_FILES = ["handwritten.json", "synthetic.json"]
MANIFEST = PROJECT_ROOT / "data" / "fastapi" / "normalized" / "github-docs" / "v1" / "manifest.json"
BUCKETS = {"tutorial", "advanced", "reference", "how-to", "deployment", "top-level"}

VALID = {
    "question_id": "hw-01",
    "question": "How do I send a 404?",
    "reference_answer": "Raise an HTTPException with status_code=404.",
    "gold_sources": ["tutorial/handling-errors.md"],
    "origin": "handwritten",
}

needs_corpus = pytest.mark.skipif(
    not MANIFEST.exists(), reason="the normalized corpus (git-ignored) is not on this machine"
)


def load_questions(name: str) -> list[dict]:
    return json.loads((QUESTIONS_DIR / name).read_text(encoding="utf-8"))["questions"]


def test_eval_question_has_the_five_required_fields_and_an_optional_paraphrase():
    assert set(EvalQuestion.model_fields) == set(VALID) | {"paraphrase"}
    assert EvalQuestion.model_validate(VALID).model_dump() == {**VALID, "paraphrase": None}

    reworded = {**VALID, "paraphrase": "What do I raise for a missing item?"}
    assert EvalQuestion.model_validate(reworded).model_dump() == reworded


@pytest.mark.parametrize("field", list(VALID))
def test_eval_question_refuses_a_missing_field(field):
    entry = {key: value for key, value in VALID.items() if key != field}

    with pytest.raises(ValidationError, match=field):
        EvalQuestion.model_validate(entry)


@pytest.mark.parametrize(
    ("field", "value"), [("origin", "llm"), ("gold_sources", [])], ids=["origin", "gold_sources"]
)
def test_eval_question_refuses_an_unknown_origin_and_empty_gold_sources(field, value):
    with pytest.raises(ValidationError, match=field):
        EvalQuestion.model_validate({**VALID, field: value})


def test_handwritten_file_holds_hw_01_to_hw_10_and_nothing_else():
    data = json.loads((QUESTIONS_DIR / "handwritten.json").read_text(encoding="utf-8"))

    assert list(data) == ["questions"]
    assert [q["question_id"] for q in data["questions"]] == [f"hw-{n:02d}" for n in range(1, 11)]


def test_synthetic_file_holds_syn_01_to_syn_40_each_with_one_gold_page():
    data = json.loads((QUESTIONS_DIR / "synthetic.json").read_text(encoding="utf-8"))

    assert list(data) == ["source_run", "model", "seed", "questions"]
    assert [q["question_id"] for q in data["questions"]] == [f"syn-{n:02d}" for n in range(1, 41)]
    assert all(len(q["gold_sources"]) == 1 for q in data["questions"])


def test_no_question_id_is_in_two_files():
    ids = [q["question_id"] for name in QUESTION_FILES for q in load_questions(name)]

    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("name", QUESTION_FILES)
def test_every_entry_validates_with_no_unknown_keys_a_unique_id_and_the_file_origin(name):
    entries = load_questions(name)
    questions = [EvalQuestion.model_validate(entry) for entry in entries]
    ids = [q.question_id for q in questions]

    # Pydantic drops unknown keys silently, so a misspelt "paraphrase" would vanish
    assert {key for entry in entries for key in entry} - set(EvalQuestion.model_fields) == set()

    assert len(ids) == len(set(ids))
    assert {q.origin for q in questions} == {name.removesuffix(".json")}


@needs_corpus
@pytest.mark.parametrize("name", QUESTION_FILES)
def test_gold_sources_are_manifest_pages_covering_every_bucket(name):
    files = json.loads(MANIFEST.read_text(encoding="utf-8"))["files"]
    bucket_of = {f["source_path"]: f["bucket"] for f in files}
    gold = {source for entry in load_questions(name) for source in entry["gold_sources"]}

    assert gold - bucket_of.keys() == set()
    assert {bucket_of[source] for source in gold} == BUCKETS


@needs_corpus
def test_synthetic_gold_pages_follow_the_sampled_bucket_counts():
    files = json.loads(MANIFEST.read_text(encoding="utf-8"))["files"]
    bucket_of = {f["source_path"]: f["bucket"] for f in files}
    counts = Counter(bucket_of[q["gold_sources"][0]] for q in load_questions("synthetic.json"))

    assert counts == {
        "tutorial": 16,
        "advanced": 11,
        "reference": 4,
        "how-to": 4,
        "deployment": 3,
        "top-level": 2,
    }
