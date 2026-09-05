#!/usr/bin/env python3
"""Build the arm64 Python sidecar expected by the Tauri bundle."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGET = "aarch64-apple-darwin"


def build(*, target: str, skip_copy: bool = False) -> Path:
    if sys.version_info < (3, 13):
        raise RuntimeError("The sidecar requires Python 3.13 or newer")
    if shutil.which("rustc") and sys.platform != "darwin":
        raise RuntimeError("The macOS sidecar must be built on macOS")
    if not (ROOT / "injector" / "sidecar.py").exists():
        raise RuntimeError("injector/sidecar.py is missing")
    if not (ROOT / "src" / "codex_taskboard").exists():
        raise RuntimeError("The backend package is missing; build the backend first")
    static_root = ROOT / "dist" / "web"
    static_index = static_root / "index.html"
    if not static_index.is_file():
        raise RuntimeError(
            "The frontend has not been built: expected dist/web/index.html; "
            "run `npm run build:web` first."
        )
    try:
        import PyInstaller  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "PyInstaller is not installed. Install the packaging extras before building."
        ) from exc

    binary_dir = ROOT / "src-tauri" / "binaries"
    build_dir = ROOT / ".build" / "pyinstaller"
    dist_dir = ROOT / ".build" / "sidecar-dist"
    binary_dir.mkdir(parents=True, exist_ok=True)
    build_dir.mkdir(parents=True, exist_ok=True)
    dist_dir.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--name",
        "codex-taskboard-sidecar",
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(build_dir),
        "--specpath",
        str(build_dir),
        "--target-architecture",
        "arm64",
        # The backend uses a src/ layout.  PyInstaller does not infer that
        # import root from the sidecar entry point, so the previous build
        # silently omitted the entire codex_taskboard package.  Keep the
        # package collection explicit because the app is imported lazily by
        # ``start_backend`` in the frozen process.
        "--paths",
        str(ROOT / "src"),
        "--add-data",
        f"{ROOT / 'injector' / 'inject.js'}{os.pathsep}injector",
        "--add-data",
        f"{static_root}{os.pathsep}dist/web",
        "--hidden-import",
        "uvicorn",
        "--collect-submodules",
        "uvicorn",
        "--collect-submodules",
        "codex_taskboard",
        # Conda environments often contain several optional Qt stacks.  They
        # are unrelated to this headless FastAPI sidecar, and PyInstaller
        # aborts when its matplotlib hook sees more than one binding.
        "--exclude-module",
        "PyQt6",
        "--exclude-module",
        "PySide6",
        str(ROOT / "injector" / "sidecar.py"),
    ]
    subprocess.run(command, cwd=ROOT, check=True)
    output = dist_dir / "codex-taskboard-sidecar"
    if not output.exists():
        raise RuntimeError(f"PyInstaller did not produce {output}")
    if skip_copy:
        return output
    tauri_binary = binary_dir / f"codex-taskboard-sidecar-{target}"
    shutil.copy2(output, tauri_binary)
    tauri_binary.chmod(0o755)
    return tauri_binary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default=DEFAULT_TARGET)
    parser.add_argument("--skip-copy", action="store_true")
    args = parser.parse_args(argv)
    try:
        output = build(target=args.target, skip_copy=args.skip_copy)
    except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        print(f"Sidecar build skipped/failed: {exc}", file=sys.stderr)
        return 1
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
