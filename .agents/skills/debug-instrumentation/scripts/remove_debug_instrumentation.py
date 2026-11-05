#!/usr/bin/env python3
"""Remove well-formed AKR debug blocks into a separate output file."""

import argparse
import re
from pathlib import Path

MARKER = re.compile(
    r"^\s*# AKR-DEBUG-(START|END): "
    r"([A-Za-z0-9_.:-]+)\s*$"
)


def remove_blocks(source: str) -> tuple[str, int]:
    """Return source without marked blocks and the number of removed blocks."""
    kept_lines: list[str] = []
    open_marker: str | None = None
    removed_count = 0

    for line_number, line in enumerate(source.splitlines(keepends=True), start=1):
        match = MARKER.match(line)
        if match:
            marker_type, marker_id = match.groups()
            if marker_type == "START":
                if open_marker is not None:
                    raise ValueError(
                        f"Nested marker at line {line_number}; {open_marker!r} is still open"
                    )
                open_marker = marker_id
                continue

            if open_marker is None:
                raise ValueError(f"Unexpected END marker at line {line_number}")
            if marker_id != open_marker:
                raise ValueError(
                    f"Mismatched END marker at line {line_number}: "
                    f"expected {open_marker!r}, found {marker_id!r}"
                )
            open_marker = None
            removed_count += 1
            continue

        if open_marker is None:
            kept_lines.append(line)

    if open_marker is not None:
        raise ValueError(f"Unclosed START marker: {open_marker!r}")

    return "".join(kept_lines), removed_count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=("Remove marker-delimited AKR debug blocks and write a separate file.")
    )
    parser.add_argument("source", type=Path, help="Instrumented Python source")
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="New path for cleaned source; must differ from source",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_path = args.source.resolve()
    output_path = args.output.resolve()

    if source_path == output_path:
        raise SystemExit("Refusing to overwrite the source; choose a separate --output")
    if not source_path.is_file():
        raise SystemExit(f"Source file does not exist: {source_path}")
    if output_path.exists():
        raise SystemExit(f"Output already exists: {output_path}")

    try:
        cleaned, removed_count = remove_blocks(source_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise SystemExit(f"Could not remove instrumentation: {exc}") from exc

    output_path.write_text(cleaned, encoding="utf-8")
    print(f"Removed {removed_count} debug block(s); wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
