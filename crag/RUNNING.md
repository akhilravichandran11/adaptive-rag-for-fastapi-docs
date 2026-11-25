# Running crag

Run every command from the project root. The CRAG pipeline has two front doors over the
same code: the command line `crag/run.py`, and a FastAPI server that answers over HTTP and
serves a web page at `/`. Both search the `markdown_ast` Voyage collection. The result
names the route the question took (`correct`, `ambiguous`, `incorrect` or
`no_documents`); [`DECISIONS.md`](DECISIONS.md) explains each.

Every question is **paid** and takes about 30 to 40 seconds. Voyage embeds it and reranks
the chunks, and Gemini grades the chunks and writes the answer. On the `ambiguous` and
`incorrect` routes, Tavily also searches the web and Gemini extracts from the pages.

## Before the first run

```bash
uv sync --extra dev
cp .env.example .env          # then fill in the settings below
```

| Setting in `.env` | Used for |
| --- | --- |
| `QDRANT_URL`, `QDRANT_API_KEY` | dense search in Qdrant |
| `VOYAGE_API_KEY` | embedding the question and reranking the chunks |
| `GOOGLE_API_KEY` | every Gemini call |
| `GEMINI_MODEL` | grading, writing follow-up queries and answering (`gemini-pro-latest` in `.env.example`) |
| `GEMINI_LIGHT_MODEL` | re-grading chunks and extracting from web pages (`gemini-flash-latest`) |
| `TAVILY_API_KEY` | web search |
| `GEMINI_FALLBACK_MODEL`, `GEMINI_LIGHT_FALLBACK_MODEL` | optional: the model tried when the primary keeps failing; unset means `gemini-flash-lite-latest` |

A missing setting stops the run before any paid call, and the error names it: the
command line exits 1, and the server does not start.

## What must already exist

Both front doors read two things that data ingestion builds (see
[`data_ingestion/RUNNING.md`](../data_ingestion/RUNNING.md)):

- the Qdrant collection `fastapi_docs__markdown_ast__voyage`;
- the chunk file `data_ingestion/outputs/full_chunks_20261015/chunks/markdown_ast.jsonl`,
  which BM25 searches in memory. The run id is fixed in `retrieval/hybrid.py`
  (`DEFAULT_CHUNKS_RUN`), and the collection must be indexed from that same run.

To build both (stage 3 is **paid**):

```bash
uv run python data_ingestion/stage2_chunk_corpus.py --strategy markdown_ast --curation-run 20260925_032632 --run-id full_chunks_20261015
uv run python data_ingestion/stage3_index_chunks.py --chunks-run full_chunks_20261015 --strategy markdown_ast --embedder voyage
```

If either is missing, the command line exits 1 naming it, and the server does not start.
`--retriever dense` needs only the collection. Where both already exist, skip the build:
stage 2 refuses a run id whose folder exists, and stage 3 stops at an existing collection
unless given `--recreate`.

## Check retrieval alone (cheaper)

Hybrid retrieval is the first step of every question. On its own it makes only Voyage
calls, with no Gemini or Tavily, so it is a cheaper check that the collection and the
chunk file are in place:

```bash
uv run python retrieval/hybrid.py --question "How do I add CORS middleware?"             # the 10 reranked chunks
uv run python retrieval/hybrid.py --question "How do I add CORS middleware?" --compare   # also the BM25 and dense lists
```

## Command line

```bash
uv run python crag/run.py --help
```

Pass either `--question` or `--route-check`.

```bash
# answer one question on hybrid retrieval: BM25 + dense, fused and reranked (the default)
uv run python crag/run.py --question "How do I add CORS middleware?"

# the same question on dense retrieval only, to compare
uv run python crag/run.py --question "How do I add CORS middleware?" --retriever dense

# the 8 fixed questions, each route compared with the expected one: 8 paid runs, about 5 minutes
uv run python crag/run.py --route-check
```

The result is logged as JSON on stderr: the question, the route, the answer, its
numbered sources, the missing aspects, and one record per step that ran, with the model
version for each LLM step. `--route-check` logs each result, then one `match` or
`MISMATCH` line per question and a count such as `route check: 7 of 8 routes as
expected`. A mismatch is reported, not raised, because the routes are LLM judgments. A
blank `--question` exits 2 before anything runs.

For example, if the answer cites `[2]`, find the entry with `rank` equal to `2`
in the `sources` array. Its `source` field identifies the documentation path or
web URL, and its `content` field contains the second excerpt the generator saw.

## Cost records

Both the retrieval and CRAG CLIs support optional cost recording with
`--costs-dir`. Each command below requires a fresh output directory:

```bash
uv run python retrieval/hybrid.py --question "How do I add CORS middleware?" --costs-dir /tmp/retrieval-costs
uv run python crag/run.py --question "How do I add CORS middleware?" --costs-dir /tmp/crag-costs
```

