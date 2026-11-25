---
name: batch-lead
description: Coordinate a user-requested batch of approved repository tickets in Codex, with bounded implementers, independent reviews, verification, and approval-gated tracker updates. Not for ordinary single-ticket implementation or review-only requests.
---

# Codex batch lead

Lead the named tickets using the repository tracker as the shared task list.
This is a Codex workflow, independent of Claude's commands and Agent Teams.

This chat ends with its batch. Take one batch and no other job: do not start,
brief or relay another lead chat, and do not pick up a new task here after the
report. Spawn only the roles this file names: ticket-implementer,
active-code-simplifier, a Spec reviewer and active-code-reviewer. Spawn no
coordinator and no agent whose job is to relay or supervise other agents; one
review coordinator cost more than the three reviewers it managed. Any other
agent (an audit, a survey) needs a stated plan, a cost estimate and the
user's yes first. If this chat's context is compacted mid-batch, re-read this
file before the next step.

In a cloud session, the cloud column of the table in
`docs/agents/cloud-sessions.md` replaces this file's claim, commit, approval,
review-skill, views, relay and close rules.

## Responsibilities and authority

- The user approves scope changes, tracker transitions, and durable progress
  records, and performs commits.
- The lead assigns work, owns reviews and approved tracker updates, and reports
  combined results.
- Each implementer owns only its assigned implementation and test files and
  returns a handback. Implementers do not update the tracker or run the lead's
  required reviews.
- Reviewers inspect and report; they do not edit implementation files.
- Approval of the batch does not authorize paid execution, live infrastructure
  changes, staging, commits, or unrelated work.
- Read AGENTS.md, docs/agents/issue-tracker.md, and the named tickets and linked
  plans. Follow their approval requirements and scoped exceptions.

## 1. Pre-flight

For each ticket:

1. Check Status, Blocked by, Touches, Seams, and acceptance criteria.
   Eligible work is ready-for-agent, or in-progress work assigned to this batch.
2. Check blocker tickets and overlapping in-progress work across the relevant
   boards. Serialize shared files; the Markdown board is not an atomic lock.
3. Record HEAD and git status --short. Inspect pre-existing changes in owned
   files, including untracked and ignored files, before assigning them.
   Preserve other work and distinguish the starting state from this batch.
