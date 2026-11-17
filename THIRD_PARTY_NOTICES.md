# Third-party notices

This project's agent workflow (skills, agents, and the issue-tracker process
under `docs/agents/`) was built on ideas from the open-source projects,
documentation, articles, and video below. Unless a row says otherwise, the
text here is rewritten, not copied; each source keeps its own license or
copyright. The project's own code is under [LICENSE](LICENSE).

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
| [Anthropic, "Orchestrate teams of Claude Code sessions"](https://code.claude.com/docs/en/agent-teams) (Claude Code docs) | © Anthropic; ideas only, no text | Running several tickets at once with a lead and named teammates; when to use a team instead of one session | `.claude/settings.json`, the "Agent teams" section of `CLAUDE.md`, `.claude/commands/batch-lead.md` |
| [Felix Rieseberg, "How to be more ambitious: A tutorial for humans with models"](https://felixrieseberg.com/how-to-be-more-ambitious-a-tutorial-for-humans-with-models/) (article) | © the author; ideas only, no text | Use an outside judge: a plan is reviewed by the model that did not write it | `scripts/judge_plan.sh`, `docs/agents/plan-complexity-judge-design.md`, the "Plan judge" rule in `AGENTS.md` |
| [AI LABS, video summary of the article above](https://www.youtube.com/watch?v=92ZDgn8bgLM) (video) | © the author; ideas only, no text | Where I found the article; no ideas beyond it | same judge files |
| [FastAPI documentation](https://github.com/fastapi/fastapi/tree/master/docs) ([LICENSE](https://github.com/fastapi/fastapi/blob/master/LICENSE)), Copyright (c) 2018 Sebastián Ramírez | MIT | **Copied excerpts, not rewritten:** documentation passages retrieved during an evaluation run and stored with its results | `evaluation/results/20261116_055637/results.jsonl` |
