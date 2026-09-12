#!/usr/bin/env python3
"""Post an LLM-generated code review comment on a GitHub pull request.

Runs in CI (see .github/workflows/pr-review.yml). This reuses the review
criteria and output contract defined in .agents/skills/code-review/SKILL.md
so an automated CI review matches the same severity-ranked format as an
interactive review done by an agent in Warp.

Required environment variables:
  OPENAI_API_KEY   - OpenAI API key (GitHub Actions secret)
  GH_TOKEN         - token for the `gh` CLI to post the comment
  PR_NUMBER        - the pull request number to comment on
  GITHUB_BASE_REF  - the PR's base branch (set automatically by GitHub Actions
                     for pull_request-triggered workflows)

Optional:
  PR_REVIEW_MODEL  - OpenAI model to use (default: gpt-4o-mini)
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from openai import OpenAI

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_PATH = REPO_ROOT / ".agents" / "skills" / "code-review" / "SKILL.md"
MAX_DIFF_CHARS = 15_000
MODEL = os.getenv("PR_REVIEW_MODEL", "gpt-4o-mini")


def load_skill_instructions() -> str:
    """Strip the YAML frontmatter from SKILL.md and return the instructions body."""
    if not SKILL_PATH.is_file():
        raise SystemExit(
            f"Code review skill not found at {SKILL_PATH}. "
            "Commit .agents/skills/code-review/SKILL.md before enabling this workflow."
        )

    text = SKILL_PATH.read_text(encoding="utf-8")
    parts = text.split("---", 2)
    if len(parts) >= 3:
        return parts[2].strip()
    return text.strip()


def get_diff(base_ref: str) -> str:
    """Diff the PR branch against the base branch, excluding the lockfile."""
    subprocess.run(["git", "fetch", "origin", base_ref], check=True, cwd=REPO_ROOT)
    result = subprocess.run(
        ["git", "diff", f"origin/{base_ref}...HEAD", "--", ".", ":!uv.lock"],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    return result.stdout


def build_prompt(diff: str) -> tuple[str, str]:
    instructions = load_skill_instructions()
    truncated = len(diff) > MAX_DIFF_CHARS
    body = diff[:MAX_DIFF_CHARS]

    user_message = (
        "Review the following pull request diff. Follow the output contract exactly.\n\n"
        f"```diff\n{body}\n```\n"
    )
    if truncated:
        user_message += (
            f"\n(Diff truncated to {MAX_DIFF_CHARS} characters; "
            "note in your report that review coverage may be incomplete.)"
        )
    return instructions, user_message


def post_comment(pr_number: str, body: str) -> None:
    subprocess.run(
        ["gh", "pr", "comment", pr_number, "--body-file", "-"],
        input=body,
        text=True,
        check=True,
        cwd=REPO_ROOT,
    )


def main() -> int:
    base_ref = os.environ["GITHUB_BASE_REF"]
    pr_number = os.environ["PR_NUMBER"]

    diff = get_diff(base_ref)
    if not diff.strip():
        print("No diff to review (after excluding uv.lock); skipping.")
        return 0

    system_prompt, user_prompt = build_prompt(diff)

    client = OpenAI()
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.1,
        max_tokens=3000,
    )
    review = response.choices[0].message.content
    if not review or not review.strip():
        print("Model returned an empty review.", file=sys.stderr)
        return 1

    header = f"### \U0001f916 Automated code review (`{MODEL}`)\n\n"
    post_comment(pr_number, header + review)
    print("Posted review comment.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
