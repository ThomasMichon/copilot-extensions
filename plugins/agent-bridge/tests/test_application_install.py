from __future__ import annotations

import asyncio
import sys
import importlib.metadata
import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, Literal
from collections.abc import Callable
import socket

import pytest
from fastapi import FastAPI

import agent_bridge
from agent_bridge import __main__ as main
from agent_bridge.serving_reconcile import ServingReconcileError
from zdd import diagnostics
from zdd.routing import Endpoint

pytestmark = pytest.mark.guard
Runtime = tuple[Path, SimpleNamespace, SimpleNamespace, ModuleType]


def _app(**state: Any) -> FastAPI:
    app = FastAPI()
    for key, value in state.items():
        setattr(app.state, key, value)
    return app


@pytest.mark.parametrize("ready", [True, False])
def test_application_start_uses_installed_callback_and_retains_failure(
    monkeypatch: pytest.MonkeyPatch, ready: bool,
) -> None:
    calls = []

    def start() -> bool:
        calls.append("start")
        return ready

    monkeypatch.setitem(
        sys.modules, "agent_bridge.source_start", SimpleNamespace(start_installed_slot=start),
    )
    arguments = main.build_parser().parse_args(["__source-start"])
    if ready:
        arguments.func(arguments)
    else:
        with pytest.raises(SystemExit) as error:
            arguments.func(arguments)
        assert error.value.code == 1
    assert calls == ["start"]


GENERATION = "0.4.1-dev3+source." + "a" * 40 + "." + "b" * 16 + ".profile." + "c" * 16


@pytest.fixture
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Runtime:
    from agent_bridge import config, source_start

    (tmp_path / "current-version").write_text(GENERATION, encoding="utf-8")
    state = SimpleNamespace(endpoint=None, ready=True, launches=[], releases=[])

    def health(_port: int, *, host: str) -> dict[str, Any]:
        endpoint = state.endpoint
        return {
            "version": endpoint.version, "ready": state.ready,
            "slot": {"pid": endpoint.pid, "role": "active", "active": endpoint.to_dict()},
        }

    def launch(argv: list[str], **options: Any) -> SimpleNamespace:
        state.launches.append((argv, options))
        state.endpoint = Endpoint(
            bind="127.0.0.1", port=int(argv[-1]), pid=20, generation=2,
            version=GENERATION, process_start_time="20",
        )
        return SimpleNamespace(pid=20)

    core = SimpleNamespace(
        _INSTALL_DIR=str(tmp_path / "separate-configuration"), _active_endpoint=lambda: state.endpoint,
        _active_endpoint_is_forward=lambda: False, _service_health_on_port=health,
        _acquire_ensure_lock=lambda: 3, _release_ensure_lock=state.releases.append,
        _daemon_launch_argv=lambda: ["python", "-m", "agent_bridge", "start"],
        _launch_bridge_daemon=launch,
    )
    probe = SimpleNamespace(bind=lambda _: None, getsockname=lambda: ("127.0.0.1", 32001))

    class Socket:
        def __enter__(self) -> SimpleNamespace:
            return probe

        def __exit__(self, *_args: object) -> Literal[False]:
            return False

    monkeypatch.setattr(socket, "socket", lambda *_: Socket())
    monkeypatch.setattr(source_start, "_core", lambda: core)
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: str(pid))
    monkeypatch.setattr(config, "load_config", lambda: SimpleNamespace(bind="127.0.0.1"))
    monkeypatch.setenv("AGENT_BRIDGE_INSTALL_DIR", str(tmp_path))
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", core._INSTALL_DIR)
    monkeypatch.setattr(agent_bridge, "__version__", GENERATION)
    monkeypatch.delenv("AGENT_BRIDGE_NO_ENSURE", raising=False)
    return tmp_path, state, core, source_start


