# Cloud and worktree sessions

Agents also run outside the main checkout: in cloud sessions (Claude Code on
the web, Cursor and Codex cloud agents) and in new local worktrees. This doc
covers their setup and the cloud rules for commits, `.scratch/` and Linear.

## Setup

`scripts/session_setup.sh` prepares a checkout: it links `.scratch`, runs
`uv sync --locked --extra dev`, downloads the local models and installs the
pre-commit hooks (a worktree shares the main checkout's). It finds the repo
from its own path, so it runs from any directory. It stops at once in the main
checkout, the only one with a real `.scratch` folder, and is safe to run
again. A step that fails prints a `session_setup:` line saying what to rerun.

| Session | What runs the script |
|---|---|
| Claude with one repository, cloud or worktree | the SessionStart hook in `.claude/settings.json` |
| Claude cloud with two repositories | the cloud environment's setup script (`bash <code repo>/scripts/session_setup.sh`), since repository hooks don't load |
| Cursor cloud | `install` in `.cursor/environment.json` |
| Cursor worktree | `.cursor/worktrees.json` |
| Codex cloud or worktree | the environment setup script in Codex's settings |

`.scratch` then points at:

- **in a worktree,** the main checkout's `.scratch`, so every session edits the
  one live copy;
- **in the cloud,** a clone of the private scratch repo
  (`adaptive-rag-for-fastapi-docs-scratch`) beside the code repo. Claude and
  Codex attach it as a second repository; Cursor clones it with the access
  `repositoryDependencies` grants. A Claude session with only the code repo
  attached cannot reach the scratch repo, so it has no `.scratch`.

## Commits in the cloud

Commit and push to the branch your tool created (`claude/…`, `cursor/…`,
`codex/…`), then open a draft pull request with the Linear issue ID in its
title. `main` changes only through a pull request the user merges. If your
tool cannot push or open the pull request itself, say so in your final
message. The local no-commit rule in `CLAUDE.md` exists because local chats
share one git index; a cloud session has its own.

## Scratch in the cloud

1. Update the clone before reading it: `git -C .scratch pull --ff-only`. If
   `.scratch` is missing, work from the Linear issue and say so in the pull
   request.
2. Make scratch edits on a branch with the same name as your code branch, push
   it, and open a draft pull request on the scratch repo. That pull request is
   the review: it stands in for the proposal page and the "yes" in chat
   ([`visual-tools.md`](visual-tools.md)).
3. Leave `NOW.md` and `views/` to the user, who regenerates them after merging.

## Linear

Move an issue to In Progress before you start work on it: that status is the
claim, so pick an issue nobody has claimed. Sign each comment with your tool:
`[Claude Code]`, `[Cursor]` or `[Codex]`.
