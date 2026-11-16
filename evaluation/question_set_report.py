"""
One markdown table row per question file (size, wording, gold pages, echo and review marks),
and with ``--sheet`` a blind review sheet. Reads local files only, with no API calls.
"""

import argparse
import json
import logging
import random
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CORPUS_DIR = PROJECT_ROOT / "data" / "fastapi" / "normalized" / "github-docs" / "v1"
REVIEW_PATH = PROJECT_ROOT / "evaluation" / "question_review.json"
OUTPUTS_DIR = PROJECT_ROOT / "evaluation" / "outputs"

WORD = re.compile(r"[a-z0-9_]+")
HOW_OPENING = re.compile(r"\s*how (?:do|can) i\b", re.IGNORECASE)
# Function and question words only. Tokens under three characters are dropped before this.
STOPWORDS = (
    {"how", "what", "when", "where", "which", "who", "why"}
    | {"are", "was", "were", "can", "could", "did", "does", "had", "has", "have"}
    | {"may", "should", "will", "would", "not"}
    | {"the", "this", "that", "these", "those", "there", "any", "all"}
    | {"its", "our", "their", "them", "they", "you", "your"}
    | {"about", "and", "but", "for", "from", "into", "than", "then", "with"}
)
HEADERS = (
    "set",
    "n",
    "mean words",
    "opens How do/can I",
    "mean gold pages",
    "multi-page",
    "gold pages covered",
    "echo mean",
    "echo min",
    "realistic",
    "needs every page",
)

logger = logging.getLogger(__name__)


def marks_for(
    questions: list[dict[str, Any]], review: dict[str, dict[str, Any]], mark: str
) -> list[bool]:
    """The true or false marks the review holds for these questions; the unmarked are left out."""
    marks = [review.get(question["question_id"], {}).get(mark) for question in questions]
    return [value for value in marks if value is not None]


