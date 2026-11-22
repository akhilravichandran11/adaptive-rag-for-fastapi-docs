"""
Prove that ``evaluation/question_set_report.py`` measures a question set as agreed (E16) and
that its command line prints the table, writes the review sheet and exits with the agreed
codes. Only the two agreed seams are tested: ``question_stats`` and ``main``.

The module reads local files and can reach no paid API, so this module installs no network
guard (C006).
"""

import json
import logging
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from evaluation import question_set_report
from evaluation.question_set_report import main, question_stats

PROJECT_ROOT = Path(__file__).resolve().parents[2]

QUESTIONS_DIR = PROJECT_ROOT / "evaluation" / "questions"
FIRST_STEPS = "tutorial/first-steps.md"
EVENTS = "advanced/events.md"
PAGES = {
    FIRST_STEPS: "Create a FastAPI application and declare a path operation with a decorator.",
    EVENTS: "Use lifespan events to run startup code before the application receives requests.",
    "reference/unused.md": "No question names this page.",
}

# t-01: 7 words, content words create, fastapi, application: all 3 on its page.
# t-02: 11 words, content words run, startup, code, lifespan, decorator: 4 on the first
#       gold page and "decorator" only on the second, so 5 of 5 across both.
# t-03: 6 words, content words fastapi, cache, between, requests: only "requests" is there.
QUESTIONS = [
    {
        "question_id": "t-01",
        "question": "How do I create a FastAPI application?",
        "reference_answer": "Make an instance of the FastAPI class.",
        "gold_sources": [FIRST_STEPS],
    },
    {
        "question_id": "t-02",
        "question": "how can I run startup code with lifespan and a decorator?",
        "reference_answer": "Pass a lifespan function when the application is made.",
        "gold_sources": [EVENTS, FIRST_STEPS],
    },
    {
        "question_id": "t-03",
        "question": "What does FastAPI cache between requests?",
        "reference_answer": "Nothing unless you add it.",
        "gold_sources": [EVENTS],
    },
]
# Seed 42 shuffles the six ids to x-01, t-02, t-03, x-02, t-01, x-03, so the two-page
# questions (t-02, x-01, x-02 in file order) come out of the shuffle in another order.
MORE_QUESTIONS = [
    {
        "question_id": "x-01",
        "question": "How do I declare a path operation?",
        "reference_answer": "Put a decorator on a function.",
        "gold_sources": [EVENTS, FIRST_STEPS],
    },
    {
        "question_id": "x-02",
        "question": "Which events run before the application receives requests?",
        "reference_answer": "The startup half of lifespan.",
        "gold_sources": [FIRST_STEPS, EVENTS],
    },
    {
        "question_id": "x-03",
        "question": "What is a decorator?",
        "reference_answer": "A function that wraps another one.",
        "gold_sources": [FIRST_STEPS],
    },
]
HEADER = (
    "| set | n | mean words | opens How do/can I | mean gold pages | multi-page "
    "| gold pages covered | echo mean | echo min | realistic | needs every page |"
)
SMALL_ROW = "| small | 3 | 8.0 | 66.7% | 1.33 | 33.3% | 2 | 75.0% | 25.0% |"

needs_corpus = pytest.mark.skipif(
    not (question_set_report.CORPUS_DIR / "manifest.json").exists(),
    reason="the normalized corpus (git-ignored) is not on this machine",
)


