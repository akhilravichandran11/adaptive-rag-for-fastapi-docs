---
name: batch-lead
description: Coordinate a user-requested batch of approved repository tickets in Codex, with bounded implementers, independent reviews, verification, and approval-gated tracker updates. Not for ordinary single-ticket implementation or review-only requests.
---

# Codex batch lead

Lead the named tickets using the repository tracker as the shared task list.
This is a Codex workflow, independent of Claude's commands and Agent Teams.

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
   before saving them. Record the approved in-progress assignment before the
   worker edits files.

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
- Exact owned files and agreed test Seams.
- Relevant approved decisions and pre-existing changes.
- Required checks and execution restrictions.
- The implementer handback contract.
- An explicit statement that other workers share the checkout: preserve their
  edits and do not revert unrelated work.
- Confirmation that this lead owns reviews and tracker updates.

Use the existing model settings unless the user requests an override.
Stay within the available concurrency limit.

Record the returned agent or thread identity. On resume, inspect existing
workers before replacing one. Confirm the former worker cannot still write
before assigning a replacement to the same files. Do not assume names,
conversation inheritance, or worker survival across sessions.

## 3. Collect and review

Read the complete handback, including actual verification results, decisions,
and proposed scope changes.

implementation-complete means ready for lead review, not ticket done.
Resolve any proposed seam, return-contract, or file-scope change with the user
before the worker implements it.

Inspect the actual files against the starting state and approved ticket.
Read complete untracked and ignored files; an ordinary Git diff omits them.

Once the handback's verification passes, spawn the active-code-simplifier on
that ticket's Python files with the handback's starting commit, and wait for
it. Run the reviews below on the simplified files. Only the first build and
revisions over about 50 lines get the simplifier.

Perform the repository's standards/spec review and then run the
active-code-reviewer. If a diff-oriented review workflow cannot cover new
untracked files, explicitly review their full contents against standards and
the ticket and disclose that method.

Return actionable in-scope findings together to the assigned implementer,
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

## 5. Approval-gated bookkeeping and staging

After verification, present the proposed ticket/board transitions and the
worker's Decisions and Progress for approval. Record them only after the user
says yes. A worker handback alone never closes a ticket.

Preserve the decisions table verbatim. For uncommitted changes, label line
references as "Reviewed working tree based on <sha>, recorded <date>".
Use "Line numbers as of <commit>" only if that commit contains the changes.

Follow the tracker rules for dependent tickets and regenerate action views
after approved tracker updates.

Leave changes unstaged unless the user authorizes staging. When authorized,
stage only the agreed non-ignored files or changes. Never force-add tests or
.scratch. Ask before staging an owned file that also contains unrelated edits.

Propose a Conventional Commit message when requested. Never commit.
Before recommending that the user commit, check that other agents or chats
writing the same checkout are idle; do not assume only this batch matters.

## 6. Report and hand off

Report per ticket:

- Worker implementation status and canonical tracker status.
- Files changed and review findings or their resolution.
- Fresh verification results, separately from prior reported evidence.
- Outstanding decisions, blockers, and scope limitations.
- Staged files only if staging was authorized.

Combine questions for the user into one numbered list with recommendations.
Keep reports in this chat unless the user authorizes another destination.
Do not assume background monitoring or cross-chat synchronization continues
after this turn; agree a separate follow-up mechanism if needed.
