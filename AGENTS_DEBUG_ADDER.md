---
name: debug-adder
description: Add akr-style debug/trace print statements to a Python file without changing its logic. Injects the debug_print (and optional debug_object) helpers, module/scope banners, entry-exit markers, and variable dumps following the conventions below. Use when the user asks to "add debug statements", "instrument this file", or "add my debug style".
---

# Debug Adder — akr trace-print style

Instrument a Python file with verbose, greppable trace prints in the author's
personal style. **You only ADD lines. You never change existing logic**, names,
control flow, imports, or the order of real statements. Every line you add is a
`print(...)`, a `debug_print(...)`, a `debug_object(...)`, or one of the helper
definitions — so the whole layer can later be stripped with a single regex (see
*Removal* at the end).

## Two dialects — match whatever the file already uses

This style comes in two dialects. **If the file already has debug lines, match
that dialect and stay consistent within the file.** If starting fresh, prefer
the *ingestion* dialect — it is strictly more robust.

| | Ingestion dialect (preferred) | Analyzer dialect (older) |
|---|---|---|
| `debug_print` helper | wrapped in `try/except` | bare |
| Markers | `-STARTED` / `-ENDED` (caps) | `-Started` / `-Ended` |
| Module & constant traces | via `debug_print(...)` | via raw `print(f"akr-debug-...")` |
| Exceptions | `traceback.print_exc()` + `debug_print(...EXCEPTION)` | `debug_object(e, True)` dump |
| `debug_object` | optional (only when inspecting objects) | usually present |

## Golden rules

1. **Non-destructive.** Do not touch existing code. Insert debug lines *around*
   it (before/after real statements), never rewrite a real statement.
2. **Greppable prefix.** Every `debug_print` payload is auto-prefixed with
   `[akr-DEBUG]->` by the helper; raw prints start with `akr-debug-`.
3. **Always flush.** The helper uses `flush=True` so output isn't buffered.
4. **Readable spacing.** Call `debug_print(..., True, True)` (i.e.
   `before=True, after=True`) so the line is wrapped in blank lines.
5. **Preserve behavior.** Debug lines must have no side effects on the program
   result. Never put a debug call where its return value is used.

## Step 1 — inject the helper(s) (once per module)

Place `debug_print` right after the imports (and after `load_dotenv(...)` if the
file has one). Do not duplicate if already present.

```python
def debug_print(value: object, before: bool = False, after: bool = False) -> None:

    try:

        if before:
            print()

        print(f"[akr-DEBUG]->{value}", flush=True)

        if after:
            print()
    except Exception as e:
        content = f"debug_print-Could not print object: {str(value)}. Error: {e}"
```

**`debug_object` is OPTIONAL** — add it only when the file needs to dump an
object's internals (e.g. `self` in a constructor, an exception object). Many
files use `debug_print` alone. When you do add it, it needs
`from pprint import pformat`:

```python
def debug_object(obj: object, pretty: bool = False, content_format: str = "vars"):
    content = "Empty"
    try:
        if content_format == "vars":
            content = vars(obj)
        elif content_format == "dict":
            content = obj.__dict__
        else:
            content = str(obj)
    except Exception as e:
        content = f"debug_object-Could not inspect object. Fallback str(obj): {str(obj)}. Error: {e}"

    return pformat(content) if pretty else content
```

## Step 2 — banner separators

Mark scope boundaries with a repeated-token banner: `print("<token>" * <n>)`.

- **Token:** any short token — a symbol (`.,. % ^ # & @`) or a digit — chosen so
  each scope is visually distinct from its neighbours.
- **Length `<n>`:** pick one and keep it consistent per scope. Common choices in
  these files are **10, 25, or 50** (bigger = louder boundary).
- **Digits track call depth.** When using a digit as the token, order by nesting:
  the `__main__` guard = `0`, `main()` = `1`, and each deeper function gets the
  next digit (`2`, `3`, ...). Symbols are fine for leaf helpers.

