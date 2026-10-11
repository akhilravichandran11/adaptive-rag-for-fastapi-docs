"""
Shape rules for chunks, messy copies of documents, and the chunk-size report.

The corpus will change — it may be updated, grow, or gain a lower-quality source —
so chunking is checked against rules that hold for any input rather than against
counts measured on today's files. Every chunk a strategy produces is held to five
**shape rules**, each in the strategy's own unit (``ChunkSettings.unit``):

    over_max      no chunk longer than the strategy's declared maximum
    under_min     no chunk shorter than its declared minimum, unless it is the
                  document's only chunk (only ``semantic`` declares one today)
    lost_text     no text lost: with whitespace ignored, the chunks cover the
                  whole document (overlap allowed)
    out_of_order  chunks follow the document's order
    empty         no empty or whitespace-only chunk

Nothing here changes a chunk. :func:`check_chunks` reports what breaks a rule,
:func:`build_report` counts the breaks per strategy over a whole run, and
:func:`write_report` saves that **chunk-size report** as JSON under
``outputs/chunk-size-reports/``. The report also counts two informational flags
that are never rules — **low-prose chunks** (fewer than half the characters are
letters or whitespace) and chunks holding an **unbroken run** (more than 200
characters with no whitespace) — and how much each embedder would truncate.

:func:`make_messy_copies` degrades real documents on purpose (blank lines
stripped, punctuation removed, an unbroken run injected, documents glued
together), standing in for a lower-quality source the corpus may gain later.

    uv run python scripts/chunk_size_report.py    # report on the current corpus

Nothing in this module calls an API. Counting sentences needs NLTK's
``punkt_tab``, and the report's BGE token counts need BGE's tokenizer, both from
the project model cache (``scripts/download_models.py``).
"""

from __future__ import annotations

import bisect
import hashlib
import json
import logging
import random
import string
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from haystack import Document

from data_ingestion.chunkers.base import (
    CHAR_UNIT,
    SENTENCE_UNIT,
    WORD_UNIT,
    ChunkSettings,
    source_path_of,
)
from data_ingestion.embedders import count_tokens, truncation_report
from data_ingestion.model_cache import register_nltk_data_dir

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = PROJECT_ROOT / "outputs" / "chunk-size-reports"

# The strategies the shape rules cover (R5). ``markdown_ast`` joins when IDX-07
# lands; this list is written out rather than read from ``STRATEGIES`` so a
# strategy is added to the report on purpose.
SHAPE_STRATEGIES = (
    "naive_small",
    "naive_medium",
    "naive_large",
    "sentence",
    "recursive",
    "semantic",
)
# ``semantic`` finds its boundaries with BGE on OpenRouter: a paid call.
PAID_STRATEGIES = ("semantic",)
SIZE_BASED_STRATEGIES = tuple(name for name in SHAPE_STRATEGIES if name not in PAID_STRATEGIES)

# The five shape rules, in the order they are reported.
OVER_MAX = "over_max"
UNDER_MIN = "under_min"
LOST_TEXT = "lost_text"
OUT_OF_ORDER = "out_of_order"
EMPTY = "empty"
SHAPE_RULES = (OVER_MAX, UNDER_MIN, LOST_TEXT, OUT_OF_ORDER, EMPTY)

# Chunk metadata key under which a strategy declares its minimum length, in its
# own unit. ``semantic`` records it on every chunk; the others declare none.
MIN_LENGTH_KEY = "min_length"

# Informational thresholds (R15), chosen without looking at the corpus.
DEFAULT_LOW_PROSE_SHARE = 0.5
DEFAULT_UNBROKEN_RUN = 200

# The four ways a messy copy degrades a document (R8).
STRIP_BLANK_LINES = "strip_blank_lines"
REMOVE_PUNCTUATION = "remove_punctuation"
INJECT_UNBROKEN_RUN = "inject_unbroken_run"
GLUE_DOCUMENTS = "glue_documents"
MESSY_CHANGES = (STRIP_BLANK_LINES, REMOVE_PUNCTUATION, INJECT_UNBROKEN_RUN, GLUE_DOCUMENTS)
DEFAULT_MESSY_COUNT = 3
DEFAULT_MESSY_SEED = 0
DEFAULT_INJECTED_RUN_LENGTH = 5_000

