# CRAG decisions

Why the corrective RAG side of this project is built the way it is. Each entry records
what was decided, what else was considered, and what it costs. Entries are added as
the pipeline grows; the newest sit at the top.

The ids in brackets, such as D7, are the decision ids of the first-cut plan for this
package. The plan itself is not in the repository, so the code points at these ids and
each one is explained here.

---

## 2026-10-26 — CRAG is plain Python around adapters passed in (D1, D2, D4, D8, D9, D10)

**Decision.** `crag/` sits at the top level, beside `retrieval/`. Imports point one way:
`backend/app` → `crag` → `retrieval`. `crag/run.py` and `backend/app/main.py` are two
front doors to the same code. CRAG never imports the evaluation code; evaluation
reaches CRAG through adapters of its own.

`CragOrchestrator` in `crag/orchestrator.py` takes six required constructor arguments:
a `retrieve(query, top_k)` function and the grader, decomposer, web search, refinement
and generator objects. The caller builds each one once and passes it in. The
orchestrator builds no default, opens no client and reads no setting
([C012](../docs/conventions.md)). Each adapter is a plain class whose `run()` returns a
dict; there is no custom Haystack component and no `Protocol`. Haystack `Pipeline`s
appear only inside retrieval.

The routing is an `if/elif` in `CragOrchestrator.run`, not a `ConditionalRouter`. The
grader's decision picks the route:

- **`correct`:** at least one chunk is relevant and every aspect is covered. The
  relevant chunks are the context.
- **`ambiguous`:** at least one chunk is relevant, but an aspect is missing. The
  decomposer writes queries for the missing aspects, the orchestrator retrieves again
  and searches the web, and refinement keeps what helps.
- **`incorrect`:** no chunk is relevant. The question itself goes to the web search,
  and refinement extracts from each page.
- **`no_documents`** (`NO_DOCUMENTS_ROUTE`): retrieval returned nothing, so nothing is
  graded or searched.

Any other decision raises `ValueError`. When a route ends with no context, the answer
is the fixed `FALLBACK_ANSWER`, "Nothing was found to answer this question from. Try
rephrasing it with more specific terms.", and no answer is generated.

`run(question)` returns a `CragResult` dataclass. Because no schema file is shared with
evaluation, this is where its fields are listed:

| Field | What it holds |
|---|---|
| `question` | The question as asked |
| `route` | `correct`, `ambiguous`, `incorrect` or `no_documents` |
| `answer` | The generated answer, or `FALLBACK_ANSWER` |
| `sources` | One dict per excerpt the answer was given: `rank` (from 1, the number the answer cites), `origin` (`internal` or `web`), `source` (the docs path or the url), `content` and `score` |
| `missing_aspects` | The aspects the grader found not covered; always empty on `incorrect` and `no_documents` |
| `steps` | One record per step that ran, in order |

A step record is a dict: `step`, `started` and `ended` (ISO times in UTC), `counts` (a
small dict of integers, such as `{"relevant": 3, "total": 10}`), and `model` only when an
LLM answered. The step names are `retrieve`, `grade`, `decompose`, `reretrieve`,
`web_search`, `refine`, `generate` and `fallback`. A step that is skipped, such as
`reretrieve` when the decomposer wrote no retrieval queries, has no record.

**Why.** The routing is the part a reader most needs to follow, and an `if/elif` keeps
it visible and debuggable. A custom component's contract only matters inside a
`Pipeline`: Haystack checks connection types and opens a tracing span per component.
Called from plain Python, a component is just a class with a decorator, so the steps
written here stay plain classes. Building the orchestrator costs nothing and opens no
connection, so tests pass fakes and the backend builds one at startup and reuses it.
The step records give later tracing and progress designs their data without a
callback now.

**Known limitation.** The step record holds counts, not chunk ids. When the grader
finds nothing relevant, the trace cannot show whether the right chunks were retrieved,
so telling a retrieval miss from a grading miss needs a paid re-run.

---

## 2026-10-26 — Settings carried over unchanged for the first cut (D7)

