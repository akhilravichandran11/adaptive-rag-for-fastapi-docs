"""Offline checks for PR autofill and suggestion publishing."""

import json
import socket
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts.ci import generate_pr_description as script

VALID_RESPONSE = """TITLE: ci: improve PR suggestions
---
## Summary
Example description.

## Changes
- Publish suggestions.

## Testing
- Test execution was not verified by this workflow.
"""


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a test tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.fixture
def workflow(monkeypatch, tmp_path):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("PR_NUMBER", "42")
    monkeypatch.setenv("PR_BODY", "")
    monkeypatch.setenv("GITHUB_BASE_REF", "main")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setattr(script, "get_branch_summary", lambda base, number: "Example diff")

    client = Mock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content=VALID_RESPONSE),
            )
        ]
    )
    monkeypatch.setattr(script, "OpenAI", lambda: client)
    run = Mock(return_value=SimpleNamespace(stdout=""))
    monkeypatch.setattr(script.subprocess, "run", run)
    return client, run, summary


@pytest.mark.parametrize("body", ["", " \n\t"])
def test_empty_description_still_applies_title_and_body(workflow, monkeypatch, body):
    client, run, summary = workflow
    monkeypatch.setenv("PR_BODY", body)
    run.return_value.stdout = body

    assert script.main() == 0

    command = run.call_args.args[0]
    assert command[:4] == ["gh", "pr", "edit", "42"]
    assert command[command.index("--title") + 1] == "ci: improve PR suggestions"
    assert command[command.index("--body") + 1].startswith("## Summary\nExample description.")
    assert client.chat.completions.create.call_count == 1
    assert not summary.exists()


@pytest.mark.parametrize(
    ("initial_body", "live_body"),
    [("Written description", ""), ("", "Written during generation")],
)
@pytest.mark.parametrize("existing_comment", [False, True])
def test_existing_text_gets_suggestions_in_both_places(
    workflow, monkeypatch, initial_body, live_body, existing_comment
):
    client, run, summary = workflow
    monkeypatch.setenv("PR_BODY", initial_body)
    marker = "<!-- pr-description-suggestion -->"
    pages = [
        [
            {"id": 10, "user": {"login": "human"}, "body": marker},
            {"id": 11, "user": {"login": "github-actions[bot]"}, "body": "Other report"},
        ],
        (
            [{"id": 99, "user": {"login": "github-actions[bot]"}, "body": marker}]
            if existing_comment
            else []
        ),
    ]
    results = []
    if not initial_body:
        results.append(SimpleNamespace(stdout=live_body))
    results.extend([SimpleNamespace(stdout=json.dumps(pages)), SimpleNamespace(stdout="")])
    run.side_effect = results

    assert script.main() == 0

    commands = [call.args[0] for call in run.call_args_list]
    assert not any(command[:3] == ["gh", "pr", "edit"] for command in commands)
    assert "--paginate" in commands[-2]
    assert "--slurp" in commands[-2]
    command = commands[-1]
    expected_endpoint = (
        "repos/owner/repo/issues/comments/99"
        if existing_comment
        else "repos/owner/repo/issues/42/comments"
    )
    assert command[2] == expected_endpoint
    assert command[command.index("--method") + 1] == ("PATCH" if existing_comment else "POST")
    posted = command[command.index("--raw-field") + 1].removeprefix("body=")
    assert "ci: improve PR suggestions" in posted
    assert "Example description." in posted
    assert summary.read_text(encoding="utf-8") == posted + "\n"
    assert client.chat.completions.create.call_count == 1


def test_comment_failure_preserves_summary(workflow, monkeypatch):
    _, run, summary = workflow
    monkeypatch.setenv("PR_BODY", "Written description")
    run.side_effect = [
        SimpleNamespace(stdout="[[]]"),
        subprocess.CalledProcessError(1, ["gh", "api"]),
    ]

    with pytest.raises(subprocess.CalledProcessError):
        script.main()

    assert "Example description." in summary.read_text(encoding="utf-8")
    assert not any(call.args[0][:3] == ["gh", "pr", "edit"] for call in run.call_args_list)


def test_missing_key_skips_generation(workflow, monkeypatch):
    client, run, summary = workflow
    monkeypatch.delenv("OPENAI_API_KEY")

    assert script.main() == 0
    assert client.chat.completions.create.call_count == 0
    assert run.call_count == 0
    assert not summary.exists()


