"""Skill deployment and explicit invocation, isolated from the user's Codex home."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch

from codex_taskboard.app_server import CodexAppServer
from codex_taskboard.errors import ValidationError
from codex_taskboard.desktop_server import DesktopAppServer
from codex_taskboard.task_skills import (
    BUNDLED_SKILLS_ROOT, EXECUTE_SKILL, SKILL_NAMES,
    global_skills_root, install_skills, turn_input,
)


class TaskSkillTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="taskboard-skills-")
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve() / "Codex 用户目录"
        env = patch.dict(os.environ, {"CODEX_HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)

    def test_install_and_upgrade_are_scoped_and_idempotent(self):
        personal = self.home / "skills/personal/SKILL.md"
        personal.parent.mkdir(parents=True)
        personal.write_text("personal skill")
        root = install_skills()
        self.assertEqual(root, self.home / "skills")
        for name in SKILL_NAMES:
            for relative in ("SKILL.md", "agents/openai.yaml"):
                self.assertEqual((root / name / relative).read_bytes(), (BUNDLED_SKILLS_ROOT / name / relative).read_bytes())
            self.assertIn("allow_implicit_invocation: false", (root / name / "agents/openai.yaml").read_text())
        skill = root / EXECUTE_SKILL / "SKILL.md"
        original_mtime = skill.stat().st_mtime_ns
        install_skills()
        self.assertEqual(skill.stat().st_mtime_ns, original_mtime)
        skill.write_text("old version")
        install_skills()
        self.assertEqual(skill.read_bytes(), (BUNDLED_SKILLS_ROOT / EXECUTE_SKILL / "SKILL.md").read_bytes())
        self.assertEqual(personal.read_text(), "personal skill")
        self.assertFalse((self.home / "config.toml").exists())

    def test_existing_unowned_skill_is_preserved(self):
        skill = global_skills_root() / EXECUTE_SKILL / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("user's skill")
        with self.assertRaisesRegex(RuntimeError, "unmanaged"):
            install_skills()
        self.assertEqual(skill.read_text(), "user's skill")
        self.assertFalse((global_skills_root() / SKILL_NAMES[1]).exists())

    def test_default_directory_and_missing_install_does_not_write(self):
        with patch.dict(os.environ, {"CODEX_HOME": ""}), patch("pathlib.Path.home", return_value=self.home):
            self.assertEqual(global_skills_root(), self.home / ".codex/skills")
        with self.assertRaisesRegex(ValidationError, "not installed"):
            turn_input("work", EXECUTE_SKILL)
        self.assertFalse(self.home.exists())
        self.assertEqual(turn_input("ordinary conversation"), [{"type": "text", "text": "ordinary conversation"}])

    async def test_both_transports_use_explicit_skill_inputs_only_when_requested(self):
        root = install_skills()
        for server_type in (CodexAppServer, DesktopAppServer):
            server = server_type(["unused"])
            server.request = AsyncMock(return_value={"turn": {"id": "turn"}})
            for name in SKILL_NAMES:
                await server.start_turn("thread", "原始任务", task_id="task", skill=name)
                params = server.request.call_args.args[1]
                self.assertEqual(set(params), {"threadId", "input"})
                self.assertIn(f"${name}", params["input"][0]["text"])
                self.assertTrue(params["input"][0]["text"].endswith("原始任务"))
                self.assertEqual(params["input"][1], {"type": "skill", "name": name, "path": str(root / name / "SKILL.md")})
            await server.steer_turn("thread", "turn", "跟进", skill=EXECUTE_SKILL)
            self.assertEqual(server.request.call_args.args[1]["input"][1]["name"], EXECUTE_SKILL)
            await server.start_turn("native", "普通对话", task_id="native")
            self.assertEqual(server.request.call_args.args[1]["input"], [{"type": "text", "text": "普通对话"}])
            await server.steer_turn("native", "turn", "继续")
            self.assertEqual(server.request.call_args.args[1]["input"], [{"type": "text", "text": "继续"}])
