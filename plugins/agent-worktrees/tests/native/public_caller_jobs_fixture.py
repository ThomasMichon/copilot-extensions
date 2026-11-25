"""Explicit installed-caller lifetime controls, outside ordinary test containment."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any

import agent_procutil as pu
from agent_procutil.owned_process import run_installation_owned
from agent_remote_driver.session_host.osutil import pid_alive, process_start_time


def _publish(path: Path, value: object) -> None:
    pending = path.with_suffix(".pending")
    pending.write_text(json.dumps(value), encoding="utf-8")
    pending.replace(path)


def _remaining(
    request: Mapping[str, Any], fallback: float, *, cleanup: float = 0,
) -> float:
    deadline = request.get("deadline")
    if deadline is None:
        return fallback
    if type(deadline) not in (int, float) or not math.isfinite(deadline):
        raise ValueError("caller fixture deadline must be finite")
    remaining = float(deadline) + cleanup - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("caller fixture deadline expired")
    return remaining


def _leaf(directory: Path) -> None:
    request = json.loads((directory / "request.json").read_text(encoding="utf-8"))
    remaining = _remaining(request, 30)
    control = subprocess.Popen(
        [sys.executable, "-I", "-B", "-c",
         f"import time;time.sleep({max(100, remaining + 30)!r})"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        **pu.no_window_kwargs(),
    )
    identity = {"pid": control.pid, "birth": process_start_time(control.pid)}
    assert identity["birth"]
    _publish(directory / "control.json", identity)
    result = run_installation_owned(
        request["command"], cwd=request["cwd"], timeout=_remaining(request, 30),
        max_stdout_bytes=128 * 1024, max_stderr_bytes=128 * 1024,
    )
    _publish(directory / "reply.json", {
        "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr,
    })


def _middle(directory: Path) -> None:
    request = json.loads((directory / "request.json").read_text(encoding="utf-8"))
    _remaining(request, 40)
    with (directory / "leaf.log").open("wb") as log:
        process, inner = pu.spawn_sync_in_kill_on_close_job(
            [sys.executable, "-I", "-B", __file__, "leaf", str(directory)],
            cwd=directory, env=dict(os.environ), require_job=True, allow_breakaway=True,
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            **pu.no_window_kwargs(),
        )
    try:
        assert inner is not None
        assert process.wait(_remaining(request, 40, cleanup=10)) == 0, (
            directory / "leaf.log"
        ).read_text(errors="replace")
    finally:
        try:
            if process.poll() is None:
                process.kill()
                process.wait(5)
        finally:
            if inner is not None:
                inner.close()
    _publish(directory / "middle.json", {"innerJobClosed": True})


def run_public_caller_jobs(
    command: Sequence[str | os.PathLike[str]], *, python: Path, cwd: Path,
    environment: dict[str, str], directory: Path, deadline: float | None = None,
) -> subprocess.CompletedProcess[str]:
    """Return only after both caller Jobs close and their bounded control exits."""
    assert os.name == "nt" and not pu.contained_test_mode()
    assert environment.get("COPILOT_EXTENSIONS_TEST_CONTAINED") != "1"
    sandbox = Path(os.environ["COPILOT_EXTENSIONS_TEST_SANDBOX"]).resolve()
    directory = Path(directory).resolve()
    assert directory.is_relative_to(sandbox)
    assert Path(python).resolve().is_relative_to(sandbox)
    request: dict[str, object] = {"command": list(map(str, command)), "cwd": str(cwd)}
    if deadline is not None:
        request["deadline"] = deadline
    _remaining(request, 50)
    directory.mkdir()
    _publish(directory / "request.json", request)
    with (directory / "middle.log").open("wb") as log:
        process, outer = pu.spawn_sync_in_kill_on_close_job(
            [str(python), "-I", "-B", __file__, "middle", str(directory)],
            cwd=cwd, env=environment, require_job=True, allow_breakaway=True,
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            **pu.no_window_kwargs(),
        )
    try:
        assert outer is not None
        assert process.wait(_remaining(request, 50, cleanup=20)) == 0, (
            directory / "middle.log"
        ).read_text(errors="replace")
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
    control_deadline = time.monotonic() + 5
    while (
        pid_alive(control["pid"]) and process_start_time(control["pid"]) == control["birth"]
        and time.monotonic() < control_deadline
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


def _require_native_tty_owner(directory: Path, environment: Mapping[str, str]) -> Path:
    from agent_procutil.daemon_instances import TEST_CONTEXT_ENV

    assert not pu.contained_test_mode()
    assert os.name == "nt" and environment.get(TEST_CONTEXT_ENV)
    assert environment.get("COPILOT_EXTENSIONS_TEST_CONTAINED") != "1"
    sandbox = Path(environment["COPILOT_EXTENSIONS_TEST_SANDBOX"]).resolve()
    assert directory.resolve().is_relative_to(sandbox)
    return sandbox


def prepare_public_tty_jobs(
    command: Sequence[str | os.PathLike[str]], *, python: Path, cwd: Path,
    environment: dict[str, str], directory: Path,
) -> list[str]:
    """Prepare a unique caller ticket; its foreground process inherits the real ConPTY."""
    directory = Path(directory)
    sandbox = _require_native_tty_owner(directory, environment)
    assert Path(python).resolve().is_relative_to(sandbox)
    assert Path(command[0]).resolve().is_relative_to(sandbox)
    assert Path(cwd).resolve().is_relative_to(sandbox)
    directory.mkdir()
    _publish(directory / "request.json", {"command": list(map(str, command)), "cwd": str(cwd)})
    return [str(python), "-I", "-B", __file__, "tty-middle", str(directory)]


def _tty_leaf(directory: Path) -> None:
    _require_native_tty_owner(directory, os.environ)
    assert sys.stdin.isatty() and sys.stdout.isatty()
    request = json.loads((directory / "request.json").read_text(encoding="utf-8"))
    control = subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", "import time;time.sleep(180)"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        **pu.no_window_kwargs(),
    )
    identity = {"pid": control.pid, "birth": process_start_time(control.pid)}
    assert identity["birth"]
    _publish(directory / "control.json", identity)
    # headless-guard: allow the installed public caller to inherit this private ConPTY
    process, inner = pu.spawn_sync_in_kill_on_close_job(
        request["command"], cwd=request["cwd"], env=dict(os.environ),
        require_job=True, allow_breakaway=True,
    )
    try:
        assert inner is not None
        caller = {"pid": process.pid, "birth": process_start_time(process.pid)}
        assert caller["birth"]
        _publish(directory / "started.json", {"caller": caller, "leafTty": True})
        deadline = time.monotonic() + 180
        while not (directory / "close.json").exists():
            assert process.poll() is None, "Public TTY caller returned before Job closure"
            assert time.monotonic() < deadline, "Public TTY caller close request timed out"
            time.sleep(.025)
        assert process.poll() is None, "Public TTY caller exited before Job closure"
    finally:
        if inner is not None:
            inner.close()
        else:
            process.kill()
        exit_code = process.wait(5)
    _publish(directory / "inner-closed.json", {
        "innerJobClosed": True, "callerExited": True, "callerExitCode": exit_code,
        "caller": caller, "leafTty": True, "callerAliveBeforeClose": True,
    })


def _tty_middle(directory: Path) -> None:
    _require_native_tty_owner(directory, os.environ)
    assert sys.stdin.isatty() and sys.stdout.isatty()
    # headless-guard: allow the nested caller fixture to inherit this private ConPTY
    process, outer = pu.spawn_sync_in_kill_on_close_job(
        [sys.executable, "-I", "-B", __file__, "tty-leaf", str(directory)],
        cwd=directory, env=dict(os.environ), require_job=True, allow_breakaway=True,
    )
    try:
        assert outer is not None
        assert process.wait(180) == 0, "Public TTY caller leaf failed"
    finally:
        if outer is not None:
            outer.close()
        else:
            process.kill()
        process.wait(5)
    control = json.loads((directory / "control.json").read_text(encoding="utf-8"))
    deadline = time.monotonic() + 5
    while (
        pid_alive(control["pid"]) and process_start_time(control["pid"]) == control["birth"]
        and time.monotonic() < deadline
    ):
        time.sleep(.025)
    assert not (
        pid_alive(control["pid"]) and process_start_time(control["pid"]) == control["birth"]
    ), control
    reply = json.loads((directory / "inner-closed.json").read_text(encoding="utf-8"))
    _publish(directory / "jobs-closed.json", {
        **reply, "outerJobClosed": True, "boundedControlExited": True, "middleTty": True,
        "control": control,
    })


def close_public_tty_jobs(directory: Path) -> dict[str, Any]:
    """Request abrupt loss of this ticket's attached caller, then retain actual cleanup."""
    _require_native_tty_owner(directory, os.environ)
    _publish(directory / "close.json", {"close": True})
    deadline = time.monotonic() + 15
    proof = directory / "jobs-closed.json"
    while not proof.exists():
        assert time.monotonic() < deadline, "Public TTY caller Jobs did not settle"
        time.sleep(.025)
    reply: dict[str, Any] = json.loads(proof.read_text(encoding="utf-8"))
    started = json.loads((directory / "started.json").read_text(encoding="utf-8"))
    assert started["leafTty"] is True and started["caller"] == reply["caller"], reply
    assert all(reply.get(key) is True for key in (
        "innerJobClosed", "outerJobClosed", "callerExited", "boundedControlExited",
        "middleTty", "leafTty", "callerAliveBeforeClose",
    )), reply
    assert type(reply["callerExitCode"]) is int, reply
    for key in ("caller", "control"):
        identity = reply[key]
        assert identity["birth"] and not (
            pid_alive(identity["pid"]) and process_start_time(identity["pid"]) == identity["birth"]
        ), identity
    return reply


