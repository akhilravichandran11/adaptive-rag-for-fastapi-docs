# Backend decisions

Why the web side of this project is built the way it is. Each entry records what was
decided, what else was considered, and what it costs. Entries are added as the app
grows; the newest sit at the top.

The ids in brackets, such as D4, are the decision ids of the first-cut CRAG plan,
explained in [`crag/DECISIONS.md`](../crag/DECISIONS.md) where they belong to CRAG.

---

## 2026-11-22 — A semantic answer cache on Upstash, off when it cannot start (D53)

**Decision.** `backend/app/cache.py` keeps answers in a free Upstash Redis database, through
Upstash's Python SDK (`upstash-redis`, in `pyproject.toml`) over HTTPS.
`SemanticCache(client)` takes its client from the caller.

- `ensure_index()` creates the search index `answers` over the hashes under `cache:`, with
  one `HYBRID` field, `question`. `EXISTSOK` makes a restart leave an existing index alone.
- `store(question, response)` writes `cache:<sha256 of the question>` with the question and
  the response as JSON, and a 24-hour expiry (`EXPIRY_SECONDS`).
- `lookup(question)` asks for the single nearest question by meaning (`$ann`, limit 1). It
  returns the stored response and the score when the score is at least `THRESHOLD = 0.94`.
  Otherwise it returns `None` beside the nearest score, or `None` with no score when nothing
  is stored.
- `open_cache(url, token, client_factory)` returns `None` and logs "cache off" at INFO
  unless `UPSTASH_REDIS_REST_URL` and `UPSTASH_REDIS_REST_TOKEN` are both set. It returns
  `None` with one WARNING naming the fix when creating the index gets no reply
  (`httpx.TransportError`). That is the only place a cache error is caught: once a cache is
  returned, an error in a lookup or a store raises.
- `scripts/clear_cache.py` deletes every `cache:` key with `SCAN` and `DEL` and logs how
  many it deleted. It keeps the index, which drops deleted keys by itself. It is run by hand
  after a pipeline change that changes answers.
- `.env.example` lists both settings empty, so the cache is off by default.
  `backend/app/main.py` does not call the cache yet.

**Why semantic.** A cache keyed by meaning also catches exact repeats. An exact-match cache
misses every paraphrase, such as "How can I enable CORS in FastAPI?" against "How do I add
CORS middleware in FastAPI?". Keeping both would only save an embedding call on an exact
repeat, and Upstash makes that call anyway.

**Why Upstash, with its own embeddings.** The app runs on a laptop and in cloud sessions
that cannot reach it, which ruled out a local Redis in Docker. Upstash's free tier (250 MB,
500K commands a month) is larger than Redis Cloud's, and it works over HTTPS. Upstash embeds
a `HYBRID` field itself (OpenAI text-embedding-3, included in the price), so the app makes
no embedding call. The costs:
- The index uses Upstash-only `SEARCH.*` commands, because Upstash rejects RediSearch's
  `FT.*`. The cache does not move to another Redis unchanged.
- Upstash sends every stored and every asked question to OpenAI. That is acceptable for
  questions about FastAPI's documentation.
- The threshold had to be set from scratch.

**Why 0.94.** `$ann` scores run from 0 to 1, higher is closer, and Upstash applies no
threshold of its own. A probe on a free database on 2026-11-21 gave two points:
- "How can I enable CORS in FastAPI?" scored 0.953 against "How do I add CORS middleware in
  FastAPI?", which must hit.
- "How do I use Depends?" scored 0.891 against "How do I use Depends with yield?", a
  different question that must not hit.
Unrelated pairs scored about 0.55. 0.94 sits between the two points.

**Why 24 hours.** A day keeps repeat questions within a session or a demo cheap, and it caps
how long a stale answer or a wrong hit can be served when nobody runs the clear script.

