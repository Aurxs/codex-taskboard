---
name: codex-taskboard-merge
description: Resolve current Git merge conflicts in a Taskboard integration worktree. Use only when explicitly invoked by Taskboard.
---

# Taskboard merge conflict resolution

Resolve only the current Git merge conflicts for the supplied task and source/target commits. Keep all work in this integration worktree.

- Preserve both histories. Complete and commit the merge.
- Format Git commit subjects as `<type>: <中文简述>`, using a lowercase English type, an ASCII colon and one space, followed by a concise Chinese summary of the actual changes. Use `chore` for merge commits, for example `chore: 合并任务分支并解决冲突`.
- Perform only proportionate, relevant verification.
- Do not reset or stash, publish branches, edit Taskboard, split tasks or start other workers.
- Report unresolved requirements in the user's language.
