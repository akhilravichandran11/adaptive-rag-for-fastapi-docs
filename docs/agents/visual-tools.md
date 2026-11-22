# Visual tools: explaining, deciding and reviewing work

Use this doc when you explain a design, ask the user for decisions, or ask them
to review edits, code or a plan. Long markdown in chat is hard to review, so
each of these jobs has a tool that shows the work instead. This doc maps each
job to its tool and holds the proposal-page contract; the tool's own skill says
how to run it.

| Job | Use | Skill or command |
|---|---|---|
| Explain a design | a diagram in chat, a full page, or a diagram file | inline visual; `visual-explainer`; `archify` or `diagram-design` |
| Ask for decisions | one page with every open question and its recommendation | `grill-with-ui` |
| Get record or doc edits approved | a proposal page in Plannotator's gate, then "yes" in chat | the proposal-page builder |
| Get code reviewed | inline comments on the diff, a visual recap, or a diff review | `crit`, `visual-recap`, `plannotator review` |
| Plan | an interactive plan with diagrams and open questions | `visual-plan` |
| Any of these, in a cloud session | the pull request conversation and line comments, or "Review in Linear" | none; see [`cloud-sessions.md`](cloud-sessions.md) |

## Codex execution

This section applies only to Codex. Claude keeps the existing workflow.
Use the job table automatically when its condition applies; Crit and
grill-with-ui remain opt-in, as described below.

- **Explain:** use Codex's available inline visualization capability for a
  small visual. Use visual-explainer for a standalone page. Save previews
  in a writable task-owned location, open them in Codex when supported,
  and provide a browser link when using a local server.
- **Listen:** choose the feedback path from the tools actually available.
  A tool that explicitly delivers later review messages may end the turn.
  For CLI reviews, keep the command in a managed running session and poll
  that same session in bounded waits of at most 60 seconds. A background
  process alone is not a subscription that wakes a finished chat.
- **Resume:** follow grill-with-ui's Wait mode and drain pending submissions
  after interruption. If listening stops, say so; do not claim the page is
  still connected to an active agent.
- **Finish a questionnaire:** complete the interview state, but treat its
  proposed plan text as a draft. Updating the canonical plan still requires
  the document-edit approval process below; Finish alone does not authorize
  that write.
- **Prepare a review:** temporary records, preview pages and review-session
  files may be created to show the proposal. Target documents and code remain
  untouched until approval. An explicit user instruction allowing no file
  writes overrides this exception: show the proposal in chat first.
- **Apply approved edits:** use the builder's Codex procedure in its README:
  page approval, then "yes" in chat, read-only check, apply_patch, and
  verification against the approved records. A successful check is not
  evidence of human approval. If acting on feedback changes the proposal,
  rebuild and review it again.
- **Keep plans local:** use the installed local-files mode for visual-plan
  and visual-recap unless publishing is explicitly requested. Local storage
  can still use a hosted viewer; it is not a promise of an offline UI.
  Use local/chat feedback for local plans, not hosted feedback APIs.
- **Find the private builder:** in a worktree, use its absolute path in the
  canonical checkout. If it is missing, report that dependency rather than
  inventing a replacement. This repo's AGENTS.md supplies the entry point
  for new sessions; it does not configure unrelated repositories.

## Explain

To explain a design, a flow or the difference between options, start from a
concrete example, then draw a small diagram in chat with your tool's inline
visual. When your tool has none, or one diagram is not enough (an
architecture, a walkthrough of a change, a comparison of several options),
build a page with the `visual-explainer` skill. For a diagram that stands as
its own file, pick by where it ends up: `archify` for an interactive diagram
traced from this repo's code, `diagram-design` for a static SVG a README
embeds, or for a chart.

## Decide

Ask a single question in chat. When several choices are open at once, offer
`/grill-with-ui`: it puts every question on one local page with its
recommendation and a discussion thread, answerable in any order. The user
starts it. Its answers go in the target's `plan.md`, as for any grilling
session (see [`issue-tracker.md`](issue-tracker.md), "Workflow").

## Review record and doc edits

Edits to findings, plans, tickets, boards or tracked docs are shown to the user
before anything is written:

- **One edit to one file:** show it in chat, the current text and then the
  proposed text.
- **More than one file, or a new file:** show them as a **proposal page**, an
  HTML page of every edit, reviewed in Plannotator's gate (its review mode
  with an Approve button). The page sorts the edits into two kinds:
  - **Read items** are edits whose wording the user judges: a rule, a decision
    paragraph, a new ticket. They are rendered with additions and removals
    marked.
  - **Skim items** are bookkeeping that follows from a decision already made:
    a status flip, a register row, a board line. Each is one plain line, with
    the exact text on request.
- **Gate approval:** the user presses Approve on the page, then says "yes" in
  chat. Write nothing until both have happened. An `annotated` decision
  carries the user's notes: revise the edits, rebuild the page and review it
  again.

The builder is private, in `.scratch/tools/proposal-page/`. From one records
file of exact text it builds the page, checks every edit against the live
files, opens the gate, and after the "yes" applies the same text. Its
`README.md` has the usage, the records format and the machine notes.

## Review code

Code is reviewed from git once it is written. Offer the view that fits:

- `/crit`: inline comments on the branch diff, in rounds: the user comments,
  you revise, they review again. The user starts it.
- `visual-recap`: an overview of a branch, PR or diff (file map, diagrams,
  annotated changes), to read before the diff itself.
- `plannotator review`: Plannotator's diff review in the browser.

## Plan

For a design with alternatives, open questions and diagrams, use the
`visual-plan` skill. Keep the plan local unless the user asks to publish it:
a local plan sits beside the target's `plan.md` in `.scratch/`, and a hosted
one is registered with `.scratch/open-plan.sh add <name> <url>`. The target's
`plan.md` stays the record: copy approved decisions back into it.

## Gotchas

- `.scratch/` is git-ignored, so the git-based review tools (`/crit`,
  `visual-recap`, `plannotator review`) cannot show tracker edits. Show those
  on a proposal page.
- Plannotator serves each session on a new local port. `plannotator sessions`
  lists the open sessions and their URLs.
- `dismissed` means the user closed the page without deciding. It is neither
  approval nor feedback: ask in chat.
- The Claude app's browser pane may not show a local page. Give the user the
  URL to open in their own browser.
- The chat built into a Plannotator page is a separate agent with read-only
  tools: it cannot edit files, and what you discuss there does not reach this
  session by itself. It can pass a message on to the session when you ask it
  to, but a relayed message is context, not a decision. (Plannotator's plugin
  connects the chat to the session directly, but only in an interactive
  terminal `claude`, not in Claude Desktop.) Put decisions in the page's
  annotations and press Annotate or Approve: only the gate's decision counts.
