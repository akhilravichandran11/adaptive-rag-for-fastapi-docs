# Ingestion decisions

Why the ingestion side of this project is built the way it is. Each entry records
what was decided, what else was considered, and what it costs. The newest entries
sit at the top.

---

## 2026-11-17 — `fastembed` and `fastembed-haystack` leave the dependencies

**Decision.** `pyproject.toml` and `uv.lock` no longer hold `fastembed` or
`fastembed-haystack`. This reverses the last clause of the 2026-10-04 entry "The local
BGE code path is removed", which kept both for Qdrant's BM25 sparse vectors.

**Why.** Hybrid search was built with in-memory BM25 instead (`retrieval/DECISIONS.md`,
2026-10-26), so nothing imports either package. BGE's tokenizer is read with `tokenizers`
and `huggingface_hub`, and `models/fastembed` keeps its name.

**Consequences.** Moving sparse vectors into Qdrant, which that entry names as its
revisit condition, brings both packages back. Files: `pyproject.toml`, `uv.lock`.

---

## 2026-11-16 — Two simpler heading-based strategies run beside `markdown_ast`

**Decision.** Two strategies join the study after `markdown_ast`, so it compares nine
strategies in 18 collections. `markdown_ast` stays registered and indexed, and stays
the default for CRAG and the backend.

`markdown_sections` (`data_ingestion/chunkers/markdown_sections.py`) has five rules:

1. Split at every heading with Haystack's `MarkdownHeaderSplitter`.
2. A section under 400 characters joins the next one (a short last one joins the one
   before), only when the two fit in 2,000 characters. The joined chunk is labelled
   with the headings both share.
3. A section over 2,000 characters is cut between paragraphs; a fenced code block is
   never cut, whatever its size.
4. The heading path (`Tutorial > Body > Nested Models`) is the `heading_path`
   metadata field only, as in `markdown_ast`, so the two differ only in where they
   cut.
5. When a long section is cut, a paragraph directly followed by a code block is
   packed with it, so the sentence that introduces code stays in its chunk. A code
   block after a heading or another code block is packed on its own.

`markdown_headers` (`data_ingestion/chunkers/markdown_headers.py`) is Haystack's
splitter with settings and no code of ours:
`MarkdownHeaderSplitter(keep_headers=True, header_split_levels=[1, 2],
secondary_split="word", split_length=300, split_overlap=0)`, run through
`split_documents` in `data_ingestion/chunkers/base.py`. It splits at `#` and `##`
headings (deeper headings stay inside their `##` section), cuts a section every 300
words (about 2,000 characters on this corpus), and keeps the heading in the first
piece's text. Both are registered in `data_ingestion/chunkers/__init__.py`, checked in
`data_ingestion/chunk_shape.py`, and reported, free, by `scripts/chunk_size_report.py`.

**Why.** Two of `markdown_ast`'s ideas are well-founded: split at headings, and never
cut a code block (`recursive`, which ignores fences, leaves 198 of its 602 chunks with
an unclosed fence). The rest is hard to defend. Its Python syntax-tree split runs on
20 of the corpus's 784 fenced code blocks, the Python ones over 2,000 characters. Its
name promises a Markdown parse it never does: headings come from
`MarkdownHeaderSplitter`'s line scan and paragraphs from a line scanner. And nothing
joins short sections, so 176 of its 1,137 chunks are under 300 characters, such as a
heading with one sentence under it. `markdown_sections` keeps the two sound ideas,
drops the syntax tree, and adds the join (rule 2) and the lead-in rule (rule 5).
`markdown_headers` is the framework baseline: it shows what those extra rules buy
over Haystack's splitter configured and not extended.

Neither builds a code syntax tree. The corpus is Markdown only, with no standalone
code files, and its largest fenced code block, 5,567 characters, fits Voyage's
32,000-token input whole. BGE's 512-token truncation is reported for every strategy,
not designed around (see the 2026-09-20 entry).

Measured on the full corpus (124 documents, offline, 2026-11-16):

| | `markdown_ast` | `markdown_sections` | `markdown_headers` |
|---|---|---|---|
| Chunks | 1,137 | 927 | 885 |
| Under 300 characters | 176 | 37 | 122 |
| Over its declared maximum | 29 | 27 | 0 |
| Chunks with an unclosed code fence | 0 | 0 | 125 |
| Chunks that open on a code block | 78 | 0 (46 before rule 5) | — |

