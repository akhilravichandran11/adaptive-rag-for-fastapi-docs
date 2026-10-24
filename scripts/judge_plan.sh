#!/usr/bin/env bash
# Run the plan-complexity judge on one plan with the model that did not write it.
#
# Usage: scripts/judge_plan.sh <plan.md> --planner claude|codex
#
# --planner claude  → Codex judges (codex exec, read-only sandbox).
# --planner codex   → Claude judges (claude -p, tools limited to Read/Grep/Glob).
# Writes judge.md next to the plan and prints its path. The flag is required:
# the script cannot tell who wrote a plan, and guessing wrong puts the planner
# in the judge's seat. Design: docs/agents/plan-complexity-judge-design.md.

set -euo pipefail
unset CDPATH

usage() {
  echo "usage: $0 <plan.md> --planner claude|codex" >&2
  exit 2
}

plan=""
planner=""
while [ $# -gt 0 ]; do
  case "$1" in
    --planner)
      [ $# -ge 2 ] || usage
      planner="$2"
      shift 2
      ;;
    -h|--help) usage ;;
    -*) usage ;;
    *)
      [ -z "$plan" ] || usage
      plan="$1"
      shift
      ;;
  esac
done
[ -n "$plan" ] || usage
case "$planner" in claude|codex) ;; *) usage ;; esac
[ -f "$plan" ] || { echo "judge_plan: no such plan file: $plan" >&2; exit 1; }

repo="$(git -C "$(dirname "$(readlink -f "$0")")" rev-parse --show-toplevel)"
plan="$(cd "$(dirname "$plan")" && pwd)/$(basename "$plan")"
out="$(dirname "$plan")/judge.md"
trap 'rm -f "$out.tmp"' EXIT

# The plan's budget is the user's total for the concern; "not stated" is itself
# a finding for the judge (Step 2 of the prompt).
budget="$(sed -n 's/^\*\*Budget:\*\* *//p' "$plan" | head -1 | tr -d '\r' | sed 's/[[:space:]]*$//')"
budget="${budget:-not stated}"

prompt="You are a senior engineer screening a candidate by reading this repository. You have ten
minutes. You are judging a plan, not writing one: you may only remove or simplify, never add.

Scope: this is a portfolio project. Its reader is a senior engineer screening a candidate,
who spends ten minutes on the repo. Prefer the simplest design that works: every design
decision must be explainable to them in sixty seconds. Budget for this plan: ${budget} of
the author's own time.

Read the plan at ${plan}. Read any file in the repository you need to check a claim.
Do not edit anything.

Step 1. List every decision or component the plan makes, numbered, before judging any.
Step 2. Check that the plan states its budget. If it does not, that is finding 0.
Step 3. For each decision:
  a. Restate the plan's reason for it. Does that reason describe a problem this project
     has now, at this scope, with evidence in the repository or in a finding? A reason
     resting on scale the project does not have, a future requirement, or a failure
     nobody has observed counts as no reason.
  b. Delete test: what breaks for the stated scope if this is removed? \"Nothing\" means drop.
  c. Was a simpler alternative named and rejected with a reason? If not, name one.
  d. Sixty-second test: could the author defend this in an interview, in one minute,
     without notes?
  e. Is it worth its share of the budget?
Step 4. Output exactly three sections:
  ## Verdicts
  A table: # | decision | verdict (keep / simplify to X / drop) | simpler alternative | one-line reason
  ## Notes
  At most three sentences per row, numbered to match.
  ## Simpler sketch
  At most half a page. First line: \"Input only: the planner revises the real plan and
  cites accepted rows.\"
Do not propose additions. Do not rewrite code. Do not edit files."

case "$planner" in
  claude)
    codex exec -s read-only -C "$repo" --skip-git-repo-check -o "$out" "$prompt" >/dev/null </dev/null
    ;;
  codex)
    # Write via a temp file so a failed run never leaves an empty judge.md.
    (cd "$repo" && claude -p "$prompt" --allowedTools Read Grep Glob --permission-mode dontAsk) > "$out.tmp" </dev/null
    mv "$out.tmp" "$out"
    ;;
esac

echo "$out"
