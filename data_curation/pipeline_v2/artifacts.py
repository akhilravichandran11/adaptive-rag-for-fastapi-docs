import hashlib
import json


def artifact_bytes(value, name):
    try:
        return (
            json.dumps(
                value,
                sort_keys=True,
                ensure_ascii=False,
                allow_nan=False,
                indent=2,
                separators=(",", ": "),
            )
            + "\n"
        ).encode("utf-8")
    except (ValueError, TypeError, UnicodeError):
        raise ValueError(f"{name}: cannot encode JSON artifact") from None


def snapshot_hashes(recipe):
    schema = json.dumps(
        recipe.response_schema,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "prompt_template_sha256": hashlib.sha256(
            recipe.prompt_template.encode("utf-8")
        ).hexdigest(),
        "response_schema_sha256": hashlib.sha256(schema).hexdigest(),
    }