For 45 of `markdown_ast`'s 78, the chunk before, from the same file, ends on prose,
which is the sentence the code belonged with. `markdown_headers` has no row there:
its cuts fall every 300 words, inside code blocks rather than before them.

**The choice waits for the retrieval results.** Chunk counts do not say which
strategy retrieves better. Which of `markdown_ast` and `markdown_sections` stays, and
whether `markdown_headers` stays as a baseline row, is decided from the recall and
MRR of all 18 collections in one scored results run. Until then `markdown_ast` stays
the default in `crag/run.py` and `backend/app/main.py`.

**Known limitations**, each kept as built on 2026-11-15:

- **`markdown_sections` has 27 chunks over 2,000 characters, by design.** 23 are one
  code block over 2,000 with its lead-in paragraph, 3 are one such block after an
  admonition's bare closing `///` line (rule 5 treats that line as a lead-in), and 1
  is a 2,142-character table with no blank line in it. The largest is 5,802
  characters.
- **4 `markdown_sections` chunks hold only a heading.** The paragraph under the
  heading moved forward with the code block over 2,000 that it introduces.
- **Unusual fences are missed.** `markdown_sections` does not see indented code
  fences, closing fences longer than the opening one, or inline triple backticks at
  the start of a line, and neither does Haystack's splitter. The corpus has none.
- **`markdown_headers` breaks code blocks.** 125 of its 885 chunks end inside an
  open fence. No shape rule checks fence balance, so the chunk-size report shows it
  breaking no rule.

---

## 2026-10-17 — Each embedder is queried the way its maker recommends

**Decision.** A question sent to BGE-large is prefixed with the model's own query
instruction, `Represent this sentence for searching relevant passages: `, and a
question sent to Voyage is embedded with `input_type="query"`. Documents get neither
change: BGE embeds them as they are, and Voyage embeds them with
`input_type="document"`. The prefix is set on BGE's entry in
`data_ingestion/embedders.py` and added in `data_ingestion/openrouter_embeddings.py`,
inside BGE's 512-token limit; nothing was re-indexed. How the retrieved chunks are
then used is recorded in [`retrieval/DECISIONS.md`](../retrieval/DECISIONS.md).

**Why.** The study's questions are short queries matched against long passages, the
case both makers give these instructions for. Giving Voyage its query mode and
not giving BGE its instruction would handicap BGE in the comparison for no reason. It
costs about ten more tokens per BGE query and nothing at indexing.

**Known limitation.** OpenRouter is assumed to pass the prefixed text to BGE unchanged
and to add no instruction of its own; its documentation says nothing either way.

---

## 2026-10-09 — The corpus is chunked once and indexed 14 times

**Decision.** Indexing is two stages with saved chunks between them.
`data_ingestion/stage2_chunk_corpus.py` splits the corpus with each of the seven
chunking strategies and saves every chunk, text and metadata, in its run folder.
`data_ingestion/stage3_index_chunks.py` reads one saved chunk run and embeds it into
one Qdrant collection per strategy and embedder, named
`fastapi_docs__<strategy>__<embedder>`: seven strategies and two embedders (Voyage
`voyage-4-lite` and BGE-large) make 14 collections. Each indexing run rebuilds the
collections it writes, but stops before writing anything if one already exists and
`--recreate` was not given. Every collection is created with four payload indexes:
`bucket`, `source_path` and `knowledge_domain` as keywords, and `quality_score` as a
float.

The first full run, on 2026-10-15, chunked all 124 documents of the normalized
`github-docs/v1` corpus, dropped none, and filled all 14 collections. Each collection
holds exactly its strategy's chunk count:

| Strategy | Chunks, and points in each of its two collections |
|---|---|
| `naive_small` | 842 |
| `naive_medium` | 443 |
| `naive_large` | 250 |
| `sentence` | 621 |
| `recursive` | 602 |
| `markdown_ast` | 1,137 |
| `semantic` | 583 |

That is 4,478 chunks and 8,956 points. BGE on OpenRouter cost about $0.024, including
`semantic`'s cut points; Voyage cost about $0.040 at list price, inside its free
allowance.

**Why 14 collections.** The study compares chunking strategies, and each strategy is
read by both embedders so that "this strategy is better" can be told apart from "this
strategy suits this embedder" (see the 2026-09-20 entry). One collection per pair
makes each comparison a change of collection name and nothing else: the same corpus
build, the same chunks, the same settings. Haystack's Qdrant store also takes one
vector size per collection, and the two embedders' vectors differ (2048 and 1024
dimensions).

