#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo_root"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "macOS packaging requires Darwin" >&2
  exit 1
fi
if [[ "$(uname -m)" != "arm64" ]]; then
  echo "This v1 build targets Apple Silicon (arm64) only" >&2
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required" >&2
  exit 1
fi
if ! command -v npm >/dev/null 2>&1; then
  echo "npm is required for the Tauri build" >&2
  exit 1
fi

# Prefer the repository environment so the sidecar is built from the pinned
# project dependencies rather than an unrelated system/Conda environment.
python_bin="$(command -v python3)"
if [[ -x "$repo_root/.venv/bin/python" ]] \
  && "$repo_root/.venv/bin/python" -c 'import PyInstaller, fastapi, uvicorn' >/dev/null 2>&1; then
  python_bin="$repo_root/.venv/bin/python"
fi
if ! "$python_bin" -c 'import PyInstaller, fastapi, uvicorn' >/dev/null 2>&1 \
  && [[ -x /opt/anaconda3/bin/python3 ]] \
  && /opt/anaconda3/bin/python3 -c 'import PyInstaller, fastapi, uvicorn' >/dev/null 2>&1; then
  python_bin="/opt/anaconda3/bin/python3"
fi

# PyInstaller and optional Conda hooks may otherwise write caches under a
# protected user Library directory.  Keep build-only caches inside the repo.
mkdir -p "$repo_root/.build/pyinstaller-config" "$repo_root/.build/matplotlib" "$repo_root/.build/cache"
export PYINSTALLER_CONFIG_DIR="${PYINSTALLER_CONFIG_DIR:-$repo_root/.build/pyinstaller-config}"
export MPLCONFIGDIR="${MPLCONFIGDIR:-$repo_root/.build/matplotlib}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-$repo_root/.build/cache}"
# Without a configured Developer ID, Tauri otherwise leaves only the Mach-O
# linker signature and the app bundle fails `codesign --verify --deep`.  A
# complete ad-hoc signature keeps local development packages internally
# consistent while still making it clear that they are not notarized.
export APPLE_SIGNING_IDENTITY="${APPLE_SIGNING_IDENTITY:--}"

# Build the static frontend before PyInstaller collects it into the sidecar.
# The packaged app serves this directory from FastAPI on the loopback port.
npm run build:web
"$python_bin" scripts/build_sidecar.py --target aarch64-apple-darwin
npm exec -- tauri build --target aarch64-apple-darwin

# Exercise the exact sidecar that the signed app will launch.  Testing the
# unsigned PyInstaller artifact above would miss macOS hardened-runtime
# library-validation failures introduced while bundling/signing the app.
app_bundle="$repo_root/src-tauri/target/aarch64-apple-darwin/release/bundle/macos/Codex Taskboard.app"
"$python_bin" scripts/check_sidecar_smoke.py --app "$app_bundle" --required

echo "App and DMG output are under src-tauri/target/aarch64-apple-darwin/release/bundle/"