Each path must name a new directory. `calls.jsonl` holds one flushed row per
completed observable SDK call: provider, operation, model, outcome, selected numeric
usage, estimated USD and a reason when USD is null. Each provider row also has
`started_at` and `ended_at`, ISO UTC times measured around the SDK invocation,
including an invocation that raises. They exclude response conversion, recording
delay and later JSON/schema validation. `outcome=response` means the SDK returned;
it does not mean the returned JSON was valid.

Gemini rows carry one opaque `logical_call_id` per `ask`, an `attempt` number
starting at 1 and continuing across retries and fallback, and `model_role`
(`primary` or `fallback`). Concurrent asks have distinct IDs; adjacent rows need
not belong to the same ask. The model field still names the resolved response
model, or the requested alias when no resolved model is available.

`summary.json` totals known USD and unknown-cost counts by provider and overall,
and gives the price date, basis and sources. Its `started_at` and `ended_at` mark
recorder creation and finalization, including local work and retry waits but
excluding checks before recorder creation. Even a zero-call failure has this
interval. `terminal_run_failed` is the CLI's final outcome; `run_failed` is true
when the run failed or any provider error was observed. A recovered error can
therefore leave `run_failed=true` and `terminal_run_failed=false`. Zero is measured
zero; null is unknown. Neither a failed run nor a partly priced run establishes a
complete bill.

Prices are standard text-request estimates for `voyage-4-lite`, `rerank-2.5`,
`gemini-3.1-pro-preview` (up to 200,000 prompt tokens), `gemini-3.8-flash` and
`gemini-3.5-flash-lite`. Gemini aliases require a supported resolved model; missing
usage, other models, modalities and tiers stay unpriced. Flash 3.8 promotional rates
expire after 2026-12-31 and then stay unpriced until refreshed. Cached prompt tokens
are subtracted from ordinary input; candidate and thinking tokens are separate
output counts. Tavily credits use the labelled $0.008/credit PAYG reference, not
this account's invoice. Free allowances, discounts and cache storage are excluded.

The integration contract is `record, finish = create_recorder(directory)` from
`observability.costs`. Call `record` with keyword arguments `provider` (`voyage`,
`gemini`, `tavily`), `operation` (`embedding`, `rerank`, `generation`, `search`),
`model` (null for Tavily), `outcome` (`response` or `error`) and `usage` (a selected
numeric dictionary, or null). Voyage supplies `total_tokens`; Tavily supplies
`credits`; Gemini supplies `prompt_token_count`, `candidates_token_count` and any
reported `cached_content_token_count`, `thoughts_token_count`, `total_token_count`
or `tool_use_prompt_token_count`. Omit absent optional fields. `service_tier` and
`modality` default to `standard` and `text`; pass other values when applicable so
they cannot receive a text/standard price. After workers join, call
`finish(run_failed=...)` in `finally`. A callback error or summary failure raises;
an append/flush failure prevents a misleading successful summary.

`record` also accepts optional `started_at`, `ended_at`, `logical_call_id`,
`attempt` and `model_role` keyword arguments. Supply measured ISO UTC timestamp
strings at the provider boundary; the recorder does not invent missing times or
identities. Omitted or null optional values are left out of the row. Direct
accounting callers can keep omitting them, but their untimed rows cannot describe
provider latency. Both callbacks still return `None`.

No prompts, answers, document content or exception text belong in these records.
Hidden SDK retries fall within the measured invocation; their separate charges
and calls interrupted before a response may be missing. A killed
process may leave only partial JSONL without a summary. There is no recovery or
invoice reconciliation. Recording is available only for single-question Voyage hybrid
CLI runs; HTTP, dense/BGE modes, route-check batches and Opik export remain outside
this scope. Commands without recording retain their existing behavior.

## Server and web page

```bash
uv run uvicorn backend.app.main:app --port 8000
```

Startup loads `.env`, checks the settings, reads the chunk file and checks that the
collection exists; it makes no paid call. The server always uses hybrid retrieval. Open
<http://127.0.0.1:8000/>, type a question and press Ask: the page shows the route, the
answer, the numbered sources and any missing aspects.

| Path | What | Cost |
| --- | --- | --- |
| `GET /` | the web page | free; each question asked on it is paid |
| `POST /query` | takes `{"question": "..."}`, returns the command line's result as JSON | **paid** |
| `GET /health` | `{"status": "ok"}` | free |
| `GET /docs` | FastAPI's interactive API docs | free; trying `/query` there is paid |

```bash
curl http://127.0.0.1:8000/health
curl -X POST http://127.0.0.1:8000/query -H "Content-Type: application/json" -d '{"question": "How do I add CORS middleware?"}'
```

`/query` strips the question, which must then be 1 to 1,000 characters; anything else
returns 422 before any paid call. A failed step returns 500, with the traceback in the
server log. Keep the server on localhost: the page's mount serves every file in
`frontend/`, git-ignored ones included.