**Why chunking and indexing are separate stages.** The two `semantic` collections
compare the embedders fairly only if they hold the same chunks, and `semantic` is not
known to give the same chunks twice: its cut points come from BGE through a paid API,
whether two runs agree has never been checked, and earlier runs, made before the
current corpus build, gave 584 and 587 chunks. Chunking once and saving the result
lets both embedders read the same file, in any order and across any number of runs.
After the full run, all 583 `semantic` chunks held the same text in both collections,
matched by source file and chunk number. Saved chunks can also be opened and compared
without querying Qdrant, and a failed indexing run is repeated from the same chunks
without paying for `semantic` again (about 20 minutes and $0.008 a run). Stage 3
refuses a chunk run made from a different build of the corpus: it compares the sha256
of the corpus manifest recorded at chunking time with today's, so saved chunks cannot
describe older files. Each stage is a thin script with one job; shared logic lives in
modules the stages import, and no stage imports another.

**Why each run rebuilds its collections.** Every point gets a fixed id, a UUID made
from its strategy, embedder, source file and chunk number, so re-running from the
same chunks writes the same points. Rebuilding means a re-run after a chunker change
cannot leave the old version of a changed chunk behind, still retrievable.
`--recreate` is the guard that stops a collection being wiped by accident. Because the
embedder is part of the id, a chunk's BGE and Voyage points have different ids; the
two collections are matched by source file and chunk number instead.

A rebuild empties a collection before refilling it, so whether a collection can be
used is read from the run's `index_report.json`, never from the collection alone.
Each collection's entry is written as `partial` before its first write, and becomes
`complete` only when every batch was written and the collection's point count equals
its chunk count, so a run killed part way leaves `partial` behind. The run's
`run_meta.json` is written last, also when a collection fails or the run is stopped
with Ctrl-C, so it shows that a run ended, not that it succeeded. The first full run
needed this: a Qdrant write timed out and left Voyage's `markdown_ast` collection at
704 of its 1,137 points, its entry said `partial`, and a rebuild with a longer client
timeout completed it.

**Why some metadata fields are indexed.** The Qdrant cluster runs in strict mode,
which refuses a filter on a field that has no payload index. Haystack's
`count_documents_by_filter` catches that refusal and returns 0, so a missing index
looks the same as "no matches" and nothing fails. Four fields are indexed, each for a
use (Haystack keeps metadata under `meta`, so the Qdrant fields are `meta.bucket` and
so on):

- `bucket` (keyword): filter retrieval by section of the docs, such as tutorial,
  advanced or deployment.
- `quality_score` (float): range filters on the curation step's quality score (0 to 10),
  such as 7 or more. It only filters; it never keeps a document out of the index.
- `source_path` (keyword): pull one document's chunks, and match retrieved chunks to
  the source files an evaluation question expects.
- `knowledge_domain` (keyword): nearly constant today (113 of 124 documents share one
  value), but it lets a later router filter by domain without re-indexing.

The offline tests use in-memory Qdrant, which has no strict mode, so only a live run
proves the indexes. All four appear in the schema of each of the 14 collections, and
in a test collection a filter on `meta.bucket` counted all 95 matching chunks instead
of 0.

**Alternatives considered.**

- **One stage that chunks, embeds and writes in one pass,** the shape most tutorials
  use. Less code, but the two `semantic` collections share chunks only when both
  embedders index in the same run, and a failed run means chunking again.
- **A per-document chunk cache inside the semantic chunker.** Shares chunks too, but
  costs about 150 lines, a dozen tests and a new pattern for this codebase.
- **Write into existing collections and sweep stale points after a complete run.**
  Keeps each collection searchable while it is re-indexed, which a study with no live
  users does not need, and it needed an index and guards of its own. It was the
  earlier plan, chosen when Voyage looked expensive; at about $0.04 for all seven
  strategies, a rebuild costs less than the code.
- **Write into existing collections with ids made from chunk content.** A changed
  chunk gets a new id, so its old version stays in the collection and is still
  retrieved.
- **Index only `bucket` and `quality_score`,** the first choice. `source_path` was
  added for evaluation and `knowledge_domain` for routing; both were already in every
  chunk's metadata.

**Consequences.** Each chunk run takes about 10 MB in the git-ignored outputs folder.
A failed or killed run leaves the collection it was writing `partial` until the next
`--recreate` run, which costs cents. A new metadata field, such as a topic label,
needs a new index and a rebuild from a chunk run that carries it. Vectors are dense
only, compared by cosine, so hybrid search will mean new collections rather than
converting these. Filters on the keyword fields should use `==`: Haystack's Qdrant
integration (10.5.0) turns `!=` and `in` on a string with no spaces into a full-text
match, which matches substrings and which strict mode may refuse.

