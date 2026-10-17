---
name: pre-edit-review-except-tests
description: Show exact proposed edits and wait for approval, while allowing tests and test-only fixtures within approved scope and agreed test seams without per-edit approval. Use when the user requests pre-edit review with a test exception or invokes this skill. A request for strict pre-edit review alone does not select this variant.
---

# Pre-Edit Review Except Tests

Keep the user in control of non-test changes while allowing the approved test work to proceed without repeated approval. Read the real current content before proposing or making changes.

## Scope and authorization

- Confirm task scope and test seams before editing. Reuse approval already given in the conversation; this skill does not expand ticket scope.
- Classify each proposed edit using the test-file exception below. All other edits require the review workflow.
- Research, reading files, and read-only investigation can proceed without edit approval.
- Clear, specific approval for an already presented change authorizes applying it directly. Silence or a follow-up question does not count as approval.

## Test-file exception

Within the approved task scope and agreed test seams, the agent may create
or modify tests and test-only fixtures without presenting an exact pre-edit
proposal or waiting for additional approval.

Before writing tests, briefly state the behavior being tested. Afterward,
report what the tests demonstrated, including expected failures before a fix
and results after it.

This exception does not authorize:
- expanding the task scope or adding unapproved test seams;
- deleting, skipping, or weakening existing assertions merely to make tests pass;
- changing shared test configuration, dependencies, or CI;
- modifying production code, live data, or generated corpus artifacts;
- making network or paid API calls.

All other edits retain the normal pre-edit-review requirements. Classify by
what an edit affects, not just its filename or location. In a mixed change,
apply the exception only to the qualifying test edits.

## Review contract for other edits

For each file requiring review, present:

1. **Existing content** — exact current lines with enough context to identify the change. For a new file, state that it does not exist.
2. **Proposed replacement** — exact new lines; for a new file, show its full proposed content.
3. **Reason** — the concrete problem fixed or capability added.
4. **Tradeoffs or caveats** — relevant risks, limitations, or alternatives. If none are known, say so.
5. **Verification plan** — the specific checks and results that would confirm success.

When record or doc edits touch more than one file or add one, present the contract as a proposal page instead of in chat, and apply only after the user's gate approval and "yes" in chat; `docs/agents/visual-tools.md` says how.

## Workflow

1. Read current file content and classify the edits.
2. For qualifying tests, state the behavior, write the tests, and run authorized checks. When using TDD, confirm the expected failure before proposing the production fix.
3. Present the review contract for other edits. Wait for explicit approval before applying those edits; qualifying test work may continue independently.
4. Apply approved edits exactly as shown. If an edit needs to differ from the approved proposal, present the revised proposal and obtain approval for that change.
5. Run the promised verification and report actual results. Further qualifying test edits do not need another pre-edit approval.
