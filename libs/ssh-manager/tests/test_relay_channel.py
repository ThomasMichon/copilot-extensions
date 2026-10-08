from __future__ import annotations

import asyncio
import os
import subprocess
import sys

import pytest

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


class _KillableProc(_FakeProc):
    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


def _supervised(monkeypatch, *, gate=None):
    """A relay whose spawned ssh 'dies' on demand, with fake spawn/kill."""
    spawned: list[_FakeProc] = []

    async def fake_spawn(*_args, **kwargs):
        proc = _KillableProc(1000 + len(spawned))
        proc.env = kwargs.get("env")
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


async def test_stop_nowait_cancels_monitor_and_kills_owned_tree(monkeypatch):
    relay, spawned = _supervised(monkeypatch)
    await relay.start()
    proc = spawned[0]
    sweeps = []
    monkeypatch.setattr(
        SupervisedRelayForward, "_kill_owned_descendants",
        lambda self: sweeps.append(self) or 0,
    )
    monitor = relay._monitor_task

    relay.stop_nowait()
    relay.stop_nowait()  # idempotent

    await _settle(lambda: not relay._cleanup_tasks)
    assert proc.killed is True
    assert sweeps, "surviving ProxyCommand descendants must be swept"
    assert relay._proc is None
    assert relay._monitor_task is None
    assert monitor.cancelled() or monitor.done()
    # A stopped relay never reconnects, even if the monitor were to run again.
    assert await relay._reconnect_allowed() is False
    assert len(spawned) == 1


async def test_spawned_ssh_carries_the_supervisor_owner_token(monkeypatch):
    relay, spawned = _supervised(monkeypatch)
    await relay.establish()

    assert spawned[0].env[relay_mod._OWNER_ENV] == relay._owner_token
    other, _ = _supervised(monkeypatch)
    assert other._owner_token != relay._owner_token
    await relay.stop()


@pytest.mark.skipif(
    not sys.platform.startswith("linux")
    or not hasattr(os, "pidfd_open")
    or not hasattr(relay_mod.signal, "pidfd_send_signal"),
    reason="owner-token sweep is Linux-only",
)
def test_owner_sweep_kills_only_processes_carrying_its_token():
    relay = SupervisedRelayForward(SSHConfig(host_alias="box"), 41000)
    sleeper = [sys.executable, "-c", "import time; time.sleep(60)"]
    # An orphaned ProxyCommand child that outlived its ssh root.
    ours = subprocess.Popen(
        sleeper, env={**os.environ, relay_mod._OWNER_ENV: relay._owner_token},
    )
    stranger = subprocess.Popen(
        sleeper, env={**os.environ, relay_mod._OWNER_ENV: "someone-else"},
    )
    try:
        assert relay._kill_owned_descendants() == 1
        assert ours.wait(timeout=10) == -9
        assert stranger.poll() is None
    finally:
        for proc in (ours, stranger):
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)


async def test_teardown_sweeps_descendants_after_root_exited(monkeypatch):
    relay, spawned = _supervised(monkeypatch)
    await relay.establish()
    proc = spawned[0]
    proc.returncode = 255  # root gone; a ProxyCommand child may survive
    sweeps = []
    monkeypatch.setattr(
        SupervisedRelayForward, "_kill_owned_descendants",
        lambda self: sweeps.append(self) or 0,
    )

    await relay.stop()

    assert sweeps == [relay]
async def test_gated_reconnect_rechecks_gate_before_each_retry(monkeypatch):
    answers = [True, False, False]

    async def gate() -> bool:
        return answers.pop(0)

    relay, spawned = _supervised(monkeypatch, gate=gate)

    async def bind_fails(self, proc):
        return _SettleResult(False, "remote port forwarding failed", True)

    monkeypatch.setattr(SupervisedRelayForward, "_wait_settled", bind_fails)

    assert await relay._restart_with_backoff("process exited") is False

    assert len(spawned) == 1, "a stopped venue must not get a second attempt"
    assert relay.retired is True


def test_owner_sweep_is_a_noop_without_pidfd_send_signal(monkeypatch):
    relay = SupervisedRelayForward(SSHConfig(host_alias="box"), 41000)
    monkeypatch.delattr(relay_mod.signal, "pidfd_send_signal", raising=False)
    monkeypatch.setattr(
        relay_mod.os, "scandir",
        lambda *_a: (_ for _ in ()).throw(AssertionError("must not scan")),
    )

    assert relay._kill_owned_descendants() == 0


async def test_terminate_releases_owner_even_if_sweep_fails(monkeypatch):
    relay, spawned = _supervised(monkeypatch)
    await relay.establish()
    released = []

    def broken_sweep(self):
        raise RuntimeError("boom")

    async def fake_cleanup(proc, **_kw):
        released.append(proc)

    monkeypatch.setattr(SupervisedRelayForward, "_kill_owned_descendants", broken_sweep)
    monkeypatch.setattr(relay_mod, "run_process_cleanup", fake_cleanup)

    await relay.stop()

    assert released == [spawned[0]]
