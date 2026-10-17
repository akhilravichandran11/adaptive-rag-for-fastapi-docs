# Third-party notices

This project's agent workflow (skills, agents, and the issue-tracker process
under `docs/agents/`) was built on ideas from the open-source projects below.
Unless a row says otherwise, the text here is rewritten, not copied; each
source keeps its own license. The project's own code is under [LICENSE](LICENSE).

| Source | License | Used for | Files here |
|---|---|---|---|
| [Matt Pocock, `skills`](https://github.com/mattpocock/skills): `grilling`, `to-tickets`, `implement`, `tdd`, `wayfinder`, `pr` | MIT | The plan → tickets → implement → review workflow; an implementer that builds one ticket at a time; the local-markdown issue tracker; the Evidence and Merge Danger sections of PR text | `docs/agents/issue-tracker.md`, `.claude/agents/ticket-implementer.md`, `.codex/agents/ticket-implementer.toml`, `.claude/skills/pr-writeup/SKILL.md`, `.agents/skills/pr-writeup/SKILL.md` |
| [HumanLayer, `show-me`](https://github.com/humanlayer/skills/blob/main/plugins/show-me/skills/show-me/SKILL.md) | MIT | "Smallest view that makes the point" for PR summaries (via Matt Pocock's `pr`) | `.claude/skills/pr-writeup/SKILL.md`, `.agents/skills/pr-writeup/SKILL.md` |
| [Addy Osmani, `agent-skills`](https://github.com/addyosmani/agent-skills): [`code-simplification`](https://github.com/addyosmani/agent-skills/blob/main/skills/code-simplification/SKILL.md), [`incremental-implementation`](https://github.com/addyosmani/agent-skills/blob/main/skills/incremental-implementation/SKILL.md) | MIT | Simplification procedure; building a ticket in small, tested steps | `.claude/agents/active-code-simplifier.md`, `.codex/agents/active-code-simplifier.toml`, `docs/agents/issue-tracker.md` |
| [Anthropic, `code-simplifier`](https://github.com/anthropics/claude-plugins-official/tree/main/plugins/code-simplifier) | Apache-2.0 | Base of the simplifier agent; **modified**: rewritten for Python, limited to the task's own lines, never adds or removes a construct | `.claude/agents/active-code-simplifier.md`, `.codex/agents/active-code-simplifier.toml` |
| [wshobson, `agents` (python-development)](https://github.com/wshobson/agents/tree/main/plugins/python-development) | MIT | Rule of three; delete before abstracting; keep I/O at the top | same simplifier files |
| [l-mb, `python-refactoring-skills`](https://github.com/l-mb/python-refactoring-skills) | MIT | Catching complexity with deterministic tools (the `ruff` complexity check) | same simplifier files |
| [affaan-m, ECC `python-patterns`](https://github.com/affaan-m/ECC/blob/main/skills/python-patterns/SKILL.md) | MIT | Plain-Python idioms | same simplifier files |
| [Fabio Akita, "Clean Code for AI Agents"](https://akitaonrails.com/en/2026/04/20/clean-code-for-ai-agents/) (article) | © the author; ideas only, no text | Distinctive names; error messages that include the value; *why* comments over *what* comments | same simplifier files |
