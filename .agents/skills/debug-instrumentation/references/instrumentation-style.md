# AKR debug instrumentation style

Use one dialect so traces are predictable and removable.

## Marker contract

Every added block must have a unique identifier and this exact shape:

```python
# AKR-DEBUG-START: load_manifest.entry
debug_print("load_manifest-STARTED", before=True, after=True)
# AKR-DEBUG-END: load_manifest.entry
```

Identifiers may contain letters, digits, dots, underscores, colons, and hyphens. Do not nest marked blocks. Do not put original program statements inside them.

Use names such as:

- `helpers.debug_print` for the helper block;
- `<function>.entry` and `<function>.exit.<n>` for boundaries;
- `<function>.value.<name>` for selected values;
- `<function>.branch.<name>` for branch decisions.

## Helper block

Add this once after imports when the file has no suitable helper. Keep the complete block marker-delimited.

```python
# AKR-DEBUG-START: helpers.debug_print
_AKR_SENSITIVE_NAMES = (
    "api_key",
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
)


def debug_print(value: object, before: bool = False, after: bool = False) -> None:
    try:
        if before:
            print()
        print(f"[akr-DEBUG]->{value}", flush=True)
        if after:
            print()
    except Exception:
        # Debug output must not replace the program's original failure.
        pass


def debug_value(name: str, value: object) -> None:
    normalized_name = name.casefold()
    if any(term in normalized_name for term in _AKR_SENSITIVE_NAMES):
        rendered = "<redacted>"
    else:
        try:
            rendered = repr(value)
        except Exception:
            rendered = f"<unprintable {type(value).__name__}>"
        if len(rendered) > 500:
            rendered = f"{rendered[:500]}...<truncated>"
    debug_print(f"{name}:{rendered}", before=True, after=True)
# AKR-DEBUG-END: helpers.debug_print
```

Redaction based on a variable name is only a backstop. Do not pass a sensitive value under an innocent name. Object `repr()` can be expensive, execute custom code, or expose nested secrets, so prefer scalar metadata and summaries.

## Trace patterns

Function entry:

```python
# AKR-DEBUG-START: load_manifest.entry
debug_print("load_manifest-STARTED", before=True, after=True)
# AKR-DEBUG-END: load_manifest.entry
```

Selected value:

```python
# AKR-DEBUG-START: load_manifest.value.document_count
debug_value("load_manifest-document_count", len(documents))
# AKR-DEBUG-END: load_manifest.value.document_count
```

Branch decision:

```python
if not documents:
    # AKR-DEBUG-START: load_manifest.branch.no_documents
    debug_print("load_manifest-no_documents-IF-True", before=True, after=True)
    # AKR-DEBUG-END: load_manifest.branch.no_documents
```

Exit before an existing return:

```python
# AKR-DEBUG-START: load_manifest.exit.1
debug_print("load_manifest-ENDED", before=True, after=True)
# AKR-DEBUG-END: load_manifest.exit.1
return documents
```

Exception trace:

```python
except Exception as exc:
    # AKR-DEBUG-START: load_manifest.exception
    debug_value("load_manifest-exception_type", type(exc).__name__)
    # AKR-DEBUG-END: load_manifest.exception
    raise
```

Do not add `raise` if the original handler did not raise. Do not print exception text by default.

## Density guidance

Default to a small set of high-value traces:

- function entry and meaningful exits;
- branch chosen at a suspected failure point;
- counts and stable identifiers;
- file paths when they do not reveal private user data;
- elapsed duration around a slow operation, using `time.perf_counter()` only when requested.

Avoid module banners, class-body banners, constructor dumps, whole prompts, document contents, response objects, environment dictionaries, and per-item traces in large loops unless explicitly requested.
