import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch

from codex_taskboard.app_server import CodexAppServer
from codex_taskboard.platforms import macos
from codex_taskboard.verification import run_commands


class CompatibilityTests(unittest.IsolatedAsyncioTestCase):
    def test_current_and_legacy_cli_layouts(self):
        with TemporaryDirectory() as folder:
            app = Path(folder)
            legacy = app / "Contents/Resources/codex"
            modern = app / "Contents/Resources/codex-cli/bin/codex"
            modern.parent.mkdir(parents=True)
            for path in (legacy, modern):
                path.write_text("#!/bin/sh\n")
                path.chmod(0o755)
            with patch.object(macos, "discover_app", return_value=app):
                self.assertEqual(macos.bundled_agent(), str(modern))
                modern.unlink()
                self.assertEqual(macos.bundled_agent(), str(legacy))

    async def test_readonly_planner_and_plan_turn_match_pinned_schema(self):
        server = CodexAppServer(["unused"])
        server.request = AsyncMock(return_value={"thread": {"id": "thread"}, "model": "test"})
        await server.start_thread("/workspace", read_only=True)
        params = server.request.call_args.args[1]
        schema = json.loads(Path("tests/fixtures/codex-0.159.2/ThreadStartParams.json").read_text())
        self.assertLessEqual(set(params), set(schema["properties"]))
        self.assertEqual(params["sandbox"], "read-only")
        self.assertEqual(params["approvalPolicy"], "never")
        server.request.return_value = {"turn": {"id": "turn"}}
        await server.start_turn("thread", "plan", task_id="task", plan=True)
        params = server.request.call_args.args[1]
        schema = json.loads(Path("tests/fixtures/codex-0.159.2/TurnStartParams.json").read_text())
        self.assertLessEqual(set(params), set(schema["properties"]))
        self.assertEqual(params["collaborationMode"]["mode"], "plan")

    def test_verification_reports_nonzero_and_timeout(self):
        import sys
        with TemporaryDirectory() as folder:
            records = run_commands(folder, [[sys.executable, "-c", "print('failure evidence'); raise SystemExit(7)"]])
            self.assertEqual(records[0]["exitCode"], 7)
            self.assertIn("failure evidence", records[0]["log"])
            records = run_commands(folder, [[sys.executable, "-c", "import time; time.sleep(10)"]], timeout=0.1)
            self.assertTrue(records[0]["timedOut"])
            self.assertNotEqual(records[0]["exitCode"], 0)