class FrozenDatetime(datetime):
    """``datetime`` with a clock the test sets. ``now(tz)`` is ``moment`` in that zone, and
    ``now()`` is naive local time, here five hours behind UTC and so on the day before."""

    moment = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return (cls.moment - timedelta(hours=5)).replace(tzinfo=None)
        return cls.moment.astimezone(tz)


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep the repo's review file, its outputs folder and the root logger out of every test.

    ``main`` configures the root logger with ``force=True``, which would remove pytest's
    log capture. The clock is frozen so a sheet folder's name is known: it is the UTC time,
    ``review_sheet_20260102_030405``, and local time would give ``20260101_220405``.
    """
    monkeypatch.setattr(logging, "basicConfig", lambda *args, **kwargs: None)
    monkeypatch.setattr(question_set_report, "REVIEW_PATH", tmp_path / "question_review.json")
    monkeypatch.setattr(question_set_report, "OUTPUTS_DIR", tmp_path / "outputs")
    monkeypatch.setattr(question_set_report, "datetime", FrozenDatetime)


@pytest.fixture
def corpus(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """``PAGES`` saved as the normalized corpus is: a manifest, and ``docs/`` files whose
    names are not their ``source_path``."""
    corpus_dir = tmp_path / "corpus"
    (corpus_dir / "docs").mkdir(parents=True)
    files = []
    for source_path, text in PAGES.items():
        output_filename = source_path.replace("/", "__")
        (corpus_dir / "docs" / output_filename).write_text(text, encoding="utf-8")
        files.append({"output_filename": output_filename, "source_path": source_path})
    (corpus_dir / "manifest.json").write_text(json.dumps({"files": files}), encoding="utf-8")
    monkeypatch.setattr(question_set_report, "CORPUS_DIR", corpus_dir)
    return corpus_dir


@pytest.fixture
def small(tmp_path: Path) -> Path:
    """``QUESTIONS`` saved in the generated set's shape, with keys beside ``questions``."""
    path = tmp_path / "small.json"
    path.write_text(json.dumps({"seed": 42, "questions": QUESTIONS}), encoding="utf-8")
    return path


# --- question_stats ---


def test_question_stats_returns_every_column_for_a_small_set():
    stats = question_stats(QUESTIONS, PAGES, {})

    assert stats == {
        "n": 3,
        "mean_words": 8.0,
        "how_opening_share": pytest.approx(2 / 3),
        "mean_gold_pages": pytest.approx(4 / 3),
        "multi_page_share": pytest.approx(1 / 3),
        "distinct_gold_pages": 2,
        "echo_mean": pytest.approx(0.75),
        "echo_min": 0.25,
        "realistic_share": None,
        "needs_all_gold_share": None,
    }


@pytest.mark.parametrize(
    ("question", "page", "echo"),
    [
        ("What is FastAPI?", "FASTAPI is a web framework.", 1.0),
        ("What is FastAPI?", "Starlette is a web framework.", 0.0),
        ("How do I use a model?", "Because users remodel it.", 0.0),
        ("What does response_model do?", "Pass response_model=Item to it.", 1.0),
        ("What is a response?", "Pass response_model=Item to it.", 0.0),
        ("Why OAuth2?", "OAuth has scopes.", 0.0),
        ("How do I set up an ID in the app?", "Set the app.", 1.0),
        ("Which header or cookie?", "A cookie is sent.", 0.5),
        # cookie, header, carry: 1 of 3 distinct words. By occurrence it would be 2 of 4.
        ("Does a cookie header carry a cookie?", "A cookie is sent.", pytest.approx(1 / 3)),
    ],
    ids=[
        "fastapi-counts-in-any-case",
        "fastapi-missing-is-not-skipped",
        "whole-tokens-not-substrings",
        "underscore-stays-in-a-token",
        "underscore-token-is-not-its-parts",
        "digits-stay-in-a-token",
        "short-and-function-words-dropped",
        "share-of-the-content-words",
        "a-repeated-word-counts-once",
    ],
)
def test_echo_is_the_share_of_content_words_found_in_the_gold_page(question, page, echo):
    entry = {"question_id": "t-01", "question": question, "gold_sources": ["page.md"]}

    stats = question_stats([entry], {"page.md": page}, {})

    assert stats["echo_mean"] == echo
    assert stats["echo_min"] == echo


