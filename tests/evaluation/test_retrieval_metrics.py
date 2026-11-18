"""
Prove that ``evaluation/retrieval_metrics.py`` scores one question's ranked chunks by the
stage 1 rules (E9), and that it joins saved results files to the question sets and averages
them per system and set (E6, E7, E10). Only the agreed seams are tested: ``score_chunks``,
``score_files`` and ``main``.

The scorer can reach no paid API, so this module installs no network guard (C006).
"""

import json
import logging
import re
from pathlib import Path

import pytest

from evaluation import retrieval_metrics
from evaluation.retrieval_metrics import main, score_chunks, score_files
from evaluation.schemas import EvalQuestion

GOLD = "advanced/response-headers.md"
OTHER = "tutorial/handling-errors.md"
SECOND_GOLD = "reference/apirouter.md"


def ranked(*paths: str) -> list[dict]:
    """Chunks at ranks 1, 2, ... from their source paths; ``"web"`` makes a web chunk."""
    chunks = []
    for rank, path in enumerate(paths, start=1):
        if path == "web":
            meta = {"source": "web", "url": "https://example.com/page"}
        else:
            meta = {"source_path": path, "bucket": "advanced"}
        chunks.append({"rank": rank, "score": 1.0 / rank, "content": "...", "meta": meta})
    return chunks


def test_the_worked_example_with_gold_at_ranks_3_9_10():
    chunks = ranked(OTHER, OTHER, GOLD, OTHER, OTHER, OTHER, OTHER, OTHER, GOLD, GOLD)

    scores = score_chunks(chunks, [GOLD])

    assert scores == {
        "recall@1": 0.0,
        "recall@3": 1.0,
        "recall@5": 1.0,
        "recall@10": 1.0,
        "reciprocal_rank": pytest.approx(1 / 3),
    }


def test_the_worked_example_with_gold_at_ranks_1_2_4():
    chunks = ranked(GOLD, GOLD, OTHER, GOLD, OTHER, OTHER, OTHER, OTHER, OTHER, OTHER)

    scores = score_chunks(chunks, [GOLD])

    assert scores["recall@1"] == 1.0
    assert scores["reciprocal_rank"] == 1.0


def test_two_gold_files_found_at_ranks_2_and_7_give_partial_recall():
    chunks = ranked(OTHER, GOLD, OTHER, OTHER, OTHER, OTHER, SECOND_GOLD)

    scores = score_chunks(chunks, [GOLD, SECOND_GOLD])

    assert [scores[f"recall@{k}"] for k in (1, 3, 5, 10)] == [0.0, 0.5, 0.5, 1.0]
    assert scores["reciprocal_rank"] == 0.5


def test_a_gold_file_hit_by_several_chunks_counts_once():
    scores = score_chunks(ranked(GOLD, GOLD, GOLD), [GOLD, SECOND_GOLD])

    assert scores["recall@3"] == 0.5


def test_a_web_chunk_is_never_gold_and_pushes_gold_down_a_rank():
    scores = score_chunks(ranked("web", GOLD, OTHER), [GOLD])

    assert scores["recall@1"] == 0.0
    assert scores["reciprocal_rank"] == 0.5


def test_no_chunks_scores_zero_everywhere():
    assert set(score_chunks([], [GOLD]).values()) == {0.0}


def test_the_rank_field_decides_not_the_list_order():
    chunks = ranked(GOLD, OTHER, OTHER)
    chunks[0]["rank"], chunks[2]["rank"] = 3, 1

    scores = score_chunks(chunks, [GOLD])

    assert scores["recall@1"] == 0.0
    assert scores["reciprocal_rank"] == pytest.approx(1 / 3)


def test_a_chunk_neither_web_nor_with_a_source_path_is_refused():
    chunks = ranked(GOLD, OTHER)
    del chunks[1]["meta"]["source_path"]

    with pytest.raises(ValueError, match="rank 2"):
        score_chunks(chunks, [GOLD])


@pytest.mark.parametrize("source_path", [None, ""])
def test_an_empty_source_path_is_refused_like_a_missing_one(source_path):
    chunks = ranked(GOLD, OTHER)
    chunks[1]["meta"]["source_path"] = source_path

    with pytest.raises(ValueError, match="rank 2"):
        score_chunks(chunks, [GOLD])


@pytest.mark.parametrize("bad_rank", [0, -1, "1", None, 1.0])
def test_a_rank_that_is_not_a_whole_number_from_1_is_refused(bad_rank):
    chunks = ranked(OTHER, GOLD)
    chunks[1]["rank"] = bad_rank

    with pytest.raises(ValueError, match="whole numbers from 1"):
        score_chunks(chunks, [GOLD])


