"""
Corpus and curation metadata for the FastAPI Q&A RAG project.

Every document that reaches the index carries where it came from and what the
curation pass said about it. Two read-only sources supply that, joined on the
document's ``output_filename``:

* ``data/fastapi/normalized/github-docs/v1/manifest.json`` — ``source_path``,
  ``bucket``, ``url`` per file, and the corpus commit (``source.ref``).
* ``data_curation/outputs/<run>/content_analysis.json`` — ``analysis.overall_quality``,
  ``knowledge_domain``, and ``suspected_prompt_injection`` per file.

The curation run is always named by the caller; there is no "newest run" default,
so a study can always say which analysis its index was built from. Documents the
run flagged as a suspected prompt injection are dropped here, each one logged, and
the drop count comes back to the caller.

Gaps are errors, never silent: a missing run folder, a missing file, or a loaded
document with no manifest or curation entry raises and names what is missing.

    from data_ingestion.metadata import enrich_documents

    result = enrich_documents(documents, curation_run="20260914_140559")
    logger.info("kept %d, dropped %d", len(result.documents), result.dropped_count)
"""

from __future__ import annotations

import dataclasses
import json
import logging
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from haystack import Document

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# The normalized corpus and its manifest (D30); the whole ``data/`` tree is git-ignored.
CORPUS_ROOT = PROJECT_ROOT / "data" / "fastapi" / "normalized" / "github-docs" / "v1"
MANIFEST_PATH = CORPUS_ROOT / "manifest.json"

# One folder per curation run; each holds the stage 1 ``content_analysis.json``.
CURATION_OUTPUTS_ROOT = PROJECT_ROOT / "data_curation" / "outputs"
CONTENT_ANALYSIS_FILENAME = "content_analysis.json"

# Document metadata key the join runs on. Loading puts the corpus file name here.
FILENAME_KEY = "output_filename"


class MetadataError(Exception):
    """Base error for anything that would leave a document's metadata incomplete."""


class MissingInputError(MetadataError):
    """A manifest or curation file is absent, unreadable, malformed, or missing a needed field."""


class UnmatchedDocumentError(MetadataError):
    """A loaded document has no entry in the manifest or in the curation run."""


@dataclass(frozen=True)
class ManifestEntry:
    """Where one corpus file came from."""

    source_path: str
    bucket: str
    url: str


@dataclass(frozen=True)
class Manifest:
    """The corpus manifest: the commit it was built from, and one entry per file."""

    corpus_commit: str
    entries: Mapping[str, ManifestEntry]


@dataclass(frozen=True)
class CurationEntry:
    """What the curation run concluded about one corpus file."""

    quality_score: float
    knowledge_domain: str
    suspected_prompt_injection: bool


@dataclass(frozen=True)
class CurationRun:
    """One curation run's per-file analysis, keyed by ``output_filename``."""

    run_id: str
    entries: Mapping[str, CurationEntry]


@dataclass
class EnrichmentResult:
    """Documents that survived enrichment, plus the ones curation quarantined."""

    documents: list[Document]
    dropped_filenames: list[str]

    @property
    def kept_count(self) -> int:
        return len(self.documents)

    @property
    def dropped_count(self) -> int:
        return len(self.dropped_filenames)


def _read_json(path: Path, label: str) -> Any:
    """Read a JSON file, turning an absent file or bad JSON into ``MissingInputError``."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise MissingInputError(f"{label} not found: {path}") from None
    except OSError as exc:
        raise MissingInputError(f"{label} could not be read: {path} ({exc})") from None

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise MissingInputError(f"{label} is not valid JSON: {path} ({exc})") from None


def _require(container: Mapping[str, Any], key: str, where: str) -> Any:
    """Return ``container[key]``, or raise naming both the field and where it was expected."""
    if not isinstance(container, Mapping) or key not in container:
        raise MissingInputError(f"missing '{key}' in {where}")
    return container[key]


def _require_filename(record: Any, where: str, seen: Mapping[str, Any], source: str) -> str:
    """Return a record's ``output_filename``, refusing a non-string or repeated one."""
    filename = _require(record, FILENAME_KEY, where)
    if not isinstance(filename, str):
        raise MissingInputError(f"'{FILENAME_KEY}' must be a string in {where}")
    if filename in seen:
        raise MissingInputError(f"duplicate '{FILENAME_KEY}' '{filename}' in {source}")
    return filename


