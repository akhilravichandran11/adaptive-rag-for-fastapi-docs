---
name: commit-planning
description: Given a pile of unstaged and/or tracked changes across many files, partition them into an ordered sequence of logical commits (phases), each with its file list and a Conventional Commit message, and flag any files that should not be committed at all. Use when the user asks things like "look at the changes and tell me the sequence of files to add and the commit message", "tell me what files belong in each stage and the commit I should make", or "tell me the correct order to make commits and which files to ignore". This skill only produces a plan; it never stages or commits anything itself — for executing an individual commit once a stage's file list is decided, use the git-commit skill.
---

# Commit Planning

A commit plan is only useful if each phase can stand on its own: revertible independently, and never depending on a file introduced in a later phase. Group by logical dependency, not by file type or the order `git status` happens to print them in.

## Scope and authorization

- This skill is read-only: inspect `git status`, `git diff`, and `git diff --staged`, and report a plan. Never run `git add` or `git commit` from this skill.
- Approval of a plan alone does not authorize staging or committing. When the user asks to execute a phase, hand off to `git-commit` with that phase's exact paths, commit message, exclusions, and any restrictions on skills or agents. Follow AGENTS.md's commit-only boundary. If execution needs a skill or agent outside the user's allowed set, stop and explain before invoking it.
- Never include a file that shouldn't be committed at all: secrets (`.env` and variants), scratch/sandbox directories, personally named or course-marker files, generated data/output directories, or anything else this repo's `.gitignore` (and any `block-private-files`-style guard) already excludes. If such a file shows up as tracked or staged anyway, list it under **Do not commit** rather than silently dropping it from the plan or silently including it in a phase.

## Workflow

1. Run `git status --porcelain=v1` to see every modified, added, deleted, renamed, and untracked path in one pass.
2. Run `git diff` (unstaged) and `git diff --staged` (already staged) to understand what actually changed in each file — a filename alone doesn't tell you which phase it belongs in.
3. Cluster files into phases by logical dependency:
   - Foundational changes (schemas, shared config, shared utilities) generally come before the phases that consume them.
   - Files that only make sense together (a new module and its test, a script and the config it reads) belong in the same phase.
   - A phase should be revertible on its own without breaking a later phase.
4. For each phase, determine the Conventional Commit type and scope the same way the `git-commit` skill would (`feat`/`fix`/`docs`/`refactor`/`chore`/`ci`/`test`/etc., with a scope when the phase's files share one).
5. Cross-check every path against this repo's exclusion patterns before finalizing the plan.

## Output contract

Present as a numbered list of phases, in commit order:
```
Phase N: <one-line description of this phase>
Files:
  - path/to/file1
  - path/to/file2
Commit message: <type>(<scope>): <description>
```
After the phases, list a **Do not commit** section for any tracked/staged file that matches an exclusion pattern, with the reason.

If the changes don't actually need splitting (everything is one cohesive change), say so and propose a single phase rather than manufacturing artificial splits.

## Reporting

Present the full plan before changing the index or creating commits.

When executing an explicitly requested phase through `git-commit`:
- Recheck the working tree and index against the approved phase. If unrelated changes are already staged, stop and report them rather than including or unstaging them.
- Stage only the approved paths and preserve the user's exclusions. Stop after that phase unless the user requested additional phases.
- Report the commit hash and subject, checks actually run and their results, and any remaining uncommitted changes.
- Report skills actually invoked and agents actually spawned, including none when applicable. Reading a skill file as content being inspected or committed does not count as invoking it.
