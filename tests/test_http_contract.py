from __future__ import annotations

import json
import os
from pathlib import Path
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

try:
    import httpx
    from codex_taskboard.app import app as default_app
    from codex_taskboard.app import create_app
    from codex_taskboard.app import _read_codex_projects
except ImportError:  # pragma: no cover - system Python may lack optional app deps
    httpx = None  # type: ignore[assignment]
    default_app = None  # type: ignore[assignment]
    create_app = None  # type: ignore[assignment]
    _read_codex_projects = None  # type: ignore[assignment]


def tearDownModule() -> None:
    # Importing the application module also creates its production singleton;
    # close that unused test-process connection to avoid a ResourceWarning.
    if default_app is not None:
        default_app.state.db.close()


@unittest.skipUnless(httpx is not None, "HTTP contract dependencies are not installed")
class HttpContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.app = create_app(
            data_dir=self.temp_dir.name,
            db_path=f"{self.temp_dir.name}/taskboard.sqlite3",
            codex_command=["codex", "app-server", "--stdio"],
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="http://testserver",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        await self.app.state.scheduler.stop()
        self.app.state.db.close()
        self.temp_dir.cleanup()

    async def test_project_defaults_null_validation_conflict_and_static(self) -> None:
        health = await self.client.get("/health")
        self.assertEqual(health.status_code, 200)
        self.assertTrue(health.json()["ok"])

        static = await self.client.get("/")
        self.assertEqual(static.status_code, 200)
        self.assertIn('id="root"', static.text)

        created = await self.client.post(
            "/api/projects",
            json={"key": "HTTP", "name": "HTTP project", "workspacePath": self.temp_dir.name},
        )
        self.assertEqual(created.status_code, 201)
        project = created.json()
        self.assertFalse(project["automationEnabled"])
        self.assertTrue(project["reviewRequired"])
        self.assertTrue(project["quotaAutoResumeEnabled"])

        null_update = await self.client.patch(
            f"/api/projects/{project['id']}",
            json={"version": project["version"], "name": None},
        )
        self.assertEqual(null_update.status_code, 422)
        self.assertEqual(null_update.json()["error"]["code"], "VALIDATION_ERROR")

        changed = await self.client.patch(
            f"/api/projects/{project['id']}",
            json={"version": project["version"], "quotaAutoResumeEnabled": False},
        )
        self.assertEqual(changed.status_code, 200)
        current = changed.json()
        self.assertFalse(current["quotaAutoResumeEnabled"])
        stale = await self.client.patch(
            f"/api/projects/{project['id']}",
            json={"version": project["version"], "name": "stale"},
        )
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["error"]["code"], "VERSION_CONFLICT")

    async def test_task_null_manual_blocked_completion_and_terminal_cancel(self) -> None:
        project = (
            await self.client.post(
                "/api/projects",
                json={"key": "TASK", "name": "Task project", "workspacePath": self.temp_dir.name},
            )
        ).json()
        blocker = (
            await self.client.post(
                f"/api/projects/{project['id']}/tasks",
                json={"title": "blocker"},
            )
        ).json()
        target = (
            await self.client.post(
                f"/api/projects/{project['id']}/tasks",
                json={"title": "blocked", "blockedByIds": [blocker["id"]]},
            )
        ).json()
        self.assertFalse(target["ready"])

        null_task = await self.client.patch(
            f"/api/tasks/{blocker['id']}",
            json={"version": blocker["version"], "description": None},
        )
        self.assertEqual(null_task.status_code, 422)

        # A deliberate manual completion is allowed to override a dependency
        # lock and should not be confused with scheduler auto-claiming.
        completed = await self.client.post(
            f"/api/tasks/{target['id']}/actions",
            json={"action": "complete", "version": target["version"]},
        )
        self.assertEqual(completed.status_code, 200)
        self.assertEqual(completed.json()["status"], "done")
        cancel_done = await self.client.post(
            f"/api/tasks/{target['id']}/actions",
            json={"action": "cancel", "version": completed.json()["version"]},
        )
        self.assertEqual(cancel_done.status_code, 422)

        canceled = await self.client.post(
            f"/api/tasks/{blocker['id']}/actions",
            json={"action": "cancel", "version": blocker["version"]},
        )
        self.assertEqual(canceled.status_code, 200)
        cancel_canceled = await self.client.post(
            f"/api/tasks/{blocker['id']}/actions",
            json={"action": "cancel", "version": canceled.json()["version"]},
        )
        self.assertEqual(cancel_canceled.status_code, 422)

    async def test_directory_picker_is_explicitly_unsupported_off_macos(self) -> None:
        with patch("codex_taskboard.app.sys.platform", "linux"):
            response = await self.client.post("/api/system/pick-directory")
        self.assertEqual(response.status_code, 501)
        self.assertEqual(response.json()["error"]["code"], "NOT_IMPLEMENTED")

    async def test_opaque_embedded_frame_origin_is_allowed_by_cors(self) -> None:
        response = await self.client.options(
            "/api/projects",
            headers={
                "Origin": "null",
                "Access-Control-Request-Method": "GET",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("access-control-allow-origin"), "null")

    async def test_codex_project_sync_is_idempotent_and_preserves_preferences(self) -> None:
        first_workspace = Path(self.temp_dir.name) / "first-project"
        second_workspace = Path(self.temp_dir.name) / "second-project"
        first_workspace.mkdir()
        second_workspace.mkdir()
        payload = {
            "projects": [
                {
                    "id": "codex-local",
                    "name": "Local project",
                    "projectKind": "local",
                    "workspacePath": str(first_workspace),
                    "hostId": "local",
                },
                {
                    "id": "codex-remote",
                    "name": "Remote project",
                    "projectKind": "remote",
                    "workspacePath": "/srv/remote-project",
                    "hostId": "remote-ssh-discovered:example",
                },
            ],
            "selectedProjectId": "codex-local",
        }
        synced = await self.client.post("/api/codex/projects/sync", json=payload)
        self.assertEqual(synced.status_code, 200)
        body = synced.json()
        self.assertEqual(len(body["projects"]), 1)
        project = body["projects"][0]
        self.assertEqual(body["selectedProjectId"], project["id"])
        self.assertEqual(project["codexProjectId"], "codex-local")
        self.assertTrue(project["key"])
        self.assertFalse(project["automationEnabled"])
        self.assertTrue(project["reviewRequired"])
        self.assertTrue(project["quotaAutoResumeEnabled"])

        changed = await self.client.patch(
            f"/api/projects/{project['id']}",
            json={
                "version": project["version"],
                "automationEnabled": True,
                "reviewRequired": False,
                "quotaAutoResumeEnabled": False,
            },
        )
        self.assertEqual(changed.status_code, 200)

        payload["projects"][0]["name"] = "Renamed project"
        payload["projects"][0]["workspacePath"] = str(second_workspace)
        updated = await self.client.post("/api/codex/projects/sync", json=payload)
        self.assertEqual(updated.status_code, 200)
        updated_project = updated.json()["projects"][0]
        self.assertEqual(updated_project["id"], project["id"])
        self.assertEqual(updated_project["key"], project["key"])
        self.assertEqual(updated_project["name"], "Renamed project")
        self.assertEqual(updated_project["workspacePath"], str(second_workspace))
        self.assertTrue(updated_project["automationEnabled"])
        self.assertFalse(updated_project["reviewRequired"])
        self.assertFalse(updated_project["quotaAutoResumeEnabled"])

        repeated = await self.client.post("/api/codex/projects/sync", json=payload)
        self.assertEqual(repeated.status_code, 200)
        repeated_project = repeated.json()["projects"][0]
        self.assertEqual(repeated_project["id"], updated_project["id"])
        self.assertEqual(repeated_project["version"], updated_project["version"])

    def test_codex_project_fallback_reads_only_top_level_local_projects(self) -> None:
        fixture_root = Path(self.temp_dir.name) / "fixture-project"
        fixture_root.mkdir()
        state_path = Path(self.temp_dir.name) / ".codex-global-state.json"
        state_path.write_text(
            json.dumps(
                {
                    "local-projects": {
                        "codex-fixture": {"rootPaths": [str(fixture_root)]},
                    },
                    "nested": {
                        "projects": [{
                            "id": "must-not-be-read",
                            "rootPaths": [str(fixture_root)],
                        }],
                    },
                }
            ),
            encoding="utf-8",
        )
        with patch.dict(os.environ, {"CODEX_HOME": self.temp_dir.name}), patch(
            "codex_taskboard.app.Path.home", return_value=Path(self.temp_dir.name)
        ):
            projects = _read_codex_projects()
        self.assertEqual(
            projects,
            [
                {
                    "id": "codex-fixture",
                    "codexProjectId": "codex-fixture",
                    "name": "fixture-project",
                    "workspacePath": str(fixture_root),
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
