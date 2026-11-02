---
name: next
description: Next actions for the user and the agent, in this chat first and then across the .scratch tracker, ranked by each hub's Current state order. Use when the user asks what's next, for their action items, or "what's my next action and yours".
---

# Next

Answer "what's my next action and yours" in two layers: what this chat is
waiting on, then what the whole tracker is waiting on. The live list is
`.scratch/NOW.md`; this skill ranks it and checks it against git, and never
keeps a list of its own.

## Scope

- Read anything. The only writes are the views `now.py` regenerates. Offer any
  record fix (a done INBOX line, a stale Needs-you line) and make it only when
  the user says yes.
- In a repo or worktree without `.scratch/now.py`, answer the chat layer only.

## Steps

1. **This chat.** From the conversation so far, name what it waits on from the
   user (an approval, a decision, an unanswered question, a commit or merge of
   work done here) and what you will do next here once that is answered.
   Done when every request you made in this chat that the user has not
   answered is listed, or you can say nothing here is waiting.

2. **The tracker.** Read `.scratch/MINE.md` first: its "My pick" items, in the
   user's order, are the user's top ranks and outrank everything below. Then run
   `python3 .scratch/now.py` and read `.scratch/NOW.md`.
   For each capability with items under Needs you or Ready for an agent, read
   the `## Current state` block in its hub (`.scratch/<capability>/_index.md`)
   and rank that capability's items by the block's **Order** line. Put the
   capability this chat is working in first, then the rest in `NOW.md` order.
   Done when every Needs-you and Ready item has a rank, or its capability is
   noted as having no Current state block.

3. **Check before listing.** For an item that asks for a commit, merge or push,
   or names a commit hash, check git first (`git status --short -- <paths>`,
   `git merge-base --is-ancestor <hash> main`). An item git shows done goes under
   "Looks done" with the evidence, not in the top three.

4. **Reply** in this shape, linking every item to its file:

   ```markdown
   **This chat**
   - You: <what this chat waits on from you, or "nothing">
   - Me: <what I do next here once that is answered, or "nothing pending">

   **Across the tracker** (<the NOW.md summary line>)
   - You: 1. … 2. … 3. … (each with one clause on why it is next)
   - An agent: 1. … 2. … 3. …
   - Looks done: <items git shows done, with the evidence; omit if none>
   - Unranked: <capabilities with no Current state block; `/catch-up <capability>` ranks them; omit if none>

   <N> more in [NOW.md](.scratch/NOW.md).
   ```

   Under the agent's first item, give the session-start instruction from
   `docs/agents/issue-tracker.md` that fits it (implement a ticket, triage a
   finding, work a target), filled in with the path, ready to paste into a
   fresh chat.
