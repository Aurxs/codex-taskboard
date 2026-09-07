"""Focused contracts for independent planning and both question protocols."""
import asyncio
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from codex_taskboard.app_server import CodexAppServer, AppServerUnavailable
from codex_taskboard.db import Database
from codex_taskboard.errors import ConflictError, ValidationError
from codex_taskboard.events import EventBus
from codex_taskboard.scheduler import Scheduler
from codex_taskboard.questions import REPLY_OPEN, REPLY_CLOSE, record_questions
from tests.test_scheduler_contract import FakeAppServer


class PlanningTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / "board.db"
        self.db = Database(self.path)
        self.project = self.db.create_project(key="PLAN", name="Plan", workspace_path=self.temp.name, automation_enabled=True)
        self.task = self.db.create_task(project_id=self.project["id"], title="Feature", description="Requirements")
        with patch("codex_taskboard.scheduler.CodexAppServer", FakeAppServer):
            self.scheduler = Scheduler(self.db, EventBus(), codex_command=["fake"])
        self.planning = self.scheduler.planning
        self.server = self.scheduler.server
        self.server.start_turn = AsyncMock(return_value="plan-turn")
        self.server.steer_turn = AsyncMock()

    async def asyncTearDown(self):
        await self.scheduler.parallel.stop()
        self.db.close()
        self.temp.cleanup()

    async def start_plan(self):
        result = await self.scheduler.action(self.task["id"], "plan_start", self.task["version"])
        await asyncio.gather(*self.scheduler.parallel.workers.values())
        return self.db.operation(result["plan"]["operationId"])

    async def test_real_plan_mode_uses_current_model_without_permission_overrides(self):
        server = CodexAppServer()
        server._thread_settings["thread"] = {"model": "gpt-6-astra", "reasoningEffort": "high"}
        server.request = AsyncMock(return_value={"turn": {"id": "turn"}})
        await server.start_turn("thread", "Plan this", task_id="task", plan=True)
        params = server.request.await_args.args[1]
        self.assertEqual(params["collaborationMode"], {"mode": "plan", "settings": {
            "model": "gpt-6-astra", "reasoning_effort": "high", "developer_instructions": None}})
        self.assertFalse({"approvalPolicy", "sandboxPolicy", "skill"} & params.keys())
        self.assertEqual(params["input"], [{"type": "text", "text": "Plan this"}])

    async def test_plan_holds_dispatch_and_long_final_survives_reopen_and_execution(self):
        op = await self.start_plan()
        current = self.db.get_task(self.task["id"])
        self.assertEqual(current["status"], "todo")
        self.assertIsNone(current["threadId"])
        self.assertTrue(self.server.start_turn.await_args.kwargs["plan"])
        self.assertIsNone(self.db.claim_candidate(self.task["id"]))
        with self.assertRaises(ValidationError):
            self.db.enqueue(self.task["id"], self.db.get_task(self.task["id"])["version"])
        with self.assertRaises(ValidationError):
            self.db.delete_task(self.task["id"], self.db.get_task(self.task["id"])["version"])
        final = "# Final plan\n" + "Detailed requirement\n" * 1000
        await self.planning.observe(op, {"id": "plan-turn", "status": "completed", "items": [
            {"type": "agentMessage", "id": "final", "text": f"<proposed_plan>{final}</proposed_plan>"}]})
        current = self.db.get_task(self.task["id"])
        self.server.read_snapshots[op["payload"]["threadId"]] = {"thread": {"turns": [{"id": "plan-turn", "status": "completed"}]}}
        self.assertFalse(current["plan"]["hold"])
        self.assertEqual(current["plan"]["state"], "confirmed")
        reopened = Database(self.path)
        try:
            self.assertEqual(reopened.get_task(current["id"])["plan"]["acceptedText"], final.strip())
        finally:
            reopened.close()
        await self.scheduler.parallel.execution_turn(self.db.get_task(current["id"]), "execution-thread", "Implement", {})
        self.assertIn(final.strip(), self.server.start_turn.await_args.args[1])
        self.assertNotIn("plan", self.server.start_turn.await_args.kwargs)

    async def test_questions_and_progress_are_not_final_and_recovery_uses_latest_turn(self):
        op = await self.start_plan()
        thread = op["payload"]["threadId"]
        self.server.read_snapshots[thread] = {"thread": {"turns": [
            {"id": "old", "status": "completed", "items": [{"type": "plan", "id": "old-plan", "text": "Old plan"}]},
            {"id": "plan-turn", "status": "completed", "items": [{"type": "agentMessage", "id": "ask", "text": "Clarify?", "delivery": "async"}]}]}}
        await self.planning.recover()
        current = self.db.get_task(self.task["id"])
        self.assertEqual(current["plan"]["state"], "conversation")
        self.assertFalse(current["plan"].get("text"))
        self.assertEqual(len(self.db.pending_interactions()), 1)
        self.server.read_snapshots[thread]["thread"]["turns"][-1] = {
            "id": "plan-turn", "status": "completed", "items": [{"type": "plan", "id": "final", "text": "New final"}]}
        await self.planning.recover()
        self.assertEqual(self.db.get_task(current["id"])["plan"]["text"], "New final")
        with self.assertRaises(ValidationError):
            await self.scheduler.action(current["id"], "plan_accept", self.db.get_task(current["id"])["version"])
        interaction = self.db.pending_interactions()[0]
        self.db.resolve_interaction(interaction["id"], interaction["version"], {"resolvedInCodex": True})
        await self.planning.recover()
        saved = self.db.get_task(current["id"])
        self.assertFalse(saved["plan"]["hold"])
        self.assertEqual(saved["plan"]["acceptedText"], "New final")

    async def test_async_answer_steers_then_continues_plan_without_blocking_or_double_send(self):
        op = await self.start_plan()
        thread = op["payload"]["threadId"]
        item = {"id": "question", "type": "agentMessage", "delivery": "async", "text": "", "questions": [
            {"title": "Which scope?", "options": ["Small", "Large"]}]}
        await self.scheduler._handle_notification("item/completed", {"threadId": thread, "turnId": "plan-turn", "item": item})
        interaction = self.db.pending_interactions()[0]
        self.assertIsNone(self.db.get_task(self.task["id"])["runState"])
        self.server.read_snapshots[thread] = {"thread": {"turns": [{"id": "plan-turn", "status": "inProgress"}]}}
        key = interaction["questions"][0]["id"]
        response = {"answers": {key: {"answers": ["Small"]}}}
        await self.scheduler.resolve_interaction(interaction["id"], interaction["version"], response)
        wire = self.server.steer_turn.await_args.args[2]
        self.assertTrue(wire.startswith(REPLY_OPEN))
        reply = json.loads(wire[len(REPLY_OPEN):-len(REPLY_CLOSE)])
        self.assertEqual(reply[0]["questionItemId"], '["request_user_input_async","question",0]')
        with self.assertRaises(ConflictError):
            await self.scheduler.resolve_interaction(interaction["id"], interaction["version"], response)
        self.assertEqual(self.server.steer_turn.await_count, 1)
        # A later answer starts another planning turn after completion.
        item["id"] = "question2"
        record_questions(self.db, self.task["id"], thread, "plan-turn", item)
        interaction = self.db.pending_interactions()[0]
        self.server.read_snapshots[thread]["thread"]["turns"][0]["status"] = "completed"
        await self.scheduler.resolve_interaction(interaction["id"], 1, {"answers": {
            interaction["questions"][0]["id"]: {"answers": ["Custom scope"]}}})
        self.assertTrue(self.server.start_turn.await_args.kwargs["plan"])

    async def test_native_answers_reconcile_partial_replies_and_question_replay(self):
        thread = "execution-thread"
        item = {"id": "q", "type": "agentMessage", "delivery": "async", "questions": [{"title": "One"}, {"title": "Two"}]}
        record_questions(self.db, self.task["id"], thread, "turn", item)
        interaction = self.db.pending_interactions()[0]
        for q in interaction["questions"]:
            text = REPLY_OPEN + json.dumps([{ "questionItemId": q["id"], "question": q["question"], "answer": "A"}]) + REPLY_CLOSE
            record_questions(self.db, self.task["id"], thread, "turn", {"type": "userMessage", "content": [{"type": "text", "text": text}]})
        self.assertFalse(self.db.pending_interactions())
        record_questions(self.db, self.task["id"], thread, "turn", item)
        self.assertEqual(len(self.db.list_interactions(self.task["id"])), 1)

    async def test_uncertain_plan_start_is_not_replayed_or_mistaken_for_previous_final(self):
        op = await self.start_plan()
        thread = op["payload"]["threadId"]
        self.server.read_snapshots[thread] = {"thread": {"turns": [{"id": "plan-turn", "status": "completed", "items": [{"type": "plan", "text": "Old"}]}]}}
        self.server.start_turn.side_effect = AppServerUnavailable("Disconnected")
        with self.assertRaises(AppServerUnavailable):
            await self.planning.send(op, "Revise the plan")
        before = self.server.start_turn.await_count
        await self.planning.recover()
        self.assertEqual(self.db.get_task(self.task["id"])["plan"]["state"], "uncertain")
        self.assertEqual(self.server.start_turn.await_count, before)

    async def test_blocking_question_can_cancel_and_rejects_empty_answers(self):
        op = await self.start_plan()
        thread = op["payload"]["threadId"]
        pending = asyncio.create_task(self.scheduler._handle_server_request("item/tool/requestUserInput", 12, {
            "threadId": thread, "turnId": "plan-turn", "questions": [{"id": "q", "question": "Choose", "options": [{"label": "A"}]}]}))
        await asyncio.sleep(0)
        interaction = self.db.pending_interactions()[0]
        with self.assertRaises(ValidationError):
            await self.scheduler.resolve_interaction(interaction["id"], 1, {"answers": {"q": {"answers": []}}})
        await self.scheduler.resolve_interaction(interaction["id"], 1, {}, canceled=True)
        self.assertEqual(await pending, {"answers": {}})
        self.assertEqual(self.db.get_task(self.task["id"])["status"], "todo")

    async def test_execution_async_reply_preserves_native_envelope(self):
        task = self.db.update_task(self.task["id"], self.task["version"], status="in_progress", run_state="running", thread_id="execution")
        self.server.register_thread_task("execution", task["id"])
        self.server.read_snapshots["execution"] = {"thread": {"turns": [{"id": "active", "status": "inProgress"}]}}
        item = {"id": "q", "type": "agentMessage", "delivery": "async", "questions": [{"title": "Scope?"}]}
        self.scheduler._record_item(task["id"], "active", item, True)
        interaction = self.db.pending_interactions()[0]
        await self.scheduler.resolve_interaction(interaction["id"], 1, {"answers": {
            interaction["questions"][0]["id"]: {"answers": ["Small"]}}})
        self.assertIsNone(self.server.steer_turn.await_args.kwargs["skill"])
        text = self.server.steer_turn.await_args.args[2]
        self.assertTrue(text.startswith(REPLY_OPEN))
        await self.scheduler.parallel.execution_turn(task, "execution", text, {})
        self.assertEqual(self.server.start_turn.await_args.args[1], text)
        self.assertIsNone(self.server.start_turn.await_args.kwargs["skill"])

    async def test_cancel_stops_plan_and_clears_questions_before_releasing_hold(self):
        op = await self.start_plan()
        thread = op["payload"]["threadId"]
        record_questions(self.db, self.task["id"], thread, "plan-turn", {
            "id": "q", "type": "agentMessage", "delivery": "async", "text": "Question"})
        self.server.read_snapshots[thread] = {"thread": {"turns": [{"id": "plan-turn", "status": "inProgress"}]}}
        current = self.db.get_task(self.task["id"])
        with self.assertRaises(ConflictError):
            await self.scheduler.action(current["id"], "plan_cancel", current["version"])
        self.assertTrue(self.db.get_task(current["id"])["plan"]["hold"])
        self.server.read_snapshots[thread]["thread"]["turns"][0]["status"] = "interrupted"
        result = await self.scheduler.action(current["id"], "plan_cancel", current["version"])
        self.assertFalse(result["plan"]["hold"])
        self.assertFalse(self.db.pending_interactions())
        self.assertEqual(self.server.interrupt_calls, [(thread, "plan-turn")])

    async def test_confirm_rejects_native_continuation_and_changed_requirements(self):
        op = await self.start_plan()
        self.planning.save(op, "ready", text="Final")
        thread = op["payload"]["threadId"]
        self.server.read_snapshots[thread] = {"thread": {"turns": [{"id": "next", "status": "inProgress"}]}}
        current = self.db.get_task(self.task["id"])
        with self.assertRaises(ConflictError):
            await self.scheduler.action(current["id"], "plan_accept", current["version"])
        self.server.read_snapshots[thread]["thread"]["turns"][0] = {"id": "plan-turn", "status": "completed"}
        current = self.db.update_task(current["id"], current["version"], description="New requirements")
        with self.assertRaises(ValidationError):
            await self.scheduler.action(current["id"], "plan_accept", current["version"])
        await self.scheduler.action(current["id"], "plan_continue", current["version"], "Update it")
        self.assertIn("New requirements", self.server.start_turn.await_args.args[1])

    async def test_draft_plan_edits_survive_replay_and_feed_execution(self):
        self.task = self.db.update_task(self.task["id"], self.task["version"], priority="draft")
        op = await self.start_plan()
        thread = op["payload"]["threadId"]
        turn = {"id": "plan-turn", "status": "completed", "items": [{"type": "plan", "id": "final", "text": "Native proposal"}]}
        self.server.read_snapshots[thread] = {"thread": {"turns": [turn]}}
        await self.planning.observe(op, turn)
        current = self.db.get_task(self.task["id"])
        self.assertEqual(current["priority"], "draft")
        self.assertIsNone(self.db.claim_candidate(current["id"]))
        saved = await self.scheduler.action(current["id"], "plan_save", current["version"], "Edited plan")
        self.assertGreater(saved["version"], current["version"])
        with self.assertRaises(ConflictError):
            await self.scheduler.action(current["id"], "plan_save", current["version"], "Stale edit")
        await self.planning.recover()
        current = self.db.get_task(current["id"])
        self.assertEqual(current["plan"]["text"], "Edited plan")
        self.assertFalse(current["plan"]["hold"])
        current = await self.scheduler.action(current["id"], "plan_save", current["version"], "Edited after confirmation")
        self.assertEqual(current["plan"]["acceptedText"], "Edited after confirmation")
        self.assertIsNone(self.db.claim_candidate(current["id"]))
        reopened = Database(self.path)
        try:
            self.assertIn("Edited after confirmation", reopened.plan_prompt(current["id"]))
        finally:
            reopened.close()
        await self.scheduler.parallel.execution_turn(current, "execution-thread", "Implement", {})
        self.assertIn("Edited after confirmation", self.server.start_turn.await_args.args[1])
        self.assertNotIn("plan", self.server.start_turn.await_args.kwargs)

    async def test_plan_edit_rejects_empty_running_and_native_continuation(self):
        op = await self.start_plan()
        current = self.db.get_task(self.task["id"])
        with self.assertRaises(ValidationError):
            await self.scheduler.action(current["id"], "plan_save", current["version"], "Too early")
        self.planning.save(op, "ready", text="Final")
        thread = op["payload"]["threadId"]
        current = self.db.get_task(current["id"])
        with self.assertRaises(ValidationError):
            await self.scheduler.action(current["id"], "plan_save", current["version"], "  ")
        self.server.read_snapshots[thread] = {"thread": {"turns": [{"id": "next-turn", "status": "inProgress"}]}}
        with self.assertRaises(ConflictError):
            await self.scheduler.action(current["id"], "plan_save", current["version"], "Outdated edit")

    async def test_planning_history_refreshes_without_execution_thread(self):
        op = await self.start_plan()
        thread = op["payload"]["threadId"]
        self.server.read_snapshots[thread] = {"thread": {"turns": [{"id": "plan-turn", "status": "inProgress", "items": [
            {"type": "agentMessage", "id": "progress", "text": "Inspecting the project"}]}]}}
        task = self.db.get_task(self.task["id"])
        self.assertIsNone(task["threadId"])
        await self.scheduler.hydrate_activity(task)
        self.assertEqual(self.db.list_activity(task["id"])[0]["message"], "Inspecting the project")
        self.server.read_snapshots[thread]["thread"]["turns"][0]["items"][0]["text"] = "Preparing the plan"
        self.scheduler.parallel.publish = AsyncMock()
        await self.planning.recover()
        self.assertEqual(self.db.list_activity(task["id"])[0]["message"], "Preparing the plan")
        self.scheduler.parallel.publish.assert_awaited()
