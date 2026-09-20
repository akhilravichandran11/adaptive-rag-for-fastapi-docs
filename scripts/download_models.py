"""
Download the offline models for the FastAPI Q&A RAG project, or check they are present.

Fills the git-ignored project cache described in ``data_ingestion/model_cache.py``
(``models/fastembed`` and ``models/nltk`` by default). Run it once by hand; the
indexing code only reads from the cache and never downloads on its own. Anything
already present is skipped, so running it again downloads nothing.

    uv run python scripts/download_models.py            # download whatever is missing (BGE-large is ~1.2 GB)
    uv run python scripts/download_models.py --check    # report only; exit 0 if all present, 1 if not

Environment (optional; read from the shell or the project .env):
    FASTEMBED_CACHE_PATH   fastembed model folder (default: <project>/models/fastembed)
    NLTK_DATA              NLTK data folder (default: <project>/models/nltk)
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))  # lets the script run by path from any directory

from data_ingestion.model_cache import (  # noqa: E402  (needs the path set above)
    FASTEMBED_ENV_VAR,
    NLTK_ENV_VAR,
    fastembed_cache_dir,
    register_nltk_data_dir,
)

logger = logging.getLogger(__name__)

# BGE-large: the secondary embedder and the semantic chunker's boundary model.
FASTEMBED_MODELS = ("BAAI/bge-large-en-v1.5",)

# Files fastembed needs, besides the ONNX weights, to load a text model.
FASTEMBED_TOKENIZER_FILES = (
    "config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
)

# NLTK package id -> the resource path looked up at load time. Haystack's
# sentence splitting loads the punkt_tab sentence tokenizer.
NLTK_RESOURCES = {"punkt_tab": "tokenizers/punkt_tab"}


@dataclass
class ModelStatus:
    """Whether one model is fully present in the project cache, and where."""

    kind: str  # "fastembed" or "nltk"
    name: str  # fastembed model name or NLTK package id
    location: Path
    missing: list[str] = field(default_factory=list)

    @property
    def present(self) -> bool:
        return not self.missing

    @property
    def label(self) -> str:
        return f"{self.kind} {self.name}"


def fastembed_status(model_name: str, cache_dir: Path) -> ModelStatus:
    """Look for a fastembed model's files in ``cache_dir`` without touching the network."""
    from fastembed import TextEmbedding
    from huggingface_hub import try_to_load_from_cache

    spec = next(m for m in TextEmbedding.list_supported_models() if m["model"] == model_name)
    repo = spec["sources"]["hf"]
    needed = [spec["model_file"], *spec["additional_files"], *FASTEMBED_TOKENIZER_FILES]

    status = ModelStatus(
        kind="fastembed", name=model_name, location=cache_dir / f"models--{repo.replace('/', '--')}"
    )
    for filename in needed:
        cached = try_to_load_from_cache(repo_id=repo, filename=filename, cache_dir=cache_dir)
        if isinstance(cached, str):
            status.location = Path(cached).parent  # the snapshot folder fastembed loads from
        else:
            status.missing.append(filename)
    return status


def nltk_status(package: str, data_dir: Path) -> ModelStatus:
    """Look for an NLTK package in ``data_dir`` only, ignoring NLTK's other search folders."""
    import nltk

    resource = NLTK_RESOURCES[package]
    status = ModelStatus(kind="nltk", name=package, location=data_dir / resource)
    try:
        nltk.data.find(resource, paths=[str(data_dir)])
    except LookupError:
        status.missing.append(resource)
    return status


def collect_status(fastembed_dir: Path, nltk_dir: Path) -> list[ModelStatus]:
    statuses = [fastembed_status(name, fastembed_dir) for name in FASTEMBED_MODELS]
    statuses += [nltk_status(package, nltk_dir) for package in NLTK_RESOURCES]
    return statuses


def download_fastembed(model_name: str, cache_dir: Path) -> None:
    """Download a fastembed model into ``cache_dir`` and load it once to prove it works."""
    from fastembed import TextEmbedding

    cache_dir.mkdir(parents=True, exist_ok=True)
    TextEmbedding(model_name=model_name, cache_dir=str(cache_dir))


def download_nltk(package: str, data_dir: Path) -> None:
    """Download an NLTK package into ``data_dir``."""
    import nltk

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
        description="Download the offline models into the project cache, or check they are present.",
        epilog=(
            f"Environment: {FASTEMBED_ENV_VAR} overrides the fastembed folder "
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
    fastembed_dir = fastembed_cache_dir()
    # NLTK 3.10+ refuses to write outside the folders on its search path, so the
    # project folder is registered before anything is checked or downloaded.
    nltk_dir = register_nltk_data_dir()
    logger.info("fastembed cache: %s", fastembed_dir)
    logger.info("NLTK data:       %s", nltk_dir)

    statuses = collect_status(fastembed_dir, nltk_dir)
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
            if status.kind == "fastembed":
                download_fastembed(status.name, fastembed_dir)
            else:
                download_nltk(status.name, nltk_dir)
        except Exception as exc:  # noqa: BLE001  (CLI converts download failures to exit status)
            logger.error("Download of %s failed: %s", status.label, exc)
            raise SystemExit(1) from None

    # Re-check from disk so the log states exactly what was fetched and where.
    statuses = collect_status(fastembed_dir, nltk_dir)
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
