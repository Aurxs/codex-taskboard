"""Use the desktop's existing App Server without owning another session process.

The launcher supplies the transport. Only real desktop notifications are observed;
no synthetic notifications or changes to the native conversation UI are needed.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

from .app_server import AppServerUnavailable, CodexAppServer, RpcFailure, usage_error_info
from .errors import UsageLimitExceeded
from .constants import APPROVAL_METHODS


class DesktopAppServer(CodexAppServer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.transport = None
        self.available = lambda: self.transport is not None
        self._loop = None
        self._connected = False
        self._requests: dict[str | int, dict[str, Any]] = {}

    @property
    def running(self) -> bool:
        return self._connected

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        if self.transport is None:
            raise AppServerUnavailable("Codex 桌面连接尚未就绪，请稍后重试")
        self._connected = True

    async def stop(self) -> None:
        self._connected = False
        self._requests.clear()
        self._completed_turns.clear()
        self._loop = None
        # The desktop owns the process and its active turns. Never stop it.

    async def request(self, method, params=None, *, timeout=None) -> Any:
        if not self.running or self.transport is None:
            raise AppServerUnavailable("Codex 桌面连接尚未就绪")
        try:
            response = await asyncio.to_thread(self.transport, method, params or {}, timeout or 30)
        except Exception as exc:
            raise AppServerUnavailable(str(exc)) from exc
        if response.get("error"):
            error = response["error"]
            if usage_error_info(error):
                raise UsageLimitExceeded(error.get("message", "Usage limit exceeded"), error=error)
            raise RpcFailure(error.get("code"), error.get("message", "Codex request failed"), error.get("data"))
        return response.get("result")

    async def create_worktree(self, workspace_path: str, branch: str | None) -> dict[str, str]:
        result = await self.request("desktop/worktree-create-managed", {
            "hostId": "local", "cwd": workspace_path,
            "startingState": {"type": "branch", "branchName": branch} if branch else {"type": "working-tree"},
            "localEnvironmentConfigPath": None, "streamId": str(uuid.uuid4()),
        }, timeout=300)
        if not isinstance(result, dict) or not all(isinstance(result.get(key), str) and result[key] for key in ("worktreeWorkspaceRoot", "worktreeGitRoot")):
            raise AppServerUnavailable("Codex 未返回工作树路径，请在 Codex 中检查工作树")
        return result

    async def set_worktree_owner(self, git_root: str, thread_id: str) -> None:
        await self.request("desktop/worktree-set-owner-thread", {
            "hostId": "local", "worktree": git_root, "conversationId": thread_id,
        }, timeout=30)

    async def respond(self, request_id, *, result=None, error=None) -> None:
        if self.transport is None:
            raise AppServerUnavailable("Codex desktop disconnected")
        response = {"id": request_id, **({"error": error} if error else {"result": result})}
        await asyncio.to_thread(self.transport, None, response, 20)

    def receive(self, message: dict[str, Any]) -> None:
        if self._loop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._receive, message)

    def _receive(self, message: dict[str, Any]) -> None:
        if not self.running or message.get("hostId") != "local":
            return
        if message.get("type") == "mcp-notification":
            method, params = message.get("method"), message.get("params") or {}
            if method == "serverRequest/resolved":
                pending = self._requests.pop(params.get("requestId"), None)
                if pending:
                    params = {**pending, **params}
            if params.get("threadId") not in self._thread_tasks and method not in {
                "account/rateLimits/updated", "account/rateLimits/changed",
            }:
                return
            if isinstance(method, str):
                self._handle_notification(method, params)
        elif message.get("type") == "mcp-request":
            request = message.get("request") or {}
            params = request.get("params") or {}
            if request.get("method") not in {*APPROVAL_METHODS, "item/tool/requestUserInput"}:
                return
            # Native requests for unrelated conversations remain entirely native.
            if params.get("threadId") not in self._thread_tasks:
                return
            request_id = request.get("id")
            if request_id is None or request_id in self._requests:
                return
            self._requests[request_id] = params
            # Observing a request must never answer or reject it. The native
            # composer remains the owner until a user explicitly answers here.
            self._handle_notification("desktop/request", {
                **params, "requestId": request_id, "requestMethod": request["method"],
            })
