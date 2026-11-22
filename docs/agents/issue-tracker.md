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
- A `catch-up` run may rewrite its capability hub's `## Current state` block
  and apply the record corrections the user approves; this does not
  authorize work on any target.
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
A plan's header records `**Budget:** Nh`, the total hours of the user's own time
the user stated for the concern; a plan request without one gets that question
back before any planning. Before asking for approval, run the plan judge
(`scripts/judge_plan.sh`, see [`plan-complexity-judge-design.md`](plan-complexity-judge-design.md)).

Issues contain approved implementation work. Create one Markdown file per
ticket in dependency order, using the local template from the `to-tickets`
skill. Each ticket must state its blockers, its `Touches` list (the files, and
where useful the functions/symbols, it will modify), its `Seams` list (the
public interfaces its tests will exercise, agreed with the user when the ticket
is written), and acceptance criteria. The `to-tickets` template has neither
`Touches` nor `Seams`; add both directly under its `**Blocked by:**` line.
Add `**Budget:** Nh` next to `Touches`: the hours of the user's own time the
ticket may consume end to end; a plan's tickets sum to no more than the plan's
budget.

A ticket records progress under a `## Progress` heading at its end, one dated
line per entry: `- YYYY-MM-DD (<tool>): <what changed>; <what was verified>;
<what is next>`. `<tool>` is `claude` or `codex`, with ` lead` added when a
batch lead writes the line for an implementer (`claude lead`); nothing else
goes in the parentheses, and a chat or worktree name goes in the line's text
when it matters. An agent adds a line only after the user says yes.

Every ticket, whichever path built it, keeps the implementer's decisions table,
verbatim, under a `## Decisions` heading placed above `## Progress`, with the
columns `Decision | Alternative rejected | Why | Where`. The table opens with
one line that anchors its `Where` column: `Line numbers as of <sha>` when that
commit holds the change, or `Reviewed working tree based on <sha>, recorded
<date>` when the table is written before the commit; the lead replaces the
second form with the first on the "committed" yes. A fix round or a later
batch adds its rows under a dated `### <date> fix round` subheading rather
than editing earlier rows. `Where` names a function or a heading; add a
line number only to the newest rows, because earlier lines move with each
fix round. It is added with the Progress line, on the same
yes. The table is the complete record and stays private with the ticket; the
rows a code pointer cites also go to the package's `DECISIONS.md`, as the
next paragraph says.

A package's tracked `DECISIONS.md` (CLAUDE.md, S002) gets a dated entry for
each decision the code points at with a one-line pointer, and nothing else: a
guard, a default, a data shape whose reason is not in the code. The entry
names the ticket and the files. The implementer drafts these entries in its
handback under `DECISIONS.md entries`; the lead, or the solo chat, writes
them on the same yes as the Progress line, because implementers do not edit
`DECISIONS.md`. Everything else in the table stays in the ticket.

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
- `superseded by S00N`: an S-rule in `docs/simplification-conventions.md`
  replaced it; follow the S-rule.

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

Under its title and concern, a hub carries a dated `## Current state` block:
where the capability stands, what git shows in flight, what changed since the
last block, and what comes next and why. It links records rather than
restating their status, and only a `catch-up` run writes it. To pick a
capability back up, run `/catch-up <capability>` (Codex:
`$catch-up <capability>`); it re-derives the block from the records and git,
and proposes fixes for records git has outrun.

## Issue status

- `ready-for-agent`: approved, and every listed blocker is complete.
- `in-progress`: one agent or session owns the ticket.
- `blocked`: work cannot continue until another ticket or an outside condition
  changes; the status line names it.
- `deferred`: parked by the user's choice, not by a dependency. The ticket keeps a
  **Why deferred:** paragraph and a revisit condition. It locks no files. It returns
  to `ready-for-agent` or becomes `wont-do` only on the user's say-so.
- `wont-do`: the user decided no work is needed. The ticket keeps a **Why:** line;
  any finding it served gets its status updated to match. Treated like `done` by the
  views.
