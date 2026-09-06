"""Exercise the native fetch envelope without connecting to a desktop."""
import json
from pathlib import Path
import subprocess
import threading
import unittest
from unittest.mock import Mock

from injector.cdp_injector import CdpInjector


class WorktreeBridgeTests(unittest.TestCase):
    def test_fetch_success_and_error_are_decoded(self):
        injector = CdpInjector.__new__(CdpInjector)
        injector._connections_lock = threading.Lock()
        injector._native_decoder = Path("injector/native_messages.js").read_text()
        connection = Mock(_closed=False, host_context_id=1)
        injector.connections = {"test": connection}
        connection.request.return_value = {"result": {"value": {"result": {}}}}
        injector.native_request("desktop/worktree-create-managed", {"cwd": "/project", "hostId": "local"}, 1)
        expression = connection.request.call_args.args[1]["expression"]
        harness = r'''
const expression = EXPRESSION;
const listeners = new Set();
globalThis.window = {
  location: {origin: 'test'},
  addEventListener: (_, listener) => listeners.add(listener),
  removeEventListener: (_, listener) => listeners.delete(listener),
  electronBridge: {sendMessageFromView: message => {
    if (message.type !== 'fetch' || message.url !== 'vscode://codex/worktree-create-managed') throw Error('wrong route');
    if (JSON.parse(message.body).cwd !== '/project') throw Error('wrong body');
    queueMicrotask(() => { for (const listener of listeners) listener({source: window, origin: 'test', data: {
      type: 'fetch-response', requestId: message.requestId, ...reply
    }}); });
  }}
};
let reply = {responseType: 'success', bodyJsonString: JSON.stringify({worktreeWorkspaceRoot: '/isolated'})};
const success = await eval(expression);
if (success.result.worktreeWorkspaceRoot !== '/isolated' || listeners.size) throw Error('success decoding');
reply = {responseType: 'error', error: 'missing branch'};
const failure = await eval(expression);
if (failure.error.message !== 'missing branch' || listeners.size) throw Error('error decoding');
'''.replace("EXPRESSION", json.dumps(expression))
        subprocess.run(["node", "--input-type=module", "-e", harness], check=True, capture_output=True, text=True, timeout=5)
