# Local Findings, Planning, and Issue Tracker

This repository keeps findings, roadmaps, working plans, and implementation
tickets in an ignored `.scratch/` workspace. The rules in this document define
the workflow; the local artifacts remain outside the published portfolio.

## Master index

`.scratch/_index.md` is the top-level index of every capability workspace. Start
there to find a capability, then open its hub (`<capability>/_index.md`). Add a
row to the master index whenever you create a capability.

## Workspace layout

A **capability** is a stable outcome area or initiative (not a filename). Inside
it, work is divided into **targets** — the independently workable units a single
session owns end to end. A target is usually one file, sometimes one concern.
Each target owns its findings and plans; the whole capability shares one board.

```text
.scratch/
├── _index.md                          # master index: all capabilities
└── <capability>/
    ├── _index.md                      # capability hub: shared context + targets table + board link
    ├── roadmaps/                      # optional: capability-wide direction and sequencing
    │   └── <roadmap-name>.md
    ├── targets/
    │   └── <target-slug>/
    │       ├── findings/
    │       │   ├── _index.md           # this target's findings register
    │       │   └── F<NNN>-<finding-slug>.md
    │       └── plans/
    │           └── <concern>/plan.md
    └── issues/
        └── _index.md                  # one shared board for the whole capability
```

Use lowercase kebab-case for capability, target, concern, roadmap, finding, and
ticket names. A capability with a single target still nests it under one
`targets/<slug>/`. A finding or ticket may touch multiple files; its target
describes the unit of work, not a single file.

The current capabilities are listed in the master index, which records the layout
each one follows. `data-curation-pipeline` predates this standard (root-level
`findings/` and `plans/`, no `targets/`) and is pending migration — until then,
treat its root `findings/` and `plans/` as a single implicit target.

## Capability roadmaps

Use optional `<capability>/roadmaps/<roadmap-name>.md` files for direction,
outcomes, and sequencing across stages or targets within a capability.
Plans remain the place for a particular concern's design and implementation
approach.

Link roadmaps from the capability hub. For the legacy data-curation workspace,
link them from its existing entry in `.scratch/_index.md` until it has a hub.
Link to canonical findings, plans, and tickets; keep their statuses in those
records rather than maintaining a second status board in the roadmap.

Roadmaps are non-executable context. Approval of a roadmap's direction does
not authorize implementation; the issue-status rule below still applies.

## One session, one target

Parallel sessions (Codex or Claude) stay out of each other's way by working
different targets:

- A session works **one target** and creates or edits files only under that
  target's `targets/<slug>/` directory.
- User-requested roadmap maintenance may update capability-level `roadmaps/`
  and its discovery links; this does not authorize work on other targets.
- Finding IDs (`F<NNN>`) are scoped to the target; the folder disambiguates them,
  so two targets may each have an `F001`.
- Ticket IDs on the shared board are prefixed by target (e.g. `TAX-01`, `SDS-01`).

## Coordinating concurrent work (the `Touches` rule)

Targets group work; **files** are what serialize it. This is file-scoped locking
(one writer per file), enforced on the board.

Every ticket declares a `Touches:` list — the source files (and where useful, the
specific functions or symbols) it will **modify**. Reads and imports do not lock;
only writes.

Board rule: **at most one non-`done` ticket may be In-Progress per file it
touches.** A ticket whose `Touches` overlaps an in-progress ticket goes
**Blocked — waiting on: `<ticket>` (file `<path>`)**. A session may claim any
ready ticket whose files are all free.

This is what makes parallel sessions safe:

- **Parallel** when `Touches` sets are disjoint — e.g. one session on
  `stage3_learning_taxonomy.py` and another on
  `stage5_generate_synthetic_dataset.py`, regardless of which target folder they
  live in.
- **Serialized** when they overlap — e.g. several concerns that all modify
  `stage2_knowledge_domains.py` run one at a time.

Shared-infrastructure files are the contention hotspots: `stage0_run_pipeline.py`
(orchestrator), `pipeline.py` (artifact names), `schemas.py`, `config.py`. Keep
tickets that *modify* them small, and **split "build the module" (touches only the
new file → parallel) from "wire it in" (touches shared infra → serialized)**.

**Ordering / baselines.** When one ticket changes another's baseline — e.g. one
rewrites a function's output while another moves that same function and verifies
it against the pre-change artifact — record the dependency as a blocker and state,
in each plan's verification, which prior tickets it assumes have landed. Never
leave a mutually-invalidating pair both `ready-for-agent`.

## Findings, plans, and tickets

