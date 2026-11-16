"""
Ask every evaluation question of every indexed variant and save each record as one line
of ``evaluation/outputs/<run_id>/results.jsonl``, the file the scorer reads. Embedding a
question, reranking it (hybrid) and answering it are PAID calls, hundreds per run; see
``--help``.
"""

import argparse
import hashlib
import json
import logging
import os
import sys
import time
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from google import genai

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))  # lets the script run by path from any directory

from data_curation.schemas import EvalQuestion  # noqa: E402  (needs the path set above)
from data_ingestion.chunkers import STRATEGIES  # noqa: E402
from data_ingestion.embedders import EMBEDDERS, cached_tokenizer_path  # noqa: E402
from data_ingestion.pipeline import (  # noqa: E402
    INDEX_REPORT_FILE,
    RUN_META_FILE,
    generate_run_id,
    resolve_run_dir,
)
from data_ingestion.variants import DEFAULT_PREFIX, check_prefix, select_variants  # noqa: E402
from retrieval.baseline_rag import RETRIEVALS, TOP_K, ask, build_rag_pipeline  # noqa: E402
from retrieval.hybrid import CANDIDATES, DEFAULT_CHUNKS_RUN, RERANK_MODEL  # noqa: E402

logger = logging.getLogger(__name__)

STAGE = "generate_results"
OUTPUTS_DIR = Path(__file__).resolve().parent / "outputs"
# The only *.jsonl in a run folder: evaluation/DECISIONS.md, 2026-11-14 (results file).
RESULTS_FILE = "results.jsonl"

# Read in this order, so --limit keeps the handwritten questions (D67).
QUESTION_FILES = (
    PROJECT_ROOT / "evaluation" / "questions" / "handwritten.json",
    PROJECT_ROOT / "evaluation" / "questions" / "synthetic.json",
)
# Saved in run_meta.json, so a resume can refuse a run that hybrid.py has since changed.
HYBRID_SETTINGS = {
    "chunks_run": DEFAULT_CHUNKS_RUN,
    "candidates": CANDIDATES,
    "rerank_model": RERANK_MODEL,
}


def load_questions(paths: Iterable[Path]) -> list[EvalQuestion]:
    """Every question in ``paths``, in file order.

    Raises ``ValueError`` naming the file that is not a question file, or every
    ``question_id`` used twice: resume tells written pairs apart by it.
    """
    questions: list[EvalQuestion] = []
    for path in paths:
        try:
            entries = json.loads(path.read_text(encoding="utf-8"))["questions"]
            questions += [EvalQuestion.model_validate(entry) for entry in entries]
        except (KeyError, TypeError, ValueError) as exc:
            # bad JSON, no "questions" list, or an entry that is not an EvalQuestion
            raise ValueError(
                f"{path} is not a question file: {type(exc).__name__}: {exc}"
            ) from None
    counts = Counter(question.question_id for question in questions)
    repeated = [question_id for question_id, count in counts.items() if count > 1]
    if repeated:
        raise ValueError(f"question_id used more than once: {', '.join(repeated)}")
    return questions


def check_index_report(record: dict[str, Any], bm25_chunks_run: str | None) -> dict[str, Any]:
    """The stage 3 run that built ``record``'s collection, once its report says ``complete``
    and, under hybrid, that it was indexed from ``bm25_chunks_run``.

    Raises ``ValueError`` when that report is missing, unreadable or says anything else.
    Why the record's ``index_run`` names the run: evaluation/DECISIONS.md, 2026-11-14 (index
    report gate), and retrieval/DECISIONS.md, 2026-11-16 (hybrid in baseline_rag).
    """
    collection = record["collection"]
    index_run = record["index_run"]
    if index_run is None:
        raise ValueError(
            f"{collection}: its first record names no index_run (its dense search returned "
            "no chunk that carries one), so its index report cannot be found"
        )
    try:
        report_path = resolve_run_dir(index_run) / INDEX_REPORT_FILE
        report = json.loads(report_path.read_text(encoding="utf-8"))
        statuses = {entry["collection"]: entry["status"] for entry in report}
        chunks_runs = {entry["collection"]: entry.get("chunks_run") for entry in report}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError(
            f"{collection}: cannot read the index report of run {index_run}: "
            f"{type(exc).__name__}: {exc}"
        ) from None
    if statuses.get(collection) != "complete":
        raise ValueError(
            f"{collection}: {report_path} records it as {statuses.get(collection)!r}, "
            "not 'complete'"
        )
    if bm25_chunks_run is None:
        chunks_run = record["chunks"][0]["meta"].get("chunks_run")
    else:
        # Fused lists from two chunkings would mix; a BM25 chunk carries no chunks_run.
        chunks_run = chunks_runs[collection]
        if chunks_run != bm25_chunks_run:
            raise ValueError(
                f"{collection}: {report_path} records it as indexed from chunk run "
                f"{chunks_run!r}, and hybrid retrieval's BM25 searches {bm25_chunks_run!r}"
            )
    return {
        "index_run": index_run,
        "chunks_run": chunks_run,
        "index_status": statuses[collection],
    }