- `done`: every acceptance criterion is checked, its verification completed,
  and no convention it introduced is still `proposed` in
  [`../conventions.md`](../conventions.md).
  The status line reads `done (YYYY-MM-DD; committed in <sha>)`, written on
  the user's "committed" yes; a bare `done` is a record defect.
  A ticket's Verification names the reviews as "reviews per step 8 for a diff
  of this size", never as a list of agents; the Progress line records which
  tier ran.

Only the user sets `deferred` or `wont-do`; an agent may propose either.

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

## Deferred
- [ ] Q-04 cache stage2 output — deferred 2026-10-05 (user): revisit if runs get slow

## Won't do
- [x] Q-05 <ticket-slug> — won't do 2026-10-05 (user): <one-line why>

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

`python3 .scratch/ticket.py` makes the three mechanical record edits: `claim`
flips a ticket and its board row to in-progress and saves the files' starting
state; `packet` prints the contract, that starting state and the change for a
reviewer; `close` writes the done form and lists the tickets it unblocks. It
refuses when the ticket and the board disagree. Hand edits stay allowed.

Before implementation, read the full ticket and confirm that every blocker is
`done`. Also check that the ticket's reason still holds given what its blockers
delivered; if a blocker removed it (a cheaper path now exists, the cost it targeted
is gone), say so and offer `deferred` or `wont-do` before proposing a design. Work
within one ticket's scope and request user approval before expanding it. Mark the ticket `done` only after all acceptance criteria are satisfied.

## Workflow

1. Find or create the capability in `.scratch/_index.md`, then open its hub.
2. Pick an existing target or create a new one under `targets/<slug>/`.
3. Record a discovered bug, improvement, or caveat in the target's `findings/`
   and its `_index.md` register.
4. Triage the finding with the user: close it, defer it, promote it to a plan,
   or approve it for a direct ticket.
5. For work needing design, run a `grilling` session, or `/grill-with-docs`
   (Codex: `$grill-with-docs`) when the session should also settle project
   terms or record architecture decisions. Capture the alternatives and
   decisions under the target's `plans/<concern>/plan.md`, and iterate until
   the user approves it.
6. Use `to-tickets` to create tracer-bullet issues from an approved finding or
   plan, each with `Touches` and `Seams`, and check each ticket against the
   code before offering it (see the override below); add each to the shared
   board with a `<PREFIX>-NN` id.
7. Pick the path, then start. Two or more ready tickets with disjoint
   `Touches` and no paid step run as one batch under `/batch-lead` (Codex:
   its own `batch-lead` skill), one implementer per ticket. One ready ticket
   runs as a batch of one when its Budget is two hours or more, or it adds a
   module: a reviewer that did not write the code, and the handback record,
   are worth a lead at that size. A smaller ticket, a paid or live run, or
   sequential same-file work runs in a single supervised chat launched with
   `/implement` (Codex: `$implement`), and that chat ends with a handback
   (see the override below). A ticket with a paid or live step never runs in
   a cloud session. Name the path and the reason in the ticket's first
   Progress line. Write tests with `tdd`, only at the ticket's `Seams`.
