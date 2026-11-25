"""A blind sheet for picking each golden question's reference chunks from saved results, and
``--build``, which turns the marked sheet into ``evaluation/golden.json``. Reads local files only.

Why: evaluation/DECISIONS.md, "2026-11-20 — The golden set: reference chunks by identity, in
`evaluation/golden.json`, picked on a blind sheet".
"""

import argparse
import json
import logging
import random
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evaluation.retrieval_metrics import QUESTIONS_DIR, load_questions
from evaluation.schemas import EvalQuestion, GoldenEntry, GoldenSet, ReferenceChunk

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUTS_DIR = PROJECT_ROOT / "evaluation" / "outputs"
GOLDEN_PATH = PROJECT_ROOT / "evaluation" / "golden.json"

# Why these 15: evaluation/DECISIONS.md, 2026-11-20
GOLDEN_IDS = (
    *(f"hw-{number:02d}" for number in range(1, 11)),
    "syn-23",
    "syn-20",
    "syn-13",
    "syn-24",
    "syn-26",
)
PICK_INSTRUCTION = (
    "Pick 1 to 5 candidate numbers, edit the answer above so that it rests on them, "
    "and say why they support it."
)
# The answer runs to the sheet's own `## Pick` heading, so a heading typed into it is kept.
PAGE_MARKERS = re.compile(
    r"^## Reference answer\n(?P<answer>.*?)^## Pick\n.*?^pick:(?P<pick>.*?)"
    r"^rationale:(?P<rationale>.*?)^## Candidates$",
    re.MULTILINE | re.DOTALL,
)
BLANK_EDGES = re.compile(r"^\s*\n|\n\s*$")

logger = logging.getLogger(__name__)


def parse_line(line: str, where: str) -> dict[str, Any]:
    try:
        record = json.loads(line)
    except ValueError as exc:
        raise ValueError(f"{where}: not valid JSON: {exc}") from None
    if not isinstance(record, dict):
        raise ValueError(f"{where}: the line is not a JSON object")
    return record


def add_chunks(
    question_candidates: dict[tuple[str, str, int], str],
    content_by_identity: dict[tuple[str, str, int], str],
    chunks: list[Any],
    where: str,
    question_id: str,
) -> None:
    for chunk in chunks:
        try:
            meta = chunk["meta"]
            reference = ReferenceChunk.model_validate(
                {field: meta.get(field) for field in ("source_path", "strategy", "chunk_index")}
                | {"content": chunk.get("content")}
            )
        except (KeyError, TypeError, AttributeError, ValueError) as exc:
            raise ValueError(
                f"{where}: question_id {question_id!r}: a chunk's meta.source_path, "
                f"meta.strategy, meta.chunk_index or content is missing or invalid: {exc!r}"
            ) from None
        identity = (reference.source_path, reference.strategy, reference.chunk_index)
        # Across every question: an identity names one chunk of the index run.
        if content_by_identity.setdefault(identity, reference.content) != reference.content:
            raise ValueError(
                f"{where}: question_id {question_id!r}: chunk {identity} has other "
                "content than an earlier line gave it"
            )
        question_candidates[identity] = reference.content


def gather_candidates(
    result_paths: list[Path],
) -> tuple[tuple[str, str] | None, dict[str, dict[tuple[str, str, int], str]]]:
    candidates: dict[str, dict[tuple[str, str, int], str]] = {}
    content_by_identity: dict[tuple[str, str, int], str] = {}
    first_index_run: tuple[str, str] | None = None
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
            record = parse_line(line, where)
            question_id = record.get("question_id")
            if question_id not in GOLDEN_IDS:
                continue
            question_candidates = candidates.setdefault(question_id, {})
            chunks = record.get("chunks")
            if chunks is not None and not isinstance(chunks, list):
                raise ValueError(
                    f"{where}: question_id {question_id!r}: chunks must be a list, null or "
                    f"absent; got {type(chunks).__name__}"
                )
            if not chunks:  # a line with no chunks names no index_run either
                continue

            index_run = record.get("index_run")
            if not isinstance(index_run, str) or not index_run:
                raise ValueError(
                    f"{where}: question_id {question_id!r} has chunks but no index_run"
                )
            if first_index_run is None:
                first_index_run = (index_run, where)
            elif index_run != first_index_run[0]:
                raise ValueError(
                    f"{where}: index_run {index_run!r}, but {first_index_run[1]} has "
                    f"{first_index_run[0]!r}; the golden set's chunks come from one index_run"
                )
            add_chunks(question_candidates, content_by_identity, chunks, where, question_id)
    return first_index_run, candidates


