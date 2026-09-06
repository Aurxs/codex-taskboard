"""Verify browser routing without driving the desktop or a website."""
import json
from pathlib import Path
import subprocess
import threading
import unittest
from unittest.mock import Mock

from injector.cdp_injector import CdpInjector


class BrowserBridgeTests(unittest.TestCase):
    def test_route_order_params_and_failure(self):
        injector = CdpInjector.__new__(CdpInjector)
        injector._connections_lock = threading.Lock()
        injector._native_decoder = Path("injector/native_messages.js").read_text()
        connection = Mock(_closed=False, host_context_id=1)
        injector.connections = {"test": connection}
        connection.request.return_value = {"result": {"value": {"result": {}}}}
        cases = []
        for method in ("turn/start", "turn/steer", "thread/read"):
            params = {"threadId": 'thread-"quoted', "input": [{"type": "text", "text": "browse"}]}
            injector.native_request(method, params, 1)
            cases.append({"method": method, "params": params,
                          "expression": connection.request.call_args.args[1]["expression"]})
        harness = r'''
import assert from 'node:assert/strict';
const cases = CASES, listeners = new Set();
let sent = [], failCapture = false;
globalThis.window = {
  location: {origin: 'test'},
  addEventListener: (_, listener) => listeners.add(listener),
  removeEventListener: (_, listener) => listeners.delete(listener),
  electronBridge: {sendMessageFromView: async message => {
    sent.push(message);
    if (message.type === 'browser-use-session-route-capture') {
      if (failCapture) throw Error('capture failed');
      return;
    }
    assert.equal(message.type, 'mcp-request');
    queueMicrotask(() => { for (const listener of listeners) listener({
      source: window, origin: 'test', data: {type: 'mcp-response', hostId: 'local',
        message: {id: message.request.id, result: {turn: {id: 'turn-1'}}}}
    }); });
  }}
};
for (const item of cases) {
  sent = [];
  assert.equal((await eval(item.expression)).result.turn.id, 'turn-1');
  const expectsRoute = item.method.startsWith('turn/');
  assert.equal(sent.length, expectsRoute ? 2 : 1);
  if (expectsRoute) assert.deepEqual(sent[0], {
    type: 'browser-use-session-route-capture', conversationId: item.params.threadId
  });
  assert.equal(sent.at(-1).request.method, item.method);
  assert.deepEqual(sent.at(-1).request.params, item.params);
  assert.equal(listeners.size, 0);
}
sent = [];
failCapture = true;
assert.match((await eval(cases[0].expression)).error.message, /capture failed/);
assert.equal(sent.length, 1); // Never dispatch input after a failed capture.
assert.equal(listeners.size, 0);
'''.replace("CASES", json.dumps(cases))
        subprocess.run(["node", "--input-type=module", "-e", harness],
                       check=True, capture_output=True, text=True, timeout=5)