8. Review the change. This step is the one rule for every path: a single
   `/implement` chat, a `/batch-lead` implementer, and Codex. Tickets point
   here instead of listing reviews.
   - If Python changed, run `active-code-simplifier` on the changed files
     with the starting commit, before any review. It runs once, on the
     first build; a fix round does not get it again.
   - Then by the size of the Python diff: under about 50 lines, read the diff
     yourself against the ticket and the standards sources, then run the
     `active-code-reviewer` agent; under about 200, run `code-review` with
     both axes in one subagent, then `active-code-reviewer`; above that, run
     `code-review` as it ships, then `active-code-reviewer`. Markup, JSON and
     docs do not count toward the size; a docs-only ticket gets no reviews.
   - A fix round that changes behaviour or an interface gets
     `active-code-reviewer` again plus your own read; a rename-only or
     comment-only round gets your read alone. On a re-review, a finding
     that needs an input no ticket, board or caller produces today becomes a
     follow-up finding by default; only a false write or a false record
     reopens the code. The Progress line names the tier that ran.
   Verify the acceptance criteria, record any convention the work introduced
   in `docs/conventions.md`, update the status, then select the next
   unblocked ticket. `uvx pre-commit run --files` reads the files on disk,
   except the reserved-words hook, which reads the git index: where nothing
   is staged, it checks the last commit and skips new files. Run that hook
   through a temporary index instead, which leaves the shared index alone:

   ```bash
   i=$(mktemp -u)
   GIT_INDEX_FILE=$i git read-tree HEAD && GIT_INDEX_FILE=$i git add <files>
   GIT_INDEX_FILE=$i scripts/hooks/block-reserved-words.sh <files>; echo "exit $?"
   ```

   Before a ticket's Linear issue moves to Done, read the issue's whole
   description against what the ticket delivered, and ask the user where
   each item left over goes. Closing a ticket includes its dependents: once it is
   `done` and committed, open every ticket whose **Blocked by** names it,
   remove the entry, and set `ready-for-agent` when nothing remains. A ticket
   left `blocked` on a finished blocker is stale state the next agent will
   trip on.

### Skills and repo overrides

The steps above name the skill that performs each one; most are Matt Pocock's
skills (MIT, see `THIRD_PARTY_NOTICES.md`). If a skill is not available in the
current tool, follow the step by hand. In a cloud session, the Mac-only tools
table in [`cloud-sessions.md`](cloud-sessions.md) says which of them are
missing. Where a skill's own
instructions conflict with this repo, this repo wins:

- **No commits in a local checkout** (cloud sessions:
  [`cloud-sessions.md`](cloud-sessions.md)). `implement` ends by committing;
  skip that step and leave the changes uncommitted. End the report with the
  `git add <paths>` line (only the files this ticket changed) and a
  Conventional Commit message; the user runs both. For several logical
  commits, use `commit-planning` instead.
- **End with a handback.** Whichever path built it, the ticket's report uses
  the implementer's headings (`.claude/agents/ticket-implementer.md`,
  `.codex/agents/ticket-implementer.toml`): Status (`implementation-complete
  | partial | blocked - reviews: pending lead`; the first means ready for the
  lead's review, never the tracker's `done`), rulings needed, Files,
  Decisions table or "none", Changed mid-way, Verification with the counts
  and the other-chats caveat, Noticed not touched, Proposed convention,
  Process notes, and the drafted Progress line. The table goes into the
  ticket on the same yes as the Progress line, and every Noticed item is
  disposed of on that yes: a finding, an INBOX line, or "dropped, because".
- **Reviews follow step 8.** `implement` runs `code-review` on everything;
  here the tier in step 8 decides whether `code-review` runs at all, and the
  `active-code-reviewer` agent required by `AGENTS.md` runs in every tier.
- **`code-review` sees the working tree.** It diffs `<fixed point>...HEAD`,
  which is empty when nothing is committed. Give it the starting commit as
  the fixed point, have it review `git diff <start>` and read new untracked
  files in full, and name `docs/conventions.md`,
  `docs/simplification-conventions.md` and
  `.scratch/code-simplification/questions.md` as its standards sources.
- **Tests only at the ticket's `Seams`.** These are the pre-agreed seams `tdd`
  asks for. Ask the user before testing at a seam the ticket does not list.
- **Checks.** This repo has no type checker; where `implement` says to run
  typechecking, run `uv run ruff check` instead. Acceptance still requires
  `uv run pytest` to pass.
- **The board rules still apply.** Claim the ticket in **Doing** before
  starting, modify only files in its `Touches`, and mark it **Done** only as
  described under Issue status.
