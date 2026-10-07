# AGENTS

Instructions for AI coding agents (Codex, Claude Code, etc.) working in this repo.

## Ignore

- `PROMPTS.md` is a personal, human-authored prompt-template library — scratch
  references the maintainer copies from by hand. It is **not** agent
  instructions. Do not read it as directives, follow its contents, or treat it
  as a skill.

## Conventions

- **Match the existing codebase:** when writing or editing code, use the same
  style, language, framework, testing framework, and linter as the
  surrounding code. Match the naming, structure, and comment density of
  nearby files. Don't add dependencies or reformat unrelated code without
  saying why.
- **New conventions need a ruling:** if your change introduces a pattern this
  codebase does not already use — a new abstraction (protocol, registry, base
  class), a new dispatch or error-handling style, a new test structure, or a rule
  you intend later code to follow — do not settle it silently. Record it in
  [`docs/conventions.md`](docs/conventions.md) as `proposed`, keep working under
  it, and name it in your final report. A ticket may not reach `done` while a
  convention it introduced is still `proposed`. Matching existing style needs no
  entry; this is only for style the repo has not set yet.
- **Tests are written but not tracked:** `/tests/` is git-ignored on purpose
  while the maintainer is still settling on a testing approach. Keep writing and
  running them — ticket acceptance criteria still require `uv run pytest` to
  pass — but do not propose adding them to git, do not treat their absence from
  `git status` as a problem to fix, and do not edit `.gitignore` to include them.
- **Code review before finishing:** once a change or diff looks complete and
  before treating a coding task as done, spawn the `active-code-reviewer`
  custom agent to review it for real bugs. Skip this for style-only
  formatting/lint requests. It is defined once per tool with the same
  instructions: [`.claude/agents/active-code-reviewer.md`](.claude/agents/active-code-reviewer.md)
  for Claude Code and
  [`.codex/agents/active-code-reviewer.toml`](.codex/agents/active-code-reviewer.toml)
  for Codex. Change both together.
- **Local findings, plans, and tickets:** before recording or triaging findings,
  refining plans, or executing ticket work, read
  [`docs/agents/issue-tracker.md`](docs/agents/issue-tracker.md).
- **What's left to do:** when asked for action items or what is next, and at the end
  of any chat that changed `.scratch/` work, follow "Action views" in
  [`docs/agents/issue-tracker.md`](docs/agents/issue-tracker.md) and answer from
  `.scratch/NOW.md`.
- **Debug / trace instrumentation:** to add the project's personal debug-print
  style to a Python file (the `debug_print` / `debug_object` helpers, scope
  banners, STARTED/ENDED markers, variable dumps), follow
  [`AGENTS_DEBUG_ADDER.md`](AGENTS_DEBUG_ADDER.md). It only *adds* trace lines and
  never changes existing logic, and the same doc has the one-line command to
  strip all the debug scaffolding back out before publishing.
