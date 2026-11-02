"""POSIX exit status alone must never attest callback completion."""

from __future__ import annotations

import base64
import json
import os
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

import caller_owned_fixture as fixture
import pytest
from test_caller_owned_native import PinnedProcess, _linux_observer

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


def test_request_bootstrap_does_not_buffer_the_following_offer_permission(tmp_path: Path) -> None:
    sdk = Path(__file__).resolve().parents[3] / "libs/installation-context"
    shell = (sdk / "shell-caller-owned.ps1").read_text(encoding="utf-8")
    bootstrap = shell.split("    $bootstrap = @'\n", 1)[1].split("\n'@", 1)[0]
    (tmp_path / "_installation_context_worker.py").write_text(
        "import os\n"
        "def main(request):\n"
        "    assert os.read(0, 100) == b'offer:coalesced-permission\\n'\n"
        "    return 0\n",
        encoding="utf-8",
    )
    request = base64.b64encode(json.dumps({
        "worker": str(tmp_path / "_installation_context_worker.py"), "request": {},
    }).encode()) + b"\noffer:coalesced-permission\n"
    process = subprocess.Popen(
        [sys.executable, "-I", "-S", "-B", "-c", bootstrap],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        stdout, stderr = process.communicate(request, timeout=5)
        assert process.returncode == 0, (stdout, stderr)
        assert not stdout and not stderr
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()


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


def test_original_lifetime_pipe_retires_group_after_leader_death() -> None:
    body = """
import json, os, subprocess, sys, time
sys.path.insert(0, sys.argv[1])
from agent_procutil._shell_caller_owned import serve
def callback() -> int:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    print(json.dumps({"callback": os.getpid(), "descendant": child.pid}), flush=True)
    time.sleep(60)
    return 0
serve(callback, owner_pid=os.getppid(), owner_birth=None, nonce="a" * 32)
"""
    with _linux_observer():
        process = subprocess.Popen(
            [sys.executable, "-I", "-S", "-B", "-c", body, str(SOURCE)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8",
        )
        pins: list[PinnedProcess] = []
        try:
            assert select.select([process.stdout], [], [], 5)[0], "no lifetime receipt"
            receipt = json.loads(process.stdout.readline())
            assert receipt["worker_pid"] == process.pid
            pins.append(PinnedProcess(fixture.identity(process.pid)))
            process.stdin.write(NONCE + "\n")
            process.stdin.flush()
            assert select.select([process.stdout], [], [], 5)[0], "callback did not start"
            identities = json.loads(process.stdout.readline())
            for pid in identities.values():
                pins.append(PinnedProcess(fixture.identity(pid)))
            pins[0].kill()
            process.wait(timeout=5)
            assert not any(pin.gone() for pin in pins[1:])
            process.stdin.close()
            process.stdin = None
            deadline = time.monotonic() + 5
            while not all(pin.gone() for pin in pins) and time.monotonic() < deadline:
                time.sleep(0.01)
            assert all(pin.gone() for pin in pins), "original pipe EOF did not settle the group"
            stdout, stderr = process.communicate(timeout=2)
            assert not stdout and not stderr
        finally:
            for pin in pins:
                pin.kill()
                pin.close()
            if process.poll() is None:
                process.kill()
                process.wait(timeout=2)
            if process.stdin:
                process.stdin.close()
            process.stdout.close()
            process.stderr.close()
