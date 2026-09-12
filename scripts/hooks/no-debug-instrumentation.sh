#!/usr/bin/env bash
# Refuse to commit temporary trace scaffolding added by the
# debug-instrumentation skill (see AGENTS_DEBUG_ADDER.md).
#
# Instrumented blocks are delimited by AKR-DEBUG-START / AKR-DEBUG-END markers,
# so those markers are the reliable signal. An unpaired marker is also reported
# because the removal script requires balanced pairs.
#
# Invoked by pre-commit with the staged Python filenames as arguments.

set -uo pipefail

failed=0

for file in "$@"; do
  [ -f "$file" ] || continue

  starts=$(grep -c 'AKR-DEBUG-START' "$file")
  ends=$(grep -c 'AKR-DEBUG-END' "$file")

  if [ "$starts" -gt 0 ] || [ "$ends" -gt 0 ]; then
    echo "ERROR: $file still contains debug instrumentation (${starts} START / ${ends} END markers)" >&2
    grep -n 'AKR-DEBUG-\(START\|END\)' "$file" >&2
    failed=1
  fi
done

if [ "$failed" -ne 0 ]; then
  cat >&2 <<'EOF'

Strip the trace blocks before committing:
  python .agents/skills/debug-instrumentation/scripts/remove_debug_instrumentation.py <file>

The script writes a separate cleaned file rather than editing in place; review
the diff and byte-compile the result before replacing the original.
EOF
  exit 1
fi

exit 0