def _check_run_id(run_id: str) -> None:
    """Refuse a run id that is not a single folder name, before it is joined as a path."""
    if not isinstance(run_id, str) or run_id in ("", ".", "..") or "/" in run_id or "\\" in run_id:
        raise MissingInputError(f"curation run id must be a single folder name, got {run_id!r}")


def load_manifest(manifest_path: Path | None = None) -> Manifest:
    """Read the corpus manifest into a per-filename index.

    Raises ``MissingInputError`` if the file is absent or unreadable, or if the
    corpus commit, the file list, or any field this project needs is missing.
    """
    path = Path(manifest_path) if manifest_path is not None else MANIFEST_PATH
    payload = _read_json(path, "Manifest")

    source = _require(payload, "source", f"manifest {path}")
    corpus_commit = _require(source, "ref", f"manifest source in {path}")
    files = _require(payload, "files", f"manifest {path}")
    if not isinstance(files, list):
        raise MissingInputError(f"'files' in manifest {path} must be a list of entries")

    entries: dict[str, ManifestEntry] = {}
    for position, record in enumerate(files):
        where = f"manifest entry {position} in {path}"
        filename = _require_filename(record, where, entries, f"manifest {path}")
        entries[filename] = ManifestEntry(
            source_path=_require(record, "source_path", f"manifest entry '{filename}' in {path}"),
            bucket=_require(record, "bucket", f"manifest entry '{filename}' in {path}"),
            url=_require(record, "url", f"manifest entry '{filename}' in {path}"),
        )

    logger.info("Manifest %s: %d files at commit %s", path, len(entries), corpus_commit)
    return Manifest(corpus_commit=corpus_commit, entries=entries)


def load_curation_run(
    run_id: str,
    curation_outputs_root: Path | None = None,
) -> CurationRun:
    """Read one curation run's ``content_analysis.json`` into a per-filename index.

    ``run_id`` is always explicit: a run folder is named, never guessed, so the
    metadata a document carries can be traced back to one analysis.

    Raises ``MissingInputError`` if the run folder or the analysis file is absent,
    or if an entry is missing a field this project needs.
    """
    _check_run_id(run_id)
    root = (
        Path(curation_outputs_root) if curation_outputs_root is not None else CURATION_OUTPUTS_ROOT
    )
    run_folder = root / run_id
    if not run_folder.is_dir():
        raise MissingInputError(f"Curation run folder not found: {run_folder}")

    path = run_folder / CONTENT_ANALYSIS_FILENAME
    payload = _read_json(path, f"Curation analysis for run '{run_id}'")
    if not isinstance(payload, list):
        raise MissingInputError(
            f"Curation analysis for run '{run_id}' must be a list of entries: {path}"
        )

    entries: dict[str, CurationEntry] = {}
    for position, record in enumerate(payload):
        where = f"curation entry {position} in {path}"
        filename = _require_filename(record, where, entries, f"curation run '{run_id}' ({path})")
        named = f"curation entry '{filename}' in {path}"
        analysis = _require(record, "analysis", named)

        # Stage 3's score and flag type checks, plus a finiteness check (JSON allows NaN
        # and Infinity; stage 3's gap is data-curation F020) and a domain check it lacks.
        quality_score = _require(analysis, "overall_quality", f"analysis of {named}")
        is_number = isinstance(quality_score, (int, float)) and not isinstance(quality_score, bool)
        try:
            finite = is_number and math.isfinite(quality_score)
        except OverflowError:  # a JSON integer too large to convert to float
            finite = False
        if not finite:
            raise MissingInputError(f"'overall_quality' must be a number in analysis of {named}")
        knowledge_domain = _require(record, "knowledge_domain", named)
        if not isinstance(knowledge_domain, str) or not knowledge_domain.strip():
            raise MissingInputError(f"'knowledge_domain' must be a non-empty string in {named}")
        flagged = _require(record, "suspected_prompt_injection", named)
        if not isinstance(flagged, bool):
            raise MissingInputError(
                f"'suspected_prompt_injection' must be true or false in {named}"
            )

        entries[filename] = CurationEntry(
            quality_score=quality_score,
            knowledge_domain=knowledge_domain,
            suspected_prompt_injection=flagged,
        )

    logger.info("Curation run %s: %d analysed files from %s", run_id, len(entries), path)
    return CurationRun(run_id=run_id, entries=entries)


