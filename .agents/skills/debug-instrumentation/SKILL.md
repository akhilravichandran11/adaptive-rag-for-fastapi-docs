---
name: debug-instrumentation
description: Add or remove temporary, marker-delimited Python trace statements in the akr debug style. Use when the user explicitly asks to instrument named Python files or remove instrumentation; do not activate for ordinary debugging or permanent application logging.
---

# Debug Instrumentation

Temporary tracing has no intended business-logic change, but it does change stdout, timing, import behavior, and possibly tests. State that caveat before editing.

## Scope and authorization

- Confirm the exact files and desired density before adding traces.
- Modify only the named files. Do not refactor adjacent logic while instrumenting.
- Use the standard library and the file's existing debug helper when it is safe.
- Never print credentials, authorization headers, cookies, prompts, document bodies, personal data, or complete request/response objects.

## Add traces

Read [references/instrumentation-style.md](references/instrumentation-style.md), then:

1. Place every added block between unique `AKR-DEBUG-START` and `AKR-DEBUG-END` comments so it can be removed exactly.
2. Put entry traces after a function's docstring, not before it.
3. Prefer function entry/exit, selected branch decisions, counts, identifiers, paths, types, and durations. Avoid dumping collections or tracing every loop iteration.
4. Do not add module-level or class-body prints unless the user explicitly asks; they execute during import or class creation.
5. Put an exit trace before each existing return and at natural fall-through when useful. Do not wrap the function in a new `try/finally` merely to guarantee an exit trace.
6. Treat exception text and object representations as potentially sensitive. Log the exception type by default.
7. Compile the changed file and run only the focused checks authorized by the user's request.

## Remove traces

Use `scripts/remove_debug_instrumentation.py` only for blocks created with this skill's exact markers. It validates marker pairing and writes a separate output file; it never overwrites the source. Review the diff and compile the cleaned copy before replacing anything.

Do not use broad `grep -v` removal: it can delete legitimate prints, leave multiline calls or helper bodies behind, and produce invalid Python.

## Report

List the files and scopes instrumented, any values deliberately omitted for safety, and the validation performed. Say `no intended business-logic change`, not `no behavior change`.