# Characters an injected run is drawn from: no whitespace, nothing content-specific.
_RUN_ALPHABET = string.ascii_letters + string.digits


@dataclass(frozen=True)
class RuleBreak:
    """One shape rule one document's chunks break.

    ``chunk_index`` is the chunk's position in the list that was checked, or
    ``None`` when the break belongs to the document as a whole (text that no
    chunk covers).
    """

    rule: str
    chunk_index: int | None
    detail: str


# --- lengths ------------------------------------------------------------------


@lru_cache(maxsize=1)
def _sentence_splitter():
    """Haystack's sentence splitter, configured as ``DocumentSplitter`` builds it."""
    from haystack.components.preprocessors.sentence_tokenizer import SentenceSplitter

    # Registered first: NLTK looks the tokenizer up on its search path as it is then.
    register_nltk_data_dir()
    return SentenceSplitter(
        language="en",
        use_split_rules=True,
        extend_abbreviations=True,
        # As DocumentSplitter.warm_up sets it; without it the same chunk can count
        # one sentence more or fewer than the sentence strategy saw.
        keep_white_spaces=True,
    )


def chunk_length(text: str, unit: str) -> int:
    """How long ``text`` is in ``unit``, counted the way the splitters count it.

    A word is a run between single spaces, as Haystack's word splitter cuts it, so
    newlines travel inside words; sentences come from the same NLTK tokenizer and
    rules the sentence splitter uses; characters are characters. A chunk's
    sentences are counted against its parent instead (see ``_chunk_lengths``).
    """
    if unit == CHAR_UNIT:
        return len(text)
    if unit == WORD_UNIT:
        if not text:
            return 0
        # Each space ends one word; a trailing space ends the last one.
        return text.count(" ") + (0 if text.endswith(" ") else 1)
    if unit == SENTENCE_UNIT:
        if not text.strip():
            return 0
        return len(_sentence_splitter().split_sentences(text))
    raise ValueError(f"Unknown chunk unit {unit!r}; expected one of: word, sentence, char")


def _chunk_lengths(parent_text: str, texts: Sequence[str], unit: str) -> list[int]:
    """Each chunk's length in ``unit`` (0 for a blank chunk), as its strategy counted it.

    Sentences are counted on the parent, not on the chunk alone: the sentence
    strategy groups the parent's sentences, and a chunk read on its own can split
    differently at its edges (the tail of a ``...`` becomes two one-character
    "sentences"). A chunk's length is how many of the parent's sentences start
    inside it; a chunk that is not verbatim parent text is counted on its own.
    """
    if unit != SENTENCE_UNIT:
        return [chunk_length(text, unit) if text.strip() else 0 for text in texts]

    starts = (
        [sentence["start"] for sentence in _sentence_splitter().split_sentences(parent_text)]
        if parent_text.strip()
        else []
    )
    lengths: list[int] = []
    search_from = 0
    for text in texts:
        if not text.strip():
            lengths.append(0)
            continue
        at = parent_text.find(text, search_from)
        if at == -1:
            at = parent_text.find(text)
        if at == -1:
            lengths.append(chunk_length(text, unit))
            continue
        lengths.append(bisect.bisect_left(starts, at + len(text)) - bisect.bisect_left(starts, at))
        search_from = at + 1
    return lengths


def _without_whitespace(text: str) -> str:
    return "".join(text.split())


# --- the shape rules ------------------------------------------------------------


