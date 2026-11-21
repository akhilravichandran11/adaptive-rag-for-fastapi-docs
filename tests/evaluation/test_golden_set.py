"""
Prove that ``evaluation/golden_set.py`` writes the blind picking sheet from saved results, that
``--build`` turns a marked sheet into ``golden.json`` or refuses it writing nothing (E22), and
that the three golden-set models hold their bounds (E21). Only the agreed seams are tested:
``main`` and the models' ``model_validate``.

The module reads local files and can reach no paid API, so this module installs no network
guard (C006).
"""

import json
import logging
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from evaluation import golden_set
from evaluation.golden_set import GOLDEN_IDS, main
from evaluation.schemas import GoldenEntry, GoldenSet, ReferenceChunk

INDEX_RUN = "20250101_000000"
DENSE_RUN = "20260101_000001"
HYBRID_RUN = "20260101_000002"
SHEET_NAME = "golden_sheet_20260102_030405"
EARLIER_GOLDEN = b'{"an earlier": "golden.json"}\n'
# Dense retrieves chunks 0-3 of a.md and hybrid 2-3 of a.md and 0-1 of b.md: six distinct
# candidates per question, two of them found by both.
DENSE_CHUNKS = [("a.md", 0), ("a.md", 1), ("a.md", 2), ("a.md", 3)]
HYBRID_CHUNKS = [("a.md", 2), ("b.md", 0), ("a.md", 3), ("b.md", 1)]
QUESTIONS = {
    question_id: {
        "question_id": question_id,
        "question": f"What does {question_id} ask?",
        "reference_answer": f"The original answer to {question_id}.",
        "gold_sources": ["a.md"],
        "origin": "handwritten" if question_id.startswith("hw-") else "synthetic",
    }
    for question_id in (*GOLDEN_IDS, "hw-11")
}


class FrozenDatetime(datetime):
    """``datetime`` with a clock the test sets, so a sheet folder's name is known."""

    moment = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return (cls.moment - timedelta(hours=5)).replace(tzinfo=None)
        return cls.moment.astimezone(tz)


def chunk_text(source_path: str, chunk_index: int) -> str:
    return f"# Heading of {source_path}\n\nThe text of chunk {chunk_index} of {source_path}."


def results_line(system: str, question_id: str, chunks: list, index_run: str = INDEX_RUN) -> str:
    """A results line as the systems save it, with its chunks ranked in the order given."""
    return json.dumps(
        {
            "system": system,
            "question_id": question_id,
            "question": QUESTIONS[question_id]["question"],
            "index_run": index_run,
            "chunks": [
                {
                    "rank": rank,
                    "score": 1.0 / rank,
                    "content": chunk_text(source_path, chunk_index),
                    "meta": {
                        "source_path": source_path,
                        "strategy": "markdown_ast",
                        "chunk_index": chunk_index,
                        "index_run": index_run,
                    },
                }
                for rank, (source_path, chunk_index) in enumerate(chunks, start=1)
            ],
            "answer": None,
        }
    )


