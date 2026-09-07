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
- Format Git commit subjects as `<type>: <summary>`, using a lowercase English type, an ASCII colon and one space, followed by a concise summary of the actual changes. Inspect the project's recent Git history and follow its prevailing commit-summary language; if no clear convention exists, use the task author's language. Choose the type that matches the changes, such as `feat`, `fix`, `docs`, `refactor`, `test`, `build`, `ci` or `chore`. Examples: `feat: 添加模块化 Windows 桌面与打包适配` or `feat: add modular Windows desktop and packaging support`.
- In the final response, describe what was completed, verification results, and any unfinished work or questions requiring a human decision. Use the language of the task title and description unless the user requests otherwise.
- Do not operate Taskboard; the scheduler manages task status.
