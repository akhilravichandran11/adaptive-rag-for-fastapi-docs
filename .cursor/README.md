# Cursor project harness

Cursor loads skills and agents from several trees in this repo. **Do not
duplicate** Claude/Codex skills into `.cursor/skills/` unless the harness
differs — copies drift (same problem as the simplifier twins).

## What Cursor already sees (no import needed)

From [Cursor skills docs](https://cursor.com/docs/skills) and
[subagents docs](https://cursor.com/docs/subagents):

| Location | Role |
|---|---|
| `.cursor/skills/` | Cursor-native project skills |
| `.agents/skills/` | Shared project skills (also used by Codex) |
| `.claude/skills/` | Claude skills — Cursor loads these for compatibility |
| `.cursor/agents/` | Cursor-native subagents (Task tool) |
| `.claude/agents/` | Claude agents — Cursor loads these; `.cursor/` wins on name clash |
| `.codex/agents/` | Codex agents — Cursor loads these |

So almost everything you already maintain for Claude/Codex is **already
available in Cursor Local** (and in Cloud Agents that clone this GitHub repo).

## Inventory decision (2026-11-18)

### Already available — do **not** copy into `.cursor/skills/`

| Skill | Lives in | Why keep single copy |
|---|---|---|
| `next` | `.claude` + `.agents` | Tracker / NOW.md action view |
| `catch-up` | `.claude` + `.agents` | Capability hub Current state |
| `commit-planning` | `.claude` + `.agents` | Commit phases; never stages |
| `bug-review` | `.claude` + `.agents` | Spec/bug review contract |
| `pr-writeup` | `.claude` + `.agents` | PR title/body |
| `pre-edit-review-except-tests` | `.claude` + `.agents` | Approval gate with test exception |
| `pre-edit-review` | `.agents` only | Strict approval; rarely needed in Cursor |
| `python-simplification` | `.claude` + `.agents` | S-rules / open questions |
| `debug-instrumentation` | `.agents` only | akr debug style add/strip |
| `portfolio-share-readiness` | `.claude` + `.agents` | Share-ready / provenance |
| `portfolio-code-quality` | `.claude` + `.agents` | Portfolio script checklist |
| `portfolio-readiness-quick` | `.claude` + `.agents` | Quick portfolio pass |
| `rag-improvement-checklist` | `.claude` + `.agents` | RAG/prompt checklist |
| `rag-improvement-quick` | `.claude` + `.agents` | Quick RAG/prompt pass |
| `batch-lead` (Codex) | `.agents/skills/batch-lead` | Codex-specific spawn wording |

### Cursor-specific — keep / add only here

| Item | Path | Why Cursor-specific |
|---|---|---|
| **batch-lead** | `.cursor/skills/batch-lead/` | Spawn surfaces differ (Task, sibling chats, worktree, Cloud). Claude command and Codex skill stay in their trees. |
| *(optional later)* thin agent wrappers | `.cursor/agents/` | Only if Task hints need Cursor-first descriptions; otherwise use `.claude/agents/*` |

### Agents (workers / reviewers) — use existing, do not fork yet

| Agent | Path | Cursor use |
|---|---|---|
| `ticket-implementer` | `.claude/agents/` (+ `.codex` toml) | Batch workers; lead pastes Cursor anti-commit / no self-review rules |
| `active-code-reviewer` | `.claude/agents/` (+ `.codex`) | Lead bug pass (`readonly`) |
| `active-code-simplifier` | `.claude/agents/` (+ `.codex`) | After tests, before review — **twins must stay in sync** via existing hook |

Create `.cursor/agents/<name>.md` **only** when Cursor needs different tool
wording than Claude; then `.cursor/` wins and you own the twin.

### Explicitly **not** imported

| Item | Reason |
|---|---|
| Claude `commands/batch-lead.md` as a Cursor skill copy | Superseded by `.cursor/skills/batch-lead` |
| Claude agent-teams / teammate messaging | Cursor has no team bus; harness.md explains the map |
| Mass copies of portfolio/RAG skills into `.cursor/skills/` | Already loaded from `.claude` / `.agents`; duplicates drift |
| `pre-edit-review` (strict) as default Cursor skill | Conflicts with Agent velocity; use except-tests or proposal-page when needed |

## MoSCoW for Cursor harness work

### Must
- Keep `.cursor/skills/batch-lead` maintained as the Cursor lead
- Rely on existing `.claude` / `.agents` skills for day-to-day workflows
- Parent git root + `.scratch` rules (batch-lead pre-flight)

### Should
- When invoking `ticket-implementer` from a Cursor lead, paste the Cursor
  constraints from batch-lead (no commit, no self-review)
- After Cloud/worktree pilots, decide if thin `.cursor/agents/` wrappers help Task discovery

### Could
- Cursor `/` commands that alias `next`, `catch-up`, `commit-planning` if slash UX is nicer than skill auto-load
- Mirror this README pointer into the GitLab workbench plan (process remote)

### Won't
- Triple-maintain every skill under `.cursor`, `.claude`, and `.agents`
- Port Claude agent-teams settings into Cursor

## How to invoke

- **Lead:** pin Local chat → `@batch-lead` or “you are batch-lead; read `.cursor/skills/batch-lead`”
- **Next / catch-up / commit-planning / bug-review / …:** ask normally; Cursor should surface the matching skill from `.claude` or `.agents`
- **Workers:** Task → `ticket-implementer` (from `.claude/agents`) or sibling/Cloud chat with full ticket prompt

## Related

- Workbench plan: `.scratch/agent-workflow/targets/mobile-remote-workbench/plans/plan.md`
- Claude batch-lead: `.claude/commands/batch-lead.md`
- Codex batch-lead: `.agents/skills/batch-lead/SKILL.md`