def check_chunks(
    parent: Document, chunks: Sequence[Document], settings: ChunkSettings
) -> list[RuleBreak]:
    """Check one parent document's chunks against the five shape rules.

    ``chunks`` are that parent's chunks in the order the strategy produced them.
    Lengths are counted in ``settings.unit`` against ``settings.size``; a minimum
    is read from the chunks' ``min_length`` metadata where the strategy declares
    one. Returns every break found, in chunk order with document-level breaks
    last; an empty list means the chunks keep all five rules.
    """
    texts = [chunk.content or "" for chunk in chunks]
    breaks: list[RuleBreak] = []

    non_empty = sum(1 for text in texts if text.strip())
    lengths = _chunk_lengths(parent.content or "", texts, settings.unit)
    for index, (chunk, text, length) in enumerate(zip(chunks, texts, lengths, strict=True)):
        if not text.strip():
            breaks.append(RuleBreak(EMPTY, index, "chunk is empty or whitespace only"))
            continue
        if length > settings.size:
            breaks.append(
                RuleBreak(
                    OVER_MAX,
                    index,
                    f"{length} {settings.unit}s, declared maximum {settings.size}",
                )
            )
        minimum = chunk.meta.get(MIN_LENGTH_KEY)
        # A document's only chunk is the whole document: shorter than the minimum
        # is then the document's own length, not a split the strategy made.
        if minimum is not None and non_empty > 1 and length < minimum:
            breaks.append(
                RuleBreak(
                    UNDER_MIN,
                    index,
                    f"{length} {settings.unit}s, declared minimum {minimum}",
                )
            )

    breaks.extend(_check_coverage(parent.content or "", texts))
    return breaks


def _check_coverage(parent_text: str, texts: Sequence[str]) -> list[RuleBreak]:
    """Rules 3 and 4: the chunks cover the parent, in its order, whitespace ignored.

    The chunks keep both rules when each can be placed in the parent at or after
    the previous chunk's start and no later than the end of what is covered so
    far (overlapping chunks repeat the previous chunk's tail), covering it to the
    end. Repeated text can allow several placements, so every one is tried
    before anything is reported. When none fits, each chunk is placed at the
    latest position that touches the coverage to say what broke: a chunk that
    only occurs earlier is out of order, one that occurs nowhere is not the
    parent's text, and whatever no chunk covers is lost.

    The check matches text, not offsets, so it cannot see a loss that repeated
    text hides: a chunk emitted twice in place of two copies of the same text
    still covers both.
    """
    source = _without_whitespace(parent_text)
    needles = [needle for needle in map(_without_whitespace, texts) if needle]
    if _fits_in_order(source, needles):
        return []

    breaks: list[RuleBreak] = []
    covered: list[tuple[int, int]] = []
    previous_start = 0
    covered_end = 0

    for index, text in enumerate(texts):
        needle = _without_whitespace(text)
        if not needle:
            continue  # reported as empty
        # The latest placement that still touches what is covered: overlap is
        # allowed, a gap is not.
        position = source.rfind(needle, previous_start, covered_end + len(needle))
        if position == -1:
            # Further on, past a gap the coverage check below reports.
            position = source.find(needle, previous_start)
        if position == -1:
            position = source.find(needle)
            if position == -1:
                breaks.append(
                    RuleBreak(LOST_TEXT, index, "chunk text does not appear in the document")
                )
                continue
            breaks.append(
                RuleBreak(
                    OUT_OF_ORDER,
                    index,
                    "chunk text appears in the document only before the previous chunk",
                )
            )
            covered.append((position, position + len(needle)))
            continue
        covered.append((position, position + len(needle)))
        previous_start = position
        covered_end = max(covered_end, position + len(needle))

    missing = _uncovered(len(source), covered)
    if missing:
        breaks.append(
            RuleBreak(
                LOST_TEXT,
                None,
                f"{missing} of {len(source)} non-whitespace characters are in no chunk",
            )
        )
    return breaks


# Placements tried before giving up on finding one; only text that repeats
# densely inside the overlap ever needs many.
_PLACEMENT_BUDGET = 100_000


def _fits_in_order(source: str, needles: Sequence[str]) -> bool:
    """Whether ``needles`` can be placed in ``source`` in order, overlapping, gap-free, to the end.

    A depth-first search over each needle's candidate starts, latest first (the
    usual answer), remembering states that failed. Iterative, so a document with
    thousands of chunks does not hit the recursion limit. Returns ``False`` when
    the budget runs out, which leaves the decision to the caller's diagnosis.
    """
    failed: set[tuple[int, int, int]] = set()
    # Each frame: needle index, previous start, covered end, exclusive upper
    # bound for the next candidate's end (candidates are tried latest first).
    frames = [[0, 0, 0, None]]
    budget = _PLACEMENT_BUDGET
    while frames:
        index, previous_start, covered_end, upper = frames[-1]
        if index == len(needles):
            if covered_end == len(source):
                return True
            frames.pop()
            continue
        needle = needles[index]
        if upper is None:
            if (index, previous_start, covered_end) in failed:
                frames.pop()
                continue
            upper = covered_end + len(needle)
        position = source.rfind(needle, previous_start, upper)
        budget -= 1
        if position == -1 or budget < 0:
            failed.add((index, previous_start, covered_end))
            frames.pop()
            if budget < 0:
                return False
            continue
        frames[-1][3] = position + len(needle) - 1
        frames.append([index + 1, position, max(covered_end, position + len(needle)), None])
    return False