Findings record review results, bugs, improvements, and caveats before work is
approved. A finding may be closed, deferred, promoted to a plan when design is
needed, or promoted directly to a ticket when the solution is clear and approved.

Use these finding types:

- `bug`: confirmed incorrect behavior.
- `improvement`: behavior works but could be made better.
- `caveat`: a limitation, uncertainty, or constraint whose required action is
  not yet decided.

Plans contain exploration from design or grill sessions: alternatives,
questions, tradeoffs, and decisions. A plan lives under its target's
`plans/<concern>/` and is ready to become tickets only after the user approves it.

Issues contain approved implementation work. Create one Markdown file per
ticket in dependency order, using the local template from the `to-tickets`
skill. Each ticket must state its blockers, its `Touches` list (the files, and
where useful the functions/symbols, it will modify), and acceptance criteria.

Only an issue with `Status: ready-for-agent` or `Status: in-progress`
authorizes implementation. Material under `findings/`, `roadmaps/`, and
`plans/` remains non-executable context.

## Finding status

- `open`: recorded but not yet decided.
- `investigating`: evidence or expected behavior is being established.
- `planning`: linked to a plan that is being refined or awaiting ticketing.
- `ticketed`: promoted to an approved implementation ticket.
- `deferred`: intentionally postponed.
- `closed`: resolved or no action is required.

## Findings register (`targets/<slug>/findings/_index.md`)

Keep one row per finding in the target's findings register:

```text
# Target: <target-slug> — findings

| ID | Type | Finding | Status | Next step |
|---|---|---|---|---|
| F001 | bug | <short description> | open | Triage |
```

Each finding file records its evidence, impact, possible direction, decisions
needed, execution considerations, and links to related plans or tickets. A
possible direction is a proposal, not authorization to implement it. The finding
file is authoritative if its status differs from the register.

## Convention status

- `proposed`: in the code, awaiting a ruling.
- `accepted`: the shape later code should follow.
- `rejected`: to be undone; the entry records what has to change.

## Conventions register (`docs/conventions.md`)

A finding records something *discovered* — a corpus defect, a library quirk. A
convention records something *introduced*: a shape later code is expected to copy.

Conventions describe the shipped code, so unlike findings they live **outside
`.scratch/`**, in the tracked `docs/conventions.md`, and are repo-wide rather than
target-scoped. Do not create a per-target conventions folder.

Keep one row per convention in the register:

```text
# Code conventions

| ID | Convention | Introduced | Status |
|---|---|---|---|
| C001 | <short description> | <ticket id> | proposed |
```

Each entry records what the pattern is, why it was chosen, what it replaces, its
blast radius (the files that change if it is rejected), and the alternatives
considered. Recording one is a proposal, not authorization for later code to copy
it while it is still `proposed`. The rule that obliges an agent to record one is in
[`../../AGENTS.md`](../../AGENTS.md).

## Capability hub (`<capability>/_index.md`)

The hub is the entry point for a capability. It holds the shared context every
target needs (references, conventions, how the work is measured) and a table of
targets linking to each target's findings register and plan, plus a link to the
board. Keep target-specific detail in the target, not the hub.

## Issue status

- `ready-for-agent`: approved, and every listed blocker is complete.
- `in-progress`: one agent or session owns the ticket.
- `blocked`: work cannot continue until the recorded condition changes.
- `done`: every acceptance criterion is checked, its verification completed,
  and no convention it introduced is still `proposed` in
  [`../conventions.md`](../conventions.md).

## Board index (`issues/_index.md`)

Each capability keeps a single `issues/_index.md` board so any session (Codex or
Claude) can see the frontier across all targets at a glance and avoid two sessions
grabbing the same ticket. Ticket lines use the `<PREFIX>-NN` id. It maps directly
onto the statuses above:

```text
# <capability> — board

## To Do (ready-for-agent)
- [ ] S3-01 build stage3 module — Touches: stage3_learning_taxonomy.py

## Doing (in-progress)
- [ ] S5-01 refactor stage5 generator — owner: chatB, since 2026-09-10 — Touches: stage5_generate_synthetic_dataset.py
- [ ] Q-02 fix stage2 weighting — owner: chatA, since 2026-09-10 — Touches: stage2_knowledge_domains.py (average_quality)

## Blocked
- [ ] Q-03 stage2 dedup — waiting on: Q-02 (file stage2_knowledge_domains.py); Touches: stage2_knowledge_domains.py
- [ ] S3-02 wire stage3 into orchestrator — waiting on: pipeline.py free; Touches: stage0_run_pipeline.py, pipeline.py

## Done
- [x] Q-01 <ticket-slug>
```

