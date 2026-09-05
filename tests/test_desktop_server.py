import asyncio
import unittest
from unittest.mock import AsyncMock, Mock

from codex_taskboard.desktop_server import DesktopAppServer
from codex_taskboard.app_server import RpcFailure


class DesktopServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_transport_uses_native_service_and_does_not_spawn_or_stop_it(self):
        server = DesktopAppServer(notification_handler=AsyncMock())
        server.transport = Mock(return_value={"result": {"turn": {"id": "turn-1"}}})
        await server.start()
        self.assertIsNone(server.process)
        await server.steer_turn("thread-1", "turn-1", "follow up")
        self.assertEqual(server.transport.call_args.args, ("turn/steer", {
            "threadId": "thread-1", "expectedTurnId": "turn-1", "input": [{"type": "text", "text": "follow up"}],
        }, 30))
        server.transport.return_value = {"error": {"code": 409, "message": "turn finished"}}
        with self.assertRaises(RpcFailure):
            await server.steer_turn("thread-1", "turn-1", "too late")
        count = server.transport.call_count
        await server.stop()
        self.assertEqual(server.transport.call_count, count)

    async def test_native_requests_are_passive_and_unrelated_threads_are_ignored(self):
        handler = AsyncMock()
        server = DesktopAppServer(notification_handler=handler)
        server.transport = Mock()
        await server.start()
        server.register_thread_task("thread-1", "task-1")
        for thread_id in ("unrelated", "thread-1"):
            server._receive({"type": "mcp-request", "hostId": "local", "request": {
                "id": 42, "method": "item/commandExecution/requestApproval", "params": {"threadId": thread_id},
            }})
        await asyncio.sleep(0)
        handler.assert_awaited_once()
        self.assertEqual(handler.call_args.args[0], "desktop/request")
        server.transport.assert_not_called()
        server._receive({"type": "mcp-notification", "hostId": "local", "method": "serverRequest/resolved", "params": {"requestId": 42}})
        await asyncio.sleep(0)
        self.assertEqual(handler.call_args.args[1]["threadId"], "thread-1")
        self.assertFalse(server._requests)
        await server.stop()

