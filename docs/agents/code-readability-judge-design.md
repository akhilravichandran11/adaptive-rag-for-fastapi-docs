# Code-readability judge: design

Status: **built, three real runs** (2026-10-27). Script: `scripts/judge_code.sh`. Run by hand; no agent is bound to it (unlike the plan judge). Sibling of [`plan-complexity-judge-design.md`](plan-complexity-judge-design.md), which holds the shared rules (judge is never the model that wrote the code, read-only, output format first).

## Summary

The `active-code-simplifier` is an editor with a checklist: it applies the S-rules to code it already understands, because it has the ticket and the diff. It cannot measure whether code is understandable, because understandability is only visible to a reader who arrives cold. This judge is that reader. It gets the files and nothing that explains them, writes one cold summary per function, and reports where it had to stop. It never proposes fixes.

## What it measures, and what it does not

- **Measures:** can a stranger say what each function is for; does each function have one job; does each name carry its meaning; where a reader has to re-read and how much they must hold at once. "One job" is the reader's form of single responsibility: a job you cannot state without "and" is two jobs.
- **Does not measure:** S-rule conformance (the simplifier), bugs (`active-code-reviewer`), spec conformance (`code-review`), or whether a design pattern "should" be used. Pattern advice adds constructs, which is the bias this portfolio is trying to remove; the judge is told to cite no rules and propose nothing.

## How it runs

`scripts/judge_code.sh --author claude|codex <file> [<file> ...]`

- `--author claude` runs Codex read-only; `--author codex` runs Claude in print mode with Read, Grep, Glob and `--permission-mode dontAsk`. Same invariant as the plan judge: the judge is never the model that wrote the code. The flag is required.
- Input is whole files, not a diff, because the first use is existing code. A diff mode can come later if a ticket-time use appears.
- The prompt forbids reading anything under `.scratch/` or `docs/` and allows other source files only to follow a call, so the judge stays cold.
- Output: `.scratch/code-judge/<UTC time>-<first file stem>.md` (git-ignored, blocked by pre-commit), with the CLI transcript in a `.log` beside it. Timestamped so a before/after pair survives.

## Output shape

1. `## Table`: one row per function, method or class: cold summary, one job?, name test, re-read points, verdict (understood / unclear).
2. `## Notes`: at most three sentences per row, only for rows that are unclear or failed a test.
3. `## Hardest to read`: the three most expensive rows, one line each.

## Intended first use (the user's plan)

Run on existing files, twice: once on the file as it is, once after `active-code-simplifier` has run on it. Compare the two tables. Separately, the user fills an "intent" column from memory and compares it with the cold summaries; a mismatch is the finding, the same held-out test as the plan judge. Only after that is it decided whether agents should run it at ticket time.

## First run (2026-10-27)

`data_ingestion/embedders.py`, Claude-written, Codex judged: 1m20s; the judge opened only the file plus `model_cache.py` and `download_models.py` to follow calls, so it stayed cold. 17 rows, all "understood"; three partial name tests (`_unsupported_backend` returns rather than raises; `build_text_embedder` is really the query embedder; `count_tokens` estimates for Voyage); hardest to read were the three tokenizer helpers, which matched an independent read of the file. Weak spot: the verdict column is binary and the real gradient sits in prose ("three things"), so a before/after-simplifier comparison has nothing numeric to compare. Candidate change: an integer "held at once" column.

## Three-run comparison (2026-10-27)

The first test of the judge as a before/after tool: `data_ingestion/embedders.py`, Codex judging every time with the same prompt. Each version acts on the previous run's findings, so the test is whether the judge registers its own findings being fixed. The simplifier comparison from "Intended first use" is still to do.

