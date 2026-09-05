#!/usr/bin/env python3
"""Run quick, dependency-free checks for launcher and Tauri configuration."""

from __future__ import annotations

import json
from pathlib import Path
import py_compile
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from injector.cdp_injector import decode_frame_bytes, encode_client_frame  # noqa: E402


def main() -> int:
    python_files = [
        ROOT / "injector" / "cdp_injector.py",
        ROOT / "injector" / "sidecar.py",
        ROOT / "scripts" / "dev.py",
        ROOT / "scripts" / "build_sidecar.py",
    ]
    for path in python_files:
        py_compile.compile(str(path), doraise=True)
    for path in [ROOT / "src-tauri" / "tauri.conf.json", ROOT / "src-tauri" / "capabilities" / "default.json"]:
        json.loads(path.read_text(encoding="utf-8"))
    subprocess.run(["bash", "-n", str(ROOT / "scripts" / "build_macos.sh")], check=True)
    build_script = (ROOT / "scripts" / "build_sidecar.py").read_text(encoding="utf-8")
    assert '"--paths"' in build_script and 'ROOT / "src"' in build_script
    assert build_script.count('"--collect-submodules"') >= 2
    mask = bytes.fromhex("01020304")
    for length in (0, 125, 126, 65_535, 65_536):
        payload = bytes((index % 251 for index in range(length)))
        frame = encode_client_frame(1, payload, mask=mask)
        _fin, opcode, decoded, consumed = decode_frame_bytes(frame)
        assert opcode == 1 and decoded == payload and consumed == len(frame)
    print(
        f"checked {len(python_files)} Python files, 2 JSON files, build scripts, "
        "and WebSocket frames (sidecar runtime smoke is a separate check)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
