"""
Download the local model files for the FastAPI Q&A RAG project, or check they are present.

Fills the git-ignored project cache described in ``data_ingestion/model_cache.py``
(``models/fastembed`` and ``models/nltk`` by default). Run it once by hand; the
indexing code only reads from the cache and never downloads on its own. Anything
already present is skipped, so running it again downloads nothing.

Two files are needed. BGE-large itself runs on OpenRouter (IDX-12), so only its
``tokenizer.json`` (711 KB) is fetched, pinned to one snapshot and checked against
its sha256 so token counts never shift; it is used to truncate texts to 512 tokens
and to count them. NLTK's ``punkt_tab`` (15 MB) splits sentences locally.

    uv run python scripts/download_models.py            # download whatever is missing
    uv run python scripts/download_models.py --check    # report only; exit 0 if all present, 1 if not

Environment (optional; read from the shell or the project .env):
    FASTEMBED_CACHE_PATH   Hugging Face cache for the tokenizer (default: <project>/models/fastembed)
    NLTK_DATA              NLTK data folder (default: <project>/models/nltk)
"""

import argparse
import hashlib
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

import nltk
from dotenv import load_dotenv
from huggingface_hub import hf_hub_download, try_to_load_from_cache

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))  # lets the script run by path from any directory

from data_ingestion.embedders import (  # noqa: E402  (needs the path set above)
    EMBEDDERS,
    EmbedderSpec,
)
from data_ingestion.model_cache import (  # noqa: E402
    FASTEMBED_ENV_VAR,
    NLTK_ENV_VAR,
    fastembed_cache_dir,
    register_nltk_data_dir,
)

logger = logging.getLogger(__name__)

# Embedders whose tokenizer is kept locally: BGE-large, run on OpenRouter, whose
# texts are truncated and counted with its own tokenizer. The repo, file, revision
# and sha256 are pinned in its registry entry.
TOKENIZER_EMBEDDERS = ("bge",)

# NLTK package id -> the resource path looked up at load time. Haystack's
# sentence splitting loads the punkt_tab sentence tokenizer.
NLTK_RESOURCES = {"punkt_tab": "tokenizers/punkt_tab"}


@dataclass
class ModelStatus:
    """Whether one model is fully present in the project cache, and where."""

    kind: str  # "tokenizer" or "nltk"
    name: str  # embedder registry name or NLTK package id
    location: Path
    missing: list[str] = field(default_factory=list)

    @property
    def present(self) -> bool:
        return not self.missing

    @property
    def label(self) -> str:
        return f"{self.kind} {self.name}"


def sha256_of(path: Path) -> str:
    """The sha256 of a file, as lowercase hex."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tokenizer_status(spec: EmbedderSpec, cache_dir: Path) -> ModelStatus:
    """Look for an embedder's pinned tokenizer in ``cache_dir`` without touching the network.

    A file whose sha256 differs from the pin counts as missing: its token counts
    would not be the ones the study recorded.
    """
    source = spec.tokenizer
    if source is None:
        raise ValueError(f"Embedder {spec.name!r} names no tokenizer source")
    snapshot = (
        cache_dir / f"models--{source.repo.replace('/', '--')}" / "snapshots" / source.revision
    )
    status = ModelStatus(kind="tokenizer", name=spec.name, location=snapshot / source.filename)
    cached = try_to_load_from_cache(
        repo_id=source.repo,
        filename=source.filename,
        revision=source.revision,
        cache_dir=cache_dir,
    )
    if not isinstance(cached, str):
        status.missing.append(source.filename)
    elif sha256_of(Path(cached)) != source.sha256:
        status.missing.append(f"{source.filename} (sha256 does not match the pin)")
    return status


def nltk_status(package: str, data_dir: Path) -> ModelStatus:
    """Look for an NLTK package in ``data_dir`` only, ignoring NLTK's other search folders."""
    resource = NLTK_RESOURCES[package]
    status = ModelStatus(kind="nltk", name=package, location=data_dir / resource)
    try:
        nltk.data.find(resource, paths=[str(data_dir)])
    except LookupError:
        status.missing.append(resource)
    return status


def collect_status(tokenizer_dir: Path, nltk_dir: Path) -> list[ModelStatus]:
    statuses = [tokenizer_status(EMBEDDERS[name], tokenizer_dir) for name in TOKENIZER_EMBEDDERS]
    statuses += [nltk_status(package, nltk_dir) for package in NLTK_RESOURCES]
    return statuses


