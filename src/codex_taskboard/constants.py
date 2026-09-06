"""Shared state and prompt constants.

The prompt strings in this module are deliberately the only text the
orchestrator adds to a Codex turn.  In particular, no system/developer
instructions or per-turn configuration is supplied by Taskboard.
"""

from enum import StrEnum


class TaskStatus(StrEnum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    IN_REVIEW = "in_review"
    DONE = "done"
    CANCELED = "canceled"


class RunState(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    WAITING_QUOTA = "waiting_quota"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_INPUT = "waiting_input"
    FAILED = "failed"


class Priority(StrEnum):
    URGENT = "urgent"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NONE = "none"
    DRAFT = "draft"


PRIORITY_RANK = {
    Priority.URGENT.value: 0,
    Priority.HIGH.value: 1,
    Priority.MEDIUM.value: 2,
    Priority.LOW.value: 3,
    Priority.NONE.value: 4,
    Priority.DRAFT.value: 5,
}

# Keep orchestration text in English and explicitly preserve the task author's
# language, so the wrapper does not force Chinese replies for English tasks.
INITIAL_TURN_TEMPLATE = (
    'You are working on task "{identifier}: {title}".\n\n'
    "{description}\n\n"
    "Complete this task in the current project directory, following all project instructions "
    "and safety settings already loaded by Codex. Continue until the task is complete and "
    "perform verification proportionate to the changes. Before completing the task, "
    "you must commit the changes produced by this task. "
    "In your final response, describe what was completed, verification results, and any "
    "unfinished work or questions requiring a human decision. Use the language of the task "
    "title and description unless the user requests otherwise. "
    "Do not operate Taskboard; the scheduler manages task status."
)

QUOTA_RESUME_PROMPT = (
    "Execution was interrupted by usage limits. Continue from this thread's existing "
    "context and complete the remaining work and verification. Keep using the user's language."
)

FAILED_RETRY_PROMPT = (
    "Continue from this thread's existing context, retry the unfinished work, complete "
    "the task, and verify the changes proportionately. Keep using the user's language."
)

REVIEW_FEEDBACK_TEMPLATE = (
    "Changes were requested during review. User feedback:\n{feedback}\n"
    "Continue editing and verifying. Keep using the user's language."
)

APPROVAL_METHODS = {
    "item/commandExecution/requestApproval": "command_approval",
    "item/fileChange/requestApproval": "file_approval",
    "item/permissions/requestApproval": "permission_request",
    "item/tool/requestUserInput": "user_input",
}

ALL_STATUSES = {item.value for item in TaskStatus}
ALL_RUN_STATES = {item.value for item in RunState}
ALL_PRIORITIES = {item.value for item in Priority}
