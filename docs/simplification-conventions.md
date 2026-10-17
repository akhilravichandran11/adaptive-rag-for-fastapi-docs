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

**S002 — A comment or docstring stays only if it answers *why* and the code cannot show it, in three lines or fewer.**
- *Why:* a *what* comment repeats the code and goes stale with it. Longer rationale moves to the package's `DECISIONS.md` (one per top-level package, dated entries newest first, each naming its files), with a one-line pointer in the code. Test: would I say it to an interviewer?
- *Applies to:* all Python. Agents delete *what* comments and flag longer *why* comments; the user moves the rationale.
- *Decided:* 2026-10-17 (Q11).

**S003 — A named step of a long function, called once by the original in order, is not a one-use helper.**
- *Why:* it is how a reader finds their place in a long function. Inline only a wrapper that forwards its arguments or whose name adds nothing. This stops the simplifier's split and the simplification skill's inline from undoing each other.
- *Applies to:* all Python.
- *Decided:* 2026-10-17 (setup-review grill, Q7).

<!-- Next: S004 from Q01 (lru_cache + deepcopy), and so on. -->
