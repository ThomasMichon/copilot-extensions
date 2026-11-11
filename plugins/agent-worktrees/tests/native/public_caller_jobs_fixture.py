"""Explicit installed-caller lifetime controls, outside ordinary test containment."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import agent_procutil as pu
from agent_remote_driver.session_host.osutil import pid_alive, process_start_time


def _publish(path, value):
    pending = path.with_suffix(".pending")
    pending.write_text(json.dumps(value), encoding="utf-8")
    pending.replace(path)


def _leaf(directory):
    request = json.loads((directory / "request.json").read_text(encoding="utf-8"))
    control = subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", "import time;time.sleep(100)"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        **pu.no_window_kwargs(),
    )
    identity = {"pid": control.pid, "birth": process_start_time(control.pid)}
    assert identity["birth"]
    _publish(directory / "control.json", identity)
    result = subprocess.run(
        request["command"], cwd=request["cwd"], stdin=subprocess.DEVNULL,
        capture_output=True, text=True, encoding="utf-8", timeout=30,
        **pu.no_window_kwargs(),
    )
    _publish(directory / "reply.json", {
        "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr,
    })


def _middle(directory):
    with (directory / "leaf.log").open("wb") as log:
        process, inner = pu.spawn_sync_in_kill_on_close_job(
            [sys.executable, "-I", "-B", __file__, "leaf", str(directory)],
            cwd=directory, env=dict(os.environ), require_job=True, allow_breakaway=True,
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            **pu.no_window_kwargs(),
        )
    try:
        assert inner is not None
        assert process.wait(40) == 0, (directory / "leaf.log").read_text(errors="replace")
    finally:
        try:
            if process.poll() is None:
                process.kill()
                process.wait(5)
        finally:
            if inner is not None:
                inner.close()
    _publish(directory / "middle.json", {"innerJobClosed": True})


def run_public_caller_jobs(command, *, python, cwd, environment, directory):
    """Return only after both caller Jobs close and their bounded control exits."""
    assert os.name == "nt" and not pu.contained_test_mode()
    assert environment.get("COPILOT_EXTENSIONS_TEST_CONTAINED") != "1"
    sandbox = Path(os.environ["COPILOT_EXTENSIONS_TEST_SANDBOX"]).resolve()
    directory = Path(directory).resolve()
    assert directory.is_relative_to(sandbox)
    assert Path(python).resolve().is_relative_to(sandbox)
    directory.mkdir()
    _publish(directory / "request.json", {"command": list(map(str, command)), "cwd": str(cwd)})
    with (directory / "middle.log").open("wb") as log:
        process, outer = pu.spawn_sync_in_kill_on_close_job(
            [str(python), "-I", "-B", __file__, "middle", str(directory)],
            cwd=cwd, env=environment, require_job=True, allow_breakaway=True,
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            **pu.no_window_kwargs(),
        )
    try:
        assert outer is not None
        assert process.wait(50) == 0, (directory / "middle.log").read_text(errors="replace")
    finally:
        try:
            if process.poll() is None:
                process.kill()
                process.wait(5)
        finally:
            if outer is not None:
                outer.close()
    assert json.loads((directory / "middle.json").read_text())["innerJobClosed"]
    control = json.loads((directory / "control.json").read_text())
    deadline = time.monotonic() + 5
    while (
        pid_alive(control["pid"]) and process_start_time(control["pid"]) == control["birth"]
        and time.monotonic() < deadline
    ):
        time.sleep(.025)
    assert not (
        pid_alive(control["pid"]) and process_start_time(control["pid"]) == control["birth"]
    ), control
    _publish(directory / "jobs-closed.json", {
        "innerJobClosed": True, "outerJobClosed": True, "boundedControlExited": True,
    })
    reply = json.loads((directory / "reply.json").read_text(encoding="utf-8"))
    return subprocess.CompletedProcess(command, **reply)


@contextmanager
def application_unavailable(python):
    """Temporarily withdraw only the fixture's selected ordinary application."""
    sandbox = Path(os.environ["COPILOT_EXTENSIONS_TEST_SANDBOX"]).resolve()
    environment = Path(python).resolve().parent.parent
    application = environment.parent
    assert environment.name == ".venv" and application.is_relative_to(sandbox)
    assert (application / "application.json").is_file()
    moved = application.with_name(application.name + "-temporarily-unavailable")
    assert not moved.exists()
    application.rename(moved)
    try:
        assert not application.exists()
        yield
    finally:
        moved.rename(application)
    assert application.is_dir() and not moved.exists()


if __name__ == "__main__":
    operation, selected = sys.argv[1:]
    {"middle": _middle, "leaf": _leaf}[operation](Path(selected))
