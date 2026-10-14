"""Retain an unreaped group owner until all launch descendants are cleaned up."""

from __future__ import annotations

import json
import os
import select
import signal
import subprocess
import sys
import tempfile


_OWNER = """
import json,os,signal,subprocess,sys
try:
    try:
        child = subprocess.Popen(
            json.loads(sys.argv[1]), stdin=subprocess.DEVNULL,
            stdout=int(sys.argv[2]), stderr=sys.stderr,
        )
    except OSError as exc:
        print(json.dumps({"error": str(exc)}), flush=True)
    else:
        print(json.dumps({"returncode": child.wait()}), flush=True)
    sys.stdin.buffer.read(1)
finally:
    os.killpg(os.getpgrp(), signal.SIGKILL)
"""


def run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
    timeout = kwargs.pop("timeout")
    kwargs.pop("check", None)
    kwargs.pop("stdin", None)
    kwargs.pop("stdout", None)
    text = kwargs.get("text", False)
    with tempfile.TemporaryFile() as output:
        owner = subprocess.Popen(
            [sys.executable, "-c", _OWNER, json.dumps(argv), str(output.fileno())],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            start_new_session=True, pass_fds=(output.fileno(),), **kwargs,
        )
        try:
            ready, _, _ = select.select([owner.stdout], [], [], timeout)
            if not ready:
                raise subprocess.TimeoutExpired(argv, timeout)
            try:
                receipt = json.loads(owner.stdout.readline())
            except ValueError as exc:
                raise OSError("launch group owner returned no valid completion receipt") from exc
            if isinstance(receipt, dict) and isinstance(receipt.get("error"), str):
                raise OSError(receipt["error"])
            if not isinstance(receipt, dict) or type(receipt.get("returncode")) is not int:
                raise OSError("launch group owner returned an invalid completion receipt")
            output.seek(0)
            stdout = output.read()
            if text:
                stdout = stdout.decode(kwargs.get("encoding") or "utf-8", errors="replace")
            return subprocess.CompletedProcess(argv, receipt["returncode"], stdout, None)
        finally:
            # No poll/wait reaps this owner before the group sweep, reserving its PID
            # even if the engine already exited or the owner itself became a zombie.
            try:
                os.killpg(owner.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            owner.communicate(timeout=5)