def test_a_web_chunk_with_rank_0_is_refused_too():
    chunks = ranked("web", GOLD)
    chunks[0]["rank"] = 0

    with pytest.raises(ValueError, match="rank 0"):
        score_chunks(chunks, [GOLD])


def test_a_question_without_gold_sources_is_refused():
    with pytest.raises(ValueError, match="gold_sources"):
        score_chunks(ranked(GOLD), [])


def question(question_id: str, origin: str, *gold_sources: str) -> EvalQuestion:
    return EvalQuestion(
        question_id=question_id,
        question=f"What does {question_id} ask?",
        reference_answer="An answer.",
        gold_sources=list(gold_sources),
        origin=origin,
    )


HANDWRITTEN = [
    question("hw-01", "handwritten", GOLD),
    question("hw-02", "handwritten", GOLD, SECOND_GOLD),
]
SYNTHETIC = [question("syn-01", "synthetic", OTHER)]


def results_line(system: str, question_id: str, *paths: str) -> dict:
    """One line in the saved results shape, with the keys the scorer must ignore."""
    return {
        "system": system,
        "question_id": question_id,
        "question": f"What does {question_id} ask?",
        "collection": "fastapi_docs__markdown_ast__voyage",
        "top_k": 10,
        "chunks": ranked(*paths),
        "answer": "An answer.",
        "usage": None,
    }


def write_results(path: Path, *lines: dict | str) -> Path:
    """Write one JSON object per line; a ``str`` is written as it is, for a broken line."""
    text = "".join((line if isinstance(line, str) else json.dumps(line)) + "\n" for line in lines)
    path.write_text(text, encoding="utf-8")
    return path


