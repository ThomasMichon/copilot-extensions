"""Darwin production adapters with synthetic ABI results and explicit native checks."""

from __future__ import annotations

import ctypes
import errno
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_procutil import _caller_owned as protocol
from agent_procutil import _darwin_caller_owned as darwin
from agent_procutil import owned_process as ownership
from agent_procutil.owned_process import OwnedCleanupError, run_caller_owned


def test_darwin_native_structures_match_xnu_64_bit_field_offsets():
    # XNU bsd/sys/signal.h (siginfo_t) and bsd/sys/proc_info.h (proc_bsdinfo).
    assert ctypes.sizeof(darwin._SigInfo) == 104
    assert {
        field: getattr(darwin._SigInfo, field).offset
        for field in ("si_pid", "si_status", "si_addr", "si_value", "si_band", "padding")
    } == {
        "si_pid": 12, "si_status": 20, "si_addr": 24,
        "si_value": 32, "si_band": 40, "padding": 48,
    }
    assert ctypes.sizeof(darwin._BSDInfo) == 136
    assert {
        field: getattr(darwin._BSDInfo, field).offset
        for field in ("status", "pid", "comm", "name", "pgid", "start_sec", "start_usec")
    } == {
        "status": 4, "pid": 12, "comm": 48, "name": 64,
        "pgid": 100, "start_sec": 120, "start_usec": 128,
    }


