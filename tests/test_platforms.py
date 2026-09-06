"""Small contracts at the OS boundary; never contact a real Codex app."""
from __future__ import annotations

import base64
import os
from pathlib import Path, PureWindowsPath
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from codex_taskboard import platforms
from codex_taskboard.platforms import windows
from codex_taskboard.git_workspace import normalize_scopes
from codex_taskboard.errors import ValidationError
from injector.cdp_injector import CdpInjector, _default_profile_path, without_taskboard_launcher_environment
from injector.sidecar import default_data_directory
from scripts.build_sidecar import native_target, validate_target


class PlatformTests(unittest.TestCase):
    def test_windows_data_and_profile_defaults_are_shared(self):
        from codex_taskboard.app import default_data_dir
        with TemporaryDirectory(prefix="taskboard 中文 ") as root, patch.dict(os.environ, {
            "APPDATA": root, "CODEX_TASKBOARD_DATA_DIR": "", "CODEX_TASKBOARD_DEV": "",
            "CODEX_TASKBOARD_CODEX_PROFILE": "",
        }), patch.object(sys, "platform", "win32"):
            expected = Path(root) / "com.codex.taskboard"
            self.assertEqual(default_data_directory(), expected)
            self.assertEqual(_default_profile_path(), expected / "codex-profile")
            with patch.dict(os.environ, {"CODEX_TASKBOARD_PACKAGED": "1"}):
                self.assertEqual(default_data_dir(), expected)
            with patch.dict(os.environ, {"CODEX_TASKBOARD_DATA_DIR": root}):
                self.assertEqual(default_data_directory(), Path(root).resolve())
                self.assertEqual(_default_profile_path(), Path(root).resolve() / "codex-profile")

    def test_windows_matches_exact_desktop_not_renderer_or_similarly_named_app(self):
        executable = PureWindowsPath(r"C:\Program Files\OpenAI\Codex.exe")
        self.assertTrue(windows.matches_process('"c:\\program files\\openai\\CODEX.EXE" --remote-debugging-port=9229', executable))
        for command in ('"C:\\Program Files\\OpenAI\\Codex.exe" --type=renderer',
                        '"C:\\Program Files\\OpenAI\\Codex.exe.old"', 'C:\\other\\Codex.exe'):
            self.assertFalse(windows.matches_process(command, executable))

    def test_discovery_honors_explicit_executable_with_unicode_and_spaces(self):
        with TemporaryDirectory(prefix="taskboard 中文 ") as root:
            executable = Path(root) / "Codex.exe"
            executable.touch()
            with patch.dict(os.environ, {"CODEX_TASKBOARD_CODEX_APP": str(executable)}):
                self.assertEqual(windows.discover_app(), executable.resolve())
            with patch.dict(os.environ, {"CODEX_TASKBOARD_CODEX_APP": root}):
                with self.assertRaisesRegex(RuntimeError, "desktop .exe"):
                    windows.discover_app()

    def test_folder_picker_passes_text_as_data_and_preserves_unicode(self):
        prompt = '目录 "quoted"; $env:PATH'
        result = subprocess.CompletedProcess([], 0, "C:\\项目 空格\\", "")
        with patch.object(windows, "hidden_process_options", return_value={}), patch.object(
            windows.subprocess, "run", return_value=result
        ) as run:
            self.assertIs(windows.pick_directory(prompt), result)
        args, kwargs = run.call_args
        script = base64.b64decode(args[0][-1]).decode("utf-16-le")
        self.assertNotIn(prompt, script)
        self.assertEqual(kwargs["env"]["TASKBOARD_FOLDER_PROMPT"], prompt)
        self.assertEqual(kwargs["encoding"], "utf-8")
        self.assertNotIn("shell", kwargs)

    def test_npm_shim_uses_node_without_shell(self):
        with TemporaryDirectory(prefix="taskboard & 中文 ") as root:
            folder = Path(root)
            npm = folder / "npm.cmd"
            script = folder / "node_modules/npm/bin/npm-cli.js"
            script.parent.mkdir(parents=True)
            script.touch()
            (folder / "node.exe").touch()
            with patch.object(sys, "platform", "win32"), patch.object(platforms.shutil, "which", return_value=str(npm)):
                self.assertEqual(platforms.executable_command(["npm", "run", "build"]),
                                 [str(folder / "node.exe"), str(script), "run", "build"])

    def test_relative_scopes_reject_windows_drive_escape_and_git_alias(self):
        for scope in (r"C:\outside", "C:relative", r"\\server\share", ".GiT/config", "file.txt:stream"):
            with self.subTest(scope=scope), self.assertRaises(ValidationError):
                normalize_scopes([scope])
        self.assertEqual(normalize_scopes([r"src\中文 文件.py"]), ["src/中文 文件.py"])

    def test_windows_attachment_materialization_preserves_extension(self):
        with patch.object(sys, "platform", "win32"):
            self.assertEqual(platforms.attachment_filename("CON.txt"), "_CON.txt")
            self.assertEqual(platforms.attachment_filename("报告:草稿?.pdf"), "报告_草稿_.pdf")
        with patch.object(sys, "platform", "darwin"):
            self.assertEqual(platforms.attachment_filename("报告:草稿?.pdf"), "报告:草稿?.pdf")

    def test_build_rejects_mislabeled_cross_platform_sidecar(self):
        with patch.object(sys, "platform", "win32"), patch("scripts.build_sidecar.platform.machine", return_value="AMD64"):
            self.assertEqual(native_target(), "x86_64-pc-windows-msvc")
            with self.assertRaisesRegex(RuntimeError, "native build"):
                validate_target("aarch64-apple-darwin")

    def make_injector(self, root: str):
        return CdpInjector(app_path=str(Path(root) / "Codex.exe"), profile_path=Path(root) / "profile",
                           source_profile_path=Path(root) / "source")

    def test_declined_restart_does_not_quit_or_launch(self):
        with TemporaryDirectory() as root:
            injector = self.make_injector(root)
            native = Mock()
            native.confirm_restart.return_value = False
            with patch("injector.cdp_injector.desktop", return_value=native), patch.object(injector, "_targets", return_value=[]), patch.object(
                injector, "_ordinary_codex_processes", return_value=[(321, "Codex.exe")]
            ), patch.object(injector, "_emit"):
                self.assertEqual(injector.run(), 0)
                native.request_quit.assert_not_called()
                native.launch.assert_not_called()

    def test_launch_keeps_cdp_loopback_profile_and_environment_contract(self):
        with TemporaryDirectory(prefix="taskboard 中文 ") as root:
            injector = self.make_injector(root)
            injector.app_path.touch()
            native = Mock()
            native.executable_path.side_effect = lambda path: path
            with patch("injector.cdp_injector.desktop", return_value=native), patch.object(
                injector, "_find_managed_pid", return_value=123
            ), patch.object(injector, "_emit"):
                injector._launch_codex()
                app, args, env = native.launch.call_args.args
                self.assertEqual(app, injector.app_path)
                self.assertIn("--remote-debugging-address=127.0.0.1", args)
                self.assertIn(f"--user-data-dir={injector.profile_path}", args)
                self.assertFalse(any(name.startswith("CODEX_TASKBOARD_") for name in env))
                injector.close()
                native.request_quit.assert_not_called()
            self.assertEqual(without_taskboard_launcher_environment({"CODEX_HOME": "keep", "CODEX_TASKBOARD_DEV": "1"}), {"CODEX_HOME": "keep"})

    @unittest.skipUnless(sys.platform == "win32", "requires Windows process groups")
    def test_windows_owned_process_tree_exits(self):
        import psutil
        program = "import subprocess,sys,time; child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); print(child.pid,flush=True); time.sleep(60)"
        parent = subprocess.Popen([sys.executable, "-c", program], stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, **platforms.process_group_options())
        try:
            child_pid = int(parent.stdout.readline())
            platforms.terminate_process_tree(parent, timeout=3)
            self.assertIsNotNone(parent.poll())
            self.assertFalse(psutil.pid_exists(child_pid))
        finally:
            if parent.poll() is None:
                platforms.terminate_process_tree(parent, timeout=1)
            parent.stdout.close()

    @unittest.skipUnless(sys.platform == "win32", "requires native Windows command-line API")
    def test_native_windows_command_parser_roundtrip(self):
        args = [r"C:\Program Files\Codex\codex.exe", "app-server", "--stdio", "C:\\中文 目录\\", 'quoted"value']
        self.assertEqual(platforms.split_command(subprocess.list2cmdline(args)), args)


if __name__ == "__main__":
    unittest.main()
