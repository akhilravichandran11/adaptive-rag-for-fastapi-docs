---
name: bug-review
description: Review a diff, pull request, or named file(s) for bugs, correctness risks, and design weaknesses, and report findings in a strict severity-ranked contract. Use whenever the user asks to "review this PR", "review my changes", "review the diff", "check this file/PR for bugs", or when running as an automated PR reviewer in CI. Do not use for style-only requests like "format this file" or "run the linter" — this skill is for substantive correctness review, not formatting.
---

# Bug Review

Find real bugs, not preferences. A reviewer who reports fifty style nits and misses the one input that crashes production has failed at the job, even if every nit was technically correct.

## Scope and authorization

- Determine the review target before starting: a diff (`git diff`, `git diff <base>...HEAD`, or `gh pr diff`), a specific set of files the user named, or the full working tree if nothing narrower was given.
- Review-only by default: do not edit files, run the linter/formatter, or push commits unless the user explicitly asks for a fix, not just a review.
- When running in CI against a pull request, treat the PR diff as the review target and post findings as the output format the calling workflow expects (e.g. inline `gh pr review --comment`), still following the output contract below for the content of each comment.

## Workflow

1. Identify every changed file in the target. For each one, read enough surrounding context (not just the diff hunk) to reason about actual runtime behavior — callers, types, error handling, concurrent access, and edge-case inputs.
2. Look for real defects: logic errors, off-by-one and boundary conditions, incorrect error handling, race conditions, resource leaks, security issues (injection, secrets, unsafe deserialization, missing auth checks), broken contracts with callers, and data-correctness risks (silent truncation, wrong units, mutable-default bugs, etc.).
3. Deliberately separate what you find into two buckets:
   - **Real bugs** — the code will misbehave for some reachable input or condition.
   - **Style/preference** — valid code that could be written differently (naming, formatting, minor idiom choices). Report these separately and clearly labeled, never mixed into the ranked bug list.
4. For every bug finding, locate the exact line. If you can't point to a specific file:line, you can't report it as a finding — go back and find the location or drop it.
5. If you suspect something is wrong but can't fully confirm it from the available context (e.g. behavior of an external call you can't see), do not discard it — include it marked `unverified` with your reasoning, rather than silently dropping a possible real bug.
6. Order findings most-severe-first.

## Output contract

Use this exact structure for every finding:
- **Severity**: Critical / High / Medium / Low
- **Location**: `file:line`
- **What's wrong**: the concrete failure scenario — the specific input or condition that causes the wrong behavior, not a vague description
- **Suggested fix**: one line

Severity guide:
- **Critical** — data loss, security exposure, crash on common input, silently wrong results
- **High** — wrong behavior on a realistic but less common input; broken error handling that hides failures
- **Medium** — correctness risk that needs a specific edge case to trigger; maintainability issue likely to cause a future bug
- **Low** — minor correctness nit unlikely to matter in practice

After the ranked list, include a separate `Style / preference (not ranked)` section for anything that isn't a real bug, and a `Unverified` section for suspected-but-unconfirmed findings.

## Reporting

Lead with the highest-severity finding. If there are zero real bugs, say so plainly instead of inflating style notes into the bug list to have something to report.