def test_selected_source_creates_separate_configuration_before_real_ensure_lock(
    runtime: Runtime, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os
    from agent_bridge.service_process_cli import _acquire_ensure_lock, _release_ensure_lock

    root, state, core, source_start = runtime
    configuration = Path(core._INSTALL_DIR)
    lock = configuration / "ensure.lock"
    monkeypatch.setattr(main, "_ENSURE_LOCK", str(lock))
    core._acquire_ensure_lock = _acquire_ensure_lock
    core._release_ensure_lock = _release_ensure_lock
    launch = core._launch_bridge_daemon

    def launch_with_real_lock(argv: list[str], **options: Any) -> Any:
        assert lock.read_text() == str(os.getpid())
        return launch(argv, **options)

    core._launch_bridge_daemon = launch_with_real_lock
    assert not configuration.exists()
    assert source_start.start_selected_source(timeout=.1)
    assert len(state.launches) == 1
    assert configuration.is_dir() and not lock.exists()
    assert (root / "current-version").read_text() == GENERATION


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("root_override", [False, True])
def test_installed_application_starts_its_slot_not_the_healthy_predecessor(
    runtime: Runtime, monkeypatch: pytest.MonkeyPatch, nested: bool, root_override: bool,
) -> None:
    from agent_bridge import install_paths

    root, state, core, source_start = runtime
    slot = root / "versions" / GENERATION
    prefix = slot / ".venv" if nested else slot
    path = prefix / "lib" / "agent_bridge" / "source_start.py"
    path.parent.mkdir(parents=True)
    assert source_start.__file__ is not None
    path.write_bytes(Path(source_start.__file__).read_bytes())
    spec = importlib.util.spec_from_file_location("agent_bridge._application_callback_test", path)
    assert spec is not None and spec.loader is not None
    callback = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(callback)
    monkeypatch.setattr(callback, "_core", lambda: core)
    monkeypatch.setattr(sys, "prefix", str(prefix))
    monkeypatch.setattr(install_paths, "__file__", str(path.parent / "install_paths.py"))
    if not root_override:
        monkeypatch.delenv("AGENT_BRIDGE_INSTALL_DIR")
    original = importlib.metadata.version
    monkeypatch.setattr(
        importlib.metadata, "version",
        lambda name: "0.4.1.dev3" if name == "agent-bridge" else original(name),
    )
    state.endpoint = Endpoint(
        bind="127.0.0.1", port=32000, pid=10, generation=1,
        version="9.0.0", process_start_time="10",
    )
    assert callback.start_installed_slot()
    assert len(state.launches) == 1 and state.releases == [3]
    assert state.launches[0][1]["contained_last_resort"] is False
    monkeypatch.setenv("AGENT_BRIDGE_INSTALL_DIR", str(root / "foreign"))
    with pytest.raises(ServingReconcileError, match="installation root differs"):
        callback.start_installed_slot()
    assert len(state.launches) == 1


def test_selected_but_unready_application_waits_without_duplicate_launch(
    runtime: Runtime, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, state, _, source_start = runtime
    state.endpoint = Endpoint(
        bind="127.0.0.1", port=32001, pid=20, generation=2,
        version=GENERATION, process_start_time="20",
    )
    state.ready = False
    assert not source_start.start_selected_source(timeout=0)
    assert state.launches == []
    monkeypatch.setattr(source_start.time, "sleep", lambda _: setattr(state, "ready", True))
    assert source_start.start_selected_source()
    assert state.launches == []


def test_displaced_or_forwarded_selection_cannot_start_local_application(runtime: Runtime) -> None:
    root, state, core, source_start = runtime
    core._active_endpoint_is_forward = lambda: True
    assert not source_start.start_selected_source()
    core._active_endpoint_is_forward = lambda: False
    (root / "current-version").write_text("different-generation", encoding="utf-8")
    with pytest.raises(ServingReconcileError, match="no longer selected"):
        source_start.start_selected_source()
    assert state.launches == [] and state.releases == []


@pytest.mark.parametrize("selected", [True, False])
@pytest.mark.parametrize("root_override", [False, True])
def test_source_route_uses_installation_selector_not_separate_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selected: bool, root_override: bool,
) -> None:
    import os

    from agent_bridge import install_paths
    from agent_bridge.routing_state import publish_daemon_route_unless_forwarded
    from zdd import routing

    root, config = tmp_path / "installation", tmp_path / "configuration"
    root.mkdir()
    (root / "current-version").write_text(
        GENERATION if selected else "different-generation", encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_BRIDGE_INSTALL_DIR", str(root))
    if not root_override:
        prefix = root / "versions" / GENERATION / ".venv"
        monkeypatch.setattr(sys, "prefix", str(prefix))
        monkeypatch.setattr(install_paths, "__file__", str(prefix / "agent_bridge" / "install_paths.py"))
        monkeypatch.delenv("AGENT_BRIDGE_INSTALL_DIR")
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(config))
    previous, _ = publish_daemon_route_unless_forwarded(
        config, bind="127.0.0.1", port=32000, pid=os.getpid(), version="0.4.1",
    )
    if selected:
        current, _ = publish_daemon_route_unless_forwarded(
            config, bind="127.0.0.1", port=32001, pid=os.getpid(), version=GENERATION,
        )
        assert current.version == GENERATION
    else:
        with pytest.raises(ServingReconcileError, match="no longer selected"):
            publish_daemon_route_unless_forwarded(
                config, bind="127.0.0.1", port=32001, pid=os.getpid(), version=GENERATION,
            )
        assert (routing.read_table(config) or {})["active"] == previous.to_dict()


async def _eventually(predicate: Callable[[], bool]) -> None:
    async def wait() -> None:
        while not predicate():
            await asyncio.sleep(.01)

    await asyncio.wait_for(wait(), 5)


@pytest.mark.asyncio
@pytest.mark.parametrize("displaced", [False, True])
@pytest.mark.parametrize("relay_enabled", [False, True])
async def test_source_lifespan_defers_route_identity_and_attachment_until_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, displaced: bool, relay_enabled: bool,
) -> None:
    from agent_bridge import app as app_module, runtime_version
    from agent_bridge.agent_registry_resolver import AgentResolver
    from agent_bridge.models import ServiceConfig
    from agent_bridge.db import Database
    from agent_bridge.session_manager import SessionManager
    from zdd import routing

    root = tmp_path
    (root / "current-version").write_text(GENERATION, encoding="utf-8")
    monkeypatch.setattr(agent_bridge, "__version__", GENERATION)
    monkeypatch.setenv("AGENT_BRIDGE_INSTALL_DIR", str(root))
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(root))
    monkeypatch.setenv("AGENT_BRIDGE_SELF_RETIRE", "0")
    monkeypatch.setenv("AGENT_WORKTREES_PROJECTS_YAML", str(root / "absent.yaml"))
    monkeypatch.setattr(runtime_version, "__version__", GENERATION)
    previous = routing.publish_active(
        root, bind="127.0.0.1", port=32000, pid=10, version="previous",
    )
    marker = root / runtime_version.RUNNING_VERSION_FILE
    marker.write_text('{"pid":10,"version":"previous"}', encoding="utf-8")
    previous_identity = marker.read_bytes()
    seed = Database(root / "bridge.db")
    seed.create_session(
        "incumbent-work", "work", None, str(root), "local", "running", 1,
        acp_session_id="incumbent-acp",
    )
    seed.create_turn("incumbent-work", 1, "unfinished work", 1)
    seed.close()
    ready, attached, exited = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls: list[tuple[Any, ...]] = []
    listeners: list[bool] = []
    maintenance: list[str] = []
    beats = 0

    async def recover_cold(*_args: Any, **_kwargs: Any) -> int:
        nonlocal beats
        beats += 1
        return 0

    async def maintain(_self: SessionManager) -> None:
        maintenance.append("session")

    monkeypatch.setattr(app_module, "_HEARTBEAT_INTERVAL_S", .01)
    monkeypatch.setattr(app_module, "_recover_owned_cold", recover_cold)
    monkeypatch.setattr(
        SessionManager, "note_heartbeats", lambda _self: maintenance.append("heartbeat"),
    )
    for method in (
        "recover_disconnected_hosts", "refresh_host_reapable", "reconcile_wedged_running",
    ):
        monkeypatch.setattr(SessionManager, method, maintain)

    async def resolver_work(function: Callable[..., Any], *args: Any) -> AgentResolver:
        assert function is app_module.daemon_resolver
        await ready.wait()
        return AgentResolver({}, {})

    async def reattach(self: SessionManager, *, recover_cold: bool) -> int:
        calls.append(("attach", recover_cold))
        attached.set()
        return 0

    async def relay(_app: FastAPI, *, start_listener: bool = True) -> None:
        listeners.append(start_listener)
        return None

    async def retire(
        app: FastAPI, old: Endpoint, current: Endpoint, *,
        initial_recovery_task: asyncio.Task[Any],
    ) -> None:
        assert old == previous and current.version == GENERATION
        await initial_recovery_task
        calls.append(("retire",))
        await exited.wait()
        app.state.cold_recovery_ready = True

    monkeypatch.setattr(app_module, "_run_in_daemon_thread", resolver_work)
    monkeypatch.setattr(app_module, "_start_credential_relay", relay)
    monkeypatch.setattr(app_module, "_retire_previous_daemon", retire)
    monkeypatch.setattr(SessionManager, "reattach_session_hosts", reattach)
    app = app_module.create_app(config=ServiceConfig(
        bind="127.0.0.1", port=0, db_path=str(root / "bridge.db"),
        enable_credential_relay=relay_enabled,
    ), token="test-token")
    app.state.bound_port = 32001
    app.state.publish_on_ready = True
    app.state.background_readiness = True
    app.state.independent_runtime_start = True
    app.state.uvicorn_server = SimpleNamespace(started=False, should_exit=False)
    context = app.router.lifespan_context(app)
    await context.__aenter__()
    try:
        await _eventually(lambda: beats >= 2)
        assert maintenance == []
        assert app.state.db.get_session("incumbent-work")["status"] == "running"
        assert app.state.db.get_turns("incumbent-work")[0]["completed_at"] is None
        assert (routing.read_table(root) or {})["active"] == previous.to_dict()
        assert marker.read_bytes() == previous_identity and calls == []
        assert not app.state.cold_recovery_ready
        ready.set()
        await _eventually(lambda: app.state.ready)
        assert listeners == [False]
        assert app.state.db.get_session("incumbent-work")["status"] == "running"
        assert app.state.db.get_turns("incumbent-work")[0]["completed_at"] is None
        assert (routing.read_table(root) or {})["active"] == previous.to_dict()
        assert marker.read_bytes() == previous_identity and calls == []
        if displaced:
            (root / "current-version").write_text("rolled-back", encoding="utf-8")
        app.state.uvicorn_server.started = True
        if displaced:
            await _eventually(lambda: app.state.uvicorn_server.should_exit)
            assert (routing.read_table(root) or {})["active"] == previous.to_dict()
            assert marker.read_bytes() == previous_identity and calls == []
        else:
            await asyncio.wait_for(attached.wait(), 5)
            await _eventually(lambda: ("retire",) in calls)
            overlap_beat = beats
            await _eventually(lambda: beats >= overlap_beat + 2)
            assert maintenance == []
            assert app.state.db.get_session("incumbent-work")["status"] == "running"
            assert app.state.db.get_turns("incumbent-work")[0]["completed_at"] is None
            assert (routing.read_table(root) or {})["active"]["version"] == GENERATION
            assert calls == [("attach", False), ("retire",)]
            assert not app.state.cold_recovery_ready
            assert listeners == [False]
            import json
            identity = json.loads(marker.read_text(encoding="utf-8"))
            assert identity["version"] == GENERATION
            assert identity["generation_id"] == app.state.session_manager._generation_id
            exited.set()
            await _eventually(lambda: maintenance.count("session") >= 3)
            assert "heartbeat" in maintenance
    finally:
        ready.set()
        exited.set()
        await context.__aexit__(None, None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("predecessor", [False, True])
async def test_source_adopts_tcp_relay_only_after_fresh_start_or_predecessor_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, predecessor: bool,
) -> None:
    from agent_bridge import app as app_module, runtime_version, source_start
    from zdd import routing

    monkeypatch.setattr(agent_bridge, "__version__", GENERATION)
    monkeypatch.setattr(runtime_version, "__version__", GENERATION)
    monkeypatch.setenv("AGENT_BRIDGE_INSTALL_DIR", str(tmp_path))
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    (tmp_path / "current-version").write_text(GENERATION, encoding="utf-8")
    if predecessor:
        routing.publish_active(tmp_path, bind="127.0.0.1", port=32000, pid=101, version="old")
    calls = []
    waiting, exited = asyncio.Event(), asyncio.Event()

    async def retire(
        app: FastAPI, _previous: Endpoint, _current: Endpoint, *,
        initial_recovery_task: asyncio.Task[Any],
    ) -> None:
        await initial_recovery_task
        calls.append("retire")
        waiting.set()
        await exited.wait()
        app.state.cold_recovery_ready = True

    async def reattach(**_kwargs: Any) -> None:
        calls.append("reattach")

    async def adopt_relay() -> None:
        calls.append("relay")

    monkeypatch.setattr(app_module, "_retire_previous_daemon", retire)
    app = _app(
        uvicorn_server=SimpleNamespace(started=True, should_exit=False),
        ready=True, shutting_down=False, bound_port=32001,
        config=SimpleNamespace(bind="127.0.0.1", enable_credential_relay=True),
        session_manager=SimpleNamespace(_generation_id="new", reattach_session_hosts=reattach),
        adopt_relay=adopt_relay,
    )
    initial = asyncio.create_task(asyncio.sleep(0))
    task = asyncio.create_task(source_start.complete_source_start(app, asyncio.Event(), initial))
    try:
        if predecessor:
            await asyncio.wait_for(waiting.wait(), 5)
            assert calls == ["retire"]
        else:
            await asyncio.wait_for(asyncio.shield(task), 5)
            assert calls == ["relay"]
    finally:
        exited.set()
        await asyncio.wait_for(task, 5)
    assert calls == (["retire", "relay", "reattach"] if predecessor else ["relay"])


