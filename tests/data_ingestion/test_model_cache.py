"""
Tests for ``data_ingestion.model_cache``: where the offline model folders resolve.

Pure path logic, so it runs offline with no models present. Every test pins the
current directory elsewhere, so a path that silently followed the cwd would fail.
"""

import os
from pathlib import Path

import pytest

from data_ingestion.model_cache import (
    FASTEMBED_ENV_VAR,
    MODELS_ROOT,
    NLTK_ENV_VAR,
    fastembed_cache_dir,
    nltk_data_dir,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def away_from_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(FASTEMBED_ENV_VAR, raising=False)
    monkeypatch.delenv(NLTK_ENV_VAR, raising=False)
    return tmp_path


def test_unset_uses_project_models_folder():
    assert fastembed_cache_dir() == MODELS_ROOT / "fastembed"
    assert nltk_data_dir() == MODELS_ROOT / "nltk"


def test_blank_uses_project_models_folder(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(FASTEMBED_ENV_VAR, "   ")
    assert fastembed_cache_dir() == MODELS_ROOT / "fastembed"


def test_relative_value_resolves_against_project_root(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(FASTEMBED_ENV_VAR, "models/fastembed")
    assert fastembed_cache_dir() == (PROJECT_ROOT / "models" / "fastembed").resolve()


def test_absolute_value_is_kept(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    target = tmp_path / "elsewhere"
    monkeypatch.setenv(FASTEMBED_ENV_VAR, str(target))
    assert fastembed_cache_dir() == target.resolve()


def test_home_value_is_expanded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv(NLTK_ENV_VAR, "~/nltk_cache")
    assert nltk_data_dir() == (tmp_path / "nltk_cache").resolve()


def test_nltk_data_uses_first_entry_relative_to_project(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(NLTK_ENV_VAR, os.pathsep.join(["models/nltk", "/somewhere/else"]))
    assert nltk_data_dir() == (PROJECT_ROOT / "models" / "nltk").resolve()


def test_nltk_data_skips_empty_entries(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    # What NLTK_DATA="$NLTK_DATA:/x" gives when NLTK_DATA was empty. NLTK skips the
    # blank entry, so the cache must too, rather than resolving it to the project root.
    monkeypatch.setenv(NLTK_ENV_VAR, os.pathsep + str(tmp_path / "nltk"))
    assert nltk_data_dir() == (tmp_path / "nltk").resolve()
    monkeypatch.setenv(NLTK_ENV_VAR, os.pathsep)
    assert nltk_data_dir() == MODELS_ROOT / "nltk"
