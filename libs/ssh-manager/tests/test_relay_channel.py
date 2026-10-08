from __future__ import annotations

import asyncio

import ssh_manager.relay_channel as relay_mod
from ssh_manager.config_sources import SSHConfig
from ssh_manager.relay_channel import SupervisedRelayForward, _SettleResult


class _FakeProc:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.returncode = None
        self.stderr = None


def test_pid_change_callback_tracks_establish_restart_and_stop(monkeypatch):
    procs = [_FakeProc(111), _FakeProc(222)]
    seen: list[tuple[int | None, str | None]] = []

    async def fake_spawn(*_args, **_kwargs):
        return procs.pop(0)

    async def fake_wait_settled(self, proc):
        return _SettleResult(True, "", False)

    async def fake_kill(self, proc):
        proc.returncode = -9

    monkeypatch.setattr(relay_mod, "create_ssh_subprocess", fake_spawn)
    monkeypatch.setattr(relay_mod, "process_identity", lambda pid: f"id-{pid}")
    monkeypatch.setattr(SupervisedRelayForward, "_wait_settled", fake_wait_settled)
    monkeypatch.setattr(SupervisedRelayForward, "_kill", fake_kill)

    forward = SupervisedRelayForward(
        SSHConfig(host_alias="box"),
        41000,
        on_pid_change=lambda: seen.append(
            (forward.process_pid, forward.process_birth_identity)
        ),
    )

    async def exercise():
        await forward.establish()
        await forward._cancel_process()
        await forward.establish()

    asyncio.run(exercise())

    assert seen[0] == (111, "id-111")
    assert (None, None) in seen
    assert seen[-1] == (222, "id-222")


def _supervised(monkeypatch, *, gate=None):
    """A relay whose spawned ssh 'dies' on demand, with fake spawn/kill."""
    spawned: list[_FakeProc] = []

    async def fake_spawn(*_args, **_kwargs):
        proc = _FakeProc(1000 + len(spawned))
        spawned.append(proc)
        return proc

    async def fake_wait_settled(self, proc):
        return _SettleResult(True, "", False)

    async def fake_kill(self, proc):
        proc.returncode = -9

    async def fast_sleep(self, _delay):
        await asyncio.sleep(0)

    monkeypatch.setattr(relay_mod, "create_ssh_subprocess", fake_spawn)
    monkeypatch.setattr(relay_mod, "process_identity", lambda pid: f"id-{pid}")
    monkeypatch.setattr(SupervisedRelayForward, "_wait_settled", fake_wait_settled)
    monkeypatch.setattr(SupervisedRelayForward, "_kill", fake_kill)
    monkeypatch.setattr(SupervisedRelayForward, "_sleep", fast_sleep)
    relay = SupervisedRelayForward(
        SSHConfig(host_alias="box"), 41000, reconnect_gate=gate,
    )
    return relay, spawned


async def _settle(predicate, *, attempts: int = 200) -> None:
    for _ in range(attempts):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition was not reached")


async def test_monitor_retires_instead_of_reconnecting_to_stopped_venue(monkeypatch):
    gate_calls = []

    async def gate() -> bool:
        gate_calls.append(True)
        return False

    relay, spawned = _supervised(monkeypatch, gate=gate)
    await relay.start()
    assert len(spawned) == 1
    spawned[0].returncode = 255  # the venue stopped; ssh exited

    await _settle(lambda: relay.retired)

    assert gate_calls
    assert len(spawned) == 1, "must not reconnect (and so re-wake) the venue"
    assert relay.is_alive is False
    task = relay._monitor_task
    assert task is None or task.done()
    await relay.stop()


async def test_monitor_skips_reconnect_while_gate_is_inconclusive(monkeypatch):
    answers = [RuntimeError("api down"), RuntimeError("api down"), True]

    async def gate() -> bool:
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    relay, spawned = _supervised(monkeypatch, gate=gate)
    await relay.start()
    spawned[0].returncode = 255

    await _settle(lambda: len(spawned) == 2)

    assert answers == []
    assert relay.retired is False
    assert relay.is_alive is True
    await relay.stop()


async def test_monitor_without_gate_still_reconnects(monkeypatch):
    relay, spawned = _supervised(monkeypatch)
    await relay.start()
    spawned[0].returncode = 255

    await _settle(lambda: len(spawned) == 2)

    assert relay.is_alive is True
    await relay.stop()


async def test_stop_nowait_cancels_monitor_and_kills_process_group(monkeypatch):
    relay, spawned = _supervised(monkeypatch)
    await relay.start()
    proc = spawned[0]
    killed_groups = []
    monkeypatch.setattr(relay_mod.sys, "platform", "linux")
    monkeypatch.setattr(relay_mod.os, "getpgid", lambda pid: pid, raising=False)
    monkeypatch.setattr(relay_mod.os, "getpgrp", lambda: 1, raising=False)
    monkeypatch.setattr(
        relay_mod.os, "killpg",
        lambda pgid, sig: killed_groups.append((pgid, sig)),
        raising=False,
    )
    monkeypatch.setattr(relay_mod.signal, "SIGKILL", 9, raising=False)
    monitor = relay._monitor_task

    relay.stop_nowait()
    relay.stop_nowait()  # idempotent

    assert killed_groups == [(proc.pid, 9)]
    assert relay._monitor_task is None
    await asyncio.sleep(0)
    assert monitor.cancelled() or monitor.done()
    proc.returncode = -9
    await _settle(lambda: relay._proc is None)
    # A stopped relay never reconnects, even if the monitor were to run again.
    assert await relay._reconnect_allowed() is False
    assert len(spawned) == 1