- **Run 1:** `embedders.py` as committed (the first run above).
- **Run 2:** v1, acting on run 1. `_unsupported_backend` becomes `_unsupported_backend_error` and `build_text_embedder` becomes `build_query_embedder`. `cached_tokenizer_path` branches on the tokenizer source, not the backend. S007 removes `lru_cache` and `deepcopy`, folding `_tokenizer_from_file` into `_local_tokenizer`.
- **Run 3:** v2, acting on run 2. The loader takes a registry name instead of a spec, because run 2 retraced spec → name → spec, and it becomes `_load_tokenizer`.

v1 and v2 are untracked copies (`data_ingestion/embedders_judged.py`, `embedders_judged_v2.py`). `count_tokens` was flagged in every run and deliberately left alone. Runs 2 and 3 judged a copy with its comparison header stripped, since the header names the findings. The script only takes files inside the repo, so that copy sat at the git-ignored `data_ingestion/sandbox/embedders.py`, and every output has the stem `embedders`. Each run took 77–88 s and 25–26k tokens. Each log shows one shell command, a `cat` of the judged file. That includes run 1, so the first-run note about following calls into `model_cache.py` and `download_models.py` is not in its log.

### What changed between runs

| Row | Name test, runs 1 / 2 / 3 |
|---|---|
| error helper | partial / pass / pass |
| text → query embedder | partial / pass / pass |
| `count_tokens` | partial / partial / partial |
| `bge_tokenizer` | pass / pass / partial ("not the fresh load") |

| Hardest | Run 1 | Run 2 | Run 3 |
|---|---|---|---|
| 1 | `cached_tokenizer_path` | `_local_tokenizer` (retraced spec → name → spec) | `truncation_stats` |
| 2 | `bge_tokenizer` | `EmbedderSpec` | `count_tokens` |
| 3 | `_tokenizer_from_file` | `truncation_stats` | `_load_tokenizer` ("three stages", no retracing) |

- **The name test moved exactly where names changed**, and it repeated on unchanged code (`count_tokens` in all three runs). The one unexplained flip is `bge_tokenizer`, whose code barely changed. Either renaming the helper to `_load_tokenizer` exposed that the public name does not say "load", or it is noise; one run per version cannot tell.
- **The tokenizer chain went from all three hardest slots to one.** That slot's reason changed from retracing to the work itself: check the source, find the file, configure it. The fix shows in the reason, not in list membership.
- **The hardest list is relative.** It always names three rows, so `truncation_stats` went from unlisted to 3rd to 1st with no code change. A row leaving the list is a signal; a row arriving is not.
- **Verdict:** every row was "understood" in every run.

### Noise on unchanged rows

"Held at once" counts, read from the re-read column, for rows whose code did not change across the three versions (or changed only in a renamed call or a comment):

| Row | Run 1 | Run 2 | Run 3 |
|---|---|---|---|
| `EmbedderSpec` | 3 | 5 | 4 |
| `truncation_stats` | 3 | 4 | 4 |
| `TruncationStats` | 2 | 3 | 2 |
| `TruncationStats.as_dict` | 2 | 1 | 1 |
| `build_document_embedder`, `truncation_report`, `count_tokens` | 3 | 3 | 3 |

The counts drift by up to 2 between runs of the same code. The rows that really changed moved by the same amount: `cached_tokenizer_path` went from four outcomes to two.

### What it means for the held-at-once column

The first run proposed an integer column so a comparison has a number to compare. On this file the number cannot carry a single comparison:

- One run per version cannot use it, because a real change and the drift are the same size (1–2).
- To use it, run each version three or more times and compare medians, or count only moves of 3 or more. That triples the cost (about 75k tokens per version) for a signal these runs already got from the name test and the hardest-list reasons.
- **Lean:** do not add the column. Compare the name test row by row and read the reasons in the hardest list; treat the counts as context.

## Open

- Whether the judge should also see the test file for a module (tests often say what a function is for; that may be a legitimate part of "reading the code" or may leak intent). Default now: no.
- Whether "one job" should be asked per class as well as per function. Default now: yes, the prompt lists classes too.
- Whether to add an integer "held at once" column. Lean: no, because the counts drift as much as a real change moves them (see the three-run comparison).
