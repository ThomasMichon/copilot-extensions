"""Refusal branches for compiler-free Linux custody never signal numeric PIDs."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from pathlib import Path
import signal
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent_procutil import _daemon_instance_linux as linux
from agent_procutil import _linux_pidfd as binding
from agent_procutil.daemon_instances import DaemonInstanceReceipt, InstanceCleanupResult

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux lifetime owner")


def test_missing_signaling_refuses_before_registry_mutation_or_fork(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    descriptor = binding.open_pidfd(os.getpid())
    acquired = []
    closed = []

    def acquire(pid: int) -> int:
        acquired.append(pid)
        return descriptor

    def close(fd: int) -> None:
        closed.append(fd)
        os.close(fd)

    fork = Mock(side_effect=AssertionError("unsupported signaling admitted a worker"))
    lock = Mock(side_effect=AssertionError("unsupported signaling mutated the inventory"))
    monkeypatch.setattr(linux, "os", SimpleNamespace(getpid=os.getpid, close=close, fork=fork))
    monkeypatch.setattr(linux, "open_pidfd", acquire)
    monkeypatch.delattr(binding.signal, "pidfd_send_signal", raising=False)
    monkeypatch.setattr(binding.ctypes, "CDLL", lambda *args, **kwargs: SimpleNamespace())
    monkeypatch.setattr(linux.store, "_locked", lock)
    try:
        assert linux.supervise(tmp_path / ("a" * 32 + ".instance"), ["never-run"]) == 125
        assert acquired == [os.getpid()]
        assert closed == [descriptor]
        fork.assert_not_called()
        lock.assert_not_called()
        assert list(tmp_path.iterdir()) == []
    finally:
        if not closed:
            os.close(descriptor)


def test_missing_unreaped_wait_wrapper_refuses_before_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(linux, "os", SimpleNamespace(getpid=os.getpid, close=os.close))
    with pytest.raises(RuntimeError, match="unreaped-child wait observation"):
        linux.check()


@pytest.mark.parametrize("parent,exited,signaled", [
    (77, False, True), (88, False, False), (77, True, False),
], ids=["matching-live", "changed-parent", "already-exited"])
def test_descendant_stop_requires_parent_custody_and_a_live_pidfd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    parent: int, exited: bool, signaled: bool,
) -> None:
    children = tmp_path / "self/task/77/children"
    children.parent.mkdir(parents=True)
    children.write_text("123 ", encoding="ascii")
    process = tmp_path / "123/stat"
    process.parent.mkdir()
    process.write_text(f"123 (fixture child) S {parent}\n", encoding="ascii")
    closed = []
    acquire, send = Mock(return_value=91), Mock()
    monkeypatch.setattr(linux, "Path", lambda name: tmp_path / name.removeprefix("/proc/"))
    monkeypatch.setattr(linux, "os", SimpleNamespace(getpid=lambda: 77, close=closed.append))
    monkeypatch.setattr(linux, "open_pidfd", acquire)
    monkeypatch.setattr(linux, "_exited", lambda fd: exited)
    monkeypatch.setattr(linux, "send_pidfd_signal", send)
    if parent != 77:
        with pytest.raises(OSError, match="parent changed"):
            linux._retire_children()
    else:
        linux._retire_children()
    acquire.assert_called_once_with(123)
    if signaled:
        send.assert_called_once_with(91, signal.SIGKILL)
    else:
        send.assert_not_called()
    assert closed == [91]


def test_inconclusive_descendant_capability_never_publishes_settlement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reaped(*args: object) -> None:
        raise ChildProcessError()

    monkeypatch.setattr(linux, "os", SimpleNamespace(killpg=Mock(), waitpid=reaped, WNOHANG=1))
    monkeypatch.setattr(
        linux, "_retire_children", Mock(side_effect=RuntimeError("signaling unavailable")),
    )
    assert linux._settle(123) is False


def test_settlement_drains_six_hundred_ready_children_without_per_child_sleep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = SimpleNamespace(now=0.0, reaped=0)

    def reap(_pid: int, _flags: int) -> tuple[int, int]:
        if clock.reaped == 600:
            raise ChildProcessError()
        clock.reaped += 1
        return 1000 + clock.reaped, 0

    def sleep(seconds: float) -> None:
        clock.now += seconds

    pause = Mock(side_effect=sleep)
    monkeypatch.setattr(linux, "os", SimpleNamespace(killpg=Mock(), waitpid=reap, WNOHANG=1))
    monkeypatch.setattr(linux, "time", SimpleNamespace(monotonic=lambda: clock.now, sleep=pause))
    monkeypatch.setattr(linux, "_retire_children", Mock())
    assert linux._settle(123) is True
    assert clock.reaped == 600
    pause.assert_not_called()


def test_immediately_waitable_children_cannot_extend_the_settlement_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = SimpleNamespace(now=0.0)

    def reap(_pid: int, _flags: int) -> tuple[int, int]:
        clock.now += 1
        return 1000, 0

    wait = Mock(side_effect=reap)
    pause = Mock()
    monkeypatch.setattr(linux, "os", SimpleNamespace(killpg=Mock(), waitpid=wait, WNOHANG=1))
    monkeypatch.setattr(linux, "time", SimpleNamespace(monotonic=lambda: clock.now, sleep=pause))
    monkeypatch.setattr(linux, "_retire_children", Mock())
    assert linux._settle(123) is False
    assert clock.now == 5
    assert wait.call_count == 5
    pause.assert_not_called()


@pytest.fixture
def monitored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    receipt = DaemonInstanceReceipt("a" * 32, tmp_path)
    original = {
        "schema": "2", "clock_domain": linux.lease_clock.domain(),
        "pid": "123", "birth": "child", "supervisor_pid": "456",
        "supervisor_birth": "supervisor", "owner_pid": "789", "owner_birth": "owner",
        "hard_deadline_mono": "10", "deadline_mono": "1",
    }
    current = dict(original)
    clock = SimpleNamespace(mono=2.0, wall=1002.0)
    exited = SimpleNamespace(si_status=37, si_code=os.CLD_EXITED)
    wait = Mock(side_effect=[None, exited])
    sleep = Mock()
    locked = Mock(side_effect=lambda value: nullcontext(value.record_path))
    read = Mock(side_effect=lambda path: dict(current))
    monkeypatch.setattr(linux, "os", SimpleNamespace(
        waitid=wait, P_PID=os.P_PID, WEXITED=os.WEXITED,
        WNOHANG=os.WNOHANG, WNOWAIT=os.WNOWAIT, CLD_EXITED=os.CLD_EXITED,
    ))
    monkeypatch.setattr(linux, "time", SimpleNamespace(
        monotonic=lambda: clock.mono, time=lambda: clock.wall, sleep=sleep,
    ))
    monkeypatch.setattr(linux, "_exited", lambda descriptor: False)
    monkeypatch.setattr(linux.store, "_locked", locked)
    monkeypatch.setattr(linux.store, "_read", read)
    return SimpleNamespace(
        receipt=receipt, original=original, current=current, clock=clock,
        wait=wait, sleep=sleep, locked=locked, read=read, exited=exited,
    )


def test_committed_renewal_precedes_a_delayed_supervisor_wakeup(monitored: SimpleNamespace) -> None:
    monitored.current.update(deadline_mono="4", updated="1000.9")
    lease = linux._Lease(10, 1)
    assert linux._watch_child(
        monitored.receipt, monitored.original, 123, 99, lease, lambda: False,
    ) == 37
    assert lease.lease_mono == 4
    monitored.read.assert_called_once()
    monitored.sleep.assert_called_once_with(.05)


def test_expiry_decision_stays_under_the_lock_when_release_is_delayed(
    monitored: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monitored.clock.mono, monitored.clock.wall = .5, 1000.5
    monitored.wait.side_effect = [None, None, monitored.exited]

    @contextmanager
    def locked(receipt: DaemonInstanceReceipt) -> Iterator[Path]:
        yield receipt.record_path
        if monitored.read.call_count == 1:
            monitored.current.update(deadline_mono="4", updated="1000.9")
            monitored.clock.mono, monitored.clock.wall = 2, 1002

    monkeypatch.setattr(linux.store, "_locked", locked)
    lease = linux._Lease(10, 1)
    assert linux._watch_child(
        monitored.receipt, monitored.original, 123, 99, lease, lambda: False,
    ) == 37
    assert monitored.read.call_count == 2
    assert lease.lease_mono == 4


def test_unrenewed_lease_is_observed_before_expiry(monitored: SimpleNamespace) -> None:
    lease = linux._Lease(10, 1)
    assert linux._watch_child(
        monitored.receipt, monitored.original, 123, 99, lease, lambda: False,
    ) == 124
    monitored.read.assert_called_once()
    monitored.sleep.assert_not_called()


def test_lock_contention_is_unknown_until_inspection_or_independent_hard_cap(
    monitored: SimpleNamespace, caplog: pytest.LogCaptureFixture,
) -> None:
    monitored.wait.side_effect = None
    monitored.wait.return_value = None
    monitored.locked.side_effect = BlockingIOError()
    monitored.original["hard_deadline_mono"] = monitored.current["hard_deadline_mono"] = "3"

    def advance(_seconds: float) -> None:
        monitored.clock.mono += .5
        monitored.clock.wall += .5

    monitored.sleep.side_effect = advance
    lease = linux._Lease(3, 1)
    assert linux._watch_child(
        monitored.receipt, monitored.original, 123, 99, lease, lambda: False,
    ) == 124
    assert monitored.locked.call_count == 2
    assert monitored.sleep.call_count == 2
    monitored.read.assert_not_called()
    assert caplog.text.count("lease inspection deferred") == 1
    assert monitored.clock.mono == lease.hard_mono


def test_cleanup_accepts_large_collection_budget_but_bounds_each_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from agent_procutil import _daemon_instance_cli as cli

    receipt = DaemonInstanceReceipt("a" * 32, tmp_path)
    cleanup = Mock(return_value=InstanceCleanupResult(
        receipt.instance_id, "settled", "process-exit-observed", (),
    ))
    monkeypatch.setattr(cli.store, "cleanup", cleanup)
    assert cli.main(["cleanup", str(receipt.record_path), "600"]) == 0
    cleanup.assert_called_once_with(receipt, stop_timeout=300)
    assert capsys.readouterr().out == f"settled\tprocess-exit-observed\t{receipt.instance_id}\n"
