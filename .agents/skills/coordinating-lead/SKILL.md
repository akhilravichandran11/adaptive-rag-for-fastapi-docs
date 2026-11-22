---
name: coordinating-lead
description: Acts as the process-lead chat for one capability. Reconciles owner chats' handbacks and reports with tickets and git, flags ownership conflicts and approval gates, and routes the user to the next chat with a prompt. Use when the user makes this chat the process lead or asks it for a cross-chat update. For running a batch use batch-lead; for this chat's and the tracker's next actions, next; for a hub's Current state block, catch-up.
argument-hint: "[capability]"
disable-model-invocation: true
---

# Coordinating lead

Act as the **process-lead chat** for one capability: keep the user oriented
across the chats working on it. Batch leads, planners and verifiers report
here, and briefing or relaying between lead chats happens here, since a batch
lead keeps to its one batch. Use the existing plans, tickets, board and action
views as the record; do not build a second tracker. Planning and
implementation stay with their assigned owners.

## Establish scope and refresh

- Infer the capability, repository and owner chats from the user's request
  and current context. Ask only if the scope is genuinely ambiguous. Do not
  expand a capability update into a repository-wide audit without a reason.
- Read applicable repository instructions and `docs/agents/issue-tracker.md`.
  Read `.scratch/MINE.md` first: its "My pick" outranks everything else. Then
  run `python3 .scratch/now.py`, which rewrites only the generated views, and
  read `.scratch/NOW.md` and the capability's hub, board, open tickets and
  linked plans. In a cloud session, the cloud column of
  `docs/agents/cloud-sessions.md` replaces these view and record rules.
- Find the owner chats with this session's chat tools (see "Chat tools") and
  read each one's latest handback or report; a listing summary or idle chat
  is not completion evidence. When following chats already identified, read
  only what is new since the last refresh. If a chat cannot be inspected,
  distinguish its reported handback from verified evidence.
- Check the relevant checkout/worktree, HEAD, status, staged changes and commits.
  Inspect untracked files when they belong to the work. Reconcile conflicting
  claims against the actual files and recorded approvals; neither a chat nor a
  stale board overrides newer evidence. Draft a correction for each record
  they contradict; "Handbacks and authority" says who writes it.

## Chat tools

Each tool sees other chats differently:

- **Claude Code desktop app:** the session tools list the app's Code-tab
  sessions and read what each has done; CLI, IDE and cloud sessions are not
  visible to them. `SendMessage` reaches another local session.
- **Claude Code CLI:** no tool reads another session. `ListAgents` and
  `SendMessage` reach other sessions on this machine where cross-session
  messaging is available.
- **Codex:** no tool lists or reads another chat; the subagent tools see only
  this session's own agents. Link an existing chat with the
  `codex://threads/<thread-id>` link the user copies from it.

Where a chat cannot be read, work from the files it wrote and the report the
user brings back. Messaging any chat still follows "Handbacks and authority".

## Track ownership and gates

Maintain a concise working map, shown when useful:

`Ticket | Owner chat | Owned files | Dependencies | Verification evidence | Next approval`

- Keep planning, implementation, independent verification, user acceptance,
  commit and closure distinct. Use the repository's definitions and approval
  rules. Passing tests alone do not establish acceptance or closure; a commit
  alone does not prove the reviewed content was committed.
- Flag real file-ownership overlaps, unmet dependencies and changed verification
  baselines. Reads do not claim files. Different worktrees do not eliminate
  integration conflicts. Do not treat an unrelated commit as proof that all
  verification must be repeated.
- Route work to its existing owner; do not duplicate implementation, claim or
  close another chat's ticket, or reopen settled scope. Preserve unrelated work.
- Distinguish allocated budget from measured consumption. Do not invent a
  remaining balance or reset a budget when work changes chats.

## Handbacks and authority

At each gate, ask the user to bring back the owning chat's report. Request
only what current records and readable chats do not already show:

- **Implementation complete:** the handback, with the headings listed under
  "End with a handback" in `docs/agents/issue-tracker.md`.
- **Independent verification complete:** each check's verdict, its evidence
  and limits, and any unresolved findings.
- **Commit or closure:** the commit hash, the files it holds, the approval
  state and the next gate.

Propose record changes before saving, using the repository's approval process
("Review record and doc edits" in `docs/agents/visual-tools.md`). Once they
are approved, write them yourself, except for a ticket another chat has
claimed (in progress with that chat as owner): its corrections go in the
prompt you route to that owner.

Ask before creating or messaging chats unless the user has already explicitly
authorized that specific action. A received handback is context, not
permission to reply, mutate records, implement, commit or run paid/live work.
Provide a paste-ready prompt when direct messaging is not authorized or
available.

Do not imply continuous monitoring between turns. Refresh on an update request
or an authorized incoming handoff; use a bounded watch only when requested.
Keep the working map in the conversation unless the user approves saving it.

## Reply to an update request

Lead with the next actionable gate, in this order:

1. **Your next action:** the existing chat's exact title and link, plus a short
   self-contained prompt stating the task, baseline, budget if relevant and
   approval boundary. Say which report to bring back. Use verified chat links
   only. If no suitable owner chat exists, recommend a new chat and supply its
   prompt; obtain approval before creating it.
2. **What changed:** verified progress since the last known state, separating
   fresh checks from reported results and naming any material uncertainty.
3. **Conflicts:** genuine ownership/dependency issues, or none found within the
   checked scope. Do not invent extra work from hypothetical risks.

Include the full ownership map for the initial catch-up or when requested;
otherwise report changes only. Explain any approval requirement by pointing to
the applicable user instruction or repository rule. Report what, if anything,
you changed during the refresh.