@pytest.mark.parametrize("pr_body", ["", "Written description"])
@pytest.mark.parametrize(
    "content",
    [
        None,
        "",
        " \n\t",
        VALID_RESPONSE.replace("TITLE:", "SUBJECT:", 1),
        VALID_RESPONSE.replace("ci: improve PR suggestions", " ", 1),
        VALID_RESPONSE.replace("\n---\n", "\nseparator\n", 1),
        "TITLE: ci: improve PR suggestions\n---\n ",
        VALID_RESPONSE.replace("## Summary", "## Overview"),
        VALID_RESPONSE.replace("Example description.", ""),
        VALID_RESPONSE.replace("- Publish suggestions.", ""),
        VALID_RESPONSE.replace("## Changes", "## Summary"),
        VALID_RESPONSE.replace("## Testing", "## Tests"),
        VALID_RESPONSE.replace("- Test execution was not verified by this workflow.", ""),
        VALID_RESPONSE.replace(
            "Test execution was not verified by this workflow.",
            "All tests passed.",
        ),
    ],
)
def test_invalid_content_is_never_published(workflow, monkeypatch, pr_body, content):
    client, run, summary = workflow
    monkeypatch.setenv("PR_BODY", pr_body)
    response = client.chat.completions.create.return_value
    response.choices[0].message.content = content

    assert script.main() == 1
    assert run.call_count == 0
    assert not summary.exists()


@pytest.mark.parametrize("pr_body", ["", "Written description"])
@pytest.mark.parametrize(
    "finish_reason",
    ["length", "content_filter", "tool_calls", "function_call", None],
)
def test_incomplete_response_is_never_published(workflow, monkeypatch, pr_body, finish_reason):
    client, run, summary = workflow
    monkeypatch.setenv("PR_BODY", pr_body)
    response = client.chat.completions.create.return_value
    response.choices[0].finish_reason = finish_reason

    assert script.main() == 1
    assert run.call_count == 0
    assert not summary.exists()


@pytest.mark.parametrize("pr_body", ["", "Written description"])
def test_missing_choices_are_never_published(workflow, monkeypatch, pr_body):
    client, run, summary = workflow
    monkeypatch.setenv("PR_BODY", pr_body)
    client.chat.completions.create.return_value.choices = []

    assert script.main() == 1
    assert run.call_count == 0
    assert not summary.exists()


