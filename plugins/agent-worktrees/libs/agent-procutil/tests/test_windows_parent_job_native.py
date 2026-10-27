"""Real kernel owner-loss controls; no observer cleanup may establish a pass."""

from __future__ import annotations

from contextlib import ExitStack
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

import pytest

from agent_procutil import no_window_kwargs
from test_caller_owned_native import PinnedProcess


pytestmark = pytest.mark.skipif(os.name != "nt", reason="native Windows parent-owned Job")
FIXTURE = Path(__file__).with_name("parent_job_fixture.py")
SOURCE = FIXTURE.parents[1] / "src"
BOOTSTRAP = (
    "import sys;sys.path.insert(0,sys.argv.pop(1));"
    "import runpy;runpy.run_path(sys.argv.pop(1),run_name='__main__')"
)


def pin_process(identity: dict[str, int | str]) -> PinnedProcess:
    birth = identity["birth"]
    match = re.fullmatch(r"windows:([1-9][0-9]{0,19})", birth) if isinstance(birth, str) else None
    if match is None or int(match[1]) >= 1 << 64:
        raise ValueError("fixture requires an exact Windows FILETIME birth")
    # The existing observer compares raw FILETIME integers, not lifecycle wire identities.
    return PinnedProcess({"pid": identity["pid"], "birth": int(match[1])})


def command(mode: str, report: Path) -> list[str]:
    return [
        getattr(sys, "_base_executable", sys.executable),
        "-I", "-S", "-B", "-c", BOOTSTRAP,
        str(SOURCE), str(FIXTURE), mode, str(report),
    ]


@pytest.mark.parametrize("mode", ["exit", "close"])
@pytest.mark.parametrize("cycle", range(2))
def test_parent_loss_kills_helper_and_atomic_job_child_before_test_cleanup(
    tmp_path: Path, mode: str, cycle: int,
) -> None:
    report = tmp_path / "receipt.json"
    parent = subprocess.Popen(
        command(mode, report), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, **no_window_kwargs(),
    )
    try:
        deadline = time.monotonic() + 10
        while not report.exists():
            assert parent.poll() is None, parent.communicate(timeout=3)
            assert time.monotonic() < deadline, "parent Job fixture startup timed out"
            time.sleep(0.01)
        receipt = json.loads(report.read_text(encoding="utf-8"))
        transfer = receipt["transfer"]
        assert transfer["parent_pid"] == parent.pid
        with ExitStack() as cleanup:
            pins = []
            for identity in (
                {"pid": transfer["worker_pid"], "birth": transfer["worker_birth"]},
                receipt["target"],
                receipt["descendant"],
            ):
                pin = pin_process(identity)
                cleanup.callback(pin.close)
                cleanup.callback(pin.kill)
                pins.append(pin)
            assert all(not pin.gone() for pin in pins)
            stdout, stderr = parent.communicate("retire\n", timeout=5)
            assert parent.returncode == 0, stdout + stderr
            assert stdout == "ready\n"
            deadline = time.monotonic() + 3
            while not all(pin.gone() for pin in pins):
                assert time.monotonic() < deadline, "kernel did not settle the parent-owned scope"
                time.sleep(0.01)
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(3)
        for stream in (parent.stdin, parent.stdout, parent.stderr):
            stream.close()


def test_replaced_parent_refuses_before_application_spawn(tmp_path: Path) -> None:
    report = tmp_path / "refused.txt"
    parent = subprocess.run(
        command("refuse", report), capture_output=True, text=True, timeout=12,
        **no_window_kwargs(),
    )
    assert parent.returncode == 0, parent.stdout + parent.stderr
    assert report.read_text(encoding="ascii") == "refused"
