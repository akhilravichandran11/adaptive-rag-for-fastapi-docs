---
name: batch-lead
description: >-
  Lead a batch of .scratch tracker tickets in Cursor: pre-flight Touches and
  blockers, spawn workers (Task subagents, sibling Agents Window chats,
  worktrees, or Cloud), review handbacks, propose git add lines, never commit.
  Use when the user says they are the lead, asks to batch-lead, coordinate
  tickets, or review remaining Cursor chats/agents for a batch. Not for
  implementing a single ticket alone.
disable-model-invocation: true
---

# Cursor batch lead

You are the **lead** for one batch of tickets under `.scratch/<hub>/issues/`.
Ticket files are the task list. Workers implement. You pre-flight, spawn,
review, stage-propose, and report. **The user commits.** Do not skip numbered
steps.

In a cloud session, the cloud column of the table in
`docs/agents/cloud-sessions.md` replaces this file's claim, commit, approval,
review-skill, views, relay and close rules.

This is the Cursor harness twin of `.claude/commands/batch-lead.md` and
`.agents/skills/batch-lead/SKILL.md`. Same board rules; different spawn
surfaces — see [harness.md](harness.md).

Read before acting: `AGENTS.md`, `docs/agents/issue-tracker.md` (esp. step 7–8,
Touches, Git worktrees), and each named ticket.

## 1. Pre-flight (before any spawn)

- Read each ticket in the user's prompt. Check `Status`, `Blocked by`, `Touches`.
- Show stale blocker/status fixes; apply only after the user says yes. Same for
  flipping to `in-progress`.
- Refuse two tickets that share a `Touches` file (check other boards too,
  e.g. `code-simplification` vs ingestion).
- Paid/live steps (eval, live index, paid API) are **not** batch work — route to
  a supervised chat (tracker step 7). Never spawn them from this lead.
- `git status --short`. Report uncommitted files in the batch's `Touches` and
  any other chat's WIP that could redden pytest.
- Confirm workspace is the **parent git root** (the checkout that owns
  `.scratch/`). Refuse a nested accidental clone.
- Say once which spawn mode you will use (Task / sibling chats / worktree /
  Cloud) and why. Mix modes only when the user asks.

## 2. Spawn workers

One worker per ticket. Every spawn prompt is **self-contained** (full ticket
path, owned files, sibling file list, settled rulings from the user prompt).

Paste the "While a question is open" list from
`.scratch/code-simplification/questions.md` **verbatim** into each implementer
prompt when the batch touches Python shape.

Tell each worker explicitly:

- Do **not** run `code-review` or `active-code-reviewer` — the lead runs them.
- Do **not** commit or `git add`.
- Change only owned files.

**Pick a harness** (details in [harness.md](harness.md)):

| Mode | Use when |
|---|---|
| Task `ticket-implementer` (or generalPurpose with the ticket prompt) | Lead stays in one chat; wants handbacks back here |
| Sibling Agents Window chats | User wants visible parallel chats to pin/review |
| Worktree agent | Parallel **code** edits; pass absolute `.scratch` paths (F001) |
| Cloud Agent | Ticket is GitHub-visible + self-contained; paste full ticket body |

Serialize dependency tickets: spawn B only after A's handback is reviewed.

## 3. Collect and review each handback

- Read the full handback. Confirm Status, Files, Verification counts. Spot-check
  two `file:line` cites against the files.
- After verification passes: count the ticket's size as tracker step 8
  defines it (production Python only, never `tests/`). At about 50 lines or
  more, and never on a simplification ticket, run `active-code-simplifier` on
  that ticket's **production** Python, naming the handback `Start` sha —
  if that agent exists in this harness; else note skipped.
- Then reviews per tracker step 8 (read-only; may run in parallel):
  - Spec: one subagent against the ticket and the decisions it cites, only
    at about 200 lines or more or when the ticket changes a model prompt. No
    standards review unless the user asks for it by name.
  - Bugs: your own read of the diff, then the `active-code-reviewer` Task on
    the same files, on every ticket that changes Python
- Fix nothing in source/tests yourself. Resume or re-prompt the **same worker**
  with **all** in-scope findings in one message. Two fix rounds max; then stop
  and ask the user.
- Progress line names the size and the reviews that ran (tracker step 8).
- `Proposed convention` / `Noticed, not touched` / `Awaiting ruling` → questions
  for the user, not silent edits.

## 4. Verify the batch

When every worker is idle:

- `uv run pytest` once with all batch changes on disk
- `uv run ruff check` on every changed file

Say if other chats' dirty files were excluded from the judgment.

## 5. Stage-propose; never commit

- Never `git commit` and never `git add` (shared index across chats).
- Per clean ticket: give exact `git add <paths>` and one Conventional Commit
  message with scope. User runs both when all workers are idle.
- Tracker / `DECISIONS.md` / multi-file record edits: proposal-page flow, then
  user yes. Tag Progress lines `(cursor lead)`.
- After user says committed: status `done (YYYY-MM-DD; committed in <sha>)`,
  unblock dependents with yes, then `python3 .scratch/now.py`.
- If phone needs the board: remind user to push nested `.scratch` to its GitHub remote.

## 6. Report

Re-run `git log -1 --oneline` and `git status --short` before reporting
(another chat may have committed).

Per ticket: status, `git add` line, proposed message, decisions table, suite
counts, cost/usage if available.

Then one numbered list of every decision the user owes, each with your
recommendation.

## Anti-patterns

- Expecting Claude-style live teammate messaging between Cursor chats
- Cloud workers for scratch-only context without pasting the ticket
- Two workers on the same file
- Lead implementing "just one line" of source/tests
- Committing from the lead chat
