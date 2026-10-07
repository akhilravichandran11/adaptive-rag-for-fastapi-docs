---
description: Show proposed non-test changes and wait for approval; tests within approved scope can proceed without per-edit approval.
---

Do not edit any non-test files yet. Reading files and other read-only
investigation are fine.

Tests and test-only fixtures within the approved task scope and agreed test
seams are the exception: you may create or change them without showing me each
edit first. Before writing them, briefly tell me the behavior being tested.
Afterward, report what they showed, including expected failures before a fix
and results after it. If you're doing TDD, confirm the expected failure before
proposing the production fix.

The test exception does not cover:

- expanding the task scope or adding test seams I haven't agreed to;
- deleting, skipping, or weakening existing assertions to make tests pass;
- changing shared test configuration, dependencies, or CI;
- modifying production code, live data, or generated corpus artifacts;
- making network or paid API calls.

Classify each edit by what it affects, not by its filename or location. In a
mixed change, only the qualifying test edits are exempt.

For every other edit, first show me:

1. The exact existing code you intend to change (or say the file doesn't exist yet).
2. The exact proposed replacement (the full content for a new file).
3. Why each change is required.
4. Any relevant tradeoffs or caveats.
5. The verification you will run afterward.

Wait for my explicit approval before making those edits. Silence or a follow-up
question is not approval. If an edit needs to differ from what I approved, show
me the revised version and wait again. Afterward, run the promised verification
and report the actual results.
