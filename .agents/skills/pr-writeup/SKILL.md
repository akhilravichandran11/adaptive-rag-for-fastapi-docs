---
name: pr-writeup
description: Draft a pull request title and description from the current branch's commits and diff, following this repo's Conventional Commits title style and a Summary/Changes/Evidence/Merge Danger body structure. Use when the user asks to "write a PR title and summary", "draft the PR description", "generate a PR title", or before running `gh pr create`. Do not use for regular commit messages (a separate concern) or once the user has already given you the exact title/body to use.
---

# PR Writeup

A good PR title and description let a reviewer understand the change without opening every file. Optimize for that reader, not for describing your own process.

The Evidence and Merge Danger sections are adapted from Matt Pocock's `pr` skill (MIT), whose summary visuals come from HumanLayer's `show-me` (MIT). See `THIRD_PARTY_NOTICES.md`.

## Scope and authorization

- Determine the target: the current branch's commits against its base branch (ask if the base isn't obvious — this repo doesn't always merge into `main`), or an existing PR number if one was given.
- This skill only drafts text. Do not run `gh pr create` or `gh pr edit` to publish anything unless the user explicitly asks you to open or update the PR.

## Workflow

1. Identify the base branch, then gather `git log <base>..HEAD --oneline` and `git diff <base>...HEAD --stat` (read the full diff for any file whose purpose isn't clear from the stat alone).
2. Determine the dominant Conventional Commit type across the branch's commits (`feat`, `fix`, `refactor`, `docs`, `chore`, `ci`, `test`, `perf`) and a scope if the commits share one consistently (e.g. `(curation)`, `(agents)`).
3. If the branch mixes multiple unrelated types or scopes, say so explicitly rather than picking one arbitrarily — that's a signal the PR might be better split.
4. Draft the title and body per the output contract below. If `GLOSSARY.md` exists, use its terms.

## Output contract

**Title**: `type(scope): summary` — imperative mood, no trailing period, target under 72 characters. Omit `(scope)` only if no single scope fits.

**Body**:
```
## Summary
1-3 sentences: what changed and why, not a restatement of the title.
<optional: one small visual>

## Changes
- One bullet per logical unit of change, not one per commit.

## Evidence
- **Before:** <failing test, wrong output, or old behavior>
  **After:** <passing test, correct output, or new behavior>
- Commands actually run (tests, manual checks) and their result.

## Merge Danger
**Door:** <one-way or two-way>
**Blast radius:** <one word>
<optional: one or two sentences on what could break>

<optional: Refs DEF-N, one line per Linear issue>
```

**Summary visual**: add one only when it makes the change clearer than prose, and pick the smallest that does: a call tree for control flow, a shallow file tree for a layout change, a `diff`-style sketch of the before/after shape, pseudocode for logic, or a Mermaid diagram for interactions between components. Most PRs need none.

**Evidence**: show a before and after wherever one exists; a test that failed and now passes is the strongest kind. If nothing was verified yet, say so explicitly instead of omitting the section.

**Merge Danger**: a two-way door is cheap to roll back; a one-way door is not (deleted data, migrated schemas, re-indexed collections, published artifacts). The blast radius names what the change could affect beyond its own files, such as downstream pipeline stages, stored artifacts, or the evaluation baseline.

**Linear reference**: when the branch's work has a Linear issue, end the body with one `Refs DEF-N` line per issue, above any attribution line. Write `Refs`, never `Fixes` or `Closes`: the PR links to the issue without changing its status, and the maintainer closes issues by hand. Leave the line out when no issue applies.

If your current environment has a standard AI-attribution convention for commits or PR descriptions (for example `Co-Authored-By: Warp <agent@warp.dev>` in Warp), append it as the last line of the body. Omit it if no such convention applies.

## Reporting

Present the title and body together for review. Do not call `gh pr create` or `gh pr edit` unless the user explicitly asks you to publish it.
