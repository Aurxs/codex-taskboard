"""OS boundary for desktop integration; scheduling, CDP and UI stay shared."""
from __future__ import annotations

import os
import re
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys


def desktop():
    if sys.platform == "darwin":
        from . import macos
        return macos
    if sys.platform == "win32":
        from . import windows
        return windows
    raise RuntimeError("Desktop integration requires macOS or Windows")


def user_data_directory() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Codex Taskboard"
    if sys.platform == "win32":
        # Matches Tauri's app_data_dir() (Roaming AppData + bundle identifier).
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "com.codex.taskboard"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "Codex Taskboard"


def hidden_process_options() -> dict:
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}


def split_command(command: str) -> list[str]:
    if sys.platform == "win32":
        from .windows import split_command as windows_split
        return windows_split(command)
    return shlex.split(command)


def executable_command(command: list[str]) -> list[str]:
    """Resolve npm's Windows shims to Node, without cmd.exe/shell interpolation."""
    if sys.platform != "win32":
        return command
    resolved = command[0] if Path(command[0]).parent != Path(".") else (shutil.which(command[0]) or command[0])
    executable = Path(resolved)
    if executable.suffix.lower() not in {".cmd", ".bat"}:
        return [resolved, *command[1:]]
    scripts = {
        "npm": "npm/bin/npm-cli.js", "npx": "npm/bin/npx-cli.js",
        "codex": "@openai/codex/bin/codex.js",
    }
    script = scripts.get(executable.stem.lower())
    entry = executable.parent / "node_modules" / script if script else None
    if entry is None or not entry.is_file():
        raise RuntimeError(f"Cannot safely launch {executable}; configure an .exe or a Node script command")
    node = executable.parent / "node.exe"
    return [str(node) if node.is_file() else (shutil.which("node") or "node"), str(entry), *command[1:]]


def process_group_options() -> dict:
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def terminate_process_tree(process: subprocess.Popen, timeout: float = 5) -> None:
    """Only for owned development processes, never the user's Codex app."""
    if process.poll() is not None:
        return
    if sys.platform == "win32":
        import psutil
        try:
            parent = psutil.Process(process.pid)
            children = parent.children(recursive=True)
            # Codex outlives Taskboard even when launched by the dev sidecar.
            external = set()
            for item in children:
                try:
                    path = Path(item.exe())
                    if path.name.lower() in {"codex.exe", "chatgpt.exe"} and (path.parent / "resources/app.asar").is_file():
                        external.update([item.pid, *(child.pid for child in item.children(recursive=True))])
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            children = [item for item in children if item.pid not in external]
            try:
                process.send_signal(signal.CTRL_BREAK_EVENT)
            except OSError:
                pass
            _, alive = psutil.wait_procs([*children, parent], timeout=timeout)
            for item in alive:
                try:
                    item.kill()
                except psutil.NoSuchProcess:
                    pass
            psutil.wait_procs(alive, timeout=timeout)
        except psutil.NoSuchProcess:
            pass
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=timeout)


def attachment_filename(name: str) -> str:
    """Keep the displayed name; only adapt the on-disk name on Windows."""
    if sys.platform != "win32":
        return name
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).rstrip(" .")
    if re.match(r"^(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)", safe, re.IGNORECASE):
        safe = "_" + safe
    return safe or "attachment"


def configure_standard_streams() -> None:
    """The launcher's JSON event pipes always use UTF-8, including frozen Python."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
