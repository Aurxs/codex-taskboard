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


PRIORITY_RANK = {
    Priority.URGENT.value: 0,
    Priority.HIGH.value: 1,
    Priority.MEDIUM.value: 2,
    Priority.LOW.value: 3,
    Priority.NONE.value: 4,
}

INITIAL_TURN_TEMPLATE = (
    "你正在执行任务「{identifier}: {title}」。\n\n"
    "{description}\n\n"
    "请在当前项目目录中完成该任务，遵循 Codex 已加载的全部项目指令与安全设置。"
    "持续工作到任务完成，并进行与改动相称的验证。最终回复请说明完成内容、"
    "验证结果，以及未完成项或需要人工决定的问题。不要操作 Taskboard；任务状态由调度器管理。"
)

QUOTA_RESUME_PROMPT = "刚才的执行因使用额度中断。请从当前 thread 的已有上下文继续，完成剩余工作和验证。"

FAILED_RETRY_PROMPT = "请从当前 thread 的已有上下文继续，重试尚未完成的工作，完成任务并进行与改动相称的验证。"

REVIEW_FEEDBACK_TEMPLATE = (
    "审阅未通过。用户反馈如下：\n{feedback}\n请继续修改并验证。"
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