def read_resumed_run(
    run_dir: Path, question_files: list[dict[str, Any]]
) -> tuple[dict[str, Any], set[tuple[str, str]]]:
    """The saved ``run_meta`` of the run in ``run_dir``, and the ``(system, question_id)``
    pairs its results file already holds (C013).

    Raises ``ValueError`` when the run cannot go on as it started, before changing a file.
    """
    run_meta = json.loads((run_dir / RUN_META_FILE).read_text(encoding="utf-8"))
    # Other tools also make timestamp folders under evaluation/outputs/.
    if not isinstance(run_meta, dict) or run_meta.get("stage") != STAGE:
        raise ValueError(f"its {RUN_META_FILE} does not record a {STAGE} run")
    # A run started before hybrid retrieval existed saved neither key, and was dense.
    settings = {"retrieval": "dense", "hybrid": None, **run_meta["settings"]}
    run_meta["settings"] = settings
    changed = [entry["path"] for entry in question_files if entry not in run_meta["question_files"]]
    if changed:
        raise ValueError(f"{', '.join(changed)} changed since it started (the sha256 differs)")
    # build_rag_pipeline reads GEMINI_MODEL itself, so one run could mix two models' answers.
    if not settings["retrieve_only"] and os.getenv("GEMINI_MODEL") != settings["answer_model"]:
        raise ValueError(
            f"it answers with GEMINI_MODEL={settings['answer_model']}, and GEMINI_MODEL is "
            f"now {os.getenv('GEMINI_MODEL')}"
        )
    # build_rag_pipeline takes these from retrieval/hybrid.py, not from the saved settings.
    if settings["hybrid"] not in (None, HYBRID_SETTINGS):
        raise ValueError(
            f"its hybrid retrieval used {settings['hybrid']}, and retrieval/hybrid.py now "
            f"gives {HYBRID_SETTINGS}"
        )

    results_path = run_dir / RESULTS_FILE
    results_bytes = results_path.read_bytes() if results_path.exists() else b""
    complete_length = results_bytes.rfind(b"\n") + 1
    written = set()
    # Split as bytes: str.splitlines also breaks at U+2028, which a chunk's text may hold.
    for number, line in enumerate(results_bytes[:complete_length].splitlines(), start=1):
        try:
            record = json.loads(line)
            written.add((record["system"], record["question_id"]))
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError(
                f"line {number} of {results_path} is not a results record: "
                f"{type(exc).__name__}: {exc}"
            ) from None
    if complete_length < len(results_bytes):
        # The only line a kill can leave: its pair is not in ``written``, so it is asked again.
        logger.warning("%s ends in a half-written line; cutting it off", results_path)
        os.truncate(results_path, complete_length)
    return run_meta, written


