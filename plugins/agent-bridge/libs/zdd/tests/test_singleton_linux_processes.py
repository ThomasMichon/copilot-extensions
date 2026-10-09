"""Real kernel ownership tests run in dedicated, bounded manager subprocesses."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# These four tests drive real fork/exec/signal process trees (repeated
# detached cutovers, a real same-process exec, and a pidfd above
# FD_SETSIZE) rather than mocked kernel state like the other singleton
# suites in this package. Set ZDD_SINGLETON_SMOKE_ONLY=1 to skip all four,
# pairing a real-process-free smoke run with a separate full-suite run
# (mirroring the crash-diagnostics-stress.yml split for real-process
# regressions not safe to gate every PR run on). libs/zdd/tests is not
# currently collected by any CI step (see TESTING.md); CI wiring that uses
# this env var is tracked separately from this source comment.
pytestmark = [
    pytest.mark.skipif(sys.platform != "linux", reason="Linux pidfd/subreaper contract"),
    pytest.mark.skipif(
        os.environ.get("ZDD_SINGLETON_SMOKE_ONLY") == "1",
        reason="real fork/exec/signal process-tree regression; unset to run",
    ),
]

_CUTOVER_DRIVER = r'''
import json, os, subprocess, sys, time
from pathlib import Path
from zdd import routing
from zdd.diagnostics import process_start_time
from zdd.singleton_manager import SingletonManager
from zdd.singleton_state import StateStore
root = Path(sys.argv[1])
route, state = root / "route", root / "state"
worker = root / "worker.py"
worker.write_text(r"""
import json, os, subprocess, sys, time
from pathlib import Path
from zdd import routing
from zdd.diagnostics import process_start_time
root = Path(sys.argv[1])
remaining = int(sys.argv[2])
with root.joinpath("pids").open("a") as out:
    out.write(json.dumps([os.getpid(), process_start_time(os.getpid())]) + "\n")
routing.publish_active(root / "route", bind="127.0.0.1", port=1234, pid=os.getpid())
if remaining:
    if remaining == 4:
        stray = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"],
                                 start_new_session=True)
        with root.joinpath("pids").open("a") as out:
            out.write(json.dumps([stray.pid, process_start_time(stray.pid)]) + "\n")
    command = [sys.executable, str(root / "worker.py"), str(root), str(remaining - 1)]
    subprocess.Popen([sys.executable, "-c",
        "import subprocess,sys;subprocess.Popen(sys.argv[1:],start_new_session=True)",
        *command], start_new_session=True)
    end = time.monotonic() + 5
    while time.monotonic() < end:
        table = routing.read_table(root / "route") or {}
        if table.get("active", {}).get("pid") != os.getpid():
            break
        time.sleep(.01)
else:
    root.joinpath("final").write_text(str(os.getpid()))
    end = time.monotonic() + 5
    while not root.joinpath("release").exists() and time.monotonic() < end:
        time.sleep(.01)
""")
spawns = []
def spawn():
    child = subprocess.Popen([sys.executable, str(worker), str(root), "4"])
    spawns.append(child.pid)
    return child
def observe():
    final = root / "final"
    saved = StateStore(state).read()
    if final.exists() and saved and saved.watched.pid == int(final.read_text()):
        root.joinpath("release").touch()
    return None
result = SingletonManager(route, spawn, manager_state_dir=state,
                          successor_wait_s=.2, poll_interval=.01,
                          resolve_update=observe).run()
survivors = [pid for pid, token in
             map(json.loads, root.joinpath("pids").read_text().splitlines())
             if token is not None and process_start_time(pid) == token]
print(json.dumps({"spawns": len(spawns), "watched": result.last_watched_pid,
                  "final": int(root.joinpath("final").read_text()),
                  "survivors": survivors}))
'''

_EXEC_DRIVER = r'''
import json, os, subprocess, sys
from pathlib import Path
from zdd.singleton_manager import SingletonManager
root, phase = Path(sys.argv[1]), sys.argv[2]
with root.joinpath("managers").open("a") as out:
    out.write(str(os.getpid()) + "\n")
def spawn():
    child = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(.5)"])
    with root.joinpath("spawns").open("a") as out:
        out.write(str(child.pid) + "\n")
    return child
def update():
    if phase == "before":
        return [sys.executable, __file__, str(root), "after"]
    return None
result = SingletonManager(root / "route", spawn, manager_state_dir=root / "state",
                          resolve_update=update, successor_wait_s=.05,
                          poll_interval=.01).run()
print(json.dumps({"code": result.exit_code,
                  "managers": root.joinpath("managers").read_text().splitlines(),
                  "spawns": root.joinpath("spawns").read_text().splitlines()}))
'''

_DISCOVERY_EXEC_DRIVER = r'''
import json, os, subprocess, sys
from pathlib import Path
from zdd.singleton_manager import SingletonManager
from zdd.singleton_state import StateStore
root, phase = Path(sys.argv[1]), sys.argv[2]
worker = root / "pending_worker.py"
if phase == "before":
    worker.write_text(r"""