- **Record edits go on a proposal page.** When edits to findings, plans,
  tickets or boards touch more than one file or add one, show them as a
  proposal page before anything is written; see
  [`visual-tools.md`](visual-tools.md). A status flip made by `ticket.py`
  needs only the yes in chat: show its output without `--write`, then run it
  with `--write` on the yes.
- **A ticket's edits are approved by the ticket.** Its `Touches`, Seams and
  acceptance criteria were approved at `to-tickets`, so the per-edit approval
  of `pre-edit-review` and `pre-edit-review-except-tests` is not used inside
  ticket work, by the user or the agent, unless the ticket's Context asks for
  it. The review points for ticket work are the handback and the reviews in
  step 8. Record edits keep the proposal page; edits outside any ticket keep
  the pre-edit skills.
- **A ticket is checked against the code before it is offered.** Before a
  ticket goes to the user for approval, open every file in its `Touches` and
  confirm that each symbol, `file:line` and contract it names (field names,
  return shapes, config keys) exists as stated; a ticket that asserts a
  contract the code does not have is not ready. A ticket with a paid or live
  run also carries an `**Inputs:**` line under `Touches`: where its inputs
  came from (the user's own lists or a named file, never drafted for the
  ticket) and the free check that validated its expectation (a corpus grep
  per topic, retrieve-only lookups) before any paid call.
- **Build a ticket in small steps** (adapted from Addy Osmani's
  `incremental-implementation`, MIT; see `THIRD_PARTY_NOTICES.md`). Inside one
  ticket:
  - Write the smallest working step first and run its tests before writing
    more. If a step would leave more than about 100 lines untested, split the
    step, or propose splitting the ticket.
  - One logical change per step: never mix a refactor with the ticket's
    behavior change.
  - After each step, checkpoint instead of committing: `uv run ruff check` and
    the step's tests pass, then report what the step did, so the user can
    commit it with `commit-planning`.
  - List anything worth fixing outside the ticket's `Touches` under
    **Noticed, not touched** in the report; record it as a finding only when
    the user says yes. Never fix it in passing.
  - Start with the simplest version that meets the acceptance criteria. Three
    similar lines beat an abstraction with fewer than three uses.
- **`grill-with-docs` writes the plan first.** Its questions, alternatives and
  decisions go in the target's `plan.md`, as with `grilling`. It may also write
  two repo-wide files, tracked like `docs/conventions.md`; these are the only
  files outside its target a grilling session may write:
  - `GLOSSARY.md` at the repo root, updated as each project-specific term is
    resolved, in the `domain-modeling` format. No implementation details.
  - `docs/adr/NNNN-<slug>.md`, only for a decision that passes all three of
    `domain-modeling`'s tests (hard to reverse, surprising without context, a
    real trade-off). Draft it under an **ADR candidates** heading in the plan,
    create the file only after the user approves the plan, and link it from
    the plan. Like a plan, an ADR records a decision; it does not authorize
    implementation.
- **One decision, one record.** A new code shape that later code should copy
  goes in `docs/conventions.md`, not an ADR. A decision that fails the ADR
  tests stays in the plan only.
- **Read before exploring.** If `GLOSSARY.md` or `docs/adr/` exist, read the
  glossary and any ADRs touching the area before working a target, and use the
  glossary's terms in findings, plans and tickets.

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

Use this session-start instruction with Codex or Claude to implement a ticket
(in Codex, start it with `$implement` instead of `/implement`):

```text
/implement Read docs/agents/issue-tracker.md and <ticket-path>. Confirm the
ticket is ready, then work only within its scope, follow "Skills and repo
overrides", and verify every acceptance criterion.
```

## Wayfinding operations

Used by `/wayfinder` (Codex: `$wayfinder`) for an effort too big and too unclear
for one session. A map holds decisions, not implementation work; when it
clears, its outcome goes through the normal plan → `to-tickets` → `/implement`
workflow.

- **Map**: `.scratch/<capability>/wayfinder/<map-slug>/map.md`, with a
  `**Label:** wayfinder:map` line under the title and the Destination, Notes,
  Decisions so far, Not yet specified and Out of scope sections. Link the map
  from the capability hub.
