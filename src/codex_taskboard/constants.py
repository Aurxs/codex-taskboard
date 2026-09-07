"""Shared state and task context templates; workflow instructions live in skills."""

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

# Keep task context separate from the explicitly invoked workflow skill.
INITIAL_TURN_TEMPLATE = (
    'You are working on task "{identifier}: {title}".\n\n'
    "{description}\n\n"
)

QUOTA_RESUME_PROMPT = (
    "Execution was interrupted by usage limits. Continue the remaining work."
)

FAILED_RETRY_PROMPT = "Retry the unfinished work in this thread."

REVIEW_FEEDBACK_TEMPLATE = (
    "Changes were requested during review. User feedback:\n{feedback}"
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
