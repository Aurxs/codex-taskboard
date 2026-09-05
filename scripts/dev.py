#!/usr/bin/env python3
"""Run the local Taskboard stack with one Ctrl-C lifecycle.

The regular development stack uses the same desktop transport as the packaged
sidecar. Backend-only diagnostics can still run an isolated stdio App Server.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]


def _port(value: str, name: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{name} must be an integer") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError(f"{name} must be between 1 and 65535")
    return port


def wait_for_backend(url: str, timeout: float = 45.0) -> bool:
    deadline = time.monotonic() + timeout
    endpoint = f"{url.rstrip('/')}/health"
    while time.monotonic() < deadline:
        try:
            with urlopen(endpoint, timeout=1.0) as response:  # nosec B310 - local URL assembled below
                if 200 <= response.status < 500:
                    return True
        except (OSError, URLError):
            pass
        time.sleep(0.25)
    return False


def wait_for_url(url: str, timeout: float = 45.0) -> bool:
    deadline = time.monotonic() + timeout
    endpoint = url.rstrip("/")
    while time.monotonic() < deadline:
        try:
            with urlopen(endpoint, timeout=1.0) as response:  # nosec B310 - caller supplies loopback URL
                if 200 <= response.status < 500:
                    return True
        except (OSError, URLError):
            pass
        time.sleep(0.25)
    return False


def _process_alive(process: subprocess.Popen[bytes]) -> bool:
    return process.poll() is None


def _spawn(command: list[str], env: dict[str, str], *, label: str) -> subprocess.Popen[bytes]:
    print(f"Starting {label}: {' '.join(command)}", flush=True)
    return subprocess.Popen(
        command,
        cwd=ROOT,
        env=env,
        start_new_session=True,
    )


def _terminate(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, OSError):
        return
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and _process_alive(process):
        time.sleep(0.1)
    if _process_alive(process):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("CODEX_TASKBOARD_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=lambda value: _port(value, "--port"), default=int(os.environ.get("CODEX_TASKBOARD_PORT", "47823")))
    parser.add_argument("--web-port", type=lambda value: _port(value, "--web-port"), default=int(os.environ.get("CODEX_TASKBOARD_WEB_PORT", "5173")))
    parser.add_argument("--cdp-port", type=lambda value: _port(value, "--cdp-port"), default=int(os.environ.get("CODEX_TASKBOARD_CDP_PORT", "9229")))
    parser.add_argument("--no-web", action="store_true", help="Do not start Vite")
    parser.add_argument("--no-injector", action="store_true", help="Do not start the CDP injector")
    parser.add_argument("--backend-only", action="store_true")
    parser.add_argument("--web-only", action="store_true")
    parser.add_argument("--injector-only", action="store_true")
    parser.add_argument("--no-csp-bypass", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    modes = [args.backend_only, args.web_only, args.injector_only]
    if sum(modes) > 1:
        print("Choose at most one of --backend-only, --web-only, --injector-only", file=sys.stderr)
        return 2
    if args.host not in {"127.0.0.1", "localhost"}:
        print("Development mode is loopback-only; choose --host 127.0.0.1", file=sys.stderr)
        return 2

    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        item for item in [str(ROOT / "src"), env.get("PYTHONPATH", "")] if item
    )
    env["CODEX_TASKBOARD_HOST"] = args.host
    env["CODEX_TASKBOARD_PORT"] = str(args.port)
    env["CODEX_TASKBOARD_URL"] = f"http://127.0.0.1:{args.port}"
    env["CODEX_TASKBOARD_CDP_PORT"] = str(args.cdp_port)
    env["CODEX_TASKBOARD_DEV"] = "1"
    env["CODEX_TASKBOARD_APP_SERVER_OWNER"] = "backend"
    env["CODEX_TASKBOARD_MANAGE_APP_SERVER"] = "1"

    start_backend = not args.web_only and not args.injector_only
    start_web = not args.backend_only and not args.injector_only and not args.no_web
    start_injector = not args.backend_only and not args.web_only and not args.no_injector
    shared_backend = start_backend and start_injector
    children: list[subprocess.Popen[bytes]] = []
    stopping = False
    web_started = False

    def stop(*_args: object) -> None:
        nonlocal stopping
        if stopping:
            return
        stopping = True
        for child in reversed(children):
            _terminate(child)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        if start_backend and not shared_backend:
            children.append(
                _spawn(
                    [sys.executable, "-m", "uvicorn", "codex_taskboard.app:app", "--host", args.host, "--port", str(args.port)],
                    env,
                    label="FastAPI backend (Codex App Server is owned here)",
                )
            )
            if not wait_for_backend(env["CODEX_TASKBOARD_URL"]):
                print("Taskboard backend did not become ready", file=sys.stderr)
                stop()
                return 1
        web_url = f"http://127.0.0.1:{args.web_port}"
        if start_web:
            package_json = ROOT / "package.json"
            if not package_json.exists():
                print("Vite skipped: root package.json does not exist yet", file=sys.stderr)
            elif not shutil_which("npm"):
                print("Vite skipped: npm is not installed", file=sys.stderr)
            else:
                children.append(
                    _spawn(
                        ["npm", "run", "dev:web", "--", "--host", "127.0.0.1", "--port", str(args.web_port)],
                        env,
                        label="Vite frontend",
                    )
                )
                if not wait_for_url(web_url):
                    print("Vite frontend did not become ready", file=sys.stderr)
                    stop()
                    return 1
                web_started = True
        if start_injector:
            injector_url = web_url if web_started else env["CODEX_TASKBOARD_URL"]
            command = [sys.executable, "-m", "injector.sidecar", "--host", args.host,
                       "--port", str(args.port), "--cdp-port", str(args.cdp_port)] if shared_backend else [
                           sys.executable, "-m", "injector.cdp_injector", "--port", str(args.cdp_port)]
            children.append(
                _spawn(
                    [
                        *command,
                        "--taskboard-url",
                        injector_url,
                        *(["--no-csp-bypass"] if args.no_csp_bypass else []),
                    ],
                    env,
                    label="loopback Codex CDP injector",
                )
            )
            if shared_backend and not wait_for_backend(env["CODEX_TASKBOARD_URL"]):
                print("Taskboard backend did not become ready", file=sys.stderr)
                stop()
                return 1
        if not children:
            print("Nothing to run", file=sys.stderr)
            return 2
        print("Codex Taskboard development stack is running. Press Ctrl-C to stop.", flush=True)
        while not stopping:
            for child in children:
                code = child.poll()
                if code is not None and code != 0:
                    print(f"A child process exited with status {code}", file=sys.stderr)
                    stop()
                    return code or 1
            time.sleep(0.5)
        return 0
    finally:
        stop()


def shutil_which(command: str) -> str | None:
    """Small local wrapper to keep subprocess lookup easy to test."""

    import shutil

    return shutil.which(command)


if __name__ == "__main__":
    raise SystemExit(main())