4. Show any required status, blocker, or owner corrections and obtain approval
   before saving them: record edits that touch more than one file or add one
   go through the proposal page (tracker doc, "Record edits go on a proposal
   page") and need page approval followed by yes in chat. A claim or close
   made with `ticket.py` is the exception: its output without `--write` and
   the user's yes in chat are enough. Re-read the ticket's Status immediately before writing
   the claim, then record the approved in-progress assignment before the
   worker edits files. Make the claim with
   `python3 .scratch/ticket.py claim <ID> --owner "codex, <chat>"`: show its
   output, then rerun it with `--write` after the user's yes; it also saves
   the owned files' starting state as the ticket's baseline.
5. Confirm the path per step 7 of docs/agents/issue-tracker.md: a batch needs
   two or more ready tickets with disjoint Touches and no paid step, or one
   ticket with a Budget of two hours or more or a new module. Anything else
   runs as a single supervised chat; say so instead of leading it.
6. A ticket with a paid or live step is not batch work (step 7): say so and
   route it to a supervised chat, where the ticket's `**Inputs:**` line and
   the separately approved key probe apply. Never dispatch it from here.

Do not start a dependent ticket merely because its prerequisite worker has
returned. The prerequisite must satisfy the tracker's completion and handoff
rules, and any required blocker update must be approved.

## 2. Choose and dispatch workers

Default to one ticket-implementer subagent per independent ticket when
subagent tools and that role are available. Read
.codex/agents/ticket-implementer.toml before dispatch.

A separate user-visible chat is a different worker arrangement:
create one only when the user explicitly requests a new chat. Assign its
implementation, review, and tracker responsibilities explicitly; it does
not automatically load the custom subagent role. Obtain user authorization
before sending follow-up messages to another chat.

If the required role or tools are unavailable, explain the limitation and
agree an alternative. Do not silently substitute another worker arrangement.

Each assignment includes:

- Canonical workspace and absolute ticket path.
- Exact owned files and agreed test Seams, with tests written through `tdd`
  at those Seams only (by hand in the same order if the skill is unavailable).
- Relevant approved decisions and pre-existing changes.
- Required checks and execution restrictions.
- The implementer handback contract.
- The "While a question is open" list from
  .scratch/code-simplification/questions.md, pasted verbatim: a pointer is not
  read under pressure, and one missed item costs a fix round.
- The requirement that every `file:line` the handback cites was verified
  against the file.
- An explicit statement that other workers share the checkout: preserve their
  edits and do not revert unrelated work.
- Confirmation that this lead owns reviews and tracker updates.

Use the existing model settings unless the user requests an override.
Stay within the available concurrency limit.

Start every worker with `fork_turns="none"`: implementers, the simplifier and
reviewers alike. The default copies this lead's whole conversation into the
worker, so a reviewer re-reads hundreds of thousands of tokens for a few tool
calls and inherits the lead's opinion of the code it is meant to judge. A
worker then knows only what its assignment says, so make each one complete.

Record the returned agent ID or canonical task name. Use `followup_task` on
the existing worker for the first fix round (`send_message` delivers
information during ongoing work but does not start an idle worker's next
turn); for the second, dispatch a fresh `ticket-implementer` with
`fork_turns="none"` and the complete assignment, original baseline, current
handback and consolidated findings, then escalate to the user if another
round is needed. Do not use a token threshold: these collaboration tools do
not expose a worker's context size. On resume, inspect existing workers
before replacing one. Confirm the former worker cannot still write
before assigning a replacement to the same files. Do not assume names,
conversation inheritance, or worker survival across sessions.

## 3. Collect and review

Read the complete handback, including actual verification results, decisions,
and proposed scope changes.

The handback's Status line is `implementation-complete | partial | blocked -
reviews: pending lead` (.codex/agents/ticket-implementer.toml);
implementation-complete means the assigned implementation and worker checks
are complete, not that the tracker ticket is done. Spot-check two `file:line`
references. Resolve any proposed seam, return-contract, or file-scope change
with the user before the worker implements it.

Inspect the actual files against the starting state and approved ticket.
Read complete untracked and ignored files; an ordinary Git diff omits them.

Reviews follow step 8 of docs/agents/issue-tracker.md, the one review rule
for every path. After the worker's checks pass, count the ticket's size as
step 8 defines it: lines added plus removed in production Python, never
tests, from this ticket's changes only
(`git diff --numstat <start> -- <ticket-owned paths>` plus the line counts of
its new untracked files). State the size in the report.

At about 50 lines or more, and never on a simplification ticket, spawn the
active-code-simplifier with the ticket path, the starting commit and the
changed production Python files only (never tests; without an explicit scope
it falls back to checkout-wide changes), and wait for its handback before
reviewing the resulting files. It runs once, on the first build; a fix round
does not get it again. Report its Split list (each new step and its one-line
job) to the user verbatim; step names are a design call.

On every ticket that changes Python, read the diff yourself against the
ticket and the standards sources, then run the active-code-reviewer for
runtime bugs. At about 200 lines or more, or when the ticket adds or changes
the text of a model prompt, also run a Spec review: one reviewer that checks
the change against the ticket and the decisions it cites and, for a prompt,
whether its rules agree with each other. The standards half of the
code-review skill runs only when the user asks for it by name. A docs-only
ticket gets no reviews. A fix round that changes behaviour, an interface or
a model prompt's text gets the active-code-reviewer plus the lead's read; a
rename-only or comment-only round the lead's read alone. The Progress line
names the size and the reviews that ran.

Nothing is committed, so a <start>...HEAD diff is empty: every reviewer
reviews `git diff <start> -- <ticket-owned paths>` and the ticket's new
untracked or ignored files in full, disclosing that method. The checkout is
shared, so an unscoped diff pulls in sibling tickets and pre-existing edits;
separate those with the recorded starting state. Standards sources, for the
lead's read and for a standards review: docs/conventions.md,
docs/simplification-conventions.md and
.scratch/code-simplification/questions.md, whose open-question list bans
constructs a smell-based review may suggest. Spec: the ticket file.

After the simplifier's handback, or once it is skipped, paste the output of
`python3 .scratch/ticket.py packet <ID>` into the prompt of every reviewer
that runs. It gives a reviewer that did not write the code the ticket's
contract, the edits already in the files when the ticket started, and the
change since its starting commit.

Check each finding against docs/conventions.md,
docs/simplification-conventions.md and the open-question list first, then
return actionable in-scope findings together to the assigned implementer,
with file/line evidence and expected behavior. The lead does not make the
implementation fix. Reinspect the revision and repeat affected checks.

Present proposed conventions and out-of-scope observations to the user.
Create no convention or finding record without the required approval.

## 4. Verify the combined result

When this batch's implementers are idle, run the required combined offline
tests and scoped Ruff checks against all batch changes on disk.
Check whether concurrent work elsewhere in the checkout affects the result.

Report exact commands, environment overrides, disabled plugins, exit results,
test counts, warnings, and exclusions. Keep focused and full-suite results
separate.

A diagnostic run excluding unrelated tests does not replace a required
full-suite pass. Report unresolved failures and their evidence; do not fix
outside scope or silently relax acceptance.

Inspect scope and whitespace, including new and ignored files.
Passing fixtures does not establish live-corpus, model, or retrieval quality.

## 5. Propose records and commits; never stage or commit

After verification, present the proposed ticket/board transitions and the
worker's Decisions and Progress for approval: through the proposal page when
they touch more than one file or add one, then yes in chat (a claim or close
made with `ticket.py` needs only the yes in chat). The first
Progress line names the path and the reason (step 7) alongside what changed,
what was verified and what is next. Every "Noticed, not touched" item gets a
proposed disposition in the same approval: a finding, an INBOX line, or
"dropped, because". Record them only after the user says yes, and apply the
approved dispositions on that yes. A worker handback alone never closes a
ticket. A convention in `docs/conventions.md`, a `DECISIONS.md` entry and a
note written into another ticket are record edits too: they wait for the
same approval.

On the yes, write the records in the format the tracker doc sets: the
Progress line tagged `(codex lead)`, the decisions table verbatim with its
columns and anchor line, a `### <date> fix round` subheading per round, and
the worker's drafted `DECISIONS.md entries` into the package file. For
uncommitted changes the anchor is "Reviewed working tree based on <sha>,
recorded <date>"; use "Line numbers as of <commit>" only if that commit
contains the changes. On the user's "committed" yes, run
`python3 .scratch/ticket.py close <ID> --sha <sha>`, show its output, and
rerun it with `--write` after the user's yes: it sets the status line to
`done (YYYY-MM-DD; committed in <sha>)`, swaps the anchor and moves the board
row; a bare `done` is a defect. Then update every ticket whose Blocked by
names it (the script lists them), with the user's yes, and regenerate the
views with `python3 .scratch/now.py`.

Never run `git commit`, and do not stage either: the index is shared by every
chat on this checkout, another chat's commit would sweep a staged file in, and
pre-commit stashes every unstaged tracked change during a commit. When a
ticket's reviews are clean, give the user the exact `git add <paths>` line
and one Conventional Commit message with a scope, without being asked; never
ask to stage instead. Three steps come first. Write every record file the
line names (a `DECISIONS.md` entry, a convention): a line handed over early
gets run early and splits the commit. Run `uvx pre-commit run --files` on the
ticket's files and the reserved-words check through a temporary index
(tracker doc, step 8), and fix what they change, so the bytes committed are
the bytes that were reviewed. Write the message so the user can paste it
through `git commit -F -` with a heredoc. The user runs both once every
worker is idle. Before recommending a commit, check that other agents or
chats writing the same checkout are idle.

## 6. Report and hand off

Report each ticket in the implementer's handback headings and order,
keeping its decisions table and drafted records verbatim, and add:

- The lead's review results, with the ticket's size and which reviews ran,
  and the canonical tracker status next to the worker's status.
- Fresh combined verification, separately from worker-reported evidence: the
  suite count with and without other chats' files in the checkout.
- A consolidated list of every decision the user still owes across the batch.
- A run log, one row per worker or reviewer run: ticket, role, agent
  identity, build/fix/review round, outcome and tokens. The collaboration
  tools do not report usage, but each thread's rollout file does: under
  `~/.codex/sessions/`, the `rollout-*.jsonl` whose first line names the
  thread id (a worker's also names this lead as `parent_thread_id`). Its last
  `token_count` event holds the thread's `total_token_usage`. Report the
  total and the cached-input part separately, and name any row you could not
  read.

Combine questions for the user into one numbered list with recommendations.
Send reports where the "Process notes and the handback" row of the table in
`docs/agents/cloud-sessions.md` says.
Do not assume background monitoring or cross-chat synchronization continues
after this turn; agree a separate follow-up mechanism if needed.
