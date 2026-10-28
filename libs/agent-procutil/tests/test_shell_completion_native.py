"""POSIX exit status alone must never attest callback completion."""

from __future__ import annotations

import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys

import pytest


pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="actual Linux caller-owned group",
)
SOURCE = Path(__file__).resolve().parents[1] / "src"
NONCE = "a" * 32
BODY = """
import os, sys
sys.path.insert(0, sys.argv[1])
from agent_procutil._shell_caller_owned import serve
def callback() -> int:
    print("partial output", flush=True)
    if sys.argv[2] == "abrupt":
        os._exit(int(sys.argv[3]))
    return int(sys.argv[3])
serve(callback, owner_pid=os.getppid(), owner_birth=None, nonce="a" * 32)
"""


@pytest.mark.parametrize("code", [0, 7])
@pytest.mark.parametrize("mode", ["returned", "abrupt"])
def test_exit_status_requires_a_separate_callback_return(mode: str, code: int) -> None:
    process = subprocess.Popen(
        [sys.executable, "-I", "-S", "-B", "-c", BODY, str(SOURCE), mode, str(code)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8",
    )
    try:
        assert select.select([process.stdout], [], [], 5)[0], "no lifetime receipt"
        receipt = json.loads(process.stdout.readline())
        assert receipt["scope"] == "posix-group" and receipt["owner_pid"] == os.getpid()
        process.stdin.write(NONCE + "\n")
        process.stdin.flush()
        process.wait(timeout=5)
        stdout, stderr = process.communicate(timeout=2)
        assert process.returncode == -signal.SIGKILL
        assert stdout.startswith("partial output\n")
        if mode == "returned":
            assert stdout == f"partial output\n\n__COPILOT_CALLER_EXIT_{NONCE}={code}\n"
            assert not stderr
        else:
            assert "__COPILOT_CALLER_EXIT_" not in stdout
            assert "callback completion is unconfirmed" in stderr
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)
        process.stdin.close()
        process.stdout.close()
        process.stderr.close()
