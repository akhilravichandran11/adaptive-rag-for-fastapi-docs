# Plan-complexity judge: design

Status: **implemented** 2026-10-25 (grill finished the same day; 15 questions, all answered). The script is `scripts/judge_plan.sh`; the scope line and judge pointer are in AGENTS.md Conventions; the budget header lines are in `docs/agents/issue-tracker.md`. Blind validation (Q7) has not been run yet.
## Summary

Plans for this portfolio project kept arriving too complex or not needed, and were approved on trust. Two things fix that. A **scope line** sets each plan's altitude before it is written: a project-wide audience sentence in AGENTS.md, and a per-plan hour budget the user states. A **judge**, run as the model that did not write the plan, in read-only mode, questions every decision in a finished plan before the user is asked to approve it. The judge is the senior engineer who will one day screen this repo; it may only remove or simplify, never add; it does not accept the plan's own reasons as evidence. It hands back a table of verdicts, short notes, and a half-page simpler sketch marked as input only, written to `judge.md` next to the plan. One script, `scripts/judge_plan.sh <plan.md> --planner claude|codex`, runs it with the other tool. Before anyone relies on it, it is run blind on past plans whose over-complexity the user already knows, plus one plan the user considers fine.

## Terms

- **judge**: a separate pass, by a different model or a fresh session, that questions a finished plan's necessity before approval; it never writes code or the plan. Avoid: reviewer, critic, second opinion.
- **scope line**: a standing sentence every plan request carries: who the audience is and how many hours the work may take. Avoid: context, constraints, preamble.
- **delete test**: for one plan decision, what breaks for the stated scope if it is removed? Nothing means drop it. Avoid: YAGNI check, necessity check.
- **sixty-second test**: could you defend this decision in an interview, in one minute, without notes? If not, it is not portfolio-defensible. Avoid: defensibility check, interview test.

## Why (in the user's words)

"My plans are either too complicated and/or not needed. This is going to be a portfolio project. Certain decisions have been made where it has been too complex. I have given the yes to the decision because I've trusted you. Maybe I need a secondary judge when the plan has been made, as to why those decisions are needed. Will this be portfolio-defensible? Is it even needed to be so complex? Can it be made simple?"

The motivating case: a plan whose complexity came with a reason attached (an optimisation in data ingestion that looked necessary), so it was approvable. A necessity pass that did not take the reason at face value would have caught it.

## Locked decisions

### Q1 Who the judge is: the senior engineer screening candidates who will read this repo

Asks at every layer "why this and not the simpler thing?" and may only remove or simplify, never add. A title alone changes vocabulary, not method; what the persona adds over a bare question list is a consistent answer to "simpler for whom": when two options both pass the tests, the screening engineer picks the one easier to explain.

Rejected: a staff-engineer design reviewer (asks "is this correct, safe and complete?", feels obliged to find gaps and add mitigations; it is the role that produced the complexity); a time-boxed tech lead (catches cost, not pointlessness; a cheap unnecessary component passes); no persona (same question list, no tie-breaker).

Cost: it will sometimes cut something a production system would need. That is the right trade for a portfolio, where over-building reads as poor judgment.

### Q2 Where the design is recorded: this file, tracked, with a one-line pointer from AGENTS.md

The failure is agents proposing complexity and the user approving on trust, so the fix must bind agents, not just the user: a tracked file plus a pointer in the one file every agent reads.

Rejected: a private `.scratch` hub (keeps the burden of skepticism on the user); a `docs/conventions.md` entry alone (too short for the question list and run procedure, and conventions.md is for code-shape rulings); a tracked doc with no AGENTS.md pointer (binds nobody).

### Q4 One judge, one pass

The screening engineer runs the fixed question list once; each row of the output answers both the delete test and the sixty-second test. The failure mode being fixed is additions nobody asked for, and every extra reader persona adds reasons to keep things. Mitigation for a single pass missing a decision: the judge lists every decision it found before judging any, so a gap is visible.

Rejected: two sequential lenses, necessity then defensibility (same tests, two runs, no gain); a three-persona panel in parallel (three runs plus a merge; the "maintainer" persona argues for abstractions); a per-plan rule, panel when a plan touches more than one package (one more rule to remember).

Why not "many fields", as in multi-agent building workflows: that idea is for building, where each field finds different bugs. Judging a plan for necessity is the opposite job, one question asked consistently.

### Q5 How the judge runs for a Claude-written plan: Codex, read-only, non-interactive

`codex exec -s read-only` with the judge prompt and the plan path, output to a file next to the plan. Claude only launches it and reads the result. A different model is the only way the judge does not share the planner's blind spots; the read-only sandbox is the only way it cannot "fix" the plan.

