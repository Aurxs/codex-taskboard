"""Durable scheduling rules shared by ordinary tasks and task-group leaves."""
from __future__ import annotations

import json
import uuid
import time
from datetime import datetime, timezone

from .errors import ConflictError, ValidationError
from .git_workspace import GitError, current_branch, git, normalize_scopes, overlaps, repository


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def rank(task: dict) -> tuple:
    return ({"urgent": 0, "high": 1, "medium": 2, "low": 3}.get(task["priority"], 4),
            task["createdAt"], task["id"])


class ParallelDatabase:
    def migrate_parallel(self) -> None:
        columns = {r["name"] for r in self._conn.execute("PRAGMA table_info(tasks)")}
        for name, definition in (
            ("kind", "TEXT NOT NULL DEFAULT 'task'"),
            ("parent_id", "TEXT REFERENCES tasks(id) ON DELETE RESTRICT"),
            ("scheduling_mode", "TEXT NOT NULL DEFAULT 'exclusive'"),
            ("write_scopes", "TEXT NOT NULL DEFAULT '[]'"),
            ("target_branch", "TEXT"),
            ("parallel_state", "TEXT NOT NULL DEFAULT '{}'"),
        ):
            if name not in columns:
                self._conn.execute(f"ALTER TABLE tasks ADD COLUMN {name} {definition}")
        for statement in (
            "CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_id)",
            """CREATE TABLE IF NOT EXISTS execution_leases (
                task_id TEXT PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
                repo_key TEXT NOT NULL, root_id TEXT NOT NULL, mode TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS scope_leases (
                owner_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                namespace TEXT NOT NULL, scopes TEXT NOT NULL,
                PRIMARY KEY(owner_id, namespace))""",
            """CREATE TABLE IF NOT EXISTS execution_queue (
                task_id TEXT PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
                request_id TEXT NOT NULL, prompt TEXT, state TEXT NOT NULL,
                previous_status TEXT NOT NULL, created_at TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS task_operations (
                id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                kind TEXT NOT NULL, state TEXT NOT NULL, payload TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
            "CREATE INDEX IF NOT EXISTS idx_task_operations ON task_operations(task_id, kind)",
        ):
            self._conn.execute(statement)

    def _parallel_json(self, row) -> dict:
        state = json.loads(row["parallel_state"])
        queued = self._conn.execute("SELECT 1 FROM execution_queue WHERE task_id=? AND state='pending'", (row["id"],)).fetchone()
        children = self._conn.execute("SELECT status,run_state,parallel_state FROM tasks WHERE parent_id=?", (row["id"],)).fetchall()
        return {
            "kind": row["kind"], "parentId": row["parent_id"],
            "schedulingMode": row["scheduling_mode"], "writeScopes": json.loads(row["write_scopes"]),
            "targetBranch": row["target_branch"], "parallel": state,
            "groupPhase": state.get("groupPhase"), "mergeState": state.get("mergeState", "none"),
            "waitReason": state.get("waitReason"), "queued": bool(queued),
            "plan": self.task_plan(row["id"], state),
            "progress": {"total": len(children),
                         "integrated": sum(json.loads(c["parallel_state"]).get("mergeState") == "merged" and c["status"] == "done" for c in children),
                         "running": sum(c["run_state"] in {"starting", "running", "waiting_input", "waiting_approval"} for c in children),
                         "attention": sum(c["run_state"] == "failed" or c["status"] == "canceled" or json.loads(c["parallel_state"]).get("needsValidation", False) for c in children)},
        }

    def task_plan(self, task_id: str, state: dict) -> dict:
        operation = self.operation(state.get("planOperation", ""))
        accepted = self.operation(state.get("acceptedPlan", ""))
        return {"hold": bool(state.get("planHold")),
                "operationId": operation["id"] if operation else None,
                "state": operation["state"] if operation else None,
                **(operation["payload"] if operation else {}),
                "acceptedText": accepted["payload"].get("text") if accepted else None}

    def plan_prompt(self, task_id: str) -> str:
        task = self.get_task(task_id)
        text = task["plan"].get("acceptedText")
        return "\nSaved implementation plan:\n" + text + "\n" if text else ""

    def children(self, parent_id: str) -> list[dict]:
        with self._lock:
            return [self._task_json_locked(r["id"]) for r in self._conn.execute(
                "SELECT id FROM tasks WHERE parent_id=? ORDER BY created_at,id", (parent_id,))]

    def repo_info(self, task: dict) -> tuple[str | None, str, bool]:
        project = self.get_project(task["projectId"])
        cache = getattr(self, "_parallel_repo_cache", {})
        self._parallel_repo_cache = cache
        cache_key = (task["projectId"], project["workspacePath"])
        cached = cache.get(cache_key)
        if cached and time.monotonic() - cached[0] < 5:
            return cached[1]
        try:
            root, key = repository(project["workspacePath"])
            result = root, key, git(root, "config", "--bool", "core.ignorecase", check=False) == "true"
        except GitError:
            # Non-Git legacy tasks retain their project-level isolation.
            result = None, "project:" + task["projectId"], False
        cache[cache_key] = (time.monotonic(), result)
        return result

    def validate_parallel_options(self, task: dict, changes: dict, *, creating=False) -> dict:
        kind = changes.get("kind", task.get("kind", "task"))
        parent_id = changes.get("parent_id", task.get("parentId"))
        mode = changes.get("scheduling_mode", task.get("schedulingMode", "exclusive"))
        if kind not in {"task", "parallel_group"} or mode not in {"exclusive", "parallel"}:
            raise ValidationError("无效的任务形态或执行方式")
        if not creating and (kind != task["kind"] or parent_id != task["parentId"]):
            raise ValidationError("任务创建后不能转换形态或转移子任务")
        if not creating and task["parentId"]:
            self.require_group_editable(task["parentId"], structural=False)
        if parent_id:
            parent = self.get_task(parent_id)
            if parent["kind"] != "parallel_group" or parent["parentId"] or kind != "task" or parent["projectId"] != task["projectId"]:
                raise ValidationError("子任务必须属于同项目的一级并行任务组")
            if creating:
                self.require_group_editable(parent_id)
            mode = "parallel"
        scopes = changes.get("write_scopes", task.get("writeScopes", []))
        root, _, _ = self.repo_info(task)
        scopes = normalize_scopes(scopes, root)
        if kind == "parallel_group" and scopes:
            raise ValidationError("请在子任务中声明修改范围，父任务自动汇总")
        target = changes.get("target_branch", task.get("targetBranch"))
        if parent_id:
            target = parent["targetBranch"]
        elif creating and not target and root:
            target = current_branch(root)
        if target is not None and (not isinstance(target, str) or not target.strip() or target.startswith("-")):
            raise ValidationError("请选择有效的合入目标")
        locked = task.get("threadId") or task.get("worktreePath") or task.get("parallel", {}).get("baseCommit")
        if not creating and locked and (mode != task["schedulingMode"] or target != task["targetBranch"]):
            raise ValidationError("任务启动后不能修改执行方式或合入目标")
        if not creating and scopes != task["writeScopes"] and task.get("status") == "in_progress" and not task.get("parallel", {}).get("paused"):
            raise ValidationError("请先暂停任务，再调整修改范围")
        if not creating and scopes != task["writeScopes"] and any(op["state"] in {"agent_running", "uncertain", "preparing", "publishing"} for op in self.operations(task["id"], "merge")):
            raise ValidationError("请先暂停任务，再调整修改范围")
        return {"kind": kind, "parent_id": parent_id, "scheduling_mode": mode,
                "write_scopes": json.dumps(scopes), "target_branch": target,
                **({"execution_mode": "worktree"} if mode == "parallel" or kind == "parallel_group" else {})}

    def require_group_editable(self, group_id: str, *, structural=True) -> dict:
        group = self.get_task(group_id)
        if group["kind"] != "parallel_group":
            raise ValidationError("该任务不是并行任务组")
        if structural and group["groupPhase"] not in {"preparing", "paused"}:
            raise ValidationError("请先暂停任务组，再调整子任务或依赖")
        if group["status"] in {"done", "canceled"}:
            raise ValidationError("任务组已结束，请另建补充任务")
        return group

    def set_parallel(self, task_id: str, **changes) -> dict:
        with self.transaction(immediate=True):
            task = self.get_task(task_id)
            return self.update_task(task_id, task["version"], parallel=changes)

    def dependencies_ready(self, task_id: str) -> bool:
        for row in self._conn.execute("""SELECT t.* FROM tasks t JOIN task_dependencies d
                ON t.id=d.blocker_task_id WHERE d.blocked_task_id=?""", (task_id,)):
            state = json.loads(row["parallel_state"])
            if row["status"] != "done" or state.get("needsValidation") or (state.get("managed") and state.get("mergeState") != "merged"):
                return False
        return True

    def enqueue(self, task_id: str, version: int, prompt: str | None = None, request_id: str | None = None) -> dict:
        with self.transaction(immediate=True):
            task = self.get_task(task_id)
            previous = self._conn.execute("SELECT * FROM execution_queue WHERE task_id=?", (task_id,)).fetchone()
            if request_id and previous and previous["request_id"] == request_id:
                return task
            if task["version"] != version:
                raise ConflictError("Task changed; refresh before running it")
            if previous and previous["state"] in {"pending", "claimed"}:
                raise ConflictError("任务已经排队或正在启动")
            if task["parallel"].get("planHold"):
                raise ValidationError("请先确认或取消任务计划")
            if task["priority"] == "draft":
                raise ValidationError("请先将草稿改为其他优先级，再执行任务")
            if task["kind"] == "parallel_group":
                raise ValidationError("请使用任务组的提交或运行操作")
            if task["parentId"]:
                parent = self.get_task(task["parentId"])
                if parent["groupPhase"] != "submitted" or parent["status"] not in {"in_progress", "in_review"}:
                    raise ValidationError("请先提交并运行父任务组")
                if parent["status"] == "in_review":
                    self.update_task(parent["id"], parent["version"], status="todo",
                                     parallel={"mergeState": "none", "approvedCommit": None, "runRequested": True})
            if prompt and task["parallel"].get("managed"):
                self.mark_downstream_stale(task_id)
            self._conn.execute("""INSERT INTO execution_queue VALUES(?,?,?,?,?,?)
                ON CONFLICT(task_id) DO UPDATE SET request_id=excluded.request_id,prompt=excluded.prompt,
                state='pending',previous_status=excluded.previous_status,created_at=excluded.created_at""",
                (task_id, request_id or str(uuid.uuid4()), prompt, "pending", task["status"], now()))
            state = {"waitReason": "已排队", "paused": False}
            if prompt and task["parallel"].get("managed"):
                state.update(mergeState="none", approvedCommit=None)
            task = self.update_task(task_id, version, parallel=state)
            return task

    def queued_prompt(self, task_id: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT prompt FROM execution_queue WHERE task_id=? AND state='claimed'", (task_id,)).fetchone()
            return row["prompt"] if row else None

    def finish_queue(self, task_id: str) -> None:
        self._conn.execute("UPDATE execution_queue SET state='done' WHERE task_id=?", (task_id,))

    def release_execution(self, task_id: str, *, scopes=False) -> None:
        self._conn.execute("DELETE FROM execution_leases WHERE task_id=?", (task_id,))
        if scopes:
            self._conn.execute("DELETE FROM scope_leases WHERE owner_id=?", (task_id,))

    def reserve_scopes(self, task: dict) -> str | None:
        root_task = self.get_task(task["parentId"]) if task["parentId"] else task
        _, key, insensitive = self.repo_info(task)
        scopes = sorted({s for child in self.children(root_task["id"]) for s in child["writeScopes"]}) if root_task["kind"] == "parallel_group" else task["writeScopes"]
        requests = [(root_task["id"], key, scopes)]
        if task["parentId"]:
            requests.append((task["id"], "group:" + task["parentId"], task["writeScopes"]))
        for owner, namespace, desired in requests:
            for row in self._conn.execute("SELECT owner_id,scopes FROM scope_leases WHERE namespace=? AND owner_id<>?", (namespace, owner)):
                if any(overlaps(a, b, ignore_case=insensitive) for a in desired for b in json.loads(row["scopes"])):
                    return f"等待任务 {self.get_task(row['owner_id'])['identifier']} 的修改范围释放或合入"
        for owner, namespace, desired in requests:
            self._conn.execute("INSERT OR REPLACE INTO scope_leases VALUES(?,?,?)", (owner, namespace, json.dumps(desired)))
        return None

    def eligibility(self, task: dict, *, fairness=True) -> str | None:
        if task["parallel"].get("planHold"):
            return "请先确认或取消任务计划"
        if not self.dependencies_ready(task["id"]):
            return "等待前置任务完成并合入"
        if task["parallel"].get("paused") or task["parallel"].get("nativeConflict"):
            return "任务已暂停或需要核对原生会话"
        root = self.get_task(task["parentId"]) if task["parentId"] else task
        if root["priority"] == "draft":
            return "请先将草稿改为其他优先级，再执行任务"
        if root["kind"] == "parallel_group" and (root["groupPhase"] != "submitted" or (task["parentId"] and root["status"] != "in_progress")):
            return "等待任务组提交执行"
        _, key, _ = self.repo_info(task)
        for lease in self._conn.execute("SELECT * FROM execution_leases WHERE repo_key=?", (key,)):
            if lease["task_id"] == task["id"]:
                if task["runState"] in {"starting", "running", "waiting_input", "waiting_approval"}:
                    return "任务已经执行中"
                continue
            if lease["root_id"] == root["id"]:
                continue
            if root["schedulingMode"] == "exclusive" or lease["mode"] == "exclusive":
                return "等待独占任务或当前执行结束"
        # Recover legacy runs and group ownership even before a lease existed.
        for other in self.list_tasks():
            if other["id"] == task["id"] or (other["parentId"] or other["id"]) == root["id"]:
                continue
            if other["parallel"].get("nativeConflict") and self.repo_info(other)[1] == key:
                return "原生会话与调度占用冲突，请先处理"
            active_group = other["kind"] == "parallel_group" and other["status"] == "in_progress" and other["groupPhase"] == "submitted"
            active_turn = other["status"] == "in_progress" and other["runState"] in {"starting", "running", "waiting_input", "waiting_approval"}
            if (active_group or active_turn) and self.repo_info(other)[1] == key:
                other_root = self.get_task(other["parentId"]) if other["parentId"] else other
                if root["schedulingMode"] == "exclusive" or other_root["schedulingMode"] == "exclusive":
                    return "等待独占任务或当前执行结束"
        if fairness:
            for earlier in self.list_tasks():
                if earlier["parentId"] or earlier["id"] == root["id"] or rank(earlier) >= rank(root):
                    continue
                pending = earlier["queued"] or (earlier["status"] == "todo" and (earlier["parallel"].get("runRequested") or self.get_project(earlier["projectId"])["automationEnabled"]))
                if (pending and earlier["schedulingMode"] == "exclusive" and earlier["priority"] != "draft"
                    and not earlier["parallel"].get("paused") and not earlier["parallel"].get("planHold") and self.dependencies_ready(earlier["id"])
                    and (earlier["kind"] != "parallel_group" or earlier["groupPhase"] == "submitted")
                    and self.repo_info(earlier)[1] == key):
                    return f"等待前序独占任务 {earlier['identifier']}"
        return self.reserve_scopes(task)

    def claim_candidate(self, task_id: str, *, expected_version: int | None = None, queued=False) -> dict | None:
        with self.transaction(immediate=True):
            task = self.get_task(task_id)
            if expected_version is not None and task["version"] != expected_version:
                raise ConflictError("Task changed; refresh before running it")
            if not queued and task["status"] != "todo":
                return None
            if task["priority"] == "draft" or task["kind"] == "parallel_group":
                return None
            reason = self.eligibility(task)
            if reason:
                if task["waitReason"] != reason:
                    self.set_parallel(task_id, waitReason=reason)
                return None
            root = self.get_task(task["parentId"]) if task["parentId"] else task
            key = self.repo_info(task)[1]
            self._conn.execute("INSERT OR REPLACE INTO execution_leases VALUES(?,?,?,?)", (task_id, key, root["id"], root["schedulingMode"]))
            self._conn.execute("UPDATE execution_queue SET state='claimed' WHERE task_id=?", (task_id,))
            return self.update_task(task_id, task["version"], status="in_progress", run_state="starting",
                                    last_error=None, parallel={"waitReason": None, "paused": False, "mergeState": "none",
                                                               "approvedCommit": None, "validationBase": None if task["parallel"].get("needsValidation") else task["parallel"].get("validationBase"), "validationRequested": bool(task["parallel"].get("needsValidation") or task["parallel"].get("validationRequested")), "needsValidation": False})

    def queue_candidates(self) -> list[dict]:
        with self._lock:
            return sorted((self.get_task(r["task_id"]) for r in self._conn.execute("SELECT task_id FROM execution_queue WHERE state='pending'")), key=rank)

    def operation(self, op_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM task_operations WHERE id=?", (op_id,)).fetchone()
            return {**dict(row), "payload": json.loads(row["payload"])} if row else None

    def operations(self, task_id: str | None = None, kind: str | None = None) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT id FROM task_operations WHERE (? IS NULL OR task_id=?) AND (? IS NULL OR kind=?) ORDER BY created_at,rowid",
                                      (task_id, task_id, kind, kind))
            return [self.operation(r["id"]) for r in rows]

    def save_operation(self, op_id: str, task_id: str, kind: str, state: str, **payload) -> dict:
        with self.transaction(immediate=True):
            previous = self.operation(op_id)
            merged = {**(previous["payload"] if previous else {}), **payload}
            self._conn.execute("""INSERT INTO task_operations VALUES(?,?,?,?,?,?,?) ON CONFLICT(id)
                DO UPDATE SET state=excluded.state,payload=excluded.payload,updated_at=excluded.updated_at""",
                (op_id, task_id, kind, state, json.dumps(merged), now(), now()))
            return self.operation(op_id)

    def mark_downstream_stale(self, task_id: str) -> None:
        with self.transaction(immediate=True):
            task = self.get_task(task_id)
            if task["parentId"]:
                self.set_parallel(task["parentId"], mergeState="none", approvedCommit=None)
            pending = [task_id]
            seen = set()
            while pending:
                current = pending.pop()
                for row in self._conn.execute("SELECT blocked_task_id FROM task_dependencies WHERE blocker_task_id=?", (current,)):
                    child_id = row["blocked_task_id"]
                    if child_id in seen:
                        continue
                    seen.add(child_id)
                    pending.append(child_id)
                    child = self.get_task(child_id)
                    if child["threadId"] or child["parallel"].get("resultCommit"):
                        self.set_parallel(child_id, needsValidation=True)