def test_a_question_with_no_content_words_is_left_out_of_echo():
    bare = {"question_id": "t-04", "question": "What is it?", "gold_sources": [EVENTS]}

    with_bare = question_stats([*QUESTIONS, bare], PAGES, {})
    alone = question_stats([bare], PAGES, {})

    assert with_bare["n"] == 4
    assert with_bare["echo_mean"] == pytest.approx(0.75)
    assert with_bare["echo_min"] == 0.25
    assert alone["echo_mean"] is None
    assert alone["echo_min"] is None


def test_a_question_with_no_gold_pages_is_left_out_of_echo():
    pageless = {**QUESTIONS[0], "question_id": "t-05", "gold_sources": []}

    with_pageless = question_stats([*QUESTIONS, pageless], PAGES, {})
    alone = question_stats([pageless], PAGES, {})

    assert with_pageless["n"] == 4
    assert with_pageless["mean_gold_pages"] == 1.0  # it still counts everywhere else
    assert with_pageless["echo_mean"] == pytest.approx(0.75)
    assert with_pageless["echo_min"] == 0.25
    assert alone["echo_mean"] is None
    assert alone["echo_min"] is None


@pytest.mark.parametrize(
    ("question", "share"),
    [
        ("How do I add a route?", 1.0),
        ("HOW CAN I add a route?", 1.0),
        ("How do instances share state?", 0.0),
        ("How should I add a route?", 0.0),
        ("In FastAPI, how do I add a route?", 0.0),
    ],
)
def test_how_opening_share_counts_only_questions_that_open_how_do_i_or_how_can_i(question, share):
    entry = {"question_id": "t-01", "question": question, "gold_sources": [FIRST_STEPS]}

    assert question_stats([entry], PAGES, {})["how_opening_share"] == share


def test_review_shares_are_over_the_marked_questions_only():
    questions = [*QUESTIONS, *MORE_QUESTIONS]
    review = {
        "t-01": {"realistic": True, "needs_all_gold": True},  # one gold page: second mark unused
        "t-02": {"realistic": False, "needs_all_gold": True},
        "t-03": {"realistic": None, "needs_all_gold": None},
        "x-01": {"realistic": True, "needs_all_gold": None},  # two gold pages, second mark open
        "x-02": {"realistic": True, "needs_all_gold": False},
        "elsewhere-01": {"realistic": False, "needs_all_gold": False},  # another file's question
    }

    stats = question_stats(questions, PAGES, review)

    assert stats["realistic_share"] == 0.75  # t-01, x-01 and x-02 of the four marked
    # t-02 of the two marked. Over all three multi-page questions it would be a third.
    assert stats["needs_all_gold_share"] == 0.5


def test_a_review_with_no_mark_for_these_questions_leaves_both_shares_blank():
    review = {
        "t-01": {"needs_all_gold": True},  # no realistic key, and only one gold page
        "t-02": {"realistic": None, "needs_all_gold": None},
    }

    stats = question_stats(QUESTIONS, PAGES, review)

    assert stats["realistic_share"] is None
    assert stats["needs_all_gold_share"] is None


def test_question_stats_names_every_gold_source_that_has_no_page_text():
    questions = [
        {**QUESTIONS[0], "gold_sources": ["tutorial/missing.md", FIRST_STEPS]},
        {**QUESTIONS[2], "gold_sources": ["advanced/absent.md"]},
    ]

    with pytest.raises(ValueError, match="advanced/absent.md, tutorial/missing.md"):
        question_stats(questions, PAGES, {})


def test_question_stats_refuses_an_empty_question_list():
    with pytest.raises(ValueError, match="no questions"):
        question_stats([], PAGES, {})


# --- main ---