Rejected: a fresh Claude session (same model family, same blind spots); a custom agent in `.claude/agents` (same model, and runs inside the planner's context); Codex with a Claude fallback (a branch for a case that does not exist on this machine; change this doc if Codex ever goes away).

Cost: depends on Codex being installed and signed in; slower than an in-session subagent.

### Q14 How the judge runs for a Codex-written plan: Claude, read-only tools

The invariant is "the judge is never the model that wrote the plan", not "Codex judges". The same script with the required `--planner codex` flag runs `claude -p` with tools limited to Read, Grep and Glob. One prompt, two backends, the same `judge.md` table either way. The flag is required rather than guessed because the script cannot tell who wrote a plan file, and guessing wrong puts the planner in the judge's seat.

The harnesses differ in three ways and the script hides all three: how read-only is enforced (Codex: an operating-system sandbox; Claude: a tool allowlist, a policy rather than a sandbox, sufficient for a reader); how the prompt goes in (both as an argument); how the answer comes out (Codex writes its last message with `-o`; Claude prints to stdout and the script redirects). The two models differ in style and in what they notice; the fixed table format makes their outputs comparable, and noticing different things from the planner is the point.

Rejected: always Codex (self-judges half the plans); a different Codex model via `-m` (same lab's shared habits, and depends on which models the account can reach); the user judging Codex plans by hand (puts the burden back on the user).

### Q8 When an agent must run the judge: before asking the user to approve any plan, and before any grill Finish that produces a plan

Applies to every plan under `.scratch/**/plans/`. The judge's output is attached to the approval request. Escape hatch: the user writes "skip the judge" in the approval message; the default stays binding.

Rejected: a size threshold, only for plans adding a module, dependency or stage (the plans approved on trust were ordinary plans, and the planner would be judging its own plan's size); only on request (the current state); a second pass on the derived ticket set (doubles the runs; ticket growth belongs to ticket review).

### Q9 The plan's total budget comes from the user

Stated in the plan request ("plan X, budget 6h"). If it is missing, the planner's first reply asks for it before writing anything; it never guesses. The plan records the budget in its header; ticket budgets must sum to no more than it; the judge flags a plan whose parts cannot fit. The budget is the one input only the user can give: how much of their time this concern is worth.

Rejected: the planner proposes it (sizes the budget to the plan it wants to write); no plan-level budget (the judge loses its scale and the delete test falls back to opinion); a fixed default (a default nobody overrides is no budget).

### Q11 The judge does not accept the plan's own reasons as evidence

For every decision it restates the reason, then asks whether it describes a problem this project has now, at the stated scope, with evidence in the repository or in a finding. A reason resting on scale the project does not have, a future requirement, or a failure nobody has observed counts as no reason, and the decision gets the delete test as if unjustified. This is the single rule that would have caught the motivating case.

Rejected: trusting stated reasons and testing only unjustified decisions (waves through exactly the motivating case); counting only reasons linked to a finding or ticket (a finding can itself describe a hypothetical, so a link proves provenance, not necessity); checking only against the budget.

Cost: the judge will sometimes challenge a reason the user knows to be true from context it cannot see. One line from the user in the approval settles it and the decision stands.

## Routine choices

- **Q3 The scope line lives in two places.** The audience sentence in AGENTS.md Conventions (project-wide, does not change between tickets). The hour budget in each ticket, as a bold header line next to Touches: `**Budget:** 3h`. The budget counts hours of the user's own time end to end (reading the plan, approving, running verification, reviewing the diff), not agent wall-clock, which is cheap and unobservable. It is a scope statement, not a deadline: nothing is late, but a design that cannot be built and verified inside it needs a stated reason or gets simplified, and the delete test becomes "is this worth an hour of a three-hour ticket?". Rough bands for this repo: 1h for a fix or seam change, 3h for a new module, a day for a new pipeline stage with evaluation. Rejected: AGENTS.md only (one budget for every ticket); ticket only (repeats the audience, misses plan requests outside the tracker); typed each time (not being done today).
- **Q10 The audience sentence, verbatim for AGENTS.md:** "This is a portfolio project. Its reader is a senior engineer screening a candidate, who spends ten minutes on the repo. Prefer the simplest design that works: every design decision must be explainable to them in sixty seconds, and anything that is not should be simplified or dropped." Rejected: a rule with a banned list of production machinery (wrong in both directions, needs maintaining); "keep it simple" (too vague); sentence plus banned list. The user wanted "simplest design that works" kept; it was folded in as one clause and nothing more was added, since every further clause would be a rule the judge already enforces.
- **Q6 and Q15 What the judge hands back: three sections, in order.** (1) `## Verdicts`: a table, one row per decision: number, decision, verdict (keep / simplify to X / drop), the simpler alternative, a one-line reason. (2) `## Notes`: at most three sentences per row, numbered to match, for the context a one-liner cannot carry (what the judge looked at, why the reason did not hold). (3) `## Simpler sketch`: at most half a page, beginning with the line "Input only: the planner revises the real plan and cites accepted rows." The sketch is never adopted verbatim; otherwise a complex plan nobody judged is replaced by a simple plan nobody judged, written by a model that just spent its attention on cutting. The planner's revision pass after the judge is the turn where the user actually decides. Rejected: table only (the user wanted context and a concrete alternative); a full rewrite adopted as-is (self-planning, and loses the notes); uncapped prose with no rewrite (no concrete alternative, easy to nod through); letting the sketch replace the plan when every touched row is a drop (the loophole that would be used).
- **Q12 The prompt and flags live in one shell script, `scripts/judge_plan.sh <plan.md> --planner claude|codex`,** runnable by the user, Claude or Codex alike. The judge's value depends on running it the same way every time; one dropped `-s read-only` lets the judge edit the plan. Rejected: a prompt file plus a documented command (four flags retyped each time); a Claude slash command with a Codex twin (two files for one shell line, not runnable from a plain terminal); prompt inline in this doc (hard to diff). Cost: a prompt change is a script change, which is fine because the prompt should change rarely and visibly.
- **Q13 The output goes to `judge.md` next to the plan,** in the same `plans/<concern>/` folder, overwritten on each run. The approval message quotes the table. The plan itself records which verdicts were accepted, which is the history that matters. Nothing here is committed: `.scratch` is git-ignored and blocked by pre-commit. Rejected: dated files (history nobody rereads); chat only (lost with the chat, and wanted later when a ticket re-grows what the judge dropped); appending to plan.md (mixes artifact and judgement; re-runs must strip the old section). Cost: a re-run erases the previous table; copy it first if a comparison is wanted.
- **Q7 Validation before adoption: run the judge blind.** On one or two past plans whose over-complexity the user already knows, and on one plan the user considers fine (to catch the second failure, cutting something defensible). Compare the rows to the user's private list. The known cases are a held-out test set; designing the questions from them would be training on the test set (overfitting), and would also remove the ability to measure the judge. After the first two or three runs keep a four-line tally: known caught, known missed, unexpected agreed, unexpected disagreed. Only two entries change the design: a missed known case means a question is missing; a repeated false alarm of one kind means a question is too eager. Never add the examples to the prompt. Rejected: adopt on the next live plan (first test on a plan that matters); design from the examples; blind run on bad plans only.

## The judge prompt

Model-agnostic; the script substitutes `<PLAN_PATH>` and `<BUDGET>` (read from the plan's `**Budget:**` header line, or "not stated").

```text
You are a senior engineer screening a candidate by reading this repository. You have ten
minutes. You are judging a plan, not writing one: you may only remove or simplify, never add.

Scope: this is a portfolio project. Its reader is a senior engineer screening a candidate,
who spends ten minutes on the repo. Prefer the simplest design that works: every design
decision must be explainable to them in sixty seconds. Budget for this plan: <BUDGET> of
the author's own time.

Read the plan at <PLAN_PATH>. Read any file in the repository you need to check a claim.
Do not edit anything.

Step 1. List every decision or component the plan makes, numbered, before judging any.
Step 2. Check that the plan states its budget. If it does not, that is finding 0.
Step 3. For each decision:
  a. Restate the plan's reason for it. Does that reason describe a problem this project
     has now, at this scope, with evidence in the repository or in a finding? A reason
     resting on scale the project does not have, a future requirement, or a failure
     nobody has observed counts as no reason.
  b. Delete test: what breaks for the stated scope if this is removed? "Nothing" means drop.
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
  At most half a page. First line: "Input only: the planner revises the real plan and
  cites accepted rows."
Do not propose additions. Do not rewrite code. Do not edit files.
```

## The script

`scripts/judge_plan.sh <plan.md> --planner claude|codex`

1. Exit with a usage error if `--planner` is missing or not one of the two values.
2. Resolve the repository root, the plan's absolute path, its folder, and `OUT=<plan folder>/judge.md`.
3. Read `<BUDGET>` from the plan's `**Budget:**` line, else "not stated"; substitute both placeholders into the prompt above.
4. `--planner claude` → `codex exec -s read-only -C "$REPO" --skip-git-repo-check -o "$OUT" "$PROMPT"`. The CLI's own transcript (Codex streams about 200 KB of it to stderr) goes to `judge.log` beside `judge.md`, so an agent running the script sees only the output path; on failure the script exits 1 and points at the log.
5. `--planner codex` → `claude -p "$PROMPT" --allowedTools Read Grep Glob --permission-mode dontAsk > "$OUT"` (run from `$REPO`). `dontAsk` denies any tool outside the allowlist without prompting, which is what makes the limit hold in a non-interactive run.
6. Print the output path.

## AGENTS.md lines (under Conventions)

- The audience sentence from Q10, verbatim.
- **Plan judge:** before asking the user to approve any plan under `.scratch/**/plans/`, or finishing a grill that produces one, run `scripts/judge_plan.sh <plan.md> --planner <the tool you are>` and attach the resulting `judge.md` to the approval request, then revise the plan in your own words citing the rows you accept. A plan request without a budget gets one question back, the budget, before any planning. The user may write "skip the judge" in an approval to waive it. Design: `docs/agents/plan-complexity-judge-design.md`.

## Ticket and plan headers (in docs/agents/issue-tracker.md)

- Plans record `**Budget:** Nh` in their header, the total the user stated.
- Tickets record `**Budget:** Nh` next to `**Touches:**`; the budgets of a plan's tickets sum to no more than the plan's.

## Verified facts

- Codex CLI 0.163.0 is installed at `/opt/homebrew/bin/codex`; `codex exec` supports `-s read-only`, `-C <dir>`, `-o <file>` and `--skip-git-repo-check`. The repo already keeps Codex twins of its agents in `.codex/agents/`.
- `claude -p` (print mode) runs non-interactively and accepts `--allowedTools` and `--model`.
- Plans live at `.scratch/<hub>/.../plans/<concern>/plan.md` and are "ready to become tickets only after the user approves" them (docs/agents/issue-tracker.md).
- Ticket files carry bold header lines (`**Plan:**`, `**Target:**`, `**Origin:**`, `**Blocked by:**`, `**Status:**`, `**Touches:**`, `**Seams:**`); a budget line fits that shape.
- The repo has `.claude/commands/` (one command), `scripts/` (Python tools and hooks) and `docs/agents/` (agent-facing docs).

## First run (2026-10-26)

Blind run on `.scratch/data-ingestion-pipeline/targets/indexing-components/plans/chunk-size-rules/plan.md` (Claude-written, already implemented; Codex judged). 3m23s; the judge opened 15 files beyond the plan (findings, DECISIONS.md, the chunker, the checker, the report script and its tests); 28 rows, 16 keep, 10 simplify, 2 drop; format held. It flagged the missing budget as finding 0, applied the "stated reasons are not evidence" rule (low-prose heuristic, configurability flags), and grounded one verdict in code the plan did not mention (the sentence chunker's overlap). Weak spots: it did not say until late that the plan was already implemented; it made a table row for finding 0 and one for the plan's prose length. The stderr flood led to the `judge.log` change above. The user's tally against their private list is pending.

## Second run (2026-10-28, started from a Codex chat)

Codex judged (`--planner codex`, so Claude was the judge) the metadata-enriched-ingestion plan, 1,332 lines. Because the script takes no per-run context, Codex wrapped a byte-identical copy of the plan in a 42-line review header (solo-operator intent, latest approved but unsaved decisions, budget gap) and judged the copy. Observations: the first `claude -p` attempt exited 1 with no output while the user's session-start hooks threw; the second succeeded and the fail-loud path left nothing partial. 26 decisions: 13 keep, 12 simplify, 1 drop, plus finding 0 for the missing whole-plan budget. Code citations (`pipeline_v2/sources.py`, `_fit_content`, the tenacity retry pattern) were exact. Every plan line number was off by 42 (snapshot numbering, despite the header's instruction to use canonical numbers). It respected the brief: declined to judge unsaved serialization details, treated the user's kept temp-sibling write as "advice only", and found the body contradicting the approved fail-loud rule. The simplifications clustered on hypothetical-concurrency and recovery machinery, which was the user's original concern. Lessons: judge the canonical file, not a wrapped copy; save approved direction into the plan before judging; the solo-operator fact is project-wide and belongs in the scope line.

## Risks

- **The hour budget may distort verdicts** toward "cheapest" rather than "simplest" (user's concern, Q3 thread). The user is running with it to see how it behaves. Exit: if the blind validation runs show the budget skewing the judge, drop the plan-level budget and stop writing the ticket line; the audience sentence alone remains the scope line.
- **A single pass can miss a decision.** Mitigated by Step 1 of the prompt (list before judging), which makes a gap visible.
- **The judge will challenge reasons the user knows to be true** from context it cannot see. One line in the approval settles it; this is cheaper than the opposite failure.
- **Claude's read-only mode is a tool allowlist, not a sandbox.** Sufficient for a reader; if the judge ever needs to run commands, revisit.
- **Codex availability.** The Claude-plans path stops working if Codex is uninstalled or signed out; the doc says to change the design then rather than carry a fallback now.

## Deferred

None. Next step: the blind validation runs from Q7, which the user starts by picking the past plans.

## Open threads

- Whether code-level readability ("is the code too complex to read?") needs anything beyond the existing `active-code-simplifier` and the S-rules in `docs/simplification-conventions.md`. The judge works at plan level, where the sixty-second test measures how many concepts a reader must hold; nothing was decided about code level.
