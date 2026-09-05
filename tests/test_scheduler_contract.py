from __future__ import annotations

import asyncio
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, Mock, patch

from codex_taskboard.constants import (
    FAILED_RETRY_PROMPT,
    INITIAL_TURN_TEMPLATE,
    QUOTA_RESUME_PROMPT,
    RunState,
    TaskStatus,
)
from codex_taskboard.db import Database
from codex_taskboard.desktop_server import DesktopAppServer
from codex_taskboard.errors import AppServerError, ConflictError, ValidationError
from codex_taskboard.events import EventBus
from codex_taskboard.scheduler import REVIEW_FEEDBACK_TEMPLATE, Scheduler, _reset_due


class FakeAppServer:
    """Deterministic in-memory App Server double; no real Codex process."""

    instances: list["FakeAppServer"] = []

    def __init__(self, command=None, *, request_handler=None, notification_handler=None):
        self.request_handler = request_handler
        self.notification_handler = notification_handler
        self.running = False
        self.thread_tasks: dict[str, str] = {}
        self.turn_tasks: dict[str, str] = {}
        self.start_thread_calls: list[str] = []
        self.resume_calls: list[str] = []
        self.turn_calls: list[tuple[str, str, str]] = []
        self.interrupt_calls: list[tuple[str, str]] = []
        self.read_snapshots: dict[str, object] = {}
        self.read_limits = {"resetsAt": "2000-01-01T00:00:00+00:00"}
        self._turn_number = 0
        type(self).instances.append(self)

    async def start(self):
        self.running = True

    async def stop(self):
        self.running = False

    def task_for_thread(self, thread_id: str):
        return self.thread_tasks.get(thread_id)

    def task_for_turn(self, turn_id: str):
        return self.turn_tasks.get(turn_id)

    def register_thread_task(self, thread_id: str, task_id: str):
        self.thread_tasks[thread_id] = task_id

    def register_turn_task(self, turn_id: str, task_id: str):
        self.turn_tasks[turn_id] = task_id

    async def start_thread(self, workspace_path: str):
        self.start_thread_calls.append(workspace_path)
        return f"thread-new-{len(self.start_thread_calls)}"

    async def resume_thread(self, thread_id: str):
        self.resume_calls.append(thread_id)
        return {}

    async def set_thread_name(self, thread_id: str, name: str):
        return {}

    async def read_thread(self, thread_id: str, *, include_turns: bool = True):
        return self.read_snapshots.get(thread_id, {})

    async def start_turn(self, thread_id: str, prompt: str, *, task_id: str):
        self._turn_number += 1
        turn_id = f"turn-{self._turn_number}"
        self.turn_calls.append((thread_id, prompt, task_id))
        self.register_turn_task(turn_id, task_id)
        return turn_id

    async def wait_for_turn(self, turn_id: str, *, timeout=None):
        return {"turnId": turn_id, "status": "completed", "output": "continued"}

    async def interrupt_turn(self, thread_id: str, turn_id: str):
        self.interrupt_calls.append((thread_id, turn_id))
        return {}

    async def read_rate_limits(self):
        return self.read_limits


class SchedulerContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_desktop_approval_mirrors_both_sides_without_automatic_response(self):
        task = self.running_task(self.project())
        native = DesktopAppServer(notification_handler=self.scheduler._handle_notification)
        native.transport = Mock(return_value={})
        self.scheduler.server = native
        await native.start()
        native.register_thread_task("thread-1", task["id"])
        for request_id in (42, 43):
            native._receive({"type": "mcp-request", "hostId": "local", "request": {
                "id": request_id, "method": "item/commandExecution/requestApproval",
                "params": {"threadId": "thread-1", "turnId": "turn-1", "command": "pwd"},
            }})
            await asyncio.sleep(0)
            interaction = self.db.list_interactions(task["id"], pending_only=True)[0]
            self.assertEqual(self.db.get_task(task["id"])["runState"], "waiting_approval")
            if request_id == 42:
                native.transport.assert_not_called()
                await self.scheduler.resolve_interaction(interaction["id"], interaction["version"], {"decision": "accept"})
                native.transport.assert_called_once_with(None, {"id": 42, "result": {"decision": "accept"}}, 20)
            else:
                native._receive({"type": "mcp-notification", "hostId": "local", "method": "serverRequest/resolved", "params": {"requestId": 43}})
                await asyncio.sleep(0)
                self.assertFalse(self.db.list_interactions(task["id"], pending_only=True))
                self.assertEqual(native.transport.call_count, 1)
                with self.assertRaises(ConflictError):
                    await self.scheduler.resolve_interaction(interaction["id"], interaction["version"], {"decision": "accept"})

    async def test_native_reconnect_reconciles_completed_turn_after_local_error(self):
        task = self.running_task(self.project(review_required=False), state="failed")
        self.server.read_snapshots["thread-1"] = {"thread": {"turns": [{"id": "turn-1", "status": "completed", "items": []}]}}
        await self.scheduler._sync_threads()
        self.assertEqual(self.db.get_task(task["id"])["status"], "done")
        self.assertEqual(self.server.turn_calls, [])

    async def test_native_followup_and_completed_history_reconcile_without_relaunch(self):
        task = self.running_task(self.project(review_required=False))
        self.server.read_snapshots["thread-1"] = {"thread": {"turns": [
            {"id": "turn-1", "status": "completed", "items": []},
            {"id": "native-turn", "status": "inProgress", "items": [
                {"id": "user", "type": "userMessage", "content": [{"type": "text", "text": "请补充细节"}]},
                {"id": "tool", "type": "commandExecution", "command": "pwd", "status": "inProgress"},
            ]},
        ]}}
        await self.scheduler._sync_threads()
        self.assertEqual(self.scheduler._task_turns[task["id"]], "native-turn")
        self.assertEqual(self.db.list_activity(task["id"])[0]["message"], "请补充细节")
        latest = self.server.read_snapshots["thread-1"]["thread"]["turns"][-1]
        latest["status"] = "completed"
        latest["items"][1].update(status="completed", aggregatedOutput="/tmp/project")
        await self.scheduler._sync_threads()
        self.assertEqual(self.db.get_task(task["id"])["status"], "done")
        self.assertEqual(self.db.list_activity(task["id"])[1]["data"]["aggregatedOutput"], "/tmp/project")
        self.assertEqual(self.server.turn_calls, [])
        self.assertEqual(self.server.resume_calls, [])

    async def test_pause_is_draft_and_late_completion_does_not_reclaim(self):
        project = self.project(automation_enabled=True)
        task = self.running_task(project)
        self.scheduler._register_turn(task["id"], "turn-1")
        paused = await self.scheduler.action(task["id"], "interrupt_requeue", task["version"])
        self.assertEqual((paused["status"], paused["priority"], paused["ready"]), ("todo", "draft", False))
        await self.scheduler._handle_notification("turn/completed", {
            "threadId": "thread-1", "turn": {"id": "turn-1", "status": "interrupted"},
        })
        await self.scheduler._dispatch_automated()
        self.assertEqual(self.db.get_task(task["id"])["status"], "todo")
        self.assertEqual(self.server.turn_calls, [])
        self.assertEqual(self.db.latest_run(task["id"])["runState"], "interrupted")

    async def test_followup_steers_active_turn_and_starts_same_thread_when_done(self):
        task = self.running_task(self.project(review_required=False))
        self.scheduler._register_turn(task["id"], "turn-1")
        self.server.steer_turn = AsyncMock(return_value={"turnId": "turn-1"})
        await self.scheduler.action(task["id"], "follow_up", task["version"], "补充")
        self.server.steer_turn.assert_awaited_once_with("thread-1", "turn-1", "补充")
        self.assertEqual(self.server.turn_calls, [])
        await self.scheduler._handle_turn_result(task["id"], {"turnId": "turn-1", "status": "completed"})
        task = self.db.get_task(task["id"])
        self.server.start_turn = AsyncMock(return_value="followup-turn")
        with patch.object(self.scheduler, "_spawn_existing_watch") as watch:
            continued = await self.scheduler.action(task["id"], "follow_up", task["version"], "再优化")
            watch.assert_called_once_with(task["id"], "followup-turn")
        self.assertEqual(continued["status"], "in_progress")
        self.server.start_turn.assert_awaited_once_with("thread-1", "再优化", task_id=task["id"])
        await self.scheduler._handle_turn_result(task["id"], {"turnId": "turn-1", "status": "completed"})
        self.assertEqual(self.db.get_task(task["id"])["status"], "in_progress")
        self.assertEqual(self.scheduler._task_turns[task["id"]], "followup-turn")

    async def test_failed_followup_does_not_change_completed_task(self):
        task = self.running_task(self.project(review_required=False))
        await self.scheduler._handle_turn_result(task["id"], {"turnId": "turn-1", "status": "completed"})
        task = self.db.get_task(task["id"])
        self.server.start_turn = AsyncMock(side_effect=AppServerError("disconnected"))
        with self.assertRaises(AppServerError):
            await self.scheduler.action(task["id"], "follow_up", task["version"], "保留草稿")
        self.assertEqual(self.db.get_task(task["id"])["status"], "done")

    async def test_recovery_reads_latest_turn_not_first_completed_turn(self):
        task = self.running_task(self.project())
        self.server.read_snapshots["thread-1"] = {"thread": {"id": "thread-1", "turns": [
            {"id": "turn-1", "status": "completed"},
            {"id": "turn-2", "status": "inProgress"},
        ]}}
        with patch.object(self.scheduler, "_spawn_existing_watch") as watch:
            await self.scheduler._recover()
            watch.assert_called_once_with(task["id"], "turn-2")
        self.assertEqual(self.db.get_task(task["id"])["status"], "in_progress")

    async def test_selected_execution_options_reach_initial_and_resumed_turn(self) -> None:
        project = self.project()
        self.server.list_models = AsyncMock(return_value=[{
            "model": "test-model", "defaultReasoningEffort": "low",
            "supportedReasoningEfforts": [{"reasoningEffort": "low"}, {"reasoningEffort": "high"}],
        }])
        self.server.start_turn = AsyncMock(return_value="turn-selected")
        for existing_thread in (None, "thread-existing"):
            task = self.db.create_task(project_id=project["id"], title="Configured task", model="test-model", reasoning_effort="high")
            task = self.db.claim_task(task["id"], task["version"])
            if existing_thread:
                task = self.db.update_task(task["id"], task["version"], thread_id=existing_thread)
            await self.scheduler._execute_task(task["id"], None)
            self.assertEqual(self.server.start_turn.call_args.kwargs, {
                "task_id": task["id"], "model": "test-model", "effort": "high",
            })
            self.assertEqual(self.db.get_task(task["id"])["status"], "in_review")

    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.db = Database(f"{self.temp_dir.name}/taskboard.sqlite3")
        self.events = EventBus()
        FakeAppServer.instances.clear()
        self.app_server_patch = patch("codex_taskboard.scheduler.CodexAppServer", FakeAppServer)
        self.app_server_patch.start()
        self.scheduler = Scheduler(self.db, self.events)
        self.server = FakeAppServer.instances[-1]

    async def asyncTearDown(self) -> None:
        await self.scheduler.stop()
        self.db.close()
        self.temp_dir.cleanup()
        self.app_server_patch.stop()

    def project(self, key: str = "APP", **kwargs):
        return self.db.create_project(
            key=key,
            name=kwargs.pop("name", key),
            workspace_path=self.temp_dir.name,
            **kwargs,
        )

    def task(self, project_id: str, title: str = "task"):
        return self.db.create_task(project_id=project_id, title=title)

    def running_task(self, project: dict, *, title: str = "task", thread_id: str = "thread-1", state: str = RunState.RUNNING.value):
        task = self.task(project["id"], title)
        task = self.db.claim_task(task["id"], task["version"])
        task = self.db.update_task(task["id"], task["version"], thread_id=thread_id, run_state=state)
        self.db.create_run(
            task_id=task["id"],
            thread_id=thread_id,
            turn_id="turn-1",
            run_state=state,
            resume_at="2000-01-01T00:00:00+00:00" if state == RunState.WAITING_QUOTA.value else None,
        )
        self.server.register_thread_task(thread_id, task["id"])
        return self.db.get_task(task["id"])

    async def test_activity_keeps_feedback_and_tools_without_duplicate_completion(self):
        task = self.running_task(self.project())
        for method, item in [
            ("item/started", {"id": "tool-1", "type": "mcpToolCall", "tool": "imagegen", "arguments": {"prompt": "draw a tree"}}),
            ("item/completed", {"id": "tool-1", "type": "mcpToolCall", "tool": "imagegen", "result": {"text": "done"}}),
            ("item/completed", {"id": "reply-1", "type": "agentMessage", "text": "图片已生成", "phase": "commentary"}),
            ("item/completed", {"id": "private-1", "type": "reasoning", "text": "private"}),
        ]:
            await self.scheduler._handle_notification(method, {"threadId": "thread-1", "turnId": "turn-1", "item": item})
        rows = self.db.list_activity(task["id"])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["status"], "completed")
        self.assertEqual(rows[0]["data"]["arguments"], {"prompt": "draw a tree"})
        self.assertEqual(rows[1]["message"], "图片已生成")
        reopened = Database(self.db.path)
        try:
            self.assertEqual(reopened.list_activity(task["id"]), rows)
        finally:
            reopened.close()

    async def test_history_read_restores_items_without_executing_turn(self):
        task = self.running_task(self.project())
        self.server.read_snapshots["thread-1"] = {"thread": {"turns": [{"id": "old-turn", "status": "completed", "items": [
            {"id": "old-feedback", "type": "agentMessage", "text": "之前的进展"},
            {"id": "old-tool", "type": "commandExecution", "command": "pwd"},
        ]}]}}
        await self.scheduler.hydrate_activity(task)
        await self.scheduler.hydrate_activity(task)
        self.assertEqual(len(self.db.list_activity(task["id"])), 2)
        self.assertEqual(self.server.turn_calls, [])
        self.assertEqual(self.server.resume_calls, [])

    async def test_completion_review_policy_and_independent_dispatch(self) -> None:
        review_project = self.project("REVIEW", review_required=True)
        task = self.running_task(review_project, title="needs review")
        await self.scheduler._handle_turn_result(
            task["id"], {"turnId": "turn-1", "status": "completed", "output": "done"}
        )
        settled = self.db.get_task(task["id"])
        self.assertEqual(settled["status"], TaskStatus.IN_REVIEW.value)
        self.assertIsNone(settled["runState"])

        dependent = self.task(review_project["id"], "dependent")
        self.db.replace_dependencies(dependent["id"], dependent["version"], [task["id"]])
        independent = self.task(review_project["id"], "independent")
        claimed = self.db.claim_next_ready(review_project["id"])
        self.assertEqual(claimed["id"], independent["id"])
        self.assertEqual(self.db.get_task(dependent["id"])["status"], TaskStatus.TODO.value)

        auto_project = self.project("AUTO", review_required=False)
        auto_task = self.running_task(auto_project, title="no review", thread_id="thread-2")
        await self.scheduler._handle_turn_result(
            auto_task["id"], {"turnId": "turn-1", "status": "completed", "output": "done"}
        )
        self.assertEqual(self.db.get_task(auto_task["id"])["status"], TaskStatus.DONE.value)

    async def test_quota_wire_error_and_switch_control_auto_resume_and_retry(self) -> None:
        project = self.project("QUOTA", review_required=False, quota_auto_resume_enabled=True)
        task = self.running_task(project, thread_id="thread-keep")
        await self.scheduler._handle_turn_result(
            task["id"],
            {
                "turnId": "turn-1",
                "status": "failed",
                "error": {"codexErrorInfo": "usageLimitExceeded", "message": "quota"},
            },
        )
        waiting = self.db.get_task(task["id"])
        self.assertEqual(waiting["runState"], RunState.WAITING_QUOTA.value)
        self.assertEqual(waiting["threadId"], "thread-keep")
        self.assertEqual(waiting["lastError"]["code"], "UsageLimitExceeded")

        spawn = Mock()
        with patch.object(self.scheduler, "_spawn_execution", spawn):
            await self.scheduler._resume_quota_tasks()
        self.assertEqual(spawn.call_count, 1)
        self.assertEqual(spawn.call_args.args[0], task["id"])
        self.assertEqual(spawn.call_args.args[1], QUOTA_RESUME_PROMPT)

        disabled_project = self.project("NOAUTO", quota_auto_resume_enabled=False)
        disabled = self.running_task(disabled_project, thread_id="thread-disabled", state=RunState.WAITING_QUOTA.value)
        spawn.reset_mock()
        with patch.object(self.scheduler, "_spawn_execution", spawn):
            await self.scheduler._resume_quota_tasks()
        self.assertFalse(spawn.called)
        self.assertEqual(self.db.get_task(disabled["id"])["runState"], RunState.WAITING_QUOTA.value)

        # Manual retry remains available with the feature disabled and still
        # uses the durable thread id.
        retry_task = self.db.get_task(disabled["id"])
        with patch.object(self.scheduler, "_spawn_execution", spawn):
            retried = await self.scheduler.action(
                retry_task["id"], "retry", retry_task["version"]
            )
        self.assertEqual(retried["runState"], RunState.STARTING.value)
        self.assertEqual(spawn.call_args.args, (disabled["id"], QUOTA_RESUME_PROMPT))

        # The App Server may encode an epoch reset as a numeric string.  It
        # must be treated as due just like a numeric JSON value.
        self.assertTrue(_reset_due("0"))

        # If the rate-limit read is unavailable, preserve the structured
        # reset supplied by the turn error instead of replacing it with null.
        fallback_project = self.project("FALLBACK", review_required=False)
        fallback = self.running_task(fallback_project, thread_id="thread-fallback")
        with patch.object(self.server, "read_rate_limits", side_effect=RuntimeError("offline")):
            await self.scheduler._handle_turn_result(
                fallback["id"],
                {
                    "turnId": "turn-1",
                    "status": "failed",
                    "error": {
                        "codexErrorInfo": "usageLimitExceeded",
                        "resetsAt": "0",
                        "message": "quota",
                    },
                },
            )
        self.assertEqual(self.db.get_task(fallback["id"])["lastError"]["resetsAt"], "0")
        self.assertEqual(self.db.latest_run(fallback["id"])["resumeAt"], "0")

    async def test_quota_resume_calls_resume_on_same_thread(self) -> None:
        project = self.project("RESUME", review_required=False)
        task = self.running_task(project, thread_id="thread-existing", state=RunState.WAITING_QUOTA.value)
        await self.scheduler._execute_task(task["id"], QUOTA_RESUME_PROMPT)
        self.assertEqual(self.server.resume_calls, ["thread-existing"])
        self.assertEqual(self.server.start_thread_calls, [])
        self.assertEqual(self.server.turn_calls[0][0], "thread-existing")
        self.assertEqual(self.server.turn_calls[0][1], QUOTA_RESUME_PROMPT)
        self.assertEqual(self.db.get_task(task["id"])["status"], TaskStatus.DONE.value)

    async def test_attachment_paths_reach_codex_turn(self):
        project = self.project("FILES")
        task = self.db.create_task(project_id=project["id"], title="with attachment", attachments=[
            {"name": "notes.md", "content": "IyBIZWxsbw=="}])
        self.db.claim_task(task["id"], task["version"])
        await self.scheduler._execute_task(task["id"], None)
        self.assertIn(self.db.attachment_prompt(task["id"]), self.server.turn_calls[0][1])
        self.assertIn("notes.md", self.server.turn_calls[0][1])

    async def test_initial_and_failed_retry_prompts_are_exact_and_keep_thread(self) -> None:
        project = self.project("PROMPT", review_required=False)
        initial = self.task(project["id"], "initial")
        initial = self.db.claim_task(initial["id"], initial["version"])
        with patch.object(self.server, "set_thread_name", new_callable=AsyncMock) as rename:
            await self.scheduler._execute_task(initial["id"], None)
            rename.assert_awaited_once_with("thread-new-1", "[Taskboard]initial")
        self.assertEqual(
            self.server.turn_calls[0],
            (
                "thread-new-1",
                INITIAL_TURN_TEMPLATE.format(
                    identifier=initial["identifier"],
                    title=initial["title"],
                    description=initial["description"],
                ),
                initial["id"],
            ),
        )
        self.assertIn("完成任务前，必须提交本次任务产生的改动。", self.server.turn_calls[0][1])

        failed = self.running_task(project, title="failed retry", thread_id="thread-retry", state=RunState.FAILED.value)
        spawn = Mock()
        with patch.object(self.scheduler, "_spawn_execution", spawn):
            retried = await self.scheduler.action(failed["id"], "retry", failed["version"])
        self.assertEqual(retried["threadId"], "thread-retry")
        self.assertEqual(spawn.call_args.args, (failed["id"], FAILED_RETRY_PROMPT))

    async def test_failures_approval_user_input_and_unknown_requests_pause_safely(self) -> None:
        project = self.project("INTERACT", review_required=False)
        failed = self.running_task(project, title="failed", thread_id="thread-failed")
        await self.scheduler._handle_turn_result(
            failed["id"],
            {"turnId": "turn-1", "status": "failed", "error": {"code": "E_FAIL", "message": "no"}},
        )
        self.assertEqual(self.db.get_task(failed["id"])["runState"], RunState.FAILED.value)

        for index, method, response in (
            (1, "item/commandExecution/requestApproval", {"decision": "accept"}),
            (2, "item/fileChange/requestApproval", {"decision": "decline"}),
            (3, "item/permissions/requestApproval", {"permissions": {"fileSystem": {"read": True}}, "scope": "turn"}),
            (4, "item/tool/requestUserInput", {"answers": {"question-1": {"answers": ["yes"]}}}),
        ):
            interaction_project = self.project(f"INT{index}", review_required=False)
            task = self.running_task(interaction_project, title=f"interaction-{index}", thread_id=f"thread-{index}")
            if method.endswith("requestUserInput"):
                payload = {"threadId": f"thread-{index}", "questions": [{"id": "question-1", "question": "Continue?"}]}
            elif method.endswith("permissions/requestApproval"):
                payload = {"threadId": f"thread-{index}", "permissions": {"fileSystem": {"read": True}}}
            else:
                payload = {"threadId": f"thread-{index}"}
            request_task = asyncio.create_task(
                self.scheduler._handle_server_request(method, f"rpc-{index}", payload)
            )
            interaction = None
            for _ in range(100):
                pending = self.db.pending_interactions()
                interaction = next((item for item in pending if item["taskId"] == task["id"]), None)
                if interaction is not None:
                    break
                await asyncio.sleep(0.001)
            self.assertIsNotNone(interaction)
            self.assertEqual(
                self.db.get_task(task["id"])["runState"],
                RunState.WAITING_INPUT.value if method.endswith("requestUserInput") else RunState.WAITING_APPROVAL.value,
            )
            if method.endswith("permissions/requestApproval"):
                with self.assertRaises(ValidationError):
                    await self.scheduler.resolve_interaction(
                        interaction["id"],
                        interaction["version"],
                        {"permissions": {"fileSystem": {"write": True}}, "scope": "turn"},
                    )
            resolved = await self.scheduler.resolve_interaction(
                interaction["id"], interaction["version"], response
            )
            self.assertEqual(resolved["status"], "resolved")
            self.assertEqual(await request_task, response)
            self.assertEqual(self.db.get_task(task["id"])["runState"], RunState.RUNNING.value)

        unknown_project = self.project("UNKNOWN", review_required=False)
        unknown = self.running_task(unknown_project, title="unknown", thread_id="thread-unknown")
        with self.assertRaises(AppServerError):
            await self.scheduler._handle_server_request(
                "server/unsupported", "rpc-unknown", {"threadId": "thread-unknown"}
            )
        self.assertEqual(self.db.get_task(unknown["id"])["runState"], RunState.FAILED.value)

    async def test_recovery_handles_completed_running_quota_and_unknown_without_relaunch(self) -> None:
        completed = self.running_task(
            self.project("REC1", review_required=False),
            title="completed",
            thread_id="thread-completed",
        )
        running = self.running_task(
            self.project("REC2", review_required=False),
            title="running",
            thread_id="thread-running",
        )
        quota = self.running_task(
            self.project("REC3", review_required=False),
            title="quota",
            thread_id="thread-quota",
            state=RunState.WAITING_QUOTA.value,
        )
        unknown = self.running_task(
            self.project("REC4", review_required=False),
            title="unknown",
            thread_id="thread-unknown",
        )
        # Recovery is intentionally a read/subscribe operation: it never
        # starts a fresh turn for a task whose prior state is unknown.
        self.server.read_snapshots = {
            "thread-completed": {"turnId": "turn-completed", "status": "completed", "output": "finished"},
            "thread-running": {"turnId": "turn-running", "status": "in_progress"},
            "thread-unknown": {"threadId": "thread-unknown"},
        }
        watch = Mock()
        with patch.object(self.scheduler, "_spawn_existing_watch", watch):
            await self.scheduler._recover()
        self.assertEqual(self.db.get_task(completed["id"])["status"], TaskStatus.DONE.value)
        self.assertEqual(self.db.get_task(running["id"])["runState"], RunState.RUNNING.value)
        self.assertEqual(self.db.get_task(quota["id"])["runState"], RunState.WAITING_QUOTA.value)
        self.assertEqual(self.db.get_task(unknown["id"])["runState"], RunState.FAILED.value)
        watch.assert_called_once_with(running["id"], "turn-running")

    async def test_dnd_actions_enforce_state_and_version_contract(self) -> None:
        project = self.project("DND")
        blocked = self.task(project["id"], "blocked")
        blocker = self.task(project["id"], "blocker")
        blocked = self.db.replace_dependencies(blocked["id"], blocked["version"], [blocker["id"]])
        with self.assertRaises(ValidationError):
            await self.scheduler.action(blocked["id"], "run", blocked["version"])

        todo = self.task(project["id"], "todo")
        spawn = Mock()
        with patch.object(self.scheduler, "_spawn_execution", spawn):
            started = await self.scheduler.action(todo["id"], "run", todo["version"])
        self.assertEqual(started["status"], TaskStatus.IN_PROGRESS.value)

        # Release the project slot for the remaining transition checks.
        started = self.db.update_task(
            started["id"], started["version"], status=TaskStatus.DONE.value, run_state=None
        )

        with self.assertRaises(ConflictError):
            await self.scheduler.action(todo["id"], "run", todo["version"])

        live = self.running_task(project, title="live", thread_id="thread-live")
        self.scheduler._register_turn(live["id"], "turn-live")
        requeued = await self.scheduler.action(
            live["id"], "interrupt_requeue", live["version"]
        )
        self.assertEqual(requeued["status"], TaskStatus.TODO.value)
        self.assertEqual(self.server.interrupt_calls[-1], ("thread-live", "turn-live"))

        review_override = self.running_task(project, title="override", thread_id="thread-override")
        self.scheduler._register_turn(review_override["id"], "turn-override")
        reviewed = await self.scheduler.action(
            review_override["id"], "complete", review_override["version"], target_status="in_review"
        )
        self.assertEqual(reviewed["status"], TaskStatus.IN_REVIEW.value)

        done = self.db.get_task(review_override["id"])
        with self.assertRaises(ValidationError):
            await self.scheduler.action(done["id"], "run", done["version"])

        feedback_task = self.running_task(project, title="feedback", thread_id="thread-feedback")
        feedback_task = self.db.update_task(
            feedback_task["id"], feedback_task["version"], status=TaskStatus.IN_REVIEW.value, run_state=None
        )
        with patch.object(self.scheduler, "_spawn_execution", spawn):
            continued = await self.scheduler.action(
                feedback_task["id"],
                "submit_review_feedback",
                feedback_task["version"],
                "Please add a regression test.",
            )
        self.assertEqual(continued["status"], TaskStatus.IN_PROGRESS.value)
        self.assertEqual(spawn.call_args.args, (feedback_task["id"], REVIEW_FEEDBACK_TEMPLATE.format(feedback="Please add a regression test.")))

    async def test_manual_todo_completion_overrides_blocking_and_cancel_is_terminal(self) -> None:
        project = self.project("OVERRIDE")
        blocker = self.task(project["id"], "blocker")
        target = self.task(project["id"], "blocked target")
        target = self.db.replace_dependencies(target["id"], target["version"], [blocker["id"]])

        completed = await self.scheduler.action(target["id"], "complete", target["version"])
        self.assertEqual(completed["status"], TaskStatus.DONE.value)

        with self.assertRaises(ValidationError):
            await self.scheduler.action(completed["id"], "cancel", completed["version"])

        canceled = await self.scheduler.action(blocker["id"], "cancel", blocker["version"])
        self.assertEqual(canceled["status"], TaskStatus.CANCELED.value)
        with self.assertRaises(ValidationError):
            await self.scheduler.action(canceled["id"], "cancel", canceled["version"])


if __name__ == "__main__":
    unittest.main()
