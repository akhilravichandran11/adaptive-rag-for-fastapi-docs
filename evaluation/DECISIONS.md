# Evaluation decisions

Why the evaluation side of this project is built the way it is. Each entry records
what was decided, what else was considered, and what it costs. Entries are added as
the harness grows; the newest sit at the top.

---

## 2026-11-14 — Results file: one line per question and system, and the only `*.jsonl` in its run folder

**Decision.** `evaluation/generate_results.py` asks every evaluation question of every selected strategy × embedder collection. It saves each result as one line of `evaluation/outputs/<run_id>/results.jsonl`. A line is the record that `ask` in `retrieval/baseline_rag.py` returns: the question, the collection, `top_k`, the ranked chunks with their metadata, the answer and its token `usage`. `system` (`baseline__<strategy>__<embedder>`) and `question_id` are put in front. Questions are asked question first: question 1 of every system, then question 2. Beside the file sits `run_meta.json`, which holds the run's settings and what each attempt saw. Nothing else in the folder ends in `.jsonl`.

**Why one file, and the only `*.jsonl`.** The scorer (`evaluation/retrieval_metrics.py`) and the review app group lines by `system`, so one file per system would be fourteen files read as one. A tool can read every `*.jsonl` in the folder without meeting an error log or a second shape of line. A question that fails is listed in `run_meta.json`'s `failed`; it is never written as a line.

**Why each line is written as it completes.** Embedding a question and answering it are paid calls, hundreds per run. Each line is flushed as soon as `ask` returns, so a stopped run keeps every result it paid for. `--resume RUN_ID` then asks only the pairs the file does not hold yet (`docs/conventions.md`, C013). A half-written last line, the only kind a kill can leave, is cut off and its pair asked again.

**Why question first.** A run stopped part way has asked every system the same questions, give or take one. Every collection is queried within the first fourteen lines, which is when its index report is checked (the index report gate entry).

**Cost.** Each line repeats the question and the chunks' metadata, so the file is larger than a table of answers. Answers are not claimed to repeat: another run on another date, with the same settings, may get different answers from the same model alias.

---

## 2026-11-14 — Index report gate: each collection's build is checked at its first result

**Decision.** When an attempt gets a collection's first result back, `generate_results.py` takes the `index_run` its chunks carry in `meta.index_run`. It reads that indexing run's `index_report.json` and goes on only if the report lists the collection as `complete`. It then records `{index_run, chunks_run, index_status}` under the collection in `run_meta.json`'s `collections`, and writes `run_meta.json` before the collection's first line. The run stops with exit 1, without saving that result, when:
- the report is missing, unreadable, or says anything but `complete`;
- the first result has no chunks, or its chunks name no `index_run`;
- on a resume, the collection's chunks now carry a different `index_run` or `chunks_run` than an earlier attempt recorded. The collection was rebuilt, and one run's lines for a system must come from one build.

**Why the chunks name the report.** Every indexed point carries the id of the indexing run that wrote it. Sorting run folders to find the latest report picks the wrong one when a collection was indexed twice, as `markdown_ast` × Voyage was: one run left it `partial` and a later one completed it. The first result gives the id at no cost. A Qdrant scroll before the first question would need new code to learn the same thing.

**Why stop instead of only recording.** A half-indexed collection would be scored as a weak chunking strategy. A rebuilt collection can hold different chunks under the same name, so a results file that mixed its two builds would score neither.

**Cost.** The check runs at a collection's first result, not before the run. In an answering run, up to thirteen other collections' answers, plus the bad collection's own, are paid for before it is found. The retrieve-only smoke run comes first and makes no answer calls, so it finds a bad collection for free. A rebuilt collection cannot be resumed: the run starts again in a new folder.

---

## 2026-11-14 — Model probe: each answering attempt records the version the alias pointed to

**Decision.** Answers come from the model named by `GEMINI_MODEL`, an alias such as `gemini-pro-latest` that Google may point at a newer model at any time. At the start of each attempt that answers, `generate_results.py` makes one short call of its own with that alias, reads the response's `model_version`, and appends `{at, model_version}` to `run_meta.json`'s `model_versions`, with `null` when the response names no version. It warns when a resume gets a different version from the run's last attempt. If the call fails, the attempt stops with exit 1, before its run folder is made and before any question is asked. A retrieve-only run, and an attempt with nothing left to ask, make no call.