def test_main_prints_one_markdown_row_per_file_labelled_by_its_stem(
    corpus: Path, small: Path, tmp_path: Path, capsys: pytest.CaptureFixture
):
    # The first two questions again under ids of their own: an id may appear only once.
    first_two = tmp_path / "first_two.json"
    questions = [{**QUESTIONS[0], "question_id": "u-01"}, {**QUESTIONS[1], "question_id": "u-02"}]
    first_two.write_text(json.dumps({"questions": questions}), encoding="utf-8")

    main([str(small), str(first_two)])

    header, rule, *rows = capsys.readouterr().out.splitlines()
    assert header == HEADER
    assert rule == "|" + "---|" * 11
    assert rows == [
        SMALL_ROW + "  |  |",
        "| first_two | 2 | 9.0 | 100.0% | 1.50 | 50.0% | 2 | 100.0% | 100.0% |  |  |",
    ]
    assert all(row.count("|") == header.count("|") for row in rows)
    assert not (tmp_path / "outputs").exists()  # no sheet without --sheet


def test_main_prints_a_share_that_falls_on_half_a_percent_to_one_decimal_place(
    corpus: Path, tmp_path: Path, capsys: pytest.CaptureFixture
):
    # One "How do I" opening in eight questions is 12.5%, which whole percents print as 12%.
    eight = tmp_path / "eight.json"
    questions = [
        QUESTIONS[0],
        *({**QUESTIONS[2], "question_id": f"h-{number}"} for number in range(1, 8)),
    ]
    eight.write_text(json.dumps({"questions": questions}), encoding="utf-8")

    main([str(eight)])

    cells = [cell.strip() for cell in capsys.readouterr().out.splitlines()[2].strip("|").split("|")]
    assert cells[:2] == ["eight", "8"]
    assert cells[3] == "12.5%"


def test_main_reads_the_default_review_file_when_it_exists(
    corpus: Path, small: Path, capsys: pytest.CaptureFixture
):
    review = {
        "t-01": {"realistic": True, "needs_all_gold": None},
        "t-02": {"realistic": False, "needs_all_gold": True},
    }
    question_set_report.REVIEW_PATH.write_text(json.dumps(review), encoding="utf-8")

    main([str(small)])

    assert capsys.readouterr().out.splitlines()[2] == SMALL_ROW + " 50.0% | 100.0% |"


def test_main_reads_the_file_review_names_and_not_the_default(
    corpus: Path, small: Path, tmp_path: Path, capsys: pytest.CaptureFixture
):
    default = {"t-01": {"realistic": False}, "t-02": {"realistic": False}}
    question_set_report.REVIEW_PATH.write_text(json.dumps(default), encoding="utf-8")
    named = tmp_path / "marks.json"
    named.write_text(
        json.dumps({"t-01": {"realistic": True}, "t-02": {"needs_all_gold": False}}),
        encoding="utf-8",
    )

    main([str(small), "--review", str(named)])

    assert capsys.readouterr().out.splitlines()[2] == SMALL_ROW + " 100.0% | 0.0% |"


def test_main_logs_how_many_questions_of_each_file_carry_each_mark(
    corpus: Path,
    small: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
    caplog: pytest.LogCaptureFixture,
):
    more = tmp_path / "more.json"
    more.write_text(json.dumps({"questions": MORE_QUESTIONS}), encoding="utf-8")
    review = {
        "t-01": {"realistic": True, "needs_all_gold": True},  # one gold page: second mark unused
        "t-02": {"realistic": None, "needs_all_gold": False},
        "x-02": {"realistic": False},
    }
    question_set_report.REVIEW_PATH.write_text(json.dumps(review), encoding="utf-8")
    caplog.set_level(logging.INFO, logger=question_set_report.__name__)

    main([str(small), str(more)])

    assert [record.getMessage() for record in caplog.records] == [
        "small: realistic marks on 1 of 3 questions; "
        "needs_all_gold marks on 1 of 1 multi-page questions",
        "more: realistic marks on 1 of 3 questions; "
        "needs_all_gold marks on 0 of 2 multi-page questions",
    ]
    assert len(capsys.readouterr().out.splitlines()) == 4  # the table keeps its shape


def test_main_logs_no_mark_counts_when_no_review_file_is_in_use(
    corpus: Path, small: Path, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.INFO, logger=question_set_report.__name__)

    main([str(small)])

    assert caplog.records == []