def _uncovered(length: int, spans: Iterable[tuple[int, int]]) -> int:
    """How many of ``length`` positions no span covers."""
    covered = 0
    reach = 0
    for start, end in sorted(spans):
        start = max(start, reach)
        if end > start:
            covered += end - start
            reach = end
    return length - covered


# --- informational flags --------------------------------------------------------


def prose_share(text: str) -> float:
    """Share of ``text``'s characters that are letters or whitespace (1.0 when empty)."""
    if not text:
        return 1.0
    return sum(1 for char in text if char.isalpha() or char.isspace()) / len(text)


def longest_unbroken_run(text: str) -> int:
    """Length of the longest stretch of ``text`` with no whitespace."""
    return max((len(piece) for piece in text.split()), default=0)


# --- messy copies -----------------------------------------------------------------


def make_messy_copies(
    documents: Sequence[Document],
    *,
    changes: Iterable[str] = MESSY_CHANGES,
    count: int = DEFAULT_MESSY_COUNT,
    seed: int = DEFAULT_MESSY_SEED,
    run_length: int = DEFAULT_INJECTED_RUN_LENGTH,
) -> list[Document]:
    """Degrade a seeded handful of ``documents`` in each of the ways ``changes`` names.

    ``count`` documents are drawn with ``seed`` (from the documents sorted by
    source path, so the input order does not matter). Each change except
    ``glue_documents`` makes one copy per drawn document; ``glue_documents`` makes
    one document of all of them, joined end to end. Every copy gets a source path
    of its own (``<path>#messy-<change>``) so the copies can be chunked together
    without colliding, and records the change under ``messy_change``. The same
    documents, changes, count and seed always give the same copies.
    """
    changes = list(dict.fromkeys(changes))
    unknown = [change for change in changes if change not in MESSY_CHANGES]
    if unknown:
        raise ValueError(
            f"Unknown messy-copy change(s) {', '.join(map(repr, unknown))}; "
            f"expected any of: {', '.join(MESSY_CHANGES)}"
        )
    if count < 1:
        raise ValueError(f"count must be at least 1, got {count}")
    if run_length < 1:
        raise ValueError(f"run_length must be at least 1, got {run_length}")
    if not documents:
        return []

    rng = random.Random(seed)
    ordered = sorted(documents, key=lambda document: source_path_of(document) or document.id)
    if count > len(ordered):
        logger.info("Asked for %d messy copies of %d documents; using all", count, len(ordered))
    chosen = rng.sample(ordered, min(count, len(ordered)))

    copies: list[Document] = []
    for change in changes:
        if change == GLUE_DOCUMENTS:
            paths = [source_path_of(document) or document.id for document in chosen]
            copies.append(
                Document(
                    content="\n".join(document.content or "" for document in chosen),
                    meta={
                        "source_path": f"messy/glued-seed{seed}.md#messy-{GLUE_DOCUMENTS}",
                        "messy_change": GLUE_DOCUMENTS,
                        "glued_from": paths,
                    },
                )
            )
            continue
        for document in chosen:
            content = document.content or ""
            if change == STRIP_BLANK_LINES:
                content = "\n".join(line for line in content.splitlines() if line.strip())
            elif change == REMOVE_PUNCTUATION:
                content = "".join(
                    char for char in content if not unicodedata.category(char).startswith("P")
                )
            elif change == INJECT_UNBROKEN_RUN:
                at = rng.randrange(len(content) + 1)
                run = "".join(rng.choices(_RUN_ALPHABET, k=run_length))
                content = content[:at] + run + content[at:]
            path = source_path_of(document) or document.id
            meta = dict(document.meta)
            meta.update(source_path=f"{path}#messy-{change}", messy_change=change)
            copies.append(Document(content=content, meta=meta))
    return copies


