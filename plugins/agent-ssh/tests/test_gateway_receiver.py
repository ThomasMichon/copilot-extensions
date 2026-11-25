import asyncio
from collections.abc import Callable
from pathlib import Path
import subprocess
import threading
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock

import pytest
from ssh_manager.config_sources import SSHConfig

from agent_ssh import gateway_receiver as receiver
from agent_ssh import gateway_sshd
from agent_ssh.gateway_native import Observation
from test_gateway_native import Provider, key
from test_gateway_native_authorization import admitted
from test_gateway_sshd import config


async def reached(predicate: Callable[[], bool]) -> None:
    async def wait() -> None:
        while not predicate():
            await asyncio.sleep(0.001)
    await asyncio.wait_for(wait(), 3)


def fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **options: Any) -> SimpleNamespace:
    provider, gateway, scope = admitted(tmp_path)
    selected = config(tmp_path)
    selected.pop("policy")
    selected.pop("directory")
    selected.update(version=2, machine="receiver", native_gateway=scope["descriptor"])
    events: list[str] = []
    listener = SimpleNamespace(
        ready=Mock(return_value=True), process=SimpleNamespace(poll=lambda: None),
        close=Mock(side_effect=lambda: events.append("listener-close")),
    )
    def spawn(*args: Any, **kwargs: Any) -> SimpleNamespace:
        events.append("listener-start")
        return listener
    start = Mock(side_effect=spawn)
    apply = Mock(side_effect=lambda *a: events.append("membership"))
    monkeypatch.setattr(gateway_sshd, "start", start)
    monkeypatch.setattr(gateway_sshd, "prepare_update", apply)
    forwards: list[Any] = []

    class Forward:
        def __init__(self, transport: SSHConfig, port: int, **kwargs: Any) -> None:
            forwards.append((transport, port, kwargs))

        async def start(self) -> None:
            assert listener.ready.call_count
            events.append("forward-start")

        async def stop(self) -> None:
            events.append("forward-stop")

    monkeypatch.setattr(receiver, "SupervisedRelayForward", Forward)

    async def observe() -> Observation:
        return gateway.inspect()

    owner = receiver.NativeReceiver(
        selected, SSHConfig(host_alias="selected-gateway"), observe=observe,
        refresh_interval=0.01, **options,
    )
    return SimpleNamespace(
        owner=owner, provider=provider, gateway=gateway, selected=selected,
        listener=listener, start=start, apply=apply, events=events, forwards=forwards,
    )