@pytest.mark.parametrize(
    ("review", "named", "not_named"),
    [
        (["t-01"], [], ["t-01"]),
        ({"t-01": True, "t-02": {"realistic": True}}, ["t-01"], ["t-02"]),
        (
            {
                "t-01": {"realistic": "yes"},
                "t-02": {"realistic": True, "needs_all_gold": 1},
                "t-03": {"realistic": False, "needs_all_gold": None},
            },
            ["t-01", "t-02"],
            ["t-03"],
        ),
    ],
    ids=["not-an-object", "entry-not-an-object", "mark-not-true-false-or-null"],
)
def test_main_exits_1_and_names_the_review_file_and_every_bad_entry(
    review,
    named: list[str],
    not_named: list[str],
    corpus: Path,
    small: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
    caplog: pytest.LogCaptureFixture,
):
    marks = tmp_path / "marks.json"
    marks.write_text(json.dumps(review), encoding="utf-8")

    with pytest.raises(SystemExit) as exit_info:
        main([str(small), "--review", str(marks), "--sheet"])

    assert exit_info.value.code == 1
    [message] = [record.getMessage() for record in caplog.records]
    assert message.startswith(f"{marks}: ")
    assert all(question_id in message for question_id in named)
    assert not any(question_id in message for question_id in not_named)
    assert capsys.readouterr().out == ""
    assert not (tmp_path / "outputs").exists()


def test_main_exits_1_for_a_default_review_file_with_a_bad_mark(
    corpus: Path, small: Path, capsys: pytest.CaptureFixture, caplog: pytest.LogCaptureFixture
):
    review_path = question_set_report.REVIEW_PATH
    review_path.write_text(json.dumps({"t-02": {"needs_all_gold": "no"}}), encoding="utf-8")

    with pytest.raises(SystemExit) as exit_info:
        main([str(small)])

    assert exit_info.value.code == 1
    assert f"{review_path}: " in caplog.text
    assert "t-02" in caplog.text
    assert capsys.readouterr().out == ""


def test_main_exits_1_and_names_every_id_of_a_file_given_twice(
    corpus: Path,
    small: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
    caplog: pytest.LogCaptureFixture,
):
    with pytest.raises(SystemExit) as exit_info:
        main([str(small), str(small), "--sheet"])

    assert exit_info.value.code == 1
    [message] = [record.getMessage() for record in caplog.records]
    assert message.endswith(": t-01, t-02, t-03")
    assert capsys.readouterr().out == ""
    assert not (tmp_path / "outputs").exists()


def test_main_exits_1_and_names_only_the_id_two_files_share(
    corpus: Path,
    small: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
    caplog: pytest.LogCaptureFixture,
):
    overlap = tmp_path / "overlap.json"
    overlap.write_text(
        json.dumps({"questions": [MORE_QUESTIONS[2], QUESTIONS[1]]}), encoding="utf-8"
    )

    with pytest.raises(SystemExit) as exit_info:
        main([str(small), str(overlap)])

    assert exit_info.value.code == 1
    [message] = [record.getMessage() for record in caplog.records]
    assert "question_id" in message
    assert message.endswith(": t-02")
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    "content",
    [{"items": QUESTIONS}, QUESTIONS, {"questions": {"t-01": QUESTIONS[0]}}, {"questions": None}],
    ids=["no-questions-key", "a-top-level-list", "questions-is-an-object", "questions-is-null"],
)
def test_main_exits_1_and_names_a_file_that_has_no_questions_list(
    content,
    corpus: Path,
    small: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
    caplog: pytest.LogCaptureFixture,
):
    shapeless = tmp_path / "shapeless.json"
    shapeless.write_text(json.dumps(content), encoding="utf-8")

    with pytest.raises(SystemExit) as exit_info:
        main([str(small), str(shapeless), "--sheet"])

    assert exit_info.value.code == 1
    [message] = [record.getMessage() for record in caplog.records]
    assert message.startswith(f"{shapeless}: ")
    assert "'questions' list" in message
    assert capsys.readouterr().out == ""
    assert not (tmp_path / "outputs").exists()


