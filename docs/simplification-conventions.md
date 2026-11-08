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

**S004 — No `Protocol` or ABC classes.**
- *Why:* only a type checker reads a `Protocol`, and none runs here (no mypy or pyright), so it would be documentation nothing checks. A seam is a constructor's or function's arguments, and tests passing fakes prove it works.
- *Applies to:* all Python. `chunkers/base.py`'s `Splitter` and `Chunker` go in their `SMP-` ticket. Revisit if a type checker is added.
- *Decided:* 2026-10-20 (CRAG first-cut grill, Q02; repo-wide by the same grill's Q12).

**S005 — Failures raise built-in exceptions with a message naming the step and the input; no custom exception classes until a caller must catch one kind apart from the others.**
- *Why:* where nothing catches a failure by type, a class hierarchy is code with no reader. When a caller does need it (a retry on one error type, an HTTP status per failure), add one class for that caller, as `data_curation/openai_llm_utils.py`'s `EmptyStructuredResponse` does for tenacity.
- *Applies to:* all Python. `data_ingestion/metadata.py`'s three classes (tests use the subclasses 11×) change in its `SMP-` ticket.
- *Decided:* 2026-10-20 (CRAG first-cut grill, Q04; repo-wide by Q12).

**S006 — A `@dataclass` only for a record passed between functions, and not `frozen` unless something relies on it never changing (a dict key, a set member, an object shared across threads). Pydantic models where data comes from outside (LLM replies, API request and response bodies).**
- *Why:* a dataclass checks nothing at runtime, so it is for data this code builds; Pydantic validates, so it is for data this code receives. `frozen` adds a rule to explain that most records never use.
- *Applies to:* all Python. The 10 frozen dataclasses are revisited in their files' `SMP-` tickets.
- *Decided:* 2026-10-20 (CRAG first-cut grill, Q06; repo-wide by Q12).

**S007 — No `functools` caches (`lru_cache`, `cache`, `cached_property`) and no `copy.deepcopy`. Load an expensive object once, in the caller, a component's `warm_up()` or the app's `lifespan`, and pass it in.**
- *Why:* a cache adds a key, a size and a staleness rule to explain, and a cached object shared by callers then needs a defensive copy. Loading once per batch costs little (BGE's tokenizer loads in about 13 ms), and the caller can see where it happens.
- *Applies to:* all Python outside `tests/`, where copying a fixture with `deepcopy` is fine. Two places break it today: `data_ingestion/embedders.py` (`_tokenizer_from_file`, `bge_tokenizer`) and `data_ingestion/chunk_shape.py` (`_sentence_splitter`). They change in their files' `SMP-` tickets.
- *Decided:* 2026-10-27 (Q01, after the `embedders.py` simplifier comparison run).

**S008 — No `from __future__ import annotations`. A hint naming something not defined yet, such as a class naming itself, is quoted.**
- *Why:* the repo requires Python 3.11, which handles every hint it uses (`list[str]`, `str | None`) without the line. Removing it made no real difference to the tests or import speed, and only one test hint had to gain quotes.
- *Applies to:* all Python outside `sandbox/`, `.scratch/` and `experiments/`, tests included. `experiments/curated_corpus_ab_pilot.py` stays as it is because each run records its script's hash. Revisit when the minimum Python reaches 3.14, which no longer needs the quotes.
- *Decided:* 2026-11-05 (Q07, after testing both options on scratch copies).

**S009 — A `@property` only for a value built from other fields that reads better than the expression it replaces, or holds logic of its own such as a guard. Never a bare wrapper around a field or `len()`.**
- *Why:* `status.present` reads better than `not status.missing`, and a `label` built from `kind` and `name` cannot go stale, but `result.kept_count` only renames `len(result.documents)` and draws "why not just `len()`?". Python can turn an attribute into a property later without touching callers, so none is added in advance.
- *Applies to:* all Python. Three pass today: `scripts/download_models.py` (`ModelStatus.present`, `ModelStatus.label`) and `data_ingestion/embedders.py` (`TruncationStats.percent_over_limit`). `data_ingestion/metadata.py`'s `EnrichmentResult.kept_count` and `dropped_count` break it; they change in its `SMP-` ticket, with their one caller in `stage2_chunk_corpus.py` and four test asserts. `@staticmethod`, the rest of Q03, is still open.
- *Decided:* 2026-11-09 (Q03, `@property` half, after comparing `_no_property` copies of `metadata.py` and `download_models.py`).

<!-- Next: S010, from the next question decided. -->
