---
name: codex-taskboard-execute
description: Complete tasks dispatched by Taskboard, including follow-ups, retries and review feedback. Use only when explicitly invoked by Taskboard.
---

# Taskboard task execution

Complete the supplied task in the current project directory, following the project instructions and safety settings already loaded by Codex.

- Continue until the task is complete. For a follow-up, retry or review request, use this thread's existing context and finish the remaining work.
- Keep changes within the declared modification scope, when supplied; request clarification before expanding it.
- If a dependency integration revision is supplied, resolve pending merge conflicts and revalidate this task against the updated dependencies.
- Perform verification proportionate to the changes. Before completing the task, commit the changes produced by this task.
- In the final response, describe what was completed, verification results, and any unfinished work or questions requiring a human decision. Use the language of the task title and description unless the user requests otherwise.
- Do not operate Taskboard; the scheduler manages task status.
