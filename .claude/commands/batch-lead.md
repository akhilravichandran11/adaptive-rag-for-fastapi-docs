---
description: Lead one batch of tracker tickets with named ticket-implementer subagents (desktop fan-out): flip statuses with my yes, spawn, review each handback, stage and propose commits, report.
---

You are the lead for one batch of tickets from `.scratch/<hub>/issues/`. The
ticket files are the task list; `ticket-implementer` subagents do the work; you
review, stage, and report. I commit. Follow this order. Do not skip a numbered
step.

## 1. Pre-flight (before any spawn)

- Read each ticket named in my prompt. For every one, check `Status`,
  `Blocked by`, and `Touches`.
- Stale state is common: a blocker that is `done` still listed, a ticket still
  `blocked` after its blocker closed. Show me the fixes (status, blocker list,
  owner) and apply them only after I say yes. Same for setting `in-progress`.
- Refuse to run two tickets that touch the same file. Check the other boards
  too (`code-simplification` overlaps ingestion files); flag an overlap to me.
- Run `git status --short`. Tell me about uncommitted tracked files in the
  batch's `Touches` before starting. Also list modified or untracked files
  outside the batch: another chat's work in progress can turn the full suite
  red. Judge "pytest passes" on the suite with that chat's files excluded,
  and say so in the Progress line.
- If the tracker has no task tools in this session (desktop), say so once and
  keep the board as the task list. Do not fake a task list in prose.

## 2. Spawn

- One `ticket-implementer` per ticket, named after the ticket (`prt04`). Give
  the ticket path, the exact files it owns, and anything the ticket leaves
  open that I settled in my prompt. Say explicitly: do not run `code-review`
  or `active-code-reviewer`; the lead runs them.
- Spawn tickets with a dependency only after the handback of the ticket they
  wait on has been reviewed.
- Every spawn prompt stands on its own. A session resume drops every
  subagent, so a resumed lead re-spawns with full context rather than
  messaging a name that no longer exists; a reused name is addressed as
  `name [ref]`.

## 3. Collect and review each handback

- Read the handback report in full. Confirm `Status`, the `Files` list, and
  that `Verification` shows actual counts.
- Once the handback's verification passes, spawn `active-code-simplifier` on
  that ticket's Python files with the handback's `Start` sha, and wait for it.
  Then run the reviews below on the simplified files. Only the first build and
  rounds over about 50 lines get the simplifier; a small fix round does not.
  Its Split list (each new step and its job) comes to me verbatim with the
  ticket's report; step names are a design call.
- Run `code-review` with the handback's `Start` sha as the fixed point.
  Nothing is committed, so its default `<Start>...HEAD` diff is empty: tell
  it to review `git diff <Start>` and to read every untracked file in full.
  Name its standards sources: `docs/conventions.md`,
  `docs/simplification-conventions.md` and
  `.scratch/code-simplification/questions.md` (its smell baseline suggests
  constructs the open-question list bans). Spec: the ticket file. It answers
  "is this what the ticket and our rules asked for"; then run the
  `active-code-reviewer` agent on the same files for runtime bugs. Fix
  nothing yourself
  for a bug: resume the implementer by name with the findings. A resumed
  agent reloads its whole transcript, so send every in-scope finding in one
  message, not one at a time.
- Before sending a fix list, check each item against `docs/conventions.md`,
  `docs/simplification-conventions.md`, the "While a question is open" list
  in `.scratch/code-simplification/questions.md` and the ticket's Seams: a
  fix that contradicts an accepted convention, or adds a construct the open
  list bans, costs a whole round.
- Review depth per round: the first build and any round that changes an
  interface or more than about 50 lines get the full set (`code-review`,
  then `active-code-reviewer`). A smaller fix round gets
  `active-code-reviewer` plus your own read of the diff against the
  standards and the spec, and the Progress line says which depth ran.
- Any `Proposed convention` or `Noticed, not touched` item goes to me as a
  question, not into `docs/conventions.md` or a finding file. Before
  proposing that a finding go to another ticket, read that ticket's `Touches`.
- An implementer's "Awaiting ruling" line (a changed seam signature, return
  value, or file set) is answered by me before review continues.

## 4. Verify the batch as a whole

- When every implementer is idle, run `uv run pytest tests` once with all
  changes on disk; an implementer's own run may predate a sibling's files.
- `uv run ruff check` on every changed file.

## 5. Stage and propose; never commit

- Never run `git commit`, and do not stage either: the index is shared by
  every chat on this checkout, and another chat's commit would sweep a
  staged file in. When a ticket's reviews are clean, give me the exact
  `git add <paths>` line and one Conventional Commit message with a scope.
  I run both, once every implementer is idle: pre-commit stashes every
  unstaged tracked change during a commit, and a live edit can be lost in
  the restore.
- No Progress line until I say yes; then add the implementer's drafted line,
  dated today, and append its decisions table verbatim to the ticket under a
  `## Decisions` heading (create the heading above `## Progress` if the ticket
  has none; a second batch on the same ticket adds a dated subheading). Lead
  the table with one line, `Line numbers as of <commit>`, since a fix round
  shifts them. The table is the only durable copy of the reasoning;
  transcripts are swept.
- After I confirm a ticket is `done` and committed, update every ticket whose
  `Blocked by` names it (drop the entry; `ready-for-agent` when nothing
  remains), with my yes. Then regenerate the views: `python3 .scratch/now.py`.

## 6. Report

Show me, per ticket: status, the `git add` line and proposed commit message,
the decisions table verbatim, and the suite count with and without other
chats' files. Then one numbered list of every decision I owe, across tickets,
each with your recommendation, so I answer once rather than in rounds. If the
relay session is not in `ListAgents`, don't wait on it: tell me, and send the
report when it appears. Then send the full handback reports and every process problem you
hit to the session named "Agent teams implementation" with `SendMessage`.
