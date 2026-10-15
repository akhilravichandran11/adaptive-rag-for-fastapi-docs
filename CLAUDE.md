# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@AGENTS.md

## Commands

- Setup: `uv sync --extra dev`, then `cp .env.example .env` and fill in keys.
- Local models (BGE tokenizer, NLTK punkt → `models/`): `uv run python scripts/download_models.py` (`--check` to only report).
- Tests: `uv run pytest`. Lint/format: `uvx pre-commit run --all-files` (ruff check + ruff format, not black).
- There is no test or lint CI; the GitHub workflows are advisory LLM PR review/description only. Run the checks above yourself.

## Gotchas

- Tests must never reach a paid API (Voyage, Gemini, OpenAI, OpenRouter) — see C006 in `docs/conventions.md` for the per-module network guard.
- `scripts/chunk_size_report.py --include-semantic` and the semantic chunker make paid calls; don't run them without asking.
- Pre-commit blocks any path containing `akr` (case-insensitive), `.env`, `.scratch`, `data`, `outputs`, `sandbox`, and leftover debug-instrumentation lines. Strip debug prints before committing (see `AGENTS_DEBUG_ADDER.md`).
- Haystack pipelines are built in code; YAML loading fails for the custom chunkers.
- `.scratch/NOW.md` is generated — regenerate with `python3 .scratch/now.py`, never hand-edit it.
- Record ingestion design decisions in `data_ingestion/DECISIONS.md` (newest first).
- The shell is zsh on macOS: a word starting with `=` (an `=====` separator in `echo`) is expanded and fails, an unquoted `$VAR` does not split into words, and there is no `timeout` command. Quote the first two; for the third use a Python or `uv run` timeout.

## Agent teams

- Enabled for this repo via `.claude/settings.json` (`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`). Experimental; teammates spawn only in an interactive terminal session. Claude Desktop sessions are SDK-hosted, so there a named `Agent` call runs as a named background subagent, not a teammate (verified 2026-10-12).
- Two modes, pick per batch. **Desktop fan-out:** a lead chat runs `/batch-lead` with named `ticket-implementer` subagents; the tracker is the task list. (Codex has no agent teams; its own `.agents/skills/batch-lead` is maintained separately, not as a twin of the Claude command.) Use it for independent tickets. **Terminal team:** run `claude` in a terminal with `CLAUDE_CODE_ENABLE_TODO_TOOLS=1` (the task tools are off by default on Claude 5 models); teammates then message each other and claim tasks. Use it only when agents must talk to each other, such as competing-hypothesis debugging or a review debate.
- Either way, give each agent the full task in the spawn prompt — it loads `CLAUDE.md`/`AGENTS.md` but not the lead's conversation. Prefer a single session for sequential or same-file work.
- Agents never commit or stage; the lead hands the user the `git add` line and the message, and the user runs both once every agent is idle. The index is shared by every chat, so a file one chat stages rides into whichever chat commits next. pre-commit stashes every unstaged tracked change in the checkout during a commit, including another chat's, and a live edit can be lost in the restore. Before committing, read `git status --short` and check who owns each unstaged tracked file.
- Each teammate owns a disjoint set of files. Never give two teammates the same module, `DECISIONS.md`, or `docs/conventions.md`.
- The repo gotchas bind teammates too: no paid API calls from tests, don't run the semantic chunker or `--include-semantic` reports, strip debug instrumentation before commits.
- With teams enabled, a *named* `Agent` call (without `fork` or `isolation`) launches a teammate. When you only want the `active-code-reviewer` result back (the AGENTS.md review step), spawn it unnamed so it runs as a plain subagent.
- To opt out for yourself, set it to `"0"` in `.claude/settings.local.json` (git-ignored; overrides the project value). `~/.claude/settings.json` won't work because project settings win over user settings. Edit `.claude/settings.json` only to disable it repo-wide.

## Git

- Conventional Commits with a scope, lowercase imperative subject: `feat(ingestion): …`, `fix(ci): …`, `docs(agents): …`.
- Branches: `feat/<area>`.
