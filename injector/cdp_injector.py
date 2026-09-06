#!/usr/bin/env python3
"""Small, loopback-only CDP injector for Codex Taskboard.

The injector deliberately talks to the renderer through the public Chrome
DevTools Protocol.  It never reads or edits the Codex application bundle,
renderer modules, or Codex data files.  The only renderer changes are a
document-start registration and an idempotent DOM script which adds the
Taskboard entry and iframe.

The WebSocket client below is intentionally dependency-free so that the same
module can be bundled as a PyInstaller sidecar.  It implements the subset of
RFC 6455 needed by CDP (text frames, continuation frames, ping/pong, and
close frames).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from codex_taskboard.platforms import configure_standard_streams, desktop, user_data_directory


DEFAULT_CDP_PORT = 9229
DEFAULT_FALLBACK_CDP_PORT = 9231
DEFAULT_TASKBOARD_PORT = 47823
DEFAULT_TASKBOARD_URL = f"http://127.0.0.1:{DEFAULT_TASKBOARD_PORT}"
# Kept as a compatibility export for the sidecar and development scripts.
# An empty value means "discover the installed app"; no user path is needed.
DEFAULT_CODEX_APP = ""
DEFAULT_INJECTION_FILE = Path(__file__).with_name("inject.js")
INJECTION_TIMEOUT = 15.0
TARGET_POLL_SECONDS = 1.5
TARGET_WAIT_TIMEOUT = 30.0
ENTRY_WAIT_TIMEOUT = 15.0
CODEX_BROWSER_DATABASES = (
    "Default/Partitions/codex-browser-app/Cookies",
    "Default/Partitions/codex-browser-app/Login Data",
    "Default/Partitions/codex-browser-app/Login Data For Account",
)
CODEX_PROFILE_IMPORT_MARKER = ".codex-taskboard-browser-profile-imported-v1"

# The launcher is deliberately loopback-only.  Do not let a user's HTTP(S)
# proxy (or a system proxy inherited by the packaged app) intercept the CDP
# discovery or the HTML document used for the sandboxed iframe.
_LOOPBACK_OPENER = build_opener(ProxyHandler({}))


def without_taskboard_launcher_environment(
    environment: dict[str, str] | None = None,
) -> dict[str, str]:
    """Remove Taskboard-only variables before launching the Codex app."""

    source = os.environ if environment is None else environment
    return {
        name: value
        for name, value in source.items()
        if not name.startswith("CODEX_TASKBOARD_")
    }


def default_codex_source_profile() -> Path | None:
    """Return the installed Codex profile used only as a read-only source."""

    configured = os.environ.get("CODEX_TASKBOARD_CODEX_SOURCE_PROFILE", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return desktop().source_profile() if sys.platform in {"darwin", "win32"} else None


def import_codex_browser_profile(
    source_profile: Path | None,
    independent_profile: Path,
) -> bool:
    """Copy the three Codex browser databases through SQLite's read-only API.

    The source profile is never opened writable.  Each destination is first
    built in a temporary file and atomically renamed into the independent
    profile, so an interrupted copy cannot leave a database that looks valid
    to Chromium.  A marker is written only after all three source databases
    were present and copied successfully; missing files can therefore be
    picked up on a later launch.
    """

    if source_profile is None:
        return False
    source_profile = Path(source_profile).expanduser().resolve()
    independent_profile = Path(independent_profile).expanduser().resolve()
    if source_profile == independent_profile:
        return False
    marker_path = independent_profile / CODEX_PROFILE_IMPORT_MARKER
    if marker_path.is_file():
        return False

    sources = [
        (relative, source_profile / relative)
        for relative in CODEX_BROWSER_DATABASES
        if (source_profile / relative).is_file()
    ]
    if not sources:
        return False

    import sqlite3

    independent_profile.mkdir(parents=True, exist_ok=True)
    copied = 0
    try:
        for relative, source_path in sources:
            destination_path = independent_profile / relative
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary_name = tempfile.mkstemp(
                prefix=f".{destination_path.name}.",
                suffix=".tmp",
                dir=destination_path.parent,
            )
            os.close(fd)
            temporary_path = Path(temporary_name)
            try:
                source_uri = source_path.as_uri() + "?mode=ro"
                source_db = sqlite3.connect(source_uri, uri=True)
                destination_db = sqlite3.connect(str(temporary_path))
                try:
                    source_db.backup(destination_db)
                    destination_db.commit()
                finally:
                    destination_db.close()
                    source_db.close()
                os.chmod(temporary_path, 0o600)
                os.replace(temporary_path, destination_path)
                copied += 1
            finally:
                try:
                    temporary_path.unlink()
                except FileNotFoundError:
                    pass
        if copied == len(CODEX_BROWSER_DATABASES):
            marker_path.write_text("1\n", encoding="utf-8")
            os.chmod(marker_path, 0o600)
        return copied > 0
    except Exception:
        # Keep the marker absent when a source is incomplete or a database is
        # temporarily unavailable.  The next managed launch can retry.
        raise


class InjectorError(RuntimeError):
    """A recoverable launcher/injection failure."""


def _runtime_exception_message(result: dict[str, Any], operation: str) -> str:
    """Keep the renderer's original exception details in a useful message."""

    details = result.get("exceptionDetails")
    if not isinstance(details, dict):
        return operation
    try:
        serialized = json.dumps(details, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        serialized = repr(details)
    return f"{operation}: {serialized}"


def discover_codex_app() -> Path | None:
    try:
        return desktop().discover_app()
    except RuntimeError:
        if sys.platform not in {"darwin", "win32"}:
            return None
        raise


def codex_executable_path(app_path: Path) -> Path:
    return desktop().executable_path(app_path)


def _read_json(url: str, timeout: float = 1.5) -> Any:
    request = Request(url, headers={"Accept": "application/json"})
    try:
        with _LOOPBACK_OPENER.open(request, timeout=timeout) as response:  # nosec B310 - loopback URL is constructed below
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise InjectorError(f"Could not read {url}: {exc}") from exc


def _loopback_host(hostname: str) -> bool:
    # CDP is bound explicitly to this address.  Do not allow a hostname here:
    # it could be redirected to a non-loopback address by local DNS/hosts.
    return hostname == "127.0.0.1"


def validate_taskboard_url(value: str) -> str:
    """Validate the iframe origin and return a normalized URL."""

    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not _loopback_host(parsed.hostname or ""):
        raise InjectorError("Taskboard URL must use a loopback HTTP(S) origin")
    if parsed.username or parsed.password or parsed.fragment:
        raise InjectorError("Taskboard URL must not contain credentials or a fragment")
    try:
        port = parsed.port
    except ValueError as exc:
        raise InjectorError("Taskboard URL must include a valid port") from exc
    if port is None or not 1 <= port <= 65535:
        raise InjectorError("Taskboard URL must include a valid port")
    return value.rstrip("/")


def validate_cdp_websocket_url(value: str, port: int) -> str:
    """Accept only the loopback WebSocket advertised by the requested CDP port."""

    parsed = urlparse(value)
    try:
        parsed_port = parsed.port
    except ValueError as exc:
        raise InjectorError(f"Rejected unexpected Codex CDP target URL: {value}") from exc
    if (
        parsed.scheme != "ws"
        or not _loopback_host(parsed.hostname or "")
        or parsed_port != port
        or not parsed.path.startswith("/devtools/page/")
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise InjectorError(f"Rejected unexpected Codex CDP target URL: {value}")
    return value


def is_codex_target(target: dict[str, Any]) -> bool:
    """Return true for normal Codex renderer pages, excluding transient overlays."""

    if target.get("type") != "page" or not target.get("webSocketDebuggerUrl"):
        return False
    url = str(target.get("url") or "")
    title = str(target.get("title") or "")
    if "initialRoute=%2Fglobal-dictation" in url or "initialRoute=%2Favatar-overlay" in url:
        return False
    # The renderer normally advertises an app:// URL.  The title fallback is
    # needed by some Codex builds, but must stay exact: a generic page whose
    # title merely contains the word "Codex" is not a safe injection target.
    return url.startswith("app://") or title.casefold() == "codex"


def discover_targets(port: int) -> list[dict[str, Any]]:
    """Read and validate renderer targets from a loopback CDP endpoint."""

    raw_targets = _read_json(f"http://127.0.0.1:{port}/json/list")
    if not isinstance(raw_targets, list):
        raise InjectorError("Codex CDP returned an invalid target list")
    targets: list[dict[str, Any]] = []
    for target in raw_targets:
        if not isinstance(target, dict) or not is_codex_target(target):
            continue
        try:
            websocket_url = validate_cdp_websocket_url(
                str(target["webSocketDebuggerUrl"]), port
            )
        except (KeyError, InjectorError):
            continue
        targets.append({**target, "webSocketDebuggerUrl": websocket_url})
    return targets


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise InjectorError("CDP WebSocket closed while reading a frame")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def encode_client_frame(opcode: int, payload: bytes, mask: bytes | None = None) -> bytes:
    """Encode one masked client frame (also used by the protocol self-check)."""

    if not 0 <= opcode <= 0x0F:
        raise ValueError("opcode must fit in four bits")
    mask = mask or secrets.token_bytes(4)
    if len(mask) != 4:
        raise ValueError("WebSocket masks must contain four bytes")
    length = len(payload)
    if length < 126:
        header = struct.pack("!BB", 0x80 | opcode, 0x80 | length)
    elif length <= 0xFFFF:
        header = struct.pack("!BBH", 0x80 | opcode, 0x80 | 126, length)
    else:
        header = struct.pack("!BBQ", 0x80 | opcode, 0x80 | 127, length)
    masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
    return header + mask + masked


def decode_frame_bytes(data: bytes) -> tuple[bool, int, bytes, int]:
    """Decode one server/client frame from bytes for small protocol tests.

    Returns ``(fin, opcode, unmasked_payload, bytes_consumed)``.  This helper
    is deliberately strict about the extended length fields so malformed CDP
    peers cannot shift the next JSON message out of alignment.
    """

    if len(data) < 2:
        raise ValueError("incomplete WebSocket frame")
    first, second = data[:2]
    fin = bool(first & 0x80)
    opcode = first & 0x0F
    length_code = second & 0x7F
    offset = 2
    if length_code == 126:
        if len(data) < offset + 2:
            raise ValueError("incomplete extended WebSocket length")
        length = struct.unpack("!H", data[offset : offset + 2])[0]
        offset += 2
    elif length_code == 127:
        if len(data) < offset + 8:
            raise ValueError("incomplete extended WebSocket length")
        length = struct.unpack("!Q", data[offset : offset + 8])[0]
        offset += 8
    else:
        length = length_code
    mask = data[offset : offset + 4] if second & 0x80 else None
    if mask is not None:
        offset += 4
    end = offset + length
    if len(data) < end:
        raise ValueError("incomplete WebSocket payload")
    payload = data[offset:end]
    if mask is not None:
        payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
    return fin, opcode, payload, end


class WebSocket:
    """Minimal RFC 6455 client with a reader for CDP responses and events."""

    def __init__(self, url: str) -> None:
        parsed = urlparse(url)
        self._host = parsed.hostname or ""
        self._port = parsed.port or 80
        self._path = parsed.path or "/"
        if parsed.query:
            self._path += f"?{parsed.query}"
        if not _loopback_host(self._host):
            raise InjectorError("CDP WebSocket host is not loopback")
        self._socket: socket.socket | None = None
        self._send_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending: dict[int, list[Any]] = {}
        self._request_id = 0
        self._reader: threading.Thread | None = None
        self._event_handler: Callable[[str, dict[str, Any]], None] | None = None
        self._host_install_lock = threading.Lock()
        self._closed = False

    def set_event_handler(self, handler: Callable[[str, dict[str, Any]], None] | None) -> None:
        self._event_handler = handler

    def connect(self) -> None:
        if self._socket is not None and not self._closed:
            return
        sock = socket.create_connection((self._host, self._port), timeout=INJECTION_TIMEOUT)
        key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
        request = (
            f"GET {self._path} HTTP/1.1\r\n"
            f"Host: {self._host}:{self._port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        ).encode("ascii")
        try:
            sock.sendall(request)
            response = b""
            while b"\r\n\r\n" not in response:
                chunk = sock.recv(4096)
                if not chunk:
                    raise InjectorError("CDP WebSocket handshake ended early")
                response += chunk
                if len(response) > 64 * 1024:
                    raise InjectorError("CDP WebSocket handshake was unexpectedly large")
            headers = response.decode("latin1", errors="replace").split("\r\n")
            if not headers or not headers[0].startswith("HTTP/1.1 101"):
                raise InjectorError(f"CDP WebSocket handshake failed: {headers[0] if headers else 'unknown'}")
            header_map = {
                line.split(":", 1)[0].strip().lower(): line.split(":", 1)[1].strip()
                for line in headers[1:]
                if ":" in line
            }
            expected_accept = base64.b64encode(
                hashlib.sha1(
                    (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")
                ).digest()
            ).decode("ascii")
            if header_map.get("sec-websocket-accept") != expected_accept:
                raise InjectorError("CDP WebSocket handshake had an invalid accept key")
            sock.settimeout(None)
        except Exception:
            try:
                sock.close()
            except OSError:
                pass
            raise
        self._socket = sock
        self._closed = False
        self._reader = threading.Thread(target=self._reader_loop, name="codex-taskboard-cdp", daemon=True)
        self._reader.start()

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        sock = self._socket
        if sock is None or self._closed:
            raise InjectorError("CDP WebSocket is closed")
        with self._send_lock:
            try:
                sock.sendall(encode_client_frame(opcode, payload))
            except OSError as exc:
                raise InjectorError(f"CDP WebSocket write failed: {exc}") from exc

    def send_json(self, payload: dict[str, Any]) -> None:
        self._send_frame(0x1, json.dumps(payload, separators=(",", ":")).encode("utf-8"))

    def _receive_frame(self) -> tuple[bool, int, bytes]:
        sock = self._socket
        if sock is None:
            raise InjectorError("CDP WebSocket is closed")
        first_two = _recv_exact(sock, 2)
        first, second = first_two
        fin = bool(first & 0x80)
        opcode = first & 0x0F
        length = second & 0x7F
        if length == 126:
            length = struct.unpack("!H", _recv_exact(sock, 2))[0]
        elif length == 127:
            length = struct.unpack("!Q", _recv_exact(sock, 8))[0]
        if length > 64 * 1024 * 1024:
            raise InjectorError("CDP WebSocket frame is too large")
        mask = _recv_exact(sock, 4) if second & 0x80 else None
        payload = _recv_exact(sock, length)
        if mask:
            payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        return fin, opcode, payload

    def _reader_loop(self) -> None:
        fragments: list[bytes] = []
        first_opcode: int | None = None
        try:
            while not self._closed:
                try:
                    fin, opcode, payload = self._receive_frame()
                except (InjectorError, OSError):
                    break
                if opcode == 0x8:
                    break
                if opcode == 0x9:
                    try:
                        self._send_frame(0xA, payload)
                    except InjectorError:
                        break
                    continue
                if opcode == 0xA:
                    continue
                if opcode in {0x1, 0x2}:
                    first_opcode = opcode
                    fragments = [payload]
                elif opcode == 0x0 and first_opcode is not None:
                    fragments.append(payload)
                else:
                    continue
                if not fin:
                    continue
                if first_opcode != 0x1:
                    first_opcode = None
                    fragments = []
                    continue
                try:
                    message = json.loads(b"".join(fragments).decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    first_opcode = None
                    fragments = []
                    continue
                first_opcode = None
                fragments = []
                if not isinstance(message, dict):
                    continue
                response_id = message.get("id")
                if isinstance(response_id, int):
                    with self._pending_lock:
                        pending = self._pending.get(response_id)
                        if pending is not None:
                            pending[1] = message
                            pending[0].set()
                    continue
                method = message.get("method")
                params = message.get("params")
                if isinstance(method, str) and isinstance(params, dict) and self._event_handler:
                    try:
                        self._event_handler(method, params)
                    except Exception as exc:  # noqa: BLE001 - event hooks must not kill the reader
                        print(f"Codex Taskboard CDP event handler failed: {exc}", file=sys.stderr, flush=True)
        finally:
            if not self._closed:
                self._closed = True
                self._socket = None
                with self._pending_lock:
                    pending = list(self._pending.values())
                    self._pending.clear()
                for item in pending:
                    item[1] = None
                    item[0].set()

    def request(self, method: str, params: dict[str, Any] | None = None, *, timeout: float = INJECTION_TIMEOUT) -> dict[str, Any]:
        self.connect()
        with self._pending_lock:
            self._request_id += 1
            request_id = self._request_id
            pending: list[Any] = [threading.Event(), None]
            self._pending[request_id] = pending
        try:
            self.send_json({"id": request_id, "method": method, "params": params or {}})
        except InjectorError:
            with self._pending_lock:
                self._pending.pop(request_id, None)
            raise
        if not pending[0].wait(timeout):
            with self._pending_lock:
                self._pending.pop(request_id, None)
            raise InjectorError(f"Timed out waiting for CDP response to {method}")
        with self._pending_lock:
            self._pending.pop(request_id, None)
        message = pending[1]
        if not isinstance(message, dict):
            raise InjectorError(f"CDP WebSocket closed while waiting for {method}")
        if "error" in message:
            error = message.get("error") or {}
            raise InjectorError(f"CDP {method} failed: {error.get('message', error)}")
        result = message.get("result")
        return result if isinstance(result, dict) else {}

    def close(self) -> None:
        if self._closed and self._socket is None:
            return
        sock = self._socket
        self._closed = True
        self._socket = None
        with self._pending_lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for item in pending:
            item[1] = None
            item[0].set()
        if sock is None:
            return
        try:
            with self._send_lock:
                sock.sendall(encode_client_frame(0x8, b""))
        except OSError:
            pass
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass


def _read_injection_source(path: Path) -> str:
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InjectorError(f"Could not read injection script {path}: {exc}") from exc
    if not source.strip():
        raise InjectorError(f"Injection script {path} is empty")
    return source


def _find_free_port(start: int) -> int:
    for port in range(start, start + 100):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise InjectorError(f"Could not find a free CDP port starting at {start}")


class CdpInjector:
    """Discover, launch, and continuously inject into Codex renderers."""

    HOST_BINDING_NAME = "__codexTaskboardHostV1"
    HOST_REQUEST_MESSAGE = "__codexTaskboardHostRequestV1"
    HOST_RESPONSE_MESSAGE = "__codexTaskboardHostResponseV1"

    def __init__(
        self,
        *,
        port: int = DEFAULT_CDP_PORT,
        taskboard_url: str = DEFAULT_TASKBOARD_URL,
        injection_path: Path = DEFAULT_INJECTION_FILE,
        app_path: str | None = None,
        launch: bool = True,
        bypass_csp: bool = True,
        profile_path: Path | None = None,
        source_profile_path: Path | None = None,
    ) -> None:
        self.taskboard_url = validate_taskboard_url(taskboard_url)
        self.port = port
        self.injection_path = Path(injection_path)
        self.app_path = Path(app_path).expanduser().resolve() if app_path else discover_codex_app()
        self.launch = launch
        self.bypass_csp = bypass_csp
        self.profile_path = (
            Path(profile_path).expanduser().resolve()
            if profile_path is not None
            else _default_profile_path()
        )
        self.source_profile_path = (
            Path(source_profile_path).expanduser().resolve()
            if source_profile_path is not None
            else default_codex_source_profile()
        )
        self.host_capability = secrets.token_hex(32)
        self.stop_event = threading.Event()
        self.open_event = threading.Event()
        self.connections: dict[str, WebSocket] = {}
        self._connections_lock = threading.RLock()
        self._open_lock = threading.Lock()
        self.managed_pid: int | None = None
        self._injected_targets: set[str] = set()
        self._injection_registrations: dict[str, str] = {}
        self._runtime_source_hash = ""
        self._source = _read_injection_source(self.injection_path)
        self._native_decoder = Path(__file__).with_name("native_messages.js").read_text(encoding="utf-8")
        self.native_event_handler = None

    def native_connected(self) -> bool:
        with self._connections_lock:
            return any(not item._closed and getattr(item, "host_context_id", None)
                       for item in self.connections.values())

    def native_request(self, method: str | None, params: dict[str, Any], timeout: float) -> dict[str, Any]:
        """Send through the existing desktop bridge; never retry a mutation."""
        with self._connections_lock:
            connection = next((item for item in self.connections.values()
                               if not item._closed and getattr(item, "host_context_id", None)), None)
        if connection is None:
            raise InjectorError("Codex 桌面尚未连接，请稍后重试")
        request_id = f"taskboard-{secrets.token_hex(16)}"
        if method is None:
            expression = f"window.electronBridge.sendMessageFromView({json.dumps({'type': 'mcp-response', 'hostId': 'local', 'response': params})})"
        elif method in {"desktop/worktree-create-managed", "desktop/worktree-set-owner-thread"}:
            message = {"type": "fetch", "requestId": request_id, "method": "POST",
                       "url": f"vscode://codex/{method.removeprefix('desktop/')}",
                       "body": json.dumps(params)}
            expression = f"""new Promise((resolve) => {{
              const id = {json.dumps(request_id)};
              const finish = (value) => {{ clearTimeout(timer); window.removeEventListener('message', receive); resolve(value); }};
              const receive = ({self._native_decoder})((data) => {{
                if (data?.type !== 'fetch-response' || data.requestId !== id) return;
                if (data.responseType === 'error') finish({{error: {{message: data.error || 'Codex worktree request failed'}}}});
                else {{ try {{ finish({{result: JSON.parse(data.bodyJsonString || 'null')}}); }}
                       catch (_) {{ finish({{error: {{message: 'Invalid Codex worktree response'}}}}); }} }}
              }});
              const timer = setTimeout(() => finish({{error: {{message: 'Codex 工作树响应超时，请在 Codex 中检查工作树后再重试'}}}}), {int(timeout * 1000)});
              window.addEventListener('message', receive);
              try {{ Promise.resolve(window.electronBridge.sendMessageFromView({json.dumps(message)})).catch(error => finish({{error: {{message: String(error)}}}})); }}
              catch (error) {{ finish({{error: {{message: String(error)}}}}); }}
            }})"""
        else:
            message = {"type": "mcp-request", "hostId": "local", "request": {
                "id": request_id, "method": method, "params": params}}
            # Native composer flows capture a browser route for their thread.
            # Taskboard can start/resume turns without opening that composer,
            # so bind the session to this host window before dispatching input.
            # This registers routing only; Codex still owns browser permissions.
            browser_route = ""
            if method in {"turn/start", "turn/steer"} and isinstance(params.get("threadId"), str) and params["threadId"]:
                browser_route = f"await window.electronBridge.sendMessageFromView({json.dumps({'type': 'browser-use-session-route-capture', 'conversationId': params['threadId']})});"
            expression = f"""new Promise((resolve, reject) => {{
              const id = {json.dumps(request_id)};
              const finish = (value) => {{ clearTimeout(timer); window.removeEventListener('message', receive); resolve(value); }};
              const receive = ({self._native_decoder})((data) => {{
                if (data?.type === 'mcp-response' && data.hostId === 'local' && data.message?.id === id) finish(data.message);
              }});
              const timer = setTimeout(() => finish({{error: {{message: 'Codex 响应超时，请检查会话后再重试'}}}}), {int(timeout * 1000)});
              window.addEventListener('message', receive);
              try {{ (async () => {{ {browser_route} await window.electronBridge.sendMessageFromView({json.dumps(message)}); }})().catch(error => finish({{error: {{message: String(error)}}}})); }}
              catch (error) {{ finish({{error: {{message: String(error)}}}}); }}
            }})"""
        result = connection.request("Runtime.evaluate", {"expression": expression, "awaitPromise": True, "returnByValue": True}, timeout=timeout + 2)
        if result.get("exceptionDetails"):
            raise InjectorError("Codex 桌面消息通道不可用")
        return (result.get("result") or {}).get("value") or {}

    def _emit(self, event: str, **payload: Any) -> None:
        """Write a machine-readable lifecycle line for the native shell.

        The Tauri wrapper only needs a small, stable contract.  Keep verbose
        diagnostics on stderr so stdout can be consumed line-by-line without
        parsing human log messages.
        """

        message = {"event": event, **payload}
        try:
            print(json.dumps(message, ensure_ascii=False, separators=(",", ":")), flush=True)
        except BrokenPipeError:
            # The native shell may exit before the injector process receives
            # its termination signal.  Do not turn a diagnostic write into a
            # second, misleading injector failure.
            self.stop_event.set()

    def stop(self, *_args: Any) -> None:
        self.stop_event.set()

    def request_open(self, *_args: Any) -> None:
        """Queue a menu-bar request to show the embedded panel."""

        self.open_event.set()

    def open_connected_renderers(self) -> None:
        """Ask every currently connected renderer to show its Taskboard panel."""

        # Signal handlers may run while the target polling loop is replacing a
        # renderer connection.  Take a snapshot under the lock, then keep
        # going when one stale connection fails so all live renderers receive
        # the open request.
        with self._open_lock:
            with self._connections_lock:
                connections = list(self.connections.items())
            for target_id, connection in connections:
                if self.stop_event.is_set():
                    return
                try:
                    opened = connection.request(
                        "Runtime.evaluate",
                        {
                            "expression": "window.__codexTaskboardInjection__?.open?.()",
                            "awaitPromise": True,
                            "returnByValue": False,
                        },
                    )
                    if opened.get("exceptionDetails"):
                        raise InjectorError(_runtime_exception_message(opened, "Taskboard open failed in Codex renderer"))
                    self._bring_to_front(connection)
                    self._activate_codex_app()
                except Exception as exc:  # noqa: BLE001 - one stale renderer must not block the others
                    self._emit(
                        "error",
                        phase="open",
                        targetId=target_id,
                        errorType=type(exc).__name__,
                        message=str(exc),
                    )
                    print(
                        f"Could not open Taskboard in Codex target {target_id}: {exc}",
                        file=sys.stderr,
                        flush=True,
                    )

    @staticmethod
    def _bring_to_front(connection: WebSocket) -> None:
        """Bring the renderer containing the panel to the foreground.

        ``Page.bringToFront`` is a convenience operation and is not available
        in a few older Chromium builds.  A missing/failed convenience call
        must not turn an otherwise successful DOM injection into a failure.
        """

        try:
            connection.request("Page.bringToFront")
        except (InjectorError, OSError) as exc:
            print(f"Codex Taskboard could not bring the Codex page to front: {exc}", file=sys.stderr, flush=True)

    def _activate_codex_app(self) -> None:
        if self.managed_pid:
            try:
                desktop().activate(self.managed_pid)
            except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                print(f"Codex Taskboard could not activate Codex: {exc}", file=sys.stderr, flush=True)

    @staticmethod
    def _process_table() -> list[tuple[int, str]]:
        try:
            return desktop().process_table()
        except (OSError, subprocess.SubprocessError) as exc:
            raise InjectorError(f"Could not inspect Codex processes: {exc}") from exc

    def _codex_process_command(self, command: str) -> bool:
        return self.app_path is not None and desktop().matches_process(
            command, codex_executable_path(self.app_path))

    def _codex_process_records(self) -> list[tuple[int, str]]:
        return [
            (pid, command)
            for pid, command in self._process_table()
            if pid != os.getpid() and self._codex_process_command(command)
        ]

    def _cdp_ports_from_processes(self) -> list[int]:
        ports: list[int] = []
        for _pid, command in self._codex_process_records():
            for match in re.finditer(r"--remote-debugging-port(?:=|\s+)(\d+)", command):
                try:
                    port = int(match.group(1))
                except ValueError:
                    continue
                if 1 <= port <= 65535 and port not in ports:
                    ports.append(port)
        return ports

    def _codex_pid_for_port(self, port: int) -> int | None:
        marker = re.compile(r"--remote-debugging-port(?:=|\s+)(\d+)")
        for pid, command in self._codex_process_records():
            match = marker.search(command)
            if match and match.group(1) == str(port):
                return pid
        return None

    def _targets(self) -> list[dict[str, Any]]:
        ports = [self.port, *self._cdp_ports_from_processes()]
        seen: set[int] = set()
        for port in ports:
            if port in seen:
                continue
            seen.add(port)
            try:
                targets = discover_targets(port)
            except InjectorError:
                continue
            if targets:
                self.port = port
                self.managed_pid = self._codex_pid_for_port(port) or self.managed_pid
                return targets
        return []

    def _ordinary_codex_processes(self) -> list[tuple[int, str]]:
        """Return normal Codex root processes which cannot be injected.

        A running Electron instance cannot acquire CDP after startup.  The
        original launcher asks the user to restart that instance first; doing
        so here prevents a silent second window from stealing the user's
        attention while the original remains in front.
        """

        return [
            (pid, command)
            for pid, command in self._codex_process_records()
            if not re.search(r"--remote-debugging-(?:port(?:=|\s)|pipe)", command)
        ]

    @staticmethod
    def _find_frame_by_name(frame_tree: dict[str, Any], frame_name: str) -> dict[str, Any] | None:
        frame = frame_tree.get("frame")
        if isinstance(frame, dict) and frame.get("name") == frame_name:
            return frame
        for child in frame_tree.get("childFrames") or []:
            if isinstance(child, dict):
                found = CdpInjector._find_frame_by_name(child, frame_name)
                if found:
                    return found
        return None

    def _taskboard_document(self, frame_capability: str) -> str:
        parsed_url = urlparse(self.taskboard_url)
        query = dict(parse_qsl(parsed_url.query, keep_blank_values=True))
        query.update(host="codex", embedded="1")
        page_url = urlunparse(parsed_url._replace(query=urlencode(query)))
        request = Request(
            page_url,
            headers={"Accept": "text/html", "Cache-Control": "no-store", "Origin": "app://-"},
        )
        try:
            with _LOOPBACK_OPENER.open(request, timeout=INJECTION_TIMEOUT) as response:  # nosec B310 - loopback URL validated above
                html = response.read().decode("utf-8")
        except (HTTPError, URLError, TimeoutError, OSError, UnicodeDecodeError) as exc:
            raise InjectorError(f"Could not load Taskboard document: {exc}") from exc
        if "<head" not in html.lower():
            raise InjectorError("Taskboard document has no head element")
        injected = (
            f"<base href={json.dumps(page_url)} />"
            f"<script>globalThis.__CODEX_TASKBOARD_FRAME_CAPABILITY__={json.dumps(frame_capability)};</script>"
        )
        return re.sub(r"<head\s*>", lambda match: match.group(0) + injected, html, count=1, flags=re.IGNORECASE)

    def _load_frame(self, connection: WebSocket, frame_name: str, frame_capability: str) -> dict[str, Any]:
        if not re.fullmatch(r"codex-taskboard-[0-9a-f-]{36}", frame_name or "", re.IGNORECASE):
            raise InjectorError("Rejected unexpected Taskboard frame name")
        if not re.fullmatch(r"[0-9a-f-]{36}", frame_capability or "", re.IGNORECASE):
            raise InjectorError("Rejected unexpected Taskboard frame capability")
        html = self._taskboard_document(frame_capability)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not self.stop_event.is_set():
            frame_tree = connection.request("Page.getFrameTree").get("frameTree") or {}
            if isinstance(frame_tree, dict):
                target_frame = self._find_frame_by_name(frame_tree, frame_name)
                if target_frame and target_frame.get("id"):
                    connection.request("Page.setDocumentContent", {"frameId": target_frame["id"], "html": html})
                    return {"loaded": True}
            time.sleep(0.05)
        raise InjectorError("Timed out waiting for the isolated Taskboard frame")

    def _send_host_response(self, connection: WebSocket, context_id: int, response: dict[str, Any]) -> None:
        expression = (
            "window.postMessage({"
            f"type:{json.dumps(self.HOST_RESPONSE_MESSAGE)},"
            f"capability:{json.dumps(self.host_capability)},"
            f"response:{json.dumps(response, separators=(',', ':'))}"
            "}, window.location.origin)"
        )
        connection.request("Runtime.evaluate", {"contextId": context_id, "expression": expression, "returnByValue": True})

    def _handle_host_binding(self, connection: WebSocket, params: dict[str, Any]) -> None:
        context_id = params.get("executionContextId")
        if context_id != getattr(connection, "host_context_id", None):
            return
        try:
            payload = json.loads(str(params.get("payload") or "{}"))
        except json.JSONDecodeError:
            return
        if not isinstance(payload, dict) or not isinstance(payload.get("id"), str):
            return
        request_id = payload["id"]
        action = payload.get("action")
        response: dict[str, Any] = {"id": request_id, "ok": False}
        try:
            if action == "ensure":
                response.update(ok=True, managed=True, restarted=False)
            elif action == "language":
                language = payload.get("language")
                if not isinstance(language, str) or len(language) > 64:
                    raise InjectorError("Invalid host language")
                self._emit("language", language=language)
                response["ok"] = True
            elif action == "load-frame":
                response.update(self._load_frame(connection, str(payload.get("frameName") or ""), str(payload.get("frameCapability") or "")))
                response["ok"] = True
            else:
                raise InjectorError(f"Unsupported Taskboard host action: {action}")
        except (InjectorError, OSError) as exc:
            response["error"] = str(exc)
        try:
            self._send_host_response(connection, int(context_id), response)
        except (InjectorError, OSError):
            pass

    def _reinstall_host_binding(self, connection: WebSocket) -> None:
        try:
            self._install_host_binding(connection)
        except (InjectorError, OSError) as exc:
            if not connection._closed:
                print(f"Codex Taskboard host binding refresh failed: {exc}", file=sys.stderr, flush=True)

    def _handle_cdp_event(self, connection: WebSocket, method: str, params: dict[str, Any]) -> None:
        if method in {"Runtime.exceptionThrown", "Log.entryAdded"}:
            detail = params.get("exceptionDetails", {}) if method == "Runtime.exceptionThrown" else params.get("entry", {})
            # Keep renderer failures diagnosable without logging console
            # arguments, project contents, or host/frame capabilities.
            print(json.dumps({"event": "renderer_error", "method": method,
                              "text": str(detail.get("text", ""))[:500],
                              "line": detail.get("lineNumber"),
                              "level": detail.get("level", "error")}), file=sys.stderr, flush=True)
            return
        if method == "Runtime.bindingCalled" and params.get("name") == self.HOST_BINDING_NAME:
            if params.get("executionContextId") == getattr(connection, "host_context_id", None):
                try:
                    payload = json.loads(params.get("payload") or "{}")
                    if payload.get("action") == "native-event":
                        if self.native_event_handler:
                            self.native_event_handler(payload.get("message") or {})
                        return
                except (ValueError, TypeError):
                    return
            threading.Thread(target=self._handle_host_binding, args=(connection, params), name="codex-taskboard-host-request", daemon=True).start()
        elif method in {"Page.loadEventFired", "Page.frameNavigated"}:
            # A renderer navigation creates a new execution context.  The
            # document-start script survives through the registration, but the
            # isolated host binding must be attached to the new context.
            threading.Thread(target=self._reinstall_host_binding, args=(connection,), name="codex-taskboard-host-refresh", daemon=True).start()

    def _install_host_binding(self, connection: WebSocket) -> int:
        with connection._host_install_lock:
            result = connection.request("Page.getFrameTree")
            frame_tree = result.get("frameTree") or {}
            root_frame = frame_tree.get("frame") if isinstance(frame_tree, dict) else None
            frame_id = root_frame.get("id") if isinstance(root_frame, dict) else None
            if not frame_id:
                raise InjectorError("Codex renderer did not expose a root frame")
            isolated = connection.request("Page.createIsolatedWorld", {"frameId": frame_id, "worldName": "codex-taskboard-host"})
            context_id = isolated.get("executionContextId")
            if not isinstance(context_id, int):
                raise InjectorError("Codex renderer did not create a host execution context")
            connection.request("Runtime.addBinding", {"name": self.HOST_BINDING_NAME, "executionContextId": context_id})
            expression = f"""(() => {{
              const capability = {json.dumps(self.host_capability)};
              if (globalThis.__codexTaskboardIsolatedBridgeV1 === capability) return;
              globalThis.__codexTaskboardIsolatedBridgeV1 = capability;
              const receiveNative = ({self._native_decoder})((message) => {{
                if (message.hostId === 'local' && ['mcp-notification', 'mcp-request'].includes(message.type)) {{
                  globalThis[{json.dumps(self.HOST_BINDING_NAME)}](JSON.stringify({{action: 'native-event', message}}));
                }}
              }});
              window.addEventListener("message", (event) => {{
                const message = event.data;
                receiveNative(event);
                if (event.source !== window || event.origin !== window.location.origin || !message || typeof message !== "object"
                  || message.type !== {json.dumps(self.HOST_REQUEST_MESSAGE)} || message.capability !== capability) return;
                globalThis[{json.dumps(self.HOST_BINDING_NAME)}](JSON.stringify(message.payload));
              }});
            }})()"""
            connection.request("Runtime.evaluate", {"contextId": context_id, "expression": expression, "returnByValue": True})
            connection.host_context_id = context_id
            return context_id

    def _read_injection_status(self, connection: WebSocket) -> dict[str, Any]:
        """Read only the renderer-owned readiness markers used by the launcher."""

        result = connection.request(
            "Runtime.evaluate",
            {
                "expression": """({
                  sourceHash: window.__codexTaskboardInjection__?.sourceHash || null,
                  entryMounted: Boolean(document.getElementById('codex-taskboard-entry')),
                  pageMounted: Boolean(document.getElementById('codex-taskboard-page')),
                  pageVisible: document.getElementById('codex-taskboard-page')?.hidden === false,
                  frameMounted: Boolean(document.getElementById('codex-taskboard-frame')),
                  frameVisible: (() => {
                    const frame = document.getElementById('codex-taskboard-frame');
                    const rect = frame?.getBoundingClientRect();
                    return Boolean(frame?.isConnected && !frame.hidden && rect.width > 0 && rect.height > 0);
                  })(),
                  frameReady: window.__codexTaskboardInjection__?.ready === true,
                  frameState: window.__codexTaskboardInjection__?.state || null
                })""",
                "returnByValue": True,
            },
        )
        if result.get("exceptionDetails"):
            raise InjectorError(_runtime_exception_message(result, "Could not read Taskboard injection status"))
        value = (result.get("result") or {}).get("value")
        return value if isinstance(value, dict) else {}

    def _wait_for_injection_status(
        self,
        connection: WebSocket,
        *,
        source_hash: str,
        frame_required: bool,
        timeout: float = ENTRY_WAIT_TIMEOUT,
    ) -> dict[str, Any]:
        """Wait for the DOM entry and, when opened, the actual iframe handshake."""

        deadline = time.monotonic() + timeout
        status: dict[str, Any] = {}
        while time.monotonic() < deadline and not self.stop_event.is_set():
            status = self._read_injection_status(connection)
            mounted = (
                status.get("sourceHash") == source_hash
                and status.get("entryMounted") is True
            )
            ready = (
                status.get("pageVisible") is True
                and status.get("frameMounted") is True
                and status.get("frameReady") is True
                and status.get("frameVisible") is True
            )
            if mounted and (not frame_required or ready):
                return status
            time.sleep(0.25)
        expected = "entry/page/frame" if frame_required else "entry"
        raise InjectorError(
            f"Codex renderer did not report the Taskboard {expected} ready within {timeout:g} seconds"
        )

    def _runtime_source(self) -> str:
        body = (
            f"window.__CODEX_TASKBOARD_URL__ = {json.dumps(self.taskboard_url)};\n"
            f"window.__CODEX_TASKBOARD_HOST_CAPABILITY__ = {json.dumps(self.host_capability)};\n"
            f"{self._source}\n//# sourceURL=codex-taskboard.user.js"
        )
        source_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        self._runtime_source_hash = source_hash
        return f"window.__CODEX_TASKBOARD_SOURCE_HASH__ = {json.dumps(source_hash)};\n{body}"

    def _inject_target(self, target: dict[str, Any]) -> None:
        target_id = str(target.get("id") or target.get("targetId") or "")
        if not target_id or target_id in self._injected_targets:
            return
        connection = WebSocket(str(target["webSocketDebuggerUrl"]))
        connection.set_event_handler(lambda method, params: self._handle_cdp_event(connection, method, params))
        registration_id: str | None = None
        try:
            connection.connect()
            connection.request("Page.enable")
            connection.request("Runtime.enable")
            connection.request("Log.enable")
            if self.bypass_csp:
                try:
                    connection.request("Page.setBypassCSP", {"enabled": True})
                except InjectorError:
                    pass
            self._install_host_binding(connection)
            source = self._runtime_source()
            registration = connection.request(
                "Page.addScriptToEvaluateOnNewDocument",
                {"source": f"{source}\n//# sourceURL=codex-taskboard.user.js"},
            )
            registration_id = registration.get("identifier")
            if not isinstance(registration_id, str) or not registration_id:
                raise InjectorError("Codex renderer did not return an injection registration id")
            evaluation = connection.request("Runtime.evaluate", {"expression": source, "awaitPromise": True, "returnByValue": False})
            if evaluation.get("exceptionDetails"):
                raise InjectorError(_runtime_exception_message(evaluation, "Taskboard injection failed in Codex renderer"))
            with self._connections_lock:
                self._injection_registrations[target_id] = registration_id
            self._wait_for_injection_status(
                connection,
                source_hash=self._runtime_source_hash,
                frame_required=False,
            )
            # The first launch should present the panel so the user can see
            # that the injection succeeded.  Subsequent clicks still use the
            # cloned native sidebar entry and the page remains embedded.
            opened = connection.request(
                "Runtime.evaluate",
                {
                    "expression": "window.__codexTaskboardInjection__?.open?.()",
                    "awaitPromise": True,
                    "returnByValue": False,
                },
            )
            if opened.get("exceptionDetails"):
                raise InjectorError("Codex Taskboard entry was mounted but could not be opened")
            frame_status = self._wait_for_injection_status(
                connection,
                source_hash=self._runtime_source_hash,
                frame_required=True,
            )
            self._bring_to_front(connection)
            self._activate_codex_app()
        except (InjectorError, OSError) as exc:
            if isinstance(registration_id, str):
                self._remove_injection_registration(connection, registration_id)
                with self._connections_lock:
                    self._injection_registrations.pop(target_id, None)
            connection.close()
            raise InjectorError(f"Could not inject Codex target {target_id}: {exc}") from exc
        with self._connections_lock:
            self.connections[target_id] = connection
            self._injected_targets.add(target_id)
        self._emit("injected", targetId=target_id, port=self.port, frameState=frame_status.get("frameState"))

    def _find_managed_pid(self) -> int | None:
        port_marker = f"--remote-debugging-port={self.port}"
        for pid, command in self._process_table():
            if pid == os.getpid() or not self._codex_process_command(command) or port_marker not in command:
                continue
            return pid
        return None

    def _launch_codex(self) -> None:
        app = self.app_path or discover_codex_app()
        if app is None or not codex_executable_path(app).is_file():
            raise InjectorError("Could not find the installed Codex desktop app; set CODEX_TASKBOARD_CODEX_APP to its executable or app bundle")
        self.app_path = app
        profile = (self.profile_path or _default_profile_path()).expanduser().resolve()
        profile.mkdir(parents=True, exist_ok=True)
        try:
            import_codex_browser_profile(self.source_profile_path, profile)
        except Exception as exc:
            # A locked/encrypted source profile must not prevent a clean launch.
            # Never copy Local State encryption keys or change source permissions.
            self._emit("warning", phase="profile-import", message=str(exc))
        command = [
            "--remote-debugging-address=127.0.0.1",
            f"--remote-debugging-port={self.port}",
            f"--remote-allow-origins=http://127.0.0.1:{self.port}",
            f"--user-data-dir={profile}",
        ]
        try:
            desktop().launch(app, command, without_taskboard_launcher_environment())
        except (OSError, subprocess.CalledProcessError) as exc:
            raise InjectorError(f"Could not launch Codex: {exc}") from exc
        self.profile_path = profile
        self.managed_pid = self._find_managed_pid()
        self._emit(
            "codex_launched",
            appPath=str(app),
            pid=self.managed_pid,
            port=self.port,
            profile=str(profile),
        )

    def _wait_for_target(self, timeout: float = TARGET_WAIT_TIMEOUT) -> list[dict[str, Any]]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not self.stop_event.is_set():
            if self.managed_pid is None:
                self.managed_pid = self._find_managed_pid()
            targets = self._targets()
            if targets:
                return targets
            time.sleep(0.5)
        return []

    @staticmethod
    def _remove_injection_registration(connection: WebSocket, registration_id: str) -> None:
        """Remove a document-start registration before releasing a CDP session."""

        if not registration_id or connection._closed:
            return
        try:
            connection.request(
                "Page.removeScriptToEvaluateOnNewDocument",
                {"identifier": registration_id},
            )
        except (InjectorError, OSError) as exc:
            # A renderer that already disappeared takes its registration with
            # it.  Keep cleanup best-effort so one stale target cannot stop
            # the remaining Codex windows from being serviced.
            print(
                f"Codex Taskboard could not remove injection registration {registration_id}: {exc}",
                file=sys.stderr,
                flush=True,
            )

    def _close_connection(self, target_id: str) -> None:
        with self._connections_lock:
            connection = self.connections.pop(target_id, None)
            self._injected_targets.discard(target_id)
            registration_id = self._injection_registrations.pop(target_id, None)
        if connection:
            if registration_id:
                self._remove_injection_registration(connection, registration_id)
            connection.close()

    def run(self) -> int:
        targets = self._targets()
        if not targets and self.launch:
            ordinary = self._ordinary_codex_processes()
            if ordinary:
                chinese = os.environ.get("CODEX_TASKBOARD_LANGUAGE", "en") == "zh-CN"
                if not desktop().confirm_restart(self.app_path, chinese):
                    self._emit("stopped", message="Codex restart canceled. Taskboard was not loaded")
                    return 0
                for pid, _ in ordinary:
                    desktop().request_quit(self.app_path, pid)
                deadline = time.monotonic() + 36
                while any(desktop().process_running(pid) for pid, _ in ordinary):
                    if self.stop_event.wait(0.1):
                        return 0
                    if time.monotonic() >= deadline:
                        raise InjectorError("Codex has not exited. Taskboard was not started")
            if not self._is_port_available_for_launch():
                self.port = _find_free_port(DEFAULT_FALLBACK_CDP_PORT)
            self._launch_codex()
            targets = self._wait_for_target()
        if not targets:
            raise InjectorError("No Codex renderer found and no installed Codex app could be launched")
        while not self.stop_event.is_set():
            current = self._targets()
            current_ids = {str(item.get("id") or "") for item in current}
            with self._connections_lock:
                connected_ids = [
                    target_id
                    for target_id, connection in self.connections.items()
                    if target_id not in current_ids or connection._closed
                ]
                injected_ids = set(self._injected_targets)
            for target_id in connected_ids:
                self._close_connection(target_id)
            for target in current:
                target_id = str(target.get("id") or "")
                if target_id in injected_ids:
                    continue
                try:
                    self._inject_target(target)
                except InjectorError as exc:
                    self._emit("error", phase="inject", targetId=target_id, message=str(exc))
                    print(str(exc), file=sys.stderr, flush=True)
            if self.open_event.is_set():
                self.open_event.clear()
                self.open_connected_renderers()
            self.stop_event.wait(TARGET_POLL_SECONDS)
        self.close()
        return 0

    def _is_port_available_for_launch(self) -> bool:
        try:
            socket.create_connection(("127.0.0.1", self.port), timeout=0.2).close()
        except OSError:
            return True
        return False

    def close(self) -> None:
        with self._connections_lock:
            target_ids = list(self.connections)
        for target_id in target_ids:
            self._close_connection(target_id)
        # The menu-bar process owns only the injector and local backend.  Do
        # not close the user's Codex window when the launcher is restarted or
        # quit; a later launcher instance can attach to the same loopback CDP.
        self.managed_pid = None


def _default_profile_path() -> Path:
    configured = os.environ.get("CODEX_TASKBOARD_CODEX_PROFILE", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    data_dir = os.environ.get("CODEX_TASKBOARD_DATA_DIR", "").strip()
    return (Path(data_dir).expanduser().resolve() if data_dir else user_data_directory()) / "codex-profile"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=int(os.environ.get("CODEX_TASKBOARD_CDP_PORT", DEFAULT_CDP_PORT)))
    parser.add_argument(
        "--taskboard-url",
        default=os.environ.get("CODEX_TASKBOARD_URL", DEFAULT_TASKBOARD_URL),
        help="Loopback HTTP origin used by the injected iframe",
    )
    parser.add_argument(
        "--app-path",
        default=os.environ.get("CODEX_TASKBOARD_CODEX_APP") or None,
        help="Optional developer override; by default discover the installed Codex desktop app automatically",
    )
    parser.add_argument("--injection-file", type=Path, default=DEFAULT_INJECTION_FILE)
    parser.add_argument("--profile", type=Path, default=None)
    parser.add_argument("--no-launch", action="store_true", help="Attach only; do not launch a Codex window")
    parser.add_argument("--no-csp-bypass", action="store_true", help="Do not request renderer CSP bypass")
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_standard_streams()
    args = build_parser().parse_args(argv)
    if not 1 <= args.port <= 65535:
        print("--port must be between 1 and 65535", file=sys.stderr)
        return 2
    injector = CdpInjector(
        port=args.port,
        taskboard_url=args.taskboard_url,
        injection_path=args.injection_file,
        app_path=args.app_path,
        launch=not args.no_launch,
        bypass_csp=not args.no_csp_bypass,
        profile_path=args.profile or _default_profile_path(),
    )
    signal.signal(signal.SIGINT, injector.stop)
    signal.signal(signal.SIGTERM, injector.stop)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, injector.stop)
    try:
        return injector.run()
    except InjectorError as exc:
        injector._emit("error", phase="injector", message=str(exc))
        print(f"Codex Taskboard injector: {exc}", file=sys.stderr)
        injector.close()
        return 1
    except KeyboardInterrupt:
        injector.stop()
        injector.close()
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