@pytest.mark.asyncio
@pytest.mark.parametrize("reattach_error", [False, True])
async def test_source_retries_host_attachment_after_native_exit_without_stopping_ready_frontend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reattach_error: bool,
) -> None:
    from agent_bridge import app as app_module, runtime_version, source_start
    from zdd import routing

    monkeypatch.setattr(agent_bridge, "__version__", GENERATION)
    monkeypatch.setattr(runtime_version, "__version__", GENERATION)
    monkeypatch.setenv("AGENT_BRIDGE_INSTALL_DIR", str(tmp_path))
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    (tmp_path / "current-version").write_text(GENERATION, encoding="utf-8")
    routing.publish_active(tmp_path, bind="127.0.0.1", port=32000, pid=101, version="old")
    calls = []

    async def reattach(*, recover_cold: bool) -> int:
        assert recover_cold is False
        calls.append("reattach")
        if reattach_error:
            raise OSError("host temporarily unavailable")
        return 1

    async def retire(
        app: FastAPI, _previous: Endpoint, _current: Endpoint, *,
        initial_recovery_task: asyncio.Task[Any],
    ) -> None:
        await initial_recovery_task
        calls.append("retire")
        app.state.cold_recovery_ready = True

    monkeypatch.setattr(app_module, "_retire_previous_daemon", retire)
    app = _app(
        uvicorn_server=SimpleNamespace(started=True, should_exit=False),
        ready=True, shutting_down=False, bound_port=32001,
        config=SimpleNamespace(bind="127.0.0.1", enable_credential_relay=False),
        session_manager=SimpleNamespace(_generation_id="new", reattach_session_hosts=reattach),
    )
    published = asyncio.Event()
    initial = asyncio.create_task(asyncio.sleep(0))
    await source_start.complete_source_start(app, published, initial)
    assert calls == ["retire", "reattach"]
    assert published.is_set() and app.state.cold_recovery_ready
    assert not app.state.uvicorn_server.should_exit
    assert (routing.read_table(tmp_path) or {})["active"]["version"] == GENERATION


