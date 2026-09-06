"""Native Windows desktop operations, isolated from Taskboard business logic."""
from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import json
import ntpath
import os
from pathlib import Path
import re
import shutil
import subprocess

from . import hidden_process_options


def powershell(script: str, *, environment: dict | None = None, timeout: float = 15) -> subprocess.CompletedProcess:
    # Fixed scripts, UTF-16 encoding and environment parameters preserve Unicode
    # and quotes without interpolating file paths into shell code.
    encoded = base64.b64encode((
        "$ErrorActionPreference = 'Stop'; "
        "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); " + script
    ).encode("utf-16-le")).decode("ascii")
    executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    return subprocess.run([str(executable), "-NoProfile", "-NonInteractive", "-STA", "-EncodedCommand", encoded],
                          env={**os.environ, **(environment or {})}, capture_output=True,
                          text=True, encoding="utf-8", timeout=timeout, **hidden_process_options())


def _store_apps() -> list[dict]:
    result = powershell(r"""
    $apps = @(Get-AppxPackage | Where-Object { $_.Name -match '(?i)(OpenAI|Codex|ChatGPT)' } | ForEach-Object {
        $pkg = $_
        $manifest = Get-AppxPackageManifest -Package $pkg.PackageFullName
        foreach ($app in $manifest.Package.Applications.Application) {
            if ($app.Executable -and ([IO.Path]::GetFileName($app.Executable) -match '(?i)^(Codex|ChatGPT)\.exe$')) {
                [PSCustomObject]@{ executable = (Join-Path $pkg.InstallLocation $app.Executable); family = $pkg.PackageFamilyName }
            }
        }
    })
    ConvertTo-Json -InputObject $apps -Compress
    """)
    if result.returncode:
        raise RuntimeError(f"Could not discover the installed Codex package: {result.stderr.strip()}")
    value = json.loads(result.stdout or "[]")
    return value if isinstance(value, list) else [value]