def write_results(path: Path, *lines: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point the module's question folder, outputs folder and golden file into ``tmp_path``,
    freeze its clock, and keep ``main``'s ``force=True`` logging setup off pytest's capture."""
    questions_dir = tmp_path / "questions"
    questions_dir.mkdir()
    for origin in ("handwritten", "synthetic"):
        in_set = [question for question in QUESTIONS.values() if question["origin"] == origin]
        (questions_dir / f"{origin}.json").write_text(
            json.dumps({"questions": in_set}), encoding="utf-8"
        )
    monkeypatch.setattr(logging, "basicConfig", lambda *args, **kwargs: None)
    monkeypatch.setattr(golden_set, "QUESTIONS_DIR", questions_dir)
    monkeypatch.setattr(golden_set, "OUTPUTS_DIR", tmp_path / "outputs")
    monkeypatch.setattr(golden_set, "GOLDEN_PATH", tmp_path / "golden.json")
    monkeypatch.setattr(golden_set, "datetime", FrozenDatetime)


@pytest.fixture
def runs(tmp_path: Path) -> list[Path]:
    """A dense and a hybrid results file, each with a line for every golden question and one
    for a question outside the golden set."""
    dense = write_results(
        tmp_path / DENSE_RUN / "results.jsonl",
        *(results_line("dense", question_id, DENSE_CHUNKS) for question_id in QUESTIONS),
    )
    hybrid = write_results(
        tmp_path / HYBRID_RUN / "results.jsonl",
        *(results_line("hybrid", question_id, HYBRID_CHUNKS) for question_id in QUESTIONS),
    )
    return [dense, hybrid]


@pytest.fixture
def sheet_dir(runs: list[Path], tmp_path: Path) -> Path:
    """A sheet written from ``runs`` and marked on every page: picks 3 and 1, and a rationale.
    An earlier ``golden.json`` sits beside it, so a refused build can be seen to leave it."""
    assert main([str(path) for path in runs]) == 0
    sheet_dir = tmp_path / "outputs" / SHEET_NAME
    for question_id in GOLDEN_IDS:
        page = sheet_dir / f"{question_id}.md"
        text = page.read_text(encoding="utf-8")
        text = text.replace("\npick:\n", "\npick: 3, 1\n")
        text = text.replace("\nrationale:\n", f"\nrationale: Both state {question_id}.\n")
        page.write_text(text, encoding="utf-8")
    (tmp_path / "golden.json").write_bytes(EARLIER_GOLDEN)
    return sheet_dir


# --- the sheet ---


def test_the_sheet_holds_one_blind_page_per_golden_question_and_a_key(
    runs: list[Path], tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.INFO, logger="evaluation.golden_set")

    assert main([str(path) for path in runs]) == 0

    sheet_dir = tmp_path / "outputs" / SHEET_NAME
    assert f"Golden sheet written to {sheet_dir}" in caplog.text
    assert sorted(path.name for path in sheet_dir.iterdir()) == sorted(
        [*(f"{question_id}.md" for question_id in GOLDEN_IDS), "key.json"]
    )
    key = json.loads((sheet_dir / "key.json").read_text(encoding="utf-8"))
    assert key["index_run"] == INDEX_RUN
    assert key["source_runs"] == [DENSE_RUN, HYBRID_RUN]
    assert list(key["questions"]) == list(GOLDEN_IDS)  # hw-11 is not a golden question

    # The union of both runs, each identity once, numbered by a shuffle seeded with the id.
    union = sorted(set(DENSE_CHUNKS) | set(HYBRID_CHUNKS))
    expected = [
        {
            "source_path": source_path,
            "strategy": "markdown_ast",
            "chunk_index": chunk_index,
            "content": chunk_text(source_path, chunk_index),
        }
        for source_path, chunk_index in union
    ]
    random.Random("hw-01").shuffle(expected)
    assert key["questions"]["hw-01"] == {str(n): chunk for n, chunk in enumerate(expected, 1)}

    page = (sheet_dir / "hw-01.md").read_text(encoding="utf-8")
    assert page.startswith("# hw-01\n\nWhat does hw-01 ask?\n\n")
    assert "\n## Reference answer\n\nThe original answer to hw-01.\n\n## Pick\n" in page
    assert "\npick:\nrationale:\n" in page
    for number, chunk in key["questions"]["hw-01"].items():
        assert f"\n### {number}: `{chunk['source_path']}`\n\n{chunk['content']}\n" in page
    assert page.count("\n### ") == 6
    for hint in ("dense", "hybrid", "rank", "score", INDEX_RUN):
        assert hint not in page


def test_the_sheet_numbers_the_chunks_the_same_way_on_a_second_run_and_with_the_files_swapped(
    runs: list[Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    assert main([str(path) for path in runs]) == 0
    monkeypatch.setattr(FrozenDatetime, "moment", datetime(2026, 1, 2, 3, 4, 6, tzinfo=UTC))
    assert main([str(path) for path in runs]) == 0
    monkeypatch.setattr(FrozenDatetime, "moment", datetime(2026, 1, 2, 3, 4, 7, tzinfo=UTC))
    assert main([str(path) for path in reversed(runs)]) == 0

    first, second, swapped = (
        tmp_path / "outputs" / f"golden_sheet_20260102_03040{second}" for second in (5, 6, 7)
    )
    for path in first.iterdir():
        assert (second / path.name).read_bytes() == path.read_bytes()
        if path.suffix == ".md":
            assert (swapped / path.name).read_bytes() == path.read_bytes()
    swapped_key = json.loads((swapped / "key.json").read_text(encoding="utf-8"))
    first_key = json.loads((first / "key.json").read_text(encoding="utf-8"))
    assert swapped_key["questions"] == first_key["questions"]
    assert swapped_key["source_runs"] == [HYBRID_RUN, DENSE_RUN]


def test_the_sheet_refuses_results_lines_from_two_index_runs(
    runs: list[Path], tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    rebuilt = write_results(
        tmp_path / "20260101_000003" / "results.jsonl",
        results_line("rebuilt", "hw-03", DENSE_CHUNKS, index_run="20250202_000000"),
    )

    assert main([*(str(path) for path in runs), str(rebuilt)]) == 1

    assert f"{rebuilt}:1: index_run '20250202_000000', but {runs[0]}:1 has '{INDEX_RUN}'" in (
        caplog.text
    )
    assert not (tmp_path / "outputs").exists()


def test_the_sheet_ignores_the_index_run_of_a_line_with_no_chunks(runs: list[Path], tmp_path: Path):
    empty = write_results(
        tmp_path / "20260101_000003" / "results.jsonl",
        json.dumps({"system": "failed", "question_id": "hw-03", "index_run": None, "chunks": []}),
    )

    assert main([*(str(path) for path in runs), str(empty)]) == 0


def test_the_sheet_refuses_a_golden_question_with_no_line_in_any_file(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    results = write_results(
        tmp_path / DENSE_RUN / "results.jsonl",
        *(results_line("dense", qid, DENSE_CHUNKS) for qid in GOLDEN_IDS if qid != "syn-24"),
    )

    assert main([str(results)]) == 1

    assert f"golden question_ids with no line in {results}: syn-24" in caplog.text
    assert not (tmp_path / "outputs").exists()


def test_the_sheet_refuses_one_identity_with_two_texts(
    runs: list[Path], tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    line = json.loads(results_line("rebuilt", "hw-05", [("a.md", 1)]))
    line["chunks"][0]["content"] = "Another text under the same identity."
    other = write_results(tmp_path / "20260101_000003" / "results.jsonl", json.dumps(line))

    assert main([*(str(path) for path in runs), str(other)]) == 1

    assert f"{other}:1: question_id 'hw-05': chunk ('a.md', 'markdown_ast', 1)" in caplog.text
    assert not (tmp_path / "outputs").exists()


def test_the_sheet_refuses_one_identity_with_two_texts_under_two_questions(
    runs: list[Path], tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    first = json.loads(results_line("rebuilt", "hw-01", [("c.md", 0)]))
    second = json.loads(results_line("rebuilt", "hw-02", [("c.md", 0)]))
    second["chunks"][0]["content"] = "Another text under the same identity."
    other = write_results(
        tmp_path / "20260101_000003" / "results.jsonl", json.dumps(first), json.dumps(second)
    )

    assert main([*(str(path) for path in runs), str(other)]) == 1

    assert f"{other}:2: question_id 'hw-02': chunk ('c.md', 'markdown_ast', 0)" in caplog.text
    assert not (tmp_path / "outputs").exists()


@pytest.mark.parametrize(
    "chunks",
    [
        [{"content": "x", "meta": {"source_path": "a.md", "strategy": "s", "chunk_index": None}}],
        [{"content": 7, "meta": {"source_path": "a.md", "strategy": "s", "chunk_index": 9}}],
        [{"content": "x", "meta": {"source_path": "a.md", "chunk_index": 9}}],
        ["not a chunk"],
        {"rank": 1, "content": "x"},
    ],
    ids=["chunk-index-null", "content-not-text", "no-strategy", "chunk-not-object", "not-a-list"],
)
def test_the_sheet_refuses_a_malformed_chunk_before_making_its_folder(
    chunks: object, runs: list[Path], tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    line = {"system": "rebuilt", "question_id": "hw-04", "index_run": INDEX_RUN, "chunks": chunks}
    other = write_results(tmp_path / "20260101_000003" / "results.jsonl", json.dumps(line))

    assert main([*(str(path) for path in runs), str(other)]) == 1

    assert f"{other}:1: question_id 'hw-04': " in caplog.text
    assert not (tmp_path / "outputs").exists()


def test_the_sheet_refuses_a_golden_question_in_no_question_file(
    runs: list[Path], tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    synthetic = tmp_path / "questions" / "synthetic.json"
    in_set = [question for question in QUESTIONS.values() if question["question_id"] != "syn-13"]
    in_set = [question for question in in_set if question["origin"] == "synthetic"]
    synthetic.write_text(json.dumps({"questions": in_set}), encoding="utf-8")

    assert main([str(path) for path in runs]) == 1

    assert "golden question_ids in no question file: syn-13" in caplog.text
    assert not (tmp_path / "outputs").exists()


def test_the_sheet_refuses_a_folder_that_already_exists(
    runs: list[Path], tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    existing = tmp_path / "outputs" / SHEET_NAME
    existing.mkdir(parents=True)

    assert main([str(path) for path in runs]) == 1

    assert f"Sheet folder {existing} already exists" in caplog.text
    assert list(existing.iterdir()) == []


def test_the_sheet_returns_1_for_a_missing_results_file(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    missing = tmp_path / "missing.jsonl"

    assert main([str(missing)]) == 1

    assert str(missing) in caplog.text


# --- --build ---


def test_build_writes_the_marked_sheet_to_golden_json(sheet_dir: Path, tmp_path: Path):
    page = sheet_dir / "hw-02.md"
    edited = "  An edited answer, its indent kept.\n\n### A heading typed into it\n\nLast line.  "
    text = page.read_text(encoding="utf-8")
    text = text.replace("The original answer to hw-02.", f"\n\n{edited}\n   \n")
    page.write_text(text, encoding="utf-8")
    key = json.loads((sheet_dir / "key.json").read_text(encoding="utf-8"))

    assert main(["--build", str(sheet_dir)]) == 0

    expected = {
        "index_run": INDEX_RUN,
        "source_runs": [DENSE_RUN, HYBRID_RUN],
        "entries": [
            {
                "question_id": question_id,
                "reference_answer": (
                    edited if question_id == "hw-02" else f"The original answer to {question_id}."
                ),
                "reference_chunks": [key["questions"][question_id][n] for n in ("3", "1")],
                "rationale": f"Both state {question_id}.",
            }
            for question_id in GOLDEN_IDS
        ],
    }
    written = (tmp_path / "golden.json").read_text(encoding="utf-8")
    assert written == json.dumps(expected, indent=2) + "\n"
    assert GoldenSet.model_validate_json(written).entries[1].reference_answer == edited


def test_build_reads_a_pick_wrapped_onto_a_second_line(sheet_dir: Path, tmp_path: Path):
    page = sheet_dir / "hw-03.md"
    page.write_text(
        page.read_text("utf-8").replace("\npick: 3, 1\n", "\npick: 3,\n 1, 5\n"), "utf-8"
    )
    key = json.loads((sheet_dir / "key.json").read_text(encoding="utf-8"))

    assert main(["--build", str(sheet_dir)]) == 0

    golden = GoldenSet.model_validate_json((tmp_path / "golden.json").read_text("utf-8"))
    assert [chunk.model_dump() for chunk in golden.entries[2].reference_chunks] == [
        key["questions"]["hw-03"][number] for number in ("3", "1", "5")
    ]


@pytest.mark.parametrize(
    ("pick", "rationale", "logged"),
    [
        ("3, 99", "Fine.", "pick 99 is not a candidate number"),
        ("2 2", "Fine.", "a pick is repeated"),
        ("", "Fine.", "reference_chunks"),
        ("1, 2, 3, 4, 5, 6", "Fine.", "reference_chunks"),
        ("1", "", "rationale"),
        ("1 and 2", "Fine.", "'pick:' holds something other than numbers"),
        ("1\npick: 4", "Fine.", "'pick:' holds something other than numbers"),
    ],
    ids=[
        "unknown-pick",
        "repeated-pick",
        "no-pick",
        "six-picks",
        "empty-rationale",
        "not-a-number",
        "a-second-pick-line",
    ],
)
def test_build_refuses_a_bad_page_naming_it_and_writes_nothing(
    pick: str,
    rationale: str,
    logged: str,
    sheet_dir: Path,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
):
    page = sheet_dir / "syn-20.md"
    text = page.read_text(encoding="utf-8")
    text = text.replace("\npick: 3, 1\n", f"\npick: {pick}\n")
    text = text.replace("\nrationale: Both state syn-20.\n", f"\nrationale: {rationale}\n")
    page.write_text(text, encoding="utf-8")

    assert main(["--build", str(sheet_dir)]) == 1

    assert f"{page}: " in caplog.text
    assert logged in caplog.text
    assert (tmp_path / "golden.json").read_bytes() == EARLIER_GOLDEN


def test_build_refuses_a_page_whose_markers_are_gone(
    sheet_dir: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    page = sheet_dir / "hw-07.md"
    page.write_text(page.read_text(encoding="utf-8").replace("\npick:", "\npicked:"), "utf-8")

    assert main(["--build", str(sheet_dir)]) == 1

    assert f"{page}: the '## Reference answer', '## Pick', 'pick:'" in caplog.text
    assert (tmp_path / "golden.json").read_bytes() == EARLIER_GOLDEN


def test_build_refuses_a_key_that_is_not_a_sheet_key(
    sheet_dir: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    key_path = sheet_dir / "key.json"
    key_path.write_text(json.dumps({"index_run": INDEX_RUN}), encoding="utf-8")

    assert main(["--build", str(sheet_dir)]) == 1

    assert f"{key_path}: not a sheet key" in caplog.text
    assert (tmp_path / "golden.json").read_bytes() == EARLIER_GOLDEN


def test_build_refuses_a_key_without_every_golden_question(
    sheet_dir: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    key_path = sheet_dir / "key.json"
    key = json.loads(key_path.read_text(encoding="utf-8"))
    del key["questions"]["syn-26"]
    key_path.write_text(json.dumps(key), encoding="utf-8")

    assert main(["--build", str(sheet_dir)]) == 1

    assert f"{key_path}: the key's questions must be an object" in caplog.text
    assert "missing ['syn-26']" in caplog.text
    assert (tmp_path / "golden.json").read_bytes() == EARLIER_GOLDEN


def test_build_refuses_a_key_whose_candidates_are_not_an_object(
    sheet_dir: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    key_path = sheet_dir / "key.json"
    key = json.loads(key_path.read_text(encoding="utf-8"))
    key["questions"]["hw-01"] = list(key["questions"]["hw-01"].values())
    key_path.write_text(json.dumps(key), encoding="utf-8")

    assert main(["--build", str(sheet_dir)]) == 1

    assert f"{key_path}: the candidates of hw-01 are not a JSON object" in caplog.text
    assert (tmp_path / "golden.json").read_bytes() == EARLIER_GOLDEN


@pytest.mark.parametrize("name", ["hw-01.md", "key.json"])
def test_build_names_a_page_or_key_that_is_not_utf8(
    name: str, sheet_dir: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    (sheet_dir / name).write_bytes(b"pick: \xff\n")

    assert main(["--build", str(sheet_dir)]) == 1

    assert f"{sheet_dir / name}: not UTF-8" in caplog.text
    assert (tmp_path / "golden.json").read_bytes() == EARLIER_GOLDEN


# --- the command line ---


@pytest.mark.parametrize(
    "argv",
    [[], ["results.jsonl", "--build", "sheet"], ["--build"], ["results.jsonl", "--no-such-flag"]],
    ids=["neither", "both", "build-without-a-folder", "unknown-flag"],
)
def test_main_returns_2_for_a_bad_command_line(
    argv: list[str], tmp_path: Path, capsys: pytest.CaptureFixture
):
    assert main(argv) == 2

    assert capsys.readouterr().out == ""
    assert not (tmp_path / "outputs").exists()


def test_main_returns_0_after_help(capsys: pytest.CaptureFixture):
    assert main(["--help"]) == 0

    assert "--build SHEET_DIR" in capsys.readouterr().out


# --- the models ---


REFERENCE_CHUNKS = [
    {"source_path": "a.md", "strategy": "markdown_ast", "chunk_index": index, "content": "Text."}
    for index in range(6)
]
ENTRY = {
    "question_id": "hw-01",
    "reference_answer": "An answer.",
    "reference_chunks": REFERENCE_CHUNKS[:1],
    "rationale": "It says so.",
}


@pytest.mark.parametrize("count", [1, 5])
def test_an_entry_takes_1_to_5_reference_chunks(count: int):
    entry = GoldenEntry.model_validate(ENTRY | {"reference_chunks": REFERENCE_CHUNKS[:count]})

    assert entry.reference_chunks == [
        ReferenceChunk.model_validate(chunk) for chunk in REFERENCE_CHUNKS[:count]
    ]


@pytest.mark.parametrize(
    "change",
    [
        {"reference_chunks": []},
        {"reference_chunks": REFERENCE_CHUNKS},
        {"reference_answer": ""},
        {"rationale": ""},
        {"reference_chunks": [{"source_path": "a.md", "strategy": "markdown_ast"}]},
    ],
    ids=["no-chunks", "six-chunks", "empty-answer", "empty-rationale", "chunk-missing-fields"],
)
def test_an_entry_is_refused_outside_its_bounds(change: dict):
    with pytest.raises(ValidationError):
        GoldenEntry.model_validate(ENTRY | change)


def test_a_golden_set_needs_at_least_one_entry():
    golden = {"index_run": INDEX_RUN, "source_runs": [DENSE_RUN], "entries": [ENTRY]}

    assert GoldenSet.model_validate(golden).entries[0].question_id == "hw-01"
    with pytest.raises(ValidationError):
        GoldenSet.model_validate(golden | {"entries": []})