**Timeout follow-up.** The first full run inherited the client's 5-second
timeout. Voyage's write batches (64 points of 2,048 numbers, about 2.6 MB a
request) hit it twice, which is why `markdown_ast` was rebuilt with the timeout
raised to 60 seconds at run time. `QDRANT_TIMEOUT` now sets a positive integer
timeout in seconds for both the collection check and document store; unset or
empty values use the 60-second default. The setting is documented in `.env.example`.

---

## 2026-10-08 — Chunk sizes are checked and reported, not enforced

**Decision.** Every chunking strategy's chunks are held to five shape rules: no chunk
over the strategy's declared size, none under its declared minimum unless it is the
whole document, no text lost (whitespace ignored), chunks in source order, and no empty
chunk. `data_ingestion/chunk_shape.py` checks them, and `scripts/chunk_size_report.py`
writes a chunk-size report for a corpus run: per strategy, how many chunks break each
rule, two informational counts (low-prose chunks and unbroken runs), and how many
chunks go over each embedder's input limit. Nothing is enforced. A chunk that breaks a
rule is counted, never re-split, merged or dropped; whether to fix sizes automatically,
and for which strategies, is a later decision made from the report's numbers.

**Why.** The corpus will change: it may grow, be pulled again, or gain a lower-quality
source, so rules tuned to today's files would only hold for today's files. The shape
rules name no content (no emoji, code fence, file name or encoding), and the tests run
them on bad documents built for the purpose and on messy copies of real ones (blank
lines stripped, punctuation removed, a long unbroken run injected, documents glued
together). Only `semantic` breaks its declared size today: Haystack treats its lengths
as targets, so it returns chunks of over 15,000 characters. `sentence` keeps its six
sentences, but a fenced code block counts as one sentence, so its chunks can reach
15,000 characters too. Both are known and measured.

The first report, on the 2026-10-05 build of the normalized `github-docs/v1` corpus
(124 documents, `manifest.json` sha256 `898fc5bf…`, Haystack 3.2.0), found the five
size-based strategies keeping all five rules. On Haystack 3.0.0 the same run had found
`recursive` losing text in 4 documents, which is why the project now requires 3.2.0.
`semantic` was not re-measured, because its run is paid; its tests mark the two size
rules as known gaps.

**Alternatives considered.**

- **Cap every strategy at BGE's 512 tokens.** Reverses the 2026-09-20 decision below
  and changes what `naive_*` and `recursive` are.
- **Fix sizes automatically now** (re-split oversized chunks, merge tiny ones).
  Deferred until the report shows where it is worth it: it changes chunk ids and means
  re-indexing every strategy it touches.
- **Content filters that drop chunks by what they contain.** Rules tuned to this
  corpus; the report only counts low-prose chunks and unbroken runs.

**Consequences.** A strategy that breaks its declared size is still indexed, so the
report has to be run and read after each corpus pull. The five size-based strategies
run offline and free; `semantic` runs only when asked for, because it calls a paid API.
Indexing does not call the report yet.

**Known limitations**, accepted rather than coded around, each with the number that
justifies it and what would reopen it:

- **`sentence` drops some line breaks.** Haystack's NLTK sentence splitter drops runs of
  blank lines and newlines at some sentence boundaries, welding the words on either side
  together. On the corpus above, 3 of 124 documents have a word that appears whole in no
  `sentence` chunk; only whitespace is lost, so the "no text lost" rule still passes.
  Revisit if a later step needs to map a chunk back to its offset in the file (store
  offsets at split time instead), or a report shows words lost in more than a handful of
  documents.
- **One encoded image.** `advanced__stream-data.md` holds a 4,254-character base64 image
  in a code sample, the corpus's only unbroken run. Each strategy carries it in one
  chunk, except `recursive`, which cuts it at 2,000 characters across three. Stripping
  it at load time would change every strategy's chunks. Revisit if a report finds
  unbroken runs in more than one document, or among the largest chunks of a new corpus.
- **Voyage token counts are an estimate.** They are counted as 1.3 tokens per word, so
  they are a lower bound that understates text with little whitespace. No conclusion
  depends on it today: the longest chunk is 15,647 characters, which BGE's tokenizer
  counts as 4,893 tokens, and even at 2 characters per token it would stay under 8,000,
  far under Voyage's 32,000-token input limit. Revisit if any chunk passes 64,000
  characters, or Voyage's truncation numbers are ever used to make a decision.

