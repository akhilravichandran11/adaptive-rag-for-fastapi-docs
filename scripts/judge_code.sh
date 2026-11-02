#!/usr/bin/env bash
# Run the code-readability judge on existing files with the model that did not
# write them. A reader test, not a linter: the judge gets the code and nothing
# else (no ticket, no plan, no chat) and says, per function, what it is for and
# where it had to stop. It never proposes fixes; that is the simplifier's job.
#
# Usage: scripts/judge_code.sh --author claude|codex <file> [<file> ...]
#
# --author claude  → Codex judges (codex exec, read-only sandbox).
# --author codex   → Claude judges (claude -p, tools limited to Read/Grep/Glob).
# Writes .scratch/code-judge/<UTC time>-<first file stem>.md (git-ignored) and a
# .log beside it with the CLI's own transcript; prints the .md path. Run by hand;
# no agent is bound to it. Design: docs/agents/code-readability-judge-design.md.

set -euo pipefail
unset CDPATH

usage() {
  echo "usage: $0 --author claude|codex <file> [<file> ...]" >&2
  exit 2
}

author=""
files=()
while [ $# -gt 0 ]; do
  case "$1" in
    --author)
      [ $# -ge 2 ] || usage
      author="$2"
      shift 2
      ;;
    -h|--help) usage ;;
    -*) usage ;;
    *)
      files+=("$1")
      shift
      ;;
  esac
done
[ "${#files[@]}" -gt 0 ] || usage
case "$author" in claude|codex) ;; *) usage ;; esac

repo="$(git -C "$(dirname "$(readlink -f "$0")")" rev-parse --show-toplevel)"
abs=()
for f in "${files[@]}"; do
  [ -f "$f" ] || { echo "judge_code: no such file: $f" >&2; exit 1; }
  a="$(cd "$(dirname "$f")" && pwd)/$(basename "$f")"
  case "$a" in "$repo"/*) ;; *) echo "judge_code: not inside the repo: $f" >&2; exit 1 ;; esac
  abs+=("$a")
done
list="$(printf '  %s\n' "${abs[@]}")"

stem="$(basename "${abs[0]}")"
case "$stem" in .*) ;; *) stem="${stem%.*}" ;; esac
outdir="$repo/.scratch/code-judge"
mkdir -p "$outdir"
out="$outdir/$(date -u +%Y%m%d-%H%M%S)-$stem.md"
log="${out%.md}.log"
trap 'rm -f "$out.tmp"' EXIT

prompt="You are a senior engineer screening a candidate by reading this repository. You have ten
minutes and you have never seen this code. You are a reader, not an editor: you report what
you could and could not understand, and you never propose fixes or rewrites.

Scope: this is a portfolio project. Its reader is a senior engineer screening a candidate,
who spends ten minutes on the repo. Every function must be explainable to them in sixty
seconds from the code alone.

Read these files in full, and nothing that explains them (no tickets, plans, findings, or
docs under .scratch/ or docs/). You may open other source files only to follow a call.
${list}
Do not edit anything.

Step 1. List every function, method and class in the files, numbered, before judging any.
Step 2. For each one, from the code alone:
  a. Cold summary: one sentence saying what it does and why it seems to exist. If you cannot
     say why it exists, say so.
  b. One job: can you state its job in one sentence without 'and'? If not, name the jobs.
  c. Name test: does the name tell you what it does or holds before you read the body?
  d. Re-read points: where did you have to stop and read again, and what did you need to
     hold in your head to get through (how many things at once)?
  e. Verdict: understood / unclear.
Step 3. Output exactly three sections:
  ## Table
  # | name | cold summary | one job? | name test | re-read points | verdict
  ## Notes
  At most three sentences per row, numbered to match. Only for rows that are unclear or
  failed a test.
  ## Hardest to read
  The three rows that cost the most time, in order, one line each on why.
Do not propose fixes. Do not cite style rules or conventions. Do not edit files."

case "$author" in
  claude)
    codex exec -s read-only -C "$repo" --skip-git-repo-check -o "$out" "$prompt" >"$log" 2>&1 </dev/null \
      || { echo "judge_code: codex failed; see $log" >&2; exit 1; }
    ;;
  codex)
    (cd "$repo" && claude -p "$prompt" --allowedTools Read Grep Glob --permission-mode dontAsk) >"$out.tmp" 2>"$log" </dev/null \
      || { echo "judge_code: claude failed; see $log" >&2; exit 1; }
    mv "$out.tmp" "$out"
    ;;
esac

echo "$out"