def test_main_exits_1_and_names_an_unknown_gold_source(
    corpus: Path,
    small: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
    caplog: pytest.LogCaptureFixture,
):
    stray = tmp_path / "stray.json"
    question = {**QUESTIONS[0], "question_id": "s-01", "gold_sources": ["tutorial/missing.md"]}
    stray.write_text(json.dumps({"questions": [question]}), encoding="utf-8")

    with pytest.raises(SystemExit) as exit_info:
        main([str(small), str(stray), "--sheet"])

    assert exit_info.value.code == 1
    assert "tutorial/missing.md" in caplog.text
    assert str(stray) in caplog.text
    assert capsys.readouterr().out == ""  # no table for the files before it
    assert not (tmp_path / "outputs").exists()  # and no sheet


@pytest.mark.parametrize(
    "argv",
    [[], ["--sheet"], ["small.json", "--no-such-flag"], ["small.json", "--review"]],
    ids=["no-file", "sheet-without-a-file", "unknown-flag", "review-without-a-path"],
)
def test_main_exits_2_for_a_bad_command_line(argv: list[str], tmp_path: Path):
    with pytest.raises(SystemExit) as exit_info:
        main(argv)

    assert exit_info.value.code == 2
    assert not (tmp_path / "outputs").exists()


def test_main_exits_1_for_a_question_file_that_is_missing(
    corpus: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    with pytest.raises(SystemExit) as exit_info:
        main([str(tmp_path / "no_such.json")])

    assert exit_info.value.code == 1
    assert "no_such.json" in caplog.text


def test_main_exits_1_for_a_question_file_that_is_not_json(
    corpus: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")

    with pytest.raises(SystemExit) as exit_info:
        main([str(broken)])

    assert exit_info.value.code == 1
    assert "broken.json is not valid JSON" in caplog.text


def test_main_exits_1_for_a_question_file_with_no_questions(
    corpus: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"questions": []}), encoding="utf-8")

    with pytest.raises(SystemExit) as exit_info:
        main([str(empty)])

    assert exit_info.value.code == 1
    assert "empty.json" in caplog.text


def test_main_exits_1_for_a_review_path_that_is_missing(
    corpus: Path, small: Path, tmp_path: Path, capsys: pytest.CaptureFixture
):
    with pytest.raises(SystemExit) as exit_info:
        main([str(small), "--review", str(tmp_path / "no_marks.json")])

    assert exit_info.value.code == 1
    assert capsys.readouterr().out == ""


