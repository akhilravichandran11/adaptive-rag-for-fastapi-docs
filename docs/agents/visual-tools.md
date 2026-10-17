# Visual tools: explaining, deciding and reviewing work

Use this doc when you explain a design, ask the user for decisions, or ask them
to review edits, code or a plan. Long markdown in chat is hard to review, so
each of these jobs has a tool that shows the work instead. This doc maps each
job to its tool and holds the proposal-page contract; the tool's own skill says
how to run it.

| Job | Use | Skill or command |
|---|---|---|
| Explain a design | a diagram in chat, or a full page | inline visual; `visual-explainer` |
| Ask for decisions | one page with every open question and its recommendation | `grill-with-ui` |
| Get record or doc edits approved | a proposal page in Plannotator's gate, then "yes" in chat | the proposal-page builder |
| Get code reviewed | inline comments on the diff, a visual recap, or a diff review | `crit`, `visual-recap`, `plannotator review` |
| Plan | an interactive plan with diagrams and open questions | `visual-plan` |

## Explain

To explain a design, a flow or the difference between options, start from a
concrete example, then draw a small diagram in chat with your tool's inline
visual. When your tool has none, or one diagram is not enough (an
architecture, a walkthrough of a change, a comparison of several options),
build a page with the `visual-explainer` skill.

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
