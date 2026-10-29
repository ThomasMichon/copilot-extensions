"""Native pause/rollback and exit confirmation for an original external child."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agent_procutil._daemon_instance_identity import birth_identity
from agent_procutil.process_retirement import PinnedProcess, RetirementRefused


@pytest.mark.skipif(
    os.name != "nt" and not sys.platform.startswith("linux"),
    reason="External process pinning requires Windows or Linux",
)
@pytest.mark.parametrize("name", [
    None,
    pytest.param(b"worker-\xc3\xa9", marks=pytest.mark.skipif(
        not sys.platform.startswith("linux"), reason="Native Linux task name",
    )),
    pytest.param(b"worker-\xff", marks=pytest.mark.skipif(
        not sys.platform.startswith("linux"), reason="Opaque Linux task-name bytes",
    )),
])
def test_pause_rollback_and_confirmed_exit(tmp_path: Path, name: bytes | None) -> None:
    progress = tmp_path / "progress"
    code = (
        "from pathlib import Path\n"
        "import ctypes,os,sys,time,threading\n"
        "p=Path(sys.argv[1])\n"
        "name=bytes.fromhex(sys.argv[2]) if sys.argv[2] else None\n"
        "ready=threading.Barrier(5)\n"
        "def worker():\n"
        " if name is not None: assert ctypes.CDLL(None).prctl(15,name,0,0,0)==0\n"
        " ready.wait()\n"
        " time.sleep(60)\n"
        "if name is not None: assert ctypes.CDLL(None).prctl(15,name,0,0,0)==0\n"
        "for _ in range(4):\n"
        " threading.Thread(target=worker,daemon=True).start()\n"
        "ready.wait()\n"
        "print(os.getpid(),flush=True)\n"
        "while True:\n"
        " p.write_text(str(os.getpid())+':'+str(time.monotonic()))\n"
        " time.sleep(.01)\n"
    )
    process = subprocess.Popen(
        [getattr(sys, "_base_executable", sys.executable), "-c", code, str(progress),
         name.hex() if name is not None else ""],
        stdout=subprocess.PIPE, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        assert int(process.stdout.readline()) == process.pid
        deadline = time.monotonic() + 5
        while not progress.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert progress.exists()
        identity = birth_identity(process.pid)
        with pytest.raises(RetirementRefused, match="identity changed"):
            PinnedProcess(process.pid, identity + "1")
        with PinnedProcess(process.pid, identity) as pinned:
            pinned.pause()
            if sys.platform.startswith("linux"):
                tasks = list(Path(f"/proc/{process.pid}/task").iterdir())
                assert len(tasks) >= 5
                assert all(
                    (task / "stat").read_bytes().rsplit(b") ", 1)[1].split()[0]
                    in {b"T", b"t"} for task in tasks
                )
            before = progress.read_bytes()
            time.sleep(.05)
            assert progress.read_bytes() == before
            assert not pinned.exited()
        deadline = time.monotonic() + 2
        observed = progress.read_bytes()
        while observed == before and time.monotonic() < deadline:
            time.sleep(.01)
            observed = progress.read_bytes()
        assert observed != before
        with PinnedProcess(process.pid, identity) as pinned:
            pinned.pause()
            pinned.terminate()
            assert pinned.exited()
        assert process.wait(timeout=5) is not None
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        process.stdout.close()


def test_own_caller_cannot_be_pinned():
    with pytest.raises(RetirementRefused, match="caller"):
        PinnedProcess(os.getpid(), "not a process authority")


@pytest.mark.parametrize("pid", [0, -1, True, os.getpid() + 2**32])
def test_invalid_or_wrapping_pid_is_rejected_before_native_access(pid):
    with pytest.raises(ValueError, match="Invalid native process PID"):
        PinnedProcess(pid, "not a process authority")


@pytest.mark.parametrize("name", [b"fixture", b"worker-\xc3\xa9", b"worker-\xff"])
def test_linux_stopped_leader_does_not_prove_worker_threads_stopped(
    tmp_path: Path, name: bytes,
) -> None:
    from agent_procutil.process_retirement import _linux_threads_stopped

    for tid, state in ((1, "T"), (2, "R")):
        task = tmp_path / "task" / str(tid)
        task.mkdir(parents=True)
        (task / "stat").write_bytes(f"{tid} (".encode() + name + f") {state} 0".encode())
    assert not _linux_threads_stopped(tmp_path)
    (tmp_path / "task" / "2" / "stat").write_bytes(b"2 (" + name + b") T 0")
    assert _linux_threads_stopped(tmp_path)


def test_linux_thread_membership_must_settle_before_pause_confirmation(tmp_path, monkeypatch):
    from agent_procutil.process_retirement import _linux_threads_stopped

    tasks = tmp_path / "task"
    (tasks / "1").mkdir(parents=True)
    (tasks / "1" / "stat").write_text("1 (fixture) T 0", encoding="ascii")
    original = Path.iterdir
    reads = 0

    def iterdir(path):
        nonlocal reads
        if path == tasks:
            reads += 1
            if reads == 2:
                (tasks / "2").mkdir()
                (tasks / "2" / "stat").write_text("2 (late) R 0", encoding="ascii")
        return original(path)

    monkeypatch.setattr(Path, "iterdir", iterdir)
    assert not _linux_threads_stopped(tmp_path)
