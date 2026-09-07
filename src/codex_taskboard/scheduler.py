"""Per-project task scheduler and Codex turn lifecycle."""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone
from typing import Any

from .app_server import CodexAppServer, extract_identifier, usage_error_info
from .constants import (
    APPROVAL_METHODS,
    FAILED_RETRY_PROMPT,
    INITIAL_TURN_TEMPLATE,
    QUOTA_RESUME_PROMPT,
    REVIEW_FEEDBACK_TEMPLATE,
    RunState,
    TaskStatus,
)
from .db import Database, utc_now
from .desktop_server import DesktopAppServer
from .errors import (
    AppServerError,
    ConflictError,
    NotFoundError,
    TaskboardError,
    UsageLimitExceeded,
    ValidationError,
)
from .events import EventBus
from .parallel_runtime import ParallelRuntime
from .planning import TaskPlanning
from .questions import REPLY_OPEN, record_questions, reply_summary, reply_text, validate_answers


def _values(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _values(child)


def _identifier(value: Any, *names: str) -> str | None:
    return extract_identifier(value, *names)


def _short_text(value: Any, *, limit: int = 4000) -> str | None:
    candidates: list[str] = []
    for item in _values(value):
        for key in ("text", "message", "content", "lastAgentMessage", "output"):
            candidate = item.get(key)
            if isinstance(candidate, str) and candidate.strip():
                candidates.append(candidate.strip())
    if not candidates:
        return None
    text = candidates[-1]
    return text if len(text) <= limit else text[-limit:]


def _turn_status(value: Any) -> str | None:
    for item in _values(value):
        candidate = item.get("status")
        if isinstance(candidate, str):
            normalized = candidate.replace("-", "_").lower()
            if normalized in {
                "completed",
                "complete",
                "succeeded",
                "failed",
                "interrupted",
                "in_progress",
                "inprogress",
                "running",
                "pending",
            }:
                return normalized
    return None


def _structured_error(value: Any) -> dict[str, Any] | None:
    for item in _values(value):
        error = item.get("error")
        if isinstance(error, dict):
            return error
    return None


def _reset_due(value: str | float | int | None) -> bool:
    if value is None:
        return False
    try:
        if isinstance(value, (int, float)):
            timestamp = float(value)
        else:
            try:
                timestamp = float(value)
            except ValueError:
                normalized = value.replace("Z", "+00:00")
                timestamp = datetime.fromisoformat(normalized).timestamp()
        return timestamp <= datetime.now(timezone.utc).timestamp()
    except (TypeError, ValueError, OverflowError):
        return False


def _seconds_until(value: str | float | int | None) -> float | None:
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            timestamp = float(value)
        else:
            try:
                timestamp = float(value)
            except ValueError:
                timestamp = datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        return max(0.0, timestamp - datetime.now(timezone.utc).timestamp())
    except (TypeError, ValueError, OverflowError):
        return None


def _find_first_key(value: Any, keys: set[str]) -> Any:
    for item in _values(value):
        for key in keys:
            if key in item:
                return item[key]
    return None


def _permission_subset(requested: Any, granted: Any) -> bool:
    """Conservatively prove that a grant is contained in a request."""
    if granted is None:
        return True
    if isinstance(requested, list):
        if not isinstance(granted, list):
            return False
        return all(item in requested for item in granted)
    if isinstance(requested, dict):
        if not isinstance(granted, dict):
            return False
        for key, value in granted.items():
            if key not in requested:
                return False
            requested_value = requested[key]
            if isinstance(value, dict) or isinstance(requested_value, dict):
                if not _permission_subset(requested_value, value):
                    return False
            elif isinstance(value, list) or isinstance(requested_value, list):
                if not _permission_subset(requested_value, value):
                    return False
            elif value is True and requested_value is not True:
                return False
            elif value != requested_value and value not in (False, None):
                return False
        return True
    return requested == granted


def _quota_event_recovered(value: Any) -> bool:
    """Recognize an explicit healthy rate-limit update conservatively."""
    saw_signal = False
    exhausted = False
    for item in _values(value):
        if "isRateLimited" in item:
            saw_signal = True
            exhausted = exhausted or bool(item["isRateLimited"])
        if "usedPercent" in item:
            saw_signal = True
            try:
                exhausted = exhausted or float(item["usedPercent"]) >= 100
            except (TypeError, ValueError):
                exhausted = True
        for key in ("limitReachedType", "rateLimitReachedType"):
            if key in item:
                saw_signal = True
                exhausted = exhausted or bool(item[key])
    return saw_signal and not exhausted


class Scheduler:
    """Coordinates durable task state and one turn per project."""

    def __init__(
        self,
        db: Database,
        events: EventBus,
        *,
        codex_command: list[str] | None = None,
    ) -> None:
        self.db = db
        self.events = events
        server_class = DesktopAppServer if os.environ.get("CODEX_TASKBOARD_DESKTOP_TRANSPORT") == "1" and not codex_command else CodexAppServer
        self.server = server_class(
            codex_command,
            request_handler=self._handle_server_request,
            notification_handler=self._handle_notification,
        )
        self._wake = asyncio.Event()
        self._stop = asyncio.Event()
        self._loop_task: asyncio.Task[None] | None = None
        self._execution_tasks: dict[str, asyncio.Task[None]] = {}
        self._task_turns: dict[str, str] = {}
        self._turn_tasks: dict[str, str] = {}
        self._turn_errors: dict[str, Any] = {}
        self._interaction_waiters: dict[str, asyncio.Future[Any]] = {}
        self._manual_overrides: set[str] = set()
        self._start_lock = asyncio.Lock()
        self._recovered = False
        self._activity_lock = asyncio.Lock()
        self._followup_lock = asyncio.Lock()
        self._starting_tasks: set[str] = set()
        self._history_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._last_sync = 0.0
        self.parallel = ParallelRuntime(self)
        self.planning = TaskPlanning(self)
        self._answer_lock = asyncio.Lock()

    @property
    def running(self) -> bool:
        return self._loop_task is not None and not self._loop_task.done()

    async def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        if isinstance(self.server, DesktopAppServer):
            for task in self.db.list_tasks():
                if task["threadId"]:
                    self.server.register_thread_task(task["threadId"], task["id"])
            await self._ensure_server()
        self._loop_task = asyncio.create_task(self._loop(), name="taskboard-scheduler")

    async def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._loop_task is not None:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
            self._loop_task = None
        for task in tuple(self._execution_tasks.values()):
            task.cancel()
        self._execution_tasks.clear()
        await self.parallel.stop()
        for waiter in tuple(self._interaction_waiters.values()):
            if not waiter.done():
                waiter.cancel()
        self._interaction_waiters.clear()
        self._manual_overrides.clear()
        await self.server.stop()

    def kick(self) -> None:
        self._wake.set()

    async def run_task(self, task_id: str, expected_version: int) -> dict[str, Any]:
        task = self.db.get_task(task_id)
        if task["version"] != expected_version:
            raise ConflictError("Task changed; refresh before running it")
        if task["status"] != "todo":
            raise ValidationError("Only todo tasks can be run")
        if not self.db.dependencies_ready(task_id):
            raise ValidationError("Task dependencies are not complete")
        return await self.parallel.queue(task)

    async def action(
        self,
        task_id: str,
        action: str,
        expected_version: int,
        feedback: str | None = None,
        *,
        target_status: str | None = None,
        attachment_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        task = self.db.get_task(task_id)
        if int(task["version"]) != expected_version:
            raise ConflictError("Task changed; refresh before applying the action")

        if action.startswith("plan_"):
            return await self.planning.action(task, action, feedback)
        if task["plan"]["hold"]:
            raise ValidationError("请先确认或取消任务计划")
        handled = await self.parallel.action(task, action, feedback)
        if handled is not None:
            return handled
        if task["kind"] == "parallel_group":
            raise ValidationError("请在任务组中选择具体子任务操作")
        if action == "interrupt_requeue" and task["parallel"].get("managed"):
            return await self.parallel.pause(task)
        if action == "run":
            return await self.run_task(task_id, expected_version)

        if action == "follow_up":
            return await self.follow_up(task_id, expected_version, feedback, attachment_ids=attachment_ids)

        if action == "interrupt_requeue":
            self._require_status(task, TaskStatus.IN_PROGRESS)
            self._manual_overrides.add(task_id)
            try:
                await self._cancel_pending_interactions(task_id)
                await self._interrupt(task)
                updated = self.db.update_task(
                    task_id,
                    self.db.get_task(task_id)["version"],
                    status=TaskStatus.TODO.value,
                    priority="draft",
                    run_state=None,
                    last_error=None,
                )
                await self._publish_task(updated)
                run = self.db.update_latest_run(task_id, run_state="interrupted")
                if run:
                    await self._publish_run(run, task["projectId"], task_id)
                self._cancel_execution(task_id)
                self.kick()
                return updated
            finally:
                self._manual_overrides.discard(task_id)

        if action in {"complete", "cancel"}:
            requested_target_status = target_status
            if action == "cancel" and requested_target_status is not None:
                raise ValidationError("targetStatus is only valid with complete")
            target_status = TaskStatus.DONE.value if action == "complete" else TaskStatus.CANCELED.value
            if action == "complete" and requested_target_status is not None:
                # The API uses targetStatus only to distinguish a manual
                # in_progress -> in_review override from completion.  Other
                # values are rejected rather than silently changing intent.
                if task["status"] == TaskStatus.IN_PROGRESS.value:
                    if requested_target_status not in {TaskStatus.IN_REVIEW.value, TaskStatus.DONE.value}:
                        raise ValidationError("Invalid targetStatus for a running task")
                    target_status = requested_target_status
                elif requested_target_status != TaskStatus.DONE.value:
                    raise ValidationError("targetStatus is only valid for an in_progress task")
            if action == "complete" and task["status"] not in {
                TaskStatus.TODO.value,
                TaskStatus.IN_REVIEW.value,
                TaskStatus.IN_PROGRESS.value,
            }:
                raise ValidationError("Only todo, in_review, or in_progress tasks can be completed")
            if action == "cancel" and task["status"] not in {
                TaskStatus.TODO.value,
                TaskStatus.IN_REVIEW.value,
                TaskStatus.IN_PROGRESS.value,
            }:
                raise ValidationError("Only todo, in_review, or in_progress tasks can be canceled")
            if task["status"] == TaskStatus.IN_PROGRESS.value:
                self._manual_overrides.add(task_id)
            try:
                if task["status"] == TaskStatus.IN_PROGRESS.value:
                    await self._cancel_pending_interactions(task_id)
                    await self._interrupt(task)
                updated = self.db.update_task(
                    task_id,
                    expected_version,
                    status=target_status,
                    run_state=None,
                    last_error=None,
                )
                await self._publish_task(updated)
                self._cancel_execution(task_id)
                self.kick()
                return updated
            finally:
                self._manual_overrides.discard(task_id)

        if action == "submit_review_feedback":
            self._require_status(task, TaskStatus.IN_REVIEW)
            if not feedback or not feedback.strip():
                raise ValidationError("Review feedback is required")
            if not task["threadId"]:
                raise ValidationError("This review has no Codex thread to resume")
            return await self.parallel.queue(task, REVIEW_FEEDBACK_TEMPLATE.format(feedback=feedback.strip()))

        if action == "retry":
            if task["status"] != TaskStatus.IN_PROGRESS.value or task["runState"] not in {RunState.FAILED.value, RunState.WAITING_QUOTA.value}:
                raise ValidationError("Only failed or quota-paused tasks can be retried")
            if not task["threadId"] and not task["parallel"].get("managed"):
                raise ValidationError("Retry requires the original Codex thread")
            return await self.parallel.queue(task, QUOTA_RESUME_PROMPT if task["runState"] == "waiting_quota" else FAILED_RETRY_PROMPT)

        raise ValidationError(f"Unknown task action {action!r}")

    async def follow_up(self, task_id: str, expected_version: int, text: str | None,
                        *, attachment_ids: list[str] | None = None) -> dict[str, Any]:
        async with self._followup_lock:
            task = self.db.get_task(task_id)
            if task["version"] != expected_version:
                raise ConflictError("Task changed; refresh before sending")
            if not text or not text.strip():
                raise ValidationError("跟进消息不能为空")
            if not task["threadId"] or task["status"] not in {"in_progress", "in_review", "done"}:
                raise ValidationError("当前任务没有可跟进的 Codex 会话")
            text = text.strip() + self.db.attachment_prompt(task_id, attachment_ids or [])
            await self._ensure_server()
            snapshot = await self.server.read_thread(task["threadId"], include_turns=True)
            turns = snapshot.get("thread", {}).get("turns", [])
            latest = turns[-1] if turns else {}
            active = latest.get("id") if _turn_status(latest) in {"inprogress", "in_progress", "running", "pending"} else None
            if not turns:
                active = self._task_turns.get(task_id)
            if active:
                await self.server.steer_turn(task["threadId"], active, text.strip(),
                                             skill=None)
                return self.db.get_task(task_id)
            if task_id in self._execution_tasks and not self._execution_tasks[task_id].done():
                raise ConflictError("Codex 正在启动或结束回合，请稍后发送")
            self.db.enqueue(task_id, expected_version, text.strip())
            claimed = self.db.claim_candidate(task_id, queued=True)
            if claimed is None:
                return await self.parallel.publish(task_id)
            if claimed["parallel"].get("validationRequested"):
                self._spawn_execution(task_id, text.strip())
                return await self.parallel.publish(task_id)
            self._starting_tasks.add(task_id)
            try:
                await self.server.resume_thread(task["threadId"])
                parent = self.db.get_task(task["parentId"]) if task["parentId"] else None
                model = task.get("model") or (parent["model"] if parent else None)
                effort = task.get("reasoningEffort") or (parent["reasoningEffort"] if parent and not task["model"] else None)
                options = await self.execution_options(model, effort)
                if self.db.get_task(task_id)["parallel"].get("paused") or (parent and self.db.get_task(parent["id"])["groupPhase"] != "submitted"):
                    raise ConflictError("任务已暂停，请继续后再发送跟进")
                self.server.register_thread_task(task["threadId"], task_id)
                turn_id = await self.parallel.execution_turn(task, task["threadId"], text.strip(), options, include_skill=False)
                if task["parallel"].get("managed"):
                    self.db.mark_downstream_stale(task_id)
                await self._adopt_turn(task_id, {"id": turn_id, "status": "inProgress"})
                self._spawn_existing_watch(task_id, turn_id)
                return self.db.get_task(task_id)
            except Exception:
                self.db.finish_queue(task_id)
                self.db.release_execution(task_id)
                current = self.db.get_task(task_id)
                self.db.update_task(task_id, current["version"], status="canceled" if current["status"] == "canceled" else task["status"], run_state=task["runState"], parallel={**task["parallel"], **{k: v for k, v in current["parallel"].items() if k in {"uncertainExecution", "nativeConflict", "waitReason", "paused"}}})
                raise
            finally:
                self._starting_tasks.discard(task_id)

    async def resolve_interaction(
        self,
        interaction_id: str,
        expected_version: int,
        response: Any,
        *,
        canceled: bool = False,
    ) -> dict[str, Any]:
        interaction = self.db.get_interaction(interaction_id)
        if interaction["kind"] == "async_user_input":
            return await self._resolve_async_question(interaction_id, expected_version, response, canceled=canceled)
        response = self._validate_interaction_response(interaction, response, canceled=canceled)
        if isinstance(self.server, DesktopAppServer):
            if interaction["status"] != "pending" or interaction["version"] != expected_version:
                raise ConflictError("该请求已在 Codex 或任务面板中处理，请刷新")
            request_id = interaction["payload"].get("requestId", interaction["requestId"])
            if request_id not in self.server._requests:
                raise ConflictError("该请求已结束或连接已重启，请在 Codex 中查看当前请求")
            await self.server.respond(request_id, result=response)
            # A native resolution notification may arrive during transport I/O.
            latest = self.db.get_interaction(interaction_id)
            if latest["status"] != "pending":
                return latest
        resolved = self.db.resolve_interaction(
            interaction_id,
            expected_version,
            response,
            canceled=canceled,
        )
        waiter = self._interaction_waiters.get(interaction_id)
        if waiter is not None and not waiter.done():
            waiter.set_result(response)
        task = self.db.get_task(interaction["taskId"])
        if not self.db.list_interactions(task["id"], pending_only=True) and task["status"] == TaskStatus.IN_PROGRESS.value and task["runState"] in {
            RunState.WAITING_APPROVAL.value,
            RunState.WAITING_INPUT.value,
        }:
            updated = await self._set_task(task["id"], run_state=RunState.RUNNING.value)
            if updated is not None:
                task = updated
        await self._publish_interaction(resolved)
        self.kick()
        return resolved

    async def _resolve_async_question(self, interaction_id, version, response, *, canceled=False):
        async with self._answer_lock:
            interaction = self.db.get_interaction(interaction_id)
            if interaction["status"] != "pending" or interaction["version"] != version:
                raise ConflictError("该问题已回答，请刷新")
            if not canceled:
                response = validate_answers(interaction, response)
                text = reply_text(interaction, response)
                op = self.parallel.auxiliary(interaction["payload"])
                if op and op["kind"] == "task_plan":
                    async with self.planning.lock:
                        op = self.db.operation(op["id"])
                        if op["state"] in {"confirmed", "canceled", "uncertain", "starting", "pending"}:
                            raise ConflictError("计划已结束或状态待核对，请刷新")
                        await self.planning.send(op, text)
                else:
                    task = self.db.get_task(interaction["taskId"])
                    if interaction["payload"].get("threadId") != task["threadId"]:
                        raise ValidationError("请在 Codex 中回答此辅助会话的问题")
                    await self.follow_up(task["id"], task["version"], text)
            latest = self.db.get_interaction(interaction_id)
            if latest["status"] != "pending":
                return latest
            resolved = self.db.resolve_interaction(interaction_id, version, response, canceled=canceled)
            await self._publish_interaction(resolved)
            return resolved

    async def _loop(self) -> None:
        if not self._recovered:
            await self._recover()
            self._recovered = True
        while not self._stop.is_set():
            self._wake.clear()
            if time.monotonic() - self._last_sync >= 5:
                self._last_sync = time.monotonic()
                await self._sync_threads()
                await self.parallel.recover()
                await self.planning.recover()
            await self._resume_quota_tasks()
            await self._dispatch_automated()
            timeout = self._next_wait_timeout()
            try:
                if timeout is None:
                    await self._wake.wait()
                else:
                    await asyncio.wait_for(self._wake.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                raise

    async def _dispatch_automated(self) -> None:
        if isinstance(self.server, DesktopAppServer) and not self.server.available():
            return
        await self.parallel.dispatch()

    async def _resume_quota_tasks(self) -> None:
        if isinstance(self.server, DesktopAppServer) and not self.server.available():
            return
        for task in self.db.waiting_quota_tasks():
            project = self.db.get_project(task["projectId"])
            if not project["quotaAutoResumeEnabled"]:
                continue
            run = self.db.latest_run(task["id"])
            reset_at = run["resumeAt"] if run else None
            if reset_at is None or not _reset_due(reset_at):
                continue
            if task["id"] in self._execution_tasks:
                continue
            if task["queued"] or task["parallel"].get("paused"):
                continue
            if task["parallel"].get("quotaRetryAt", 0) > time.time():
                continue
            try:
                await self.parallel.queue(task, QUOTA_RESUME_PROMPT)
            except Exception as exc:
                self.db.set_parallel(task["id"], quotaRetryAt=time.time() + 5, waitReason=str(exc))

    def _next_wait_timeout(self) -> float | None:
        if isinstance(self.server, DesktopAppServer) and not self.server.available():
            return 5.0
        waits: list[float] = [5.0]
        for task in self.db.waiting_quota_tasks():
            project = self.db.get_project(task["projectId"])
            if not project["quotaAutoResumeEnabled"]:
                continue
            run = self.db.latest_run(task["id"])
            if run:
                seconds = _seconds_until(run["resumeAt"])
                if seconds is not None:
                    waits.append(seconds)
        return max(0.1, min(waits))

    async def _recover(self) -> None:
        if isinstance(self.server, DesktopAppServer):
            # The desktop process outlives this sidecar. Its live snapshots
            # reconcile runs below; never resume, fail or cancel native turns
            # just because the renderer has not attached yet.
            for interaction in self.db.pending_interactions():
                if interaction["kind"] == "async_user_input":
                    continue
                self.db.resolve_interaction(interaction["id"], interaction["version"],
                                            {"reconnectInCodex": True}, canceled=True)
            return
        in_progress = self.db.in_progress_tasks()
        needs_server = bool(in_progress) and any(
            task["runState"] not in {RunState.WAITING_QUOTA.value} and task["threadId"]
            for task in in_progress
        )
        if needs_server:
            try:
                await self._ensure_server()
            except AppServerError as exc:
                for task in in_progress:
                    if task["runState"] == RunState.WAITING_QUOTA.value:
                        continue
                    await self._fail_task(task["id"], {"code": exc.code, "message": str(exc)})
        for task in in_progress:
            if task["runState"] == RunState.WAITING_QUOTA.value:
                continue
            if not task["threadId"] or not self.server.running:
                await self._fail_task(
                    task["id"],
                    {"code": "RECOVERY_UNKNOWN", "message": "The previous Codex turn could not be confirmed after restart."},
                )
                continue
            try:
                snapshot = await self.server.read_thread(task["threadId"], include_turns=True)
            except Exception as exc:
                await self._fail_task(
                    task["id"], {"code": "RECOVERY_READ_FAILED", "message": str(exc)}
                )
                continue
            turns = snapshot.get("thread", {}).get("turns", [])
            latest = turns[-1] if turns else snapshot
            status = _turn_status(latest)
            turn_id = _identifier(latest, "turnId", "id")
            self.server.register_thread_task(task["threadId"], task["id"])
            if status in {"completed", "complete", "succeeded"}:
                await self._settle_completed(task["id"], latest, _short_text(latest))
            elif status in {"in_progress", "inprogress", "running", "pending"} and turn_id:
                try:
                    # thread/read is observational only.  Resume is required
                    # before subscribing to a turn from a new process.
                    await self.server.resume_thread(task["threadId"])
                except Exception as exc:
                    await self._fail_task(
                        task["id"],
                        {"code": "RECOVERY_RESUME_FAILED", "message": str(exc)},
                    )
                    continue
                self._register_turn(task["id"], turn_id)
                await self._set_task(task["id"], run_state=RunState.RUNNING.value)
                self._spawn_existing_watch(task["id"], turn_id)
            else:
                await self._fail_task(
                    task["id"],
                    {"code": "RECOVERY_UNKNOWN", "message": "The previous Codex turn state could not be confirmed."},
                )
        # A pending interaction cannot safely be answered after a process
        # restart because its JSON-RPC request id belongs to the old process.
        for interaction in self.db.pending_interactions():
            if interaction["kind"] == "async_user_input":
                continue
            try:
                self.db.resolve_interaction(
                    interaction["id"], interaction["version"], {"decision": "cancel"}, canceled=True
                )
            except TaskboardError:
                continue
            await self._fail_task(
                interaction["taskId"],
                {"code": "RECOVERY_INTERACTION_LOST", "message": "A pending Codex interaction was interrupted by restart."},
            )

    async def _ensure_server(self) -> None:
        async with self._start_lock:
            if not self.server.running:
                await self.server.start()

    def _record_item(self, task_id: str, turn_id: str, item: dict[str, Any],
                     completed: bool, created_at: str | None = None, *, thread_id: str | None = None) -> None:
        kind = item.get("type", "")
        if kind == "reasoning" or not item.get("id"):
            return
        thread_id = thread_id or self.db.get_task(task_id)["threadId"]
        if thread_id and (completed or item.get("delivery") == "async"):
            record_questions(self.db, task_id, thread_id, turn_id, item)
        message = item.get("text") or item.get("command") or item.get("tool") or item.get("query") or kind
        if kind in {"userMessage", "steeringUserMessage"}:
            message = "\n".join(part.get("text", "") for part in item.get("content", []) if isinstance(part, dict) and part.get("type") == "text") or item.get("text") or "用户消息"
            message = reply_summary(message)
        if kind == "agentMessage" and item.get("delivery") == "async":
            message = "\n".join(q.get("title", "") for q in item.get("questions") or []) or item.get("text", "")
        self.db.save_activity(task_id, {
            "id": f"{turn_id}:{item['id']}", "kind": kind,
            "message": str(message) if kind != "agentMessage" or item.get("text") or item.get("delivery") == "async" else "正在回复…",
            "status": "completed" if completed else "running", "data": item,
            **({"createdAt": created_at} if created_at else {}),
        })

    async def hydrate_activity(self, task: dict[str, Any], *, force: bool = False) -> dict[str, Any] | None:
        """Refresh retained history, including native follow-ups and completed items."""
        if not task["threadId"]:
            return
        async with self._activity_lock:
            cached = self._history_cache.get(task["id"])
            if not force and cached and time.monotonic() - cached[0] < 1:
                return cached[1]
            await self._ensure_server()
            self.server.register_thread_task(task["threadId"], task["id"])
            snapshot = await self.server.read_thread(task["threadId"], include_turns=True)
            thread = snapshot.get("thread", {})
            for turn in thread.get("turns", []):
                created_at = task["createdAt"]
                if isinstance(turn.get("startedAt"), (int, float)):
                    created_at = datetime.fromtimestamp(turn["startedAt"], timezone.utc).isoformat(timespec="milliseconds")
                for item in turn.get("items", []):
                    self._record_item(task["id"], turn.get("id", ""), item,
                                      item.get("status") == "completed" or turn.get("status") != "inProgress", created_at)
            self._history_cache[task["id"]] = (time.monotonic(), snapshot)
            return snapshot

    async def _sync_threads(self) -> None:
        if isinstance(self.server, DesktopAppServer) and not self.server.available():
            return
        for task in self.db.list_tasks():
            if not task["threadId"]:
                continue
            self.server.register_thread_task(task["threadId"], task["id"])
            try:
                before = self.db.list_activity(task["id"])
                snapshot = await self.hydrate_activity(task, force=True)
                current = self.db.get_task(task["id"])
                if current["version"] != task["version"] or task["id"] in self._starting_tasks:
                    continue
                turns = (snapshot or {}).get("thread", {}).get("turns", [])
                if turns:
                    latest = turns[-1]
                    latest_status = _turn_status(latest)
                    run = self.db.latest_run(task["id"])
                    if latest.get("id") and (not run or run["turnId"] != latest["id"]):
                        await self._adopt_turn(task["id"], latest)
                    elif run and task["status"] == "in_progress" and (
                        task["runState"] not in {"waiting_quota", "failed"}
                        or task["parallel"].get("uncertainObservation")
                        or latest_status in {"completed", "inprogress", "in_progress", "running", "pending"}
                    ):
                        self._register_turn(task["id"], latest["id"])
                        if task["runState"] in {"failed", "waiting_quota"} and latest_status in {"inprogress", "in_progress", "running", "pending"}:
                            updated = await self._set_task(task["id"], run_state="running", last_error=None)
                            if updated:
                                await self._publish_task(updated)
                    if latest_status in {"completed", "failed", "interrupted"}:
                        if self._task_turns.get(task["id"]) == latest.get("id"):
                            await self._handle_turn_result(task["id"], {"turn": latest})
                if before != self.db.list_activity(task["id"]):
                    await self.events.publish("activity.updated", project_id=task["projectId"], task_id=task["id"])
            except Exception:
                # A disconnected desktop is retried; retained tasks are not relaunched.
                continue

    async def _adopt_turn(self, task_id: str, turn: dict[str, Any]) -> None:
        if task_id in self._manual_overrides:
            return
        task = self.db.get_task(task_id)
        turn_id = turn.get("id")
        if not turn_id or self._task_turns.get(task_id) == turn_id:
            return
        run = self.db.latest_run(task_id)
        if any(previous["turnId"] == turn_id for previous in self.db.list_runs(task_id)):
            return
        if turn.get("status") in {"inProgress", "in_progress", "running", "pending"}:
            with self.db.transaction(immediate=True):
                has_lease = self.db._conn.execute("SELECT 1 FROM execution_leases WHERE task_id=?", (task_id,)).fetchone()
                if not has_lease:
                    reason = self.db.eligibility(task, fairness=False)
                    root = self.db.get_task(task["parentId"]) if task["parentId"] else task
                    self.db._conn.execute("INSERT OR REPLACE INTO execution_leases VALUES(?,?,?,?)",
                                          (task_id, self.db.repo_info(task)[1], root["id"], root["schedulingMode"]))
                    if reason:
                        self.db.set_parallel(task_id, nativeConflict=True, waitReason=reason)
                if task["parallel"].get("managed") and task["parallel"].get("resultCommit"):
                    self.db.mark_downstream_stale(task_id)
                    self.db.set_parallel(task_id, mergeState="none", approvedCommit=None)
        self._register_turn(task_id, turn_id)
        if not run or run["turnId"] != turn_id:
            self.db.create_run(task_id=task_id, thread_id=task["threadId"], turn_id=turn_id, run_state="running")
        updated = await self._set_task(task_id, status="in_progress", run_state="running", last_error=None)
        if updated:
            await self._publish_task(updated)

    async def list_models(self) -> list[dict[str, Any]]:
        await self._ensure_server()
        return await self.server.list_models()

    async def execution_options(self, model: str | None, effort: str | None) -> dict[str, str]:
        if not model:
            if effort:
                raise ValidationError("请先选择模型，再选择推理强度")
            return {}
        available = await self.list_models()
        selected = next((item for item in available if item.get("model") == model), None)
        if selected is None:
            raise ValidationError("所选模型当前不可用，请重新选择")
        supported = {item["reasoningEffort"] for item in selected.get("supportedReasoningEfforts", [])}
        resolved = effort or selected.get("defaultReasoningEffort")
        if resolved and resolved not in supported:
            raise ValidationError("所选模型不支持该推理强度")
        return {"model": model, **({"effort": resolved} if resolved else {})}

    def _spawn_execution(self, task_id: str, prompt: str | None = None) -> None:
        current = self._execution_tasks.get(task_id)
        if current is not None and not current.done():
            return
        execution = asyncio.create_task(
            self._execute_task(task_id, prompt), name=f"taskboard-task-{task_id}"
        )
        self._execution_tasks[task_id] = execution
        execution.add_done_callback(lambda done, task_id=task_id: self._execution_finished(task_id, done))

    def _spawn_existing_watch(self, task_id: str, turn_id: str) -> None:
        current = self._execution_tasks.get(task_id)
        if current is not None and not current.done():
            return
        execution = asyncio.create_task(
            self._watch_turn(task_id, turn_id), name=f"taskboard-recover-{task_id}"
        )
        self._execution_tasks[task_id] = execution
        execution.add_done_callback(lambda done, task_id=task_id: self._execution_finished(task_id, done))

    def _execution_finished(self, task_id: str, task: asyncio.Task[None]) -> None:
        if self._execution_tasks.get(task_id) is task:
            self._execution_tasks.pop(task_id, None)
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception as exc:
            asyncio.create_task(
                self._fail_task(task_id, {"code": "SCHEDULER_ERROR", "message": str(exc)})
            )
        finally:
            self.kick()

    def _cancel_execution(self, task_id: str) -> None:
        execution = self._execution_tasks.get(task_id)
        if execution is not None and not execution.done():
            execution.cancel()
        self._execution_tasks.pop(task_id, None)
        turn_id = self._task_turns.pop(task_id, None)
        if turn_id:
            self._turn_tasks.pop(turn_id, None)

    async def _execute_task(self, task_id: str, continuation_prompt: str | None) -> None:
        task = self.db.get_task(task_id)
        if task["status"] != TaskStatus.IN_PROGRESS.value:
            return
        project = self.db.get_project(task["projectId"])
        self._starting_tasks.add(task_id)
        try:
            await self._ensure_server()
            task = await self.parallel.prepare_task(task_id)
            if task["parallel"].get("paused") or (task["parentId"] and self.db.get_task(task["parentId"])["groupPhase"] != "submitted"):
                return
            thread_id = task["threadId"]
            resuming = bool(thread_id)
            if not thread_id:
                if task.get("executionMode") == "worktree" and not task.get("worktreePath"):
                    worktree = await self.server.create_worktree(project["workspacePath"], task.get("branch"))
                    task = await self._set_task(task_id, worktree_path=worktree["worktreeWorkspaceRoot"],
                                                worktree_git_root=worktree["worktreeGitRoot"]) or self.db.get_task(task_id)
                if task["status"] != TaskStatus.IN_PROGRESS.value:
                    return
                thread_id = await self.parallel.execution_thread(task, task.get("worktreePath") or project["workspacePath"])
                self.server.register_thread_task(thread_id, task_id)
                task = await self._set_task(task_id, thread_id=thread_id) or self.db.get_task(task_id)
            else:
                self.server.register_thread_task(thread_id, task_id)
                if resuming:
                    await self.server.resume_thread(thread_id)
            if task.get("worktreeGitRoot"):
                await self.server.set_worktree_owner(task["worktreeGitRoot"], thread_id)
            task = self.db.get_task(task_id)
            if task["status"] != TaskStatus.IN_PROGRESS.value:
                return
            if continuation_prompt is None:
                prompt = INITIAL_TURN_TEMPLATE.format(
                    identifier=task["identifier"],
                    title=task["title"],
                    description=task["description"],
                )
            else:
                prompt = continuation_prompt
            question_reply = prompt.startswith(REPLY_OPEN)
            if task["parentId"]:
                parent = self.db.get_task(task["parentId"])
                if not question_reply:
                    prompt = (f"Parent task: {parent['title']}\nOverall requirements:\n{parent['description']}\n\n" + prompt)
                    if not resuming or continuation_prompt is None:
                        prompt += self.db.attachment_prompt(parent["id"])
                task = {**task, "model": task["model"] or parent["model"],
                        "reasoningEffort": task["reasoningEffort"] or (parent["reasoningEffort"] if not task["model"] else None)}
            if task["writeScopes"] and not question_reply:
                prompt += "\nDeclared modification scope:\n" + "\n".join(task["writeScopes"])
            if task["parallel"].get("validationBase") and not question_reply:
                prompt += f"\nDependency integration revision: {task['parallel']['validationBase']}"
            if continuation_prompt is None:
                prompt += self.db.attachment_prompt(task_id)
            await self._set_task(task_id, run_state=RunState.RUNNING.value)
            options = await self.execution_options(task.get("model"), task.get("reasoningEffort"))
            current = self.db.get_task(task_id)
            if current["parallel"].get("paused") or current["status"] != "in_progress" or (current["parentId"] and self.db.get_task(current["parentId"])["groupPhase"] != "submitted"):
                return
            turn_id = await self.parallel.execution_turn(task, thread_id, prompt, options, include_skill=not resuming or continuation_prompt is None)
            self._register_turn(task_id, turn_id)
            self._starting_tasks.discard(task_id)
            run = self.db.latest_run(task_id)
            if not run or run["turnId"] != turn_id:
                run = self.db.create_run(
                    task_id=task_id, thread_id=thread_id, turn_id=turn_id,
                    run_state=RunState.RUNNING.value,
                )
            await self._publish_run(run, task["projectId"], task_id)
            if not resuming:
                try:
                    await self.server.set_thread_name(thread_id, f"[Taskboard]{task['title']}")
                except Exception as exc:
                    self.db.save_activity(task_id, {"id": f"{turn_id}:title-warning", "kind": "warning",
                                                    "message": f"会话标题同步失败：{exc}"})
            completed = await self.server.wait_for_turn(turn_id)
            await self._handle_turn_result(task_id, completed)
        except asyncio.CancelledError:
            raise
        except UsageLimitExceeded as exc:
            await self._quota_wait(task_id, exc, None)
        except Exception as exc:
            await self._fail_task(task_id, {"code": getattr(exc, "code", "TURN_FAILED"), "message": str(exc)}, uncertain=bool(self._task_turns.get(task_id)))
        finally:
            self._starting_tasks.discard(task_id)

    async def _watch_turn(self, task_id: str, turn_id: str) -> None:
        try:
            result = await self.server.wait_for_turn(turn_id)
            await self._handle_turn_result(task_id, result)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self._task_turns.get(task_id) == turn_id:
                await self._fail_task(task_id, {"code": "RECOVERED_TURN_FAILED", "message": str(exc)}, uncertain=True)

    def _register_turn(self, task_id: str, turn_id: str) -> None:
        self._task_turns[task_id] = turn_id
        self._turn_tasks[turn_id] = task_id
        self.server.register_turn_task(turn_id, task_id)

    async def _handle_turn_result(self, task_id: str, result: dict[str, Any]) -> None:
        task = self.db.get_task(task_id)
        if task_id in self._manual_overrides:
            return
        if task["parallel"].get("paused") or (task["parentId"] and self.db.get_task(task["parentId"])["groupPhase"] in {"pausing", "paused"}):
            return
        if task["status"] != TaskStatus.IN_PROGRESS.value:
            return
        turn_id = _identifier(result, "turnId", "id")
        current_turn = self._task_turns.get(task_id)
        if current_turn and turn_id and current_turn != turn_id:
            return
        run = self.db.latest_run(task_id)
        if not current_turn and run and run["turnId"] == turn_id and run["runState"] in {"completed", "failed", "waiting_quota", "interrupted"}:
            return
        if _turn_status(result) in {"completed", "complete", "succeeded", "failed", "interrupted"}:
            if task["parallel"].get("nativeConflict") or task["parallel"].get("uncertainExecution"):
                self.db.set_parallel(task_id, nativeConflict=False, uncertainExecution=None, uncertainObservation=False, waitReason=None)
        for operation in self.db.operations(task_id, "execution"):
            if operation["payload"].get("turnId") == turn_id:
                self.db.save_operation(operation["id"], task_id, "execution", "completed", observedResult=_turn_status(result))
        prior_error = self._turn_errors.pop(turn_id, None) if turn_id else None
        status = _turn_status(result)
        error = _structured_error(result) or _structured_error(prior_error)
        info = usage_error_info(result) or usage_error_info(prior_error)
        summary = _short_text(result)
        if info is not None:
            await self._quota_wait(
                task_id,
                UsageLimitExceeded(
                    "Codex usage limit exceeded",
                    error=error or info,
                    resets_at=_find_first_key(
                        error or result,
                        {"resetsAt", "resetAt", "reset_at"},
                    ),
                ),
                summary,
            )
        elif status in {"completed", "complete", "succeeded", None} and not error:
            await self._settle_completed(task_id, result, summary)
        elif status == "interrupted":
            await self._fail_task(
                task_id,
                {"code": "TURN_INTERRUPTED", "message": "Codex turn was interrupted before completion."},
            )
        else:
            await self._fail_task(
                task_id,
                error
                or {"code": "TURN_FAILED", "message": "Codex turn failed without a structured error."},
            )
        if self._task_turns.get(task_id) == turn_id:
            self._task_turns.pop(task_id, None)
        if turn_id:
            self._turn_tasks.pop(turn_id, None)

    async def _settle_completed(
        self, task_id: str, result: Any, summary: str | None
    ) -> None:
        task = self.db.get_task(task_id)
        if task["status"] != TaskStatus.IN_PROGRESS.value:
            return
        if await self.parallel.completed(task_id, result, summary):
            return
        project = self.db.get_project(task["projectId"])
        status = TaskStatus.IN_REVIEW.value if project["reviewRequired"] else TaskStatus.DONE.value
        updated = await self._set_task(
            task_id,
            status=status,
            run_state=None,
            last_message=summary,
            last_error=None,
        )
        if updated is not None:
            await self._publish_task(updated)
            self.kick()
        run = self.db.update_latest_run(
            task_id,
            run_state="completed",
            last_output_summary=summary,
            structured_error=None,
        )
        if run:
            await self._publish_run(run, task["projectId"], task_id)

    async def _quota_wait(
        self,
        task_id: str,
        error: UsageLimitExceeded,
        summary: str | None,
    ) -> None:
        task = self.db.get_task(task_id)
        if task["status"] != TaskStatus.IN_PROGRESS.value:
            return
        resets_at = error.resets_at
        try:
            await self._ensure_server()
            limits = await self.server.read_rate_limits()
            if limits.get("resetsAt") is not None:
                resets_at = limits["resetsAt"]
            await self.events.publish(
                "quota.updated",
                project_id=task["projectId"],
                task_id=task_id,
                payload=limits,
            )
        except Exception:
            pass
        self.db.release_execution(task_id)
        self.db.finish_queue(task_id)
        structured = {
            "code": "UsageLimitExceeded",
            "message": str(error),
            "resetsAt": resets_at,
        }
        updated = await self._set_task(
            task_id,
            run_state=RunState.WAITING_QUOTA.value,
            last_message=summary,
            last_error=structured,
        )
        if updated is not None:
            await self._publish_task(updated)
        run = self.db.update_latest_run(
            task_id,
            run_state=RunState.WAITING_QUOTA.value,
            structured_error=structured,
            last_output_summary=summary,
            resume_at=str(resets_at) if resets_at is not None else None,
        )
        if run is None:
            run = self.db.create_run(
                task_id=task_id,
                thread_id=task["threadId"],
                turn_id=self._task_turns.get(task_id),
                run_state=RunState.WAITING_QUOTA.value,
                structured_error=structured,
                last_output_summary=summary,
                resume_at=str(resets_at) if resets_at is not None else None,
            )
        if run:
            await self._publish_run(run, task["projectId"], task_id)
        self.kick()

    async def _fail_task(self, task_id: str, error: Any, *, uncertain: bool = False) -> None:
        if task_id in self._manual_overrides:
            return
        try:
            task = self.db.get_task(task_id)
        except NotFoundError:
            return
        if task["status"] != TaskStatus.IN_PROGRESS.value:
            return
        code = error.get("code", "") if isinstance(error, dict) else ""
        uncertain = uncertain or code in {"CODEX_APP_SERVER_UNAVAILABLE", "SCHEDULER_ERROR", "TRANSPORT_ERROR", "RECOVERY_UNKNOWN", "RECOVERY_READ_FAILED", "RECOVERY_RESUME_FAILED"}
        if uncertain:
            self.db.set_parallel(task_id, nativeConflict=True, uncertainObservation=True, waitReason="回合状态待核对，连接中断不代表执行已停止")
        else:
            self.db.release_execution(task_id)
        self.db.finish_queue(task_id)
        updated = await self._set_task(
            task_id,
            run_state=RunState.FAILED.value,
            last_error=error,
        )
        if updated is not None:
            await self._publish_task(updated)
        run = self.db.update_latest_run(
            task_id,
            run_state=RunState.FAILED.value,
            structured_error=error,
        )
        if run is None:
            run = self.db.create_run(
                task_id=task_id,
                thread_id=task["threadId"],
                turn_id=self._task_turns.get(task_id),
                run_state=RunState.FAILED.value,
                structured_error=error,
            )
        if run:
            await self._publish_run(run, task["projectId"], task_id)
        self.kick()

    async def _interrupt(self, task: dict[str, Any]) -> None:
        if task["id"] in self._starting_tasks:
            raise ConflictError("Codex 正在启动回合，请稍后暂停")
        turn_id = self._task_turns.get(task["id"])
        if not turn_id and task["threadId"]:
            await self._ensure_server()
            snapshot = await self.server.read_thread(task["threadId"], include_turns=True)
            turns = snapshot.get("thread", {}).get("turns", [])
            latest = turns[-1] if turns else {}
            if _turn_status(latest) in {"inprogress", "in_progress", "running", "pending"}:
                turn_id = latest.get("id")
        if not turn_id or not task["threadId"]:
            return
        try:
            await self._ensure_server()
            await self.server.interrupt_turn(task["threadId"], turn_id)
        except Exception as exc:
            raise AppServerError(f"Unable to interrupt Codex turn: {exc}") from exc

    async def _cancel_pending_interactions(self, task_id: str) -> None:
        """Resolve old server requests with the narrowest safe cancellation."""
        for interaction in self.db.list_interactions(task_id, pending_only=True):
            response = self._validate_interaction_response(interaction, None, canceled=True)
            try:
                canceled = self.db.resolve_interaction(
                    interaction["id"], interaction["version"], response, canceled=True
                )
            except TaskboardError:
                continue
            waiter = self._interaction_waiters.get(interaction["id"])
            if waiter is not None and not waiter.done():
                waiter.set_result(response)
            await self._publish_interaction(canceled)

    async def _set_task(self, task_id: str, **changes: Any) -> dict[str, Any] | None:
        for _ in range(2):
            try:
                current = self.db.get_task(task_id)
                return self.db.update_task(task_id, current["version"], **changes)
            except ConflictError:
                continue
            except NotFoundError:
                return None
        return None

    @staticmethod
    def _require_status(task: dict[str, Any], status: TaskStatus) -> None:
        if task["status"] != status.value:
            raise ValidationError(f"Task must be {status.value} for this action")

    async def _handle_notification(self, method: str, params: dict[str, Any]) -> None:
        auxiliary = self.parallel.auxiliary(params)
        turn_id = _identifier(params, "turnId")
        if turn_id and method in {"error", "turn/error", "turn/failed"}:
            self._turn_errors[turn_id] = params
        task_id = self._event_task_id(params)
        project_id = None
        if task_id:
            try:
                project_id = self.db.get_task(task_id)["projectId"]
            except TaskboardError:
                task_id = None
        if method in {"account/rateLimits/updated", "account/rateLimits/changed"}:
            await self._refresh_quota_resets(params)
            await self.events.publish("quota.updated", payload=params)
            self.kick()
        elif method == "thread/status/changed":
            await self.events.publish(
                "run.status",
                project_id=project_id,
                task_id=task_id,
                payload=params,
            )
            self.kick()
        elif task_id:
            if method == "desktop/request":
                request_id = str(params["requestId"])
                if not any(item["requestId"] == request_id for item in self.db.list_interactions(task_id)):
                    kind = APPROVAL_METHODS.get(params["requestMethod"], "user_input")
                    interaction = self.db.create_interaction(task_id=task_id, kind=kind,
                                                             request_id=request_id, payload=params)
                    updated = None if auxiliary else await self._set_task(task_id, run_state="waiting_input" if kind == "user_input" else "waiting_approval")
                    if updated:
                        await self._publish_task(updated)
                    await self._publish_interaction(interaction)
            elif method == "turn/started" and not auxiliary:
                await self._adopt_turn(task_id, params.get("turn", {}))
            elif method == "turn/completed" and not auxiliary:
                await self._handle_turn_result(task_id, params)
            elif method == "serverRequest/resolved":
                for interaction in self.db.list_interactions(task_id, pending_only=True):
                    if str(interaction["requestId"]) == str(params.get("requestId")):
                        resolved = self.db.resolve_interaction(interaction["id"], interaction["version"], {"resolvedInCodex": True})
                        await self._publish_interaction(resolved)
                task = self.db.get_task(task_id)
                if not auxiliary and task_id not in self._manual_overrides and task["status"] == "in_progress" and task["runState"] in {"waiting_input", "waiting_approval"} and not self.db.list_interactions(task_id, pending_only=True):
                    updated = await self._set_task(task_id, run_state="running")
                    if updated:
                        await self._publish_task(updated)
            if method in {"item/started", "item/completed"}:
                self._record_item(task_id, turn_id or "", params.get("item", {}), method == "item/completed", thread_id=_identifier(params, "threadId"))
            await self.events.publish(
                "run.event",
                project_id=project_id,
                task_id=task_id,
                payload={"method": method, "params": params},
            )

    def _event_task_id(self, params: dict[str, Any]) -> str | None:
        thread_id = _identifier(params, "threadId")
        if thread_id:
            task_id = self.server.task_for_thread(thread_id)
            if task_id:
                return task_id
            task = next((task for task in self.db.list_tasks() if task["threadId"] == thread_id), None)
            if task:
                self.server.register_thread_task(thread_id, task["id"])
                return task["id"]
        turn_id = _identifier(params, "turnId")
        if turn_id:
            return self.server.task_for_turn(turn_id) or self._turn_tasks.get(turn_id)
        return None

    async def _refresh_quota_resets(self, payload: Any) -> None:
        reset_at = None
        for item in _values(payload):
            for key in ("resetsAt", "resetAt", "reset_at"):
                if item.get(key) is not None:
                    reset_at = item[key]
                    break
            if reset_at is not None:
                break
        if _quota_event_recovered(payload):
            reset_at = utc_now()
        if reset_at is None:
            return
        for operation in self.db.operations(kind="merge"):
            if operation["state"] == "waiting_quota":
                self.db.save_operation(operation["id"], operation["task_id"], "merge", "waiting_quota", resumeAt=str(reset_at))
        for task in self.db.waiting_quota_tasks():
            run = self.db.update_latest_run(task["id"], resume_at=str(reset_at))
            if run:
                await self._publish_run(run, task["projectId"], task["id"])

    @staticmethod
    def _validate_interaction_response(
        interaction: dict[str, Any], response: Any, *, canceled: bool
    ) -> Any:
        kind = interaction["kind"]
        if canceled:
            if kind == "user_input":
                return {"answers": {}}
            if kind in {"permission_request", "permissions"}:
                return {"permissions": {}, "scope": "turn"}
            return {"decision": "cancel"}
        if kind in {"command_approval", "file_approval"}:
            if not isinstance(response, dict) or response.get("decision") not in {
                "accept",
                "acceptForSession",
                "decline",
                "cancel",
            }:
                raise ValidationError("Approval response must contain a valid decision")
            return {"decision": response["decision"]}
        if kind in {"permission_request", "permissions"}:
            if not isinstance(response, dict):
                raise ValidationError("Permission response must be an object")
            scope = response.get("scope", "turn")
            if scope not in {"turn", "session"}:
                raise ValidationError("Permission scope must be turn or session")
            requested = _find_first_key(interaction["payload"], {"permissions", "requestedPermissions"})
            granted = response.get("permissions", {})
            if requested is None:
                if granted not in ({}, [], None):
                    raise ValidationError("Cannot grant permissions that were not requested")
            elif not _permission_subset(requested, granted):
                raise ValidationError("Permission response exceeds the requested permissions")
            return {"permissions": granted, "scope": scope}
        if kind == "async_user_input":
            return validate_answers(interaction, response)
        if kind == "user_input":
            if not isinstance(response, dict) or not isinstance(response.get("answers"), dict):
                raise ValidationError("User input response must contain answers")
            return validate_answers(interaction, response)
        raise ValidationError(f"Unsupported interaction kind {kind!r}")

    async def _handle_server_request(
        self, method: str, request_id: str | int, params: dict[str, Any]
    ) -> Any:
        kind = APPROVAL_METHODS.get(method)
        if kind is None and method == "item/tool/requestUserInput":
            kind = "user_input"
        if kind is None:
            task_id = self._event_task_id(params)
            if task_id:
                await self._fail_task(
                    task_id,
                    {"code": "UNSUPPORTED_SERVER_REQUEST", "message": f"Unsupported Codex request: {method}"},
                )
            raise AppServerError(f"Unsupported Codex server request: {method}")
        task_id = self._event_task_id(params)
        if task_id is None:
            raise AppServerError("Codex interaction did not identify a task")
        interaction = self.db.create_interaction(
            task_id=task_id,
            kind=kind,
            request_id=str(request_id),
            payload=params,
            blocking_scope="task",
        )
        waiter: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        # Register before any await so a fast UI response cannot race the
        # server-request bridge between interaction creation and publication.
        self._interaction_waiters[interaction["id"]] = waiter
        next_state = RunState.WAITING_INPUT.value if kind == "user_input" else RunState.WAITING_APPROVAL.value
        updated = None if self.parallel.auxiliary(params) else await self._set_task(task_id, run_state=next_state)
        if updated is not None:
            await self._publish_task(updated)
        await self._publish_interaction(interaction)
        try:
            return await waiter
        finally:
            self._interaction_waiters.pop(interaction["id"], None)

    async def _publish_task(self, task: dict[str, Any]) -> None:
        await self.events.publish(
            "task.updated",
            project_id=task["projectId"],
            task_id=task["id"],
            payload=task,
        )

    async def _publish_interaction(self, interaction: dict[str, Any]) -> None:
        await self.events.publish(
            "interaction.updated",
            task_id=interaction["taskId"],
            payload=interaction,
        )

    async def _publish_run(self, run: dict[str, Any], project_id: str, task_id: str) -> None:
        await self.events.publish(
            "run.updated",
            project_id=project_id,
            task_id=task_id,
            payload=run,
        )