# --- the chunk-size report --------------------------------------------------------


def file_sha256(path: Path) -> str:
    """Hex sha256 of a file's bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _settings_from_chunk(chunk: Document) -> ChunkSettings:
    """The settings a chunk records about the strategy that made it."""
    meta = chunk.meta
    return ChunkSettings(
        strategy=meta["strategy"],
        size=meta["chunk_size"],
        overlap=meta["chunk_overlap"],
        unit=meta["chunk_unit"],
    )


def build_report(
    documents: Sequence[Document],
    chunks_by_strategy: Mapping[str, Sequence[Document]],
    *,
    low_prose_share: float = DEFAULT_LOW_PROSE_SHARE,
    unbroken_run: int = DEFAULT_UNBROKEN_RUN,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    """Measure every strategy's chunks of ``documents`` against the shape rules.

    ``chunks_by_strategy`` maps a strategy name to the chunks it produced for
    ``documents``, as the chunker returned them. Chunks are matched to their
    parent by ``source_path``, so every document needs a distinct one.

    Returns a JSON-ready dict: per strategy, the counts (chunks, over max, under
    min, empty, documents with lost text, order errors, low-prose, unbroken run,
    the largest chunk, and truncation per embedder — exact for BGE, an estimate
    for Voyage); per chunk, its strategy, source path, index, length, the rules
    it breaks, the two informational flags and its BGE tokens; and every
    document-level break. A chunk is low-prose when under ``low_prose_share`` of
    its characters are letters or whitespace, and holds an unbroken run when it
    has more than ``unbroken_run`` characters without whitespace. With
    ``manifest_path`` the report records that file's sha256, naming the corpus
    build it measured.
    """
    if not 0 < low_prose_share <= 1:
        raise ValueError(f"low_prose_share must be in (0, 1], got {low_prose_share}")
    if unbroken_run < 1:
        raise ValueError(f"unbroken_run must be at least 1, got {unbroken_run}")

    parents: dict[str, Document] = {}
    for document in documents:
        path = source_path_of(document)
        if path is None:
            raise ValueError(f"document {document.id} has no source_path; the report needs one")
        if path in parents:
            raise ValueError(f"two documents share the source path {path}")
        parents[path] = document

    summaries: dict[str, dict[str, Any]] = {}
    chunk_rows: list[dict[str, Any]] = []
    document_rows: list[dict[str, Any]] = []

    for strategy, chunks in chunks_by_strategy.items():
        by_parent: dict[str, list[Document]] = {path: [] for path in parents}
        for chunk in chunks:
            path = chunk.meta.get("source_path")
            if path not in by_parent:
                raise ValueError(f"{strategy}: a chunk's source path {path!r} is not a document")
            by_parent[path].append(chunk)

        texts = [chunk.content or "" for chunk in chunks]
        bge_tokens = dict(zip(map(id, chunks), count_tokens("bge", texts), strict=True))
        counts = dict.fromkeys(SHAPE_RULES, 0)
        documents_with_lost_text = 0
        low_prose = 0
        unbroken = 0
        largest = 0
        unit = None
        declared_max = None

        for path, parent_chunks in by_parent.items():
            settings = (
                _settings_from_chunk(parent_chunks[0])
                if parent_chunks
                # No chunks: only the lost-text rule can apply, and it has no unit.
                else ChunkSettings(strategy=strategy, size=0, overlap=0, unit=CHAR_UNIT)
            )
            if parent_chunks:
                unit, declared_max = settings.unit, settings.size
            breaks = check_chunks(parents[path], parent_chunks, settings)
            for rule_break in breaks:
                counts[rule_break.rule] += 1
            if any(rule_break.rule == LOST_TEXT for rule_break in breaks):
                documents_with_lost_text += 1
            for rule_break in breaks:
                if rule_break.chunk_index is None:
                    document_rows.append(
                        {
                            "strategy": strategy,
                            "source_path": path,
                            "rule": rule_break.rule,
                            "detail": rule_break.detail,
                        }
                    )

            lengths = _chunk_lengths(
                parents[path].content or "",
                [chunk.content or "" for chunk in parent_chunks],
                settings.unit,
            )
            for index, (chunk, length) in enumerate(zip(parent_chunks, lengths, strict=True)):
                text = chunk.content or ""
                largest = max(largest, length)
                share = prose_share(text)
                run = longest_unbroken_run(text)
                is_low_prose = share < low_prose_share
                has_unbroken_run = run > unbroken_run
                low_prose += is_low_prose
                unbroken += has_unbroken_run
                chunk_rows.append(
                    {
                        "strategy": strategy,
                        "source_path": path,
                        "chunk_index": index,
                        "length": length,
                        "unit": settings.unit,
                        "chars": len(text),
                        "rules_broken": sorted(
                            {b.rule for b in breaks if b.chunk_index == index},
                            key=SHAPE_RULES.index,
                        ),
                        "low_prose": is_low_prose,
                        "prose_share": round(share, 3),
                        "unbroken_run": has_unbroken_run,
                        "longest_run": run,
                        "bge_tokens": bge_tokens[id(chunk)],
                    }
                )

        summaries[strategy] = {
            "unit": unit,
            "declared_max": declared_max,
            "chunks": len(chunks),
            "over_max": counts[OVER_MAX],
            "under_min": counts[UNDER_MIN],
            "empty": counts[EMPTY],
            "documents_with_lost_text": documents_with_lost_text,
            "order_errors": counts[OUT_OF_ORDER],
            "low_prose": low_prose,
            "unbroken_run": unbroken,
            "largest": largest,
            "truncation": {
                name: stats.as_dict() for name, stats in truncation_report(texts).items()
            },
        }

    manifest = None
    if manifest_path is not None:
        manifest = {"path": str(manifest_path), "sha256": file_sha256(manifest_path)}

    return {
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "manifest": manifest,
        "thresholds": {"low_prose_share": low_prose_share, "unbroken_run": unbroken_run},
        "documents": len(parents),
        "strategies": summaries,
        "chunks": chunk_rows,
        "document_breaks": document_rows,
    }


def write_report(report: Mapping[str, Any], out_dir: Path = REPORTS_DIR) -> Path:
    """Write ``report`` as JSON to ``out_dir/<UTC time>.json`` and return the path.

    The name comes from the report's ``created_at``; a second report in the same
    second gets a numbered suffix rather than overwriting the first.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    created = datetime.fromisoformat(report["created_at"]).astimezone(UTC)
    stem = created.strftime("%Y%m%dT%H%M%SZ")
    payload = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    for attempt in range(1000):
        path = out_dir / (f"{stem}.json" if attempt == 0 else f"{stem}-{attempt}.json")
        try:
            with path.open("x", encoding="utf-8") as handle:
                handle.write(payload)
        except FileExistsError:
            continue
        return path
    raise FileExistsError(f"too many reports named {stem} in {out_dir}")