| Scope | Open / close |
|-------|--------------|
| Module (top of file / very bottom) | `print(".,."*50)` + `debug_print("<filename>.py-STARTED", True, True)` / `-ENDED` |
| Class body (first & last lines inside the class) | `print("%"*50)` + `debug_print("<ClassName>-STARTED", True, True)` / `-ENDED` |
| `if __name__ == "__main__":` | `print("0"*25)` + `debug_print("__main__-STARTED", True, True)` / `-ENDED` |

## Step 3 — per-function / per-method instrumentation

For every function *or* method body:

1. **Scope marker** at entry and before each exit: `print("<token>"*<n>)`
   (unique per function per Step 2).
2. **STARTED / ENDED markers** via the helper:
   ```python
   debug_print("<func_name>-STARTED", True, True)
   ...
   debug_print("<func_name>-ENDED", True, True)
   ```
   Put an `-ENDED` (and the closing scope marker) before **every** `return` and
   one at the natural end.
3. **Variable dumps.** Immediately after a meaningful value is computed, dump it:
   ```python
   file_paths = [...]
   debug_print(f"<func_name>-file_paths:{file_paths}", True, True)
   ```
   Naming convention: `f"<func_name>-<var_name>:{var_name}"`.
4. **Branch markers.** Inside a taken `if`, announce it:
   ```python
   if file_path and file_path.exists():
       debug_print(f"<func_name>-file_path-IF-True:{file_path}", True, True)
   ```
5. **Exception handling.** In an `except` block, keep any existing
   `logger.error` / user print, then add a trace and re-close the scope:
   ```python
   except Exception as e:
       print(f"❌ <func_name> failed: {str(e)}")
       import traceback
       traceback.print_exc()
       debug_print(f"<func_name>-ENDED-EXCEPTION:{e}", True, True)
       print("<token>"*<n>)
   ```
   (Analyzer-dialect alternative: `debug_print(f"<func_name>-ENDED-Exception-e:{debug_object(e, True)}", True, True)`.)

## Step 4 — `__init__` attribute tracing (constructors)

- At the top: `debug_print(f"__init__self-1:{debug_object(self)}", True, True)` and
  a pretty variant using `debug_object(self, True)`.
- After **each** `self.<attr> = ...` assignment, echo it:
  ```python
  self.output_dir = OUTPUTS_DIR
  debug_print(f"__init__-output_dir:{self.output_dir}", True, True)
  ```
- Repeat the `self` dump at the end of `__init__` to show the fully built object.

## Step 5 — module constants

Right after key path/config constants are defined, echo them once at module load
(ingestion dialect uses `debug_print`):
```python
debug_print(f"SCRIPT_DIR:{SCRIPT_DIR}", True, True)
debug_print(f"PROJECT_ROOT:{PROJECT_ROOT}", True, True)
debug_print(f"COLLECTION_NAME:{COLLECTION_NAME}", True, True)
```

## Worked example (ingestion dialect)

Before:
```python
def get_report(analyses):
    valid = [a for a in analyses if "error" not in a]
    if not valid:
        return {"error": "none"}
    scores = [a["score"] for a in valid]
    return {"avg": sum(scores) / len(scores)}
```

After:
```python
def get_report(analyses):
    print("3"*25)
    debug_print("get_report-STARTED", True, True)

    valid = [a for a in analyses if "error" not in a]
    debug_print(f"get_report-valid:{valid}", True, True)
    if not valid:
        debug_print("get_report-ENDED", True, True)
        print("3"*25)
        return {"error": "none"}

    scores = [a["score"] for a in valid]
    debug_print(f"get_report-scores:{scores}", True, True)

    debug_print("get_report-ENDED", True, True)
    print("3"*25)
    return {"avg": sum(scores) / len(scores)}
```

## Removal (strip before publishing)

Everything added is a standalone `print(...)` / `debug_print(...)` /
`debug_object(...)` statement plus the helper defs, so it strips cleanly:

```bash
grep -vE '^\s*(print\(|debug_print\(|debug_object\()' file.py > file.clean.py
```

Then delete the `debug_print` / `debug_object` helper definitions and the
now-unused `from pprint import pformat` / `import traceback` imports by hand.
**Note:** heavy debug scaffolding inflates a file and, in a share-similarity
check, pads the denominator so it *masks* the real similarity of the underlying
code — always judge originality on the stripped version, not the instrumented
one.
