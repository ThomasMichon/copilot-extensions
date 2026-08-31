"""Normal starts promptly and safely retire the daemon they supersede."""

from __future__ import annotations

import asyncio
import os
import threading
from types import SimpleNamespace

import pytest

from agent_bridge.app import _retire_previous_daemon, create_app
from agent_bridge.client import BridgeConnectionError
from agent_bridge.models import ServiceConfig
from zdd import routing


class _Client:
    def __init__(
        self,
        drain_result=None,
        *,
        on_drain=None,
        drain_error=None,
        shutdown_error=None,
        undrain_error=None,
    ):
        self.drain_result = drain_result or {"drained": True}
        self.on_drain = on_drain
        self.drain_error = drain_error
        self.shutdown_error = shutdown_error
        self.undrain_error = undrain_error
        self.drain_calls = []
        self.shutdown_calls = 0
        self.undrain_calls = 0

    def drain(self, **kwargs):
        self.drain_calls.append(kwargs)
        if self.on_drain is not None:
            self.on_drain()
        if self.drain_error is not None:
            raise self.drain_error
        return self.drain_result

    def shutdown(self):
        self.shutdown_calls += 1
        if self.shutdown_error is not None:
            raise self.shutdown_error
        return {"shutting_down": True}

    def undrain(self):
        self.undrain_calls += 1
        if self.undrain_error is not None:
            raise self.undrain_error
        return {"draining": False}


def _app():
    return SimpleNamespace(state=SimpleNamespace(auth_token="test-token"))


def _publish_pair(tmp_path):
    old = routing.publish_active(
        tmp_path,
        bind="127.0.0.1",
        port=41001,
        pid=101,
        version="old",
    )
    successor, previous = routing.publish_active_with_previous(
        tmp_path,
        bind="127.0.0.1",
        port=41002,
        pid=202,
        version="new",
        demote_existing=True,
    )
    assert previous == old
    return successor, previous


