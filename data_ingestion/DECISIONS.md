# Ingestion decisions

Why the ingestion side of this project is built the way it is. Each entry records
what was decided, what else was considered, and what it costs. Entries are added as
the pipeline grows; the newest sit at the top.

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
15,000 characters too. Both are measured, not oversights.

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
stays in the study for both of its jobs — the semantic chunker's boundary model and
the embedder behind the `*__bge` collections — but it now runs on OpenRouter
(`baai/bge-large-en-v1.5`, OpenAI-compatible embeddings endpoint) instead of locally
through fastembed. Only its tokenizer file stays on the laptop.

**Why.** Time. The first full-corpus semantic run took 106 minutes on a MacBook Air
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
compared per strategy **and** per embedder.

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

That contrast is the point: "big chunks lose their tails on a small-context
embedder" is a finding worth reporting, and designing it away would hide it. The
indexing report records, per variant, how many chunks exceeded the limit and by how
much, so the effect is measured rather than assumed.

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

There is a second, sharper reason. Recent NLTK versions refuse to read or write data
outside the folders on their search path, so the project cache must be registered
before use (`data_ingestion/model_cache.py::register_nltk_data_dir`). Without that
step the cached copy is ignored *and* rejected, and the library falls back to
downloading its own.

**Consequences.** One extra ~15 MB download alongside the embedding model, and one
rule for code that splits sentences: register the project's data folder first.
