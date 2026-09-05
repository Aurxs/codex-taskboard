from __future__ import annotations

import sys
import textwrap
import unittest
from typing import Any
from unittest.mock import patch

from codex_taskboard.app_server import CodexAppServer, default_command, usage_error_info


FAKE_JSONL_SERVER = textwrap.dedent(
    r'''
    import json
    import sys

    seen = []
    for raw in sys.stdin:
        message = json.loads(raw)
        method = message.get("method")
        if method:
            seen.append({"method": method, "params": message.get("params")})
        if "id" not in message:
            continue
        if method == "initialize":
            result = {}
        elif method == "thread/start":
            result = {"thread": {"id": "thread-1"}}
        elif method == "thread/resume":
            result = {"thread": {"id": "thread-1"}}
        elif method == "thread/name/set":
            result = {}
        elif method == "turn/start":
            result = {"turn": {"id": "turn-1"}}
        elif method == "turn/interrupt":
            result = {}
        elif method == "account/rateLimits/read":
            result = {
                "rateLimits": {"resetsAt": "2099-01-01T00:00:00Z"},
                "seen": seen,
            }
        else:
            result = {}
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}) + "\n")
        sys.stdout.flush()
    ''',
)


class AppServerProtocolTests(unittest.IsolatedAsyncioTestCase):
    def test_default_command_prefers_installed_codex_app_bundle(self) -> None:
        with patch.dict(
            "os.environ",
            {"CODEX_APP_SERVER_COMMAND": "", "CODEX_BIN": ""},
            clear=False,
        ), patch(
            "codex_taskboard.app_server._installed_codex_bundle",
            return_value="/Applications/Codex.app/Contents/Resources/codex",
        ), patch(
            "codex_taskboard.app_server.shutil.which",
            return_value="/opt/homebrew/bin/codex",
        ):
            self.assertEqual(
                default_command(),
                [
                    "/Applications/Codex.app/Contents/Resources/codex",
                    "app-server",
                    "--stdio",
                ],
            )

    async def test_wire_methods_send_only_workspace_and_prompt(self) -> None:
        server = CodexAppServer([sys.executable, "-u", "-c", FAKE_JSONL_SERVER])
        await server.start()
        try:
            thread_id = await server.start_thread("/tmp/codex-project")
            self.assertEqual(thread_id, "thread-1")
            await server.set_thread_name(thread_id, "[APP-1] Build")
            turn_id = await server.start_turn(thread_id, "complete the task", task_id="task-1")
            self.assertEqual(turn_id, "turn-1")
            await server.resume_thread(thread_id)
            limits = await server.read_rate_limits()
            self.assertEqual(limits["resetsAt"], "2099-01-01T00:00:00Z")
            seen = limits["raw"]["seen"]

            initialize = next(item for item in seen if item["method"] == "initialize")
            self.assertEqual(
                set(initialize["params"]),
                {"clientInfo"},
            )
            start = next(item for item in seen if item["method"] == "thread/start")
            self.assertEqual(start["params"], {"cwd": "/tmp/codex-project"})
            name = next(item for item in seen if item["method"] == "thread/name/set")
            self.assertEqual(name["params"], {"threadId": thread_id, "name": "[APP-1] Build"})
            turn = next(item for item in seen if item["method"] == "turn/start")
            self.assertEqual(
                turn["params"],
                {"threadId": thread_id, "input": [{"type": "text", "text": "complete the task"}]},
            )
            self.assertNotIn("model", turn["params"])
            self.assertNotIn("effort", turn["params"])
            self.assertNotIn("sandboxPolicy", turn["params"])
            self.assertNotIn("approvalPolicy", turn["params"])
            self.assertNotIn("developerInstructions", turn["params"])
            resume = next(item for item in seen if item["method"] == "thread/resume")
            self.assertEqual(resume["params"], {"threadId": thread_id})
            rate_limits = next(item for item in seen if item["method"] == "account/rateLimits/read")
            self.assertEqual(rate_limits["params"], {})
        finally:
            await server.stop()

    async def test_server_request_responses_use_json_rpc_result_or_error_shape(self) -> None:
        responses: list[dict[str, Any]] = []

        class RecordingServer(CodexAppServer):
            async def respond(self, request_id: str | int, *, result: Any = None, error: dict[str, Any] | None = None) -> None:
                message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id}
                if error is None:
                    message["result"] = result
                else:
                    message["error"] = error
                responses.append(message)

        async def handler(method: str, request_id: str | int, params: dict[str, Any]) -> Any:
            self.assertEqual(method, "item/commandExecution/requestApproval")
            self.assertEqual(params, {"threadId": "thread-1"})
            return {"decision": "approve"}

        server = RecordingServer(request_handler=handler)
        await server._handle_server_request(
            "item/commandExecution/requestApproval", "rpc-1", {"threadId": "thread-1"}
        )
        self.assertEqual(
            responses,
            [{"jsonrpc": "2.0", "id": "rpc-1", "result": {"decision": "approve"}}],
        )

        async def failing_handler(method: str, request_id: str | int, params: dict[str, Any]) -> Any:
            raise RuntimeError("declined")

        server.request_handler = failing_handler
        await server._handle_server_request("unknown", 9, {})
        self.assertEqual(responses[-1]["jsonrpc"], "2.0")
        self.assertEqual(responses[-1]["id"], 9)
        self.assertEqual(responses[-1]["error"]["code"], -32000)
        self.assertEqual(responses[-1]["error"]["message"], "declined")

    def test_real_0153_usage_limit_wire_value_is_detected(self) -> None:
        self.assertEqual(
            usage_error_info(
                {"error": {"codexErrorInfo": "usageLimitExceeded", "message": "quota"}}
            ),
            {"type": "usageLimitExceeded"},
        )
        self.assertIsNotNone(
            usage_error_info(
                {"error": {"codexErrorInfo": "usageLimitExceeded", "resetsAt": 123}}
            )
        )
        self.assertIsNone(usage_error_info({"error": {"codexErrorInfo": "other"}}))


if __name__ == "__main__":
    unittest.main()
