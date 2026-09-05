"""FastAPI application for Codex Taskboard."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, Response
from urllib.parse import quote
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from . import __version__
from .db import Database
from .errors import ConflictError, TaskboardError, UnsupportedError, ValidationError
from .events import EventBus
from .scheduler import Scheduler


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ProjectCreateBody(StrictModel):
    key: str
    name: str
    workspacePath: str
    codexProjectId: str | None = None
    automationEnabled: bool = False
    reviewRequired: bool = True
    quotaAutoResumeEnabled: bool = True


class ProjectUpdateBody(StrictModel):
    version: int = Field(ge=1)
    key: str | None = None
    name: str | None = None
    workspacePath: str | None = None
    codexProjectId: str | None = None
    automationEnabled: bool | None = None
    reviewRequired: bool | None = None
    quotaAutoResumeEnabled: bool | None = None


class CodexProjectSyncItem(StrictModel):
    """A project advertised by the injected Codex renderer."""

    id: str | None = None
    name: str | None = None
    projectKind: Literal["local", "remote"] | None = None
    workspacePath: str | None = None
    hostId: str | None = None


class CodexProjectSyncBody(StrictModel):
    projects: list[CodexProjectSyncItem] = Field(default_factory=list)
    selectedProjectId: str | None = None


class VersionBody(StrictModel):
    version: int = Field(ge=1)


class TaskExecutionBody(StrictModel):
    model: str | None = Field(default=None, min_length=1, max_length=200)
    reasoningEffort: str | None = Field(default=None, min_length=1, max_length=40)


class AttachmentBody(StrictModel):
    name: str = Field(min_length=1, max_length=255)
    content: str = Field(max_length=14_000_000)


class TaskCreateBody(TaskExecutionBody):
    title: str
    description: str = ""
    priority: Literal["urgent", "high", "medium", "low", "none", "draft"] = "none"
    blockedByIds: list[str] = Field(default_factory=list)
    attachments: list[AttachmentBody] = Field(default_factory=list, max_length=10)


class TaskUpdateBody(TaskExecutionBody):
    attachments: list[AttachmentBody] = Field(default_factory=list, max_length=10)
    version: int = Field(ge=1)
    title: str | None = None
    description: str | None = None
    priority: Literal["urgent", "high", "medium", "low", "none", "draft"] | None = None


class DependenciesBody(StrictModel):
    blockedByIds: list[str]
    version: int = Field(ge=1)


class ActionBody(StrictModel):
    action: str
    version: int = Field(ge=1)
    feedback: str | None = None
    targetStatus: Literal["in_review", "done"] | None = None


class InteractionResolveBody(StrictModel):
    version: int = Field(ge=1)
    response: Any = None
    canceled: bool = False


def default_data_dir() -> Path:
    configured = os.environ.get("CODEX_TASKBOARD_DATA_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    if os.environ.get("CODEX_TASKBOARD_DEV", "").lower() in {"1", "true", "yes"}:
        return Path.cwd() / ".data"
    if getattr(sys, "frozen", False) or os.environ.get("CODEX_TASKBOARD_PACKAGED") == "1":
        return Path.home() / "Library" / "Application Support" / "Codex Taskboard"
    return Path.cwd() / ".data"


def _codex_state_candidates() -> list[Path]:
    configured = os.environ.get("CODEX_PROJECTS_FILE", "").strip()
    if configured:
        return [Path(configured).expanduser()]
    codex_home = Path(
        os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))
    ).expanduser()
    return [codex_home / ".codex-global-state.json"]


def _read_codex_projects() -> list[dict[str, Any]]:
    """Read the saved local-project map used by the Codex renderer.

    Live project discovery comes from the injected renderer.  This endpoint is
    only a read-only fallback for a host that cannot expose that bridge, so it
    intentionally accepts the one persisted shape Codex uses instead of
    recursively guessing at arbitrary JSON objects.
    """
    found: dict[str, dict[str, Any]] = {}
    for path in _codex_state_candidates():
        if not path.is_file() or path.suffix.lower() != ".json":
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(value, dict):
            continue
        local_projects = value.get("local-projects")
        if not isinstance(local_projects, dict):
            continue
        for raw_project_id, item in local_projects.items():
            if not isinstance(raw_project_id, str) or not raw_project_id.strip():
                continue
            if not isinstance(item, dict) or not isinstance(item.get("rootPaths"), list):
                continue
            workspace = next(
                (root.strip() for root in item["rootPaths"]
                 if isinstance(root, str) and root.strip()),
                "",
            )
            if not workspace:
                continue
            project_id = raw_project_id.strip()
            name = item.get("name") if isinstance(item.get("name"), str) else ""
            found[project_id] = {
                "id": project_id,
                "codexProjectId": project_id,
                "name": name.strip() or Path(workspace).name,
                "workspacePath": workspace,
            }
    return sorted(found.values(), key=lambda item: (item["name"].lower(), item["id"]))


def create_app(
    *,
    data_dir: str | Path | None = None,
    db_path: str | Path | None = None,
    codex_command: list[str] | None = None,
) -> FastAPI:
    root = Path(data_dir).expanduser() if data_dir is not None else default_data_dir()
    database_path = Path(db_path).expanduser() if db_path is not None else root / "taskboard.sqlite3"
    db = Database(database_path)
    events = EventBus()
    scheduler = Scheduler(db, events, codex_command=codex_command)
    app = FastAPI(title="Codex Taskboard", version=__version__)
    app.state.db = db
    app.state.events = events
    app.state.scheduler = scheduler

    app.add_middleware(
        CORSMiddleware,
        # The embedded Taskboard iframe intentionally has an opaque (null)
        # origin.  Keep this explicit rather than using ``*`` so the service
        # remains local-only and credential policy stays predictable.
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173", "null"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(TaskboardError)
    async def taskboard_error(_: Request, exc: TaskboardError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    **({"details": exc.details} if exc.details is not None else {}),
                }
            },
        )

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "version": __version__, "schedulerRunning": scheduler.running}

    @app.get("/api/projects")
    async def projects() -> dict[str, Any]:
        return {"projects": db.list_projects()}

    @app.post("/api/projects", status_code=201)
    async def create_project(body: ProjectCreateBody) -> dict[str, Any]:
        project = db.create_project(
            key=body.key,
            name=body.name,
            workspace_path=body.workspacePath,
            codex_project_id=body.codexProjectId,
            automation_enabled=body.automationEnabled,
            review_required=body.reviewRequired,
            quota_auto_resume_enabled=body.quotaAutoResumeEnabled,
        )
        await events.publish("project.updated", project_id=project["id"], payload=project)
        scheduler.kick()
        return project

    @app.patch("/api/projects/{project_id}")
    async def update_project(project_id: str, body: ProjectUpdateBody) -> dict[str, Any]:
        fields = body.model_dump(exclude={"version"}, exclude_unset=True, by_alias=False)
        for field, value in fields.items():
            if value is None and field != "codexProjectId":
                raise ValidationError(f"{field} cannot be null")
        project = db.update_project(project_id, body.version, **_snake_project_fields(fields))
        await events.publish("project.updated", project_id=project_id, payload=project)
        scheduler.kick()
        return project

    @app.delete("/api/projects/{project_id}")
    async def delete_project(project_id: str, body: VersionBody) -> dict[str, bool]:
        db.delete_project(project_id, body.version)
        await events.publish("project.deleted", project_id=project_id, payload={"id": project_id})
        scheduler.kick()
        return {"ok": True}

    @app.get("/api/codex/models")
    async def codex_models() -> dict[str, Any]:
        return {"models": await scheduler.list_models()}

    @app.get("/api/codex/projects")
    async def codex_projects() -> dict[str, Any]:
        return {"projects": _read_codex_projects()}

    @app.post("/api/codex/projects/sync")
    async def sync_codex_projects(body: CodexProjectSyncBody) -> dict[str, Any]:
        """Persist the local projects reported by the embedded Codex UI.

        Remote projects are deliberately ignored: their workspace cannot be
        executed by the local scheduler.  The renderer may send them in the
        same HostContext list, so ignoring them is preferable to rejecting the
        entire synchronization payload.
        """
        selected_internal_id: str | None = None
        synced: dict[str, dict[str, Any]] = {}
        for item in body.projects:
            project_id = item.id.strip() if isinstance(item.id, str) else ""
            workspace_path = (
                item.workspacePath.strip()
                if isinstance(item.workspacePath, str)
                else ""
            )
            if item.projectKind != "local" or not project_id or not workspace_path:
                continue
            name = item.name.strip() if isinstance(item.name, str) else ""
            project = db.upsert_codex_project(
                codex_project_id=project_id,
                name=name,
                workspace_path=workspace_path,
            )
            synced[project_id] = project
            await events.publish(
                "project.updated",
                project_id=project["id"],
                payload=project,
            )

        selected_id = (
            body.selectedProjectId.strip()
            if isinstance(body.selectedProjectId, str)
            else ""
        )
        if selected_id:
            selected_internal_id = synced.get(selected_id, {}).get("id")
        if synced:
            scheduler.kick()
        return {
            "projects": db.list_projects(),
            "selectedProjectId": selected_internal_id,
        }

    @app.post("/api/system/pick-directory")
    async def pick_directory() -> dict[str, str]:
        if sys.platform != "darwin":
            raise UnsupportedError("Directory picker is only available on macOS")
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                [
                    "/usr/bin/osascript",
                    "-e",
                    "POSIX path of (choose folder with prompt \"选择 Codex 项目目录\")",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired as exc:
            raise TaskboardError("Directory picker timed out") from exc
        if result.returncode != 0:
            # osascript uses a non-zero exit for the user's Cancel action.
            raise ConflictError("Directory selection canceled")
        workspace = result.stdout.strip().rstrip("/")
        if not workspace or "\x00" in workspace:
            raise ValidationError("Directory picker returned no usable path")
        return {"workspacePath": workspace}

    @app.get("/api/projects/{project_id}/tasks")
    async def project_tasks(project_id: str, includeCanceled: bool = True) -> dict[str, Any]:
        db.get_project(project_id)
        return {"tasks": db.list_tasks(project_id, include_canceled=includeCanceled)}

    @app.post("/api/projects/{project_id}/tasks", status_code=201)
    async def create_task(project_id: str, body: TaskCreateBody) -> dict[str, Any]:
        await scheduler.execution_options(body.model, body.reasoningEffort)
        task = db.create_task(
            model=body.model,
            reasoning_effort=body.reasoningEffort,
            project_id=project_id,
            title=body.title,
            description=body.description,
            priority=body.priority,
            attachments=[item.model_dump() for item in body.attachments],
        )
        if body.blockedByIds:
            task = db.replace_dependencies(task["id"], task["version"], body.blockedByIds)
        await events.publish("task.updated", project_id=project_id, task_id=task["id"], payload=task)
        scheduler.kick()
        return task

    @app.get("/api/attachments/{attachment_id}")
    async def download_attachment(attachment_id: str) -> Response:
        name, content = db.get_attachment(attachment_id)
        return Response(content, media_type="application/octet-stream", headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name, safe='')}",
            "X-Content-Type-Options": "nosniff",
        })

    @app.get("/api/attachments/{attachment_id}/preview")
    async def preview_attachment(attachment_id: str) -> dict[str, str]:
        path = db.attachment_path(attachment_id)
        types = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                 ".gif": "image/gif", ".webp": "image/webp"}
        suffix = path.suffix.lower()
        if suffix in types:
            return {"kind": "image", "content": "data:" + types[suffix] + ";base64," +
                    base64.b64encode(path.read_bytes()).decode("ascii")}
        if suffix in {".md", ".markdown", ".txt"}:
            return {"kind": "text" if suffix == ".txt" else "markdown",
                    "content": path.read_text(encoding="utf-8-sig", errors="replace")}
        return {"kind": "external", "content": str(path)}

    @app.post("/api/attachments/{attachment_id}/open")
    async def open_attachment(attachment_id: str) -> dict[str, bool]:
        path = db.attachment_path(attachment_id)
        if path.suffix.lower() not in {".pdf", ".ppt", ".pptx", ".doc", ".docx", ".xls", ".xlsx"}:
            raise ValidationError("此附件应在面板内预览")
        try:
            if sys.platform == "win32":
                await asyncio.to_thread(os.startfile, str(path))
            else:
                command = ["/usr/bin/open", str(path)] if sys.platform == "darwin" else ["xdg-open", str(path)]
                result = await asyncio.to_thread(subprocess.run, command, check=False,
                                               capture_output=True, timeout=15)
                if result.returncode:
                    raise TaskboardError("无法在系统默认应用中打开附件，请检查是否安装了对应应用")
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise TaskboardError("无法在系统默认应用中打开附件") from exc
        return {"opened": True}

    @app.get("/api/tasks/{task_id}")
    async def task_detail(task_id: str) -> dict[str, Any]:
        task = db.get_task(task_id)
        try:
            await scheduler.hydrate_activity(task)
        except Exception:
            task["activityError"] = "暂时无法读取 Codex 历史，以下为本地保存的记录。"
        task = {**db.get_task(task_id), **({"activityError": task["activityError"]} if task.get("activityError") else {})}
        task["activity"] = db.list_activity(task_id)
        task["runs"] = db.list_runs(task_id)
        task["interactions"] = db.list_interactions(task_id)
        return task

    @app.patch("/api/tasks/{task_id}")
    async def update_task(task_id: str, body: TaskUpdateBody) -> dict[str, Any]:
        fields = body.model_dump(exclude={"version"}, exclude_unset=True, by_alias=False)
        if "model" in fields or "reasoningEffort" in fields:
            current = db.get_task(task_id)
            if current["status"] == "in_progress":
                raise ValidationError("请先暂停任务，再修改模型或推理强度")
            await scheduler.execution_options(fields.get("model", current.get("model")), fields.get("reasoningEffort", current.get("reasoningEffort")))
        for field, value in fields.items():
            if value is None and field not in {"model", "reasoningEffort"}:
                raise ValidationError(f"{field} cannot be null")
        task = db.update_task(task_id, body.version, **_snake_task_fields(fields))
        await events.publish("task.updated", project_id=task["projectId"], task_id=task_id, payload=task)
        scheduler.kick()
        return task

    @app.delete("/api/tasks/{task_id}")
    async def delete_task(task_id: str, body: VersionBody) -> dict[str, bool]:
        task = db.get_task(task_id)
        db.delete_task(task_id, body.version)
        await events.publish("task.deleted", project_id=task["projectId"], task_id=task_id, payload={"id": task_id})
        scheduler.kick()
        return {"ok": True}

    @app.put("/api/tasks/{task_id}/dependencies")
    async def replace_dependencies(task_id: str, body: DependenciesBody) -> dict[str, Any]:
        task = db.replace_dependencies(task_id, body.version, body.blockedByIds)
        await events.publish("task.updated", project_id=task["projectId"], task_id=task_id, payload=task)
        # A dependency replacement can also change the readiness of a blocked
        # task, so publish a cheap project snapshot of affected cards.
        for affected in db.list_tasks(task["projectId"]):
            if affected["id"] == task_id or any(item["id"] == task_id for item in affected["blockedBy"]):
                await events.publish("task.updated", project_id=task["projectId"], task_id=affected["id"], payload=affected)
        scheduler.kick()
        return task

    @app.post("/api/tasks/{task_id}/actions")
    async def task_action(task_id: str, body: ActionBody) -> dict[str, Any]:
        task = await scheduler.action(
            task_id,
            body.action,
            body.version,
            body.feedback,
            target_status=body.targetStatus,
        )
        return task

    @app.post("/api/interactions/{interaction_id}/resolve")
    async def resolve_interaction(interaction_id: str, body: InteractionResolveBody) -> dict[str, Any]:
        interaction = await scheduler.resolve_interaction(
            interaction_id,
            body.version,
            body.response,
            canceled=body.canceled,
        )
        return interaction

    @app.get("/api/events")
    async def event_stream() -> StreamingResponse:
        return StreamingResponse(
            events.stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.on_event("startup")
    async def startup() -> None:
        await scheduler.start()

    @app.on_event("shutdown")
    async def shutdown() -> None:
        await scheduler.stop()
        db.close()

    configured_static = os.environ.get("CODEX_TASKBOARD_STATIC_DIR", "").strip()
    static_root = Path(configured_static).expanduser() if configured_static else Path.cwd() / "dist" / "web"
    if not static_root.is_dir():
        bundled_root = Path(__file__).resolve().parents[2] / "dist" / "web"
        if bundled_root.is_dir():
            static_root = bundled_root
    if static_root.is_dir():
        assets = static_root / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/", include_in_schema=False)
        async def spa_index() -> FileResponse:
            return FileResponse(static_root / "index.html")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa_fallback(path: str) -> FileResponse:
            candidate = static_root / path
            if candidate.is_file() and static_root in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(static_root / "index.html")

    return app


def _snake_project_fields(fields: dict[str, Any]) -> dict[str, Any]:
    mapping = {
        "workspacePath": "workspace_path",
        "codexProjectId": "codex_project_id",
        "automationEnabled": "automation_enabled",
        "reviewRequired": "review_required",
        "quotaAutoResumeEnabled": "quota_auto_resume_enabled",
    }
    return {mapping.get(key, key): value for key, value in fields.items()}


def _snake_task_fields(fields: dict[str, Any]) -> dict[str, Any]:
    return {("reasoning_effort" if key == "reasoningEffort" else key): value for key, value in fields.items()}


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run(
        "codex_taskboard.app:app",
        host=os.environ.get("CODEX_TASKBOARD_HOST", "127.0.0.1"),
        port=int(os.environ.get("CODEX_TASKBOARD_PORT", "47823")),
        reload=False,
    )


if __name__ == "__main__":
    main()