def download_tokenizer(spec: EmbedderSpec, cache_dir: Path, *, force: bool = False) -> None:
    """Download an embedder's pinned tokenizer into ``cache_dir`` and check its sha256.

    ``force`` fetches the file again even if one is cached, for a cached copy whose
    hash does not match the pin; otherwise the cached copy would be returned as is.
    """
    source = spec.tokenizer
    if source is None:
        raise ValueError(f"Embedder {spec.name!r} names no tokenizer source")
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = hf_hub_download(
        repo_id=source.repo,
        filename=source.filename,
        revision=source.revision,
        cache_dir=cache_dir,
        force_download=force,
    )
    actual = sha256_of(Path(path))
    if actual != source.sha256:
        raise RuntimeError(
            f"{source.repo}/{source.filename}@{source.revision} has sha256 {actual}, "
            f"not the pinned {source.sha256}; delete {path} and run this script again"
        )


def download_nltk(package: str, data_dir: Path) -> None:
    """Download an NLTK package into ``data_dir``."""
    data_dir.mkdir(parents=True, exist_ok=True)
    if not nltk.download(package, download_dir=str(data_dir), quiet=True, raise_on_error=True):
        raise RuntimeError(f"NLTK reported a failed download for '{package}'")


def log_status(statuses: list[ModelStatus]) -> None:
    for status in statuses:
        if status.present:
            logger.info("present  %s  (%s)", status.label, status.location)
        else:
            logger.warning(
                "MISSING  %s  (%s; missing: %s)",
                status.label,
                status.location,
                ", ".join(status.missing),
            )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download the local model files into the project cache, or check they are present.",
        epilog=(
            f"Environment: {FASTEMBED_ENV_VAR} overrides the tokenizer folder "
            f"(default models/fastembed); {NLTK_ENV_VAR} overrides the NLTK folder "
            "(default models/nltk). Both may be set in the shell or the project .env."
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report which models are present and which are missing, download nothing; "
        "exit 0 if all are present, 1 otherwise",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    # Same format as the curation and Qdrant stages.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )
    # httpx (under huggingface_hub) logs one INFO line per HTTP request.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    load_dotenv(PROJECT_ROOT / ".env")
    tokenizer_dir = fastembed_cache_dir()
    # NLTK 3.10+ refuses to write outside the folders on its search path, so the
    # project folder is registered before anything is checked or downloaded.
    nltk_dir = register_nltk_data_dir()
    logger.info("tokenizer cache: %s", tokenizer_dir)
    logger.info("NLTK data:       %s", nltk_dir)

    statuses = collect_status(tokenizer_dir, nltk_dir)
    log_status(statuses)
    missing = [s for s in statuses if not s.present]

    if args.check:
        if missing:
            logger.warning(
                "%d of %d models missing; run without --check to download them",
                len(missing),
                len(statuses),
            )
            raise SystemExit(1)
        logger.info("All %d models are present.", len(statuses))
        return

    if not missing:
        logger.info("All %d models are already present; nothing to download.", len(statuses))
        return

    for status in missing:
        logger.info("Downloading %s ...", status.label)
        try:
            if status.kind == "tokenizer":
                download_tokenizer(
                    EMBEDDERS[status.name], tokenizer_dir, force=status.location.exists()
                )
            elif status.kind == "nltk":
                download_nltk(status.name, nltk_dir)
            else:
                raise ValueError(f"no downloader for {status.kind!r}")
        except Exception as exc:  # noqa: BLE001  (CLI converts download failures to exit status)
            logger.error("Download of %s failed: %s", status.label, exc)
            raise SystemExit(1) from None

    # Re-check from disk so the log states exactly what was fetched and where.
    statuses = collect_status(tokenizer_dir, nltk_dir)
    fetched = {s.label for s in missing}
    for status in statuses:
        if status.label in fetched and status.present:
            logger.info("Fetched  %s  -> %s", status.label, status.location)
    still_missing = [s for s in statuses if not s.present]
    if still_missing:
        log_status(still_missing)
        logger.error("Some models are still missing after the download.")
        raise SystemExit(1)
    logger.info("All %d models are present.", len(statuses))


if __name__ == "__main__":
    main()
