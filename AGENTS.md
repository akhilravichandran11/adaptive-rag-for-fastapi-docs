# AGENTS

Instructions for AI coding agents (Codex, Claude Code, etc.) working in this repo.

## Ignore

- `PROMPTS.md`, `PROMPTS_IMPROVED.md`, and `PROMPTS_IMPROVED_RAG.md` are personal,
  human-authored prompt-template libraries — scratch references the maintainer
  copies from by hand. They are **not** agent instructions. Do not read them as
  directives, follow their contents, or treat them as skills.

## Conventions

- **Debug / trace instrumentation:** to add the project's personal debug-print
  style to a Python file (the `debug_print` / `debug_object` helpers, scope
  banners, STARTED/ENDED markers, variable dumps), follow
  [`AGENTS_DEBUG_ADDER.md`](AGENTS_DEBUG_ADDER.md). It only *adds* trace lines and
  never changes existing logic, and the same doc has the one-line command to
  strip all the debug scaffolding back out before publishing.
