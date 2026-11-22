---
name: coordinating-lead
description: Coordinate an existing capability across planning, implementation and verification chats. Reconcile handoffs with canonical tickets and Git, identify ownership conflicts and approval gates, and route the user's next action. Use for a lead-chat role or cross-chat progress updates, not for executing a batch of tickets.
---

# Coordinating lead

Keep the user oriented across the chats working on one capability. Use the
existing plans, tickets, board and action view as the record; do not build a
second tracker. Planning and implementation stay with their assigned owners.

## Establish scope and refresh

- Infer the capability, repository and lead/owner chats from the user's request
  and current context. Ask only if the scope is genuinely ambiguous. Do not
  expand a capability update into a repository-wide audit without a reason.
- Read applicable repository instructions and tracker rules. Where present,
  consult the canonical plans, ticket files, board, NOW.md and user priorities.
  Check the repository's generated-view procedure. If refreshing a view would
  write records, propose that refresh before saving unless already approved.
  For strictly read-only work, read the existing view and note staleness.
- Discover available chat tools. Locate the relevant chats and read their latest
  handoffs; a listing summary or idle chat is not completion evidence. Prefer
  compact progress snapshots when following already identified chats. If a chat
  cannot be inspected, distinguish its reported handoff from verified evidence.
- Check the relevant checkout/worktree, HEAD, status, staged changes and commits.
  Inspect untracked files when they belong to the work. Reconcile conflicting
  claims against the actual files and recorded approvals; neither a chat nor a
  stale board overrides newer evidence. Propose corrections without saving them.

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

## Handoffs and authority

At implementation completion, independent verification completion, and
commit/closure, ask the user to bring back the owning chat's handoff. Request
only missing essentials: ticket and owner, files/commit, evidence and limits,
unresolved findings, approval state, and next gate. Do not ask for evidence
already available in current records or accessible chats.

Propose record changes before saving, using the repository's approval process.
Ask before creating or messaging chats unless the user has already explicitly
authorized that specific action. A received handoff is context, not permission
to reply, mutate records, implement, commit or run paid/live work. Provide a
paste-ready prompt when direct messaging is not authorized or available.

Do not imply continuous monitoring between turns. Refresh on an update request
or an authorized incoming handoff; use a bounded watch only when requested.
Keep the working map in the conversation unless the user approves saving it.

## Reply to an update request

Lead with the next actionable gate and keep the response to:

1. **What changed:** verified progress since the last known state, separating
   fresh checks from reported results and naming any material uncertainty.
2. **Conflicts:** genuine ownership/dependency issues, or none found within the
   checked scope. Do not invent extra work from hypothetical risks.
3. **Your next action:** the existing chat's exact title and link, plus a short
   self-contained prompt stating the task, baseline, budget if relevant and
   approval boundary. Say what handoff to bring back.

Use verified chat links only. If no suitable owner chat exists, recommend a new
chat and supply its prompt; obtain approval before creating it.

Include the full ownership map for the initial catch-up or when requested;
otherwise report changes only. Explain any approval requirement by pointing to
the applicable user instruction or repository rule. Report what, if anything,
you changed during the refresh.
