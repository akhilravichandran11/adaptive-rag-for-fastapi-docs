#!/usr/bin/env bash
# Keep the two simplifier agent definitions in sync.
#
# The body of .claude/agents/active-code-simplifier.md (after the front matter)
# and developer_instructions in .codex/agents/active-code-simplifier.toml are
# twins: identical except for one tool-wording line. Fail on any other
# difference.
#
# Invoked by pre-commit when either file is staged; takes no arguments.

set -euo pipefail

python3 -I - <<'PY'
import difflib
import sys
import tomllib

MD = ".claude/agents/active-code-simplifier.md"
TOML = ".codex/agents/active-code-simplifier.toml"
# The one line allowed to differ. If the tool wording changes on purpose,
# update both lines here in the same commit.
ALLOWED = [
    "-You start with no memory of the conversation that spawned you.",
    "+You run in an isolated thread with no memory of the conversation that spawned you.",
]

with open(MD) as f:
    md_body = f.read().split("---\n", 2)[2]
with open(TOML, "rb") as f:
    toml_body = tomllib.load(f)["developer_instructions"]

diff = [
    line
    for line in difflib.unified_diff(
        md_body.strip("\n").splitlines(),
        toml_body.strip("\n").splitlines(),
        lineterm="",
        n=0,
    )
    if line[:1] in "+-" and not line.startswith(("---", "+++"))
]
if diff != ALLOWED:
    print(f"ERROR: {MD} and {TOML} have drifted. Lines that differ:", file=sys.stderr)
    print("\n".join(diff) or "(none)", file=sys.stderr)
    print("Change both files together; only the tool-wording line may differ.", file=sys.stderr)
    sys.exit(1)
PY
