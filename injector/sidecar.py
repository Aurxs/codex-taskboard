#!/usr/bin/env python3
"""Packaged Python sidecar for the Tauri shell.

The backend and injector share an in-process transport to the desktop's
existing App Server. Backend-only diagnostics can still use a stdio server.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import signal
import sys
import threading
import time
from typing import Any
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

from codex_taskboard.platforms import configure_standard_streams, user_data_directory

from injector.cdp_injector import (
    CdpInjector,
    DEFAULT_CDP_PORT,
    DEFAULT_TASKBOARD_PORT,
    InjectorError,
)


def _emit(event: str, **payload: Any) -> None:
    """Write one stable lifecycle event for the native menu-bar shell."""

    try:
        print(json.dumps({"event": event, **payload}, ensure_ascii=False, separators=(",", ":")), flush=True)
    except BrokenPipeError:
        pass


def default_data_directory() -> Path:
    configured = os.environ.get("CODEX_TASKBOARD_DATA_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    if os.environ.get("CODEX_TASKBOARD_DEV") == "1":
        return Path.cwd() / ".data"
    return user_data_directory()


def static_directory() -> Path | None:
    """Find the bundled or development frontend before importing FastAPI."""

    configured = os.environ.get("CODEX_TASKBOARD_STATIC_DIR", "").strip()
    if configured:
        candidate = Path(configured).expanduser().resolve()
        return candidate if (candidate / "index.html").is_file() else None

    # PyInstaller extracts --add-data destinations below _MEIPASS.  Keep this
    # lookup independent from the backend import so its module-level app sees
    # the packaged static root during construction.
    meipass = getattr(sys, "_MEIPASS", None)
    candidates: list[Path] = []
    if meipass:
        candidates.append(Path(str(meipass)) / "dist" / "web")

    # In development, the Vite build lives at the repository root.  Resolve
    # from this file rather than cwd so the sidecar works when launched by
    # Tauri or another process with a different working directory.
    candidates.append(Path(__file__).resolve().parents[1] / "dist" / "web")
    for candidate in candidates:
        candidate = candidate.resolve()
        if (candidate / "index.html").is_file():
            return candidate
    return None


def wait_for_backend(url: str, timeout: float = 45.0) -> None:
    deadline = time.monotonic() + timeout
    health_url = f"{url.rstrip('/')}/health"
    # This is an internal loopback probe.  System HTTP proxy settings must not
    # reroute it, otherwise a healthy packaged backend can look unavailable.
    opener = build_opener(ProxyHandler({}))
    while time.monotonic() < deadline:
        try:
            with opener.open(health_url, timeout=1.0) as response:  # nosec B310 - URL is loopback-validated by CLI
                if 200 <= response.status < 500:
                    return
        except (OSError, URLError):
            pass
        time.sleep(0.25)
    raise RuntimeError(f"Timed out waiting for Taskboard backend at {health_url}")


@dataclass(slots=True)
class BackendRuntime:
    """Own the in-process Uvicorn server and its thread lifecycle."""

    server: Any
    thread: threading.Thread

    def request_stop(self) -> None:
        """Ask Uvicorn to leave its event loop at the next safe point."""

        self.server.should_exit = True

    def stop(self, timeout: float = 10.0) -> None:
        """Stop the server and wait for FastAPI lifespan shutdown to finish."""

        self.request_stop()
        if self.thread is threading.current_thread():
            return
        self.thread.join(timeout=timeout)
        if self.thread.is_alive():
            # ``force_exit`` lets Uvicorn finish its shutdown path even when a
            # long-lived request is preventing the normal graceful exit.
            self.server.force_exit = True
            self.thread.join(timeout=min(2.0, timeout))


def start_backend(host: str, port: int, data_dir: Path, injector=None) -> BackendRuntime:
    # Set the environment before importing the application.  The module-level
    # FastAPI app constructs its database and scheduler during import.
    os.environ["CODEX_TASKBOARD_HOST"] = host
    os.environ["CODEX_TASKBOARD_PORT"] = str(port)
    os.environ["CODEX_TASKBOARD_DATA_DIR"] = str(data_dir)
    # Normal launches share the desktop service; backend-only diagnostics
    # retain the explicit stdio transport.
    os.environ["CODEX_TASKBOARD_APP_SERVER_OWNER"] = "backend"
    os.environ["CODEX_TASKBOARD_MANAGE_APP_SERVER"] = "1"
    if injector is not None:
        os.environ["CODEX_TASKBOARD_DESKTOP_TRANSPORT"] = "1"
    static_root = static_directory()
    if static_root is not None:
        os.environ["CODEX_TASKBOARD_STATIC_DIR"] = str(static_root)

    # Keep this import inside the function so `python -m injector.cdp_injector`
    # remains useful on machines that only want to attach to an existing UI.
    try:
        import uvicorn
        from codex_taskboard.app import app
    except ImportError as exc:
        raise RuntimeError(
            "The packaged sidecar requires the backend package and uvicorn; "
            "run `python -m pip install -e .` during development. "
            f"Import failed with {exc.__class__.__name__}: {exc}"
        ) from exc

    if injector is not None:
        app.state.scheduler.server.transport = injector.native_request
        app.state.scheduler.server.available = injector.native_connected
        injector.native_event_handler = app.state.scheduler.server.receive

    config = uvicorn.Config(app, host=host, port=port, log_level="info", lifespan="on")
    server = uvicorn.Server(config)

    def serve() -> None:
        server.run()

    thread = threading.Thread(target=serve, name="codex-taskboard-backend", daemon=True)
    thread.start()
    return BackendRuntime(server=server, thread=thread)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("CODEX_TASKBOARD_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("CODEX_TASKBOARD_PORT", DEFAULT_TASKBOARD_PORT)))
    parser.add_argument("--cdp-port", type=int, default=int(os.environ.get("CODEX_TASKBOARD_CDP_PORT", DEFAULT_CDP_PORT)))
    parser.add_argument(
        "--taskboard-url",
        default=os.environ.get("CODEX_TASKBOARD_URL") or None,
        help="Optional iframe origin; by default it follows --port",
    )
    parser.add_argument(
        "--codex-app",
        default=os.environ.get("CODEX_TASKBOARD_CODEX_APP") or None,
        help="Optional developer override; normally the installed Codex app is discovered automatically",
    )
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--profile", type=Path, default=None)
    parser.add_argument(
        "--control-file",
        type=Path,
        default=None,
        help="Optional local command mailbox used by the frozen menu-bar launcher",
    )
    parser.add_argument("--no-server", action="store_true", help="Only run the injector")
    parser.add_argument("--no-injector", action="store_true", help="Only run the backend")
    parser.add_argument("--no-csp-bypass", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_standard_streams()
    args = build_parser().parse_args(argv)
    if args.host not in {"127.0.0.1", "localhost"}:
        print("The packaged sidecar only binds the Taskboard service to loopback", file=sys.stderr)
        return 2
    data_dir = (args.data_dir or default_data_directory()).expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    # Keep the backend probe and iframe origin on the same port.  This matters
    # for the packaged health smoke (and for a second local instance) because
    # ``--port`` is intentionally allowed to avoid a busy 47823.
    taskboard_url = (args.taskboard_url or f"http://127.0.0.1:{args.port}").rstrip("/")
    backend: BackendRuntime | None = None
    injector: CdpInjector | None = None
    shutdown_requested = threading.Event()
    pending_open = threading.Event()
    control_watcher: threading.Thread | None = None

    def request_stop(*_args: object) -> None:
        shutdown_requested.set()
        if injector is not None:
            injector.stop()
        if backend is not None:
            backend.request_stop()

    def request_open(*_args: object) -> None:
        if injector is not None:
            injector.request_open()
        else:
            pending_open.set()

    def watch_control_file(path: Path) -> None:
        last_value = ""
        while not shutdown_requested.wait(0.25):
            try:
                value = path.read_text(encoding="utf-8").strip()
            except (OSError, UnicodeError):
                continue
            if not value or value == last_value:
                continue
            last_value = value
            command = value.splitlines()[-1].strip().lower()
            if command == "open":
                request_open()
            elif command == "stop":
                request_stop()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, request_stop)
    if hasattr(signal, "SIGUSR1"):
        signal.signal(signal.SIGUSR1, request_open)
    try:
        if args.control_file is not None:
            control_path = args.control_file.expanduser().resolve()
            control_path.parent.mkdir(parents=True, exist_ok=True)
            control_watcher = threading.Thread(
                target=watch_control_file,
                args=(control_path,),
                name="codex-taskboard-control",
                daemon=True,
            )
            control_watcher.start()
        if not args.no_injector:
            injector = CdpInjector(
                port=args.cdp_port, taskboard_url=taskboard_url,
                app_path=args.codex_app,
                profile_path=args.profile or data_dir / "codex-profile",
                launch=True, bypass_csp=not args.no_csp_bypass,
            )
        if not args.no_server:
            backend = start_backend(args.host, args.port, data_dir, injector)
            wait_for_backend(f"http://127.0.0.1:{args.port}")
            _emit("backend_ready", host=args.host, port=args.port, url=taskboard_url)
        if args.no_injector:
            while backend and backend.thread.is_alive() and not shutdown_requested.wait(1):
                pass
            return 0
        if pending_open.is_set():
            injector.request_open()
        return injector.run()
    except (InjectorError, RuntimeError) as exc:
        _emit("error", phase="sidecar", message=str(exc))
        print(f"Codex Taskboard sidecar: {exc}", file=sys.stderr)
        return 1
    finally:
        shutdown_requested.set()
        if injector is not None:
            injector.stop()
            injector.close()
        if backend is not None:
            backend.stop()
        if control_watcher is not None and control_watcher is not threading.current_thread():
            control_watcher.join(timeout=1.0)


if __name__ == "__main__":
    raise SystemExit(main())