**Why a call of its own.** The answer pipeline's generator reports only the alias, so recording a version per answer would mean replacing that generator. One extra call costs under a cent. It lets a reader line this run up against the model versions recorded by the project's CRAG pipeline (`crag/llm.py`).

**What it does not show.** The probe identifies its own response only. It records the version the alias pointed to when each attempt started. A move of the alias during an attempt is not seen, and the answers themselves carry no version. Answers are not claimed to repeat across dates.

**Cost.** One more paid call per answering attempt, and one more thing a missing or refused `GOOGLE_API_KEY` stops before any question is asked.

---

## 2026-11-14 — Retrieval is scored at file level, from saved results

**Decision.** `evaluation/retrieval_metrics.py` scores retrieval from the results files
a system has already saved, so scoring makes no API call and gives the same numbers on
every run. It reports recall@1, recall@3, recall@5, recall@10 and MRR, and prints one
markdown table with a row for each system and question set. Run it as
`uv run python evaluation/retrieval_metrics.py RESULTS.jsonl [RESULTS.jsonl ...] [--questions FILE ...]`;
`--questions` defaults to every `*.json` in `evaluation/questions/`. The table goes to
standard output and nothing is written to disk. The exit code is 0 for a table, 1 for a
file that is missing or cannot be scored, and 2 for a bad command line.

**How one question is scored.** A retrieved chunk is a hit when its `meta.source_path`
is one of the question's `gold_sources`. recall@k is the number of the question's gold
pages with a chunk at rank k or better, divided by its number of gold pages; a page
found by several chunks counts once. The reciprocal rank is 1 over the rank of the
best-ranked hit, or 0 when there is none, and MRR is its mean. A chunk's place is its
`rank` field, not its position in the list, because the field is the rank the system
itself reported. A chunk from outside the corpus is marked `meta.source == "web"`: it is
never a hit, and it still holds its rank. Any other chunk must carry a `source_path`,
and one without it stops the run. Treating a chunk with no `source_path` as a web chunk
was rejected, because a broken corpus chunk would then pass as an ordinary miss.

**Why one row per question set.** Each system gets one row for the handwritten set and
one for the generated set, grouped by the question's `origin`, and there is no pooled
row. Pooled, the forty generated questions would outweigh the ten handwritten ones, and
each generated question has a single gold page, so its recall is 0 or 1. Every set that
is loaded gets a row for every system, so a set a system never ran shows as zeros with
all of its questions under `no chunks`, not as a missing row.

**Why a question with no chunks scores zero and is counted.** A question with no line in
a system's results, or whose line has no chunks (the `chunks` key is missing, `null` or
an empty list), scores 0 in every column, and the `no chunks` column counts those
questions. Every mean is over the whole set, `n`. Leaving such questions out of the mean
was rejected: it flatters a system that fails on the hard questions. Refusing the whole
file was rejected too: one failed call would block the scores of every system.

**What stops a run.** A file that is missing or is not UTF-8 text; a line that is not a
JSON object, or has no `system` or `question_id`; a `question_id` that is in no question
file; a second line for the same system and question; a `chunks` value that is not a
list; a chunk that cannot be scored; and a `question_id` that appears twice in the
question files. The error names the file and, for a results line, the line and the
question. Each of these means a file is damaged or was scored against the wrong
questions, and a table printed anyway would look valid.

**Why questions are read through `EvalQuestion`.** Each question file is validated with
`EvalQuestion` (`evaluation/schemas.py`), the model the question sets were written
with, so the question fields are defined in one place. Reading `question_id`,
`gold_sources` and `origin` as plain JSON was rejected: it would be a second place that
knows the question fields.

**Why results are read as plain JSON.** A results file is the contract between a system
and the scorer: one JSON object per line. The scorer reads three keys, `system`,
`question_id` and `chunks`, ignores the rest, and imports no system's code, so the
baseline and CRAG are scored by the same code from files alone. A Pydantic model of the
line was rejected: it would be a second definition beside the one each system writes
with, for three keys. Saving the table to a run folder was rejected because nothing
reads saved metrics yet, and the output can be redirected to a file.