import os, subprocess, sys, time
from pathlib import Path
from zdd import routing
root, role = Path(sys.argv[1]), sys.argv[2]
if role == "starter":
    subprocess.Popen([sys.executable, __file__, str(root), "successor"],
                     start_new_session=True)
else:
    end = time.monotonic() + 5
    while not root.joinpath("after_exec").exists() and time.monotonic() < end:
        time.sleep(.01)
    routing.publish_active(root / "route", bind="127.0.0.1", port=1234, pid=os.getpid())
    root.joinpath("final").write_text(str(os.getpid()))
    while not root.joinpath("release").exists() and time.monotonic() < end:
        time.sleep(.01)
""")
with root.joinpath("managers").open("a") as out:
    out.write(str(os.getpid()) + "\n")
if phase == "after":
    root.joinpath("after_exec").touch()
def spawn():
    with root.joinpath("spawns").open("a") as out:
        out.write("spawn\n")
    return subprocess.Popen([sys.executable, str(worker), str(root), "starter"])
def update():
    saved = StateStore(root / "state").read()
    if phase == "before" and saved and saved.phase == "discovering":
        return [sys.executable, __file__, str(root), "after"]
    final = root / "final"
    if phase == "after" and final.exists() and saved and saved.watched.pid == int(final.read_text()):
        root.joinpath("release").touch()
    return None
result = SingletonManager(root / "route", spawn, manager_state_dir=root / "state",
                          resolve_update=update, successor_wait_s=3,
                          poll_interval=.01).run()
print(json.dumps({"watched": result.last_watched_pid, "final": int(root.joinpath("final").read_text()),
                  "managers": root.joinpath("managers").read_text().splitlines(),
                  "spawns": root.joinpath("spawns").read_text().splitlines()}))
'''

_HIGH_FD_DRIVER = r'''
import fcntl, json, os, resource, subprocess, sys, time
from zdd.singleton_linux import LinuxBackend, ProcessReference
if resource.getrlimit(resource.RLIMIT_NOFILE)[0] <= 1024:
    print(json.dumps({"unsupported_limit": True}))
    raise SystemExit(0)
backend = LinuxBackend()
child = subprocess.Popen([sys.executable, "-c", "import sys;sys.stdin.buffer.read(1)"],
                         stdin=subprocess.PIPE)
identity = backend.identify(child.pid)
reference = backend.open_process(identity)
if reference is None:
    raise RuntimeError("test child exited before pidfd capture")
high = None
try:
    high = ProcessReference(identity, fcntl.fcntl(reference.fd, fcntl.F_DUPFD_CLOEXEC, 1024))
    initial = high.alive()
    child.stdin.close()
    child.wait(timeout=3)
    terminal = high.alive()
    print(json.dumps({"fd": high.fd, "initial": initial, "terminal": terminal}))
finally:
    reference.close()
    if high is not None:
        high.close()
    child.stdin.close()
    child.wait(timeout=3)
'''


def _run_driver(script: str, tmp_path: Path, *args: str) -> dict:
    driver = tmp_path / "manager_driver.py"
    driver.write_text(script, encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, str(driver), str(tmp_path), *args],
        capture_output=True, text=True, timeout=15, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_repeated_detached_cutovers_and_orphan_cleanup(tmp_path: Path) -> None:
    result = _run_driver(_CUTOVER_DRIVER, tmp_path)
    assert result["spawns"] == 1
    assert result["watched"] == result["final"]
    assert result["survivors"] == []


def test_real_exec_preserves_manager_pid_lease_and_watched_child(tmp_path: Path) -> None:
    result = _run_driver(_EXEC_DRIVER, tmp_path, "before")
    assert result["code"] != 0
    assert len(result["spawns"]) == 1
    assert len(result["managers"]) == 2
    assert result["managers"][0] == result["managers"][1]


def test_real_exec_during_cutover_discovers_pending_successor(tmp_path: Path) -> None:
    result = _run_driver(_DISCOVERY_EXEC_DRIVER, tmp_path, "before")
    assert len(result["spawns"]) == 1
    assert result["managers"][0] == result["managers"][1]
    assert len(result["managers"]) == 2
    assert result["watched"] == result["final"]


def test_high_numbered_pidfd_liveness_has_no_select_ceiling(tmp_path: Path) -> None:
    result = _run_driver(_HIGH_FD_DRIVER, tmp_path)
    if result.get("unsupported_limit"):
        pytest.skip("host descriptor limit does not permit a descriptor above FD_SETSIZE")
    assert result["fd"] >= 1024
    assert result["initial"] is True
    assert result["terminal"] is False
