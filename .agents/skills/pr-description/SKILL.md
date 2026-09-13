---
name: pr-description
description: Draft a pull request title and description from the current branch's commits and diff, following this repo's Conventional Commits title style and a Summary/Changes/Testing body structure. Use when the user asks to "write a PR title and summary", "draft the PR description", "generate a PR title", or before running `gh pr create`. Do not use for regular commit messages (a separate concern) or once the user has already given you the exact title/body to use.
---

# PR Description

A good PR title and description let a reviewer understand the change without opening every file. Optimize for that reader, not for describing your own process.

## Scope and authorization

- Determine the target: the current branch's commits against its base branch (ask if the base isn't obvious — this repo doesn't always merge into `main`), or an existing PR number if one was given.
- This skill only drafts text. Do not run `gh pr create` or `gh pr edit` to publish anything unless the user explicitly asks you to open or update the PR.

## Workflow

1. Identify the base branch, then gather `git log <base>..HEAD --oneline` and `git diff <base>...HEAD --stat` (read the full diff for any file whose purpose isn't clear from the stat alone).
2. Determine the dominant Conventional Commit type across the branch's commits (`feat`, `fix`, `refactor`, `docs`, `chore`, `ci`, `test`, `perf`) and a scope if the commits share one consistently (e.g. `(curation)`, `(agents)`).
3. If the branch mixes multiple unrelated types or scopes, say so explicitly rather than picking one arbitrarily — that's a signal the PR might be better split.
4. Draft the title and body per the output contract below.

## Output contract

**Title**: `type(scope): summary` — imperative mood, no trailing period, target under 72 characters. Omit `(scope)` only if no single scope fits.

**Body**:
```
## Summary
1-3 sentences: what changed and why, not a restatement of the title.

## Changes
- One bullet per logical unit of change, not one per commit.

## Testing
- What was actually run to verify this (tests, manual checks, commands, or their output). If nothing was verified yet, say so explicitly instead of omitting the section.
```

If your current environment has a standard AI-attribution convention for commits or PR descriptions (for example `Co-Authored-By: Warp <agent@warp.dev>` in Warp), append it as the last line of the body. Omit it if no such convention applies.

## Reporting

Present the title and body together for review. Do not call `gh pr create` or `gh pr edit` unless the user explicitly asks you to publish it.
