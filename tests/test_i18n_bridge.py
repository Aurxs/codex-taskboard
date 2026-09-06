"""The language bridge accepts only the authenticated renderer context."""
import json
from unittest import TestCase
from unittest.mock import Mock

from injector.cdp_injector import CdpInjector


class LanguageBridgeTests(TestCase):
    def test_language_requires_host_context_and_valid_string(self):
        injector = CdpInjector.__new__(CdpInjector)
        injector._emit = Mock()
        injector._send_host_response = Mock()
        connection = Mock(host_context_id=7)
        params = {"executionContextId": 8, "payload": json.dumps({
            "id": "language-1", "action": "language", "language": "zh-CN",
        })}
        injector._handle_host_binding(connection, params)
        injector._emit.assert_not_called()
        injector._send_host_response.assert_not_called()

        params["executionContextId"] = 7
        injector._handle_host_binding(connection, params)
        injector._emit.assert_called_once_with("language", language="zh-CN")
        self.assertTrue(injector._send_host_response.call_args.args[2]["ok"])

        for invalid in [None, 123, "x" * 65]:
            params["payload"] = json.dumps({"id": "invalid", "action": "language", "language": invalid})
            injector._handle_host_binding(connection, params)
            self.assertFalse(injector._send_host_response.call_args.args[2]["ok"])
        self.assertEqual(injector._emit.call_count, 1)
