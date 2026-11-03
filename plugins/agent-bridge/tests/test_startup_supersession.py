"""Normal starts promptly and safely retire the daemon they supersede."""

from __future__ import annotations

import asyncio
import os
import threading
from dataclasses import replace
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


@pytest.fixture
def claimed_host(tmp_path, monkeypatch):
    from agent_bridge import self_retire
    from agent_bridge.db import Database
    from agent_bridge.models import SessionStatus
    from agent_bridge.session_host.host_index import HostRecord
    from agent_bridge.session_manager import session_manager_from_config

    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    cfg = ServiceConfig(
        db_path=str(tmp_path / "handoff.db"), enable_credential_relay=False,
    )
    db = Database(tmp_path / "handoff.db")
    manager = session_manager_from_config(db, cfg)
    record = HostRecord(
        session_id="preserved-host", port=41003, host_pid=os.getpid(),
        child_pid=os.getpid(), owner_generation=manager._generation_id,
        owner_pid=os.getpid(),
    )
    manager._host_index.register(record)
    monkeypatch.setattr(manager, "_rec_host_alive", lambda _: True)
    manager._sessions[record.session_id] = SimpleNamespace(
        session_id=record.session_id, status=SessionStatus.RUNNING,
        has_active_background_tasks=False, updated_at=0,
    )
    old = routing.publish_active(
        tmp_path, bind="127.0.0.1", port=41001, pid=os.getpid(), version="old",
    )
    generation = "0.4.1-dev3+source." + "a" * 40 + "." + "b" * 16
    current, _ = routing.publish_active_with_previous(
        tmp_path, bind="127.0.0.1", port=41002, pid=202, version=generation,
        demote_existing=True,
    )
    observe = self_retire.is_superseded
    monkeypatch.setattr(
        self_retire, "is_superseded",
        lambda *args: observe(*args, is_listening=lambda *_: True),
    )
    try:
        yield manager, db, cfg, record, old, current
    finally:
        db.close()


@pytest.mark.asyncio
async def test_source_self_retirement_preserves_live_hosts_and_releases_only_own_claim(claimed_host):
    from agent_bridge.app import _count_active_sessions
    from agent_bridge.app_supersession import release_own_host_claims, source_self_retire_ready
    from agent_bridge.self_retire import retire_check
    from agent_bridge.session_manager import session_manager_from_config

    manager, db, cfg, record, old, _ = claimed_host
    successor = session_manager_from_config(db, cfg)
    app = SimpleNamespace(state=SimpleNamespace(session_manager=manager))
    assert not successor._claim_host_record(record)
    assert retire_check(
        db.db_path.parent, os.getpid(), old.generation,
        lambda: _count_active_sessions(manager, db),
    )[:2] == (True, False)
    try:
        assert await source_self_retire_ready(
            app, old.generation, ready_successor=lambda _: True,
        ) is True
        assert manager.is_draining
        assert manager._live_host_records() == [record]
        assert not successor._claim_host_record(record)
        assert release_own_host_claims(manager) == [record.session_id]
        assert successor._claim_host_record(record)
        assert release_own_host_claims(manager) == []
        successor._host_index.refresh()
        retained = successor._host_index.get(record.session_id)
        assert retained.owner_generation == successor._generation_id
        assert (retained.host_pid, retained.child_pid) == (record.host_pid, record.child_pid)
    finally:
        manager.set_draining(False)
        await asyncio.sleep(0)


