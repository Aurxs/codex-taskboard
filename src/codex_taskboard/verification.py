"""Programmatic verification of the exact candidate commit before publication."""
from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import time

from .errors import ValidationError
from .platforms import hidden_process_options, executable_command


def validate_commands(value: list) -> list[list[str]]:
    if not isinstance(value, list) or len(value) > 20:
        raise ValidationError("Verification commands must be a list (at most 20 commands)")
    for command in value:
        if not isinstance(command, list) or not command or not all(isinstance(arg, str) and arg and "\0" not in arg for arg in command):
            raise ValidationError("Each verification command must be a nonempty argv array")
    return value


def run_commands(root: str, commands: list[list[str]], *, timeout: float = 300) -> list[dict]:
    """No implicit shell. Keep bounded log tails and terminate timed-out processes."""
    records = []
    for command in validate_commands(commands):
        started = time.time()
        with tempfile.TemporaryFile() as output:
            try:
                process = subprocess.Popen(executable_command(command), cwd=root, stdin=subprocess.DEVNULL,
                    stdout=output, stderr=subprocess.STDOUT,
                    start_new_session=os.name != "nt", **hidden_process_options())
                try:
                    code = process.wait(timeout=timeout)
                    timed_out = False
                except subprocess.TimeoutExpired:
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                       capture_output=True, **hidden_process_options())
                    else:
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                    code, timed_out = process.returncode, True
                size = output.seek(0, os.SEEK_END)
                output.seek(max(0, size - 65536))
                log = output.read().decode(errors="replace")
                record = {"command": command, "exitCode": code, "log": log,
                          "logTruncated": size > 65536, "timedOut": timed_out}
            except OSError as exc:
                record = {"command": command, "exitCode": None, "log": str(exc), "timedOut": False}
        records.append({**record, "startedAt": started, "durationSeconds": time.time() - started})
        if record["exitCode"] != 0 or record["timedOut"]:
            break
    return records