@pytest.mark.parametrize("base_ref", ["main", "release"])
def test_pr_diff_does_not_replace_trusted_checkout(tmp_path, monkeypatch, base_ref):
    """A PR may change automation files, but those files must remain data."""
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    trusted = tmp_path / "trusted"
    seed.mkdir()

    def git(*args, cwd=seed):
        return subprocess.run(
            ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
        ).stdout

    git("init", "--bare", str(remote))
    git("init", "-b", "main")
    git("config", "user.name", "Workflow test")
    git("config", "user.email", "workflow@example.invalid")
    git("remote", "add", "origin", str(remote))
    script_path = Path("scripts/ci/generate_pr_description.py")
    skill_path = Path(".agents/skills/pr-writeup/SKILL.md")
    (seed / script_path).parent.mkdir(parents=True)
    (seed / skill_path).parent.mkdir(parents=True)
    (seed / script_path).write_text("# Trusted automation\n", encoding="utf-8")
    (seed / skill_path).write_text("Trusted instructions\n", encoding="utf-8")
    (seed / "uv.lock").write_text("original\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "chore: common base")

    if base_ref != "main":
        git("checkout", "-b", base_ref)
        (seed / "base-only.txt").write_text("BASE_ONLY\n", encoding="utf-8")
        git("add", ".")
        git("commit", "-m", "chore: base-only change")
    git("push", "origin", f"{base_ref}:refs/heads/{base_ref}")
    git("checkout", "-b", "feature")
    (seed / script_path).write_text(
        'raise RuntimeError("PR automation must not run")\n', encoding="utf-8"
    )
    (seed / skill_path).write_text("Untrusted PR instructions\n", encoding="utf-8")
    (seed / "feature.txt").write_text("PR_CHANGE\n", encoding="utf-8")
    (seed / "uv.lock").write_text("LOCK_ONLY_CHANGE\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "feat: PR change", "-m", "Explain the functional change.")
    git("push", "origin", "HEAD:refs/pull/42/head")

    git("checkout", "main")
    (seed / "trusted-only.txt").write_text("DEFAULT_ONLY\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "chore: default-only change")
    git("push", "origin", "main:refs/heads/main")
    git("clone", "--branch", "main", str(remote), str(trusted))
    monkeypatch.setattr(script, "REPO_ROOT", trusted)
    monkeypatch.setattr(script, "SKILL_PATH", trusted / skill_path)
    before = git("rev-parse", "HEAD", cwd=trusted)

    summary = script.get_branch_summary(base_ref, "42")

    assert "feat: PR change" in summary
    assert "PR_CHANGE" in summary
    assert "PR automation must not run" in summary
    assert "Untrusted PR instructions" in summary
    assert "DEFAULT_ONLY" not in summary
    assert "BASE_ONLY" not in summary
    assert "LOCK_ONLY_CHANGE" not in summary
    assert "chore: default-only change" not in summary
    assert git("rev-parse", "HEAD", cwd=trusted) == before
    assert git("status", "--porcelain", cwd=trusted) == ""
    assert (trusted / script_path).read_text(encoding="utf-8") == "# Trusted automation\n"
    assert script.load_skill_instructions() == "Trusted instructions"

    monkeypatch.setattr(script, "MAX_DIFF_CHARS", 1)
    shortened = script.get_branch_summary(base_ref, "42")
    overview, patch = shortened.split("Diff:\n", 1)
    assert "Explain the functional change." in overview
    for path in (str(script_path), str(skill_path), "feature.txt", "uv.lock"):
        assert path in overview
    assert "diff truncated to 1 characters" in patch
    assert "PR_CHANGE" not in patch
    assert git("rev-parse", "HEAD", cwd=trusted) == before
    assert git("status", "--porcelain", cwd=trusted) == ""


@pytest.mark.parametrize("number", ["", "0", "-1", "42/head", "42\n43"])
def test_invalid_pr_number_is_rejected_before_fetch(monkeypatch, number):
    run = Mock(side_effect=AssertionError("Git must not run for an invalid PR number"))
    monkeypatch.setattr(script.subprocess, "run", run)

    with pytest.raises(ValueError, match="positive integer"):
        script.get_branch_summary("main", number)

    assert run.call_count == 0


@pytest.mark.parametrize(
    "title",
    [
        "fix: support legacy includes",
        "feat(skills): add review guidance",
        "refactor!: remove legacy backend",
        "feat(api)!: replace endpoint",
        "fix: " + "x" * 66,
    ],
)
def test_valid_title_publishes_without_correction(workflow, title):
    client, run, _ = workflow
    client.chat.completions.create.return_value.choices[0].message.content = VALID_RESPONSE.replace(
        "ci: improve PR suggestions", title
    )

    assert script.main() == 0
    assert client.chat.completions.create.call_count == 1
    command = run.call_args.args[0]
    assert command[command.index("--title") + 1] == title


@pytest.mark.parametrize("pr_body", ["", "Written description"])
@pytest.mark.parametrize(
    "title",
    [
        "Updated some stuff",
        "fix: " + "x" * 67,
        "fix: support legacy includes.",
        "fix: support\tlegacy includes",
    ],
)
@pytest.mark.parametrize("bad_corrections", [0, 1, 2])
def test_title_gets_at_most_two_corrections(workflow, monkeypatch, pr_body, title, bad_corrections):
    client, run, summary = workflow
    monkeypatch.setenv("PR_BODY", pr_body)
    corrected = bad_corrections < 2
    # A correction reply is the title alone; the body stays the first reply's.
    contents = [VALID_RESPONSE.replace("ci: improve PR suggestions", title)]
    contents += [title] * bad_corrections
    if corrected:
        contents.append("ci: improve PR suggestions")
    client.chat.completions.create.side_effect = [
        SimpleNamespace(
            choices=[
                SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=content))
            ]
        )
        for content in contents
    ]
    if pr_body:
        run.side_effect = [SimpleNamespace(stdout="[[]]"), SimpleNamespace(stdout="")]

    assert script.main() == (0 if corrected else 1)
    assert client.chat.completions.create.call_count == (2 if bad_corrections == 0 else 3)
    if corrected:
        if pr_body:
            assert "ci: improve PR suggestions" in summary.read_text()
            assert not any(call.args[0][:3] == ["gh", "pr", "edit"] for call in run.call_args_list)
        else:
            command = run.call_args.args[0]
            assert command[command.index("--title") + 1] == "ci: improve PR suggestions"
            assert command[command.index("--body") + 1].startswith(
                "## Summary\nExample description."
            )
    else:
        assert run.call_count == 0
        assert not summary.exists()


@pytest.mark.parametrize("failure", ["body", "empty", "length", "choices"])
def test_bad_correction_never_publishes(workflow, failure):
    client, run, summary = workflow
    first = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(
                    content=VALID_RESPONSE.replace("ci: improve PR suggestions", "Updated stuff")
                ),
            )
        ]
    )
    second = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="length" if failure == "length" else "stop",
                message=SimpleNamespace(
                    content=(
                        ""
                        if failure == "empty"
                        else VALID_RESPONSE.replace("## Testing", "## Tests")
                        if failure == "body"
                        else VALID_RESPONSE
                    )
                ),
            )
        ]
    )
    if failure == "choices":
        second.choices = []
    # A correction reply is read as the title alone, so a full reply with a bad body
    # fails the title check and is asked for once more.
    client.chat.completions.create.side_effect = [first, second, second]

    assert script.main() == 1
    assert client.chat.completions.create.call_count == (3 if failure == "body" else 2)
    assert run.call_count == 0
    assert not summary.exists()
