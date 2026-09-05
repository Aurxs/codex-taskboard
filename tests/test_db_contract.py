from __future__ import annotations

import base64
from pathlib import Path
import unittest
from tempfile import TemporaryDirectory

from codex_taskboard.constants import Priority, TaskStatus
from codex_taskboard.db import Database
from codex_taskboard.errors import ConflictError, ValidationError


class DatabaseContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.db = Database(f"{self.temp_dir.name}/taskboard.sqlite3")

    def tearDown(self) -> None:
        self.db.close()
        self.temp_dir.cleanup()

    def project(self, key: str = "APP", **kwargs):
        return self.db.create_project(
            key=key,
            name=kwargs.pop("name", key),
            workspace_path=self.temp_dir.name,
            **kwargs,
        )

    def task(self, project_id: str, title: str, priority: str = Priority.NONE.value):
        return self.db.create_task(project_id=project_id, title=title, priority=priority)

    def set_created_at(self, task_id: str, value: str) -> None:
        with self.db.transaction(immediate=True):
            self.db._conn.execute(
                "UPDATE tasks SET created_at = ?, updated_at = ? WHERE id = ?",
                (value, value, task_id),
            )

    def test_completed_time_survives_later_edits(self):
        project = self.project()
        task = self.task(project["id"], "completion time")
        self.assertIsNone(task["completedAt"])
        done = self.db.update_task(task["id"], task["version"], status="done")
        self.assertEqual(done["completedAt"], done["updatedAt"])
        edited = self.db.update_task(done["id"], done["version"], title="edited")
        self.assertEqual(edited["completedAt"], done["completedAt"])
        repeated = self.db.update_task(edited["id"], edited["version"], status="done")
        self.assertEqual(repeated["completedAt"], done["completedAt"])

    def test_completion_time_migration_backfills_existing_done_tasks(self):
        project = self.project()
        task = self.task(project["id"], "legacy completion")
        done = self.db.update_task(task["id"], task["version"], status="done")
        with self.db.transaction(immediate=True):
            self.db._conn.execute("ALTER TABLE tasks DROP COLUMN completed_at")
            self.db._conn.execute("UPDATE schema_meta SET version = 5")
        self.db.close()
        self.db = Database(f"{self.temp_dir.name}/taskboard.sqlite3")
        self.assertEqual(self.db.get_task(done["id"])["completedAt"], done["updatedAt"])

    def test_draft_is_not_claimed_until_published(self):
        project = self.project()
        draft = self.task(project["id"], "draft", "draft")
        self.assertFalse(draft["ready"])
        self.assertIsNone(self.db.claim_next_ready(project["id"]))
        with self.assertRaises(ValidationError):
            self.db.claim_task(draft["id"], draft["version"])
        ready = self.db.update_task(draft["id"], draft["version"], priority="high")
        self.assertTrue(ready["ready"])
        self.assertEqual(self.db.claim_next_ready(project["id"])["id"], draft["id"])
        with self.assertRaises(ValidationError):
            self.db.update_task(ready["id"], ready["version"] + 1, priority="draft")

    def test_attachments_are_atomic_persistent_and_available_to_codex(self):
        project = self.project()
        content = "# 验收标准\n保留图片".encode()
        task = self.db.create_task(project_id=project["id"], title="files", attachments=[
            {"name": "说明.md", "content": base64.b64encode(content).decode()}])
        attachment = task["attachments"][0]
        self.assertEqual(attachment["size"], len(content))
        self.db.close()
        self.db = Database(f"{self.temp_dir.name}/taskboard.sqlite3")
        self.assertEqual(self.db.get_attachment(attachment["id"]), ("说明.md", content))
        path = self.db.attachment_prompt(task["id"]).splitlines()[-1]
        self.assertEqual(Path(path).read_bytes(), content)
        for name, encoded in [("../bad.md", "YQ=="), ("a.md", "bad!"), ("a.html", "YQ==")]:
            with self.assertRaises(ValidationError):
                self.db.create_task(project_id=project["id"], title="invalid", attachments=[{"name": name, "content": encoded}])
        self.assertEqual(len(self.db.list_tasks(project["id"])), 1)
        self.db.delete_task(task["id"], task["version"])
        self.assertEqual(self.db._conn.execute("SELECT count(*) FROM task_attachments").fetchone()[0], 0)

    def test_v4_migration_preserves_tasks_dependencies_and_activity(self):
        project = self.project()
        first = self.task(project["id"], "first")
        second = self.task(project["id"], "second")
        self.db.replace_dependencies(second["id"], second["version"], [first["id"]])
        self.db.save_activity(first["id"], {"id": "event", "message": "saved"})
        conn = self.db._conn
        conn.execute("PRAGMA foreign_keys = OFF")
        schema = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'tasks'").fetchone()[0]
        schema = schema.replace('CREATE TABLE "tasks"', 'CREATE TABLE tasks_old').replace(", 'draft'", "")
        conn.execute(schema)
        conn.execute("INSERT INTO tasks_old SELECT * FROM tasks")
        conn.execute("DROP TABLE tasks")
        conn.execute("ALTER TABLE tasks_old RENAME TO tasks")
        conn.execute("DROP TABLE task_attachments")
        conn.execute("ALTER TABLE tasks DROP COLUMN completed_at")
        conn.execute("UPDATE schema_meta SET version = 4")
        conn.execute("PRAGMA foreign_keys = ON")
        self.db.close()
        self.db = Database(f"{self.temp_dir.name}/taskboard.sqlite3")
        self.assertEqual(self.db.get_task(second["id"])["blockedBy"][0]["id"], first["id"])
        self.assertEqual(self.db.list_activity(first["id"])[0]["message"], "saved")
        self.assertFalse(self.task(project["id"], "draft", "draft")["ready"])
        self.assertEqual(self.db._conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_self_cross_project_and_indirect_cycles_are_rejected(self) -> None:
        app = self.project()
        other = self.project("OTHER")
        first = self.task(app["id"], "first")
        second = self.task(app["id"], "second")
        third = self.task(app["id"], "third")
        foreign = self.task(other["id"], "foreign")

        with self.assertRaises(ValidationError):
            self.db.replace_dependencies(first["id"], first["version"], [first["id"]])
        with self.assertRaises(ValidationError):
            self.db.replace_dependencies(first["id"], first["version"], [foreign["id"]])

        second = self.db.replace_dependencies(second["id"], second["version"], [first["id"]])
        third = self.db.replace_dependencies(third["id"], third["version"], [second["id"]])
        with self.assertRaises(ValidationError):
            self.db.replace_dependencies(first["id"], first["version"], [third["id"]])

    def test_all_blockers_must_be_done_before_ready_or_claim(self) -> None:
        app = self.project()
        blocker_a = self.task(app["id"], "blocker-a")
        blocker_b = self.task(app["id"], "blocker-b")
        target = self.db.replace_dependencies(
            self.task(app["id"], "target")["id"],
            1,
            [blocker_a["id"], blocker_b["id"], blocker_a["id"]],
        )
        self.assertFalse(target["ready"])
        with self.assertRaises(ValidationError):
            self.db.claim_task(target["id"], target["version"])

        blocker_a = self.db.update_task(
            blocker_a["id"], blocker_a["version"], status=TaskStatus.DONE.value
        )
        target = self.db.get_task(target["id"])
        self.assertFalse(target["ready"])
        with self.assertRaises(ValidationError):
            self.db.claim_task(target["id"], target["version"])

        self.db.update_task(blocker_b["id"], blocker_b["version"], status=TaskStatus.DONE.value)
        target = self.db.get_task(target["id"])
        self.assertTrue(target["ready"])
        claimed = self.db.claim_task(target["id"], target["version"])
        self.assertEqual(claimed["id"], target["id"])
        self.assertEqual(claimed["status"], TaskStatus.IN_PROGRESS.value)

    def test_canceled_blocker_does_not_unlock_dependents(self) -> None:
        app = self.project()
        blocker = self.task(app["id"], "canceled blocker")
        target = self.task(app["id"], "target")
        target = self.db.replace_dependencies(target["id"], target["version"], [blocker["id"]])
        self.db.update_task(blocker["id"], blocker["version"], status=TaskStatus.CANCELED.value)
        target = self.db.get_task(target["id"])
        self.assertFalse(target["ready"])
        self.assertIsNone(self.db.claim_next_ready(app["id"]))

    def test_priority_fifo_single_claim_and_cross_project_parallelism(self) -> None:
        app = self.project()
        other = self.project("OTHER")
        old_medium = self.task(app["id"], "old medium", Priority.MEDIUM.value)
        urgent = self.task(app["id"], "urgent", Priority.URGENT.value)
        same_first = self.task(app["id"], "same first", Priority.HIGH.value)
        same_second = self.task(app["id"], "same second", Priority.HIGH.value)
        other_task = self.task(other["id"], "other", Priority.LOW.value)
        self.set_created_at(old_medium["id"], "2020-01-01T00:00:00.000+00:00")
        self.set_created_at(urgent["id"], "2020-01-02T00:00:00.000+00:00")
        self.set_created_at(same_first["id"], "2020-01-03T00:00:00.000+00:00")
        self.set_created_at(same_second["id"], "2020-01-04T00:00:00.000+00:00")

        first = self.db.claim_next_ready(app["id"])
        self.assertEqual(first["id"], urgent["id"])
        self.assertIsNone(self.db.claim_next_ready(app["id"]))
        first = self.db.update_task(first["id"], first["version"], status=TaskStatus.DONE.value)

        second = self.db.claim_next_ready(app["id"])
        self.assertEqual(second["id"], same_first["id"])
        second = self.db.update_task(second["id"], second["version"], status=TaskStatus.DONE.value)
        third = self.db.claim_next_ready(app["id"])
        self.assertEqual(third["id"], same_second["id"])

        parallel = self.db.claim_next_ready(other["id"])
        self.assertEqual(parallel["id"], other_task["id"])

    def test_settings_defaults_and_optimistic_version_conflict(self) -> None:
        project = self.project()
        self.assertFalse(project["automationEnabled"])
        self.assertTrue(project["reviewRequired"])
        self.assertTrue(project["quotaAutoResumeEnabled"])
        changed = self.db.update_project(
            project["id"],
            project["version"],
            quota_auto_resume_enabled=False,
        )
        self.assertFalse(changed["quotaAutoResumeEnabled"])
        with self.assertRaises(ConflictError):
            self.db.update_project(
                project["id"], project["version"], automation_enabled=True
            )


if __name__ == "__main__":
    unittest.main()
