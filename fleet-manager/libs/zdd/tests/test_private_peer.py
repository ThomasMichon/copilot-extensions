"""Exact-reference exit policy, separate from channel and process lifetime."""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from zdd.private_peer import PeerProcess
from zdd import private_peer
from zdd.private_protocol import Deadline, PeerIdentity, PrivateIPCError, Refusal
from zdd.private_windows import WindowsChannel
from zdd.private_windows_native import WAIT_TIMEOUT, Native

PEER = PeerIdentity(123, "456", "synthetic-principal")


def test_missing_native_poll_is_a_fixed_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(private_peer, "select", SimpleNamespace())
    with pytest.raises(PrivateIPCError) as caught:
        private_peer._pidfd_exited(55, 0)
    assert caught.value.reason is Refusal.UNAVAILABLE
    assert caught.value.__cause__ is None


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_missing_native_backend_refuses_before_taking_custody(
    monkeypatch: pytest.MonkeyPatch, platform: str,
) -> None:
    monkeypatch.setattr(private_peer, "sys", SimpleNamespace(platform=platform))
    with pytest.raises(PrivateIPCError) as caught:
        PeerProcess(55, PEER)
    assert caught.value.reason is Refusal.UNAVAILABLE


def _api(result: int) -> tuple[Native, list[int]]:
    api = object.__new__(Native)
    closed: list[int] = []
    api.k = SimpleNamespace(WaitForSingleObject=lambda *args: result)
    api.close = closed.append
    return api, closed


@pytest.mark.parametrize("result,exited", [(0, True), (WAIT_TIMEOUT, False)])
def test_only_native_process_signal_is_exit(result: int, exited: bool) -> None:
    api, closed = _api(result)
    reference = PeerProcess(55, PEER, api=api)
    try:
        assert reference.peer == PEER
        assert reference.exited(Deadline(1)) is exited
    finally:
        reference.close()
        reference.close()
    assert closed == [55]
    with pytest.raises(PrivateIPCError) as caught:
        reference.exited(Deadline(1))
    assert caught.value.reason is Refusal.CLOSED


@pytest.mark.parametrize("result", [0xFFFFFFFF, 0x80, 1])
def test_native_wait_error_never_certifies_exit(result: int) -> None:
    api, _closed = _api(result)
    reference = PeerProcess(55, PEER, api=api)
    try:
        with pytest.raises(PrivateIPCError) as caught:
            reference.exited(Deadline(1))
        assert caught.value.reason is Refusal.IDENTITY
    finally:
        reference.close()


@pytest.mark.parametrize("method", ["exited", "wait_exit"])
def test_exit_observation_rechecks_original_deadline(
    method: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    api, _closed = _api(0)
    reference = PeerProcess(55, PEER, api=api)
    deadline = Deadline(1)
    original = deadline.remaining
    calls = 0

    def remaining() -> float:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise PrivateIPCError(Refusal.DEADLINE)
        return original()

    monkeypatch.setattr(deadline, "remaining", remaining)
    try:
        with pytest.raises(PrivateIPCError) as caught:
            getattr(reference, method)(deadline)
        assert caught.value.reason is Refusal.DEADLINE
    finally:
        reference.close()


def test_cancelled_wait_does_not_probe_or_close_custody() -> None:
    api, closed = _api(0)
    calls: list[int] = []
    api.k.WaitForSingleObject = lambda handle, timeout: calls.append(handle) or 0
    reference = PeerProcess(55, PEER, api=api)
    cancelled = threading.Event()
    cancelled.set()
    try:
        with pytest.raises(PrivateIPCError) as caught:
            reference.wait_exit(Deadline(1, cancelled=cancelled))
        assert caught.value.reason is Refusal.CANCELLED
        assert calls == closed == []
        assert reference.exited(Deadline(1)) is True
    finally:
        reference.close()


@pytest.mark.parametrize("field,value", [
    ("_pid", -1), ("_thread", None),
])
def test_foreign_owner_cannot_observe_or_release(field: str, value: object) -> None:
    api, closed = _api(0)
    reference = PeerProcess(55, PEER, api=api)
    original = getattr(reference, field)
    setattr(reference, field, value)
    try:
        for operation in (
            lambda: reference.exited(Deadline(1)),
            lambda: reference.wait_exit(Deadline(1)),
            reference.close,
        ):
            with pytest.raises(PrivateIPCError) as caught:
                operation()
            assert caught.value.reason is Refusal.AUTHORITY
        assert closed == []
    finally:
        setattr(reference, field, original)
        reference.close()


@pytest.mark.parametrize("expiry", [False, True])
def test_channel_duplicates_only_held_handle_and_cleans_failed_acquisition(expiry: bool) -> None:
    api, closed = _api(WAIT_TIMEOUT)
    duplicates: list[tuple[int, int]] = []
    checks = 0

    def alive(handle: int) -> None:
        nonlocal checks
        assert handle == 12
        checks += 1
        if checks == 2 and expiry:
            raise PrivateIPCError(Refusal.DEADLINE)

    def duplicate(handle: int, pid: int) -> int:
        duplicates.append((handle, pid))
        return 55

    api.alive = alive
    api.duplicate_process = duplicate
    channel = WindowsChannel(11, PEER, 12, api=api)
    if expiry:
        with pytest.raises(PrivateIPCError) as caught:
            channel.retain_peer(Deadline(1))
        assert caught.value.reason is Refusal.DEADLINE
        assert closed == [55]
    else:
        reference = channel.retain_peer(Deadline(1))
        assert reference.peer == PEER
        channel.close()
        assert reference.exited(Deadline(1)) is False
        reference.close()
        assert closed == [11, 12, 55]
    assert duplicates == [(12, PEER.pid)]
    assert checks == 2
    channel.close()