def check_every_golden_id(
    candidates: dict[str, dict[tuple[str, str, int], str]], result_paths: list[Path]
) -> None:
    missing = [question_id for question_id in GOLDEN_IDS if question_id not in candidates]
    if missing:
        files = ", ".join(str(path) for path in result_paths)
        raise ValueError(f"golden question_ids with no line in {files}: {', '.join(missing)}")
    empty = [question_id for question_id in GOLDEN_IDS if not candidates[question_id]]
    if empty:
        raise ValueError(f"golden question_ids with no chunks to pick from: {', '.join(empty)}")


def number_candidates(
    candidates: dict[str, dict[tuple[str, str, int], str]],
) -> dict[str, list[dict[str, Any]]]:
    numbered = {}
    for question_id in GOLDEN_IDS:
        # Sorted first, so the numbering does not depend on the order the files are given in.
        chunks = [
            {"source_path": path, "strategy": strategy, "chunk_index": index, "content": content}
            for (path, strategy, index), content in sorted(candidates[question_id].items())
        ]
        random.Random(question_id).shuffle(chunks)
        numbered[question_id] = chunks
    return numbered


def collect_candidates(result_paths: list[Path]) -> tuple[str, dict[str, list[dict[str, Any]]]]:
    first_index_run, candidates = gather_candidates(result_paths)
    check_every_golden_id(candidates, result_paths)
    return first_index_run[0], number_candidates(candidates)