In the example above `S5-01` and `Q-02` run in parallel because their `Touches`
sets are disjoint; `Q-03` is blocked because it would write the same
`stage2_knowledge_domains.py` as `Q-02`; `S3-02` is blocked on the shared
orchestrator/`pipeline.py`.

Rule: a session claims a ticket by moving its line to **Doing** with an owner
**before** starting work, and moves it to **Done** only after every acceptance
criterion is verified. The per-ticket file's `Status:` line and this board must
agree; if they disagree, the ticket file wins and the board is corrected.

Before implementation, read the full ticket and confirm that every blocker is
`done`. Work within one ticket's scope and request user approval before expanding
it. Mark the ticket `done` only after all acceptance criteria are satisfied.

## Workflow

1. Find or create the capability in `.scratch/_index.md`, then open its hub.
2. Pick an existing target or create a new one under `targets/<slug>/`.
3. Record a discovered bug, improvement, or caveat in the target's `findings/`
   and its `_index.md` register.
4. Triage the finding with the user: close it, defer it, promote it to a plan,
   or approve it for a direct ticket.
5. For work needing design, capture the alternatives and decisions under the
   target's `plans/<concern>/plan.md` and iterate until the user approves it.
6. Use `to-tickets` to create tracer-bullet issues from an approved finding or
   plan; add each to the shared board with a `<PREFIX>-NN` id.
7. Start a fresh implementation session with one ready ticket.
8. Verify its acceptance criteria, record any convention the work introduced
   in `docs/conventions.md`, update its status, then select the next
   unblocked ticket.

Use this session-start instruction for finding triage:

```text
Read docs/agents/issue-tracker.md and <finding-path>. Review the evidence and
help me decide its next step. Do not implement it without a ready ticket.
```

Use this session-start instruction to rule on proposed conventions:

```text
Read docs/agents/issue-tracker.md and docs/conventions.md. For each `proposed`
convention, show me what it replaces, its blast radius, and the alternatives, then
record my ruling. Do not change code outside undoing a convention I reject.
```

Use this session-start instruction to work a target in a fresh chat:

```text
Read docs/agents/issue-tracker.md and .scratch/<capability>/_index.md.
Work the <target-slug> target only. Create/record findings and plans under
targets/<target-slug>/, and add any tickets to issues/_index.md with the
<PREFIX>- id. Do not touch other targets.
```

Use this session-start instruction with Codex or Claude to implement a ticket:

```text
Read docs/agents/issue-tracker.md and <ticket-path>. Confirm the ticket is ready,
then work only within its scope and verify every acceptance criterion.
```

## Action views (what is left to do)

`.scratch/now.py` generates three views from the tracker's own files, so nobody keeps
a second status list by hand:

- `.scratch/NOW.md` — Needs you, Ready for an agent, In progress, Blocked. Open this first.
- `.scratch/views/needs-you.md` — only what needs the user, by capability.
- `.scratch/views/everything.md` — every open ticket, finding and inbox line.

They are rebuilt from scratch on every run, carry a do-not-edit header, and are never
edited. They read:

- each ticket's and finding's `**Status:**` line (an `open` finding counts as needing
  the user's triage);
- a `**Needs you:** <what>` line, placed directly under `**Status:**` in any ticket or
  finding, for as long as something waits on the user;
- `.scratch/INBOX.md`, which holds only loose items with no ticket or finding, as
  `- [<capability or general>] <text>`; a bullet with `**Needs you:**` is the user's.

Hooks in the user's global Claude and Codex config run it at session start (printing
one summary line into the agent's context) and after each reply. Run it by hand with
`python3 .scratch/now.py` in any other tool.

**At the end of a chat** that touched `.scratch/` work or left something for the user:

1. Set the `Status:` of every ticket and finding the chat changed.
2. Add a `**Needs you:**` line for each thing now waiting on the user; delete the lines
   the user has done.
3. Add or delete `.scratch/INBOX.md` lines for loose items (merges, pushes, decisions
   with no ticket).
4. Run `python3 .scratch/now.py`.
5. End the reply with the Needs-you count and a link to `.scratch/NOW.md` rather than
   re-listing everything.

**When the user asks what is next** or what their action items are, run `now.py` and
answer from `.scratch/NOW.md`; do not rebuild the list from boards and memory.

## Git worktrees

The checkout where `.scratch/` is created owns the canonical local workspace.
Ignored files do not appear automatically in another Git worktree. Give a
worktree session the absolute path to the canonical finding, plan, or ticket,
and keep its status in that single file rather than creating a second copy.
