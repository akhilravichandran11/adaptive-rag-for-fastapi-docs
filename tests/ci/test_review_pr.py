import socket
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from markdown_it import MarkdownIt

from scripts.ci import review_pr as script


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a test tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.fixture
def workflow(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("PR_NUMBER", "42")
    monkeypatch.setenv("GITHUB_BASE_REF", "main")
    monkeypatch.setenv("PR_HEAD_SHA", "960b9a2993b9227ddd28deca773091509b10ed84")
    monkeypatch.setenv(
        "PR_REVIEW_RUN_URL", "https://github.com/owner/repo/actions/runs/123/attempts/2"
    )
    monkeypatch.setattr(script, "MODEL", "review-model")
    monkeypatch.setattr(script, "get_diff", lambda base: "x" * 1_069_795)
    client = Mock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="stop", message=SimpleNamespace(content="Example review.")
            )
        ]
    )
    monkeypatch.setattr(script, "OpenAI", lambda: client)
    run = Mock()
    monkeypatch.setattr(script.subprocess, "run", run)
    return client, run


def test_partial_coverage_is_first_notice(workflow):
    _, run = workflow

    assert script.main() == 0

    run.assert_called_once()
    assert run.call_args.args[0] == ["gh", "pr", "comment", "42", "--body-file", "-"]
    body = run.call_args.kwargs["input"]
    assert body.startswith(
        "### Automated code review\n\n"
        "> **Partial review · 1.4% of the diff**\n"
        "> The model received the first 15,000 of 1,069,795 diff characters."
    )
    assert body.index("Partial review") < body.index("Example review.")


@pytest.mark.parametrize("diff_length", [14_999, 15_000, 15_001])
@pytest.mark.parametrize("finish_reason", ["stop", "length"])
def test_input_and_output_limit_notices_are_independent(
    workflow, monkeypatch, diff_length, finish_reason
):
    client, run = workflow
    monkeypatch.setattr(script, "get_diff", lambda base: "x" * diff_length)
    client.chat.completions.create.return_value.choices[0].finish_reason = finish_reason

    assert script.main() == 0

    run.assert_called_once()
    body = run.call_args.kwargs["input"]
    assert ("Partial review" in body) == (diff_length > 15_000)
    assert ("Incomplete response" in body) == (finish_reason == "length")
    if finish_reason == "length":
        assert body.index("Incomplete response") < body.index("Example review.")


def test_footer_links_to_reviewed_commit_and_run_attempt(workflow):
    _, run = workflow

    assert script.main() == 0

    body = run.call_args.kwargs["input"]
    assert body.endswith(
        "Example review.\n\n---\n\n`review-model` · "
        "[`960b9a2`](https://github.com/owner/repo/commit/"
        "960b9a2993b9227ddd28deca773091509b10ed84) · "
        "[Workflow run](https://github.com/owner/repo/actions/runs/123/attempts/2)"
    )


@pytest.mark.parametrize(
    "review",
    [
        "Suggested fix\n\n```python\nprint('example')",
        "Suggested fix\n\n~~~~python\nprint('example')",
        "1. Fix\n\n   ```python\n   x = 1",
    ],
)
def test_truncated_code_block_does_not_swallow_footer_links(workflow, review):
    client, run = workflow
    choice = client.chat.completions.create.return_value.choices[0]
    choice.finish_reason = "length"
    choice.message.content = review

    assert script.main() == 0

    rendered = MarkdownIt().render(run.call_args.kwargs["input"])
    assert '<a href="https://github.com/owner/repo/commit/' in rendered
    assert '<a href="https://github.com/owner/repo/actions/runs/123/attempts/2">' in rendered
