# Cursor harness map (batch lead)

How this lead spawns and reviews workers. Claude agent-teams and Codex
batch-lead skills are **not** available here; use these surfaces instead.

## Lead chat

- Pin one **Local** Agents Window / sidebar chat on the **parent git root**.
- Name it clearly, e.g. `Lead · IDX batch`.
- That chat loads this skill when the user invokes batch-lead / says they are lead.
- Lead does not implement tickets; it coordinates.

## Worker surfaces

### A. Task / subagent (same parent chat)

- Use the Task tool with `ticket-implementer` when available, else a coding
  subagent with the full ticket prompt.
- Best when you want handbacks returned into the lead transcript.
- For parallel editors, request **isolation / worktree** so workers do not
  clobber the same checkout.
- `/multitask` if the product supports async parallel subagents from one plan.

### B. Sibling Agents Window chats

- User (or lead instructions to the user) starts one new Agent chat per ticket.
- Lead prompt tells the user the exact first message to paste into each chat.
- Lead "reviews remaining chats" by reading each chat's handback / diff in the
  sidebar — there is no teammate message bus; the user or lead relays.
- Closest UX to Claude Desktop fan-out with visible side sessions.

### C. Worktree agents

- `/worktree` or Agents Window worktree for isolated checkouts.
- Cut from the branch the main checkout is on (usually `feat/data-ingestion`),
  not a stale `main`.
- `.scratch/` does **not** appear in the worktree. Pass **absolute paths** to
  canonical scratch files under the main checkout (F001 /
  `docs/agents/issue-tracker.md` "Git worktrees").
- Prefer worktrees for **code-only** tickets; leave scratch status flips to the
  lead on the main checkout.

### D. Cloud Agents

- Start from Cloud picker, cursor.com/agents, or `/in-cloud`.
- Clone is GitHub only: **no** `.scratch`, no dirty local files.
- Paste the full ticket markdown (and any settled rulings) into the Cloud
  prompt. Prefer tickets already summarized on Linear DEF-*.
- Tracked `tests/` and CI mean Cloud can run `uv run pytest` after env/secrets
  setup.
- Lead reviews via Agents Window artifacts / PR, not via local `.scratch` edits
  from the VM.

## Reviewing "the remaining chats"

In Cursor, remaining work is **other agent rows in the Agents Window**, not
Claude teammates.

1. List each worker by ticket id and surface (Task / chat name / worktree /
   Cloud URL).
2. For each idle worker: open handback or diff; run lead reviews (simplifier →
   code-review tier → active-code-reviewer).
3. Send one consolidated fix message back to that worker (resume Task, or paste
   into that chat / Cloud follow-up).
4. When all clean: one batch report in the lead chat; user commits.

## Mode choice cheat sheet

| Ticket shape | Prefer |
|---|---|
| Scratch + code, needs tracker edits | Local Task or sibling chat on main checkout |
| Parallel pure code, no scratch writes | Worktree |
| Laptop closed / phone monitor / pytest on GitHub | Cloud |
| Workers must debate each other | Do not use Cursor batch-lead; use Claude terminal team |

## Related

- Claude: `.claude/commands/batch-lead.md`
- Codex: `.agents/skills/batch-lead/SKILL.md`
- Workbench plan: `.scratch/agent-workflow/targets/mobile-remote-workbench/plans/plan.md`
