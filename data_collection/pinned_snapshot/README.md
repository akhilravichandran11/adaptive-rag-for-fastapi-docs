# Pinned snapshot collection

Read selected FastAPI documents and their referenced examples directly from one
local checkout into a **new candidate directory**. This is the default workflow
for future collection. The active corpus has not been switched; replacing it
requires separate approval.

The collector uses Python 3.11+, the standard library, and installed Git. Run it
as a module from this repository root. It borrows the two include regexes from
`data_collection/collect_referenced_code.py`; it does not call that collector or
perform network requests.

## Prepare a checkout and collect

Choose a previously unused run name. Keep the checkout and generated data under
the local plan's verification directory, outside this source folder:

```sh
run=".scratch/data-collection-pipeline/targets/github-docs-source/plans/direct-snapshot/adoption-verification/manual-01"
mkdir -p "$run"
git init "$run/upstream"
git -C "$run/upstream" fetch --depth 1 https://github.com/fastapi/fastapi.git 6a6002298106d89d3a65fd62527dc0bdc4e681fd
git -C "$run/upstream" -c core.autocrlf=false checkout --detach 6a6002298106d89d3a65fd62527dc0bdc4e681fd
python -m data_collection.pinned_snapshot.collect \
  --snapshot-dir "$run/upstream" \
  --output-dir "$run/raw"
python -m data_collection.data_normalization.normalize_github_docs \
  --raw-dir "$run/raw" \
  --output-dir "$run/normalized"
```

The Git setup downloads the public revision once. The collector only reads the
checkout; it neither clones nor updates it. Keep it stable during collection.
HEAD must match the pinned commit, and staged or unstaged tracked changes are
rejected. Selected documents and examples must be regular tracked files, with
checkout bytes identical to their pinned Git blobs. Untracked files are ignored;
untracked references fail. Checkout filters that alter selected bytes also fail.

`--output-dir` must not exist, and its resolved location must be outside the
checkout and this project's `data/fastapi/raw/` and `data/fastapi/normalized/`
trees. A retry needs another fresh output directory. Both path arguments are
required; there is no implicit active-corpus destination.

The normalizer requires `--raw-dir` and `--output-dir` together. It reads the
candidate's docs and code, requires both source manifests, and writes normalized
docs and a manifest to a fresh directory. Resolved output paths must not overlap the input
or sit inside active raw/normalized trees. `--help` writes nothing. Its no-argument
behavior remains for compatibility; use explicit paths for candidate runs.

Inspect both candidates before seeking separate approval for manual promotion.
Back up and replace complete active directories
(`data/fastapi/{raw,normalized}/github-docs/v1/`), rather than merging files.
Saved chunks must be regenerated for the new manifest fingerprint before indexing.
These commands stop before promotion and do not regenerate chunks.

## Output and failures

```text
raw/
  docs/<flattened-source-path>.md
  code/docs_src/...
  code/fastapi/...
  manifest.json
  code_manifest.json
```

The five document subtrees, seven top-level allowlisted files, case-sensitive
`.md` suffix, buckets and 500-byte threshold match the existing selection rules.
The threshold is measured on **source bytes**. Documents are copied unchanged;
`{* ... *}`, `{! ... !}` and `{!> ... !}` references preserve options and backlinks.
Leading `../` and `./` follow the existing MkDocs repo-root convention; absolute
paths, internal traversal, unsupported roots and symlinks are rejected. Each
distinct example is copied once.

The schema-1 manifests retain source `github-docs`, version `v1`, the pinned
revision, URLs, paths, counts and byte totals. Acquisition is identified as
`direct-snapshot`; no combined ingest file or GitIngest-only metadata is emitted.
Manifests are written only after all copies and membership/size checks succeed.

Exit codes:

- `0`: required files and both manifests were written successfully.
- `1`: collection or operational failure, with a diagnostic on stderr.
- `2`: invalid CLI invocation, with argparse usage on stderr.

Failures can leave an incomplete directory for diagnosis. It is not a candidate
for consumption, must not be reused, and has no successful manifest pair. Missing
or unreadable examples abort collection; there is no HTTP or stale-file fallback.

## Measured comparison and limits

The local GDS-04 run on 2026-10-14 collected 124 documents and 289 referenced files.
Isolated use of the unchanged normalizer resolved 445 includes, with zero unresolved.
All document membership, referenced-file bytes, options, backlinks, source identity
and include records matched the baseline. In 123 documents, the baseline had one
extra trailing LF; the candidate preserves pinned source bytes, and normalized
output retains that same one-byte difference. The remaining document was identical.

The 500-byte rule still excludes 13 otherwise allowed documents. The 13 selected
API-reference documents still contain 49 unrendered `:::` directives; acquisition
does not build generated API documentation. These limits are unchanged. The
unfinished GDS-03 validator requires old GitIngest fields and is not a compatibility
target for this slice. No retrieval or grounding improvement has been measured.

The new script has 244 physical lines versus 228 + 185 in the two existing
acquisition scripts, including comments and blanks. It still imports their two
pure regexes. After checkout preparation, one local collection command replaces
the two collector commands and their aggregate split/per-example HTTP work.
First use adds three Git setup commands and retained checkout storage (about
51.8 MB in this run); it is not fewer setup commands overall. These are measured
tradeoffs, not a promised percentage reduction.

Tests are kept in the git-ignored `tests/data_collection/pinned_snapshot/`:

```sh
uv run pytest tests/data_collection/pinned_snapshot/ -q
uv run ruff check data_collection/pinned_snapshot/collect.py tests/data_collection/pinned_snapshot/test_collect.py
```

In a sandbox that blocks the `pytest-rerunfailures` plugin's local socket, use
`-p no:rerunfailures` and report that override explicitly.

The local [plan](../../.scratch/data-collection-pipeline/targets/github-docs-source/plans/direct-snapshot/plan.md),
[ticket](../../.scratch/data-collection-pipeline/issues/GDS-04-collect-pinned-snapshot-and-compare-baseline.md)
and [comparison evidence](../../.scratch/data-collection-pipeline/targets/github-docs-source/plans/direct-snapshot/verification/2026-10-14-gds04/evidence/comparison-report.md)
are intentionally git-ignored. The evidence includes every file/manifest difference,
commands, statuses, and before/after hashes. Normalization there redirects **all**
input/output paths in a separate process; do not invoke the normalizer's hardcoded
CLI against the active corpus for this comparison. Adoption, default-path changes,
removal of old collectors or GitIngest, and commits remain outside GDS-04.