def replace_application(
    python: Path, incoming: Path, *, shell: str, cwd: Path,
    environment: dict[str, str], expected_generation: int,
) -> Path:
    """Use regular installation and canonical GC, never overwrite a built venv."""
    sandbox = Path(os.environ["COPILOT_EXTENSIONS_TEST_SANDBOX"]).resolve()
    outgoing = Path(python).resolve().parent.parent.parent
    incoming = Path(incoming).resolve()
    assert outgoing.is_relative_to(sandbox) and incoming.is_relative_to(sandbox)
    assert outgoing.parent.name == "versions" and incoming.parent == outgoing.parent
    assert incoming != outgoing and (incoming / "application.json").is_file()
    assert not (incoming / ".venv").exists()
    root = outgoing.parent.parent
    assert (root / "current-version").read_text().strip() == outgoing.name
    projects = (root / "projects.yaml").read_bytes()

    def run(
        command: Sequence[str | os.PathLike[str]], timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        reply = subprocess.run(
            list(map(str, command)), cwd=cwd, env=environment,
            capture_output=True, text=True, encoding="utf-8", timeout=timeout,
            **pu.no_window_kwargs(),
        )
        assert reply.returncode == 0, reply.stdout + reply.stderr
        return reply

    installed = run([
        shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", incoming / "install.ps1",
    ], 60)
    assert json.loads(installed.stdout.splitlines()[-1])["state"] == "installed"
    for selector in ("current-version", "last-known-good"):
        assert (root / selector).read_text().strip() == incoming.name
    assert json.loads((incoming / "install-success.json").read_text()) == {
        "state": "installed", "version": incoming.name,
    }
    assert (root / "projects.yaml").read_bytes() == projects
    selected_python = incoming / ".venv" / "Scripts" / "python.exe"
    assert selected_python.is_file()
    checked = run([
        selected_python, "-I", "-B", "-c",
        "import json,sys;import agent_worktrees as app;"
        "print(json.dumps({'generation':getattr(app,'_fixture_application_generation',None),"
        "'prefix':sys.prefix,'module':app.__file__}))",
    ], 15)
    facts = json.loads(checked.stdout)
    assert facts["generation"] == expected_generation, facts
    assert Path(facts["prefix"]).resolve() == (incoming / ".venv").resolve(), facts
    assert Path(facts["module"]).resolve().is_relative_to((incoming / ".venv").resolve()), facts
    collected = run([
        selected_python, "-I", "-B", incoming / "runtime" / "versioned_runtime.py",
        "--root", root, "--json", "gc", "--protect-pids", "--min-age-days", "0",
    ], 30)
    assert json.loads(collected.stdout) == {"removed": [outgoing.name]}, collected.stdout
    assert not outgoing.exists() and incoming.is_dir()
    return selected_python


def restore_application(
    python: Path, application: Path, *, shell: str, cwd: Path,
    environment: dict[str, str],
) -> Path:
    """Reconstruct the collected original slot from its unbuilt canonical artifact."""
    sandbox = Path(os.environ["COPILOT_EXTENSIONS_TEST_SANDBOX"]).resolve()
    outgoing = Path(python).resolve().parent.parent.parent
    application = Path(application).resolve()
    assert outgoing.is_relative_to(sandbox) and application.is_relative_to(sandbox)
    assert outgoing.parent.name == "versions" and not (application / ".venv").exists()
    version = json.loads((application / "application.json").read_text())["construction"]["runtime_version"]
    incoming = outgoing.parent / version
    assert incoming.parent == outgoing.parent and incoming.name == version
    assert incoming != outgoing and not incoming.exists()
    shutil.copytree(application, incoming)
    return replace_application(
        python, incoming, shell=shell, cwd=cwd, environment=environment, expected_generation=1,
    )


@contextmanager
def application_unavailable(python: Path) -> Iterator[None]:
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
    {"middle": _middle, "leaf": _leaf, "tty-middle": _tty_middle, "tty-leaf": _tty_leaf}[
        operation
    ](Path(selected))
