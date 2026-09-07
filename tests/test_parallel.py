"""Focused concurrency and Git integration contracts; no live Codex calls."""
from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch

from codex_taskboard.task_skills import MERGE_SKILL, PLAN_SKILL
from codex_taskboard.db import Database
from codex_taskboard.events import EventBus
from codex_taskboard.scheduler import Scheduler
from codex_taskboard import git_workspace as git
from codex_taskboard.errors import ConflictError, ValidationError
from tests.test_scheduler_contract import FakeAppServer


class ParallelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        git.git(str(self.root), "init", "-b", "main")
        git.git(str(self.root), "config", "user.name", "Test")
        git.git(str(self.root), "config", "user.email", "test@example.invalid")
        (self.root / "shared.txt").write_text("base\n")
        git.git(str(self.root), "add", ".")
        git.git(str(self.root), "commit", "-m", "base")
        self.db = Database(Path(self.temp.name) / "board.db")
        self.project = self.db.create_project(key="TEST", name="Test", workspace_path=str(self.root), automation_enabled=True)
        with patch("codex_taskboard.scheduler.CodexAppServer", FakeAppServer):
            self.scheduler = Scheduler(self.db, EventBus(), codex_command=["fake"])
        self.runtime = self.scheduler.parallel
        self.server = self.scheduler.server
        self.worktrees = []
        async def create_worktree(workspace, branch):
            path = Path(self.temp.name) / f"wt-{len(self.worktrees)}"
            git.git(workspace, "worktree", "add", "--detach", str(path), branch)
            self.worktrees.append(path)
            return {"worktreeGitRoot": str(path), "worktreeWorkspaceRoot": str(path)}
        self.server.create_worktree = AsyncMock(side_effect=create_worktree)
        self.server.set_worktree_owner = AsyncMock()

    async def asyncTearDown(self):
        await self.runtime.stop()
        for worker in self.scheduler._execution_tasks.values():
            worker.cancel()
        await asyncio.gather(*self.scheduler._execution_tasks.values(), return_exceptions=True)
        self.db.close()
        self.temp.cleanup()

    def task(self, title="Task", **kwargs):
        return self.db.create_task(project_id=self.project["id"], title=title, scheduling_mode="parallel", **kwargs)

    async def result(self, task, path="result.txt", text="result\n"):
        current = self.db.get_task(task["id"])
        if current["status"] == "todo":
            self.db.claim_task(current["id"], current["version"])
        current = await self.runtime.prepare_task(current["id"])
        worktree = Path(current["worktreeGitRoot"])
        (worktree / path).parent.mkdir(parents=True, exist_ok=True)
        (worktree / path).write_text(text)
        git.git(str(worktree), "add", path)
        git.git(str(worktree), "commit", "-m", title_safe(task["title"]))
        await self.runtime.completed(task["id"], {}, "Implemented")
        return self.db.get_task(task["id"])

    async def approve_merge(self, task):
        task = self.db.get_task(task["id"])
        if task["mergeState"] == "pending_review":
            await self.runtime.action(task, "complete")
        await self.runtime.merge_task(task["id"])
        return self.db.get_task(task["id"])

    async def test_unlimited_parallel_claim_and_exclusive_barrier(self):
        tasks = [self.task(str(i)) for i in range(4)]
        with patch.object(self.scheduler, "_spawn_execution") as spawn:
            await self.runtime.dispatch()
        self.assertEqual(spawn.call_count, 4)
        exclusive = self.db.create_task(project_id=self.project["id"], title="exclusive")
        later = self.task("later")
        self.assertIsNone(self.db.claim_candidate(later["id"]))
        self.assertIsNone(self.db.claim_candidate(exclusive["id"]))
        for task in tasks:
            task = self.db.get_task(task["id"])
            self.db.update_task(task["id"], task["version"], status="done", run_state=None)
        self.assertIsNotNone(self.db.claim_candidate(exclusive["id"]))
        self.assertIsNone(self.db.claim_candidate(later["id"]))

    async def test_same_git_repository_alias_and_scope_component_rules(self):
        first = self.task("first", write_scopes=["web/"])
        self.db.claim_task(first["id"], first["version"])
        overlap = self.task("overlap", write_scopes=["web/page.tsx"])
        sibling = self.task("sibling", write_scopes=["website/page.tsx"])
        unscoped = self.task("unscoped")
        self.assertIsNone(self.db.claim_candidate(overlap["id"]))
        self.assertIsNotNone(self.db.claim_candidate(sibling["id"]))
        self.assertIsNotNone(self.db.claim_candidate(unscoped["id"]))
        alias = self.db.create_project(key="ALIAS", name="Alias", workspace_path=str(self.root))
        exclusive = self.db.create_task(project_id=alias["id"], title="exclusive")
        self.assertIsNone(self.db.claim_candidate(exclusive["id"]))
        with self.assertRaises(ValidationError):
            self.task("escape", write_scopes=["../outside"])

    async def test_merge_review_and_idempotent_publication(self):
        task = await self.result(self.task())
        self.assertEqual(task["mergeState"], "pending_review")
        self.assertFalse((self.root / "result.txt").exists())
        task = await self.approve_merge(task)
        self.assertEqual(task["status"], "done")
        self.assertEqual((self.root / "result.txt").read_text(), "result\n")
        count = len(self.worktrees)
        await self.runtime.merge_task(task["id"])
        self.assertEqual(len(self.worktrees), count)
        self.assertEqual(self.server.turn_calls, [])
        op = self.db.operations(task["id"], "merge")[-1]
        self.db.save_operation(op["id"], task["id"], "merge", "publishing")
        await self.runtime.recover_operation(op["id"])
        self.assertEqual(self.db.operation(op["id"])["state"], "completed")

    async def test_dirty_target_and_outside_scope_preserve_changes(self):
        task = await self.result(self.task(write_scopes=["allowed/"]), path="elsewhere.txt")
        self.assertEqual(task["mergeState"], "blocked")
        self.assertEqual(task["runState"], "failed")
        self.assertFalse((self.root / "elsewhere.txt").exists())
        other = await self.result(self.task("other"))
        (self.root / "shared.txt").write_text("user changes\n")
        other = await self.approve_merge(other)
        self.assertEqual(other["mergeState"], "blocked")
        self.assertEqual((self.root / "shared.txt").read_text(), "user changes\n")
        self.assertFalse((self.root / "result.txt").exists())

    async def test_group_preparation_dependency_snapshot_and_final_delivery(self):
        group = self.db.create_task(project_id=self.project["id"], title="Group", kind="parallel_group")
        a = self.db.create_task(project_id=self.project["id"], parent_id=group["id"], title="A")
        b = self.db.create_task(project_id=self.project["id"], parent_id=group["id"], title="B", blocked_by_ids=[a["id"]])
        with patch.object(self.scheduler, "_spawn_execution") as spawn:
            await self.runtime.dispatch()
        spawn.assert_not_called()
        self.assertEqual(self.server.create_worktree.await_count, 0)
        await self.runtime.action(self.db.get_task(group["id"]), "group_submit")
        await self.runtime.workers["group:" + group["id"]]
        self.assertIsNone(self.db.claim_candidate(b["id"]))
        a = await self.result(a, "a.txt", "A\n")
        self.assertEqual(a["mergeState"], "queued")
        await self.runtime.merge_task(a["id"])
        self.assertFalse((self.root / "a.txt").exists())
        b = await self.result(b, "b.txt", "B\n")
        self.assertTrue((Path(b["worktreeGitRoot"]) / "a.txt").exists())
        await self.runtime.merge_task(b["id"])
        group = self.db.get_task(group["id"])
        self.assertEqual(group["progress"]["integrated"], 2)
        self.assertEqual(group["mergeState"], "pending_review")
        self.assertIsNone(group["threadId"])
        group = await self.approve_merge(group)
        self.assertEqual(group["status"], "done")
        self.assertTrue((self.root / "a.txt").exists())
        self.assertTrue((self.root / "b.txt").exists())

    async def test_conflict_uses_separate_auxiliary_session_and_checks_git(self):
        a, b = self.task("A"), self.task("B")
        # Both sources intentionally start from the same base.
        b = await self.runtime.prepare_task(b["id"])
        a = await self.result(a, "shared.txt", "A\n")
        await self.approve_merge(a)
        b = await self.result(b, "shared.txt", "B\n")
        async def resolve(thread, prompt, **kwargs):
            self.assertEqual(kwargs["skill"], MERGE_SKILL)
            operation = self.db.operations(b["id"], "merge")[-1]
            root = operation["payload"]["worktreeGitRoot"]
            Path(root, "shared.txt").write_text("A and B\n")
            git.git(root, "add", "shared.txt")
            git.git(root, "commit", "--no-edit")
            return "merge-turn"
        self.server.start_turn = AsyncMock(side_effect=resolve)
        self.server.wait_for_turn = AsyncMock(return_value={"turn": {"id": "merge-turn", "status": "completed"}})
        b = await self.approve_merge(b)
        self.assertEqual(b["status"], "done", b["waitReason"])
        self.assertIsNone(b["threadId"])
        self.assertEqual(self.server.start_turn.await_count, 1)
        self.assertEqual((self.root / "shared.txt").read_text(), "A and B\n")
        self.assertTrue(git.is_ancestor(str(self.root), b["parallel"]["resultCommit"], git.commit(str(self.root))))

    async def test_pause_requires_observed_stop_and_releases_after_confirmation(self):
        task = self.task(write_scopes=["web"])
        task = self.db.claim_task(task["id"], task["version"])
        task = self.db.update_task(task["id"], task["version"], thread_id="native", run_state="running")
        self.server.read_snapshots["native"] = {"thread": {"turns": [{"id": "turn", "status": "inProgress"}]}}
        with self.assertRaises(ConflictError):
            await self.runtime.pause(task)
        self.assertTrue(self.db._conn.execute("SELECT 1 FROM execution_leases WHERE task_id=?", (task["id"],)).fetchone())
        self.server.read_snapshots["native"] = {"thread": {"turns": [{"id": "turn", "status": "interrupted"}]}}
        await self.runtime.pause(self.db.get_task(task["id"]))
        self.assertFalse(self.db._conn.execute("SELECT 1 FROM execution_leases WHERE task_id=?", (task["id"],)).fetchone())
        self.assertTrue(self.db.get_task(task["id"])["parallel"]["paused"])
        await self.scheduler._handle_turn_result(task["id"], {"turn": {"id": "turn", "status": "completed"}})
        self.assertEqual(self.db.get_task(task["id"])["runState"], "failed")

    async def test_plan_generation_explicitly_uses_plan_skill(self):
        group = self.db.create_task(project_id=self.project["id"], title="拆分任务", kind="parallel_group")
        self.db.save_operation("proposal", group["id"], "plan", "pending")
        self.server.start_turn = AsyncMock(return_value="plan-turn")
        self.server.wait_for_turn = AsyncMock(return_value={
            "status": "completed", "output": [{"type": "agentMessage", "text": '{"tasks":[{"key":"a","title":"子任务"}]}'}],
        })
        await self.runtime.run_plan("proposal")
        self.assertEqual(self.server.start_turn.call_args.kwargs["skill"], PLAN_SKILL)
        self.assertEqual(self.db.operation("proposal")["state"], "ready")
        self.assertEqual(self.db.children(group["id"]), [])

    async def test_plan_confirmation_is_atomic_and_never_executes(self):
        group = self.db.create_task(project_id=self.project["id"], title="Group", kind="parallel_group")
        self.db.save_operation("proposal", group["id"], "plan", "ready")
        cycle = {"tasks": [{"key": "a", "title": "A", "blockedByKeys": ["b"]}, {"key": "b", "title": "B", "blockedByKeys": ["a"]}]}
        with self.assertRaises(ValidationError):
            await self.runtime.confirm_plan(group["id"], group["version"], "proposal", cycle)
        self.assertEqual(self.db.children(group["id"]), [])
        cycle["tasks"][0]["blockedByKeys"] = []
        await self.runtime.confirm_plan(group["id"], group["version"], "proposal", cycle)
        self.assertEqual(len(self.db.children(group["id"])), 2)
        await self.runtime.confirm_plan(group["id"], group["version"], "proposal", cycle)
        self.assertEqual(len(self.db.children(group["id"])), 2)
        self.assertEqual(self.server.turn_calls, [])
        self.assertEqual(self.server.create_worktree.await_count, 0)

    async def test_uncertain_turn_is_reconciled_without_duplicate_dispatch(self):
        task = self.task()
        task = self.db.claim_task(task["id"], task["version"])
        task = self.db.update_task(task["id"], task["version"], thread_id="existing")
        self.server.start_turn = AsyncMock(side_effect=ConnectionError("response lost"))
        with self.assertRaises(ConnectionError):
            await self.runtime.execution_turn(task, "existing", "work", {})
        with self.assertRaises(ConflictError):
            await self.runtime.execution_turn(task, "existing", "work", {})
        self.assertEqual(self.server.start_turn.await_count, 1)
        self.server.read_snapshots["existing"] = {"thread": {"turns": [{"id": "accepted", "status": "inProgress"}]}}
        with patch.object(self.scheduler, "_spawn_existing_watch") as watch:
            await self.runtime.recover()
        watch.assert_called_once_with(task["id"], "accepted")
        self.assertFalse(self.db.get_task(task["id"])["parallel"]["uncertainExecution"])
        self.assertEqual(self.server.start_turn.await_count, 1)

    async def test_missing_skill_is_a_definitive_failure_without_uncertain_turn(self):
        task = self.task()
        self.server.start_turn = AsyncMock(side_effect=ValidationError("skill not installed"))
        with self.assertRaises(ValidationError):
            await self.runtime.execution_turn(task, "existing", "work", {})
        self.assertEqual(self.db.operations(task["id"], "execution")[-1]["state"], "blocked")
        self.assertFalse(self.db.get_task(task["id"])["parallel"].get("uncertainExecution"))

    async def test_target_advance_reprepares_and_queued_followup_invalidates_review(self):
        task = await self.result(self.task())
        await self.runtime.action(task, "complete")
        original = self.runtime.publish_merge
        async def advance(op_id):
            (self.root / "concurrent.txt").write_text("another commit")
            git.git(str(self.root), "add", ".")
            git.git(str(self.root), "commit", "-m", "target advanced")
            await original(op_id)
        with patch.object(self.runtime, "publish_merge", side_effect=advance):
            await self.runtime.merge_task(task["id"])
        self.assertEqual(self.db.get_task(task["id"])["mergeState"], "queued")
        await self.runtime.merge_task(task["id"])
        self.assertEqual(self.db.get_task(task["id"])["status"], "done")
        self.assertTrue((self.root / "concurrent.txt").exists())
        other = await self.result(self.task("followup"), "followup.txt")
        await self.runtime.action(other, "complete")
        exclusive = self.db.create_task(project_id=self.project["id"], title="exclusive")
        self.db.claim_task(exclusive["id"], exclusive["version"])
        with patch.object(self.scheduler, "_spawn_execution") as spawn:
            await self.runtime.queue(self.db.get_task(other["id"]), "Please revise")
        spawn.assert_not_called()
        self.assertTrue(self.db.get_task(other["id"])["queued"])
        self.assertEqual(self.db.get_task(other["id"])["mergeState"], "none")
        await self.runtime.merge_task(other["id"])
        self.assertFalse((self.root / "followup.txt").exists())

    async def test_manual_group_queues_without_auto_claim_and_failed_child_isolated(self):
        draft = self.db.create_task(project_id=self.project["id"], title="Draft group", kind="parallel_group", priority="draft")
        self.assertIn("草稿", self.db.eligibility(draft))
        self.db.update_project(self.project["id"], self.project["version"], automation_enabled=False)
        active = self.db.create_task(project_id=self.project["id"], title="active exclusive")
        self.db.claim_task(active["id"], active["version"])
        group = self.db.create_task(project_id=self.project["id"], title="Group", kind="parallel_group")
        a = self.db.create_task(project_id=self.project["id"], parent_id=group["id"], title="A")
        b = self.db.create_task(project_id=self.project["id"], parent_id=group["id"], title="B", blocked_by_ids=[a["id"]])
        c = self.db.create_task(project_id=self.project["id"], parent_id=group["id"], title="C")
        group = await self.runtime.action(self.db.get_task(group["id"]), "group_submit")
        group = await self.runtime.action(group, "run")
        self.assertTrue(group["parallel"]["runRequested"])
        before = group["version"]
        await self.runtime.dispatch()
        self.assertEqual(self.db.get_task(group["id"])["version"], before, "Blocked groups must not self-trigger an infinite dispatch loop")
        active = self.db.get_task(active["id"])
        self.db.update_task(active["id"], active["version"], status="done", run_state=None)
        with patch.object(self.scheduler, "_spawn_execution"):
            await self.runtime.dispatch()
        await self.runtime.workers["group:" + group["id"]]
        self.db.claim_task(a["id"], a["version"])
        await self.scheduler._fail_task(a["id"], {"code": "TURN_FAILED", "message": "failed"})
        self.assertIsNone(self.db.claim_candidate(b["id"]))
        self.assertIsNotNone(self.db.claim_candidate(c["id"]))
        self.assertEqual(self.db.get_task(group["id"])["progress"]["attention"], 1)

    async def test_uncertain_worktree_requires_linking_and_never_creates_twice(self):
        task = self.task()
        original = self.server.create_worktree.side_effect
        async def lose_response(workspace, branch):
            await original(workspace, branch)
            raise ConnectionError("response lost")
        self.server.create_worktree.side_effect = lose_response
        with self.assertRaises(ConnectionError):
            await self.runtime.prepare_task(task["id"])
        # Simulate a restart losing the in-memory response and task-level flag.
        self.db.set_parallel(task["id"], uncertainWorktree=None)
        with self.assertRaises(ConflictError):
            await self.runtime.prepare_task(task["id"])
        self.assertEqual(self.server.create_worktree.await_count, 1)
        task = self.db.get_task(task["id"])
        self.assertTrue(task["parallel"]["uncertainWorktree"])
        await self.runtime.action(task, "attach_worktree", str(self.worktrees[0]))
        task = await self.runtime.prepare_task(task["id"])
        self.assertEqual(Path(task["worktreeGitRoot"]).resolve(), self.worktrees[0].resolve())
        self.assertEqual(self.server.create_worktree.await_count, 1)

    async def test_reworked_dependency_is_visible_when_consumer_revalidates(self):
        group = self.db.create_task(project_id=self.project["id"], title="Group", kind="parallel_group")
        a = self.db.create_task(project_id=self.project["id"], parent_id=group["id"], title="A")
        b = self.db.create_task(project_id=self.project["id"], parent_id=group["id"], title="B", blocked_by_ids=[a["id"]])
        await self.runtime.action(self.db.get_task(group["id"]), "group_submit")
        await self.runtime.workers["group:" + group["id"]]
        await self.result(a, "a.txt", "first")
        await self.runtime.merge_task(a["id"])
        await self.result(b, "b.txt")
        await self.runtime.merge_task(b["id"])
        self.assertEqual(self.db.get_task(group["id"])["status"], "in_review")
        with patch.object(self.scheduler, "_spawn_execution"):
            await self.runtime.queue(self.db.get_task(a["id"]), "revise")
            await self.runtime.dispatch()
            await self.runtime.workers["group:" + group["id"]]
            await self.runtime.dispatch()
        await self.result(self.db.get_task(a["id"]), "a.txt", "revised")
        await self.runtime.merge_task(a["id"])
        self.assertTrue(self.db.get_task(b["id"])["parallel"]["needsValidation"])
        with patch.object(self.scheduler, "_spawn_execution"):
            await self.runtime.queue(self.db.get_task(b["id"]), "revalidate")
        b = await self.runtime.prepare_task(b["id"])
        self.assertEqual(Path(b["worktreeGitRoot"], "a.txt").read_text(), "revised")
        await self.result(b, "b.txt", "revalidated")
        await self.runtime.merge_task(b["id"])
        self.assertFalse(self.db.get_task(b["id"])["parallel"]["needsValidation"])
        self.assertEqual(self.db.get_task(group["id"])["status"], "in_review")

    async def test_lost_turn_observation_holds_execution_until_terminal_confirmation(self):
        task = self.task()
        task = self.db.claim_task(task["id"], task["version"])
        self.db.update_task(task["id"], task["version"], thread_id="thread", run_state="running")
        self.db.create_run(task_id=task["id"], thread_id="thread", turn_id="live", run_state="running")
        self.scheduler._register_turn(task["id"], "live")
        self.server.wait_for_turn = AsyncMock(side_effect=ConnectionError("connection lost"))
        await self.scheduler._watch_turn(task["id"], "live")
        exclusive = self.db.create_task(project_id=self.project["id"], title="exclusive")
        self.assertIsNone(self.db.claim_candidate(exclusive["id"]))
        self.assertTrue(self.db.get_task(task["id"])["parallel"]["uncertainObservation"])
        await self.scheduler._handle_turn_result(task["id"], {"turn": {"id": "live", "status": "interrupted"}})
        self.assertFalse(self.db.get_task(task["id"])["parallel"]["uncertainObservation"])
        self.assertIsNotNone(self.db.claim_candidate(exclusive["id"]))

    async def test_http_group_creation_idempotency_and_top_level_filter(self):
        import httpx
        from codex_taskboard.app import create_app
        app = create_app(data_dir=self.temp.name, db_path=str(Path(self.temp.name) / "http.db"), codex_command=["fake"])
        try:
            project = app.state.db.create_project(key="HTTP", name="HTTP", workspace_path=str(self.root))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                url = f"/api/projects/{project['id']}/tasks"
                body = {"title": "Group", "kind": "parallel_group", "requestId": "create-group"}
                response = await client.post(url, json=body)
                self.assertEqual(response.status_code, 201, response.text)
                group = response.json()
                repeated = await client.post(url, json=body)
                self.assertEqual(repeated.json()["id"], group["id"])
                child_body = {"title": "Child", "version": group["version"], "requestId": "create-child"}
                child_url = f"/api/tasks/{group['id']}/children"
                child = await client.post(child_url, json=child_body)
                self.assertEqual(child.status_code, 201, child.text)
                self.assertEqual((await client.post(child_url, json=child_body)).json()["id"], child.json()["id"])
                self.assertEqual(len((await client.get(url)).json()["tasks"]), 1)
                detail = (await client.get(f"/api/tasks/{group['id']}")).json()
                self.assertEqual(len(detail["children"]), 1)
                self.assertEqual(detail["groupPhase"], "preparing")
                premature = await client.post(f"/api/tasks/{child.json()['id']}/actions", json={"action": "run", "version": child.json()["version"]})
                self.assertIn(premature.status_code, {409, 422})
        finally:
            await app.state.scheduler.stop()
            app.state.db.close()


def title_safe(title):
    return "Implement " + title