def test_native_receiver_composes_the_existing_listener_reverse_and_refresh_owners(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = fixture(tmp_path, monkeypatch)

    async def exercise() -> None:
        stop = asyncio.Event()
        running = asyncio.create_task(selected.owner.run(stop))
        try:
            await reached(lambda: selected.apply.call_count >= 2)
            assert selected.owner.started.is_set()
            assert selected.owner.membership_current
            assert selected.owner.membership_observed_at is not None
            update = selected.apply.call_args.args[1]
            assert update.directory == selected.gateway.inspect().directory
            assert selected.events[:2] == ["listener-start", "forward-start"]
            transport, port, options = selected.forwards[0]
            assert transport.host_alias == "selected-gateway"
            assert port == selected.selected["native_gateway"]["relay_port"]
            assert options == {"remote_bind_host": "127.0.0.1", "host_port": selected.selected["port"]}
        finally:
            stop.set()
            await running
        assert selected.events[-2:] == ["forward-stop", "listener-close"]
        assert not selected.owner.running and selected.owner.listener is None
        assert not selected.owner.started.is_set()

    asyncio.run(exercise())


@pytest.mark.parametrize("failure", ["unavailable", "foreign", "untyped", "validation-timeout"])
def test_failed_membership_refresh_retains_listener_and_catches_up_later(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    selected = fixture(tmp_path, monkeypatch)
    observations = 0
    caught_up = False
    valid: Observation = selected.gateway.inspect()
    foreign = Provider()
    foreign.descriptor["host_key"] = key(23)
    calls: list[bool] = []

    def apply(*args: Any) -> None:
        if failure == "validation-timeout" and not caught_up:
            calls.append(True)
            raise subprocess.TimeoutExpired("sshd", 10)

    selected.apply.side_effect = apply

    async def observe() -> Observation:
        nonlocal observations
        observations += 1
        if observations == 1 or caught_up or failure == "validation-timeout":
            return valid
        calls.append(True)
        if failure == "unavailable":
            raise OSError("private provider error")
        if failure == "untyped":
            return cast(Observation, object())
        from agent_ssh.gateway_native import NativeGateway
        return NativeGateway(foreign, foreign.descriptor).inspect()

    selected.owner.observe = observe

    async def exercise() -> None:
        nonlocal caught_up
        stop = asyncio.Event()
        running = asyncio.create_task(selected.owner.run(stop))
        try:
            await reached(lambda: bool(calls) and not selected.owner.membership_current)
            if failure == "validation-timeout":
                assert selected.apply.call_count >= 1
            else:
                assert selected.apply.call_count == 0
            selected.listener.close.assert_not_called()
            assert selected.events.count("listener-start") == 1
            caught_up = True
            await reached(lambda: selected.owner.membership_current)
            assert selected.owner.membership_current
        finally:
            stop.set()
            await running

    asyncio.run(exercise())


def test_unattributed_listener_never_opens_reverse_and_is_retired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = fixture(tmp_path, monkeypatch, listener_timeout=0.01)
    selected.listener.ready.return_value = False

    async def exercise() -> None:
        with pytest.raises(TimeoutError, match="attribution"):
            await selected.owner.run(asyncio.Event())
        assert "forward-start" not in selected.events
        selected.listener.close.assert_called_once()

    asyncio.run(exercise())


def test_stop_during_native_spawn_joins_custody_before_retirement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = fixture(tmp_path, monkeypatch)
    spawning, release = threading.Event(), threading.Event()

    def start(*args: Any, **kwargs: Any) -> Any:
        spawning.set()
        assert release.wait(3)
        return selected.listener

    selected.start.side_effect = start

    async def exercise() -> None:
        stop = asyncio.Event()
        running = asyncio.create_task(selected.owner.run(stop))
        try:
            await reached(spawning.is_set)
            stop.set()
            await asyncio.sleep(0.01)
            assert not running.done()
            selected.listener.close.assert_not_called()
        finally:
            release.set()
            await running
        selected.listener.close.assert_called_once()
        assert "forward-start" not in selected.events

    asyncio.run(exercise())


def test_cancellation_during_membership_write_joins_writer_before_child_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = fixture(tmp_path, monkeypatch)
    writing, release = threading.Event(), threading.Event()

    def write(*args: Any) -> None:
        writing.set()
        assert release.wait(3)
        selected.events.append("write-settled")

    selected.apply.side_effect = write

    async def exercise() -> None:
        running = asyncio.create_task(selected.owner.run(asyncio.Event()))
        try:
            await reached(writing.is_set)
            running.cancel()
            await asyncio.sleep(0.01)
            assert not running.done()
            selected.listener.close.assert_not_called()
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await running
        assert selected.events[-3:] == ["write-settled", "forward-stop", "listener-close"]

    asyncio.run(exercise())


def test_cleanup_failure_retains_custody_and_prevents_reentry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = fixture(tmp_path, monkeypatch)
    selected.listener.close.side_effect = OSError("owned child retirement unconfirmed")

    async def exercise() -> None:
        stop = asyncio.Event()
        running = asyncio.create_task(selected.owner.run(stop))
        await reached(lambda: "forward-start" in selected.events)
        stop.set()
        with pytest.raises(OSError, match="retirement"):
            await running
        assert selected.owner.listener is selected.listener
        assert selected.owner.running
        with pytest.raises(RuntimeError, match="already running"):
            await selected.owner.run(asyncio.Event())
        selected.listener.close.side_effect = None
        await selected.owner.close()
        assert not selected.owner.running and selected.owner.listener is None

    asyncio.run(exercise())


def test_initial_observation_failure_has_no_listener_or_forward_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = fixture(tmp_path, monkeypatch)

    async def unavailable() -> object:
        raise OSError("selected authenticated transport unavailable")

    selected.owner.observe = unavailable

    async def exercise() -> None:
        with pytest.raises(OSError, match="transport unavailable"):
            await selected.owner.run(asyncio.Event())
        selected.start.assert_not_called()
        assert selected.events == ["forward-stop"]

    asyncio.run(exercise())


@pytest.mark.parametrize("cancel", [False, True])
def test_stop_during_blocked_reverse_start_cancels_and_joins_before_listener_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancel: bool,
) -> None:
    selected = fixture(tmp_path, monkeypatch)

    async def exercise() -> None:
        entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def start() -> None:
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release.wait()
                selected.events.append("startup-settled")

        selected.owner.forward.start = start
        stop = asyncio.Event()
        running = asyncio.create_task(selected.owner.run(stop))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            if cancel:
                running.cancel()
            else:
                stop.set()
            await asyncio.wait_for(cleaning.wait(), 1)
            assert not running.done()
            selected.listener.close.assert_not_called()
        finally:
            release.set()
            if cancel:
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(running, 1)
            else:
                await asyncio.wait_for(running, 1)
        assert selected.events[-3:] == ["startup-settled", "forward-stop", "listener-close"]

    asyncio.run(exercise())
