#!/usr/bin/env python3
"""Smoke-test a frozen sidecar without starting Codex or an App Server."""

from __future__ import annotations

import argparse
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BINARIES = (
    ROOT / "src-tauri" / "binaries" / "codex-taskboard-sidecar-aarch64-apple-darwin",
    ROOT / ".build" / "sidecar-dist" / "codex-taskboard-sidecar",
)
DEFAULT_SIDECAR_NAME = "codex-taskboard-sidecar"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        return int(server.getsockname()[1])


def wait_for_health(port: int, process: subprocess.Popen[bytes], timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/health"
    opener = build_opener(ProxyHandler({}))
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = process.communicate(timeout=2)[0].decode("utf-8", errors="replace")
            raise RuntimeError(f"sidecar exited with {process.returncode}: {output[-2000:]}")
        try:
            with opener.open(url, timeout=1.0) as response:  # nosec B310 - loopback only
                if response.status == 200:
                    return
        except (OSError, URLError):
            time.sleep(0.2)
    raise RuntimeError(f"timed out waiting for {url}")


def smoke(binary: Path) -> None:
    port = free_port()
    with tempfile.TemporaryDirectory(prefix="codex-taskboard-smoke-") as data_dir:
        control_file = Path(data_dir) / "launcher-control"
        control_file.write_text("", encoding="utf-8")
        process = subprocess.Popen(
            [
                str(binary),
                "--no-injector",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--data-dir",
                data_dir,
                "--control-file",
                str(control_file),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        try:
            wait_for_health(port, process)
        finally:
            if process.poll() is None:
                control_file.write_text(f"{time.time_ns()}\nstop\n", encoding="utf-8")
            try:
                output = process.communicate(timeout=8)[0].decode("utf-8", errors="replace")
            except subprocess.TimeoutExpired:
                process.kill()
                output = process.communicate(timeout=2)[0].decode("utf-8", errors="replace")
            if process.returncode not in (0, -15, 143):
                raise RuntimeError(f"sidecar shutdown failed ({process.returncode}): {output[-2000:]}")


def sidecar_from_app(app: Path) -> Path:
    """Resolve the sidecar inside a bundled .app, rather than a build artifact."""

    app = app.expanduser().resolve()
    if app.suffix != ".app":
        raise RuntimeError(f"expected a .app bundle: {app}")
    return app / "Contents" / "MacOS" / DEFAULT_SIDECAR_NAME


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    binary_group = parser.add_mutually_exclusive_group()
    binary_group.add_argument(
        "--binary",
        type=Path,
        default=None,
        help="path to a sidecar executable (including one inside a .app)",
    )
    binary_group.add_argument(
        "--app",
        type=Path,
        default=None,
        help="path to a signed .app; smoke-tests its Contents/MacOS sidecar",
    )
    parser.add_argument("--required", action="store_true", help="fail when no frozen binary exists")
    args = parser.parse_args(argv)
    if args.app:
        binary = sidecar_from_app(args.app)
    elif args.binary:
        binary = args.binary.expanduser().resolve()
    else:
        binary = next((candidate for candidate in DEFAULT_BINARIES if candidate.is_file()), None)
    if binary is None:
        message = "SKIPPED: no frozen sidecar found; build it before running the runtime smoke check"
        print(message)
        return 1 if args.required else 0
    if not binary.is_file() or not binary.stat().st_mode & 0o111:
        print(f"FAILED: sidecar is not an executable file: {binary}", file=sys.stderr)
        return 1
    try:
        smoke(binary)
    except (OSError, RuntimeError) as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"PASSED: packaged sidecar health smoke ({binary})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
