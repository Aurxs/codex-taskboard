"""Small SQLite persistence layer for the local taskboard.

There is intentionally no ORM here.  The state machine is easier to audit
when the handful of transactions which mutate it are visible SQL operations.
All public writers take an expected version and use ``BEGIN IMMEDIATE`` for
the claim/dependency transactions so two scheduler loops cannot claim the
same task.
"""

from __future__ import annotations

import base64
import binascii
import json
import hashlib
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .constants import ALL_PRIORITIES, ALL_STATUSES, Priority, TaskStatus
from .errors import ConflictError, NotFoundError, ValidationError

SCHEMA_VERSION = 6
_UNSET = object()
_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
_KEY_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_-]+")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_id() -> str:
    return str(uuid.uuid4())


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_load(value: str | None, default: Any = None) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


class Database:
    """Thread-safe SQLite connection with explicit schema migrations."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            self.path,
            check_same_thread=False,
            timeout=10,
            isolation_level=None,
        )
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA busy_timeout = 10000")
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._migrate()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            try:
                yield self._conn
            except Exception:
                self._conn.rollback()
                raise
            else:
                self._conn.commit()

    def _migrate(self) -> None:
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_meta (singleton INTEGER PRIMARY KEY CHECK (singleton = 1), version INTEGER NOT NULL)"
        )
        row = self._conn.execute(
            "SELECT version FROM schema_meta WHERE singleton = 1"
        ).fetchone()
        current = int(row["version"]) if row else 0
        if current > SCHEMA_VERSION:
            raise RuntimeError(
                f"Database schema {current} is newer than supported {SCHEMA_VERSION}"
            )
        if current < 1:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    key TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    name TEXT NOT NULL,
                    workspace_path TEXT NOT NULL,
                    codex_project_id TEXT,
                    automation_enabled INTEGER NOT NULL DEFAULT 0 CHECK (automation_enabled IN (0, 1)),
                    review_required INTEGER NOT NULL DEFAULT 1 CHECK (review_required IN (0, 1)),
                    next_task_number INTEGER NOT NULL DEFAULT 1,
                    version INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    identifier TEXT NOT NULL UNIQUE,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    priority TEXT NOT NULL DEFAULT 'none' CHECK (priority IN ('urgent', 'high', 'medium', 'low', 'none')),
                    status TEXT NOT NULL DEFAULT 'todo' CHECK (status IN ('todo', 'in_progress', 'in_review', 'done', 'canceled')),
                    thread_id TEXT UNIQUE,
                    run_state TEXT CHECK (run_state IS NULL OR run_state IN ('starting', 'running', 'waiting_quota', 'waiting_approval', 'waiting_input', 'failed')),
                    last_message TEXT,
                    last_error TEXT,
                    version INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS task_dependencies (
                    blocker_task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    blocked_task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (blocker_task_id, blocked_task_id),
                    CHECK (blocker_task_id <> blocked_task_id)
                );

                CREATE TABLE IF NOT EXISTS task_runs (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    thread_id TEXT,
                    turn_id TEXT,
                    run_state TEXT NOT NULL,
                    structured_error TEXT,
                    last_output_summary TEXT,
                    resume_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS interactions (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    blocking_scope TEXT NOT NULL DEFAULT 'task',
                    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'resolved', 'canceled')),
                    response TEXT,
                    version INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_tasks_project_status ON tasks(project_id, status);
                CREATE INDEX IF NOT EXISTS idx_tasks_project_created ON tasks(project_id, created_at, id);
                CREATE INDEX IF NOT EXISTS idx_dependencies_blocked ON task_dependencies(blocked_task_id);
                CREATE INDEX IF NOT EXISTS idx_task_runs_task ON task_runs(task_id, updated_at);
                CREATE INDEX IF NOT EXISTS idx_interactions_task_status ON interactions(task_id, status);
                """
            )
            self._conn.execute(
                "INSERT INTO schema_meta(singleton, version) VALUES (1, 1)"
            )
            current = 1
        # Keep this branch explicit so adding a future migration cannot be
        # accidentally hidden in the initial schema.
        if current < 2:
            # Keep quota recovery independently switchable from the general
            # automation toggle.  Existing installations retain the safe,
            # useful default of resuming automatically.
            columns = {
                row["name"]
                for row in self._conn.execute("PRAGMA table_info(projects)").fetchall()
            }
            if "quota_auto_resume_enabled" not in columns:
                self._conn.execute(
                    "ALTER TABLE projects ADD COLUMN quota_auto_resume_enabled INTEGER NOT NULL DEFAULT 1"
                )
            self._conn.execute(
                "UPDATE schema_meta SET version = 2 WHERE singleton = 1"
            )
            current = 2
        if current < 3:
            self._conn.execute("ALTER TABLE tasks ADD COLUMN model TEXT")
            self._conn.execute("ALTER TABLE tasks ADD COLUMN reasoning_effort TEXT")
            self._conn.execute("UPDATE schema_meta SET version = 3 WHERE singleton = 1")
            current = 3
        if current < 4:
            self._conn.execute("""CREATE TABLE task_activity (
                task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                id TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY (task_id, id))""")
            self._conn.execute("UPDATE schema_meta SET version = 4 WHERE singleton = 1")
            current = 4
        if current < 5:
            # Rebuild the CHECK constraint while preserving dependent tables and indexes.
            self._conn.execute("PRAGMA foreign_keys = OFF")
            try:
                with self.transaction(immediate=True):
                    schema = self._conn.execute("SELECT sql FROM sqlite_master WHERE name = 'tasks'").fetchone()[0]
                    indexes = self._conn.execute("SELECT sql FROM sqlite_master WHERE type = 'index' AND tbl_name = 'tasks' AND sql IS NOT NULL").fetchall()
                    schema = re.sub(r'CREATE TABLE ["`\[]?tasks["`\]]?', "CREATE TABLE tasks_new", schema, count=1).replace("'low', 'none'", "'low', 'none', 'draft'")
                    self._conn.execute(schema)
                    self._conn.execute("INSERT INTO tasks_new SELECT * FROM tasks")
                    self._conn.execute("DROP TABLE tasks")
                    self._conn.execute("ALTER TABLE tasks_new RENAME TO tasks")
                    for index in indexes:
                        self._conn.execute(index[0])
                    self._conn.execute("""CREATE TABLE task_attachments (
                        id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                        name TEXT NOT NULL, content BLOB NOT NULL)""")
                    if self._conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
                        raise RuntimeError("Migration would violate foreign keys")
                    self._conn.execute("UPDATE schema_meta SET version = 5 WHERE singleton = 1")
            finally:
                self._conn.execute("PRAGMA foreign_keys = ON")
            current = 5
        if current < 6:
            with self.transaction(immediate=True):
                self._conn.execute("ALTER TABLE tasks ADD COLUMN completed_at TEXT")
                self._conn.execute("UPDATE tasks SET completed_at = updated_at WHERE status = 'done'")
                self._conn.execute("UPDATE schema_meta SET version = 6 WHERE singleton = 1")
            current = 6
        if current != SCHEMA_VERSION:
            raise RuntimeError(f"No migration path from schema {current}")

    # ------------------------------------------------------------------
    # Row conversion helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _project_json(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "key": row["key"],
            "name": row["name"],
            "workspacePath": row["workspace_path"],
            "codexProjectId": row["codex_project_id"],
            "automationEnabled": bool(row["automation_enabled"]),
            "reviewRequired": bool(row["review_required"]),
            "quotaAutoResumeEnabled": bool(row["quota_auto_resume_enabled"]),
            "version": int(row["version"]),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    @staticmethod
    def _summary(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "identifier": row["identifier"],
            "title": row["title"],
            "status": row["status"],
        }

    def _task_json_locked(self, task_id: str) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError(f"Task {task_id} was not found")
        blocked_by = self._conn.execute(
            """
            SELECT t.id, t.identifier, t.title, t.status
            FROM task_dependencies d
            JOIN tasks t ON t.id = d.blocker_task_id
            WHERE d.blocked_task_id = ?
            ORDER BY t.created_at, t.id
            """,
            (task_id,),
        ).fetchall()
        blocks = self._conn.execute(
            """
            SELECT t.id, t.identifier, t.title, t.status
            FROM task_dependencies d
            JOIN tasks t ON t.id = d.blocked_task_id
            WHERE d.blocker_task_id = ?
            ORDER BY t.created_at, t.id
            """,
            (task_id,),
        ).fetchall()
        ready = row["status"] == TaskStatus.TODO.value and row["priority"] != "draft" and all(
            blocker["status"] == TaskStatus.DONE.value for blocker in blocked_by
        )
        return {
            "id": row["id"],
            "identifier": row["identifier"],
            "projectId": row["project_id"],
            "title": row["title"],
            "description": row["description"],
            "attachments": [{"id": a["id"], "name": a["name"], "size": a["size"],
                             "url": f"/api/attachments/{a['id']}"}
                            for a in self._conn.execute("SELECT id, name, length(content) AS size FROM task_attachments WHERE task_id = ? ORDER BY rowid", (task_id,))],
            "priority": row["priority"],
            "model": row["model"],
            "reasoningEffort": row["reasoning_effort"],
            "status": row["status"],
            "version": int(row["version"]),
            "threadId": row["thread_id"],
            "runState": row["run_state"],
            "lastMessage": row["last_message"],
            "lastError": _json_load(row["last_error"], row["last_error"]),
            "blockedBy": [self._summary(item) for item in blocked_by],
            "blocks": [self._summary(item) for item in blocks],
            "ready": ready,
            "completedAt": row["completed_at"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    @staticmethod
    def _task_summary_for_event(task: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": task["id"],
            "identifier": task["identifier"],
            "projectId": task["projectId"],
            "status": task["status"],
            "version": task["version"],
        }

    # ------------------------------------------------------------------
    # Projects
    # ------------------------------------------------------------------
    def list_projects(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM projects ORDER BY created_at, id"
            ).fetchall()
            return [self._project_json(row) for row in rows]

    def get_project(self, project_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
            if row is None:
                raise NotFoundError(f"Project {project_id} was not found")
            return self._project_json(row)

    def create_project(
        self,
        *,
        key: str,
        name: str,
        workspace_path: str,
        codex_project_id: str | None = None,
        automation_enabled: bool = False,
        review_required: bool = True,
        quota_auto_resume_enabled: bool = True,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        key = key.strip()
        name = name.strip()
        workspace_path = str(Path(workspace_path).expanduser())
        if not _KEY_RE.fullmatch(key):
            raise ValidationError(
                "Project key must start with a letter and contain at most 32 letters, numbers, '-' or '_'."
            )
        if not name:
            raise ValidationError("Project name cannot be empty")
        if not workspace_path or "\x00" in workspace_path:
            raise ValidationError("workspacePath must be a valid non-empty path")
        project_id = project_id or new_id()
        now = utc_now()
        with self.transaction(immediate=True):
            try:
                self._conn.execute(
                    """
                    INSERT INTO projects (
                        id, key, name, workspace_path, codex_project_id,
                        automation_enabled, review_required, quota_auto_resume_enabled,
                        next_task_number,
                        version, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 1, ?, ?)
                    """,
                    (
                        project_id,
                        key,
                        name,
                        workspace_path,
                        codex_project_id,
                        int(automation_enabled),
                        int(review_required),
                        int(quota_auto_resume_enabled),
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                if "projects.key" in str(exc) or "UNIQUE constraint failed: projects.key" in str(exc):
                    raise ConflictError(f"Project key {key!r} is already in use") from exc
                raise
            row = self._conn.execute(
                "SELECT * FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
            assert row is not None
            return self._project_json(row)

    def upsert_codex_project(
        self,
        *,
        codex_project_id: str,
        name: str,
        workspace_path: str,
    ) -> dict[str, Any]:
        """Synchronize one local project reported by the Codex renderer.

        Codex's project id is the durable identity for an imported project.
        Existing rows are updated only for the host-owned name and workspace;
        Taskboard preferences remain untouched.  The whole lookup/update or
        insert runs in one immediate transaction so concurrent host-context
        syncs cannot create duplicate rows.
        """
        codex_project_id = codex_project_id.strip()
        name = name.strip()
        workspace_path = str(Path(workspace_path).expanduser()).strip()
        if not codex_project_id:
            raise ValidationError("codexProjectId must be a non-empty string")
        if not workspace_path or "\x00" in workspace_path:
            raise ValidationError("workspacePath must be a valid non-empty path")
        if not name:
            name = Path(workspace_path).name or "Codex project"

        now = utc_now()
        with self.transaction(immediate=True):
            row = self._conn.execute(
                """
                SELECT * FROM projects
                WHERE codex_project_id = ?
                ORDER BY created_at, id
                LIMIT 1
                """,
                (codex_project_id,),
            ).fetchone()
            if row is not None:
                changes: dict[str, Any] = {}
                if row["name"] != name:
                    changes["name"] = name
                if row["workspace_path"] != workspace_path:
                    changes["workspace_path"] = workspace_path
                if changes:
                    changes["updated_at"] = now
                    assignments = ", ".join(f"{column} = ?" for column in changes)
                    self._conn.execute(
                        f"""
                        UPDATE projects
                        SET {assignments}, version = version + 1
                        WHERE id = ?
                        """,
                        [*changes.values(), row["id"]],
                    )
                updated = self._conn.execute(
                    "SELECT * FROM projects WHERE id = ?", (row["id"],)
                ).fetchone()
                assert updated is not None
                return self._project_json(updated)

            project_id = new_id()
            key = self._generated_codex_key_locked(
                name=name,
                workspace_path=workspace_path,
                codex_project_id=codex_project_id,
            )
            self._conn.execute(
                """
                INSERT INTO projects (
                    id, key, name, workspace_path, codex_project_id,
                    automation_enabled, review_required, quota_auto_resume_enabled,
                    next_task_number, version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 0, 1, 1, 1, 1, ?, ?)
                """,
                (
                    project_id,
                    key,
                    name,
                    workspace_path,
                    codex_project_id,
                    now,
                    now,
                ),
            )
            created = self._conn.execute(
                "SELECT * FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
            assert created is not None
            return self._project_json(created)

    def _generated_codex_key_locked(
        self,
        *,
        name: str,
        workspace_path: str,
        codex_project_id: str,
    ) -> str:
        """Return a readable, deterministic and database-unique project key."""
        source = name.strip() or Path(workspace_path).name or "CODEX"
        base = _KEY_SANITIZE_RE.sub("-", source).strip("-_").upper()
        if not base:
            base = "CODEX"
        if not base[0].isalpha():
            base = f"P-{base}"
        # Keep the key stable across sync order/name changes while retaining
        # enough of the human-readable project name for the UI.
        suffix = hashlib.sha1(codex_project_id.encode("utf-8")).hexdigest()[:6].upper()
        candidate = f"{base[:25].rstrip('-_')}-{suffix}"
        if not _KEY_RE.fullmatch(candidate):
            candidate = f"P-{suffix}"

        used = {
            str(row["key"]).upper()
            for row in self._conn.execute("SELECT key FROM projects").fetchall()
        }
        if candidate.upper() not in used:
            return candidate
        for index in range(2, 10_000):
            extra = f"-{index}"
            fallback = f"{candidate[:32 - len(extra)]}{extra}"
            if _KEY_RE.fullmatch(fallback) and fallback.upper() not in used:
                return fallback
        raise ConflictError("Unable to generate a unique project key")

    def update_project(
        self,
        project_id: str,
        expected_version: int,
        *,
        key: str | object = _UNSET,
        name: str | object = _UNSET,
        workspace_path: str | object = _UNSET,
        codex_project_id: str | None | object = _UNSET,
        automation_enabled: bool | object = _UNSET,
        review_required: bool | object = _UNSET,
        quota_auto_resume_enabled: bool | object = _UNSET,
    ) -> dict[str, Any]:
        values: dict[str, Any] = {}
        if key is not _UNSET:
            assert isinstance(key, str)
            key = key.strip()
            if not _KEY_RE.fullmatch(key):
                raise ValidationError("Invalid project key")
            values["key"] = key
        if name is not _UNSET:
            assert isinstance(name, str)
            name = name.strip()
            if not name:
                raise ValidationError("Project name cannot be empty")
            values["name"] = name
        if workspace_path is not _UNSET:
            assert isinstance(workspace_path, str)
            workspace_path = str(Path(workspace_path).expanduser())
            if not workspace_path or "\x00" in workspace_path:
                raise ValidationError("Invalid workspacePath")
            values["workspace_path"] = workspace_path
        if codex_project_id is not _UNSET:
            if codex_project_id is not None and not isinstance(codex_project_id, str):
                raise ValidationError("codexProjectId must be a string or null")
            values["codex_project_id"] = codex_project_id
        if automation_enabled is not _UNSET:
            values["automation_enabled"] = int(bool(automation_enabled))
        if review_required is not _UNSET:
            values["review_required"] = int(bool(review_required))
        if quota_auto_resume_enabled is not _UNSET:
            values["quota_auto_resume_enabled"] = int(bool(quota_auto_resume_enabled))
        if not values:
            return self.get_project(project_id)
        values["updated_at"] = utc_now()
        assignments = ", ".join(f"{column} = ?" for column in values)
        params = [*values.values(), project_id, expected_version]
        with self.transaction(immediate=True):
            try:
                cursor = self._conn.execute(
                    f"UPDATE projects SET {assignments}, version = version + 1 WHERE id = ? AND version = ?",
                    params,
                )
            except sqlite3.IntegrityError as exc:
                if "UNIQUE constraint failed" in str(exc):
                    raise ConflictError("Project key is already in use") from exc
                raise
            if cursor.rowcount == 0:
                exists = self._conn.execute(
                    "SELECT version FROM projects WHERE id = ?", (project_id,)
                ).fetchone()
                if exists is None:
                    raise NotFoundError(f"Project {project_id} was not found")
                raise ConflictError(
                    f"Project {project_id} changed; expected version {expected_version}, current {exists['version']}"
                )
            row = self._conn.execute(
                "SELECT * FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
            assert row is not None
            return self._project_json(row)

    def delete_project(self, project_id: str, expected_version: int) -> None:
        with self.transaction(immediate=True):
            row = self._conn.execute(
                "SELECT version FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
            if row is None:
                raise NotFoundError(f"Project {project_id} was not found")
            if int(row["version"]) != expected_version:
                raise ConflictError("Project changed; refresh before deleting")
            self._conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))

    # ------------------------------------------------------------------
    # Tasks and dependency graph
    # ------------------------------------------------------------------
    def list_tasks(
        self,
        project_id: str | None = None,
        *,
        include_canceled: bool = True,
    ) -> list[dict[str, Any]]:
        with self._lock:
            clauses: list[str] = []
            params: list[Any] = []
            if project_id is not None:
                clauses.append("project_id = ?")
                params.append(project_id)
            if not include_canceled:
                clauses.append("status <> 'canceled'")
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            rows = self._conn.execute(
                f"SELECT id FROM tasks {where} ORDER BY created_at, id", params
            ).fetchall()
            return [self._task_json_locked(row["id"]) for row in rows]

    def get_task(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            return self._task_json_locked(task_id)

    def save_activity(self, task_id: str, item: dict[str, Any]) -> None:
        with self._lock:
            existing = self._conn.execute("SELECT payload FROM task_activity WHERE task_id = ? AND id = ?",
                                          (task_id, item["id"])).fetchone()
            if existing:
                previous = json.loads(existing["payload"])
                if previous.get("status") == "completed" and item.get("status") == "running":
                    return
                item = {**item, "data": {**previous.get("data", {}), **item.get("data", {})}}
            self._conn.execute(
                """INSERT INTO task_activity(task_id, id, payload, created_at) VALUES (?, ?, ?, ?)
                ON CONFLICT(task_id, id) DO UPDATE SET payload = excluded.payload""",
                (task_id, item["id"], _json(item), item.get("createdAt") or utc_now()),
            )

    def list_activity(self, task_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload, created_at FROM task_activity WHERE task_id = ? ORDER BY created_at, rowid",
                (task_id,),
            ).fetchall()
            return [{**json.loads(row["payload"]), "createdAt": row["created_at"]} for row in rows]

    def get_task_row(self, task_id: str) -> sqlite3.Row:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if row is None:
                raise NotFoundError(f"Task {task_id} was not found")
            return row

    def create_task(
        self,
        *,
        project_id: str,
        title: str,
        description: str = "",
        priority: str = Priority.NONE.value,
        task_id: str | None = None,
        attachments: list[dict[str, str]] | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> dict[str, Any]:
        title = title.strip()
        description = description or ""
        if not title:
            raise ValidationError("Task title cannot be empty")
        if priority not in ALL_PRIORITIES:
            raise ValidationError(f"Invalid priority {priority!r}")
        prepared = self._prepare_attachments(attachments or [])
        task_id = task_id or new_id()
        now = utc_now()
        with self.transaction(immediate=True):
            project = self._conn.execute(
                "SELECT key, next_task_number FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
            if project is None:
                raise NotFoundError(f"Project {project_id} was not found")
            number = int(project["next_task_number"])
            identifier = f"{project['key']}-{number}"
            self._conn.execute(
                "UPDATE projects SET next_task_number = ?, updated_at = ? WHERE id = ?",
                (number + 1, now, project_id),
            )
            self._conn.execute(
                """
                INSERT INTO tasks (
                    id, identifier, project_id, title, description, priority, model, reasoning_effort,
                    status, version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'todo', 1, ?, ?)
                """,
                (task_id, identifier, project_id, title, description, priority, model, reasoning_effort, now, now),
            )
            self._conn.executemany("INSERT INTO task_attachments(id, task_id, name, content) VALUES (?, ?, ?, ?)",
                                   [(aid, task_id, name, content) for aid, name, content in prepared])
            return self._task_json_locked(task_id)

    @staticmethod
    def _prepare_attachments(attachments: list[dict[str, str]]) -> list[tuple[str, str, bytes]]:
        prepared = []
        total = 0
        if len(attachments or []) > 10:
            raise ValidationError("最多添加 10 个附件")
        for item in attachments or []:
            name = item.get("name", "")
            if not name or len(name) > 255 or any(c in name for c in ("/", "\\", "\x00", "\r", "\n")) or name in {".", ".."}:
                raise ValidationError("附件文件名无效")
            if Path(name).suffix.lower() not in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".md", ".markdown", ".txt", ".pdf", ".ppt", ".pptx", ".doc", ".docx", ".xls", ".xlsx"}:
                raise ValidationError("仅支持图片、Markdown、文本、PDF 和 Office 文档")
            encoded = item.get("content", "")
            if len(encoded) > 14_000_000:
                raise ValidationError("单个附件不能超过 10 MB")
            try:
                content = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error):
                raise ValidationError("附件编码无效") from None
            total += len(content)
            if len(content) > 10 * 1024 * 1024 or total > 20 * 1024 * 1024:
                raise ValidationError("单个附件最多 10 MB，合计最多 20 MB")
            prepared.append((new_id(), name, content))
        return prepared

    def get_attachment(self, attachment_id: str) -> tuple[str, bytes]:
        with self._lock:
            row = self._conn.execute("SELECT name, content FROM task_attachments WHERE id = ?", (attachment_id,)).fetchone()
            if row is None:
                raise NotFoundError("Attachment was not found")
            return row["name"], row["content"]

    def attachment_path(self, attachment_id: str) -> Path:
        name, content = self.get_attachment(attachment_id)
        folder = self.path.resolve().parent / "attachments" / attachment_id
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        if not path.exists() or path.read_bytes() != content:
            path.write_bytes(content)
        return path

    def attachment_prompt(self, task_id: str) -> str:
        task = self.get_task(task_id)
        paths = [str(self.attachment_path(item["id"])) for item in task["attachments"]]
        return "\n\nTask attachments (read the documents or view the images):\n" + "\n".join(paths) if paths else ""

    def update_task(
        self,
        task_id: str,
        expected_version: int,
        *,
        title: str | object = _UNSET,
        description: str | object = _UNSET,
        priority: str | object = _UNSET,
        model: str | None | object = _UNSET,
        reasoning_effort: str | None | object = _UNSET,
        thread_id: str | None | object = _UNSET,
        status: str | object = _UNSET,
        run_state: str | None | object = _UNSET,
        last_message: str | None | object = _UNSET,
        last_error: Any = _UNSET,
        attachments: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        values: dict[str, Any] = {}
        if title is not _UNSET:
            assert isinstance(title, str)
            title = title.strip()
            if not title:
                raise ValidationError("Task title cannot be empty")
            values["title"] = title
        if description is not _UNSET:
            if not isinstance(description, str):
                raise ValidationError("description must be a string")
            values["description"] = description
        if priority is not _UNSET:
            assert isinstance(priority, str)
            if priority not in ALL_PRIORITIES:
                raise ValidationError(f"Invalid priority {priority!r}")
            values["priority"] = priority
        for key, value in (("model", model), ("reasoning_effort", reasoning_effort)):
            if value is not _UNSET:
                if value is not None and (not isinstance(value, str) or not value.strip()):
                    raise ValidationError(f"{key} must be a non-empty string or null")
                values[key] = value
        if thread_id is not _UNSET:
            if thread_id is not None and not isinstance(thread_id, str):
                raise ValidationError("threadId must be a string or null")
            values["thread_id"] = thread_id
        if status is not _UNSET:
            if status is not None and status not in ALL_STATUSES:
                raise ValidationError(f"Invalid task status {status!r}")
            values["status"] = status
        if run_state is not _UNSET:
            if run_state is not None and run_state not in {
                "starting",
                "running",
                "waiting_quota",
                "waiting_approval",
                "waiting_input",
                "failed",
            }:
                raise ValidationError(f"Invalid run state {run_state!r}")
            values["run_state"] = run_state
        if last_message is not _UNSET:
            if last_message is not None and not isinstance(last_message, str):
                raise ValidationError("lastMessage must be a string or null")
            values["last_message"] = last_message
        if last_error is not _UNSET:
            if last_error is None or isinstance(last_error, str):
                values["last_error"] = last_error
            else:
                values["last_error"] = _json(last_error)
        prepared = self._prepare_attachments(attachments or [])
        if not values and not prepared:
            return self.get_task(task_id)
        values["updated_at"] = utc_now()
        assignments = ", ".join(f"{column} = ?" for column in values)
        if status == "done":
            assignments += ", completed_at = CASE WHEN status = 'done' THEN completed_at ELSE ? END"
            params = [*values.values(), values["updated_at"], task_id, expected_version]
        else:
            if status is not _UNSET:
                assignments += ", completed_at = NULL"
            params = [*values.values(), task_id, expected_version]
        with self.transaction(immediate=True):
            if prepared:
                existing = self._conn.execute("SELECT count(*), coalesce(sum(length(content)), 0) FROM task_attachments WHERE task_id = ?", (task_id,)).fetchone()
                if existing[0] + len(prepared) > 10 or existing[1] + sum(len(content) for _, _, content in prepared) > 20 * 1024 * 1024:
                    raise ValidationError("最多 10 个附件，合计最多 20 MB")
            if priority == "draft":
                current = self._conn.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
                if current is not None and current["status"] == "in_progress" and not (
                    status == "todo" and run_state is None
                ):
                    raise ValidationError("请先暂停任务，再将其设为草稿")
            cursor = self._conn.execute(
                f"UPDATE tasks SET {assignments}, version = version + 1 WHERE id = ? AND version = ?",
                params,
            )
            if cursor.rowcount == 0:
                exists = self._conn.execute(
                    "SELECT version FROM tasks WHERE id = ?", (task_id,)
                ).fetchone()
                if exists is None:
                    raise NotFoundError(f"Task {task_id} was not found")
                raise ConflictError(
                    f"Task {task_id} changed; expected version {expected_version}, current {exists['version']}"
                )
            self._conn.executemany("INSERT INTO task_attachments(id, task_id, name, content) VALUES (?, ?, ?, ?)",
                                   [(aid, task_id, name, content) for aid, name, content in prepared])
            return self._task_json_locked(task_id)

    def claim_next_ready(self, project_id: str) -> dict[str, Any] | None:
        """Atomically claim the highest priority/FIFO ready task."""
        with self.transaction(immediate=True):
            project = self._conn.execute(
                "SELECT id FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
            if project is None:
                raise NotFoundError(f"Project {project_id} was not found")
            active = self._conn.execute(
                "SELECT 1 FROM tasks WHERE project_id = ? AND status = 'in_progress' LIMIT 1",
                (project_id,),
            ).fetchone()
            if active is not None:
                return None
            row = self._conn.execute(
                """
                SELECT t.id
                FROM tasks t
                WHERE t.project_id = ?
                  AND t.status = 'todo'
                  AND t.priority <> 'draft'
                  AND NOT EXISTS (
                    SELECT 1 FROM task_dependencies d
                    JOIN tasks blocker ON blocker.id = d.blocker_task_id
                    WHERE d.blocked_task_id = t.id
                      AND blocker.status <> 'done'
                  )
                ORDER BY CASE t.priority
                    WHEN 'urgent' THEN 0
                    WHEN 'high' THEN 1
                    WHEN 'medium' THEN 2
                    WHEN 'low' THEN 3
                    ELSE 4 END,
                    t.created_at, t.id
                LIMIT 1
                """,
                (project_id,),
            ).fetchone()
            if row is None:
                return None
            now = utc_now()
            cursor = self._conn.execute(
                """
                UPDATE tasks
                SET status = 'in_progress', run_state = 'starting', updated_at = ?, version = version + 1
                WHERE id = ? AND status = 'todo'
                """,
                (now, row["id"]),
            )
            if cursor.rowcount != 1:
                return None
            return self._task_json_locked(row["id"])

    def claim_task(self, task_id: str, expected_version: int) -> dict[str, Any]:
        """Atomically claim one explicitly selected ready task."""
        with self.transaction(immediate=True):
            task = self._conn.execute(
                "SELECT * FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if task is None:
                raise NotFoundError(f"Task {task_id} was not found")
            if int(task["version"]) != expected_version:
                raise ConflictError("Task changed; refresh before running it")
            if task["priority"] == "draft":
                raise ValidationError("请先将草稿改为其他优先级，再执行任务")
            if task["status"] != TaskStatus.TODO.value:
                raise ValidationError("Only todo tasks can be run")
            blockers = self._conn.execute(
                """
                SELECT blocker.status
                FROM task_dependencies d JOIN tasks blocker ON blocker.id = d.blocker_task_id
                WHERE d.blocked_task_id = ? AND blocker.status <> 'done'
                """,
                (task_id,),
            ).fetchall()
            if blockers:
                raise ValidationError("Task dependencies are not complete")
            active = self._conn.execute(
                "SELECT 1 FROM tasks WHERE project_id = ? AND status = 'in_progress' LIMIT 1",
                (task["project_id"],),
            ).fetchone()
            if active is not None:
                raise ConflictError("This project already has a running task")
            self._conn.execute(
                "UPDATE tasks SET status = 'in_progress', run_state = 'starting', updated_at = ?, version = version + 1 WHERE id = ?",
                (utc_now(), task_id),
            )
            return self._task_json_locked(task_id)

    def replace_dependencies(
        self,
        task_id: str,
        expected_version: int,
        blocker_ids: list[str],
    ) -> dict[str, Any]:
        # Preserve order only at the API boundary; the graph itself is a set.
        unique_ids = list(dict.fromkeys(blocker_ids))
        with self.transaction(immediate=True):
            task = self._conn.execute(
                "SELECT id, project_id, version FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if task is None:
                raise NotFoundError(f"Task {task_id} was not found")
            if int(task["version"]) != expected_version:
                raise ConflictError("Task changed; refresh before editing dependencies")
            if task_id in unique_ids:
                raise ValidationError("A task cannot block itself")
            if len(unique_ids) > 100:
                raise ValidationError("A task may have at most 100 blockers")
            if unique_ids:
                placeholders = ",".join("?" for _ in unique_ids)
                rows = self._conn.execute(
                    f"SELECT id, project_id FROM tasks WHERE id IN ({placeholders})",
                    unique_ids,
                ).fetchall()
                known = {row["id"]: row["project_id"] for row in rows}
                missing = [item for item in unique_ids if item not in known]
                if missing:
                    raise NotFoundError(f"Blocker task {missing[0]} was not found")
                cross_project = [
                    item for item in unique_ids if known[item] != task["project_id"]
                ]
                if cross_project:
                    raise ValidationError("Task dependencies must stay within one project")

            # Replace incoming edges first.  Since every new edge points into
            # task_id, checking reachability from task_id catches direct and
            # indirect cycles without needing a recursive SQL extension.
            old_edges = self._conn.execute(
                "SELECT blocker_task_id, blocked_task_id FROM task_dependencies"
            ).fetchall()
            adjacency: dict[str, set[str]] = {}
            for edge in old_edges:
                if edge["blocked_task_id"] == task_id:
                    continue
                adjacency.setdefault(edge["blocker_task_id"], set()).add(
                    edge["blocked_task_id"]
                )
            for blocker_id in unique_ids:
                if self._reachable(adjacency, task_id, blocker_id):
                    raise ValidationError("Task dependencies cannot contain a cycle")
            self._conn.execute(
                "DELETE FROM task_dependencies WHERE blocked_task_id = ?", (task_id,)
            )
            now = utc_now()
            self._conn.executemany(
                "INSERT INTO task_dependencies(blocker_task_id, blocked_task_id, created_at) VALUES (?, ?, ?)",
                [(blocker_id, task_id, now) for blocker_id in unique_ids],
            )
            self._conn.execute(
                "UPDATE tasks SET version = version + 1, updated_at = ? WHERE id = ?",
                (now, task_id),
            )
            return self._task_json_locked(task_id)

    @staticmethod
    def _reachable(adjacency: dict[str, set[str]], start: str, target: str) -> bool:
        if start == target:
            return True
        seen: set[str] = set()
        stack = [start]
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            for child in adjacency.get(current, ()):
                if child == target:
                    return True
                stack.append(child)
        return False

    def delete_task(self, task_id: str, expected_version: int) -> None:
        with self.transaction(immediate=True):
            row = self._conn.execute(
                "SELECT status, version FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if row is None:
                raise NotFoundError(f"Task {task_id} was not found")
            if int(row["version"]) != expected_version:
                raise ConflictError("Task changed; refresh before deleting")
            if row["status"] not in {TaskStatus.TODO.value, TaskStatus.CANCELED.value}:
                raise ValidationError("Only todo or canceled tasks can be deleted")
            self._conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))

    # ------------------------------------------------------------------
    # Runs and interactions
    # ------------------------------------------------------------------
    def create_run(
        self,
        *,
        task_id: str,
        thread_id: str | None,
        turn_id: str | None,
        run_state: str,
        structured_error: Any = None,
        last_output_summary: str | None = None,
        resume_at: str | None = None,
    ) -> dict[str, Any]:
        run_id = new_id()
        now = utc_now()
        with self.transaction(immediate=True):
            self._conn.execute(
                """
                INSERT INTO task_runs(
                    id, task_id, thread_id, turn_id, run_state, structured_error,
                    last_output_summary, resume_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    task_id,
                    thread_id,
                    turn_id,
                    run_state,
                    None if structured_error is None else _json(structured_error),
                    last_output_summary,
                    resume_at,
                    now,
                    now,
                ),
            )
        return self.get_run(run_id)

    def update_latest_run(
        self,
        task_id: str,
        *,
        run_state: str | object = _UNSET,
        thread_id: str | None | object = _UNSET,
        turn_id: str | None | object = _UNSET,
        structured_error: Any = _UNSET,
        last_output_summary: str | None | object = _UNSET,
        resume_at: str | None | object = _UNSET,
    ) -> dict[str, Any] | None:
        values: dict[str, Any] = {}
        for field, value in (
            ("run_state", run_state),
            ("thread_id", thread_id),
            ("turn_id", turn_id),
            ("last_output_summary", last_output_summary),
            ("resume_at", resume_at),
        ):
            if value is not _UNSET:
                values[field] = value
        if structured_error is not _UNSET:
            values["structured_error"] = (
                None if structured_error is None else _json(structured_error)
            )
        if not values:
            return self.latest_run(task_id)
        values["updated_at"] = utc_now()
        assignments = ", ".join(f"{column} = ?" for column in values)
        with self.transaction(immediate=True):
            row = self._conn.execute(
                "SELECT id FROM task_runs WHERE task_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (task_id,),
            ).fetchone()
            if row is None:
                return None
            self._conn.execute(
                f"UPDATE task_runs SET {assignments} WHERE id = ?",
                [*values.values(), row["id"]],
            )
        return self.get_run(row["id"])

    def latest_run(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM task_runs WHERE task_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (task_id,),
            ).fetchone()
            return self._run_json(row) if row else None

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM task_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise NotFoundError(f"Run {run_id} was not found")
            return self._run_json(row)

    @staticmethod
    def _run_json(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "taskId": row["task_id"],
            "threadId": row["thread_id"],
            "turnId": row["turn_id"],
            "runState": row["run_state"],
            "structuredError": _json_load(row["structured_error"]),
            "lastOutputSummary": row["last_output_summary"],
            "resumeAt": row["resume_at"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    def list_runs(self, task_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM task_runs WHERE task_id = ? ORDER BY created_at, id",
                (task_id,),
            ).fetchall()
            return [self._run_json(row) for row in rows]

    def create_interaction(
        self,
        *,
        task_id: str,
        kind: str,
        request_id: str,
        payload: Any,
        blocking_scope: str = "task",
    ) -> dict[str, Any]:
        interaction_id = new_id()
        now = utc_now()
        with self.transaction(immediate=True):
            task = self._conn.execute(
                "SELECT id FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if task is None:
                raise NotFoundError(f"Task {task_id} was not found")
            self._conn.execute(
                """
                INSERT INTO interactions(
                    id, task_id, kind, request_id, payload, blocking_scope,
                    status, version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending', 1, ?, ?)
                """,
                (
                    interaction_id,
                    task_id,
                    kind,
                    request_id,
                    _json(payload),
                    blocking_scope,
                    now,
                    now,
                ),
            )
            return self._interaction_json_locked(interaction_id)

    def get_interaction(self, interaction_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM interactions WHERE id = ?", (interaction_id,)
            ).fetchone()
            if row is None:
                raise NotFoundError(f"Interaction {interaction_id} was not found")
            return self._interaction_json(row)

    def list_interactions(
        self, task_id: str, *, pending_only: bool = False
    ) -> list[dict[str, Any]]:
        with self._lock:
            clause = "AND status = 'pending'" if pending_only else ""
            rows = self._conn.execute(
                f"SELECT * FROM interactions WHERE task_id = ? {clause} ORDER BY created_at, id",
                (task_id,),
            ).fetchall()
            return [self._interaction_json(row) for row in rows]

    def resolve_interaction(
        self,
        interaction_id: str,
        expected_version: int,
        response: Any,
        *,
        canceled: bool = False,
    ) -> dict[str, Any]:
        with self.transaction(immediate=True):
            row = self._conn.execute(
                "SELECT * FROM interactions WHERE id = ?", (interaction_id,)
            ).fetchone()
            if row is None:
                raise NotFoundError(f"Interaction {interaction_id} was not found")
            if int(row["version"]) != expected_version:
                raise ConflictError("Interaction changed; refresh before resolving")
            if row["status"] != "pending":
                raise ValidationError("Interaction is no longer pending")
            now = utc_now()
            self._conn.execute(
                "UPDATE interactions SET status = ?, response = ?, version = version + 1, updated_at = ? WHERE id = ?",
                (
                    "canceled" if canceled else "resolved",
                    _json(response),
                    now,
                    interaction_id,
                ),
            )
            return self._interaction_json_locked(interaction_id)

    def pending_interactions(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM interactions WHERE status = 'pending' ORDER BY created_at, id"
            ).fetchall()
            return [self._interaction_json(row) for row in rows]

    def _interaction_json_locked(self, interaction_id: str) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT * FROM interactions WHERE id = ?", (interaction_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError(f"Interaction {interaction_id} was not found")
        return self._interaction_json(row)

    @staticmethod
    def _interaction_json(row: sqlite3.Row) -> dict[str, Any]:
        payload = _json_load(row["payload"], {})
        if not isinstance(payload, dict):
            payload = {"value": payload}
        result = {
            "id": row["id"],
            "taskId": row["task_id"],
            "kind": row["kind"],
            "requestId": row["request_id"],
            "payload": payload,
            "blockingScope": row["blocking_scope"],
            "status": row["status"],
            "response": _json_load(row["response"]),
            "version": int(row["version"]),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }
        # These convenience fields keep the UI compact while preserving the
        # original JSON-RPC payload for future protocol additions.
        for field in ("title", "message", "command", "cwd", "questions", "permissionScope"):
            if field in payload:
                result[field] = payload[field]
        return result

    # ------------------------------------------------------------------
    # Scheduler/recovery queries
    # ------------------------------------------------------------------
    def in_progress_tasks(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM tasks WHERE status = 'in_progress' ORDER BY created_at, id"
            ).fetchall()
            return [self._task_json_locked(row["id"]) for row in rows]

    def waiting_quota_tasks(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM tasks WHERE status = 'in_progress' AND run_state = 'waiting_quota' ORDER BY created_at, id"
            ).fetchall()
            return [self._task_json_locked(row["id"]) for row in rows]

    def project_has_active_task(self, project_id: str) -> bool:
        with self._lock:
            return self._conn.execute(
                "SELECT 1 FROM tasks WHERE project_id = ? AND status = 'in_progress' LIMIT 1",
                (project_id,),
            ).fetchone() is not None
