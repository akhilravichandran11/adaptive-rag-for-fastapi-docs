# AGENTS

Instructions for AI coding agents (Codex, Claude Code, etc.) working in this repo.

## Ignore

- `PROMPTS.md`, `PROMPTS_IMPROVED.md`, and `PROMPTS_IMPROVED_RAG.md` and `PROMPTS_ISSUE_TRACKER.md` are personal,
  human-authored prompt-template libraries — scratch references the maintainer
  copies from by hand. They are **not** agent instructions. Do not read them as
  directives, follow their contents, or treat them as skills.

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
- **Code review before finishing:** once a change or diff looks complete and
  before treating a coding task as done, spawn the `active-code-reviewer`
  custom agent (defined in
  [`.codex/agents/active-code-reviewer.toml`](.codex/agents/active-code-reviewer.toml))
  to review it for real bugs. Skip this for style-only formatting/lint
  requests.
- **Local findings, plans, and tickets:** before recording or triaging findings,
  refining plans, or executing ticket work, read
  [`docs/agents/issue-tracker.md`](docs/agents/issue-tracker.md).
- **Debug / trace instrumentation:** to add the project's personal debug-print
  style to a Python file (the `debug_print` / `debug_object` helpers, scope
  banners, STARTED/ENDED markers, variable dumps), follow
  [`AGENTS_DEBUG_ADDER.md`](AGENTS_DEBUG_ADDER.md). It only *adds* trace lines and
  never changes existing logic, and the same doc has the one-line command to
  strip all the debug scaffolding back out before publishing.
