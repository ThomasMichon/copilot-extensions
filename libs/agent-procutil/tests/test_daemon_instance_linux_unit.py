"""Refusal branches for compiler-free Linux custody never signal numeric PIDs."""

from __future__ import annotations

import os
from pathlib import Path
import signal
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent_procutil import _daemon_instance_linux as linux
from agent_procutil import _linux_pidfd as binding

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
