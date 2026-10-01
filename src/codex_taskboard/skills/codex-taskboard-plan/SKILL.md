---
name: codex-taskboard-plan
description: Produce a JSON task decomposition proposal from read-only repository analysis and context supplied by Taskboard. Use only when explicitly invoked by Taskboard.
---

# Taskboard task decomposition

First inspect the repository in the supplied workspace using read-only searches and file reads. Read AGENTS.md, relevant code, dependency boundaries and test instructions. Do not implement, edit files, install dependencies, run builds/tests, start other agents, request write permissions or operate Taskboard. Report unavailable evidence in the proposal instead of inventing it.

Create an editable task decomposition grounded in those files and the supplied requirements. Include explicit write scopes, dependencies, a reason why each task can run in parallel or must wait, and concrete acceptance criteria. Native subagents cannot allocate work outside the board scheduler. The proposal is not execution authorization.

Return only JSON with this shape:

```json
{"tasks":[{"key":"a","title":"...","description":"...","blockedByKeys":[],"writeScopes":[],"parallelReason":"...","acceptanceCriteria":"..."}]}
```

Keys must be unique. Dependencies must be acyclic and refer only to keys in this proposal. Use the task author's language.
