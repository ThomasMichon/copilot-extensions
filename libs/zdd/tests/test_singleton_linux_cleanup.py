"""Cleanup ordering and pidfd custody with controlled process snapshots."""

from __future__ import annotations

import signal
from pathlib import Path
from types import SimpleNamespace

import pytest

from zdd.singleton_linux import LinuxBackend
from zdd.singleton_state import ProcessIdentity


class Reference:
    def __init__(self, pid: int, events: list[str]) -> None:
        self.identity = ProcessIdentity(pid, str(pid), "boot")
        self.events = events
        self.live = True
        self.closed = False

    def alive(self) -> bool:
        return self.live

    def send_signal(self, number: int) -> bool:
        self.events.append(f"signal:{self.identity.pid}:{number}")
        if number == signal.SIGKILL:
            self.live = False
        return True

    def close(self) -> None:
        assert not self.closed
        self.closed = True


def test_cleanup_waits_for_stop_then_rescans_late_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = LinuxBackend.__new__(LinuxBackend)
    backend.manager_pid = 999
    events: list[str] = []
    references = {pid: Reference(pid, events) for pid in (100, 101)}
    visible = {100}
    monkeypatch.setattr(Path, "iterdir", lambda path: iter(
        SimpleNamespace(name=str(pid)) for pid in sorted(visible)
    ))
    monkeypatch.setattr(backend, "identify", lambda pid: references[pid].identity)
    monkeypatch.setattr(backend, "open_process", lambda identity: references[identity.pid])
    monkeypatch.setattr(backend, "owns", lambda reference: True)
    monkeypatch.setattr(backend, "reap_zombies", lambda: None)

    def wait_stopped(reference: Reference, deadline: float) -> None:
        events.append(f"stopped:{reference.identity.pid}")
        if reference.identity.pid == 100:
            visible.add(101)

    monkeypatch.setattr(backend, "_wait_stopped", wait_stopped)
    backend.cleanup()
    assert events.index("stopped:100") < events.index(f"signal:101:{signal.SIGSTOP}")
    assert all(not reference.live and reference.closed for reference in references.values())
    assert events.index("stopped:101") < events.index(f"signal:100:{signal.SIGKILL}")


def test_cleanup_ownership_probe_failure_closes_unclaimed_pidfd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = LinuxBackend.__new__(LinuxBackend)
    backend.manager_pid = 999
    reference = Reference(100, [])
    monkeypatch.setattr(Path, "iterdir", lambda path: iter([SimpleNamespace(name="100")]))
    monkeypatch.setattr(backend, "identify", lambda pid: reference.identity)
    monkeypatch.setattr(backend, "open_process", lambda identity: reference)

    def fail_probe(candidate: Reference) -> bool:
        raise TimeoutError("injected ownership proof deadline")

    monkeypatch.setattr(backend, "owns", fail_probe)
    with pytest.raises(TimeoutError, match="ownership proof"):
        backend.cleanup()
    assert reference.closed
