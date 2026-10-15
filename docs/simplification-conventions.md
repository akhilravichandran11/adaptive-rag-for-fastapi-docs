# Simplification conventions (tracked: `docs/simplification-conventions.md`)

These are code-shape rules for the Python in this repo, so that every line can be read and explained. Each rule was decided once, in a grill session, after asking three things: is it needed, what would an interviewer ask about it, and can it be explained in one sentence?

- **Where these rules apply.** Agents follow them in new code and in simplification tickets. When an S-rule conflicts with a C-entry in `docs/conventions.md`, the S-rule wins, and the C-entry is marked `superseded by S00N`.
- **Open questions** live in `.scratch/code-simplification/questions.md` (private). They don't bind anything until they are decided here.

Format: **ID — rule.** *Why:* one line. *Applies to:* paths. *Decided:* date and session.

---

**S001 — Haystack chunkers stay `@component` classes with `run()` and `output_types`.**
- *Why:* it is the framework's contract for joining a Pipeline. It costs two lines per class, and stages may still call `run()` directly.
- *Applies to:* `data_ingestion/chunkers/`.
- *Decided:* 2026-10-14.

<!-- Next: S002 from Q01 (lru_cache + deepcopy), and so on. -->