def write_questions(path: Path, questions: list[EvalQuestion], **other_keys: object) -> Path:
    """Write a question file in the shape of ``evaluation/questions/*.json``."""
    data = {**other_keys, "questions": [question.model_dump() for question in questions]}
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the root logger out of every test.

    ``main`` configures the root logger with ``force=True``, which would remove pytest's
    log capture.
    """
    monkeypatch.setattr(logging, "basicConfig", lambda *args, **kwargs: None)


def test_each_column_is_the_mean_over_the_questions_of_the_set(tmp_path: Path):
    results = write_results(
        tmp_path / "results.jsonl",
        results_line("baseline", "hw-01", GOLD, OTHER),
        results_line("baseline", "hw-02", OTHER, GOLD),  # one of its two gold files, at rank 2
    )

    rows = score_files([results], HANDWRITTEN)

    assert rows == [
        {
            "system": "baseline",
            "set": "handwritten",
            "n": 2,
            "recall@1": 0.5,
            "recall@3": 0.75,
            "recall@5": 0.75,
            "recall@10": 0.75,
            "MRR": 0.75,
            "no chunks": 0,
        }
    ]


def test_a_missing_question_and_a_line_without_chunks_score_zero_and_are_counted(tmp_path: Path):
    questions = [question(f"hw-0{n}", "handwritten", GOLD) for n in (1, 2, 3, 4, 5)]
    absent = results_line("baseline", "hw-04")
    del absent["chunks"]
    results = write_results(
        tmp_path / "results.jsonl",
        results_line("baseline", "hw-01", GOLD),
        # hw-02 has no line at all
        results_line("baseline", "hw-03"),  # "chunks": []
        absent,
        {**results_line("baseline", "hw-05"), "chunks": None},
    )

    [row] = score_files([results], questions)

    assert row["n"] == 5
    assert row["no chunks"] == 4
    assert [row[column] for column in ("recall@1", "recall@10", "MRR")] == [0.2, 0.2, 0.2]


def test_a_line_whose_chunks_miss_every_gold_file_scores_zero_but_is_not_no_chunks(tmp_path: Path):
    results = write_results(
        tmp_path / "results.jsonl",
        results_line("baseline", "hw-01", OTHER, "web"),
        results_line("baseline", "hw-02", OTHER),
    )

    [row] = score_files([results], HANDWRITTEN)

    assert row["no chunks"] == 0
    assert [row[column] for column in ("recall@10", "MRR")] == [0.0, 0.0]


def test_two_sets_give_two_rows_per_system_including_a_set_the_system_never_ran(tmp_path: Path):
    both_sets = write_results(
        tmp_path / "hybrid.jsonl",
        results_line("hybrid", "syn-01", OTHER),
        results_line("hybrid", "hw-01", GOLD),
    )
    handwritten_only = write_results(
        tmp_path / "baseline.jsonl", results_line("baseline", "hw-01", GOLD)
    )

    rows = score_files([both_sets, handwritten_only], HANDWRITTEN + SYNTHETIC)

    assert [(row["system"], row["set"], row["n"], row["no chunks"]) for row in rows] == [
        ("baseline", "handwritten", 2, 1),
        ("baseline", "synthetic", 1, 1),  # never ran: every question is a no-chunks question
        ("hybrid", "handwritten", 2, 1),
        ("hybrid", "synthetic", 1, 0),
    ]
    assert [(row["recall@1"], row["MRR"]) for row in rows] == [
        (0.5, 0.5),
        (0.0, 0.0),
        (0.5, 0.5),
        (1.0, 1.0),
    ]


def test_only_chunks_is_scored_not_first_pass_chunks(tmp_path: Path):
    line = {**results_line("crag", "hw-01", OTHER, GOLD), "first_pass_chunks": ranked(GOLD)}
    results = write_results(tmp_path / "results.jsonl", line)

    [row] = score_files([results], HANDWRITTEN[:1])

    assert row["recall@1"] == 0.0
    assert row["MRR"] == 0.5


def test_a_unicode_line_separator_inside_a_chunk_does_not_split_the_line(tmp_path: Path):
    line = results_line("baseline", "hw-01", GOLD)
    line["chunks"][0]["content"] = "one\u2028two\u0085three"
    results = tmp_path / "results.jsonl"
    results.write_text(json.dumps(line, ensure_ascii=False) + "\n", encoding="utf-8")

    [row] = score_files([results], HANDWRITTEN[:1])

    assert row["MRR"] == 1.0


@pytest.mark.parametrize(
    ("bad_line", "names"),
    [
        ("{not json", "not valid JSON"),
        ("[1, 2]", "not a JSON object"),
        ({"question_id": "hw-02", "chunks": []}, "got system None and question_id 'hw-02'"),
        (
            {"system": "", "question_id": "hw-02", "chunks": []},
            "got system '' and question_id 'hw-02'",
        ),
        ({"system": "baseline", "chunks": []}, "got system 'baseline' and question_id None"),
        (
            {"system": "baseline", "question_id": ["hw-02"], "chunks": []},
            "got system 'baseline' and question_id ['hw-02']",
        ),
        (results_line("baseline", "hw-99"), "question_id 'hw-99' is in no question file"),
        (results_line("baseline", "hw-01", OTHER), "second line for system 'baseline' and"),
    ],
    ids=[
        "invalid-json",
        "not-an-object",
        "no-system",
        "empty-system",
        "no-question-id",
        "question-id-not-a-string",
        "question-in-no-file",
        "second-line-for-system-and-question",
    ],
)
def test_a_bad_results_line_is_refused_naming_the_file_the_line_and_the_id(
    bad_line: dict | str, names: str, tmp_path: Path
):
    results = write_results(
        tmp_path / "results.jsonl",
        results_line("baseline", "hw-01", GOLD),
        "",  # a blank line is skipped, and still counted
        bad_line,
    )

    with pytest.raises(ValueError, match=re.escape(f"{results}:3: ")) as error:
        score_files([results], HANDWRITTEN)

    assert names in str(error.value)


@pytest.mark.parametrize(
    "bad_chunk",
    [
        {"rank": 1},
        {"meta": {"source_path": GOLD}},
        {"rank": 0, "meta": {"source_path": GOLD}},
        {"rank": 1, "meta": {"bucket": "advanced"}},
        {"rank": 1, "meta": None},
        "not a chunk",
    ],
    ids=["no-meta", "no-rank", "rank-0", "no-source-path", "meta-null", "not-an-object"],
)
def test_a_chunk_that_cannot_be_scored_is_refused_naming_the_file_the_line_and_the_id(
    bad_chunk: dict | str, tmp_path: Path
):
    results = write_results(
        tmp_path / "results.jsonl",
        results_line("baseline", "hw-01", GOLD),
        {**results_line("baseline", "hw-02"), "chunks": [bad_chunk]},
    )

    with pytest.raises(ValueError, match=re.escape(f"{results}:2: question_id 'hw-02'")):
        score_files([results], HANDWRITTEN)


@pytest.mark.parametrize(
    "chunks",
    [0, "", False, {}, 5, "a string", {"rank": 1, "meta": {"source_path": GOLD}}],
    ids=["zero", "empty-string", "false", "empty-object", "number", "string", "one-chunk-object"],
)
def test_chunks_that_is_not_a_list_is_refused_and_not_counted_as_no_chunks(
    chunks: object, tmp_path: Path
):
    results = write_results(
        tmp_path / "results.jsonl",
        results_line("baseline", "hw-01", GOLD),
        {**results_line("baseline", "hw-02"), "chunks": chunks},
    )

    expected = f"{results}:2: question_id 'hw-02': chunks must be a list, null or absent"
    with pytest.raises(ValueError, match=re.escape(expected)):
        score_files([results], HANDWRITTEN)


# Ends in an accented e: two bytes in UTF-8, and a byte that is not UTF-8 in Latin-1.
ACCENTED = '{"system": "baseline", "question_id": "hw-02", "note": "caf' + chr(0xE9)


@pytest.mark.parametrize(
    "tail",
    [ACCENTED.encode("utf-8")[:-1], (ACCENTED + '"}').encode("latin-1")],
    ids=["cut-inside-a-character", "latin-1"],
)
def test_a_results_file_that_is_not_utf_8_is_refused_naming_the_file(tail: bytes, tmp_path: Path):
    results = tmp_path / "results.jsonl"
    first_line = json.dumps(results_line("baseline", "hw-01", GOLD)).encode("utf-8")
    results.write_bytes(first_line + b"\n" + tail)

    with pytest.raises(ValueError, match=re.escape(f"{results}: not a UTF-8 results file")):
        score_files([results], HANDWRITTEN)


def test_a_bad_line_in_one_file_stops_the_run_before_a_later_file_is_read(tmp_path: Path):
    first = write_results(tmp_path / "first.jsonl", results_line("baseline", "hw-99", GOLD))
    missing = tmp_path / "missing.jsonl"

    with pytest.raises(ValueError, match=re.escape(f"{first}:1: ")):
        score_files([first, missing], HANDWRITTEN)


def test_a_second_line_for_a_system_and_question_in_another_file_is_refused(tmp_path: Path):
    first = write_results(tmp_path / "first.jsonl", results_line("baseline", "hw-01", GOLD))
    second = write_results(
        tmp_path / "second.jsonl",
        results_line("hybrid", "hw-01", GOLD),  # another system may answer the same question
        results_line("baseline", "hw-01", OTHER),
    )

    with pytest.raises(ValueError, match=re.escape(f"{second}:2: ")) as error:
        score_files([first, second], HANDWRITTEN)

    assert "'baseline'" in str(error.value)
    assert "'hw-01'" in str(error.value)


def test_main_prints_one_markdown_table_returns_0_and_writes_no_file(
    tmp_path: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
):
    handwritten = write_questions(tmp_path / "handwritten.json", HANDWRITTEN)
    synthetic = write_questions(
        tmp_path / "synthetic.json", SYNTHETIC, source_run="20261110_000000", model="m", seed=7
    )
    results = write_results(
        tmp_path / "results.jsonl",
        results_line("baseline", "hw-01", GOLD, OTHER),
        results_line("baseline", "hw-02", OTHER, OTHER, GOLD),
    )
    monkeypatch.chdir(tmp_path)
    files_before = sorted(tmp_path.iterdir())

    exit_code = main([str(results), "--questions", str(handwritten), str(synthetic)])

    assert exit_code == 0
    assert capsys.readouterr().out.splitlines() == [
        "| system | set | n | recall@1 | recall@3 | recall@5 | recall@10 | MRR | no chunks |",
        "|---|---|---|---|---|---|---|---|---|",
        "| baseline | handwritten | 2 | 0.50 | 0.75 | 0.75 | 0.75 | 0.67 | 0 |",
        "| baseline | synthetic | 1 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 1 |",
    ]
    assert sorted(tmp_path.iterdir()) == files_before


def test_main_reads_every_json_file_in_the_questions_folder_by_default(
    tmp_path: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
):
    folder = tmp_path / "questions"
    folder.mkdir()
    write_questions(folder / "handwritten.json", HANDWRITTEN)
    write_questions(folder / "synthetic.json", SYNTHETIC)
    (folder / "notes.md").write_text("not a question file", encoding="utf-8")
    monkeypatch.setattr(retrieval_metrics, "QUESTIONS_DIR", folder)
    results = write_results(tmp_path / "results.jsonl", results_line("baseline", "hw-01", GOLD))

    assert main([str(results)]) == 0

    rows = capsys.readouterr().out.splitlines()[2:]
    assert [row.split(" | ")[1:3] for row in rows] == [["handwritten", "2"], ["synthetic", "1"]]


def test_main_prints_only_the_header_for_a_results_file_with_no_lines(
    tmp_path: Path, capsys: pytest.CaptureFixture
):
    questions = write_questions(tmp_path / "handwritten.json", HANDWRITTEN)
    results = write_results(tmp_path / "results.jsonl", "", "")

    assert main([str(results), "--questions", str(questions)]) == 0

    assert len(capsys.readouterr().out.splitlines()) == 2


def test_main_returns_1_and_logs_the_file_line_and_id_of_a_bad_results_line(
    tmp_path: Path, capsys: pytest.CaptureFixture, caplog: pytest.LogCaptureFixture
):
    questions = write_questions(tmp_path / "handwritten.json", HANDWRITTEN)
    results = write_results(
        tmp_path / "results.jsonl",
        results_line("baseline", "hw-01", GOLD),
        results_line("baseline", "hw-99", GOLD),
    )

    assert main([str(results), "--questions", str(questions)]) == 1

    assert f"{results}:2: question_id 'hw-99'" in caplog.text
    assert capsys.readouterr().out == ""  # no table for the lines before it


@pytest.mark.parametrize("missing", ["results", "questions"])
def test_main_returns_1_and_logs_a_missing_file(
    missing: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
    caplog: pytest.LogCaptureFixture,
):
    paths = {"results": tmp_path / "results.jsonl", "questions": tmp_path / "handwritten.json"}
    write_results(paths["results"], results_line("baseline", "hw-01", GOLD))
    write_questions(paths["questions"], HANDWRITTEN)
    paths[missing].unlink()

    assert main([str(paths["results"]), "--questions", str(paths["questions"])]) == 1

    assert str(paths[missing]) in caplog.text
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    "text",
    [
        "{not json",
        json.dumps([HANDWRITTEN[0].model_dump()]),
        json.dumps({"entries": [HANDWRITTEN[0].model_dump()]}),
        json.dumps({"questions": [{**HANDWRITTEN[0].model_dump(), "origin": "llm"}]}),
        json.dumps({"questions": [{"question_id": "hw-01"}]}),
    ],
    ids=["invalid-json", "a-bare-list", "no-questions-key", "unknown-origin", "missing-fields"],
)
def test_main_returns_1_and_logs_an_invalid_question_file(
    text: str, tmp_path: Path, capsys: pytest.CaptureFixture, caplog: pytest.LogCaptureFixture
):
    broken = tmp_path / "broken.json"
    broken.write_text(text, encoding="utf-8")
    results = write_results(tmp_path / "results.jsonl", results_line("baseline", "hw-01", GOLD))

    assert main([str(results), "--questions", str(broken)]) == 1

    assert f"{broken}: not a question file" in caplog.text
    assert capsys.readouterr().out == ""


def test_main_returns_1_when_a_question_id_is_in_two_question_files(
    tmp_path: Path, capsys: pytest.CaptureFixture, caplog: pytest.LogCaptureFixture
):
    first = write_questions(tmp_path / "handwritten.json", HANDWRITTEN)
    second = write_questions(tmp_path / "more.json", HANDWRITTEN[1:])
    results = write_results(tmp_path / "results.jsonl", results_line("baseline", "hw-01", GOLD))

    assert main([str(results), "--questions", str(first), str(second)]) == 1

    assert f"{second}: question_id 'hw-02' is already in {first}" in caplog.text
    assert capsys.readouterr().out == ""


def test_main_returns_1_when_a_question_id_is_repeated_inside_one_question_file(
    tmp_path: Path, capsys: pytest.CaptureFixture, caplog: pytest.LogCaptureFixture
):
    questions = write_questions(tmp_path / "handwritten.json", [*HANDWRITTEN, HANDWRITTEN[0]])
    results = write_results(tmp_path / "results.jsonl", results_line("baseline", "hw-01", GOLD))

    assert main([str(results), "--questions", str(questions)]) == 1

    assert f"{questions}: question_id 'hw-01' is already in {questions}" in caplog.text
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--questions", "handwritten.json"],
        ["--questions", "handwritten.json", "results.jsonl"],
        ["results.jsonl", "--no-such-flag"],
        ["results.jsonl", "--questions"],
    ],
    ids=[
        "no-arguments",
        "no-results-file",
        "questions-before-the-results-file",
        "unknown-flag",
        "questions-without-a-file",
    ],
)
def test_main_returns_2_for_a_bad_command_line(argv: list[str], capsys: pytest.CaptureFixture):
    assert main(argv) == 2

    assert capsys.readouterr().out == ""


def test_main_returns_0_after_help_and_its_usage_puts_the_results_files_first(
    capsys: pytest.CaptureFixture,
):
    assert main(["--help"]) == 0

    out = capsys.readouterr().out
    assert "--questions" in out
    assert out.splitlines()[0].endswith(
        " [-h] RESULTS.jsonl [RESULTS.jsonl ...] [--questions FILE ...]"
    )
