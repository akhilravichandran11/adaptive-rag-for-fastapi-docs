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
  owner) and apply them only after I say yes. Same for setting `in-progress`:
  run `python3 .scratch/ticket.py claim <ID> --owner "claude, <chat>"`, show
  me its output, and rerun it with `--write` on my yes, before the spawn, so
  the files' starting state is saved before the implementer edits them.
- Refuse to run two tickets that touch the same file. Check the other boards
  too (`code-simplification` overlaps ingestion files); flag an overlap to me.
- A ticket with a paid or live step (an evaluation, a route check, a live
  index) is not batch work: step 7 of the tracker doc routes it to a
  supervised chat. Say so and never spawn it from here. That chat checks the
  ticket's `**Inputs:**` line (CRG-12's route questions were invented by the
  ticket writer and caught only after the paid run) and confirms the keys
  with the cheapest paid call first (a rotated Voyage key killed CRG-13's
  first run on its first call).
- Run `git status --short`. Tell me about uncommitted tracked files in the
  batch's `Touches` before starting. Also list modified or untracked files
  outside the batch: another chat's work in progress can turn the full suite
  red. Judge "pytest passes" on the suite with that chat's files excluded,
  and say so in the Progress line.
- If the tracker has no task tools in this session (desktop), say so once and
  keep the board as the task list. Do not fake a task list in prose.

## 2. Spawn

- One `ticket-implementer` per ticket, named after the ticket (`prt04`). Give
  the ticket path, the exact files it owns, the files its siblings own, and
  anything the ticket leaves open that I settled in my prompt. Paste the
  "While a question is open" list from
  `.scratch/code-simplification/questions.md` verbatim into the prompt: a
  pointer to the file is not read under pressure, and one missed item (a
  factory helper in a test, CRG-12) costs a fix round. Say explicitly: do not
  run `code-review` or `active-code-reviewer`; the lead runs them.
- Spawn tickets with a dependency only after the handback of the ticket they
  wait on has been reviewed.
- Every spawn prompt stands on its own. A session resume drops every
  subagent, so a resumed lead re-spawns with full context rather than
  messaging a name that no longer exists; a reused name is addressed as
  `name [ref]`.

## 3. Collect and review each handback

- Read the handback report in full. Confirm `Status`, the `Files` list, and
  that `Verification` shows actual counts. Spot-check two `file:line`
  references against the files: a handback once cited line 343 of a 262-line
  file.
- Once the handback's verification passes, spawn `active-code-simplifier` on
  that ticket's Python files with the handback's `Start` sha, and wait for it,
  as step 8 of `docs/agents/issue-tracker.md` says. Then run the reviews on
  the simplified files. Name only production files for it: it refuses
  `tests/` by its own rules even when a prompt names a test file.
  Its Split list (each new step and its job) comes to me verbatim with the
  ticket's report; step names are a design call.
- Once the simplifier is done, run `python3 .scratch/ticket.py packet <ID>`
  and paste its output into the prompt of every reviewer that runs
  (`code-review`, `active-code-reviewer`): a reviewer that did not write the
  code needs the contract, the edits that were there before the ticket, and
  the change.
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
  message, not one at a time. The one edit you may make yourself is a
  user-ruled, non-code record line (a `DECISIONS.md` sentence, a docstring
  pointer) after the implementer has handed back; say so in the Progress
  line. Source and tests are always the implementer's, even one line.
- Before sending a fix list, check each item against `docs/conventions.md`,
  `docs/simplification-conventions.md`, the "While a question is open" list
  in `.scratch/code-simplification/questions.md` and the ticket's Seams: a
  fix that contradicts an accepted convention, or adds a construct the open
  list bans, costs a whole round.
- Review depth is step 8's tier rule in `docs/agents/issue-tracker.md`: the
  size of the Python diff picks the tier, `code-review` runs at most once per
  ticket on the first build, and the Progress line names the tier. The
  reasons are cost: `code-review` is four to six times a bug review and finds
  spec gaps and style, not bugs (CRG-18 was 3 lines and cost 110k; CRG-15 was
  6 lines of Python plus 135 of markup and cost 113k for no must-fix).
  `code-review` and `active-code-reviewer` may run in parallel: both only
  read.
- Resume versus respawn. A resumed implementer reloads its whole transcript,
  so each fix round costs more than the last (PRT-04: 226k build, then 328k
  to 410k per resume). Resume for the first fix round. For a later round, or
  whenever the implementer's last usage line is past about 250k tokens, spawn
  a fresh implementer with the ticket path, the current handback, the fix
  list and the files it owns; it reads the code, not the history. Two fix
  rounds is the budget; needing a third means the ticket or the fix list is
  wrong, so stop and tell me.
- Keep a cost ledger. Every subagent completion hands you a usage line
  (tokens, tool uses, duration). Record each run on a per-ticket table as it
  lands; it goes in the report and the Process notes.
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
  tagged `(claude lead)`, and the decisions table in the record format the
  tracker doc sets (columns, the anchor line, dated fix-round subheadings).
  Record edits that touch more than one file or add one go through the
  proposal page first (tracker doc, "Record edits go on a proposal page"):
  page approval, then my yes in chat. A claim or close made with
  `ticket.py` is the exception: its output without `--write` and my yes in
  chat are enough.
  On the same yes, write the implementer's drafted `DECISIONS.md entries`
  into the package file. On my "committed" yes, run
  `python3 .scratch/ticket.py close <ID> --sha <sha>`, show me its output,
  and rerun it with `--write` on my yes: it sets the status line to
  `done (YYYY-MM-DD; committed in <sha>)`, swaps a working-tree anchor for
  `Line numbers as of <sha>`, moves the board row and lists the tickets the
  next bullet updates.
- After I confirm a ticket is `done` and committed, update every ticket whose
  `Blocked by` names it (drop the entry; `ready-for-agent` when nothing
  remains), with my yes. Then regenerate the views: `python3 .scratch/now.py`.

## 6. Report

Before writing the report, run `git log -1 --oneline` and `git status
--short` again: another chat may have committed during the batch (it has,
twice). Anchor every table to the Start sha, say what HEAD is now, and check
that no batch file rode into the other chat's commit.
Show me, per ticket: status, the `git add` line and proposed commit message,
the decisions table verbatim, the suite count with and without other chats'
files, and the cost table (one row per subagent run: role, tokens, tool
uses, minutes; a total line per ticket). Then one numbered list of every decision I owe, across tickets,
each with your recommendation, so I answer once rather than in rounds. If the
relay session is not in `ListAgents`, don't wait on it: tell me, and send the
report when it appears. Then send the full handback reports and every process problem you
hit to the session named "Agent teams implementation" with `SendMessage`.
