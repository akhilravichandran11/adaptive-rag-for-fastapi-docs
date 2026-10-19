# Project commands. Run `just` to list them.

# Personal recipes that stay out of git
import? 'justfile.local'

default:
    @just --list

# Install dependencies, dev extras included
sync:
    uv sync --extra dev

# Download local models into models/ (pass --check to only report)
models *args:
    uv run python scripts/download_models.py {{args}}

test *args:
    uv run pytest {{args}}

# ruff check + ruff format and the repo hooks, over every file
lint:
    uvx pre-commit run --all-files

# Regenerate .scratch/NOW.md
now:
    python3 .scratch/now.py
