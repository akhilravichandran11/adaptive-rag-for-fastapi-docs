"""File-level retrieval metrics (recall@k and reciprocal rank) for saved results, with no API calls.

Why: evaluation/DECISIONS.md, "2026-11-14 — Retrieval is scored at file level, from saved results".
"""

import argparse
import json
import logging
from pathlib import Path
from typing import Any

from evaluation.schemas import EvalQuestion

PROJECT_ROOT = Path(__file__).resolve().parents[1]

logger = logging.getLogger(__name__)

KS = (1, 3, 5, 10)
COLUMNS = ("system", "set", "n", *(f"recall@{k}" for k in KS), "MRR", "no chunks")
QUESTIONS_DIR = PROJECT_ROOT / "evaluation" / "questions"


def score_chunks(chunks: list[dict[str, Any]], gold_sources: list[str]) -> dict[str, float]:
    if not gold_sources:
        raise ValueError("score_chunks: the question has no gold_sources")
    gold = set(gold_sources)
    best_rank: dict[str, int] = {}
    for chunk in chunks:
        rank, meta = chunk["rank"], chunk["meta"]
        if not isinstance(rank, int) or rank < 1:
            raise ValueError(
                f"score_chunks: a chunk has rank {rank!r}; ranks are whole numbers from 1"
            )
        if meta.get("source") == "web":
            continue
        path = meta.get("source_path")
        if not path:
            raise ValueError(
                f"score_chunks: the chunk at rank {rank} has no meta.source_path "
                "and is not a web chunk (meta.source == 'web')"
            )
        if path in gold:
            best_rank[path] = min(rank, best_rank.get(path, rank))

    scores = {f"recall@{k}": sum(rank <= k for rank in best_rank.values()) / len(gold) for k in KS}
    scores["reciprocal_rank"] = 1 / min(best_rank.values()) if best_rank else 0.0
    return scores


def _parse_line(where: str, line: str) -> tuple[str, str, Any]:
    try:
        record = json.loads(line)
    except ValueError as exc:
        raise ValueError(f"{where}: not valid JSON: {exc}") from None
    if not isinstance(record, dict):
        raise ValueError(f"{where}: the line is not a JSON object")
    system, question_id = record.get("system"), record.get("question_id")
    if not (isinstance(system, str) and system and isinstance(question_id, str)):
        raise ValueError(
            f"{where}: a line needs a system and a question_id; "
            f"got system {system!r} and question_id {question_id!r}"
        )
    return system, question_id, record.get("chunks")


def _mean_rows(
    scored: dict[tuple[str, str], dict[str, float] | None], questions: list[EvalQuestion]
) -> list[dict[str, Any]]:
    rows = []
    for system in sorted({system for system, _ in scored}):
        for origin in sorted({question.origin for question in questions}):
            in_set = [question for question in questions if question.origin == origin]
            keys = [(system, question.question_id) for question in in_set]
            with_chunks = [scored[key] for key in keys if scored.get(key)]
            n = len(in_set)
            row: dict[str, Any] = {"system": system, "set": origin, "n": n}
            for k in KS:
                row[f"recall@{k}"] = sum(scores[f"recall@{k}"] for scores in with_chunks) / n
            row["MRR"] = sum(scores["reciprocal_rank"] for scores in with_chunks) / n
            row["no chunks"] = n - len(with_chunks)
            rows.append(row)
    return rows


def score_files(result_paths: list[Path], questions: list[EvalQuestion]) -> list[dict[str, Any]]:
    by_id = {question.question_id: question for question in questions}
    scored: dict[tuple[str, str], dict[str, float] | None] = {}  # None: the line has no chunks
    for path in result_paths:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:  # a missing file's OSError names the path
            raise ValueError(f"{path}: not a UTF-8 results file: {exc}") from None
        # split("\n"), not splitlines(): that also breaks on U+2028, which JSON allows in a string.
        for number, line in enumerate(text.split("\n"), start=1):
            if not line.strip():
                continue
            where = f"{path}:{number}"
            system, question_id, chunks = _parse_line(where, line)
            if question_id not in by_id:
                raise ValueError(f"{where}: question_id {question_id!r} is in no question file")
            if (system, question_id) in scored:
                raise ValueError(
                    f"{where}: a second line for system {system!r} and question_id {question_id!r}"
                )
            if chunks is not None and not isinstance(chunks, list):
                raise ValueError(
                    f"{where}: question_id {question_id!r}: chunks must be a list, null or "
                    f"absent; got {type(chunks).__name__}"
                )
            try:
                scored[system, question_id] = (
                    score_chunks(chunks, by_id[question_id].gold_sources) if chunks else None
                )
            except (KeyError, TypeError, AttributeError, ValueError) as exc:
                raise ValueError(
                    f"{where}: question_id {question_id!r}: its chunks cannot be scored: {exc!r}"
                ) from None
    return _mean_rows(scored, questions)


def load_questions(paths: list[Path]) -> list[EvalQuestion]:
    questions: list[EvalQuestion] = []
    file_of: dict[str, Path] = {}
    for path in paths:
        try:
            entries = json.loads(path.read_text(encoding="utf-8"))["questions"]
            in_file = [EvalQuestion.model_validate(entry) for entry in entries]
        except (KeyError, TypeError, ValueError) as exc:  # a missing file's OSError names the path
            raise ValueError(f"{path}: not a question file: {exc!r}") from None
        for question in in_file:
            if question.question_id in file_of:
                raise ValueError(
                    f"{path}: question_id {question.question_id!r} is already in "
                    f"{file_of[question.question_id]}"
                )
            file_of[question.question_id] = path
        questions += in_file
    return questions


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        # Written out: the generated usage lists --questions first, and typed in that order
        # --questions takes the results files as question files.
        usage="%(prog)s [-h] RESULTS.jsonl [RESULTS.jsonl ...] [--questions FILE ...]",
        description="Print one markdown table of file-level recall@k and MRR, one row per "
        "system and question set, from saved results files. Reads local files only.",
    )
    parser.add_argument(
        "results",
        nargs="+",
        type=Path,
        metavar="RESULTS.jsonl",
        help="A results file: one JSON object per line with system, question_id and chunks",
    )
    parser.add_argument(
        "--questions",
        nargs="+",
        type=Path,
        metavar="FILE",
        help="Question files, each a JSON object with a 'questions' list "
        "(default: every *.json in evaluation/questions/)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
    except SystemExit as exc:  # argparse exits 2 on a bad command line and 0 after --help
        return exc.code

    # Configure once from the entry point, in the format the stages use.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )

    try:
        questions = load_questions(args.questions or sorted(QUESTIONS_DIR.glob("*.json")))
        rows = score_files(args.results, questions)
    except (OSError, ValueError) as exc:
        logger.error("%s", exc)
        return 1

    print("| " + " | ".join(COLUMNS) + " |")
    print("|" + "---|" * len(COLUMNS))
    for row in rows:
        values = [row[column] for column in COLUMNS]
        cells = [f"{value:.2f}" if isinstance(value, float) else str(value) for value in values]
        print("| " + " | ".join(cells) + " |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