def discover_app() -> Path | None:
    configured = os.environ.get("CODEX_TASKBOARD_CODEX_APP", "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_file() or path.suffix.lower() != ".exe":
            raise RuntimeError("CODEX_TASKBOARD_CODEX_APP must point to the Codex desktop .exe")
        return path.resolve()
    # Prefer the executable of an already-running desktop, including Store installs.
    import psutil
    for proc in psutil.process_iter(["name", "exe", "cmdline"]):
        info = proc.info
        if str(info["name"]).lower() not in {"codex.exe", "chatgpt.exe"}:
            continue
        path = Path(info["exe"]) if info["exe"] else None
        if path and (path.parent / "resources" / "app.asar").is_file() and not any(
            arg.startswith("--type=") for arg in (info["cmdline"] or [])
        ):
            return path.resolve()
    # Registry App Paths supports machine-wide and per-user unpackaged installs.
    import winreg
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for name in ("Codex.exe", "ChatGPT.exe"):
            try:
                with winreg.OpenKey(hive, rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{name}") as key:
                    path = Path(winreg.QueryValue(key, None).strip('"'))
                    if path.is_file():
                        return path.resolve()
            except OSError:
                pass
    roots = [Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local") / "Programs"]
    roots += [Path(os.environ[key]) for key in ("ProgramFiles", "ProgramFiles(x86)") if os.environ.get(key)]
    for root in roots:
        for name in ("Codex", "ChatGPT"):
            for folder in (root / name, root / "OpenAI" / name):
                path = folder / f"{name}.exe"
                if path.is_file():
                    return path.resolve()
    for app in _store_apps():
        path = Path(app["executable"])
        if path.is_file():
            return path.resolve()
    return None


def executable_path(app: Path) -> Path:
    return app


def source_profile() -> Path | None:
    roaming = Path(os.environ.get("APPDATA") or Path.home() / "AppData/Roaming")
    for name in ("Codex", "ChatGPT"):
        candidate = roaming / name
        if candidate.is_dir():
            return candidate
    # Packaged apps may redirect Roaming AppData to their package LocalCache.
    local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local")
    try:
        for app in _store_apps():
            for name in ("Codex", "ChatGPT"):
                candidate = local / "Packages" / app["family"] / "LocalCache/Roaming" / name
                if candidate.is_dir():
                    return candidate
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError):
        pass
    return None


def process_table() -> list[tuple[int, str]]:
    import psutil
    return [(p.pid, subprocess.list2cmdline(p.info["cmdline"]))
            for p in psutil.process_iter(["name", "cmdline"])
            if str(p.info["name"]).lower() in {"codex.exe", "chatgpt.exe"}
            and p.info["cmdline"]]


def matches_process(command: str, executable: Path) -> bool:
    match = re.match(r'^\s*(?:"([^"]+)"|(\S+))', command)
    if not match or re.search(r'(?:^|\s)"?--type(?:=|\s)', command):
        return False
    return ntpath.normcase(match[1] or match[2]) == ntpath.normcase(str(executable))


def split_command(command: str) -> list[str]:
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    shell.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    shell.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel.LocalFree.restype = wintypes.HLOCAL
    count = ctypes.c_int()
    args = shell.CommandLineToArgvW(command, ctypes.byref(count))
    if not args:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return [args[index] for index in range(count.value)]
    finally:
        kernel.LocalFree(ctypes.cast(args, wintypes.HLOCAL))


def launch(app: Path, arguments: list[str], environment: dict[str, str]) -> None:
    # PyInstaller's DLL search path must not leak into the external Electron app.
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetDllDirectoryW.argtypes = [wintypes.DWORD, wintypes.LPWSTR]
    kernel.SetDllDirectoryW.argtypes = [wintypes.LPCWSTR]
    buffer = ctypes.create_unicode_buffer(32768)
    kernel.GetDllDirectoryW(len(buffer), buffer)
    kernel.SetDllDirectoryW(None)
    try:
        subprocess.Popen([str(app), *arguments], env=environment, cwd=app.parent,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         close_fds=True, creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
    finally:
        kernel.SetDllDirectoryW(buffer.value or None)


def _windows(pid: int):
    user = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    handles = []
    @callback_type
    def visit(hwnd, _):
        owner = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user.IsWindowVisible(hwnd):
            handles.append(hwnd)
        return True
    user.EnumWindows(visit, 0)
    return user, handles


def activate(pid: int) -> None:
    user, handles = _windows(pid)
    user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user.SetForegroundWindow.argtypes = [wintypes.HWND]
    if handles:
        user.ShowWindow(handles[0], 9)  # SW_RESTORE, including minimized windows.
        user.SetForegroundWindow(handles[0])


def confirm_restart(app: Path, chinese: bool) -> bool:
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
    message = "需要重新启动 Codex 才能显示任务面板。现在重新启动？" if chinese else "Restart Codex to show Taskboard?"
    return user.MessageBoxW(None, message, "Codex Taskboard", 0x00000004 | 0x00000020 | 0x00010000) == 6


def request_quit(app: Path, pid: int) -> None:
    user, handles = _windows(pid)
    if not handles:
        raise RuntimeError("Codex has no visible window to close; quit Codex and restart Taskboard")
    user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    for handle in handles:
        if not user.PostMessageW(handle, 0x0010, 0, 0):  # WM_CLOSE: allow save/confirmation UI.
            raise ctypes.WinError(ctypes.get_last_error())


def process_running(pid: int) -> bool:
    import psutil
    return psutil.pid_exists(pid)


def pick_directory(prompt: str) -> subprocess.CompletedProcess:
    return powershell("""
    Add-Type -AssemblyName System.Windows.Forms
    $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
    $dialog.Description = $env:TASKBOARD_FOLDER_PROMPT
    $dialog.ShowNewFolderButton = $true
    try {
        if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
            [Console]::Write($dialog.SelectedPath)
        } else { exit 1 }
    } finally { $dialog.Dispose() }
    """, environment={"TASKBOARD_FOLDER_PROMPT": prompt}, timeout=120)


def bundled_agent() -> str | None:
    try:
        app = discover_app()
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError):
        app = None
    if app:
        for candidate in (app.parent / "resources/codex.exe", app.parent / "resources/bin/codex.exe"):
            if candidate.is_file():
                return str(candidate)
    return shutil.which("codex.exe")
