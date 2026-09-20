"""
Where the offline models live for the FastAPI Q&A RAG project.

Every locally run model has one home inside the project, ``<project>/models/``,
which is git-ignored and filled once by ``scripts/download_models.py``:

    models/
    ├── fastembed/   # fastembed ONNX models (BGE-large), Hugging Face cache layout
    └── nltk/        # NLTK data (the punkt_tab sentence tokenizer)

Each subfolder can be moved with the variable its library already reads:
``FASTEMBED_CACHE_PATH`` for fastembed and ``NLTK_DATA`` for NLTK. The variables
are read from ``os.environ`` at call time; callers that keep them in ``.env`` load
it first. Code that needs a model imports the paths from here instead of
hardcoding them, and loads with ``local_files_only=True`` so it never downloads.
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODELS_ROOT = PROJECT_ROOT / "models"

FASTEMBED_ENV_VAR = "FASTEMBED_CACHE_PATH"
NLTK_ENV_VAR = "NLTK_DATA"


def _from_env(env_var: str, default: Path) -> Path:
    """Return the path named by ``env_var`` if it is set and non-empty, else ``default``.

    ``NLTK_DATA`` may hold several directories separated by ``os.pathsep``; the
    first one is the cache this project reads from and downloads into.
    """
    value = os.getenv(env_var, "").strip()
    if not value:
        return default
    first = value.split(os.pathsep)[0]
    return Path(first).expanduser().resolve()


def fastembed_cache_dir() -> Path:
    """Folder passed to fastembed as ``cache_dir`` (``models/fastembed`` by default)."""
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
