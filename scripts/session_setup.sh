#!/usr/bin/env bash
# Prepare a checkout outside the main one for an agent session: a cloud VM or a
# new local worktree. Links .scratch, installs the dev environment, downloads
# the local models and, outside worktrees, installs the pre-commit hooks.
#
# Usage: bash scripts/session_setup.sh (from any directory)
#
# Stops at once in the main checkout, the only one with a real .scratch folder,
# and is safe to run again. Who runs it where: docs/agents/cloud-sessions.md.

set -euo pipefail
unset CDPATH

scratch_repo=adaptive-rag-for-fastapi-docs-scratch
# The repo is the script's parent folder, whichever directory runs it.
cd "$(dirname "${BASH_SOURCE[0]}")/.."
root=$(pwd -P)

if [ -d .scratch ] && [ ! -L .scratch ]; then
  exit 0
fi

git_dir=$(cd "$(git rev-parse --git-dir)" && pwd -P)
common_dir=$(cd "$(git rev-parse --git-common-dir)" && pwd -P)
if [ "$git_dir" != "$common_dir" ]; then
  # A worktree shares the main checkout's live copy and its git hooks.
  scratch=$(dirname "$common_dir")/.scratch
else
  # A cloud clone keeps the scratch repo beside the code repo.
  scratch=$(dirname "$root")/$scratch_repo
  if [ ! -d "$scratch" ]; then
    GIT_TERMINAL_PROMPT=0 git clone --quiet \
      "https://github.com/akhilravichandran11/$scratch_repo.git" "$scratch" ||
      echo "session_setup: could not clone $scratch_repo; .scratch stays missing"
  fi
fi
if [ -d "$scratch" ]; then
  ln -sfn "$scratch" .scratch
fi

if ! command -v uv >/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
uv sync --locked --extra dev
uv run python scripts/download_models.py ||
  echo "session_setup: models not downloaded; run scripts/download_models.py again"
if [ "$git_dir" = "$common_dir" ]; then
  uvx pre-commit install --install-hooks ||
    echo "session_setup: pre-commit setup failed; run uvx pre-commit install --install-hooks before you commit"
fi
