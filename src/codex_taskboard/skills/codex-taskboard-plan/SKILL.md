---
name: codex-taskboard-plan
description: Produce a JSON task decomposition proposal from context supplied by Taskboard. Use only when explicitly invoked by Taskboard.
---

# Taskboard task decomposition

Create a task decomposition proposal using only the supplied context. Do not implement, edit files, run commands, start other agents or operate Taskboard.

Return only JSON with this shape:

```json
{"tasks":[{"key":"a","title":"...","description":"...","blockedByKeys":[],"writeScopes":[]}]}
```

Keys must be unique. Dependencies must be acyclic and refer only to keys in this proposal. Use the task author's language.