---

## 2026-10-04 — The local BGE code path is removed

**Decision.** The fastembed branches that ran BGE-large on the laptop are deleted;
BGE is reached only through OpenRouter. Its tokenizer stays in `models/fastembed`
(the folder keeps its name so nothing on disk moves), and the `fastembed` and
`fastembed-haystack` packages stay as dependencies, because Qdrant's BM25 sparse
vectors (`Qdrant/bm25`) run through them once hybrid search is built.

**Why.** Unused code paths still have to be read, tested and kept in step; after
IDX-12 no registry entry used this one.

**Consequences.** Running BGE locally again would mean restoring the branches from
git history (`91f9ddd` is the last version with them) and re-downloading the 1.2 GB
model. The model file itself is deleted by hand.

---

## 2026-10-04 — BGE-large runs on OpenRouter, not on the laptop

**Decision.** BGE-large (`BAAI/bge-large-en-v1.5`, 1024 dimensions, 512-token input)
stays in the study for both of its jobs (the semantic chunker's boundary model and
the embedder behind the `*__bge` collections), but it now runs on OpenRouter
(`baai/bge-large-en-v1.5`, OpenAI-compatible embeddings endpoint) instead of locally
through fastembed. Only its tokenizer file stays on the laptop.

**Why.** Time: the first full-corpus semantic run took 106 minutes on a MacBook Air
with BGE-large on the CPU, with other applications closed to let it finish, and
embedding every strategy's chunks for the `*__bge` collections would take hours more
on the same machine. A study that is re-run whenever the corpus or a setting changes
cannot wait that long. On OpenRouter the same model costs $0.01 per million tokens,
and one pass over the corpus is about 240K tokens, so the whole study's BGE
embedding costs cents.

**Truncation is ours.** OpenRouter's documentation says input over the model's limit
is "truncated or rejected" without saying which. Every text is cut to 512 BGE tokens
(`[CLS]` and `[SEP]` included) with BGE's own tokenizer before it is sent, and the
number cut is logged, so the result does not depend on what OpenRouter does and a
long code block cannot fail a run. The truncation statistics are unchanged: they use
the same tokenizer file, pinned to the snapshot already cached; its sha256 is
checked when it is downloaded and by `scripts/download_models.py --check`.

**Alternatives considered.**

- **Keep running it locally.** Free and offline, but too slow to re-run.
- **A GPU job (Colab, Modal).** Fast, but a learning curve this project does not need.
- **A smaller BGE variant.** Faster locally, but a different vector size and a weaker
  comparison against Voyage.

**Consequences.** The BGE side of the pipeline is no longer offline or free: both
embedders are now paid APIs (OpenRouter, Voyage), so the "runs offline and free" lines
in the two BGE entries below no longer hold. Tests stay offline and never make a paid
call; they stub the client. Public FastAPI documentation is sent to a third party, and
a single hosting provider means an outage stops embedding until it is back. The
1.34 GB local model file is no longer read and can be deleted.

---

## 2026-09-20 — BGE-large is the second embedder, not the main one

**Decision.** The corpus is indexed twice: once with Voyage `voyage-4-lite`
(2048 dimensions, 32k-token input) as the primary embedder, and once with
`BAAI/bge-large-en-v1.5` (1024 dimensions, 512-token input), run locally through
fastembed. Every chunking strategy is indexed with both, so retrieval quality can be
compared both per strategy and per embedder.

**Why keep a second embedder at all.** A chunking comparison with one embedder
cannot tell "this strategy is better" apart from "this strategy happens to suit this
embedder". Two embedders with very different input limits separate those. BGE also
runs offline and free, so the pipeline stays testable without an API key or spend.

**Why the large variant.** Its 1024 dimensions match the vector size the collections
already use, and it is the strongest of the BGE family that runs comfortably on a
laptop CPU. The cost is size: about 1.2 GB of ONNX weights.

**The 512-token limit is deliberate, and becomes a result.** Chunk sizes are kept at
the sizes each strategy is meant to demonstrate, rather than shrunk to fit BGE. With
those sizes, every strategy has chunks longer than BGE can read, and their tails are
silently dropped when embedded. Counted with BGE's own tokenizer by the first
chunk-size report (2026-10-05 build of the normalized `github-docs/v1` corpus, 124
documents; see the 2026-10-08 entry):