**Known limit.** Matching is by page, not by passage: a chunk from the right page but
the wrong section still counts as a hit. A long page cut into many chunks is therefore
easier to hit than a short one. Matching against hand-picked gold chunks is later work.
Only `chunks`, the context the answer used, is scored; the `first_pass_chunks` a
corrective system may also save are not scored yet.

---

## 2026-11-09 — Questions name their answer pages by `source_path`; ten are written by hand and forty generated

**Decision.** An evaluation question is an `EvalQuestion` (`evaluation/schemas.py`)
with five required fields, `question_id`, `question`, `reference_answer`,
`gold_sources` and `origin` (`handwritten` or `synthetic`), and one optional field,
`paraphrase`. `gold_sources` lists the pages that answer the question by their
manifest `source_path`, such as `advanced/additional-responses.md`, and holds at least
one; a question lists two pages only where it takes two to answer it. The first set,
`evaluation/questions/handwritten.json`, holds ten questions written by hand, `hw-01`
to `hw-10`, whose gold pages between them reach all six buckets of the docs: tutorial,
advanced, reference, how-to, deployment and the top-level pages. A test checks that
every entry loads as an `EvalQuestion` with no unknown keys, that every gold page is in
the manifest, and that every bucket is covered.

**Why `source_path`.** Every indexed chunk carries its page's `source_path` in
`meta.source_path`, so retrieval is scored by comparing strings: a retrieved chunk is a
hit when its `source_path` is in the question's `gold_sources`. A page's normalized
filename (`advanced__additional-responses.md`) is its other name, but no chunk field
holds it, so scoring on it would need a lookup table.

**Why no `bucket` or `difficulty`.** Nothing reads either one: results are reported by
system and by question set. A stored bucket would be the first gold page's, an
arbitrary pick for a two-page question, so a check that needs one looks it up in the
manifest. A difficulty label had no honest source: the corpus analysis rated 112 of
the 124 pages `intermediate`.

**Why an optional `paraphrase`.** Where a question's author also wrote a second
wording of it, `paraphrase` holds that wording; every other entry leaves it `null`, and
generated questions never set it. Nothing reads it yet, but unlike a bucket it cannot
be looked up later: only the person who wrote the question can supply it. It is kept so
that a later run can ask both wordings and compare how retrieval does on each.

**Why a handwritten set.** Questions generated from a page tend to reuse its headings
and terms, which makes retrieval look easier than it is for a real user. Ten questions
phrased the way a developer asks them, with answers drafted from the docs and then
checked by hand, give a harder baseline beside the generated set, and they are the set
rated by hand to calibrate the answer judges.

**Why a generated set, and its limit.** The second set,
`evaluation/questions/synthetic.json`, holds forty questions, `syn-01` to
`syn-40`, written by `data_curation/stage5_synthetic_qa.py`. With a fixed seed it
draws forty pages in proportion to the bucket sizes (16, 11, 4, 4, 3 and 2 in the
order above), skips any page the corpus analysis flagged for possible prompt
injection, and takes for each page a question the corpus analysis already wrote
for it. One model call per page answers that question from that page alone. The
forty were read by hand before the copy, and a bad entry is corrected by hand,
never regenerated: syn-13's answer was rewritten without its opening hedge, and
syn-16, whose page does not say how to set environment variables, takes the
page's second question from the corpus analysis, with an answer written from the
page. The limit: the corpus analysis wrote these questions while
reading the page, so they tend to reuse its wording and may make retrieval look
easier than it is for a real user. That is why the two sets are reported apart.
The echo also favours keyword search: about nine in ten of a
synthetic question's content words appear in its gold page, against about three
in four for the handwritten ten, so a gap between hybrid and dense retrieval on
this set is checked against the handwritten set. A second limit: the four
reference questions, syn-21 to syn-24, point at short pages (560 to 1,779
characters, against a median of 5,414), and other pages often answer those
questions more fully, so a miss on one of the four can mean the retriever found
a better page; those misses are read by hand. Listing that page as a second gold
source would not help: recall divides by the number of gold pages, so either
page alone would score half.

**Cost.** Ten questions are a small sample: one question moves a recall figure by ten
points, so the handwritten set shows direction, not small differences.