@pytest.mark.asyncio
@pytest.mark.parametrize("foreign_drain", [False, True])
async def test_source_self_retirement_waits_for_nonpreservable_work_and_undoes_only_own_drain(
    claimed_host, foreign_drain,
):
    from agent_bridge.app_supersession import source_self_retire_ready
    from agent_bridge.models import SessionStatus

    manager, db, _, _, old, current = claimed_host
    app = SimpleNamespace(state=SimpleNamespace(session_manager=manager))
    if foreign_drain:
        manager.set_draining(True, source="operator")
    else:
        manager._sessions["command"] = SimpleNamespace(
            session_id="command", status=SessionStatus.RUNNING,
            has_active_background_tasks=False, updated_at=0,
        )
    try:
        assert await source_self_retire_ready(
            app, old.generation, ready_successor=lambda _: True,
        ) is False
        assert manager.is_draining
        assert routing.restore_previous_if_owner(
            db.db_path.parent, pid=current.pid, generation=current.generation,
        )
        assert await source_self_retire_ready(
            app, old.generation, ready_successor=lambda _: True,
        ) is None
        assert manager.is_draining is foreign_drain
        if foreign_drain:
            assert manager.drain_status()["source"] == "operator"
    finally:
        manager.set_draining(False)
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_source_self_retirement_preserves_a_drain_owner_changed_during_preparation(
    claimed_host, monkeypatch,
):
    from agent_bridge.app_supersession import source_self_retire_ready

    manager, _, _, _, old, _ = claimed_host
    app = SimpleNamespace(state=SimpleNamespace(session_manager=manager))
    drain = manager.drain

    async def replaced(**options):
        result = await drain(**options)
        assert result["drained"]
        manager.set_draining(False)
        manager.set_draining(True, source="operator")
        return result

    monkeypatch.setattr(manager, "drain", replaced)
    try:
        assert await source_self_retire_ready(
            app, old.generation, ready_successor=lambda _: True,
        ) is False
        assert manager.drain_status()["source"] == "operator"
    finally:
        manager.set_draining(False)
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_source_self_retirement_rechecks_governance_before_opening_its_gate(
    claimed_host, monkeypatch,
):
    from agent_bridge import app as app_module
    from agent_bridge.app_supersession import source_self_retire_ready

    manager, _, _, _, old, _ = claimed_host
    app = SimpleNamespace(state=SimpleNamespace(session_manager=manager))
    calls = []

    async def blocked(_governance, checkpoint, *, loop_name):
        calls.append(checkpoint)
        return True

    monkeypatch.setattr(app_module, "_governance_backoff", blocked)
    assert await source_self_retire_ready(
        app, old.generation, ready_successor=lambda _: True,
    ) is False
    assert calls == ["pre-mutation:self-retire"]
    assert not manager.is_draining


@pytest.mark.asyncio
@pytest.mark.parametrize("birth,wait_error", [
    (None, False), ("original-birth", False), ("original-birth", True),
])
async def test_source_application_waits_for_self_retirement_without_old_control(
    tmp_path, monkeypatch, birth, wait_error,
):
    from agent_bridge import app as app_module
    from zdd import process_exit

    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    successor, previous = _publish_pair(tmp_path)
    previous = replace(previous, process_start_time=birth)
    calls = []

    class Reference:
        def wait(self, timeout):
            calls.append(("wait", timeout))
            if wait_error:
                raise OSError("native exit evidence unavailable")
            return True

        def close(self):
            calls.append(("close",))

    monkeypatch.setattr(process_exit, "open_process", lambda *_args, **_kwargs: Reference())

    async def recover(_app, owner, **options):
        assert owner == successor and options["predecessor_exited"] is True
        calls.append(("recover",))
        return 0

    monkeypatch.setattr(app_module, "_recover_owned_cold", recover)
    app = _app()
    app.state.independent_runtime_start = True
    await _retire_previous_daemon(
        app, previous, successor,
        make_client=lambda _: pytest.fail("application startup cannot control its predecessor"),
    )
    expected = [("wait", 1.0), ("close",)] if wait_error else [
        ("wait", 1.0), ("recover",), ("close",),
    ]
    assert calls == (expected if birth else [])


@pytest.mark.asyncio
async def test_source_retirement_cancellation_settles_native_wait_before_close(tmp_path, monkeypatch):
    from zdd import process_exit

    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    successor, previous = _publish_pair(tmp_path)
    previous = replace(previous, process_start_time="original-birth")
    entered, release = threading.Event(), threading.Event()
    calls = []

    class Reference:
        def wait(self, timeout):
            entered.set()
            assert release.wait(5)
            calls.append("wait-finished")
            return True

        def close(self):
            calls.append("close")

    monkeypatch.setattr(process_exit, "open_process", lambda *_args, **_kwargs: Reference())
    app = _app()
    app.state.independent_runtime_start = True
    task = asyncio.create_task(_retire_previous_daemon(
        app, previous, successor,
        make_client=lambda _: pytest.fail("source startup cannot control its predecessor"),
    ))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        await asyncio.sleep(.02)
        task.cancel()
        await asyncio.sleep(.02)
        assert not task.done() and calls == []
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
    assert calls == ["wait-finished", "close"]


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
