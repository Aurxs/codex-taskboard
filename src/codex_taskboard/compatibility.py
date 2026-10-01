"""Read-only host diagnostics, separate from protocol and renderer adapters."""
from __future__ import annotations

import json
from pathlib import Path
import plistlib
import sys

from .platforms import desktop

TESTED_PROTOCOL = "0.159.2"
TESTED_HOST = "26.928.31416"


def host_diagnostics() -> dict:
    result = {"protocol": {"experimental": True, "testedVersion": TESTED_PROTOCOL},
              "host": {"platform": sys.platform, "version": None, "build": None},
              "ui": {"adapter": "electron-cdp", "status": "unprobed"}}
    if sys.platform not in {"darwin", "win32"}:
        return result
    native = desktop()
    app = native.discover_app()
    result["host"]["path"] = str(app) if app else None
    result["protocol"]["binary"] = native.bundled_agent()
    if app and sys.platform == "darwin":
        try:
            with (Path(app) / "Contents/Info.plist").open("rb") as stream:
                info = plistlib.load(stream)
            result["host"].update(version=info.get("CFBundleShortVersionString"), build=info.get("CFBundleVersion"))
            package = Path(app) / "Contents/Resources/codex-cli/codex-package.json"
            result["protocol"]["version"] = json.loads(package.read_text())["version"]
        except (OSError, ValueError, KeyError):
            pass
    result["protocol"]["matchesTestedVersion"] = result["protocol"].get("version") == TESTED_PROTOCOL
    # A matching binary version is not evidence that private host/UI APIs work.
    return result
