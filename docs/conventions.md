# Code conventions

Patterns this codebase commits to. A finding records something *discovered* — a
corpus defect, a library quirk. A convention records something *introduced*: a
shape later code is expected to copy.

Whoever introduces a pattern the repo does not already use records it here as
`proposed`, keeps working under it, and names it in their final report. The
maintainer rules. A ticket may not reach `done` while a convention it introduced
is still `proposed`.

C001–C004 were backfilled from work that finished before this register existed.
They do not reopen their tickets; the gate applies to tickets started after
2026-09-21.

Matching existing style needs no entry. This is only for style the repo has not
set yet.

> Code-shape rules live in [`simplification-conventions.md`](simplification-conventions.md).
> Where an S-rule there conflicts with an entry here, the S-rule wins and the
> entry is marked `superseded by S00N`. Open code-shape questions
> are tracked privately in `.scratch/code-simplification/questions.md`.

## Status

- `proposed`: in the code, awaiting a ruling.
- `accepted`: the shape later code should follow.
- `rejected`: to be undone; the entry records what has to change.
- `superseded by S00N`: an S-rule in `docs/simplification-conventions.md`
  replaced it; follow the S-rule.

## Register

| ID | Convention | Introduced | Status |
|---|---|---|---|
| [C001](#c001-a-backend-field-discriminates-registry-entries-not-the-key-string) | A `backend` field discriminates registry entries, not the key string | IDX-04 | accepted |
| [C002](#c002-protocol-based-structural-typing-with-a-name--factory-registry) | `Protocol`-based structural typing with a name → factory registry | IDX-05 | superseded by S004 (`Protocol` half) and S012 (registry half) |
| [C003](#c003-structure-checking-test-parsers-are-stateless-by-design) | Structure-checking test parsers are stateless by design | IDX-02 | accepted |
| [C004](#c004-a-documented-limitation-instead-of-a-global-side-effect) | A documented limitation instead of a global side effect | IDX-05 | accepted |
| [C005](#c005-model-source-ids-are-separate-from-persisted-source-paths) | Model source IDs are separate from persisted source paths | Ticket 14 | accepted |
| [C006](#c006-tests-never-make-a-paid-api-call) | Tests never make a paid API call | IDX-04 | accepted |
| [C007](#c007-a-stage-imports-modules-never-another-stage) | A stage imports modules, never another stage | PRT-01 | accepted |
| [C008](#c008-a-multi-name-lookup-reports-every-unknown-name-at-once) | A multi-name lookup reports every unknown name at once | PRT-02 | accepted |
| [C009](#c009-stages-hand-off-through-saved-plain-json-artifacts-with-an-input-fingerprint) | Stages hand off through saved plain-JSON artifacts with an input fingerprint | PRT-03 | accepted |
| [C010](#c010-a-stage-refuses-an-existing-run-folder-and-creates-it-exclusively-before-its-first-write) | A stage refuses an existing run folder and creates it exclusively before its first write | PRT-09 | accepted |
| [C011](#c011-a-module-may-carry-a-guarded-command-line-and-still-be-imported-by-a-stage) | A module may carry a guarded command line and still be imported by a stage | PRT-11 | accepted |
| [C012](#c012-crag-adapters-are-required-constructor-arguments-the-caller-builds-them-once-at-startup) | CRAG adapters are required constructor arguments; the caller builds them once at startup | CRG-07 | accepted |
| [C013](#c013-a-run-of-hundreds-of-paid-calls-writes-its-settings-first-and-may-be-resumed-into-its-own-folder) | A run of hundreds of paid calls writes its settings first and may be resumed into its own folder | PRT-17 | accepted |

---

## C001: A `backend` field discriminates registry entries, not the key string

**Status:** accepted · **Introduced:** 2026-09-20, IDX-04 · **Where:** `data_ingestion/embedders.py`

**The convention, stated.** A registry entry carries the field its consumers
dispatch on. Reading behaviour out of the key string is what makes a new entry
fall through into another entry's branch, and a billed call is the worst way to
find that out.

**What.** `EmbedderSpec` carries `backend: str` (`"openrouter"` or `"voyage"`; `bge`
moved from fastembed to OpenRouter in IDX-12, and the fastembed backend was removed
in IDX-14).
Every dispatch keys off `spec.backend` and ends in `raise _unsupported_backend(spec)`
— `build_document_embedder`, `build_query_embedder`, `cached_tokenizer_path`,
`count_tokens`.

**Clarified 2026-09-29 (IDX-10).** "Ends in" describes the rule, not the position:
each of the four functions raises `_unsupported_backend(spec)` for any backend it
does not handle. `cached_tokenizer_path` does it in a guard at the top, because an
OpenRouter-only body follows; the other three raise as their last line.

**Why.** The first version dispatched on the registry key: `if name == "bge": …
else: <Voyage>`. A third entry added later would have fallen through to the Voyage
branch and fired a **billed API call against a model Voyage does not recognize**.
Keying off an explicit field turns that silent misroute into a `ValueError`.

**What it replaces.** Dispatch on the registry key string.

**Blast radius.** `embedders.py` (one dataclass field, four dispatch sites) and
`tests/data_ingestion/test_embedders.py`. Nothing consumes the registry yet, so reversing is cheap
**today** and gets steadily more expensive as IDX-06 onward wire embedders in.

**Alternatives.** Subclass per backend, or a dict of handler callables. Both are
heavier than one field for two backends; either becomes the better shape at four or
five.

**Note.** This was added beyond the ticket's literal scope and flagged as such.

---

## C002: `Protocol`-based structural typing with a name → factory registry

**Status:** superseded by S004 (`Protocol` half, 2026-10-20) and S012 (registry half, 2026-11-14) · **Introduced:** 2026-09-20, IDX-05 · **Where:** `data_ingestion/chunkers/`

**Ruled before IDX-06 and IDX-07.** Both add a strategy to this package, so both
copy this shape: a `@component` class satisfying `Chunker` structurally, a
zero-arg `build_<name>()` beside the others that imports its module inside the
function body, and one `STRATEGIES` entry.

**What.** `base.py` defines `Splitter` and `Chunker` as `typing.Protocol`s; the five
strategies satisfy them without inheriting. `__init__.py` maps `STRATEGIES`:
name → zero-arg factory (`build_naive_small`, …), each importing its module **inside**
the function body.

**Why.** Protocols let Haystack `@component` classes be typed without a shared base
class fighting Haystack's own machinery. Lazy imports mean naming a strategy does not
pull `onnxruntime` and the NLTK stack into the process.

**What it replaces.** No prior repo precedent — no other `data_ingestion` module uses
`Protocol`, and this is the first factory registry.

**Blast radius.** `chunkers/base.py`, `chunkers/__init__.py`, four strategy modules,
`tests/data_ingestion/test_chunkers.py`. **IDX-06 and IDX-07 will both copy this shape**, so a ruling
before either starts is much cheaper than after.

**Two registry shapes, kept deliberately.** `EMBEDDERS` maps name → frozen dataclass
spec; `STRATEGIES` maps name → factory callable. They stay different because they hold
different things: an embedder spec is data a caller reads (model, dimension, input
limit, and the `backend` of [C001](#c001-a-backend-field-discriminates-registry-entries-not-the-key-string)),
while a chunker is only ever constructed. Wrapping the factories in a `ChunkerSpec` to
make the two match would add a layer nothing reads. A future registry picks whichever
of the two fits what its entries are for.

**Alternatives.** ABC with inheritance (conflicts with Haystack's component
decorator); plain duck typing with no protocol (loses type checking); eager imports
in `__init__.py` (was tried, produced a `RuntimeWarning`).

---

## C003: Structure-checking test parsers are stateless by design

**Status:** accepted · **Introduced:** 2026-09-20, IDX-02 · **Where:** `tests/data_ingestion/test_loading.py`

**What.** `read_structure()` applies three independent regexes per line —
`HEADING_RE`, `FENCE_RE`, `INDENT_RE` — and never tracks whether a line sits inside a
code fence.

**Why.** The first version tracked fence open/close. On `advanced__templates.md` (the
one corpus file with doubled fences, now finding F007) it went out of phase and
**silently under-reported 6 headings instead of 8** — a regression-detection test that
quietly stops detecting. Statelessness trades precision for a guarantee it can never
desync.

**What it replaces.** A stateful fence-tracking parser.

**Cost, stated plainly.** The parser counts a `#` comment inside a code fence as a
heading. Tests assert on that deliberately (`test_loading.py:174`, `:248`). Anyone
reading those assertions cold will think they are wrong.

**Blast radius.** `tests/data_ingestion/test_loading.py` only. Generalizes to any future
structure-preservation test.

**Scope.** Test-only, and `/tests/` is not tracked — this affects no published code.

**Alternatives.** Parse with `markdown_it` (a real parser, but pins test outcomes to a
third-party renderer's version); fix the corpus upstream (that is F007's open
decision, still yours to make).

---

## C004: A documented limitation instead of a global side effect

**Status:** accepted · **Introduced:** 2026-09-20, IDX-05 · **Where:** `data_ingestion/chunkers/__init__.py:26`

**Scope of the ruling.** Repo-wide, not just this package: no module registers
anything process-wide at import time to buy a convenience its importers did not
ask for. The opt-in escape hatch named below gets written when something needs
it — for this case, whenever stage 2 wants pipelines loaded from YAML rather
than built in code.

**What.** Haystack 3.0 will not deserialize a class from a module outside its trusted
allowlist, so `Pipeline.loads()` / YAML round-trips of any pipeline holding these
chunkers will fail. Rather than call `allow_deserialization_module()` at import time,
the package docstring records the limitation.

**Why.** Import-time registration is a process-wide side effect imposed on every
consumer, including ones that only wanted a strategy name. Pipelines are built in code
here, not loaded from YAML, so nothing currently needs it.

**What it replaces.** Nothing — a choice not to add something.

**The convention, stated.** When a fix would take effect globally at import time,
document the limitation and let whoever needs it opt in.

**Blast radius.** None today. It becomes a real blocker the moment pipeline
serialization is wanted — likely whenever stage 2 indexing is built.

**Alternatives.** Register at import (rejected above); expose an explicit
`enable_deserialization()` for callers to invoke — arguably the better shape, not
built because nothing needed it yet.

---

## C005: Model source IDs are separate from persisted source paths

**Status:** accepted · **Introduced:** 2026-09-23, Ticket 14 · **Where:** `data_curation/schemas.py`, `data_curation/stage3_learning_taxonomy_final.py`

**What.** Stage 3 has a dedicated model-facing draft schema with ordered
`source_ids`. The persisted `DatasetFile` and `SynthesisPlan` schemas continue
to require ordered `source_paths`. A before-validator rejects malformed ID
selections without coercion and includes the planned filename in errors.

**Why.** The model may choose relevant sources, but Python must own authoritative
source identity. Integer selection handles avoid asking the model to reproduce
manifest paths.

**What it replaces.** Using the persisted synthesis-plan schema directly as the
model-response schema. Stage 3 uses the draft schema for the model response and
materializes it into the persisted schema before downstream validation.

**Boundary.** IDs are run-local and must never be persisted. The
materialization step resolves them through the current catalog, validates the
existing persisted schema, and retains the existing Stage 3 contract checks.
Stage 4 continues to consume exact source paths.

**Tradeoffs.** Draft file fields duplicate the persisted file fields and must
stay aligned. Deterministic identity does not make semantic selection
deterministic. Schema validation cannot establish catalog membership.

**Blast radius.** `data_curation/schemas.py`,
`data_curation/stage3_learning_taxonomy_final.py`, and
`tests/data_curation/test_stage3_source_identity.py`. No Stage 4 change is required.

**Alternatives.** Exact-path enums still ask the model to return authoritative
paths. Fully deterministic semantic selection requires a separate relevance
policy. Fuzzy path correction and automatic retries are outside this design.

---

## C006: Tests never make a paid API call

**Status:** accepted (user, 2026-09-29) · **Introduced:** 2026-09-20, IDX-04 · **Where:** `tests/data_ingestion/test_embedders.py:50-59`; copied (non-autouse, plus client patches) in `tests/data_curation/test_stage3_source_identity.py:144-146`

**The convention, stated.** No test may reach a paid API: Voyage
(`api.voyageai.com`), Gemini (`generativelanguage.googleapis.com`), OpenAI
(`api.openai.com`), OpenRouter (`openrouter.ai`, added by IDX-12) or Tavily
(`api.tavily.com`, added by CRG-10). A test module whose code under test could call one installs a
guard that makes the call fail the test instead of costing money.

**Scope: paid calls only.** Qdrant, Redis and Opik are not billed per call. A test
may connect to them, locally or in the cloud; this convention does not block them.

**Two forms.**

- *No network at all*, for a test that needs nothing outside the process: patch
  `socket.socket.connect`, `socket.socket.connect_ex` and `socket.create_connection`
  to raise, as `test_embedders.py` does. This also blocks infrastructure and catches
  unintended model downloads (F002's `punkt_tab`), so prefer it whenever the test
  needs no network.
- *Paid hosts only*, for a test that needs Qdrant, Redis or Opik: patch
  `socket.getaddrinfo` to raise for the paid hosts above and pass every other name
  through. Block at name resolution: by the time `connect()` runs, urllib3 passes an
  IP address, so a host check there cannot see the name. (Checked 2026-09-29: this
  stops both `httpx` and `requests` before any connection, and `localhost` still
  resolves.)

**Why.** "Offline" had been asserted only by construction (`local_files_only=True`,
lazy Voyage clients). A guard turns that claim into something a test run proves.

**What it replaces.** Nothing; there was no network guard before IDX-04.

**Cost.** Neither form covers code that runs at collection time, before fixtures
exist; `needs_bge` in `test_embedders.py` uses a filesystem probe for that reason.
The paid-host list lives here, so a new paid provider has to be added to it.

**Blast radius.** Test-only, and `/tests/` is not tracked. Modules whose code cannot
reach a paid API (`test_loading.py`, `test_chunkers.py`, `test_model_cache.py`) need
no guard.

**Deferred.** A shared guard in `tests/data_ingestion/conftest.py` (user,
2026-09-29: not now). Each module installs its own until then.

**Alternatives.** `pytest-socket` (a new dev dependency); library switches such as
`HF_HUB_OFFLINE=1` (cover one library, not the paid APIs); patching each SDK's client
class, as the stage 3 test also does (precise, but must track every client).

---

## C007: A stage imports modules, never another stage

**Status:** accepted (user, 2026-10-11) · **Introduced:** 2026-10-10, PRT-01 (plan D42) · **Where:** `data_ingestion/qdrant_store.py`, `data_ingestion/stage1_setup_qdrant.py`

**The convention, stated.** A stage is a thin script that runs one step of a
pipeline. Code that more than one stage needs lives in a module the stages
import; a stage never imports another stage to borrow its code. An orchestrator
that runs stages in order (`data_curation/stage0_run_pipeline.py`) is not a stage
and may import them.

**Why.** Each stage and module can be read, tested and explained on its own.
Importing a stage drags in its command line and side effects, and loading one
script from another by file path to borrow a single function hides that
dependency from imports, linters and tests.

**What it replaces.** Importing a stage for its helpers, which stage 3 would
have needed to reach stage 1's Qdrant code.

**Blast radius.** `qdrant_store.py` and stage 1's imports today; PRT-03/PRT-04
are written to it. Rejecting it means stage 3 imports stage 1 instead.

**Alternatives.** Copy the Qdrant code into stage 3 (duplication); stage 3
imports stage 1 (rejected above).

---

## C008: A multi-name lookup reports every unknown name at once

**Status:** accepted (user, 2026-10-12) · **Introduced:** 2026-10-12, PRT-02 · **Where:** `data_ingestion/variants.py` (`select_variants`)

**The convention, stated.** A function that checks several user-supplied names
against registries collects every unknown name and raises one `ValueError` that
names each of them and lists the known names. A single-name lookup
(`get_chunker`, `get_spec`) keeps raising on its one name.

**Why.** A command with two typos (`--strategy` and `--embedder`) fails once with
both named, not once per typo.

**What it replaces.** Nothing multi-name existed; the single-name lookups stop
at the first unknown name.

**Blast radius.** `select_variants` and its test; stage 3 and step 6 call it.
Rejecting it means calling `get_chunker`/`get_spec` per name and failing on the
first.

**Alternatives.** Reuse the single-name lookups (fails one typo at a time; three
message styles stay); argparse `choices=` (CLI only, and `select_variants` is also
called by step 6).

---

## C009: Stages hand off through saved plain-JSON artifacts with an input fingerprint

**Status:** accepted (user, 2026-10-12) · **Introduced:** 2026-10-12, PRT-03 (plan D38) · **Where:** `data_ingestion/chunk_snapshot.py`, `data_ingestion/stage2_chunk_corpus.py`

**The convention, stated.** When one stage's output is another stage's input,
the producing stage saves it in its run directory
(`data_ingestion/outputs/<run_id>/`) through a module both stages import, as
JSON Lines (one record per line, JSON values only) so it reads back equal. The
producing stage writes `run_meta.json` last, recording a fingerprint of its
input (here the sha256 of the corpus `manifest.json`). The consuming stage reads
only from a named saved run (`--chunks-run <run_id>`) and refuses one whose
fingerprint no longer matches today's input.

**Why.** A paid or unrepeatable step (semantic's OpenRouter cut points) runs
once, and both embedders index the same chunks across separate runs (D29). The
saved chunks can be opened and compared locally, and each stage can be explained
on its own (C007). The fingerprint stops a handoff from silently describing an
older corpus build, which the corpus commit cannot detect: GDS-02 changed two
documents without changing it.

**What it replaces.** Chunking, embedding and writing in one in-memory pass with
nothing saved. The data_curation stages also save per-run JSON (D12), but they
share one run directory under the orchestrator and check no input fingerprint.

**Blast radius.** `chunk_snapshot.py`, `stage2_chunk_corpus.py` and their tests;
PRT-04's stage 3 is written to read through it. Rejecting it means stage 3
chunks for itself: one pass per embedder, with semantic paid twice and its cut
points never checked for repeatability.

**Alternatives.** One stage with nothing saved; IDX-13's per-document cache
(deferred by the user on 2026-10-05); pickle (unsafe and unreadable); Parquet
(a new dependency).

---

## C010: A stage refuses an existing run folder and creates it exclusively before its first write

**Status:** accepted (user, 2026-10-17) · **Introduced:** 2026-10-16, PRT-09 · **Where:** `data_ingestion/stage2_chunk_corpus.py`, `data_ingestion/stage3_index_chunks.py` (`main`)

**The convention, stated.** A stage that writes into
`data_ingestion/outputs/<run_id>/` exits 1, naming the folder, when that folder
already exists, before it reads any input. Once every input check has passed, it
creates the folder with `mkdir(parents=True)` (no `exist_ok`) right before its
first write, and exits 1 on `FileExistsError`. A run folder then holds one run's
files only.

**Why.** Two runs sharing a folder corrupt each other without a word (F001):
stage 3's `run_meta.json` replaced a chunk run's, two stage-3 passes overwrote one
`index_report.json`, and a stage-2 re-run left a stale `run_meta.json` beside new
chunk files. The early check fails before any paid work. The exclusive create
closes the window between the check and the first write, which for a stage-2
`semantic` run is the whole paid pass. On macOS's case-insensitive disk both also
catch run ids that differ only in case.

**What it replaces.** Writing into the folder through `mkdir(exist_ok=True)`
(`chunk_snapshot.write_chunks`, stage 3's `write_json`), and stage 3's narrower
refusal of a run id equal to `--chunks-run`, which stays for its clearer message.

**Cost.** A run that fails after creating its folder uses up its run id, even when
the folder is empty; the retry needs a new one. The check and the create are
copied in both stages, because C007 bars one importing the other and
`pipeline.py` was outside PRT-09's Touches.

**Blast radius.** Both stages' `main` and their tests (the run-folder, case-only
and race tests); step 6 would copy it if it writes run folders. Rejecting it
means removing both guards from both stages and those tests.

**Alternatives.** The existence check alone (leaves the race window); creating the
folder at the check (an input error after it leaves an empty folder that blocks the
id and looks like a killed run); clearing an existing folder first (destroys an
earlier run's output); a shared `pipeline.new_run_dir(run_id)` helper (the better
home once `pipeline.py` may change, e.g. in SMP-06/07).

---

## C011: A module may carry a guarded command line and still be imported by a stage

**Status:** accepted (user, 2026-10-18) · **Introduced:** 2026-10-18, PRT-11 (plan D46) · **Where:** `retrieval/baseline_rag.py` (`parse_args`, `main`)

**The convention, stated.** A module whose functions other code imports may also be
run by path for one-off use. Its command line lives in `parse_args` and `main` and
runs only under `if __name__ == "__main__"`, so importing the module parses no
arguments and opens no connection. A stage may import such a module like any other
(C007), provided the command line writes no run folder: a file that saves a run is
a stage.

**Why.** Step 6 must query each variant exactly as a single question from the
command line does, and one module for both keeps them the same. A separate script for
one question would be a stage that step 6 may not import (C007), so the pipeline code
would move into a module anyway and leave a script of a few lines around it.

**What it replaces.** No written rule. In `data_ingestion/` a file with a command
line has been a stage, and a module has had none. Elsewhere the shape already occurs:
the chunker modules keep demo `__main__` blocks that take no arguments, and
`data_collection/collect_referenced_code.py` has a `main()` while
`data_collection/pinned_snapshot/collect.py` imports it.

**Cost.** Its command line is one more thing an importer loads and never runs. The
`sys.path` line it used to carry, an import-time side effect C004 rules out, went with
S014.

**Blast radius.** `retrieval/baseline_rag.py` (`parse_args`, `main`) and its `main`
tests; step 6 is to import `build_rag_pipeline` and `ask`.
Rejecting it means moving `parse_args` and `main` into a separate script beside the
module, which imports both functions.

**Alternatives.** A separate thin script (two files for one step, and a script that
is not a stage but looks like one); no command line, asking questions only through
step 6 or a REPL (no quick way to try one question against one variant).

---

## C012: CRAG adapters are required constructor arguments; the caller builds them once at startup

**Status:** accepted 2026-10-20 · **Introduced:** 2026-10-20, CRG-07 (CRAG first-cut plan D4) · **Where:** `crag/orchestrator.py` (`CragOrchestrator.__init__`)

**The convention, stated.** The orchestrator takes every step it calls (the
`retrieve` function and the grader, decomposer, web search, refinement and
generator objects) as a required constructor argument and stores it, nothing
else. It builds no default adapter, opens no client, calls no `warm_up()` and
reads no environment variable. The caller (`crag/run.py`, the backend's startup)
reads the settings, builds each adapter once and passes it in. Each adapter is a
plain object with `run(...)` returning a dict; no `Protocol` or base class
describes it (S004). When an LLM answered, that dict includes `"model"`: the version
the response reports (`model_version`), or the alias that was called when the response
has none (D5, D11), so the orchestrator can record it.

**The step trace.** `CragResult.steps` holds one dict per step that ran, in order:
`{"step", "started", "ended", "counts", "model"?}`. `started` and `ended` are ISO
UTC times, `counts` is a small dict of integers (such as `{"relevant": 3,
"total": 10}`), and `model` is present only when an LLM answered. A step that is
skipped (the decomposer returned no vector or no web queries) has no entry. A
fallback adds a `"fallback"` step.

**Routes.** `CragResult.route` is `"correct"`, `"ambiguous"`, `"incorrect"` or
`"no_documents"` (`NO_DOCUMENTS_ROUTE`: retrieval returned nothing, so nothing was
graded or searched). Any route whose context ends up empty returns the fixed
`FALLBACK_ANSWER` without generating.

**Why.** Creating the orchestrator should cost nothing and touch no network, so
tests pass fakes that record their calls and a web server builds one orchestrator
at startup and reuses it. Settings are read in one place, so an environment
variable cannot silently replace an argument the caller passed.

**What it replaces.** Adapters built inside the orchestrator with
environment-driven defaults: construction opened the LLM and web search clients,
and settings read inside the classes could override the model a caller passed in
(plan fixes 1 and 2). Elsewhere in the repo,
`retrieval/baseline_rag.py` builds its components inside `build_rag_pipeline`.

**Cost.** Every caller writes the construction itself: six adapters in
`crag/run.py` and again in the backend, and a test builds six fakes.

**Blast radius.** `crag/orchestrator.py` and its tests; the wave 2 adapters
(`crag/grader.py`, `decomposer.py`, `web_search.py`, `refinement.py`) take their
clients and settings as arguments for the same reason; `crag/run.py` (CRG-12) and
`backend/app/main.py` (CRG-14) build them; the backend's response model and the
frontend read `route` and `steps` in the shapes above. Rejecting it means the orchestrator
builds defaults when an argument is `None`, and its tests must patch the clients.

**Alternatives.** Optional arguments with defaults built inside (construction
opens clients); a factory function that builds the real
orchestrator from settings (adds a factory while Q09 is open, and the caller still
needs one for fakes); a `ConditionalRouter` pipeline with the adapters as
components (rejected by D8 and D9).

---

## C013: A run of hundreds of paid calls writes its settings first and may be resumed into its own folder

**Status:** accepted (user, 2026-11-10) · **Introduced:** 2026-11-14, PRT-17 (plan D61, D66) · **Where:** `evaluation/generate_results.py` (`main`, `read_resumed_run`, `build_pipelines`, `ask_pairs`)

**The convention, stated.** A run whose job is hundreds of paid calls, each saved as it completes, creates its folder exclusively as C010 says. It then writes `run_meta.json` with its settings right away, appends and flushes each result as it completes, and rewrites `run_meta.json` in a `finally` at the end of each attempt. It may be resumed into its own folder with `--resume <run_id>` alone. The settings come from the saved `run_meta.json`, a half-written last line is cut off, and the attempt asks only the pairs its results file does not hold. Every other stage keeps C009 (`run_meta.json` written last) and C010 (refuse an existing folder). The two exceptions apply only when both hold: the run's calls are paid, and each result is saved on its own, so a stopped run leaves paid work on disk worth keeping.

**Why.** The full answer run is about 700 paid calls, $15–30 and three hours. Without resume, a kill at hour two pays again for what was saved, and a failed pair cannot be filled in alone; with it, a resume is also the retry. The settings go first because a run killed before its end must still have them to resume from.

**What it replaces.** For this script only, C009's "run_meta.json last" and C010's refusal of an existing folder. A fresh run still keeps both halves of C010: the early check, and the exclusive create.

**Cost.** A reader meets two exceptions to the run-folder rules, and a resume has to guard against mixing what two attempts saw:
- **Checked before any paid call:** it refuses a `run_meta.json` that is not this script's, a question file whose sha256 changed, a different `GEMINI_MODEL`, and a query embedder whose recorded settings differ from the saved entry (model, backend, api_model, dimension, query_prefix, input_type).
- **Checked at a collection's first record:** it stops when the collection's chunks now carry a different `index_run` or `chunks_run` than an earlier attempt recorded, before that record is saved.
- **Not guarded:** two resumes of one folder at once would duplicate lines (one process per folder is assumed, not enforced). A kill during the final rewrite can leave `run_meta.json` unreadable, so no crash safety is claimed for it. The model behind the alias may move between attempts: the probe records it and warns, but does not stop.

**Blast radius.** `evaluation/generate_results.py` and its tests. Rejecting it means a stopped run starts again from nothing in a new folder, and failed pairs need a full rerun.

**Alternatives.**
- No resume: pays again for a killed run.
- Resuming into a new folder that reads the old one: two files to score as one run.
- Writing settings last, as C009 does: a killed run would leave nothing to resume from.
