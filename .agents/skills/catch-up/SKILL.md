---
name: catch-up
description: Rebuild where one .scratch capability stands from its records and git, propose fixes for records git has outrun, and rewrite the hub's Current state block.
argument-hint: "<capability folder under .scratch/>"
disable-model-invocation: true
---

# Catch-up

A fresh session gets its bearings by re-deriving where a capability stands
from the tracker records and from git, rather than trusting what the last
chat wrote. The tracker rules are in `docs/agents/issue-tracker.md`; read it
first. This skill owns one thing those rules leave open: a short, dated
`## Current state` block in the capability's hub.

## Scope

- One capability per run, named in the argument. With no argument, list the
  capabilities in `.scratch/_index.md` and ask which one.
- Work in the checkout that holds `.scratch/`, and run git there. From a
  worktree without `.scratch/`, ask for the canonical checkout's path and
  use it for both.
- Writes: the views `now.py` generates, the hub's `## Current state` block,
  and the corrections the user approves in step 4. Every other record change,
  including a new `**Needs you:**` or INBOX line, goes through step 4.
- The block describes and links. Each ticket's and finding's status stays in
  its own file, and `.scratch/NOW.md` stays the live answer to what is next
  and what needs the user.

## Steps

1. **Load the records.** Run `python3 .scratch/now.py`. Then read, for this
   capability: its lines in `.scratch/NOW.md` and `.scratch/views/everything.md`;
   the hub `<capability>/_index.md`, noting the as-of date and commit of any
   existing Current state block; the board `issues/_index.md`; each findings
   register; the `.scratch/INBOX.md` lines tagged with it, plus `[general]`
   lines about its files; and `.scratch/DECISIONS.md` entries dated on or
   after the as-of date. Open every ticket that is not `done` or `wont-do`,
   every finding that is not `closed`, and any plan or roadmap the hub or
   board links as current.
   Done when every open ticket and finding is named with the file that holds
   its status.

2. **Read git since the last block.** The capability's code paths are the
   files in its hub's targets table and its tickets' `Touches` lines.
   - What landed: `git log <as-of commit>..HEAD --format='%h %ad %s' --date=short --name-only -- <paths>`.
     On a first run, use `--since='<date 14 days ago> 00:00'` in place of the
     range; a bare date counts from the current time of day and drops commits.
   - What is uncommitted: `git status --short -- <paths>`.
   - What is not on `main`: `git branch --show-current` and
     `git log --oneline main..HEAD`.
   - `git check-ignore <path>` each path that returns nothing: an ignored
     path is silent, not unchanged.
   Then open each `done` ticket whose `Touches` files appear in those commits.
   Done when each commit is matched to a ticket or finding, or listed as
   unmatched. Match on ticket ID in the message, except data-curation's bare
   numeric IDs, which match on `Touches` files only.

3. **Find drift.** Drift is a record that git or a canonical file has
   outrun. Test each claim directly, whatever its age: `git log -1 -- <path>`
   for a file, `git merge-base --is-ancestor <hash> main` (and `origin/main`)
   for a commit. Check every record from steps 1–2 for:
   - text saying changes are uncommitted or unmerged that git shows
     committed or merged;
   - an INBOX or `**Needs you:**` line whose action git shows done;
   - a `done` ticket whose completion section names no commit, where git
     shows the commit that landed it;
   - a hub status cell, board line, or register row that disagrees with the
     ticket or finding file it summarises (the file wins);
   - an approved plan whose tickets exist and are all `done` or `wont-do`,
     with no step left unticketed.
   A board line in **Doing** with an owner and a since-date is a live claim:
   leave that ticket's records as they are.
   Done when every check has run against every record.

4. **Propose corrections.** List the drift as numbered corrections, each with
   the record (`path:line`), the evidence (commit hash or file), and the
   exact replacement text. Apply the ones the user approves. Status changes
   follow "Issue status" in `docs/agents/issue-tracker.md`.
   Done when the user has ruled on each correction, or there was no drift.

5. **Write the Current state block.** Replace the existing block, or insert
   it directly above the hub's first `##` heading:

   ```markdown
   ## Current state

   _As of YYYY-MM-DD at <short HEAD hash> · catch-up (<tool>)_

   <Two to four sentences: where the capability stands and what it is working toward.>

   - **In flight:** <uncommitted paths and commits not on main, from step 2; or "nothing in git">. Doing and Needs-you items: [NOW.md](../NOW.md).
   - **Since <previous as-of date, or the first-run start date>:** <what landed or was decided, each linked to its ticket, finding, decision, or commit>
   - **Order:** <what comes next and why, citing the roadmap, plan, or review that sets the order>
   - **Watch out:** <gotchas a fresh session would trip on; omit the line if none>
   ```

   Name every item by link and let the linked file carry its status. Keep the
   block under 15 lines; when more landed than fits, link the board's Done
   section for the rest.
   `data-curation-pipeline` has no hub. Propose creating its `_index.md` as a
   step-4 correction: title, links to `findings/_index.md`,
   `issues/_index.md` and its roadmaps, the block, and the master index entry
   pointed at the new hub. If the user declines, reply with the block
   unwritten.
   Done when every line traces to a record or commit you read in steps 1–3.

6. **Close out.** Run `python3 .scratch/now.py`, then reply with the new
   block, the corrections applied or declined, and the Needs-you count with a
   link to `.scratch/NOW.md`. If `.git/FETCH_HEAD` is more than a day old,
   say that `main` may be behind the remote.
