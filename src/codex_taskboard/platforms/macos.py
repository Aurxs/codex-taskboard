"""macOS-only desktop APIs. No scheduler or CDP logic belongs here."""
from __future__ import annotations

import os
from pathlib import Path
import plistlib
import subprocess
import sys

CODEX_APP_CANDIDATES = (
    "/Applications/ChatGPT.app", "~/Applications/ChatGPT.app",
    "/Applications/Codex.app", "~/Applications/Codex.app",
)

def discover_app() -> Path | None:
    """Return the first supported installed Codex/ChatGPT app bundle.

    LaunchServices can find an app which is not in ``/Applications`` (for
    example an app kept in ``~/Applications`` or a developer copy).  The
    fixed paths remain the fast path, while the small ``mdfind`` fallback
    keeps the packaged launcher from requiring an app path setting.
    """

    if sys.platform != "darwin":
        return None
    configured = os.environ.get("CODEX_TASKBOARD_CODEX_APP", "").strip()
    candidates = ([configured] if configured else []) + list(CODEX_APP_CANDIDATES)
    for candidate in candidates:
        path = Path(candidate).expanduser()
        if path.is_dir() and path.suffix.lower() == ".app":
            return path.resolve()
    try:
        result = subprocess.run(
            [
                "/usr/bin/mdfind",
                "-0",
                "kMDItemContentType == 'com.apple.application-bundle' && "
                "(kMDItemFSName == 'ChatGPT.app' || kMDItemFSName == 'Codex.app')",
            ],
            check=False,
            capture_output=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for raw_path in result.stdout.split(b"\0"):
        if not raw_path:
            continue
        path = Path(raw_path.decode("utf-8", errors="ignore"))
        if path.is_dir() and path.suffix.lower() == ".app":
            return path.resolve()
    return None


def executable_path(app_path: Path) -> Path:
    """Resolve the actual Electron executable inside an app bundle."""

    info_path = app_path / "Contents" / "Info.plist"
    try:
        with info_path.open("rb") as stream:
            executable_name = plistlib.load(stream).get("CFBundleExecutable")
    except (OSError, ValueError, TypeError, plistlib.InvalidFileException):
        executable_name = None
    if not isinstance(executable_name, str) or not executable_name.strip():
        executable_name = app_path.stem
    return app_path / "Contents" / "MacOS" / executable_name.strip()



def source_profile() -> Path:
    return Path.home() / "Library" / "Application Support" / "Codex"


def launch(app: Path, arguments: list[str], environment: dict[str, str]) -> None:
    subprocess.run(["/usr/bin/open", "-n", "-a", str(app), "--args", *arguments],
                   check=True, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def process_table() -> list[tuple[int, str]]:
    result = subprocess.run(["/bin/ps", "-ww", "-axo", "pid=,command="],
                            check=True, capture_output=True, text=True)
    rows = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and parts[0].isdigit():
            rows.append((int(parts[0]), parts[1]))
    return rows


def matches_process(command: str, executable: Path) -> bool:
    return command == str(executable) or command.startswith(f"{executable} ")


def activate(pid: int) -> None:
    script = ("ObjC.import('AppKit'); "
              f"const app = $.NSRunningApplication.runningApplicationWithProcessIdentifier({pid}); "
              "if (!app || !app.activateWithOptions(1)) throw new Error('Unable to activate Codex');")
    subprocess.run(["/usr/bin/osascript", "-l", "JavaScript", "-e", script],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def confirm_restart(app: Path, chinese: bool) -> bool:
    cancel, restart = ("取消", "重新启动 Codex") if chinese else ("Cancel", "Restart Codex")
    message = "需要重新启动 Codex 才能显示任务面板。" if chinese else "Restart Codex to show Taskboard."
    script = f'display dialog "{message}" buttons {{"{cancel}", "{restart}"}} default button "{restart}" with title "Codex Taskboard"'
    result = subprocess.run(["/usr/bin/osascript", "-e", script], capture_output=True, text=True)
    return result.returncode == 0 and restart in result.stdout


def request_quit(app: Path, pid: int) -> None:
    # Use the selected process, including apps outside /Applications.
    script = ("ObjC.import('AppKit'); "
              f"const app = $.NSRunningApplication.runningApplicationWithProcessIdentifier({pid}); "
              "if (app && !app.terminate) throw new Error('Codex did not accept the quit request');")
    subprocess.run(["/usr/bin/osascript", "-l", "JavaScript", "-e", script], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def process_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def pick_directory(prompt: str) -> subprocess.CompletedProcess:
    return subprocess.run(["/usr/bin/osascript", "-e", f'POSIX path of (choose folder with prompt "{prompt}")'],
                          check=False, capture_output=True, text=True, timeout=120)


def bundled_agent() -> str | None:
    app = discover_app()
    if app:
        candidate = app / "Contents/Resources/codex"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None
