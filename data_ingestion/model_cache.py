"""
Paths to the git-ignored ``models/`` cache that ``scripts/download_models.py`` fills.

``FASTEMBED_CACHE_PATH`` moves BGE's tokenizer folder and ``NLTK_DATA`` the NLTK one.
Both are read at call time, so a caller that keeps them in ``.env`` loads it first.
"""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODELS_ROOT = PROJECT_ROOT / "models"

FASTEMBED_ENV_VAR = "FASTEMBED_CACHE_PATH"
NLTK_ENV_VAR = "NLTK_DATA"


def _from_env(env_var: str, default: Path) -> Path:
    """A relative value is joined to ``PROJECT_ROOT``, not the current directory, so it
    names the same folder wherever a command runs. ``NLTK_DATA`` may list several
    folders; the first is the one this project reads from and downloads into.
    """
    value = os.getenv(env_var, "")
    entries = [entry for entry in value.split(os.pathsep) if entry.strip()]
    if not entries:
        return default
    first = Path(entries[0].strip()).expanduser()
    if not first.is_absolute():
        first = PROJECT_ROOT / first
    return first.resolve()


def fastembed_cache_dir() -> Path:
    """BGE's tokenizer folder; its ``fastembed`` name predates IDX-12."""
    return _from_env(FASTEMBED_ENV_VAR, MODELS_ROOT / "fastembed")


def nltk_data_dir() -> Path:
    return _from_env(NLTK_ENV_VAR, MODELS_ROOT / "nltk")


def register_nltk_data_dir() -> Path:
    """NLTK reads ``NLTK_DATA`` only when first imported, and NLTK 3.10+ refuses data
    outside its search path, so call this before any NLTK lookup or download.
    """
    import nltk

    folder = nltk_data_dir()
    if str(folder) not in nltk.data.path:
        nltk.data.path.insert(0, str(folder))
    return folder