@pytest.mark.parametrize("disposition,returncode,error", [
    (1, None, "default SIGCHLD"), (0, 0, "already released"), (0, None, None),
])
def test_darwin_wait_dispatch_preserves_common_custody_checks(
    monkeypatch, disposition, returncode, error,
):
    observations = []
    process = SimpleNamespace(pid=40, returncode=returncode)
    status = object()
    monkeypatch.setattr(ownership, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(ownership, "signal", SimpleNamespace(
        SIGCHLD=20, SIG_DFL=0, getsignal=lambda signum: disposition,
    ))
    monkeypatch.setattr(ownership, "os", SimpleNamespace())  # no Python waitid or constants
    monkeypatch.setattr(
        darwin, "observe_unreaped", lambda root: observations.append(root) or status,
    )
    if error:
        with pytest.raises(RuntimeError, match=error):
            ownership._observe_unreaped(process)
        assert observations == []
    else:
        assert ownership._observe_unreaped(process) is status
        assert observations == [process]


@pytest.mark.parametrize("failure", ["none", "native", "sigchld"])
def test_darwin_pre_spawn_requires_native_waitid_without_python_exposure(monkeypatch, failure):
    calls = []

    def probe():
        calls.append("native")
        if failure == "native":
            raise OSError("native waitid unavailable")

    class ReachedSpawn(Exception):
        pass

    def spawn(*args, **kwargs):
        calls.append("spawn")
        assert kwargs["start_new_session"] is True
        raise ReachedSpawn

    monkeypatch.setattr(ownership, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(ownership, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(ownership, "signal", SimpleNamespace(
        SIGCHLD=20, SIG_DFL=0, getsignal=lambda signum: int(failure == "sigchld"),
    ))
    monkeypatch.setattr(darwin, "_waitid", probe)
    monkeypatch.setattr(ownership, "no_window_kwargs", lambda: {})
    monkeypatch.setattr(ownership, "spawn_sync_in_kill_on_close_job", spawn)
    if failure == "none":
        with pytest.raises(ReachedSpawn):
            ownership._run_owned(["product"], timeout=1)
        assert calls == ["native", "spawn"]
    elif failure == "native":
        with pytest.raises(OSError, match="native waitid unavailable"):
            ownership._run_owned(["product"], timeout=1)
        assert calls == ["native"]
    else:
        with pytest.raises(RuntimeError, match="default SIGCHLD"):
            ownership._run_owned(["product"], timeout=1)
        assert calls == ["native"]


@pytest.mark.parametrize("pid,result", [(0, None), (40, "exit"), (41, "mismatch")])
def test_native_waitid_construction_retains_original_child(monkeypatch, pid, result):
    calls = []

    def waitid(kind, child, data, flags):
        calls.append((kind, child, flags))
        assert ctypes.sizeof(data._obj) == 104
        data._obj.si_pid = pid
        return 0

    def load(path, **kwargs):
        assert path == "/usr/lib/libSystem.B.dylib" and kwargs == {"use_errno": True}
        return SimpleNamespace(waitid=waitid)

    monkeypatch.setattr(darwin, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(ctypes, "CDLL", load)
    process = SimpleNamespace(pid=40, returncode=None)
    if result == "mismatch":
        with pytest.raises(RuntimeError, match="identity changed"):
            darwin.observe_unreaped(process)
    else:
        observed = darwin.observe_unreaped(process)
        assert (observed is None) is (result is None)
    assert calls == [(1, 40, 37)] and process.returncode is None


def test_native_waitid_error_cannot_release_custody(monkeypatch):
    def fail(*args):
        ctypes.set_errno(errno.ECHILD)
        return -1

    monkeypatch.setattr(darwin, "_waitid", lambda: fail)
    with pytest.raises(RuntimeError, match="custody is unconfirmed"):
        darwin.observe_unreaped(SimpleNamespace(pid=40, returncode=None))


def _queue(monkeypatch, *, parents=(40, 40), registration=(), events=None):
    calls, closed = [], []
    ancestry = iter(parents)
    exit_event = SimpleNamespace(ident=40, filter=-5, flags=0, fflags=0x80000000)

    def control(changes, count, timeout):
        calls.append((changes, count, timeout))
        return registration if changes is not None else (
            [exit_event] if events is None else events
        )

    monkeypatch.setattr(darwin, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(darwin, "os", SimpleNamespace(getppid=lambda: next(ancestry)))
    monkeypatch.setattr(darwin, "select", SimpleNamespace(
        kqueue=lambda: SimpleNamespace(control=control, close=lambda: closed.append(True)),
        kevent=lambda pid, **kwargs: (pid, kwargs),
        KQ_FILTER_PROC=-5, KQ_EV_ADD=1, KQ_EV_ONESHOT=16,
        KQ_NOTE_EXIT=0x80000000, KQ_EV_ERROR=0x4000,
    ))
    return calls, closed


def test_kqueue_binds_exact_parent_before_waiting(monkeypatch):
    calls, closed = _queue(monkeypatch)
    watch = darwin.OwnerWatch(40)
    watch.wait()
    watch.close()
    event, = calls[0][0]
    assert event == (40, {"filter": -5, "flags": 17, "fflags": 0x80000000})
    assert calls[1] == (None, 1, None)
    assert closed == [True]


@pytest.mark.parametrize("parents,registration", [
    ((1,), ()), ((40, 1), ()), ((40,), [object()]),
])
def test_kqueue_rejects_reparenting_or_dead_owner(monkeypatch, parents, registration):
    _, closed = _queue(monkeypatch, parents=parents, registration=registration)
    with pytest.raises(RuntimeError):
        darwin.OwnerWatch(40)
    assert closed == [True]


@pytest.mark.parametrize("event", [
    SimpleNamespace(ident=41, filter=-5, flags=0, fflags=0x80000000),
    SimpleNamespace(ident=40, filter=-5, flags=0x4000, fflags=0x80000000),
    SimpleNamespace(ident=40, filter=-5, flags=0, fflags=0),
    SimpleNamespace(ident=40, filter=1, flags=0, fflags=0x80000000),
    None,
])
def test_kqueue_rejects_wrong_identity_and_native_errors(monkeypatch, event):
    _queue(monkeypatch, events=[] if event is None else [event])
    watch = darwin.OwnerWatch(40)
    try:
        with pytest.raises(RuntimeError):
            watch.wait()
    finally:
        watch.close()


def _census(monkeypatch, *, status=5, group=10, pid=20, info_error=None, short=False):
    calls = []

    def listing(leader, data, size):
        assert leader == 10 and size == ctypes.sizeof(data)
        data[0], data[1] = 10, 20
        return 2

    def info(number, flavor, argument, data, size):
        calls.append(number)
        assert number == 20 and flavor == 3 and argument == 0
        assert size == ctypes.sizeof(darwin._BSDInfo) == 136
        if info_error is not None:
            ctypes.set_errno(info_error)
            return 0
        data._obj.pid, data._obj.pgid, data._obj.status = pid, group, status
        return size - 1 if short else size

    def load(path, **kwargs):
        assert path == "/usr/lib/libproc.dylib" and kwargs == {"use_errno": True}
        return SimpleNamespace(proc_listpgrppids=listing, proc_pidinfo=info)

    monkeypatch.setattr(darwin, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(ctypes, "CDLL", load)
    return darwin.GroupCensus(), calls


@pytest.mark.parametrize("status,group,active", [(2, 10, True), (5, 10, False), (2, 99, False)])
def test_libproc_observes_only_live_members_of_owned_group(monkeypatch, status, group, active):
    census, calls = _census(monkeypatch, status=status, group=group)
    assert census.active(10) is active
    assert calls == [20]


@pytest.mark.parametrize("kwargs", [
    {"pid": 21}, {"short": True}, {"info_error": errno.EPERM}, {"info_error": 0},
])
def test_libproc_refuses_unconfirmed_member_identity(monkeypatch, kwargs):
    census, _ = _census(monkeypatch, **kwargs)
    with pytest.raises(RuntimeError):
        census.active(10)


def test_libproc_tolerates_only_proven_disappearance(monkeypatch):
    census, _ = _census(monkeypatch, info_error=errno.ESRCH)
    assert not census.active(10)


def test_libproc_full_pid_count_retries_instead_of_accepting_truncation(monkeypatch):
    census, calls = _census(monkeypatch)
    listing = census.list_pids
    capacities = []

    def full_first(leader, data, size):
        capacities.append(len(data))
        return len(data) if len(capacities) == 1 else listing(leader, data, size)

    census.list_pids = full_first
    assert not census.active(10)
    assert capacities == [64, 128] and calls == [20]


def test_libproc_zero_with_native_error_is_not_an_empty_group(monkeypatch):
    census, _ = _census(monkeypatch)

    def fail(*args):
        ctypes.set_errno(errno.EPERM)
        return 0

    census.list_pids = fail
    with pytest.raises(OSError, match="census failed"):
        census.active(10)


@pytest.mark.parametrize("size", [-1, 3, 257])
def test_libproc_rejects_failed_or_malformed_census(monkeypatch, size):
    census, _ = _census(monkeypatch)
    census.list_pids = lambda *args: size
    with pytest.raises((OSError, RuntimeError)):
        census.active(10)


def test_darwin_public_dispatch_uses_guard_and_caller_pid(monkeypatch):
    probes, invocations = [], []
    monkeypatch.setattr(darwin, "require_support", lambda: probes.append(True))
    monkeypatch.setattr(protocol, "sys", SimpleNamespace(platform="darwin", executable=sys.executable))
    monkeypatch.setattr(protocol, "os", SimpleNamespace(
        name="posix", getpid=lambda: 40, pipe=os.pipe, close=os.close,
        set_inheritable=os.set_inheritable,
    ))
    monkeypatch.setattr(protocol, "signal", SimpleNamespace(SIGKILL=9))

    def run(argv, **kwargs):
        invocations.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, -9, json.dumps({
            "version": 1, "kind": "completed", "returncode": 7, "stdout": "ok", "stderr": "",
        }), "")

    monkeypatch.setattr(protocol, "_run_owned", run)
    result = run_caller_owned(
        ["product"], timeout=2, max_stdout_bytes=10, max_stderr_bytes=10,
    )
    assert result.returncode == 7 and result.stdout == "ok"
    assert probes == [True]
    argv, options = invocations[0]
    assert argv[-1] == "40" and argv[5] == protocol._BOOTSTRAP
    assert options["confirm_posix_group"] is True
    assert options["pass_fds"] == (int(argv[-2]),)


def test_darwin_native_failure_refuses_before_guard_spawn(monkeypatch):
    monkeypatch.setattr(protocol, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(protocol, "os", SimpleNamespace(name="posix"))

    def refuse():
        raise OSError("native unavailable")

    monkeypatch.setattr(darwin, "require_support", refuse)
    monkeypatch.setattr(protocol, "_run_owned", lambda *a, **k: pytest.fail("spawned"))
    with pytest.raises(OSError, match="native unavailable"):
        run_caller_owned(["product"], timeout=2, max_stdout_bytes=10, max_stderr_bytes=10)


@pytest.mark.parametrize("live", [True, False])
def test_darwin_confirmation_keeps_unreaped_root_and_refuses_live_members(monkeypatch, live):
    clock = [0.]
    roots = []
    monkeypatch.setattr(protocol, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(protocol, "_observe_unreaped", lambda root: roots.append(root) or object())
    monkeypatch.setattr(darwin, "GroupCensus", lambda: SimpleNamespace(active=lambda pid: live))
    monkeypatch.setattr(protocol, "time", SimpleNamespace(
        monotonic=lambda: clock[0],
        sleep=lambda delay: clock.__setitem__(0, clock[0] + 1.1),
    ))
    root = SimpleNamespace(pid=10, returncode=None)
    if live:
        with pytest.raises(OwnedCleanupError) as caught:
            protocol._confirm_group(root)
        assert caught.value.process is root
    else:
        protocol._confirm_group(root)
        assert len(roots) == 2
    assert roots and all(item is root for item in roots) and root.returncode is None


@pytest.mark.skipif(sys.platform != "darwin", reason="requires actual macOS kernel APIs")
def test_native_darwin_kqueue_and_libproc_construction():
    darwin.require_support()
    assert darwin.GroupCensus().active(os.getpgrp()) in (True, False)


@pytest.mark.skipif(sys.platform != "darwin", reason="requires actual macOS kernel APIs")
def test_native_darwin_guard_identity_and_bounded_execution():
    result = run_caller_owned(
        [sys.executable, "-I", "-c",
         "import os,json; print(json.dumps([os.getppid(),os.getpgrp(),os.getsid(0)]))"],
        timeout=5, max_stdout_bytes=1024, max_stderr_bytes=1024,
    )
    parent, group, session = json.loads(result.stdout)
    assert parent == group == session
    with pytest.raises(subprocess.TimeoutExpired):
        run_caller_owned(
            [sys.executable, "-I", "-c", "import time; time.sleep(60)"],
            timeout=.3, max_stdout_bytes=1024, max_stderr_bytes=1024,
        )


@pytest.mark.skipif(sys.platform != "darwin", reason="requires actual macOS kernel APIs")
def test_native_darwin_caller_death_retires_guard_target_and_descendant(tmp_path):
    import select

    marker = tmp_path / "identities.json"
    target = (
        "import os,json,time; from pathlib import Path; child=os.fork(); "
        "Path(" + repr(str(marker)) + ").write_text("
        "json.dumps([os.getppid(),os.getpid(),child])) if child else None; "
        "time.sleep(5)"
    )
    caller = (
        "import sys;sys.path.insert(0," + repr(str(Path(protocol.__file__).resolve().parents[1]))
        + ");from agent_procutil.owned_process import run_caller_owned;"
        + "run_caller_owned(" + repr([sys.executable, "-I", "-c", target])
        + ",timeout=4,max_stdout_bytes=1024,max_stderr_bytes=1024)"
    )
    process = subprocess.Popen(
        [sys.executable, "-I", "-c", caller],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    identities, settled = None, False
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                identities = json.loads(marker.read_text())
                break
            except (OSError, ValueError):
                time.sleep(.01)
        assert identities is not None
        with select.kqueue() as queue:
            changes = [
                select.kevent(
                    pid, filter=select.KQ_FILTER_PROC,
                    flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                    fflags=select.KQ_NOTE_EXIT,
                )
                for pid in identities
            ]
            assert not queue.control(changes, 3, 0)
            # This Popen child remains unreaped and cannot have its PID reused.
            process.kill()
            gone = set()
            deadline = time.monotonic() + 1.5
            while len(gone) < 3 and time.monotonic() < deadline:
                for event in queue.control(None, 3, .05):
                    assert not event.flags & select.KQ_EV_ERROR
                    assert event.fflags & select.KQ_NOTE_EXIT
                    gone.add(event.ident)
            settled = gone == set(identities)
            assert settled
    finally:
        if process.returncode is None:
            process.kill()
        process.wait(timeout=3)
        # Even on a failed owner-death assertion, this fixture is finite by
        # construction; no fallback ever signals an unowned descendant PID.
        if identities is not None and not settled:
            time.sleep(5)
