"""Tests for the dedicated local-forward helper (``ssh -N -L``)."""

from __future__ import annotations

import socket

import pytest

from ssh_manager import LocalForward, build_forward_ssh_args, pick_free_local_port
from ssh_manager.config_sources import SSHConfig


class TestBuildForwardArgs:
    def test_basic_shape(self) -> None:
        cfg = SSHConfig(host_alias="cs.foo", user="vscode",
                        config_file="/tmp/cs.config")
        args = build_forward_ssh_args(cfg, 49222, 51000)
        assert args[0] == "ssh"
        assert "-F" in args and "/tmp/cs.config" in args
        assert "-N" in args
        assert "-L" in args
        li = args.index("-L")
        assert args[li + 1] == "127.0.0.1:49222:127.0.0.1:51000"
        assert args[-1] == "vscode@cs.foo"
        joined = " ".join(args)
        assert "ExitOnForwardFailure=yes" in joined

    def test_drops_control_master_options(self) -> None:
        cfg = SSHConfig(
            host_alias="cs.foo",
            extra_options={"ControlMaster": "auto", "StrictHostKeyChecking": "no"},
        )
        args = build_forward_ssh_args(cfg, 1, 2)
        joined = " ".join(args)
        assert "ControlMaster" not in joined
        assert "StrictHostKeyChecking=no" in joined

    def test_custom_remote_host_and_extra_opts(self) -> None:
        cfg = SSHConfig(host_alias="box")
        args = build_forward_ssh_args(
            cfg, 5, 6, remote_host="localhost",
            extra_options={"LogLevel": "quiet"},
        )
        li = args.index("-L")
        assert args[li + 1] == "127.0.0.1:5:localhost:6"
        assert "LogLevel=quiet" in " ".join(args)

    def test_reverse_forwards_carried(self) -> None:
        cfg = SSHConfig(host_alias="box")
        args = build_forward_ssh_args(
            cfg, 5, 6, reverse_forwards=["51234:127.0.0.1:51234"],
        )
        ri = args.index("-R")
        assert args[ri + 1] == "51234:127.0.0.1:51234"
        # -R comes before the target
        assert args.index("-R") < args.index("box")
        # ExitOnForwardFailure is dropped when a -R relay is present so a relay
        # bind collision cannot tear down the -L endpoint.
        assert "ExitOnForwardFailure=yes" not in " ".join(args)


class TestPickFreePort:
    def test_returns_bindable_port(self) -> None:
        port = pick_free_local_port()
        assert 1 <= port <= 65535
        # Re-bindable right after (the picker released it).
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", port))
        finally:
            s.close()


class _FakeProc:
    def __init__(self, returncode: int | None = None) -> None:
        self.returncode = returncode
        self.killed = False
        self.stderr = None

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode if self.returncode is not None else 0


@pytest.mark.asyncio
class TestLocalForwardLifecycle:
    async def test_establish_success(self, monkeypatch) -> None:
        proc = _FakeProc(returncode=None)

        async def fake_spawn(*a, **k):
            return proc

        monkeypatch.setattr("ssh_manager.forward.asyncio.create_subprocess_exec",
                            fake_spawn)
        monkeypatch.setattr(LocalForward, "_port_accepts",
                            staticmethod(lambda port: _true()))

        fwd = LocalForward(SSHConfig(host_alias="box"), 51000, local_port=49999)
        port = await fwd.establish()
        assert port == 49999
        assert fwd.is_alive

    async def test_establish_retries_then_succeeds(self, monkeypatch) -> None:
        # First spawned proc "dies" (returncode set) so establish retries.
        procs = [_FakeProc(returncode=1), _FakeProc(returncode=None)]
        calls = {"n": 0}

        async def fake_spawn(*a, **k):
            p = procs[calls["n"]]
            calls["n"] += 1
            return p

        accepts = {"n": 0}

        async def fake_accept(port):
            # Only the second (live) proc's port accepts.
            accepts["n"] += 1
            return calls["n"] == 2

        monkeypatch.setattr("ssh_manager.forward.asyncio.create_subprocess_exec",
                            fake_spawn)
        monkeypatch.setattr(LocalForward, "_port_accepts",
                            staticmethod(fake_accept))
        monkeypatch.setattr(LocalForward, "_drain_stderr",
                            staticmethod(lambda p: _empty()))

        fwd = LocalForward(SSHConfig(host_alias="box"), 51000, ready_timeout=2.0)
        port = await fwd.establish()
        assert port > 0
        assert calls["n"] == 2  # retried once

    async def test_establish_raises_when_never_ready(self, monkeypatch) -> None:
        async def fake_spawn(*a, **k):
            return _FakeProc(returncode=None)

        async def never(port):
            return False

        monkeypatch.setattr("ssh_manager.forward.asyncio.create_subprocess_exec",
                            fake_spawn)
        monkeypatch.setattr(LocalForward, "_port_accepts", staticmethod(never))
        monkeypatch.setattr(LocalForward, "_drain_stderr",
                            staticmethod(lambda p: _text("boom")))

        fwd = LocalForward(SSHConfig(host_alias="box"), 51000,
                           local_port=49998, ready_timeout=0.3)
        with pytest.raises(ConnectionError):
            await fwd.establish()

    async def test_refresh_reuses_port(self, monkeypatch) -> None:
        async def fake_spawn(*a, **k):
            return _FakeProc(returncode=None)

        monkeypatch.setattr("ssh_manager.forward.asyncio.create_subprocess_exec",
                            fake_spawn)
        monkeypatch.setattr(LocalForward, "_port_accepts",
                            staticmethod(lambda port: _true()))

        fwd = LocalForward(SSHConfig(host_alias="box"), 51000)
        p1 = await fwd.establish()
        p2 = await fwd.refresh()
        assert p1 == p2

    async def test_cancel_idempotent(self, monkeypatch) -> None:
        async def fake_spawn(*a, **k):
            return _FakeProc(returncode=None)

        monkeypatch.setattr("ssh_manager.forward.asyncio.create_subprocess_exec",
                            fake_spawn)
        monkeypatch.setattr(LocalForward, "_port_accepts",
                            staticmethod(lambda port: _true()))

        fwd = LocalForward(SSHConfig(host_alias="box"), 51000, local_port=49997)
        await fwd.establish()
        await fwd.cancel()
        assert not fwd.is_alive
        await fwd.cancel()  # no raise


async def _true() -> bool:
    return True


async def _empty() -> str:
    return ""


async def _text(s: str) -> str:
    return s