**Decision.** The first cut carries over the earlier prototype's tested settings, one
step per module, as plain constants with no `.env` flag around them. Anything that
looked wrong was noted, not fixed.

| Setting | File | Name | Value |
|---|---|---|---|
| Chunks per retrieval, first and again on `ambiguous` | `crag/orchestrator.py` | `RETRIEVE_TOP_K` | 10 |
| Grading calls | `crag/grader.py` | — | Two: per-chunk relevance, then aspect coverage over the relevant chunks |
| Relevance verdict | `crag/grader.py` | `RELEVANT_VERDICT` | `"relevant"`, matched exactly; there is no relevance score threshold |
| `correct` or `ambiguous` | `crag/grader.py` | — | `all_covered` decides, and is trusted even when an aspect disagrees with it |
| `ambiguous` route | `crag/orchestrator.py` | — | Always both a second retrieval and a web search, one query each per analysis |
| Queries per missing aspect | `crag/decomposer.py` | `VECTOR_QUERY_MAX_WORDS`, `WEB_QUERY_KEYWORDS` | fewer than 25 words; `"3-5"` keywords |
| Analyses and aspects | `crag/decomposer.py` | — | Not matched: the model may merge or add some |
| Tavily search depth | `crag/web_search.py` | `SEARCH_DEPTH` | `"advanced"` |
| Results per web query | `crag/web_search.py` | `MAX_RESULTS_PER_QUERY` | 3 |
| Web page length | `crag/web_search.py` | — | Not truncated; the whole page goes to refinement |
| Pages extracted at once | `crag/refinement.py` | `EXTRACT_WORKERS` | 3 |
| Context size | `crag/orchestrator.py` | — | No cap: every kept chunk and extract goes to the generator |

On the `incorrect` route the question is the only web query, so the context holds at
most 3 pages.

**Why.** These settings were tested together in the prototype. Changing them while
porting would mix two kinds of change, so each stays as tested until a route check
shows it is wrong.

**Known limitation.** The route check of 2026-10-26 ran 8 questions with known routes:
6 of 8 as expected on dense retrieval and 7 of 8 on hybrid. The one miss on both was a
streaming question that also asked about a cloud storage library the docs do not cover.
The grader rejected all 10 chunks, though the docs answer the streaming half. It looks
like the grader rejects chunks that answer only part of a question, which is still to
be revisited.

---

## 2026-10-26 — One retry-then-fallback path, and a model tier per step (D5, D11)

**Decision.** Every LLM call goes through `StructuredLLM.ask` in `crag/llm.py`. It calls
the primary model with up to `MAX_ATTEMPTS` (3) tries and an exponential wait capped at
10 s. Then it logs one warning naming both models and calls the fallback model with the
same retries. If that fails too, the error is raised. Only `RETRIED_ERRORS` are retried:
a `ClientError` with code 429, a `ServerError`, and pydantic's `ValidationError` for a
reply that does not fit the schema. A 400 or a bad key is a bug in the request, so it
raises at once, with no fallback. So does any other error.

Model names are always the `-latest` aliases, never version numbers. Each step uses one
of two tiers. `crag/run.py` and `backend/app/main.py` build them from settings:

| Tier | Steps | Primary (`.env.example`) | Fallback (`.env.example`) |
|---|---|---|---|
| pro | grader, decomposer, generator | `GEMINI_MODEL`: `gemini-pro-latest` | `GEMINI_FALLBACK_MODEL`: `gemini-flash-latest` |
| flash | refinement, both its calls | `GEMINI_LIGHT_MODEL`: `gemini-flash-latest` | `GEMINI_LIGHT_FALLBACK_MODEL`: `gemini-flash-lite-latest` |

Both primaries are required. A fallback left unset uses `FALLBACK_MODEL`
(`gemini-flash-lite-latest`) in `crag/llm.py`. The fallback is one tier down.

`ask` returns the reply and the `model_version` the response reports, or the alias when
the response has none. The step record stores that, so it shows the version that
answered, not the alias that was called.