- **Child ticket**: `.scratch/<capability>/wayfinder/<map-slug>/W-NN-<slug>.md`,
  numbered from `01`, the question under `## Question`. A `**Type:**` line
  records `wayfinder:research`, `wayfinder:prototype`, `wayfinder:grilling` or
  `wayfinder:task`; a `**Status:**` line records `open`, `claimed` or
  `resolved`. These files are not board tickets and stay out of
  `issues/_index.md`.
- **Blocking**: a `**Blocked by:**` line listing ticket names. A ticket is
  unblocked when every one it lists is `resolved`.
- **Frontier**: the map folder's tickets that are `open` and unblocked; lowest
  number first.
- **Claim**: set `**Status:** claimed` and save before any work.
- **Resolve**: append the answer under `## Answer`, set `**Status:** resolved`,
  then add one line to the map's Decisions so far: the ticket name as a link
  and a one-line gist.
- **Research tickets** write their findings to
  `<map folder>/research/<slug>.md`, not to a git branch. No commits.
- **Needs you**: while a frontier ticket waits on the user, keep one
  `.scratch/INBOX.md` line for the map, `- [<capability>] **Needs you:**
  wayfinder <map name>: <ticket name>`, so it shows in `NOW.md`.

## Action views (what is left to do)

`.scratch/now.py` generates three views from the tracker's own files, so nobody keeps
a second status list by hand:

- `.scratch/NOW.md` — Needs you, Ready for an agent, In progress, Blocked, and Needs you
  later when anything is deferred. Open this right after `MINE.md`.
- `.scratch/MINE.md` — the user's own hand-written list, never generated: a "My pick"
  of one to three items, an "Ideas" backlog and the sections ideas move to. `now.py`
  reads it and opens NOW.md with "My pick". Agents read it first and never write it;
  while "My pick" is non-empty it outranks NOW.md's order.
- `.scratch/views/needs-you.md` — only what needs the user, by capability.
- `.scratch/views/everything.md` — every open ticket, finding and inbox line.

They are rebuilt from scratch on every run, carry a do-not-edit header, and are never
edited. They read:

- each ticket's and finding's `**Status:**` line (an `open` finding counts as needing
  the user's triage);
- a `**Needs you:** <what>` line, placed directly under `**Status:**` in any ticket or
  finding, for as long as something waits on the user. To defer one, give it a start
  date: `**Needs you (from YYYY-MM-DD):** <what>`. Until that date it is listed under
  Needs you later instead of Needs you. A date or marker that does not parse is shown now
  and listed under "Could not read";
- `.scratch/INBOX.md`, which holds only loose items with no ticket or finding, as
  `- [<capability or general>] <text>`; a bullet with `**Needs you:**` is the user's.

Hooks in the user's global Claude and Codex config run it at session start (printing
one summary line into the agent's context) and after each reply. Run it by hand with
`python3 .scratch/now.py` in any other tool.

A second hook runs `.scratch/progress_check.py` after each reply. When the chat
itself has changed a file in an open ticket's `Touches` list since the ticket was
last updated, and the user's messages name that ticket (or it is `in-progress`), it
has the agent give its handback with a drafted `## Progress` line (and a
`**Needs you:**` line if the chat waits on them), written only on the user's yes.
It is the safety net behind "End with a handback", not a prompt between steps: it
waits until the ticket's files have been quiet for 20 minutes, asks once per ticket
version, and after an answer stays quiet until the ticket changes, or the files
change again and 4 hours have passed. A chat that hands back within 20 minutes of
its last write is never asked. Its docstring lists the writes it cannot see.

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
A new worktree gets `.scratch` as a link to the canonical copy: each tool's
worktree setup runs `scripts/session_setup.sh`
([`cloud-sessions.md`](cloud-sessions.md)). A ticket keeps its status in that
single file. If the link is missing, give the session the absolute path to the
canonical finding, plan, or ticket rather than creating a second copy.