def log_report(report: Mapping[str, Any], *, log: logging.Logger | None = None) -> None:
    """Log the per-strategy counts as a table, one line per strategy."""
    log = log or logger
    header = (
        f"{'strategy':<13} {'chunks':>6} {'over max':>8} {'under min':>9} {'empty':>5} "
        f"{'lost docs':>9} {'order':>5} {'low-prose':>9} {'unbroken':>8} "
        f"{'largest':>14} {'BGE > limit':>12} {'BGE dropped':>11} {'Voyage > (est)':>14}"
    )
    log.info("%s", header)
    for strategy, row in report["strategies"].items():
        truncation = row["truncation"]
        bge = truncation.get("bge", {})
        voyage = truncation.get("voyage", {})
        largest = f"{row['largest']} {row['unit'] or ''}".strip()
        log.info(
            "%-13s %6d %8d %9d %5d %9d %5d %9d %8d %14s %12s %11s %14s",
            strategy,
            row["chunks"],
            row["over_max"],
            row["under_min"],
            row["empty"],
            row["documents_with_lost_text"],
            row["order_errors"],
            row["low_prose"],
            row["unbroken_run"],
            largest,
            bge.get("chunks_over_limit", "-"),
            bge.get("tokens_lost", "-"),
            voyage.get("chunks_over_limit", "-"),
        )