def document_filename(document: Document) -> str:
    """Return the corpus file name a document is joined on.

    Loading records it as ``output_filename``; the base name of Haystack's own
    ``file_path`` is accepted as a fallback. A document with neither cannot be
    matched, so it raises rather than being indexed without provenance.
    """
    filename = document.meta.get(FILENAME_KEY)
    if filename:
        return str(filename)

    file_path = document.meta.get("file_path")
    if file_path:
        return Path(str(file_path)).name

    raise UnmatchedDocumentError(
        f"document {document.id} has no '{FILENAME_KEY}' (or 'file_path') to join on"
    )


def attach_metadata(
    documents: Iterable[Document],
    *,
    manifest: Manifest,
    curation: CurationRun,
) -> EnrichmentResult:
    """Stamp each document with its manifest and curation metadata, dropping quarantined ones.

    Every kept document gains ``source_path``, ``bucket``, ``url``,
    ``corpus_commit``, ``quality_score``, ``knowledge_domain``, and
    ``curation_run``; existing metadata is preserved. Documents the run flagged as
    a suspected prompt injection are left out and logged by name.

    Returns new ``Document`` objects (the originals are untouched) keeping their
    original ids: Haystack derives an id when a document is built, so enrichment
    does not renumber documents that were already loaded.

    Raises ``UnmatchedDocumentError`` for a document with no file name to join on,
    or with no entry in the manifest or in the run.
    """
    kept: list[Document] = []
    dropped: list[str] = []

    for document in documents:
        filename = document_filename(document)

        manifest_entry = manifest.entries.get(filename)
        if manifest_entry is None:
            raise UnmatchedDocumentError(f"no manifest entry for document '{filename}'")

        curation_entry = curation.entries.get(filename)
        if curation_entry is None:
            raise UnmatchedDocumentError(
                f"no entry for document '{filename}' in curation run '{curation.run_id}'"
            )

        if curation_entry.suspected_prompt_injection:
            dropped.append(filename)
            logger.warning(
                "Dropping '%s': curation run %s flagged it as a suspected prompt injection",
                filename,
                curation.run_id,
            )
            continue

        enriched = dataclasses.replace(
            document,
            meta={
                **document.meta,
                "source_path": manifest_entry.source_path,
                "bucket": manifest_entry.bucket,
                "url": manifest_entry.url,
                "corpus_commit": manifest.corpus_commit,
                "quality_score": curation_entry.quality_score,
                "knowledge_domain": curation_entry.knowledge_domain,
                "curation_run": curation.run_id,
            },
        )
        kept.append(enriched)

    logger.info(
        "Attached metadata from curation run %s: %d documents kept, %d dropped",
        curation.run_id,
        len(kept),
        len(dropped),
    )
    return EnrichmentResult(documents=kept, dropped_filenames=dropped)


def enrich_documents(
    documents: Iterable[Document],
    *,
    curation_run: str,
    manifest_path: Path | None = None,
    curation_outputs_root: Path | None = None,
) -> EnrichmentResult:
    """Load both metadata sources and attach them to ``documents``.

    The convenience entry point over ``load_manifest``, ``load_curation_run``, and
    ``attach_metadata``; callers that already hold the indexes (for example one
    that enriches several batches) should use those directly.
    """
    manifest = load_manifest(manifest_path)
    curation = load_curation_run(curation_run, curation_outputs_root)
    return attach_metadata(documents, manifest=manifest, curation=curation)
