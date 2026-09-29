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