def write_page(path: Path, question: EvalQuestion, chunks: list[dict[str, Any]]) -> None:
    lines = [f"# {question.question_id}", "", question.question, ""]
    lines += ["## Reference answer", "", question.reference_answer, ""]
    lines += ["## Pick", "", PICK_INSTRUCTION, "", "pick:", "rationale:", ""]
    lines += ["## Candidates", ""]
    for number, chunk in enumerate(chunks, start=1):
        lines += [f"### {number}: `{chunk['source_path']}`", "", chunk["content"], ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def write_sheet(result_paths: list[Path]) -> Path:
    questions = load_questions(sorted(QUESTIONS_DIR.glob("*.json")))
    by_id = {question.question_id: question for question in questions}
    unknown = [question_id for question_id in GOLDEN_IDS if question_id not in by_id]
    if unknown:
        raise ValueError(f"golden question_ids in no question file: {', '.join(unknown)}")
    index_run, numbered = collect_candidates(result_paths)

    sheet_dir = OUTPUTS_DIR / f"golden_sheet_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    try:
        sheet_dir.mkdir(parents=True)
    except FileExistsError:
        raise ValueError(
            f"Sheet folder {sheet_dir} already exists; run the command again"
        ) from None
    for question_id, chunks in numbered.items():
        write_page(sheet_dir / f"{question_id}.md", by_id[question_id], chunks)
    key = {
        "index_run": index_run,
        "source_runs": [path.parent.name for path in result_paths],
        "questions": {
            question_id: {str(number): chunk for number, chunk in enumerate(chunks, start=1)}
            for question_id, chunks in numbered.items()
        },
    }
    (sheet_dir / "key.json").write_text(json.dumps(key, indent=2) + "\n", encoding="utf-8")
    return sheet_dir


def read_page(page: Path, numbered: dict[str, Any]) -> GoldenEntry:
    try:
        text = page.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:  # a missing page's OSError names the path
        raise ValueError(f"{page}: not UTF-8: {exc}") from None
    markers = PAGE_MARKERS.search(text)
    if not markers:
        raise ValueError(
            f"{page}: the '## Reference answer', '## Pick', 'pick:', 'rationale:' and "
            "'## Candidates' markers are not all there, in that order"
        )
    if not re.fullmatch(r"[\d,\s]*", markers["pick"]):
        raise ValueError(
            f"{page}: 'pick:' holds something other than numbers, commas and spaces: "
            f"{markers['pick'].strip()!r}"
        )
    picks = markers["pick"].replace(",", " ").split()
    unknown = [number for number in picks if number not in numbered]
    if unknown:
        raise ValueError(f"{page}: pick {', '.join(unknown)} is not a candidate number on the page")
    if len(set(picks)) != len(picks):
        raise ValueError(f"{page}: a pick is repeated in {markers['pick'].strip()!r}")
    try:
        return GoldenEntry.model_validate(
            {
                "question_id": page.stem,
                "reference_answer": BLANK_EDGES.sub("", markers["answer"]),
                "reference_chunks": [numbered[number] for number in picks],
                "rationale": markers["rationale"].strip(),
            }
        )
    except ValueError as exc:  # pydantic's ValidationError is a ValueError
        raise ValueError(f"{page}: {exc}") from None


def build_golden(sheet_dir: Path) -> GoldenSet:
    key_path = sheet_dir / "key.json"
    try:
        text = key_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:  # a missing key's OSError names the path
        raise ValueError(f"{key_path}: not UTF-8: {exc}") from None
    try:
        key = json.loads(text)
        questions = key["questions"]
        missing = [question_id for question_id in GOLDEN_IDS if question_id not in questions]
        extra = [question_id for question_id in questions if question_id not in GOLDEN_IDS]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{key_path}: not a sheet key: {exc!r}") from None
    if missing or extra or not isinstance(questions, dict):
        raise ValueError(
            f"{key_path}: the key's questions must be an object holding exactly the golden "
            f"question_ids; missing {missing}, not golden {extra}"
        )
    not_objects = [
        question_id for question_id in GOLDEN_IDS if not isinstance(questions[question_id], dict)
    ]
    if not_objects:
        raise ValueError(
            f"{key_path}: the candidates of {', '.join(not_objects)} are not a JSON object"
        )
    entries = [
        read_page(sheet_dir / f"{question_id}.md", questions[question_id])
        for question_id in GOLDEN_IDS
    ]
    try:
        return GoldenSet.model_validate(
            {
                "index_run": key.get("index_run"),
                "source_runs": key.get("source_runs"),
                "entries": entries,
            }
        )
    except ValueError as exc:
        raise ValueError(f"{key_path}: {exc}") from None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        usage="%(prog)s [-h] (RESULTS.jsonl [RESULTS.jsonl ...] | --build SHEET_DIR)",
        description="Write a blind sheet for picking the golden questions' reference chunks "
        "from saved results files, or with --build turn a marked sheet into "
        "evaluation/golden.json. Reads local files only.",
    )
    parser.add_argument(
        "results",
        nargs="*",
        type=Path,
        metavar="RESULTS.jsonl",
        help="A results file whose lines hold the golden questions' ranked chunks",
    )
    parser.add_argument(
        "--build",
        type=Path,
        metavar="SHEET_DIR",
        help="A marked sheet folder, holding one page per question and its key.json",
    )
    args = parser.parse_args(argv)
    if bool(args.results) == bool(args.build):
        parser.error("give results files or --build SHEET_DIR, not both and not neither")
    return args


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
        if args.build:
            golden = build_golden(args.build)
            GOLDEN_PATH.write_text(golden.model_dump_json(indent=2) + "\n", encoding="utf-8")
            logger.info("Wrote %d entries to %s", len(golden.entries), GOLDEN_PATH)
        else:
            sheet_dir = write_sheet(args.results)
            logger.info("Golden sheet written to %s", sheet_dir)
    except (OSError, ValueError) as exc:
        logger.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