def write_run_meta(run_dir: Path, run_meta: dict[str, Any]) -> None:
    path = run_dir / RUN_META_FILE
    path.write_text(json.dumps(run_meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def new_run_meta(
    args: argparse.Namespace, run_id: str, question_files: list[dict[str, Any]]
) -> dict[str, Any]:
    variants = select_variants(args.strategy, args.embedder)
    return {
        "stage": STAGE,
        "run_id": run_id,
        "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "settings": {
            "strategies": list(dict.fromkeys(variant.strategy for variant in variants)),
            "embedders": list(dict.fromkeys(variant.embedder.name for variant in variants)),
            "collection_prefix": args.collection_prefix,
            "retrieve_only": args.retrieve_only,
            "limit": args.limit,
            "top_k": TOP_K,
            "answer_model": None if args.retrieve_only else os.getenv("GEMINI_MODEL"),
            "retrieval": args.retrieval,
            "hybrid": HYBRID_SETTINGS if args.retrieval == "hybrid" else None,
        },
        "question_files": question_files,
        "embedders": {},
        "collections": {},
        "failed": [],
        "model_versions": [],
    }


def build_pipelines(run_meta: dict[str, Any]) -> dict[str, Any]:
    settings = run_meta["settings"]
    kind = "hybrid" if settings["retrieval"] == "hybrid" else "baseline"
    pipelines = {}
    try:
        for strategy in settings["strategies"]:
            for name in settings["embedders"]:
                [variant] = select_variants(strategy, name)
                pipeline = build_rag_pipeline(
                    variant,
                    prefix=settings["collection_prefix"],
                    top_k=settings["top_k"],
                    retrieve_only=settings["retrieve_only"],
                    retrieval=settings["retrieval"],
                )
                pipelines[f"{kind}__{strategy}__{name}"] = pipeline
                spec = variant.embedder
                embedder = {
                    "model": spec.model,
                    "backend": spec.backend,
                    "api_model": spec.api_model,
                    "dimension": spec.dimension,
                    "query_prefix": spec.query_prefix,
                    # The spec has no input_type, so it is read from the built component,
                    # as stage 3 records the document side; BGE's has none and records null.
                    "input_type": getattr(
                        pipeline.get_component("query_embedder"), "input_type", None
                    ),
                }
                # An entry already saved for it, by an earlier attempt or another strategy,
                # must match; with none saved, it is compared with itself.
                saved = run_meta["embedders"].get(name, embedder)
                changed = [
                    f"{field} was {saved.get(field)!r} and is now {embedder[field]!r}"
                    for field in embedder
                    if saved.get(field) != embedder[field]
                ]
                if changed:
                    raise ValueError(
                        f"Embedder {name} changed since this run started: {'; '.join(changed)}. "
                        "One run cannot mix two query embedders; start a new run"
                    )
                run_meta["embedders"][name] = embedder
    except ValueError as exc:  # missing collection, chunk file or config; changed embedder
        logger.error("%s", exc)
        raise SystemExit(1) from None
    return pipelines


def probe_model_version(run_meta: dict[str, Any]) -> None:
    """Add the version the alias answers as now, from one short PAID call, to model_versions.

    Why its own call, and what it does not show: evaluation/DECISIONS.md, 2026-11-14 (model probe).
    """
    settings = run_meta["settings"]
    try:
        client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
        response = client.models.generate_content(
            model=settings["answer_model"], contents="Reply with OK."
        )
        version = response.model_version
    except Exception:  # a missing key, a refused model, no network: answers would fail too
        logger.exception("The model probe failed, so no question was asked")
        raise SystemExit(1) from None
    versions = run_meta["model_versions"]
    if versions and versions[-1]["model_version"] != version:
        logger.warning(
            "%s now answers as %s; this run's last attempt recorded %s, so its answers "
            "come from more than one model version",
            settings["answer_model"],
            version,
            versions[-1]["model_version"],
        )
    versions.append(
        {"at": datetime.now(UTC).isoformat(timespec="seconds"), "model_version": version}
    )


def ask_pairs(
    run_dir: Path,
    run_meta: dict[str, Any],
    pipelines: dict[str, Any],
    pairs: list[tuple[str, EvalQuestion]],
) -> None:
    settings = run_meta["settings"]
    hybrid = settings["retrieval"] == "hybrid"
    bm25_chunks_run = settings["hybrid"]["chunks_run"] if hybrid else None
    gated_collections: set[str] = set()
    with (run_dir / RESULTS_FILE).open("a", encoding="utf-8") as results:
        for number, (system, question) in enumerate(pairs, start=1):
            started = time.monotonic()
            try:
                record = ask(pipelines[system], question.question)
            except Exception as exc:  # one failed pair is listed and the rest still run
                logger.exception(
                    "[%d/%d] %s %s failed", number, len(pairs), system, question.question_id
                )
                run_meta["failed"].append(
                    {
                        "system": system,
                        "question_id": question.question_id,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                continue
            collection = record["collection"]
            if collection not in gated_collections:
                try:
                    build = check_index_report(record, bm25_chunks_run)
                except ValueError as exc:
                    logger.error(
                        "%s. The run stops here and this record is not saved. Fix the "
                        "collection or its report, then: uv run python "
                        "evaluation/generate_results.py --resume %s",
                        exc,
                        run_dir.name,
                    )
                    raise SystemExit(1) from None
                # An entry saved already is an earlier attempt's; with none, it is compared
                # with itself.
                saved = run_meta["collections"].get(collection, build)
                rebuilt = [
                    f"{field} was {saved.get(field)} and is now {build[field]}"
                    for field in ("index_run", "chunks_run")
                    if saved.get(field) != build[field]
                ]
                if rebuilt:
                    logger.error(
                        "%s was rebuilt after an earlier attempt of run %s: %s. One run cannot "
                        "mix two builds, so it stops here and this record is not saved; "
                        "start a new run",
                        collection,
                        run_dir.name,
                        "; ".join(rebuilt),
                    )
                    raise SystemExit(1)
                run_meta["collections"][collection] = build
                # On disk before the collection's first line: a kill skips `finally`.
                write_run_meta(run_dir, run_meta)
                gated_collections.add(collection)
            line = {"system": system, "question_id": question.question_id, **record}
            results.write(json.dumps(line, ensure_ascii=False) + "\n")
            results.flush()  # on disk as soon as it is paid for
            answer = record["answer"]
            logger.info(
                "[%d/%d] %s %s: %d chunks, %s, %.1f s",
                number,
                len(pairs),
                system,
                question.question_id,
                len(record["chunks"]),
                "no answer" if answer is None else f"{len(answer)}-character answer",
                time.monotonic() - started,
            )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ask every evaluation question of every selected strategy × embedder "
        "collection and save the records in a new folder under evaluation/outputs/. "
        "Embedding each question, reranking it (hybrid) and answering it are PAID API calls.",
        # An option left off the command line is left out of the result, not set to a
        # default, so --resume can tell that it came alone.
        argument_default=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--strategy", help=f"One of: {', '.join(STRATEGIES)}, or 'all' (default: all)"
    )
    parser.add_argument(
        "--embedder",
        help=f"One of: {', '.join(EMBEDDERS)}, or 'all' (default: all); bge needs "
        "OPENROUTER_API_KEY, voyage VOYAGE_API_KEY",
    )
    parser.add_argument(
        "--collection-prefix",
        help=f"Collections are named <prefix>__<strategy>__<embedder> (default: {DEFAULT_PREFIX})",
    )
    parser.add_argument(
        "--retrieve-only",
        action="store_true",
        help="Save the retrieved chunks without asking Gemini: every answer is null, and no "
        "GEMINI_MODEL or Google key is needed",
    )
    parser.add_argument(
        "--retrieval",
        choices=RETRIEVALS,
        help="dense searches each collection alone, in systems named baseline__…; hybrid adds "
        "BM25 over the saved chunk run and reranks with Voyage, in systems named hybrid__… "
        "(default: dense)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        metavar="N",
        help="Ask only the first N questions, handwritten first (default: all of them)",
    )
    parser.add_argument(
        "--resume",
        metavar="RUN_ID",
        help="Continue the run in evaluation/outputs/RUN_ID with the settings it saved, "
        "asking only the pairs its results.jsonl does not hold. Takes no other option",
    )
    given = vars(parser.parse_args(argv))
    args = argparse.Namespace(
        **{
            "strategy": "all",
            "embedder": "all",
            "collection_prefix": DEFAULT_PREFIX,
            "retrieve_only": False,
            "retrieval": "dense",
            "limit": None,
            "resume": None,
            **given,
        }
    )
    if args.resume is not None:
        if len(given) > 1:
            parser.error(
                f"--resume takes no other option: the run's settings come from its {RUN_META_FILE}"
            )
        runs = [path.name for path in OUTPUTS_DIR.glob("*") if path.is_dir()]
        if args.resume not in runs:
            parser.error(f"--resume: {args.resume!r} is not the name of a folder in {OUTPUTS_DIR}")
        return args
    try:
        select_variants(args.strategy, args.embedder)  # names every unknown name at once (C008)
        check_prefix(args.collection_prefix)
    except ValueError as exc:
        parser.error(str(exc))
    if args.limit is not None and args.limit < 1:
        parser.error(f"--limit must be at least 1; got {args.limit}")
    return args


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    # Configure once from the entry point, in the format the stages use.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )
    # httpx (under the Qdrant, OpenAI, Voyage and Gemini clients) logs one INFO line per request.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    load_dotenv(PROJECT_ROOT / ".env")

    try:
        questions = load_questions(QUESTION_FILES)
        question_files = [
            {
                # Relative, so a published run_meta.json names no folder on this machine.
                "path": os.path.relpath(path, PROJECT_ROOT),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "count": len(load_questions([path])),
            }
            for path in QUESTION_FILES
        ]
    except (OSError, ValueError) as exc:
        logger.error("%s", exc)
        raise SystemExit(1) from None

    if args.resume is None:
        run_dir = OUTPUTS_DIR / generate_run_id()
        # C010: refused before any paid call; the exclusive mkdir below closes the race.
        if run_dir.exists():
            logger.error("Run folder %s already exists; start again for a new run id", run_dir)
            raise SystemExit(1)
        run_meta = new_run_meta(args, run_dir.name, question_files)
        written = set()
    else:
        run_dir = OUTPUTS_DIR / args.resume
        try:
            run_meta, written = read_resumed_run(run_dir, question_files)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.error("Run %s cannot be resumed: %s", args.resume, exc)
            raise SystemExit(1) from None
        run_meta["failed"] = []  # this attempt's failures; the earlier ones are asked again
    settings = run_meta["settings"]

    # BGE's query embedder cuts the question to 512 tokens with its local tokenizer.
    if "bge" in settings["embedders"] and cached_tokenizer_path("bge") is None:
        logger.error(
            "BGE's tokenizer is not in the project model cache. "
            "Run 'uv run python scripts/download_models.py' from the project root first."
        )
        raise SystemExit(1)
    # Both query embedders build without their key and read it at the first question, so a
    # missing key would get past the probe and the new folder (D60).
    keys = {"bge": "OPENROUTER_API_KEY", "voyage": "VOYAGE_API_KEY"}
    for name in settings["embedders"]:
        if not os.getenv(keys[name]):
            logger.error(
                "%s is not set; %s needs it to embed each question (.env)", keys[name], name
            )
            raise SystemExit(1)
    if settings["retrieval"] == "hybrid" and not os.getenv("VOYAGE_API_KEY"):
        logger.error("VOYAGE_API_KEY is not set; hybrid retrieval needs it to rerank (.env)")
        raise SystemExit(1)

    # Every pipeline is built before the run folder exists: each costs one free Qdrant
    # call, so a missing collection or key stops the run with nothing written (D60).
    pipelines = build_pipelines(run_meta)

    # Question first: a run stopped part way has asked every variant the same questions.
    pairs = [
        (system, question)
        for question in questions[: settings["limit"]]
        for system in pipelines
        if (system, question.question_id) not in written
    ]
    logger.info(
        "Run %s: %d pair(s) to ask, %d already written", run_dir.name, len(pairs), len(written)
    )

    if pairs and not settings["retrieve_only"]:
        probe_model_version(run_meta)

    if args.resume is None:
        try:
            run_dir.mkdir(parents=True)
        except FileExistsError:
            logger.error("Run folder %s already exists; start again for a new run id", run_dir)
            raise SystemExit(1) from None
    # The settings go to disk before the first record, so a killed run can be resumed (C013).
    write_run_meta(run_dir, run_meta)
    try:
        ask_pairs(run_dir, run_meta, pipelines, pairs)
    finally:
        # Ctrl-C reaches this; a kill does not, and loses only what this attempt added.
        write_run_meta(run_dir, run_meta)

    failed = run_meta["failed"]
    if failed:
        logger.error(
            "%d of %d pair(s) failed and are not in %s. Ask them again with: "
            "uv run python evaluation/generate_results.py --resume %s",
            len(failed),
            len(pairs),
            RESULTS_FILE,
            run_dir.name,
        )
        raise SystemExit(1)
    logger.info(
        "Run %s: this attempt added %d record(s); %s now holds %d",
        run_dir.name,
        len(pairs),
        run_dir / RESULTS_FILE,
        len(written) + len(pairs),
    )


if __name__ == "__main__":
    main()
