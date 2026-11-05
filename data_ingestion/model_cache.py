"""
Where the offline models live for the FastAPI Q&A RAG project.

Every locally run model has one home inside the project, ``<project>/models/``,
which is git-ignored and filled once by ``scripts/download_models.py``:

    models/
    ├── fastembed/   # BGE's tokenizer.json, Hugging Face cache layout (name predates IDX-12)
    └── nltk/        # NLTK data (the punkt_tab sentence tokenizer)

Each subfolder can be moved with the variable its library already reads:
``FASTEMBED_CACHE_PATH`` for the tokenizer folder and ``NLTK_DATA`` for NLTK. A relative value
is taken from the project root, so the same ``.env`` works from any directory. The
variables are read from ``os.environ`` at call time; callers that keep them in
``.env`` load it first. Code that needs a file imports the paths from here instead
of hardcoding them, and only looks files up in the cache, so it never downloads.
"""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODELS_ROOT = PROJECT_ROOT / "models"

FASTEMBED_ENV_VAR = "FASTEMBED_CACHE_PATH"
NLTK_ENV_VAR = "NLTK_DATA"


def _from_env(env_var: str, default: Path) -> Path:
    """Return the path named by ``env_var`` if it is set and non-empty, else ``default``.

    ``NLTK_DATA`` may hold several directories separated by ``os.pathsep``; the
    first one is the cache this project reads from and downloads into. A relative
    value is joined to ``PROJECT_ROOT`` rather than the current directory, so
    ``models/fastembed`` names the same folder wherever a command runs.
    """
    value = os.getenv(env_var, "").strip()
    if not value:
        return default
    entries = [entry for entry in value.split(os.pathsep) if entry.strip()]
    if not entries:
        return default
    first = Path(entries[0].strip()).expanduser()
    if not first.is_absolute():
        first = PROJECT_ROOT / first
    return first.resolve()


def fastembed_cache_dir() -> Path:
    """Folder holding BGE's tokenizer (``models/fastembed`` by default; the name predates IDX-12)."""
    return _from_env(FASTEMBED_ENV_VAR, MODELS_ROOT / "fastembed")


def nltk_data_dir() -> Path:
    """Folder NLTK reads its data from (``models/nltk`` by default)."""
    return _from_env(NLTK_ENV_VAR, MODELS_ROOT / "nltk")


def register_nltk_data_dir() -> Path:
    """Put ``nltk_data_dir()`` first on NLTK's search path and return it.

    NLTK reads ``NLTK_DATA`` only once, when it is first imported, and otherwise
    searches the home and system folders. Call this before any NLTK lookup (for
    example before building a Haystack sentence splitter) so the project cache is
    found and NLTK does not fetch its own copy. NLTK 3.10+ also refuses to read or
    write data outside the folders on this path, so downloads need it too.
    """
    import nltk

    folder = nltk_data_dir()
    if str(folder) not in nltk.data.path:
        nltk.data.path.insert(0, str(folder))
    return folder