def test_main_exits_1_without_the_corpus(
    small: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    monkeypatch.setattr(question_set_report, "CORPUS_DIR", tmp_path / "no_corpus")

    with pytest.raises(SystemExit) as exit_info:
        main([str(small)])

    assert exit_info.value.code == 1
    assert "manifest.json" in caplog.text


def test_sheet_writes_the_three_files_and_still_prints_the_table(
    corpus: Path, small: Path, tmp_path: Path, capsys: pytest.CaptureFixture
):
    more = tmp_path / "more.json"
    more.write_text(json.dumps({"questions": MORE_QUESTIONS}), encoding="utf-8")
    every_question = [*QUESTIONS, *MORE_QUESTIONS]
    file_order = [question["question_id"] for question in every_question]
    shuffled = list(file_order)
    random.Random(42).shuffle(shuffled)

    main([str(small), str(more), "--sheet"])

    sheet_dir = tmp_path / "outputs" / "review_sheet_20260102_030405"
    assert sorted(path.name for path in sheet_dir.iterdir()) == [
        "key.json",
        "multi_page.md",
        "sheet.md",
    ]
    assert [path.name for path in (tmp_path / "outputs").iterdir()] == [sheet_dir.name]
    assert len(capsys.readouterr().out.splitlines()) == 4

    key = json.loads((sheet_dir / "key.json").read_text(encoding="utf-8"))
    assert list(key) == ["q01", "q02", "q03", "q04", "q05", "q06"]
    assert list(key.values()) == shuffled
    assert shuffled != file_order  # the seed really reorders these six

    # sheet.md: each question's text under its neutral id, in key order, and nothing that
    # tells the sets apart.
    sheet = (sheet_dir / "sheet.md").read_text(encoding="utf-8")
    text_by_id = {question["question_id"]: question["question"] for question in every_question}
    positions = [
        sheet.index(f"## {neutral_id}\n\n{text_by_id[question_id]}\n")
        for neutral_id, question_id in key.items()
    ]
    assert positions == sorted(positions)
    for question in every_question:
        assert question["question_id"] not in sheet
        assert question["reference_answer"] not in sheet
    assert not any(source_path in sheet for source_path in PAGES)

    # multi_page.md: only the two-page questions, by question_id and in file order (not the
    # sheet's shuffled order), with pages and answer.
    multi_page = (sheet_dir / "multi_page.md").read_text(encoding="utf-8")
    two_page_ids = ["t-02", "x-01", "x-02"]
    headings = [line[3:] for line in multi_page.splitlines() if line.startswith("## ")]
    assert headings == two_page_ids
    assert [question_id for question_id in shuffled if question_id in two_page_ids] != headings
    assert f"{QUESTIONS[1]['question']}\n\n- {EVENTS}\n- {FIRST_STEPS}\n" in multi_page
    assert f"{MORE_QUESTIONS[1]['question']}\n\n- {FIRST_STEPS}\n- {EVENTS}\n" in multi_page
    for question in (QUESTIONS[1], MORE_QUESTIONS[0], MORE_QUESTIONS[1]):
        assert f"Reference answer: {question['reference_answer']}\n" in multi_page
    for single_page_id in ("t-01", "t-03", "x-03"):
        assert single_page_id not in multi_page


def test_sheet_is_the_same_on_a_second_run(
    corpus: Path, small: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    main([str(small), "--sheet"])
    monkeypatch.setattr(FrozenDatetime, "moment", datetime(2026, 1, 2, 3, 4, 6, tzinfo=UTC))
    main([str(small), "--sheet"])

    first = tmp_path / "outputs" / "review_sheet_20260102_030405"
    second = tmp_path / "outputs" / "review_sheet_20260102_030406"
    for name in ("sheet.md", "key.json", "multi_page.md"):
        assert (first / name).read_bytes() == (second / name).read_bytes()


def test_sheet_refuses_a_folder_that_already_exists(
    corpus: Path,
    small: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
    caplog: pytest.LogCaptureFixture,
):
    sheet_dir = tmp_path / "outputs" / "review_sheet_20260102_030405"
    sheet_dir.mkdir(parents=True)

    with pytest.raises(SystemExit) as exit_info:
        main([str(small), "--sheet"])

    assert exit_info.value.code == 1
    assert str(sheet_dir) in caplog.text
    assert list(sheet_dir.iterdir()) == []  # the earlier folder is left as it was
    assert capsys.readouterr().out == ""


@needs_corpus
def test_main_prints_a_two_row_table_for_the_two_committed_question_sets(
    capsys: pytest.CaptureFixture,
):
    main([str(QUESTIONS_DIR / "handwritten.json"), str(QUESTIONS_DIR / "synthetic.json")])

    header, rule, *rows = capsys.readouterr().out.splitlines()
    assert len(rows) == 2
    assert rows[0].startswith("| handwritten | 10 | ")
    assert rows[1].startswith("| synthetic | 40 | ")
    for row in rows:
        cells = [cell.strip() for cell in row.strip("|").split("|")]
        assert len(cells) == 11
        assert all(cells[:9])  # every measured column is filled
        assert cells[9:] == ["", ""]  # no review file here, so both review columns are blank
