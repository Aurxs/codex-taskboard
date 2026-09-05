from __future__ import annotations

import tempfile
import unittest
from unittest.mock import AsyncMock

import httpx

from codex_taskboard.app import create_app
from codex_taskboard.app_server import CodexAppServer
from codex_taskboard.db import Database


class ExecutionOptionsTests(unittest.IsolatedAsyncioTestCase):
    async def test_upgrade_preserves_existing_tasks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = f"{directory}/db.sqlite3"
            db = Database(path)
            project = db.create_project(key="T", name="Test", workspace_path=directory)
            task = db.create_task(project_id=project["id"], title="Existing task")
            db._conn.execute("ALTER TABLE tasks DROP COLUMN model")
            db._conn.execute("ALTER TABLE tasks DROP COLUMN reasoning_effort")
            db._conn.execute("UPDATE schema_meta SET version = 2")
            db.close()
            db = Database(path)
            self.assertEqual(db.get_task(task["id"])["title"], "Existing task")
            self.assertIsNone(db.get_task(task["id"])["model"])
            db.close()

    async def test_wire_only_adds_explicit_options(self):
        server = CodexAppServer(["unused"])
        server.request = AsyncMock(return_value={"turn": {"id": "turn-test"}})
        await server.start_turn("thread-test", "hello", task_id="task-test")
        self.assertEqual(server.request.call_args.args[1], {
            "threadId": "thread-test", "input": [{"type": "text", "text": "hello"}],
        })
        await server.start_turn("thread-test", "hello", task_id="task-test", model="test-model", effort="high")
        params = server.request.call_args.args[1]
        self.assertEqual(params["model"], "test-model")
        self.assertEqual(params["effort"], "high")
        self.assertNotIn("approvalPolicy", params)
        self.assertNotIn("sandboxPolicy", params)

    async def test_http_persistence_validation_and_clear(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(data_dir=directory, db_path=f"{directory}/db.sqlite3", codex_command=["unused"])
            catalog = [{"model": "test-model", "displayName": "Test", "defaultReasoningEffort": "low", "supportedReasoningEfforts": [{"reasoningEffort": "low"}, {"reasoningEffort": "high"}]}]
            app.state.scheduler.list_models = AsyncMock(return_value=catalog)
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
                    project = app.state.db.create_project(key="T", name="Test", workspace_path=directory)
                    path = f"/api/projects/{project['id']}/tasks"
                    response = await client.post(path, json={"title": "Selected model", "model": "test-model", "reasoningEffort": "high"})
                    self.assertEqual(response.status_code, 201, response.text)
                    task = response.json()
                    self.assertEqual(task["reasoningEffort"], "high")
                    self.assertEqual((await client.get("/api/codex/models")).json()["models"], catalog)
                    invalid = await client.post(path, json={"title": "Invalid", "model": "test-model", "reasoningEffort": "ultra"})
                    self.assertEqual(invalid.status_code, 422)
                    reopened = Database(f"{directory}/db.sqlite3")
                    self.assertEqual(reopened.get_task(task["id"])["model"], "test-model")
                    reopened.close()
                    cleared = await client.patch(f"/api/tasks/{task['id']}", json={"version": task["version"], "model": None, "reasoningEffort": None})
                    self.assertEqual(cleared.status_code, 200, cleared.text)
                    self.assertIsNone(cleared.json()["model"])
            finally:
                await app.state.scheduler.stop()
                app.state.db.close()
