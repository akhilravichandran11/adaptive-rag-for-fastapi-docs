---
name: pre-edit-review
description: Before editing any file, show the exact existing code, the exact proposed replacement, the reason for each change, tradeoffs/caveats, and the verification plan, then wait for explicit approval before touching any file. Use when the user invokes /pre-edit-review or $pre-edit-review before a coding task, or says things like "don't edit yet", "show me first", or "walk me through the changes before you make them". Do not use for tasks the user has already approved, for read-only questions where no edit was ever going to happen, or once the user has explicitly told you to proceed with the edit.
disable-model-invocation: true
---

# Pre-Edit Review

Showing a diff before applying it only has value if it's the diff that's actually about to happen. Never present a plausible-looking change and then implement something different — read the real current code first, every time.

## Scope and authorization

- This skill governs the moment right before a file edit, not the whole task. Research, reading files, and running read-only commands to investigate are all still fine without approval.
- Do not call any file-editing tool until the user has explicitly approved the proposal you presented. "Looks good", "do it", "yes", or an explicit go-ahead counts as approval; silence or a follow-up question does not.
- If the user's request already contains clear, specific approval for a specific change (e.g. they pasted the exact diff they want applied), you can skip the review and implement directly — this skill is for changes you are proposing, not changes the user already fully specified.
- If mid-task you discover the real change needs to differ from what was approved (e.g. the file doesn't match what you expected), stop and re-present the revised proposal rather than silently adapting.

## Output contract

For every file you intend to change, present:
1. **Existing code** — the exact current lines, quoted verbatim (not paraphrased), with enough surrounding context to be unambiguous.
2. **Proposed replacement** — the exact new lines you intend to write, verbatim.
3. **Why this change is required** — the concrete problem it fixes or capability it adds. Not "improves code quality" — the specific behavior that's wrong now and right after.
4. **Tradeoffs or caveats** — anything a reviewer would want to know: risk of regression, edge cases not covered, performance implications, or why an alternative approach was rejected. If there are genuinely none, say so rather than omitting the section.
5. **Verification plan** — the specific check you'll run afterward (tests, a manual invocation, a lint/typecheck command) and what result would confirm success.

## Workflow

1. Read the actual current file content before drafting the proposal — never reconstruct it from memory or from an earlier read that may be stale.
2. Present the output contract above for each file, grouped clearly if there are multiple files.
3. Stop and wait. Do not call any edit tool in the same turn as the proposal.
4. Once approved, implement exactly what was shown. If you need to deviate even slightly from the approved proposal, flag the deviation before proceeding rather than silently including it.
5. Run the verification you promised and report the actual result, not just that you ran it.
