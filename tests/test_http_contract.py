from __future__ import annotations

import json
import os
from pathlib import Path
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, Mock, patch

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

    async def test_followup_passes_only_explicit_attachment_ids(self):
        db = self.app.state.db
        project = db.create_project(key="FOLLOWUP", name="Followup", workspace_path=self.temp_dir.name)
        task = db.create_task(project_id=project["id"], title="Followup", attachments=[
            {"name": "notes.md", "content": "YQ=="}])
        ids = [task["attachments"][0]["id"]]
        for extra, expected in [({}, []), ({"attachmentIds": ids}, ids)]:
            with patch.object(self.app.state.scheduler, "action", new=AsyncMock(return_value=task)) as action:
                response = await self.client.post(f"/api/tasks/{task['id']}/actions", json={
                    "action": "follow_up", "version": task["version"], "feedback": "补充", **extra})
                self.assertEqual(response.status_code, 200)
                action.assert_awaited_once_with(task["id"], "follow_up", task["version"], "补充",
                                               target_status=None, attachment_ids=expected)

    async def test_create_draft_with_attachment_and_download(self):
        project = self.app.state.db.create_project(key="FILES", name="Files", workspace_path=self.temp_dir.name)
        response = await self.client.post(f"/api/projects/{project['id']}/tasks", json={
            "title": "Draft", "priority": "draft", "attachments": [{"name": "notes.md", "content": "IyBIZWxsbw=="}]})
        self.assertEqual(response.status_code, 201)
        task = response.json()
        self.assertFalse(task["ready"])
        download = await self.client.get(task["attachments"][0]["url"])
        self.assertEqual(download.content, b"# Hello")
        self.assertIn("attachment;", download.headers["content-disposition"])
        invalid = await self.client.post(f"/api/projects/{project['id']}/tasks", json={
            "title": "Invalid", "attachments": [{"name": "../notes.md", "content": "YQ=="}]})
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(len(self.app.state.db.list_tasks(project["id"])), 1)

    async def test_local_attachment_preview_and_external_open(self):
        db = self.app.state.db
        project = db.create_project(key="PREVIEW", name="Preview", workspace_path=self.temp_dir.name)
        task = db.create_task(project_id=project["id"], title="Preview", attachments=[
            {"name": "notes.md", "content": "IyBIZWxsbw=="},
            {"name": "image.png", "content": "YWJj"},
            {"name": "slides.pptx", "content": "YWJj"},
            {"name": "document.docx", "content": "YWJj"},
        ])
        markdown, image, slides, document = task["attachments"]
        response = await self.client.get(markdown["url"] + "/preview")
        self.assertEqual(response.json(), {"kind": "markdown", "content": "# Hello"})
        self.assertEqual(db.attachment_path(markdown["id"]).read_bytes(), b"# Hello")
        response = await self.client.get(image["url"] + "/preview")
        self.assertEqual(response.json(), {"kind": "image", "content": "data:image/png;base64,YWJj"})
        with patch("codex_taskboard.app.sys.platform", "darwin"), patch("codex_taskboard.app.subprocess.run") as run:
            for file in (slides, document):
                response = await self.client.get(file["url"] + "/preview")
                self.assertEqual(response.json()["kind"], "external")
                self.assertEqual(Path(response.json()["content"]).read_bytes(), b"abc")
            run.assert_not_called()
            run.return_value.returncode = 0
            response = await self.client.post(slides["url"] + "/open")
            self.assertEqual(response.json(), {"opened": True})
            self.assertEqual(run.call_args.args[0], ["/usr/bin/open", str(db.attachment_path(slides["id"]))])
            run.reset_mock()
            response = await self.client.post(markdown["url"] + "/open")
            self.assertEqual(response.status_code, 422)
            run.assert_not_called()
            run.return_value.returncode = 1
            response = await self.client.post(document["url"] + "/open")
            self.assertEqual(response.status_code, 400)
            self.assertIn("系统默认应用", response.json()["error"]["message"])
        response = await self.client.get("/api/attachments/missing/preview")
        self.assertEqual(response.status_code, 404)

    async def test_append_attachments_is_atomic_and_versioned(self):
        project = self.app.state.db.create_project(key="APPEND", name="Append", workspace_path=self.temp_dir.name)
        task = self.app.state.db.create_task(project_id=project["id"], title="Draft", priority="draft",
                                             attachments=[{"name": "first.md", "content": "YQ=="}])
        url = f"/api/tasks/{task['id']}"
        response = await self.client.patch(url, json={"version": task["version"],
            "attachments": [{"name": "second.png", "content": "Yg=="}]})
        self.assertEqual(response.status_code, 200)
        updated = response.json()
        self.assertEqual([item["name"] for item in updated["attachments"]], ["first.md", "second.png"])
        self.assertEqual(updated["version"], task["version"] + 1)
        download = await self.client.get(updated["attachments"][1]["url"])
        self.assertEqual(download.content, b"b")
        conflict = await self.client.patch(url, json={"version": task["version"],
            "attachments": [{"name": "stale.md", "content": "YQ=="}]})
        self.assertEqual(conflict.status_code, 409)
        for files in ([{"name": "valid.md", "content": "YQ=="}, {"name": "bad.exe", "content": "YQ=="}],
                      [{"name": "extra.md", "content": "YQ=="}] * 9):
            rejected = await self.client.patch(url, json={"version": updated["version"], "attachments": files})
            self.assertEqual(rejected.status_code, 422)
        current = self.app.state.db.get_task(task["id"])
        self.assertEqual(current["attachments"], updated["attachments"])
        self.assertEqual(current["version"], updated["version"])

    async def test_remove_attachments_is_persistent_and_versioned(self):
        db = self.app.state.db
        project = db.create_project(key="REMOVE", name="Remove", workspace_path=self.temp_dir.name)
        task = db.create_task(project_id=project["id"], title="Files", priority="draft", attachments=[
            {"name": "first.md", "content": "YQ=="}, {"name": "second.md", "content": "Yg=="}])
        first, second = task["attachments"]
        url = f"/api/tasks/{task['id']}"
        response = await self.client.patch(url, json={"version": task["version"], "removeAttachmentIds": [first["id"]]})
        self.assertEqual(response.status_code, 200)
        updated = response.json()
        self.assertEqual(updated["attachments"], [second])
        self.assertEqual(updated["version"], task["version"] + 1)
        self.assertEqual((await self.client.get(url)).json()["attachments"], [second])
        self.assertEqual((await self.client.get(first["url"])).status_code, 404)
        self.assertEqual((await self.client.get(first["url"] + "/preview")).status_code, 404)
        self.assertEqual((await self.client.get(second["url"])).content, b"b")
        self.assertNotIn(first["id"], db.attachment_prompt(task["id"]))
        duplicate = await self.client.patch(url, json={"version": task["version"], "removeAttachmentIds": [first["id"]]})
        self.assertEqual(duplicate.status_code, 409)
        conflict = await self.client.patch(url, json={"version": task["version"], "removeAttachmentIds": [second["id"]]})
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(db.get_task(task["id"])["attachments"], [second])
        response = await self.client.patch(url, json={"version": updated["version"], "removeAttachmentIds": [second["id"]]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["attachments"], [])
        self.assertEqual(db.attachment_prompt(task["id"]), "")

    async def test_remove_attachments_checks_ownership_and_rolls_back_invalid_updates(self):
        db = self.app.state.db
        project = db.create_project(key="ATOMIC", name="Atomic", workspace_path=self.temp_dir.name)
        files = [{"name": f"{i}.md", "content": "YQ=="} for i in range(10)]
        task = db.create_task(project_id=project["id"], title="Files", priority="draft", attachments=files)
        other = db.create_task(project_id=project["id"], title="Other", priority="draft", attachments=files[:1])
        first = task["attachments"][0]
        url = f"/api/tasks/{task['id']}"
        for changes in (
            {"removeAttachmentIds": [first["id"], other["attachments"][0]["id"]]},
            {"removeAttachmentIds": [first["id"], "missing"]},
            {"removeAttachmentIds": [first["id"]], "attachments": files[:2]},
        ):
            response = await self.client.patch(url, json={"version": task["version"], "title": "Changed", **changes})
            self.assertEqual(response.status_code, 422)
            current = db.get_task(task["id"])
            self.assertEqual(current["attachments"], task["attachments"])
            self.assertEqual(current["version"], task["version"])
            self.assertEqual(current["title"], task["title"])
        self.assertEqual(db.get_task(other["id"])["attachments"], other["attachments"])
        response = await self.client.patch(url, json={"version": task["version"],
            "removeAttachmentIds": [first["id"]], "attachments": [{"name": "replacement.md", "content": "Yg=="}]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["attachments"]), 10)
        self.assertEqual(response.json()["attachments"][-1]["name"], "replacement.md")

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

    async def test_directory_picker_uses_interface_language(self) -> None:
        result = Mock(returncode=0, stdout="/tmp/demo\n", stderr="")
        for locale, expected in [("zh-CN", "选择 Codex 项目目录"), ("en", "Choose a Codex project directory")]:
            with patch("codex_taskboard.app.sys.platform", "darwin"), patch(
                "codex_taskboard.app.subprocess.run", return_value=result
            ) as run:
                response = await self.client.post(
                    "/api/system/pick-directory", headers={"Accept-Language": locale}
                )
            self.assertEqual(response.status_code, 200)
            self.assertIn(expected, run.call_args.args[0][-1])

    async def test_directory_picker_is_explicitly_unsupported_off_desktop_platforms(self) -> None:
        with patch("codex_taskboard.app.sys.platform", "linux"):
            response = await self.client.post("/api/system/pick-directory")
        self.assertEqual(response.status_code, 501)
        self.assertEqual(response.json()["error"]["code"], "NOT_IMPLEMENTED")

    async def test_windows_directory_picker_preserves_drive_root_and_cancel(self) -> None:
        for workspace in ("C:\\", "C:\\项目 空格"):
            with patch("codex_taskboard.app.sys.platform", "win32"), patch(
                "codex_taskboard.platforms.windows.pick_directory",
                return_value=Mock(returncode=0, stdout=workspace),
            ):
                response = await self.client.post("/api/system/pick-directory")
                self.assertEqual(response.json(), {"workspacePath": workspace})
        with patch("codex_taskboard.app.sys.platform", "win32"), patch(
            "codex_taskboard.platforms.windows.pick_directory",
            return_value=Mock(returncode=1, stdout=""),
        ):
            response = await self.client.post("/api/system/pick-directory")
            self.assertEqual(response.status_code, 409)

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