**Why an unreachable database turns the cache off.** Upstash may archive or delete a free
database after quiet weeks. Refusing to start would turn that into an app that will not run,
and treating every error as a miss would hide a broken cache for good. So the check runs
once, at startup. If the database cannot be reached, the app logs one WARNING ("make a new
database or unset UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN") and runs without the
cache. An error once the cache is on raises, as every other adapter's does (S005). An error
reply at startup, such as a wrong token, means the database was reached, so it raises too.

**Known limitation.**
- The threshold rests on two pairs. A paraphrase that scores 0.93 misses, and a different
  question that scores 0.95 is served a wrong answer for up to a day. Each lookup returns
  its score, so recorded scores can recalibrate it.
- The `$ann` query shape and `EXISTSOK` come from the SDK and have not yet run against a
  live database.
- A store is two commands. If `EXPIRE` fails after `HSET`, the request raises and the key
  keeps no expiry until the clear script runs.
- The SDK's HTTP client has no timeout and retries once after 3 seconds, so an unreachable
  database delays startup by at least that.

---

## 2026-10-26 — The frontend is one static page served by the same app (D6)

**Decision.** The page is `frontend/index.html` and one script, `frontend/app.js`: plain
HTML and JavaScript, with a short `<style>` block and no framework, library or external
file. `backend/app/main.py` serves it with
`app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")`, where
`FRONTEND_DIR` is the project root's `frontend/`. The mount is the last statement in the
file, so `/query` and `/health` are matched first, and `html=True` serves `index.html` at
`/`. The script calls `POST /query` with `fetch`. It shows the route, the answer, the
numbered sources and any missing aspects; it does not show the step records.

Every value from the response is set with `textContent` or as an attribute, never with
`innerHTML`, and the answer is shown as plain text. A web source becomes a link only when
it starts with `http`, with `rel="noopener"`.

**Why.** One static page needs no build step and no second server. It is served from the
same origin as the API, so it needs no CORS. It sits in `frontend/`, outside `backend/`,
so a visual change touches only `frontend/`. The answer and the sources are built from
untrusted web pages, so they are never parsed as HTML.

**Known limitation.** The mount serves every file in `frontend/`, including git-ignored
local files. That is fine on localhost, not on a public server. A mount at `/` also
answers every path no route matched, so `GET /query` is a 404 rather than a 405, and
`POST /query/` is a 405 rather than a redirect to `/query`.

---

## 2026-10-26 — Two endpoints over one orchestrator built at startup (D4, D10)

**Decision.** `backend/app/main.py` has two endpoints. `GET /health` returns
`{"status": "ok"}`. `POST /query` takes `{"question": ...}` and returns the
orchestrator's `CragResult` as JSON. The `lifespan` builds one `CragOrchestrator` at
startup, on hybrid retrieval, and stores it on `app.state`. The endpoint gets it through
`Depends(get_crag)`, so tests swap in a fake without running startup.

`/query` is a plain `def`, not `async def`, because the orchestrator is synchronous.
FastAPI then runs it in a worker thread, so a long query does not block the server.
An adapter's error is not caught, so it returns 500.

The request and response bodies are Pydantic models in `backend/app/models.py`. The
question is stripped, then must be 1 to 1,000 characters; anything else returns 422
before any paid call. `QueryResponse` mirrors `CragResult` field for field, with
`Source` and `Step` models for its lists. A step with no model returns `"model": null`.

Not built in the first cut: a semantic cache, conversation memory, query-type routing,
the security guards, tracing, progress events, and feedback and metrics endpoints. Each
can wrap `CragOrchestrator.run` or one of the adapters later.

**Why.** Building the orchestrator once means its clients and the BM25 index are made
once, not per request. The dependency lets the endpoint tests run offline. A failed
query should fail loudly while the first cut is checked by hand; a 500 with the
traceback in the server log shows the cause. Mirroring `CragResult` keeps one shape for
both front doors, so the command line and the API return the same record.

**Known limitation.** Every query makes paid calls to Voyage, Gemini and Tavily, and has
no cache. In the 2026-10-26 route check, run from the command line, a query took 30 to
40 seconds on average; on the server it holds one worker thread for that time. Logging
is left to uvicorn; the one INFO line after startup shows only if logging is
configured.

---

## 2026-10-26 — Startup refuses to run without every setting, VOYAGE_API_KEY included

**Decision.** The `lifespan` in `backend/app/main.py` loads `.env` from the project root,
then checks `REQUIRED_SETTINGS`: `GOOGLE_API_KEY`, `TAVILY_API_KEY`, `GEMINI_MODEL`,
`GEMINI_LIGHT_MODEL` and `VOYAGE_API_KEY`. An empty value counts as missing. If any is
missing it raises `RuntimeError("startup: missing settings X, Y; set them in .env")`,
naming all of them at once, and the server does not start. A `ValueError` from
`build_hybrid_pipeline` (no chunk file, no collection, no Qdrant setting) is not caught
either. Importing the module reads no setting and opens no connection; all of it
happens in the `lifespan`.

**Why.** Hybrid retrieval embeds the query and reranks with Voyage. Without the key
check, the server would start and then fail on the first query, inside Haystack, with a
less clear message. Naming every missing setting at once saves one restart per setting.
The command line exits with `SystemExit(1)`, but a server's startup should raise
instead: `RuntimeError` is a built-in exception, and its message names the step and
every missing setting (S005 in
[`docs/simplification-conventions.md`](../docs/simplification-conventions.md)). The
uncaught traceback from the pipeline already names its cause.

---

## 2026-10-26 — The wiring is copied from crag/run.py on purpose

**Decision.** The `lifespan` in `backend/app/main.py` builds the same adapters as `main`
in `crag/run.py`: one Gemini client, a pro and a flash `StructuredLLM`, the Tavily
client and the six adapters. The code is copied, with a one-line comment in the
`lifespan` saying so. The backend differs in three ways: it always uses hybrid
retrieval, it raises `RuntimeError` instead of exiting, and it does not catch the
pipeline's `ValueError`.

**Why.** A shared `build_…()` function would be a factory function. Whether the repo
uses factory functions is an open code-shape question (Q09, named in that comment), and
while it is open no new one is added.

**Known limitation.** There are two copies to keep in step. A ruling on Q09 can remove
one.