def question_stats(
    questions: list[dict[str, Any]],
    page_texts: dict[str, str],
    review: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """One table row. Echo is the share of a question's content words found in its gold pages."""
    if not questions:
        raise ValueError("question_stats: there are no questions")
    gold_pages = {source for question in questions for source in question["gold_sources"]}
    unknown = sorted(gold_pages - page_texts.keys())
    if unknown:
        raise ValueError(f"question_stats: gold sources with no page text: {', '.join(unknown)}")
    page_words = {source: set(WORD.findall(page_texts[source].lower())) for source in gold_pages}

    echoes = []
    for question in questions:
        tokens = WORD.findall(question["question"].lower())
        content_words = {token for token in tokens if len(token) >= 3} - STOPWORDS
        # With no content words or no gold page there is no share, so the question is left out.
        if content_words and question["gold_sources"]:
            gold_words = set().union(*(page_words[source] for source in question["gold_sources"]))
            echoes.append(len(content_words & gold_words) / len(content_words))

    n = len(questions)
    how_openings = [question for question in questions if HOW_OPENING.match(question["question"])]
    multi_page = [question for question in questions if len(question["gold_sources"]) >= 2]
    realistic = marks_for(questions, review, "realistic")
    needs_all = marks_for(multi_page, review, "needs_all_gold")
    return {
        "n": n,
        "mean_words": sum(len(question["question"].split()) for question in questions) / n,
        "how_opening_share": len(how_openings) / n,
        "mean_gold_pages": sum(len(question["gold_sources"]) for question in questions) / n,
        "multi_page_share": len(multi_page) / n,
        "distinct_gold_pages": len(gold_pages),
        "echo_mean": sum(echoes) / len(echoes) if echoes else None,
        "echo_min": min(echoes, default=None),
        "realistic_share": sum(realistic) / len(realistic) if realistic else None,
        "needs_all_gold_share": sum(needs_all) / len(needs_all) if needs_all else None,
    }


def percent(share: float | None) -> str:
    return "" if share is None else f"{share:.1%}"


def table_row(label: str, stats: dict[str, Any]) -> str:
    cells = [
        label,
        str(stats["n"]),
        f"{stats['mean_words']:.1f}",
        percent(stats["how_opening_share"]),
        f"{stats['mean_gold_pages']:.2f}",
        percent(stats["multi_page_share"]),
        str(stats["distinct_gold_pages"]),
        percent(stats["echo_mean"]),
        percent(stats["echo_min"]),
        percent(stats["realistic_share"]),
        percent(stats["needs_all_gold_share"]),
    ]
    return "| " + " | ".join(cells) + " |"


def write_sheet(sheet_dir: Path, questions: list[dict[str, Any]]) -> None:
    """Shuffled with a fixed seed under neutral ids, so a mark cannot follow a question's set."""
    shuffled = list(questions)
    random.Random(42).shuffle(shuffled)
    key = {}
    sheet = ["# Review sheet", "", "Could a FastAPI user plausibly ask this? Mark yes or no.", ""]
    for number, question in enumerate(shuffled, start=1):
        key[f"q{number:02d}"] = question["question_id"]
        sheet += [f"## q{number:02d}", "", question["question"], "", "realistic:", ""]

    multi_page = ["# Multi-page questions", "", "Is every page needed? Mark yes or no.", ""]
    for question in questions:
        if len(question["gold_sources"]) >= 2:
            multi_page += [
                f"## {question['question_id']}",
                "",
                question["question"],
                "",
                *(f"- {source}" for source in question["gold_sources"]),
                "",
                f"Reference answer: {question['reference_answer']}",
                "",
                "needs_all_gold:",
                "",
            ]

    (sheet_dir / "sheet.md").write_text("\n".join(sheet), encoding="utf-8")
    (sheet_dir / "key.json").write_text(json.dumps(key, indent=2) + "\n", encoding="utf-8")
    (sheet_dir / "multi_page.md").write_text("\n".join(multi_page), encoding="utf-8")


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:  # a missing file's OSError already names the path
        raise ValueError(f"{path} is not valid JSON: {exc}") from None


def load_review(path: Path) -> dict[str, dict[str, Any]]:
    review = read_json(path)
    if not isinstance(review, dict):
        raise ValueError(f"{path}: the review file must be a JSON object keyed by question_id")
    bad_ids = sorted(
        question_id
        for question_id, entry in review.items()
        if not isinstance(entry, dict)
        or any(
            not isinstance(entry.get(mark), bool | None) for mark in ("realistic", "needs_all_gold")
        )
    )
    if bad_ids:
        raise ValueError(
            f"{path}: each entry must be an object whose realistic and needs_all_gold marks "
            f"are true, false or null; not so for {', '.join(bad_ids)}"
        )
    return review


def load_questions(path: Path) -> list[dict[str, Any]]:
    data = read_json(path)
    if not isinstance(data, dict) or not isinstance(data.get("questions"), list):
        raise ValueError(f"{path}: expected a JSON object with a 'questions' list")
    return data["questions"]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Print one markdown table row per question file: size, wording, gold "
        "pages, echo and review marks. Reads local files only.",
    )
    parser.add_argument(
        "files",
        nargs="+",
        type=Path,
        metavar="FILE",
        help="A question file: a JSON object with a 'questions' list",
    )
    parser.add_argument(
        "--review",
        type=Path,
        metavar="PATH",
        help="Review marks by question_id (default: evaluation/question_review.json, "
        "when it exists)",
    )
    parser.add_argument(
        "--sheet",
        action="store_true",
        help="Also write a blind review sheet to a new evaluation/outputs/review_sheet_<run_id>/",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    # Configure once from the entry point, in the format the stages use.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )

    review_path = args.review or (REVIEW_PATH if REVIEW_PATH.exists() else None)
    try:
        manifest = read_json(CORPUS_DIR / "manifest.json")
        page_texts = {
            entry["source_path"]: (CORPUS_DIR / "docs" / entry["output_filename"]).read_text(
                encoding="utf-8"
            )
            for entry in manifest["files"]
        }
        review = load_review(review_path) if review_path else {}
        question_sets = [(path, load_questions(path)) for path in args.files]
        all_questions = [question for _, questions in question_sets for question in questions]
        # A repeated id would make the sheet's key and the review marks ambiguous.
        id_counts = Counter(question["question_id"] for question in all_questions)
        repeated = sorted(question_id for question_id, count in id_counts.items() if count > 1)
        if repeated:
            raise ValueError(
                f"question_id used more than once in the given files: {', '.join(repeated)}"
            )
    except (OSError, ValueError) as exc:
        logger.error("%s", exc)
        raise SystemExit(1) from None

    rows = []
    for path, questions in question_sets:
        try:
            rows.append(table_row(path.stem, question_stats(questions, page_texts, review)))
        except ValueError as exc:  # an empty file, or a gold source the manifest does not list
            logger.error("%s: %s", path, exc)
            raise SystemExit(1) from None
        if review_path:
            multi_page = [question for question in questions if len(question["gold_sources"]) >= 2]
            logger.info(
                "%s: realistic marks on %d of %d questions; "
                "needs_all_gold marks on %d of %d multi-page questions",
                path.stem,
                len(marks_for(questions, review, "realistic")),
                len(questions),
                len(marks_for(multi_page, review, "needs_all_gold")),
                len(multi_page),
            )

    if args.sheet:
        sheet_dir = OUTPUTS_DIR / f"review_sheet_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
        try:
            sheet_dir.mkdir(parents=True)
        except FileExistsError:
            logger.error("Sheet folder %s already exists; run the command again", sheet_dir)
            raise SystemExit(1) from None
        write_sheet(sheet_dir, all_questions)
        logger.info("Review sheet written to %s", sheet_dir)

    print("| " + " | ".join(HEADERS) + " |")
    print("|" + "---|" * len(HEADERS))
    print("\n".join(rows))


if __name__ == "__main__":
    main()
