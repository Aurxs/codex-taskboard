"""Minimal JSON-RPC client for the official Codex App Server.

The client intentionally sends only the parameters needed to select a
workspace and a turn. Model and effort are sent only when explicitly selected
for a task. It does not add an approval policy,
sandbox, personality, goal, or hidden context, so the user's existing Codex
configuration remains authoritative.
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
import sys
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import AppServerError, UsageLimitExceeded

ServerRequestHandler = Callable[[str, str | int, dict[str, Any]], Awaitable[Any]]
NotificationHandler = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass(slots=True)
class RpcFailure(Exception):
    """An error returned by the App Server JSON-RPC peer."""

    code: int | str | None
    message: str
    data: Any = None

    def __str__(self) -> str:
        return self.message


class AppServerUnavailable(AppServerError):
    code = "CODEX_APP_SERVER_UNAVAILABLE"


def default_command() -> list[str]:
    configured = os.environ.get("CODEX_APP_SERVER_COMMAND", "").strip()
    if configured:
        return shlex.split(configured)
    configured_bin = os.environ.get("CODEX_BIN", "").strip()
    codex_bin = configured_bin or _installed_codex_bundle()
    if not codex_bin:
        codex_bin = shutil.which("codex")
    # --stdio is explicit for sidecar launches and is equivalent to the
    # app-server default transport in Codex 0.153.x.
    return [codex_bin or "codex", "app-server", "--stdio"]


def _installed_codex_bundle() -> str | None:
    """Find the App Server binary shipped with an installed Codex app.

    The desktop app is the user's source of truth for authentication and
    configuration.  Prefer its bundled binary so a Taskboard launch does not
    require a separate PATH install or an API key.  The explicit environment
    overrides above remain useful for tests and development.
    """
    if sys.platform != "darwin":
        return None
    app_roots = (Path("/Applications"), Path.home() / "Applications")
    for app_root in app_roots:
        for app_name in ("ChatGPT.app", "Codex.app"):
            candidate = app_root / app_name / "Contents" / "Resources" / "codex"
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    return None


def _walk_values(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_values(child)


def usage_error_info(value: Any) -> dict[str, Any] | None:
    """Return structured ``UsageLimitExceeded`` metadata when present."""
    for candidate in _walk_values(value):
        info = candidate.get("codexErrorInfo")
        if isinstance(info, dict):
            kind = info.get("type") or info.get("code") or info.get("name")
            if kind in {"UsageLimitExceeded", "usageLimitExceeded"} or "usage" in str(kind).lower():
                return info
        elif isinstance(info, str) and info.lower() in {
            "usagelimitexceeded",
            "usage_limit_exceeded",
        }:
            return {"type": info}
        error_type = candidate.get("type") or candidate.get("code")
        if error_type in {"UsageLimitExceeded", "usageLimitExceeded"}:
            return candidate
    return None


def _extract_reset_at(value: Any) -> str | float | int | None:
    for candidate in _walk_values(value):
        for key in ("resetsAt", "resetAt", "reset_at"):
            if key in candidate and candidate[key] is not None:
                return candidate[key]
    return None


def extract_identifier(value: Any, *names: str) -> str | None:
    """Find an id in the few response shapes used by App Server versions."""
    wanted = set(names)
    for candidate in _walk_values(value):
        for name in wanted:
            found = candidate.get(name)
            if isinstance(found, (str, int)):
                return str(found)
    return None


class CodexAppServer:
    """One stdio App Server process shared by project task turns."""

    def __init__(
        self,
        command: Sequence[str] | None = None,
        *,
        request_handler: ServerRequestHandler | None = None,
        notification_handler: NotificationHandler | None = None,
    ) -> None:
        self.command = list(command or default_command())
        self.request_handler = request_handler
        self.notification_handler = notification_handler
        self.process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._write_lock = asyncio.Lock()
        self._next_id = 1
        self._pending: dict[str | int, asyncio.Future[Any]] = {}
        self._turn_waiters: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._completed_turns: dict[str, dict[str, Any]] = {}
        self._turn_tasks: dict[str, str] = {}
        self._thread_tasks: dict[str, str] = {}
        self._server_request_tasks: set[asyncio.Task[None]] = set()
        self.last_stderr: str | None = None

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.returncode is None

    def task_for_thread(self, thread_id: str) -> str | None:
        return self._thread_tasks.get(thread_id)

    def task_for_turn(self, turn_id: str) -> str | None:
        return self._turn_tasks.get(turn_id)

    def register_thread_task(self, thread_id: str, task_id: str) -> None:
        self._thread_tasks[thread_id] = task_id

    def register_turn_task(self, turn_id: str, task_id: str) -> None:
        self._turn_tasks[turn_id] = task_id

    async def start(self) -> None:
        if self.running:
            return
        try:
            self.process = await asyncio.create_subprocess_exec(
                *self.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=16 * 1024 * 1024,
            )
        except (FileNotFoundError, OSError) as exc:
            self.process = None
            raise AppServerUnavailable(
                f"Unable to start Codex App Server ({' '.join(self.command)}): {exc}"
            ) from exc
        self._reader_task = asyncio.create_task(self._reader_loop(), name="codex-app-server-reader")
        self._stderr_task = asyncio.create_task(self._stderr_loop(), name="codex-app-server-stderr")
        try:
            # ``initialize`` is protocol negotiation, not task context.  Do
            # not send any user-configurable execution settings here.
            await self.request(
                "initialize",
                {
                    "clientInfo": {
                        "name": "codex-taskboard",
                        "title": "Codex Taskboard",
                        "version": "0.1.0",
                    }
                },
                timeout=20,
            )
            await self.notify("initialized", {})
        except Exception:
            await self.stop()
            raise

    async def stop(self) -> None:
        process = self.process
        self.process = None
        if process is None:
            return
        for future in [*self._pending.values(), *self._turn_waiters.values()]:
            if not future.done():
                future.set_exception(AppServerUnavailable("Codex App Server stopped"))
        self._pending.clear()
        self._turn_waiters.clear()
        self._completed_turns.clear()
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=3)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        for task in (self._reader_task, self._stderr_task):
            if task is not None and not task.done():
                task.cancel()
        for task in tuple(self._server_request_tasks):
            task.cancel()
        self._reader_task = None
        self._stderr_task = None

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> Any:
        process = self.process
        if process is None or process.returncode is not None or process.stdin is None:
            raise AppServerUnavailable("Codex App Server is not running")
        request_id: int = self._next_id
        self._next_id += 1
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._pending[request_id] = future
        message = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        try:
            async with self._write_lock:
                process.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode())
                await process.stdin.drain()
            result = await asyncio.wait_for(future, timeout=timeout) if timeout else await future
            return result
        except asyncio.TimeoutError as exc:
            raise AppServerUnavailable(f"Timed out waiting for Codex method {method}") from exc
        finally:
            self._pending.pop(request_id, None)

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        process = self.process
        if process is None or process.returncode is not None or process.stdin is None:
            raise AppServerUnavailable("Codex App Server is not running")
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        async with self._write_lock:
            process.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode())
            await process.stdin.drain()

    async def respond(
        self,
        request_id: str | int,
        *,
        result: Any = None,
        error: dict[str, Any] | None = None,
    ) -> None:
        process = self.process
        if process is None or process.returncode is not None or process.stdin is None:
            return
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id}
        if error is None:
            message["result"] = result
        else:
            message["error"] = error
        async with self._write_lock:
            process.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode())
            await process.stdin.drain()

    async def create_worktree(self, workspace_path: str, branch: str | None) -> dict[str, str]:
        raise AppServerUnavailable("新工作树需要 Codex 桌面原生连接")

    async def set_worktree_owner(self, git_root: str, thread_id: str) -> None:
        raise AppServerUnavailable("新工作树需要 Codex 桌面原生连接")

    async def start_thread(self, workspace_path: str) -> str:
        response = await self.request("thread/start", {"cwd": workspace_path}, timeout=30)
        thread_id = extract_identifier(response, "threadId", "id")
        if thread_id is None:
            raise AppServerError("Codex thread/start returned no thread id", details=response)
        return thread_id

    async def resume_thread(self, thread_id: str) -> Any:
        return await self.request("thread/resume", {"threadId": thread_id}, timeout=30)

    async def set_thread_name(self, thread_id: str, name: str) -> Any:
        # turn/start can return before the new rollout's metadata is flushed.
        # Renaming is idempotent; retry only this known persistence race.
        delays = (0.25, 0.5, 1.0, 2.0)
        for attempt in range(len(delays) + 1):
            try:
                return await self.request(
                    "thread/name/set", {"threadId": thread_id, "name": name}, timeout=20
                )
            except RpcFailure as exc:
                message = str(exc)
                if not (
                    "failed to read session metadata" in message
                    and "rollout at " in message
                    and " is empty" in message
                ) or attempt == len(delays):
                    raise
                await asyncio.sleep(delays[attempt])

    async def read_thread(self, thread_id: str, *, include_turns: bool = True) -> Any:
        return await self.request(
            "thread/read",
            {"threadId": thread_id, "includeTurns": include_turns},
            timeout=30,
        )

    async def list_models(self) -> list[dict[str, Any]]:
        models = []
        cursor = None
        seen = set()
        while True:
            result = await self.request("model/list", {"limit": 100, "includeHidden": False, **({"cursor": cursor} if cursor else {})})
            models.extend(result.get("data", []))
            cursor = result.get("nextCursor")
            if not cursor or cursor in seen:
                return models
            seen.add(cursor)

    async def start_turn(self, thread_id: str, prompt: str, *, task_id: str,
                         model: str | None = None, effort: str | None = None) -> str:
        response = await self.request(
            "turn/start",
            {
                "threadId": thread_id,
                "input": [{"type": "text", "text": prompt}],
                **({"model": model} if model else {}),
                **({"effort": effort} if effort else {}),
            },
            timeout=30,
        )
        turn_id = extract_identifier(response, "turnId", "id")
        if turn_id is None:
            raise AppServerError("Codex turn/start returned no turn id", details=response)
        self.register_turn_task(turn_id, task_id)
        return turn_id

    async def wait_for_turn(self, turn_id: str, *, timeout: float | None = None) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        completed = self._completed_turns.pop(turn_id, None)
        if completed is not None:
            return completed
        future = self._turn_waiters.get(turn_id)
        if future is None:
            future = loop.create_future()
            self._turn_waiters[turn_id] = future
        try:
            return await asyncio.wait_for(future, timeout=timeout) if timeout else await future
        finally:
            self._turn_waiters.pop(turn_id, None)

    async def interrupt_turn(self, thread_id: str, turn_id: str) -> Any:
        return await self.request(
            "turn/interrupt", {"threadId": thread_id, "turnId": turn_id}, timeout=20
        )

    async def steer_turn(self, thread_id: str, turn_id: str, text: str) -> Any:
        return await self.request("turn/steer", {
            "threadId": thread_id, "expectedTurnId": turn_id,
            "input": [{"type": "text", "text": text}],
        }, timeout=30)

    async def read_rate_limits(self) -> Any:
        response = await self.request("account/rateLimits/read", {}, timeout=20)
        return {
            "raw": response,
            "resetsAt": _extract_reset_at(response),
        }

    async def _reader_loop(self) -> None:
        process = self.process
        if process is None or process.stdout is None:
            return
        try:
            while True:
                raw = await process.stdout.readline()
                if not raw:
                    break
                try:
                    message = json.loads(raw.decode(errors="replace"))
                except json.JSONDecodeError:
                    continue
                if not isinstance(message, dict):
                    continue
                if "method" in message and "id" in message:
                    task = asyncio.create_task(
                        self._handle_server_request(
                            message["method"], message["id"], message.get("params") or {}
                        ),
                        name=f"codex-server-request-{message['id']}",
                    )
                    self._server_request_tasks.add(task)
                    task.add_done_callback(self._server_request_tasks.discard)
                    continue
                if "id" in message:
                    request_id = message["id"]
                    future = self._pending.get(request_id)
                    if future is None or future.done():
                        continue
                    if "error" in message:
                        error = message["error"]
                        info = usage_error_info(error)
                        if info is not None:
                            future.set_exception(
                                UsageLimitExceeded(
                                    str(error.get("message", "Codex usage limit exceeded"))
                                    if isinstance(error, dict)
                                    else "Codex usage limit exceeded",
                                    error=error,
                                    resets_at=_extract_reset_at(error),
                                )
                            )
                        else:
                            future.set_exception(
                                RpcFailure(
                                    error.get("code") if isinstance(error, dict) else None,
                                    error.get("message", "Codex request failed")
                                    if isinstance(error, dict)
                                    else str(error),
                                    error.get("data") if isinstance(error, dict) else error,
                                )
                            )
                    else:
                        future.set_result(message.get("result"))
                    continue
                method = message.get("method")
                if isinstance(method, str):
                    params = message.get("params")
                    if not isinstance(params, dict):
                        params = {}
                    self._handle_notification(method, params)
        finally:
            unavailable = AppServerUnavailable("Codex App Server exited")
            for future in [*self._pending.values(), *self._turn_waiters.values()]:
                if not future.done():
                    future.set_exception(unavailable)

    def _handle_notification(self, method: str, params: dict[str, Any]) -> None:
        turn_id = extract_identifier(params, "turnId")
        if method == "turn/completed" or method.endswith("/turn/completed"):
            if turn_id is None:
                turn_id = extract_identifier(params, "id")
            if turn_id is not None:
                future = self._turn_waiters.get(turn_id)
                if future is not None and not future.done():
                    future.set_result(params)
                else:
                    self._completed_turns[turn_id] = params
        if self.notification_handler is not None:
            task = asyncio.create_task(
                self.notification_handler(method, params),
                name=f"codex-notification-{method.replace('/', '-')}",
            )
            task.add_done_callback(_consume_task_exception)

    async def _handle_server_request(
        self, method: str, request_id: str | int, params: dict[str, Any]
    ) -> None:
        if self.request_handler is None:
            await self.respond(
                request_id,
                error={"code": -32601, "message": "Server requests are not supported"},
            )
            return
        try:
            result = await self.request_handler(method, request_id, params)
        except Exception as exc:
            await self.respond(
                request_id,
                error={"code": -32000, "message": str(exc) or "Request canceled"},
            )
        else:
            await self.respond(request_id, result=result)

    async def _stderr_loop(self) -> None:
        process = self.process
        if process is None or process.stderr is None:
            return
        try:
            while True:
                line = await process.stderr.readline()
                if not line:
                    break
                self.last_stderr = line.decode(errors="replace").strip()[-4000:]
        except asyncio.CancelledError:
            raise


def _consume_task_exception(task: asyncio.Task[Any]) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception:
        # Notification failures are reported to the task by the scheduler;
        # never let one callback tear down the protocol reader.
        pass
