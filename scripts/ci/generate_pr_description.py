#!/usr/bin/env python3
"""Fill in a pull request's title and body when it was opened without one.

Runs in CI (see .github/workflows/pr-description.yml). Only acts when the PR
body is empty/whitespace, so it never overwrites a description someone
actually wrote. Reuses the criteria in .agents/skills/pr-description/SKILL.md
so the auto-generated description follows the same Conventional Commits
title style and Summary/Changes/Testing body structure as an interactive
draft from an agent.

Required environment variables:
  OPENAI_API_KEY   - OpenAI API key (GitHub Actions secret)
  GH_TOKEN         - token for the `gh` CLI to update the PR
  PR_NUMBER        - the pull request number to update
  PR_BODY          - the PR's current body (may be empty)
  GITHUB_BASE_REF  - the PR's base branch (set automatically by GitHub Actions
                     for pull_request-triggered workflows)

Optional:
  PR_DESCRIPTION_MODEL - OpenAI model to use (default: gpt-4o-mini)
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from openai import OpenAI

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_PATH = REPO_ROOT / ".agents" / "skills" / "pr-description" / "SKILL.md"
MAX_DIFF_CHARS = 15_000
MODEL = os.getenv("PR_DESCRIPTION_MODEL", "gpt-4o-mini")

RESPONSE_FORMAT_INSTRUCTIONS = """
Respond in exactly this format, with no other text before or after:

TITLE: <the PR title, one line>
---
<the PR body markdown, following the Output contract's Body structure>
"""


def load_skill_instructions() -> str:
    """Strip the YAML frontmatter from SKILL.md and return the instructions body."""
    if not SKILL_PATH.is_file():
        raise SystemExit(
            f"pr-description skill not found at {SKILL_PATH}. "
            "Commit .agents/skills/pr-description/SKILL.md before enabling this workflow."
        )

    text = SKILL_PATH.read_text(encoding="utf-8")
    parts = text.split("---", 2)
    if len(parts) >= 3:
        return parts[2].strip()
    return text.strip()


def get_branch_summary(base_ref: str) -> str:
    """Collect the commit log and diffstat for the PR branch vs. its base."""
    subprocess.run(["git", "fetch", "origin", base_ref], check=True, cwd=REPO_ROOT)

    log = subprocess.run(
        ["git", "log", f"origin/{base_ref}..HEAD", "--oneline"],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    ).stdout

    diff = subprocess.run(
        ["git", "diff", f"origin/{base_ref}...HEAD", "--", ".", ":!uv.lock"],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    ).stdout

    truncated = len(diff) > MAX_DIFF_CHARS
    diff = diff[:MAX_DIFF_CHARS]
    if truncated:
        diff += f"\n... (diff truncated to {MAX_DIFF_CHARS} characters)"

    return f"Commits:\n{log}\nDiff:\n```diff\n{diff}\n```\n"


def build_prompt(branch_summary: str) -> tuple[str, str]:
    instructions = load_skill_instructions()
    user_message = (
        "Draft a title and body for this pull request based on the following "
        f"branch summary.\n\n{branch_summary}\n{RESPONSE_FORMAT_INSTRUCTIONS}"
    )
    return instructions, user_message


def parse_response(text: str) -> tuple[str, str]:
    if not text.startswith("TITLE:"):
        raise ValueError(f"Response did not start with 'TITLE:': {text[:200]!r}")
    first_line, _, rest = text.partition("\n")
    title = first_line[len("TITLE:") :].strip()
    if "---" not in rest:
        raise ValueError("Response did not contain a '---' body delimiter")
    _, _, body = rest.partition("---")
    return title, body.strip()


def update_pr(pr_number: str, title: str, body: str) -> None:
    footer = (
        "\n\n_This description was generated automatically from the branch diff "
        f"by the pr-description workflow using `{MODEL}`. Please review for accuracy._"
    )
    subprocess.run(
        ["gh", "pr", "edit", pr_number, "--title", title, "--body", body + footer],
        check=True,
        cwd=REPO_ROOT,
    )


def main() -> int:
    pr_body = os.environ.get("PR_BODY", "")
    if pr_body.strip():
        print("PR already has a body; leaving it untouched.")
        return 0

    base_ref = os.environ["GITHUB_BASE_REF"]
    pr_number = os.environ["PR_NUMBER"]

    branch_summary = get_branch_summary(base_ref)
    system_prompt, user_prompt = build_prompt(branch_summary)

    client = OpenAI()
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
        max_tokens=1200,
    )
    raw = response.choices[0].message.content
    if not raw or not raw.strip():
        print("Model returned an empty response.", file=sys.stderr)
        return 1

    try:
        title, body = parse_response(raw.strip())
    except ValueError as error:
        print(f"Could not parse model response: {error}", file=sys.stderr)
        return 1

    update_pr(pr_number, title, body)
    print(f"Updated PR #{pr_number} with generated title and body.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