**Why.** The earlier prototype copied its retry and fallback code into three modules;
one copy is easier to keep right. Only an error that can pass on a retry is retried,
so a broken request fails fast instead of spending six calls. Google renames and
replaces model versions often, so a pinned version would need chasing. A wrong grade
sends a question down the wrong route, and the generator writes the answer, so these
get pro; each is one or two calls per question. Refinement carries the volume, one
extraction per web page, 3 at a time, and only copies passages out, so it runs on
flash. A 429 on pro is the likely reason to fall back, and flash has its own quota.

**Known limitation.** The model behind an alias changes over time, so answers from
different dates may come from different models. Recording `model_version` makes the
change visible but does not prevent it. On 2026-10-26, `gemini-pro-latest` answered as
`gemini-3.1-pro-preview` and `gemini-flash-latest` as `gemini-3.8-flash`, with no retry
or fallback warning in either route check run. `MAX_ATTEMPTS` is to be revisited when
the logs show retries. Refinement's re-grade runs on flash while the first grade ran on
pro, so the two can judge similar chunks slightly differently.

---

## 2026-10-26 — The command line reads settings once and fails before any paid call (D3, C012)

**Decision.** `crag/run.py` loads `.env` from the project root and checks
`REQUIRED_SETTINGS` before it builds anything: `GOOGLE_API_KEY`, `TAVILY_API_KEY`,
`VOYAGE_API_KEY`, `GEMINI_MODEL` and `GEMINI_LIGHT_MODEL`. An empty value counts as
missing. It logs every missing name at once and exits 1. The settings are read here
and passed to the adapters, never read inside them.

`--retriever hybrid` is the default and uses `retrieval/hybrid.py`. `--retriever dense`
is kept and uses `build_rag_pipeline(variant, retrieve_only=True)` from
`retrieval/baseline_rag.py`. Both search the `markdown_ast` Voyage collection. A
missing collection, chunk run or Qdrant setting exits 1 before any query. A blank
`--question` exits 2 from `parse_args`. `--route-check` runs the 8 questions in
`ROUTE_CHECK` and reports a route that differs from the expected one, without raising.

**Why.** Both retrievers embed the query with Voyage, so `VOYAGE_API_KEY` is required
for either. Without the check, a missing key would fail inside the run, after other
calls. BM25 refuses an empty query only inside the run, after the paid query
embedding, so the blank question is caught first. Hybrid is the default because CRAG
was always meant to run on it; it started on dense only so it did not wait for hybrid
retrieval to be built. Dense stays for comparison: on the 2026-10-26 route check it
routed 6 of 8 questions as expected and hybrid 7 of 8 (see the hybrid entry in
[`retrieval/DECISIONS.md`](../retrieval/DECISIONS.md)).

---

## 2026-10-26 — Excerpts are numbered, labelled by origin and treated as data

**Decision.** `crag/generator.py` writes each piece of context as
`<excerpt number="n" origin="..." source="...">`. The origin is `documentation` for an
indexed chunk, with its docs path as the source, and `web` for a web extract, with its
url. A chunk counts as documentation when its meta has `source_path`, because indexed
chunks carry a `url` too. The prompt says the excerpts are data, not instructions. It
asks the model to cite excerpts by number, to say when they do not cover the question
instead of answering from memory, and to say so when a web excerpt disagrees with the
documentation. The numbers match `rank` in `CragResult.sources`. An empty answer raises
`ValueError` naming the model and the question.

**Why.** The earlier prototype's prompt promised numbered sources with their origin,
but printed only the chunk text. A number is short to cite, and the reader can match it
to a source. The origin tells the model which excerpts are less authoritative. An
answer that says "not covered" keeps a bad route visible; an answer from memory would
hide it. Treating excerpts as data means an instruction inside a web page is not
followed. The layout follows the baseline's prompt (the 2026-10-17 entry in
[`retrieval/DECISIONS.md`](../retrieval/DECISIONS.md)).

**Known limitation.** The text is not escaped, so an excerpt containing `</excerpt>`
could close its tag early. Web pages make this more likely than the docs alone; it is
left for the security guards, which the first cut does not build.