@pytest.mark.asyncio
async def test_idle_predecessor_is_drained_without_force_then_shutdown(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    successor, previous = _publish_pair(tmp_path)
    client = _Client()

    await _retire_previous_daemon(
        _app(), previous, successor, make_client=lambda _ep: client
    )

    assert client.drain_calls == [{
        "timeout": 30.0,
        "poll": 0.25,
        "force": False,
        "source": "startup-supersession",
        "reason": "superseded by a normal daemon start",
    }]
    assert client.shutdown_calls == 1
    assert client.undrain_calls == 0


@pytest.mark.asyncio
async def test_busy_predecessor_is_not_shutdown_before_drain_completes(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    successor, previous = _publish_pair(tmp_path)
    drain_started = threading.Event()
    allow_drain = threading.Event()

    def wait_for_safe_boundary():
        drain_started.set()
        assert allow_drain.wait(2)

    client = _Client(on_drain=wait_for_safe_boundary)
    task = asyncio.create_task(
        _retire_previous_daemon(
            _app(), previous, successor, make_client=lambda _ep: client
        )
    )

    assert await asyncio.to_thread(drain_started.wait, 1)
    assert client.shutdown_calls == 0
    allow_drain.set()
    await task
    assert client.shutdown_calls == 1


@pytest.mark.asyncio
async def test_unreachable_predecessor_is_left_to_self_retire_backstop(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    successor, previous = _publish_pair(tmp_path)
    client = _Client(
        drain_error=BridgeConnectionError("unreachable"),
        undrain_error=BridgeConnectionError("unreachable"),
    )

    await _retire_previous_daemon(
        _app(), previous, successor, make_client=lambda _ep: client
    )

    assert client.shutdown_calls == 0
    assert client.undrain_calls == 1


@pytest.mark.asyncio
async def test_timed_out_predecessor_is_undrained(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    successor, previous = _publish_pair(tmp_path)
    client = _Client(drain_result={"drained": False})

    await _retire_previous_daemon(
        _app(), previous, successor, make_client=lambda _ep: client
    )

    assert client.shutdown_calls == 0
    assert client.undrain_calls == 1


@pytest.mark.asyncio
async def test_failed_shutdown_releases_predecessor_drain(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    successor, previous = _publish_pair(tmp_path)
    client = _Client(
        shutdown_error=BridgeConnectionError("shutdown response lost")
    )

    await _retire_previous_daemon(
        _app(), previous, successor, make_client=lambda _ep: client
    )

    assert client.shutdown_calls == 1
    assert client.undrain_calls == 1


@pytest.mark.asyncio
async def test_lost_startup_ownership_skips_predecessor_drain(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    successor, previous = _publish_pair(tmp_path)
    routing.publish_active(
        tmp_path,
        bind="127.0.0.1",
        port=41003,
        pid=303,
        version="newer",
        demote_existing=True,
    )
    client = _Client()

    await _retire_previous_daemon(
        _app(), previous, successor, make_client=lambda _ep: client
    )

    assert client.drain_calls == []
    assert client.shutdown_calls == 0


@pytest.mark.asyncio
async def test_restored_predecessor_is_undrained_instead_of_shutdown(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    successor, previous = _publish_pair(tmp_path)

    def restore_previous():
        routing.publish_active(
            tmp_path,
            bind=previous.bind,
            port=previous.port,
            pid=previous.pid,
            version=previous.version,
            demote_existing=True,
        )

    client = _Client(on_drain=restore_previous)

    await _retire_previous_daemon(
        _app(), previous, successor, make_client=lambda _ep: client
    )

    assert client.shutdown_calls == 0
    assert client.undrain_calls == 1


@pytest.mark.asyncio
async def test_newer_successor_does_not_block_retiring_two_generations_back(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    successor, previous = _publish_pair(tmp_path)

    def publish_newer():
        routing.publish_active(
            tmp_path,
            bind="127.0.0.1",
            port=41003,
            pid=303,
            version="newer",
            demote_existing=True,
        )

    client = _Client(on_drain=publish_newer)

    await _retire_previous_daemon(
        _app(), previous, successor, make_client=lambda _ep: client
    )

    assert client.shutdown_calls == 1
    assert client.undrain_calls == 0


@pytest.mark.asyncio
async def test_successor_shutdown_finishes_inflight_predecessor_handshake(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("AGENT_BRIDGE_SELF_RETIRE", "0")
    monkeypatch.setenv(
        "AGENT_WORKTREES_PROJECTS_YAML", str(tmp_path / "none.yaml")
    )
    routing.publish_active(
        tmp_path,
        bind="127.0.0.1",
        port=42001,
        pid=os.getpid(),
        version="old",
    )
    drain_started = threading.Event()
    allow_drain = threading.Event()

    def wait_for_safe_boundary():
        drain_started.set()
        assert allow_drain.wait(2)

    predecessor = _Client(on_drain=wait_for_safe_boundary)
    cfg = ServiceConfig(
        port=0,
        bind="127.0.0.1",
        db_path=str(tmp_path / "test.db"),
        enable_credential_relay=False,
    )
    app = create_app(config=cfg, token="test-token")
    app.state.bound_port = 42002
    app.state.publish_on_ready = True
    app.state.supersession_client_factory = lambda _ep: predecessor

    lifespan = app.router.lifespan_context(app)
    await lifespan.__aenter__()
    assert await asyncio.to_thread(drain_started.wait, 1)

    exit_task = asyncio.create_task(lifespan.__aexit__(None, None, None))
    await asyncio.sleep(0.05)
    assert not exit_task.done()
    assert predecessor.shutdown_calls == 0

    allow_drain.set()
    await exit_task
    assert predecessor.shutdown_calls == 1


@pytest.mark.asyncio
async def test_cancelled_lifespan_still_finishes_predecessor_handshake(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("AGENT_BRIDGE_SELF_RETIRE", "0")
    monkeypatch.setenv(
        "AGENT_WORKTREES_PROJECTS_YAML", str(tmp_path / "none.yaml")
    )
    routing.publish_active(
        tmp_path,
        bind="127.0.0.1",
        port=43001,
        pid=os.getpid(),
        version="old",
    )
    drain_started = threading.Event()
    allow_drain = threading.Event()

    def wait_for_safe_boundary():
        drain_started.set()
        assert allow_drain.wait(2)

    predecessor = _Client(on_drain=wait_for_safe_boundary)
    cfg = ServiceConfig(
        port=0,
        bind="127.0.0.1",
        db_path=str(tmp_path / "test.db"),
        enable_credential_relay=False,
    )
    app = create_app(config=cfg, token="test-token")
    app.state.bound_port = 43002
    app.state.publish_on_ready = True
    app.state.supersession_client_factory = lambda _ep: predecessor

    lifespan = app.router.lifespan_context(app)
    await lifespan.__aenter__()
    assert await asyncio.to_thread(drain_started.wait, 1)

    exit_task = asyncio.create_task(lifespan.__aexit__(None, None, None))
    await asyncio.sleep(0.05)
    exit_task.cancel()
    await asyncio.sleep(0.05)
    assert not exit_task.done()
    assert predecessor.shutdown_calls == 0

    allow_drain.set()
    await exit_task
    assert predecessor.shutdown_calls == 1
