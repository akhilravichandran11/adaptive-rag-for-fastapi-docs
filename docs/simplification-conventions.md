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
- *Applies to:* all Python. Three pass today: `scripts/download_models.py` (`ModelStatus.present`, `ModelStatus.label`) and `data_ingestion/embedders.py` (`TruncationStats.percent_over_limit`). `data_ingestion/metadata.py`'s `EnrichmentResult.kept_count` and `dropped_count` break it; they change in its `SMP-` ticket, with their one caller in `stage2_chunk_corpus.py` and four test asserts. `@staticmethod`, the rest of Q03, is S010.
- *Decided:* 2026-11-09 (Q03, `@property` half, after comparing `_no_property` copies of `metadata.py` and `download_models.py`).

**S010 — No `@staticmethod`. A helper that uses neither `self` nor `cls` is a module function, placed above the class that calls it.**
- *Why:* a static method is a plain function kept inside a class, and it draws "why is this in the class at all?". A module function says the same thing, can be imported and tested on its own, and its calls drop the `self.`.
- *Applies to:* all Python outside `sandbox/` and `.scratch/`. The one use, `_concept_comparison_key` in `data_curation/stage2_knowledge_domains.py`, stays as it is because that guide stage is deferred and not being edited. `@classmethod` is not covered: Pydantic's before-validators need it.
- *Decided:* 2026-11-10 (Q03, `@staticmethod` half, after comparing a `_no_staticmethod` copy of `stage2_knowledge_domains.py`).

**S011 — Strategies that differ only in their numbers are one class. It takes the strategy name and looks its numbers up in a module table, and tests check what each strategy does, not how many classes there are.**
- *Why:* `NaiveSmallChunker`, `NaiveMediumChunker` and `NaiveLargeChunker` were the same class three times with two numbers changed, which draws "why three classes?". One `NaiveChunker(strategy)` and a three-row size table say the same with one class to read. The only test that needed three classes counted them; a behaviour test now checks that each strategy cuts at its own size.
- *Applies to:* all Python. Today that is `data_ingestion/chunkers/naive.py`, changed together with its three `build_naive_*` factories and `tests/data_ingestion/test_chunkers.py`. The argument is a plain `str` stored under its own name, because Haystack rebuilds a component from its init arguments and cannot serialize a dataclass. `NAIVE_SIZES` maps names to numbers, not to classes or factories, so S012 keeps it.
- *Decided:* 2026-11-10 (Q10, after comparing a one-class copy: the same 1,535 chunks on all 124 docs, and the pipeline still saves and reloads).

**S012 — No registry of factory functions. Code that builds one of several things by name does it in one function with a branch per name, each importing its module inside the branch, and keeps the names it accepts in a tuple beside it. A dict keyed by name is fine when it holds data, such as `NAIVE_SIZES` or the `EMBEDDERS` specs.**
- *Why:* seven `build_*` functions that each imported one class and called it, plus a dict pointing at them, were three layers for one job, and drew "why a factory per strategy?". One `get_chunker(name)` reads top to bottom, still imports a strategy's module only when that strategy is asked for, and gives the same error for an unknown name. A dict of classes was not an option: it would import every strategy module, and the semantic model stack, when the package loads.
- *Applies to:* all Python outside `sandbox/` and `.scratch/`. Today that is `data_ingestion/chunkers/__init__.py`, where `STRATEGIES` becomes a tuple of names, changed together with the two tests that called a factory (`test_chunkers_semantic.py`, `test_chunkers_markdown_ast.py`) and the `Chunker` docstring in `base.py`. C002's registry half is superseded. The imports inside `get_chunker` stay for the reason above, and S013 keeps them.
- *Decided:* 2026-11-14 (Q09, after comparing a one-function copy: the same class and settings for all seven names, no strategy module loaded on import, and all 1,475 tests passing against it).

**S013 — Imports go at the top of the file. One stays inside a function only when moving it up would add half a second or more to importing the file, and some caller imports the file without calling that function.**
- *Why:* an import inside a function hides a dependency until the function runs, and draws "why is this import down here?". At the top it is visible and fails at startup. Some are worth it: `embedders.py` imports in about a quarter of a second, and the callers that only read `EMBEDDERS` would wait about 7 s more if its Voyage and OpenRouter imports moved up. Time a site before keeping it: the median of five fresh-interpreter runs of the exact import statement, after the file's own imports.
- *Applies to:* all Python outside `sandbox/` and `.scratch/`. Of the 21 sites timed, 14 stay: the seven branches of `get_chunker` (S012; a strategy module the package imports also breaks its own `python -m` demo), the four Voyage and OpenRouter imports in `embedders.py` (5.5 s and 1.3 s), `nltk` in `model_cache.py` (1.1 s, and `embedders.py` imports that file), the sentence splitter in `chunk_shape.py` (1.0 s, needed only for `sentence`), and the semantic module in `stage2_chunk_corpus.py` (1.0 s, needed only for `semantic`). Seven moved up: `huggingface_hub` and `tokenizers` in `embedders.py`, `haystack.dataclasses` in `stage1_setup_qdrant.py`, and `huggingface_hub` and `nltk` in `scripts/download_models.py`, where every run uses `nltk` anyway.
- *Decided:* 2026-11-17 (Q05, option (b), after timing every site).

<!-- Next: S014, from the next question decided. -->