| Strategy | Chunks over BGE's 512-token input limit | Average tokens dropped from each |
|---|---|---|
| `naive_small` | 121 of 842 (14.4%) | 70 |
| `naive_medium` | 352 of 443 (79.5%) | 317 |
| `naive_large` | 217 of 250 (86.8%) | 878 |
| `sentence` | 274 of 621 (44.1%) | 626 |
| `recursive` | 428 of 602 (71.1%) | 99 |

The largest `naive_large` chunk loses about four-fifths of its tokens. Voyage, with
more than 60× the input budget, reads those same chunks whole.

"Big chunks lose their tails on a small-context embedder" is a finding worth
reporting, and designing it away would hide it. The indexing report records, per
variant, how many chunks exceeded the limit and by how much.

*Corrected 2026-10-08.* This entry first gave estimates (69% of `naive_medium` and
70% of `naive_large` chunks over BGE's input limit, with `naive_small` and `recursive`
"comfortably inside"), made before BGE's tokenizer was used to count. Code-heavy text
costs more tokens per word than the estimate assumed.

**Alternatives considered.**

- **Shrink every chunk to fit 512 tokens.** Makes all variants comparable on BGE,
  but changes what each strategy is, and hides the truncation effect.
- **Use only Voyage.** Cheaper to build and no downloads, but no second opinion, no
  offline path, and no truncation contrast.
- **A smaller BGE variant** (`bge-small`, 384 dimensions). Lighter and faster, but a
  different vector size and a weaker comparison against Voyage.

**Consequences.** ~1.2 GB downloaded once into the git-ignored `models/` folder; a
local CPU embedding pass that is slower than the API; and truncation statistics that
must be read alongside BGE's scores whenever the two embedders are compared.

---

## 2026-09-20 — BGE-large also finds the semantic chunker's boundaries

**Decision.** The semantic chunking strategy detects topic changes using BGE-large
locally, cutting where the similarity between neighbouring sentence groups drops
into the largest 5% of drops for that document.

**Why.** Semantic chunking needs an embedding model to *find the cut points*, which
is a separate job from the embedding that is later stored and searched. Choosing
BGE for it means no extra model to download, no API cost for a step that runs on
every re-chunk, and, most importantly, one set of chunks: the Voyage and BGE
variants of the semantic strategy contain exactly the same text, so the comparison
between them isolates the embedder.

**Alternatives considered.**

- **A small sentence-transformer model.** Faster, but another download and a weaker
  sense of where topics change.
- **Voyage.** Paid calls on every re-chunk, and it would not improve Voyage's own
  results, since boundaries are shared by both variants anyway.

**Consequences.** Re-chunking the corpus semantically embeds it an extra time
locally, which is the slowest offline step in the pipeline. The percentile, group
size, and length bounds are recorded in each chunk's metadata so a run can be
reproduced.

---

## 2026-09-20 — Sentence boundaries come from NLTK's Punkt, cached in the project

**Decision.** Sentence detection uses the Punkt tokenizer that the pipeline's
splitters rely on (NLTK's `punkt_tab` data), and that data is downloaded once, ahead
of time, into the git-ignored `models/nltk` folder.

**Which parts need it.** Two of the seven strategies: the sentence strategy, which
groups six sentences per chunk, and the semantic strategy, which splits a document
into sentences before comparing groups of them. The word-based, character-based, and
markdown-structure strategies do not.

**Why a real tokenizer.** The cheap alternative is to cut at every period followed by
a space. On API documentation that is wrong constantly: `app.get(...)`,
`response.headers`, version numbers like `3.11`, and file names such as `main.py`
all contain periods that end nothing. Punkt is trained to tell those from real
sentence ends, so sentence-based chunks stay whole sentences.

**Why cache it in the project.** Left alone, the splitter downloads the data itself,
on first use, into a folder in the user's home directory. That makes a "no network"
run fail in surprising ways, hides a download inside what looks like local work, and
leaves the pipeline's inputs outside the project. Fetching it up front with
`scripts/download_models.py` makes it explicit: one command fills `models/`,
`--check` reports what is present without downloading, and the pipeline only ever
reads from that folder.

The stronger reason is that recent NLTK versions refuse to read or write data
outside the folders on their search path, so the project cache must be registered
before use (`data_ingestion/model_cache.py::register_nltk_data_dir`). Without that
step the cached copy is ignored *and* rejected, and the library falls back to
downloading its own.

**Consequences.** One extra ~15 MB download alongside the embedding model, and one
rule for code that splits sentences: register the project's data folder first.