def test_settled_native_call_finishes_when_asyncio_run_cancels_pending_tasks() -> None:
    import subprocess
    import textwrap

    from agent_procutil import no_window_kwargs
    from agent_bridge import app_lifecycle

    script = textwrap.dedent("""
        import asyncio, contextvars, threading
        from agent_bridge.app_lifecycle import settled_thread_call

        context = contextvars.ContextVar('fixture_context')
        entered, release = threading.Event(), threading.Event()
        observed = []

        def native(*, label):
            entered.set()
            assert release.wait(3)
            observed.append(('worker', context.get(), label))

        async def caller():
            try:
                await settled_thread_call(native, label='native-call')
            except asyncio.CancelledError:
                observed.append(('cancelled',))

        async def main():
            context.set('inherited-context')
            asyncio.create_task(caller())
            assert await asyncio.to_thread(entered.wait, 3)
            threading.Timer(.05, release.set).start()

        asyncio.run(main())
        assert observed == [
            ('worker', 'inherited-context', 'native-call'), ('cancelled',),
        ], observed
    """)
    source = str(Path(app_lifecycle.__file__).resolve().parents[1])
    result = subprocess.run(
        [sys.executable, "-c", f"import sys; sys.path.insert(0, {source!r});\n" + script],
        capture_output=True, text=True, timeout=10, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
