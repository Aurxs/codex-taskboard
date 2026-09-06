#!/usr/bin/env python3
"""Build the Windows x64 sidecar and per-user NSIS installer on Windows."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from codex_taskboard.platforms import executable_command  # noqa: E402
from build_sidecar import build, native_target  # noqa: E402
from check_sidecar_smoke import smoke  # noqa: E402


def main() -> int:
    if sys.platform != "win32":
        raise RuntimeError("Windows packaging must run on Windows")
    target = native_target()
    for executable in ("node", "npm", "cargo"):
        if not shutil.which(executable):
            raise RuntimeError(f"{executable} is required")
    os.environ.setdefault("PYINSTALLER_CONFIG_DIR", str(ROOT / ".build/pyinstaller-config"))
    subprocess.run(executable_command(["npm", "run", "build"]), cwd=ROOT, check=True)
    binary = build(target=target)
    smoke(binary)
    subprocess.run(executable_command(["npm", "exec", "--", "tauri", "build", "--target", target]), cwd=ROOT, check=True)
    release = ROOT / "src-tauri/target" / target / "release"
    # Tauri's final sidecar name differs from the target-suffixed build input.
    smoke(release / "codex-taskboard-sidecar.exe")
    installers = list((release / "bundle/nsis").glob("*-setup.exe"))
    if not installers:
        raise RuntimeError("Tauri did not produce an NSIS installer")
    output = ROOT / "output"
    output.mkdir(exist_ok=True)
    for installer in installers:
        destination = output / installer.name
        shutil.copy2(installer, destination)
        print(f"Installer output: {destination}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        print(f"Windows build failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
