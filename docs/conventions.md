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

## Status

- `proposed`: in the code, awaiting a ruling.
- `accepted`: the shape later code should follow.
- `rejected`: to be undone; the entry records what has to change.

## Register

| ID | Convention | Introduced | Status |
|---|---|---|---|
| [C001](#c001-a-backend-field-discriminates-registry-entries-not-the-key-string) | A `backend` field discriminates registry entries, not the key string | IDX-04 | accepted |
| [C002](#c002-protocol-based-structural-typing-with-a-name--factory-registry) | `Protocol`-based structural typing with a name → factory registry | IDX-05 | accepted |
| [C003](#c003-structure-checking-test-parsers-are-stateless-by-design) | Structure-checking test parsers are stateless by design | IDX-02 | accepted |
| [C004](#c004-a-documented-limitation-instead-of-a-global-side-effect) | A documented limitation instead of a global side effect | IDX-05 | accepted |
| [C005](#c005-model-source-ids-are-separate-from-persisted-source-paths) | Model source IDs are separate from persisted source paths | Ticket 14 | accepted |
| [C006](#c006-tests-never-make-a-paid-api-call) | Tests never make a paid API call | IDX-04 | accepted |

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
— `build_document_embedder`, `build_text_embedder`, `cached_tokenizer_path`,
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

**Status:** accepted · **Introduced:** 2026-09-20, IDX-05 · **Where:** `data_ingestion/chunkers/`

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
(`api.openai.com`) or OpenRouter (`openrouter.ai`, added by IDX-12). A test module whose code under test could call one installs a
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
