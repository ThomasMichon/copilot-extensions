from __future__ import annotations

import argparse
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.skipif(os.name != "nt", reason="equivalent native Windows path spellings")
@pytest.mark.parametrize("extended_prefix", [False, True])
@pytest.mark.parametrize("extended_module", [False, True])
def test_source_start_accepts_same_physical_windows_namespace(
    tmp_path, monkeypatch, extended_prefix, extended_module,
):
    from agent_dispatch import source_start

    version = f"1.2.3+source.{'a' * 40}.{'b' * 16}.profile.{'c' * 16}"
    root = tmp_path / "app"
    slot = root / "versions" / version
    prefix = slot / ".venv"
    prefix.mkdir(parents=True)
    bootstrap = slot / ".runtime-bootstrap"
    bootstrap.mkdir()
    (bootstrap / "versioned_runtime.py").write_text(
        "def is_complete(root, version):\n    return True\n", encoding="utf-8",
    )
    (root / "current-version").write_text(version, encoding="utf-8")
    module = prefix / "source_start.py"
    module.write_text("", encoding="utf-8")
    monkeypatch.setattr(source_start, "__version__", version)
    monkeypatch.setattr(source_start.sys, "prefix", "\\\\?\\" + str(prefix) if extended_prefix else str(prefix))
    monkeypatch.setattr(source_start, "__file__", "\\\\?\\" + str(module) if extended_module else str(module))
    monkeypatch.setattr(source_start, "install_dir", lambda: root)
    assert source_start.require_selected_source().samefile(root)
    foreign = tmp_path / "foreign-source.py"
    foreign.write_text("", encoding="utf-8")
    monkeypatch.setattr(source_start, "__file__", "\\\\?\\" + str(foreign) if extended_module else str(foreign))
    with pytest.raises(ValueError, match="selection"):
        source_start.require_selected_source()


def test_source_start_parser_is_strict():
    from agent_dispatch.__main__ import build_parser
    from agent_dispatch.source_start import command

    parser = build_parser()
    assert parser.parse_args(["__source-start"]).func is command
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["__source-start", "--force"])
    assert exc.value.code == 2


def test_source_start_refuses_checkout_before_any_service_effect(monkeypatch):
    from agent_dispatch import source_start

    monkeypatch.setattr(source_start, "__version__", "1.2.3")
    with pytest.raises(ValueError, match="physical selected Source"):
        source_start.require_selected_source()


@pytest.mark.parametrize("selected,complete,foreign", [
    (True, True, False), (False, True, False), (True, False, False), (True, True, True),
])
def test_reconcile_requires_selected_complete_physical_repair_generation(
    tmp_path, monkeypatch, selected, complete, foreign,
):
    from agent_dispatch import source_start
    from agent_procutil import _daemon_instance_identity

    version = f"1.2.3+repair.{'a' * 32}"
    root = tmp_path / "app"
    slot = root / "versions" / version
    slot.mkdir(parents=True)
    bootstrap = slot / ".runtime-bootstrap"
    bootstrap.mkdir()
    (bootstrap / "versioned_runtime.py").write_text(
        f"def is_complete(root, version):\n    return {complete}\n", encoding="utf-8",
    )
    (root / "current-version").write_text(version if selected else "another", encoding="utf-8")
    module = (tmp_path if foreign else slot) / "source_start.py"
    monkeypatch.setattr(source_start, "__version__", version)
    monkeypatch.setattr(source_start.sys, "prefix", str(slot))
    monkeypatch.setattr(source_start, "__file__", str(module))
    monkeypatch.setattr(source_start, "install_dir", lambda: root)
    monkeypatch.setattr(_daemon_instance_identity, "runtime_slot_paths", lambda _: (
        version, str(slot), str(root),
    ))
    if selected and complete and not foreign:
        assert source_start.require_selected_generation().samefile(root)
    else:
        with pytest.raises(ValueError, match="complete|selection"):
            source_start.require_selected_generation()


@pytest.mark.parametrize("selected", [False, True])
def test_source_start_requires_completed_selected_physical_generation(tmp_path, monkeypatch, selected):
    from agent_dispatch import source_start
    from agent_procutil import _daemon_instance_identity

    version = f"1.2.3+source.{'a' * 40}.{'b' * 16}.profile.{'c' * 16}"
    root = tmp_path / "app"
    slot = root / "versions" / version
    prefix = slot / ".venv"
    prefix.mkdir(parents=True)
    bootstrap = slot / ".runtime-bootstrap"
    bootstrap.mkdir()
    (bootstrap / "versioned_runtime.py").write_text(
        "def is_complete(root, version):\n    return False\n", encoding="utf-8",
    )
    (root / "current-version").write_text(version if selected else "another", encoding="utf-8")
    monkeypatch.setattr(source_start, "__version__", version)
    monkeypatch.setattr(source_start.sys, "prefix", str(prefix))
    monkeypatch.setattr(source_start, "__file__", str(prefix / "source_start.py"))
    monkeypatch.setattr(source_start, "install_dir", lambda: root)
    monkeypatch.setattr(_daemon_instance_identity, "runtime_slot_paths", lambda _: (
        version, str(slot), str(root),
    ))
    with pytest.raises(ValueError, match="complete|selection"):
        source_start.require_selected_source()


def test_source_start_callback_never_deploys_or_succeeds_after_failed_coordinator(monkeypatch):
    from agent_dispatch import coordinator_cli, source_start

    calls = []
    monkeypatch.setattr(source_start, "require_selected_source", lambda: Path("app"))
    monkeypatch.setattr(source_start, "supervisor_plan", lambda root: None)
    monkeypatch.setattr(source_start, "prepare_scheduled_launchers", lambda *_a, **_k: None)
    monkeypatch.setattr(coordinator_cli, "_cmd_cutover", lambda args, **kwargs: (
        calls.append(kwargs) or 1
    ))
    monkeypatch.setattr(source_start, "ensure_supervisor", lambda root: pytest.fail("failed coordinator"))
    assert source_start.command(argparse.Namespace(json=True)) == 1
    assert len(calls) == 1
    assert calls[0]["source_replacement"] is True
    assert calls[0]["source_environment"]["AGENT_DISPATCH_INSTALL_DIR"] == "app"


def test_source_start_callback_reports_supervisor_failure_as_failure(monkeypatch):
    from agent_dispatch import coordinator_cli, source_start

    monkeypatch.setattr(source_start, "require_selected_source", lambda: Path("app"))
    monkeypatch.setattr(source_start, "supervisor_plan", lambda root: None)
    monkeypatch.setattr(source_start, "prepare_scheduled_launchers", lambda *_a, **_k: None)
    monkeypatch.setattr(coordinator_cli, "_cmd_cutover", lambda args, **kwargs: 0)
    monkeypatch.setattr(source_start, "ensure_supervisor", lambda root, **kwargs: (
        (_ for _ in ()).throw(RuntimeError("native supervisor readiness unconfirmed"))
    ))
    with pytest.raises(RuntimeError, match="readiness unconfirmed"):
        source_start.command(argparse.Namespace(json=True))


@pytest.mark.parametrize("changed", ["version", "birth", "config", "pid"])
def test_source_readiness_binds_generation_native_birth_and_configuration(monkeypatch, changed):
    from agent_dispatch import source_start
    from zdd import diagnostics

    payload = {
        "version": "source-a", "status": "ok", "runtime_config_identity": "config-a",
        "slot": {"pid": 41, "process_start_time": "birth-a", "role": "passive"},
    }
    if changed == "version":
        payload["version"] = "source-b"
    elif changed == "birth":
        payload["slot"]["process_start_time"] = "birth-b"
    elif changed == "config":
        payload["runtime_config_identity"] = "config-b"
    else:
        payload["slot"]["pid"] = 42
    monkeypatch.setattr(diagnostics, "process_start_time", lambda _: "birth-a")
    handle = SimpleNamespace(pid=41, poll=lambda: None)
    assert not source_start.accept_readiness(
        payload, handle=handle, version="source-a", config_identity="config-a",
    )


def test_source_readiness_accepts_only_live_pinned_selected_generation(monkeypatch):
    from agent_dispatch import source_start
    from zdd import diagnostics

    monkeypatch.setattr(diagnostics, "process_start_time", lambda _: "birth-a")
    handle = SimpleNamespace(pid=41, poll=lambda: None)
    payload = {
        "version": "source-a", "status": "ok", "runtime_config_identity": "config-a",
        "slot": {"pid": 41, "process_start_time": "birth-a", "role": "active"},
    }
    assert source_start.accept_readiness(
        payload, handle=handle, version="source-a", config_identity="config-a", active=True,
    )
    handle.poll = lambda: 0
    assert not source_start.accept_readiness(
        payload, handle=handle, version="source-a", config_identity="config-a", active=True,
    )


@pytest.mark.parametrize("confirmed", [True, False])
def test_reconcile_without_incumbent_never_starts_a_coordinator(tmp_path, monkeypatch, confirmed):
    from agent_dispatch import source_start
    from agent_dispatch.config import Config

    calls = []
    monkeypatch.setattr(source_start, "require_selected_source", lambda: pytest.fail("wrong owner"))
    options = {
        "token": None, "spawn_passive": lambda _port: pytest.fail("unexpected start"),
        "pick_free_port": lambda: pytest.fail("unexpected allocation"), "timeout": 1.0,
        "verify_selected": lambda: calls.append("selected"),
        "start_if_missing": False, "confirm_inactive": lambda _table: confirmed,
    }
    if confirmed:
        result = source_start.run_coordinator(
            Config(host="127.0.0.1", db_path=str(tmp_path / "tasks.db")), tmp_path, **options,
        )
        assert result == {"ok": True, "status": "inactive"}
        assert calls
    else:
        with pytest.raises(RuntimeError, match="inactive"):
            source_start.run_coordinator(
                Config(host="127.0.0.1", db_path=str(tmp_path / "tasks.db")), tmp_path, **options,
            )


def test_reconcile_rechecks_inactivity_after_selection_validation(tmp_path):
    from agent_dispatch import source_start
    from agent_dispatch.config import Config

    observations = iter((True, False))
    with pytest.raises(RuntimeError, match="inactive state changed"):
        source_start.run_coordinator(
            Config(host="127.0.0.1", db_path=str(tmp_path / "tasks.db")), tmp_path,
            token=None, spawn_passive=lambda _port: pytest.fail("unexpected start"),
            pick_free_port=lambda: pytest.fail("unexpected allocation"), timeout=1.0,
            verify_selected=lambda: None, start_if_missing=False,
            confirm_inactive=lambda _table: next(observations),
        )


def test_reconcile_refuses_changed_preflight_route_before_contact(tmp_path):
    from agent_dispatch import source_start
    from agent_dispatch.config import Config

    with pytest.raises(RuntimeError, match="preflight route changed"):
        source_start.run_coordinator(
            Config(host="127.0.0.1", db_path=str(tmp_path / "tasks.db")), tmp_path,
            token=None, spawn_passive=lambda _port: pytest.fail("unexpected start"),
            pick_free_port=lambda: pytest.fail("unexpected allocation"), timeout=1.0,
            verify_selected=lambda: None, start_if_missing=False, confirm_inactive=lambda _table: True,
            expected_table={"active": {"pid": 42}},
        )


@pytest.mark.parametrize("change", ["selection", "breadcrumb"])
def test_reconcile_rechecks_selected_generation_before_reusing_coordinator(tmp_path, monkeypatch, change):
    from agent_dispatch import client, source_start
    from agent_dispatch.config import Config
    from zdd import breadcrumb, diagnostics, routing

    cfg = Config(host="127.0.0.1", db_path=str(tmp_path / "tasks.db"))
    birth = diagnostics.process_start_time(os.getpid())
    assert birth
    routing.publish_active(
        tmp_path, bind="127.0.0.1", port=43123, pid=os.getpid(),
        version=source_start.__version__, process_start_time=birth,
    )

    class Client:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def health(self):
            return {
                "status": "ok", "version": source_start.__version__,
                "runtime_config_identity": source_start.config_identity(cfg),
                "runtime_behavior_identity_v1": source_start.behavior_identity(cfg),
                "data_auth_required": False, "data_token_match_v1": False,
                "slot": {"pid": os.getpid(), "process_start_time": birth, "role": "active"},
            }

    monkeypatch.setattr(client, "DispatchClient", Client)
    calls = []

    def selected():
        calls.append(True)
        if len(calls) > 1:
            if change == "selection":
                raise ValueError("selection changed")
            breadcrumb.breadcrumb_path(tmp_path).write_text("unsettled", encoding="utf-8")

    expected_error = ValueError if change == "selection" else RuntimeError
    with pytest.raises(expected_error, match="selection changed" if change == "selection" else "owner changed"):
        source_start.run_coordinator(
            cfg, tmp_path, token=None, spawn_passive=lambda _port: pytest.fail("unexpected start"),
            pick_free_port=lambda: pytest.fail("unexpected allocation"), timeout=1.0,
            verify_selected=selected, start_if_missing=False, confirm_inactive=lambda _table: True,
        )


def test_reconcile_refuses_exited_incumbent_before_spawn(tmp_path, monkeypatch):
    from agent_procutil import _daemon_instance_identity
    from agent_dispatch import client, source_start
    from agent_dispatch.config import Config
    from zdd import breadcrumb, diagnostics, routing

    cfg = Config(host="127.0.0.1", db_path=str(tmp_path / "tasks.db"))
    pid, version = os.getpid(), "0.0.1"
    birth = diagnostics.process_start_time(pid)
    assert birth
    routing.publish_active(
        tmp_path, bind="127.0.0.1", port=43123, pid=pid,
        version=version, process_start_time=birth,
    )
    monkeypatch.setattr(routing, "_listening", lambda *_args, **_kwargs: True)
    exited = False
    original_birth = _daemon_instance_identity.birth_identity
    original_write = breadcrumb.write_breadcrumb
    spawned = []

    def live_birth(owner):
        if exited:
            raise ProcessLookupError(owner)
        return original_birth(owner)

    def write(*args, **kwargs):
        nonlocal exited
        record = original_write(*args, **kwargs)
        if kwargs.get("state") == "started":
            exited = True
        return record

    class Client:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def health(self):
            return {
                "status": "ok", "version": version,
                "runtime_config_identity": source_start.config_identity(cfg),
                "slot": {"pid": pid, "process_start_time": birth, "role": "active"},
            }

    def spawn(port):
        spawned.append(port)
        raise RuntimeError("unexpected spawn")

    monkeypatch.setattr(client, "DispatchClient", Client)
    monkeypatch.setattr(_daemon_instance_identity, "birth_identity", live_birth)
    monkeypatch.setattr(breadcrumb, "write_breadcrumb", write)
    with pytest.raises(RuntimeError, match="predecessor.*ownership"):
        source_start.run_coordinator(
            cfg, tmp_path, token=None, spawn_passive=spawn, pick_free_port=lambda: 43124,
            timeout=1.0, verify_selected=lambda: None, start_if_missing=False,
            confirm_inactive=lambda _table: True,
        )
    assert spawned == []


def test_reconcile_bounds_real_routing_witness_contention(tmp_path, monkeypatch):
    import subprocess
    import sys
    import time
    from agent_dispatch import client, source_start
    from agent_dispatch.config import Config
    from zdd import diagnostics, routing

    cfg = Config(host="127.0.0.1", db_path=str(tmp_path / "tasks.db"))
    pid, version = os.getpid(), "0.0.1"
    birth = diagnostics.process_start_time(pid)
    assert birth
    routing.publish_active(
        tmp_path, bind="127.0.0.1", port=43123, pid=pid,
        version=version, process_start_time=birth,
    )

    class Client:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def health(self):
            return {
                "status": "ok", "version": version,
                "runtime_config_identity": source_start.config_identity(cfg),
                "slot": {"pid": pid, "process_start_time": birth, "role": "active"},
            }

    monkeypatch.setattr(client, "DispatchClient", Client)
    script = """
import sys, time
from pathlib import Path
from zdd import routing
root = Path(sys.argv[1])
with routing.routing_lock(root, deadline=time.monotonic() + 5):
    (root / "held").touch()
    deadline = time.monotonic() + 10
    while not (root / "release").exists() and time.monotonic() < deadline:
        time.sleep(0.01)
"""
    holder = subprocess.Popen(
        [sys.executable, "-B", "-c", script, str(tmp_path)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        limit = time.monotonic() + 5
        while not (tmp_path / "held").exists():
            assert holder.poll() is None and time.monotonic() < limit
            time.sleep(0.01)
        started = time.monotonic()
        result = source_start.run_coordinator(
            cfg, tmp_path, token=None, spawn_passive=lambda _port: pytest.fail("unexpected spawn"),
            pick_free_port=lambda: 43124, timeout=0.15, verify_selected=lambda: None,
            start_if_missing=False, confirm_inactive=lambda _table: True,
        )
        elapsed = time.monotonic() - started
    finally:
        (tmp_path / "release").touch()
        assert holder.wait(timeout=15) == 0
    assert result["ok"] is False
    assert elapsed < 2


def test_reconcile_bounds_native_cutover_lock_wait(tmp_path, monkeypatch):
    from agent_dispatch import client, source_start
    from agent_dispatch.config import Config
    from zdd import cutover, diagnostics, routing

    cfg = Config(host="127.0.0.1", db_path=str(tmp_path / "tasks.db"))
    birth = diagnostics.process_start_time(os.getpid())
    assert birth
    routing.publish_active(
        tmp_path, bind="127.0.0.1", port=43123, pid=os.getpid(),
        version="1.0.0", process_start_time=birth,
    )

    client_timeouts = []

    class Client:
        def __init__(self, *_args, **kwargs):
            client_timeouts.append(kwargs["timeout"])

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def health(self):
            return {
                "status": "ok", "version": "1.0.0",
                "runtime_config_identity": source_start.config_identity(cfg),
                "slot": {"pid": os.getpid(), "process_start_time": birth, "role": "active"},
            }

    captured = []

    class Orchestrator:
        def __init__(self, *_args, **_kwargs):
            pass

        def run(self, **kwargs):
            captured.append(kwargs)
            return SimpleNamespace(to_dict=lambda: {"ok": True})

    monkeypatch.setattr(client, "DispatchClient", Client)
    monkeypatch.setattr(cutover, "CutoverOrchestrator", Orchestrator)
    result = source_start.run_coordinator(
        cfg, tmp_path, token=None, spawn_passive=lambda _port: None,
        pick_free_port=lambda: 43124, timeout=0.3, verify_selected=lambda: None,
        start_if_missing=False, confirm_inactive=lambda _table: True,
    )
    assert result["ok"] is True
    assert captured[0]["lock_timeout"] <= 0.3
    assert client_timeouts and all(value <= 0.3 for value in client_timeouts)


def test_reconcile_never_sends_credentials_to_foreign_route(tmp_path, monkeypatch):
    from agent_dispatch import client, coordinator_observation, source_start
    from agent_dispatch.config import Config
    from zdd import diagnostics, routing

    birth = diagnostics.process_start_time(os.getpid())
    assert birth
    routing.publish_active(
        tmp_path, bind="192.0.2.1", port=43123, pid=os.getpid(),
        version="1.0.0", process_start_time=birth,
    )
    calls = []

    class Client:
        def __init__(self, url, **_kwargs):
            calls.append(url)
            raise RuntimeError("unexpected client")

    monkeypatch.setattr(client, "DispatchClient", Client)
    with pytest.raises(coordinator_observation.ServingObservationError):
        source_start.run_coordinator(
            Config(host="127.0.0.1", db_path=str(tmp_path / "tasks.db")), tmp_path,
            token="fixture", spawn_passive=lambda _port: pytest.fail("unexpected start"),
            pick_free_port=lambda: pytest.fail("unexpected allocation"), timeout=1.0,
            verify_selected=lambda: None, start_if_missing=False, confirm_inactive=lambda _table: True,
        )
    assert calls == []


def test_coordinator_observation_does_not_infer_inactivity_from_legacy_evidence(tmp_path, monkeypatch):
    from agent_dispatch import coordinator_observation
    from agent_dispatch.config import Config

    routing = tmp_path / "routing"
    run = tmp_path / "run"
    routing.mkdir()
    run.mkdir()
    cfg = Config(host="127.0.0.1", db_path=str(tmp_path / "tasks.db"))
    monkeypatch.setattr(coordinator_observation, "_legacy_listener", lambda _cfg: False)
    assert coordinator_observation.observe(cfg, routing, run_dir=run, token=None, environment={}) == {
        "state": "inactive", "generation": None,
    }
    assert coordinator_observation.observe(
        cfg, routing, run_dir=run, token=None, environment={"AGENT_DISPATCH_WSL_WINDOWS_CLIENT": "0"},
    ) == {"state": "inactive", "generation": None}
    (run / "endpoint.json").write_text("{}", encoding="utf-8")
    with pytest.raises(coordinator_observation.ServingObservationError):
        coordinator_observation.observe(cfg, routing, run_dir=run, token=None, environment={})


def test_coordinator_observation_rechecks_route_after_inactive_probe(tmp_path, monkeypatch):
    from agent_dispatch import coordinator_observation
    from agent_dispatch.config import Config
    from zdd import diagnostics, routing

    cfg = Config(host="127.0.0.1", db_path=str(tmp_path / "tasks.db"))
    birth = diagnostics.process_start_time(os.getpid())
    assert birth

    def competing_start(_cfg):
        routing.publish_active(
            tmp_path, bind="127.0.0.1", port=43123, pid=os.getpid(),
            version="1.2.3", process_start_time=birth,
        )
        return False

    monkeypatch.setattr(coordinator_observation, "_legacy_listener", competing_start)
    with pytest.raises(coordinator_observation.ServingObservationError):
        coordinator_observation.observe(cfg, tmp_path, run_dir=tmp_path, token=None, environment={})


@pytest.mark.parametrize("occupied", [False, True])
def test_coordinator_legacy_listener_uses_native_bind_evidence(monkeypatch, occupied):
    import errno
    import socket
    from agent_dispatch import coordinator_observation
    from agent_dispatch.config import Config

    class Probe:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def setsockopt(self, *_args):
            pass

        def bind(self, _address):
            if occupied:
                raise OSError(errno.EADDRINUSE, "occupied")

    monkeypatch.setattr(socket, "socket", lambda *_args, **_kwargs: Probe())
    monkeypatch.setattr(socket, "create_connection", lambda *_args, **_kwargs: (
        (_ for _ in ()).throw(TimeoutError())
    ))
    assert coordinator_observation._legacy_listener(Config(host="127.0.0.1", port=43123)) is occupied


def test_coordinator_localhost_uses_the_service_bind_address_family(monkeypatch):
    import socket
    from agent_dispatch import coordinator_observation

    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: [
        (socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("::1", 0, 0, 0)),
        (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 0)),
    ])
    assert coordinator_observation._loopback("localhost") == "::1"


def test_coordinator_localhost_rejects_nonloopback_resolution(monkeypatch):
    import socket
    from agent_dispatch import coordinator_observation

    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("192.0.2.1", 0)),
    ])
    with pytest.raises(coordinator_observation.ServingObservationError):
        coordinator_observation._loopback("localhost")


@pytest.mark.skipif(not __import__("socket").has_ipv6, reason="IPv6 unavailable")
def test_coordinator_legacy_localhost_detects_an_ipv6_only_listener():
    import socket
    from agent_dispatch import coordinator_observation
    from agent_dispatch.config import Config

    with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as listener:
        try:
            listener.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            listener.bind(("::1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as ipv4:
                ipv4.bind(("127.0.0.1", port))
        except OSError:
            pytest.skip("isolated IPv6 loopback is unavailable")
        assert coordinator_observation._legacy_listener(Config(host="localhost", port=port))


@pytest.mark.parametrize(
    "changed", [
        "none", "version", "birth", "config", "route", "remote", "auth", "auth-type",
        "data-mismatch", "data-missing", "data-type", "behavior",
        "breadcrumb", "breadcrumb-during-health",
    ],
)
def test_coordinator_observation_requires_authenticated_generation_and_birth(
    tmp_path, monkeypatch, changed,
):
    import httpx
    from agent_dispatch import client, coordinator_observation, source_start
    from agent_dispatch.config import Config
    from zdd import breadcrumb, diagnostics, routing

    cfg = Config(host="127.0.0.1", db_path=str(tmp_path / "tasks.db"))
    version = "1.2.3+repair." + "a" * 32
    pid = os.getpid()
    birth = diagnostics.process_start_time(pid)
    assert birth
    routing.publish_active(
        tmp_path, bind="192.0.2.1" if changed == "remote" else "127.0.0.1",
        port=43123, pid=pid, version=version,
        process_start_time=birth,
    )
    active = routing.read_table(tmp_path, strict=True)["active"]
    health = {
        "status": "ok", "version": version, "runtime_config_identity": source_start.config_identity(cfg),
        "runtime_behavior_identity_v1": source_start.behavior_identity(cfg),
        "data_auth_required": True, "data_token_match_v1": True,
        "slot": {"pid": pid, "process_start_time": birth, "role": "active", "active": active.copy()},
    }
    if changed == "version":
        health["version"] = "other"
    elif changed == "birth":
        health["slot"]["process_start_time"] = "0"
    elif changed == "config":
        health["runtime_config_identity"] = "other"
    elif changed == "route":
        health["slot"]["active"]["port"] = 43124
    elif changed == "auth":
        health["data_auth_required"] = False
    elif changed == "auth-type":
        health["data_auth_required"] = "yes"
    elif changed == "data-mismatch":
        health["data_token_match_v1"] = False
    elif changed == "data-missing":
        health.pop("data_token_match_v1")
    elif changed == "data-type":
        health["data_token_match_v1"] = "yes"
    elif changed == "behavior":
        health["runtime_behavior_identity_v1"] = "different"
    elif changed == "breadcrumb":
        breadcrumb.breadcrumb_path(tmp_path).write_text("{}", encoding="utf-8")

    calls = []
    local_transport = object()
    monkeypatch.setattr(httpx, "HTTPTransport", lambda **kwargs: (
        local_transport if kwargs == {"trust_env": False} else pytest.fail("proxy-enabled transport")
    ))

    class Client:
        def __init__(self, _url, *, token, timeout, transport):
            calls.append(_url)
            assert token in ("fixture", None)
            self.token = token
            assert transport is local_transport

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def health(self):
            if self.token is None:
                from agent_dispatch.client_errors import DispatchError

                raise DispatchError(401, "missing credential")
            if changed == "breadcrumb-during-health":
                breadcrumb.breadcrumb_path(tmp_path).write_text("{}", encoding="utf-8")
            return health

    monkeypatch.setattr(client, "DispatchClient", Client)
    if changed == "none":
        assert coordinator_observation.observe(cfg, tmp_path, run_dir=tmp_path, token="fixture",
                                               environment={}) == {"state": "ready", "generation": version}
    else:
        with pytest.raises(coordinator_observation.ServingObservationError):
            coordinator_observation.observe(cfg, tmp_path, run_dir=tmp_path, token="fixture", environment={})
        if changed == "remote":
            assert calls == []


@pytest.mark.parametrize(("field", "value"), [
    ("sweep_interval", 123.0), ("orphan_grace", 456.0),
    ("handoff_fallback_enabled", True), ("handoff_fallback_grace", 789.0),
])
def test_behavior_identity_binds_coordinator_settings(tmp_path, field, value):
    from dataclasses import replace
    from agent_dispatch import source_start
    from agent_dispatch.config import Config

    cfg = Config(db_path=str(tmp_path / "tasks.db"))
    assert source_start.behavior_identity(cfg) != source_start.behavior_identity(replace(cfg, **{field: value}))


def test_behavior_identity_excludes_credentials(tmp_path):
    from dataclasses import replace
    from agent_dispatch import source_start
    from agent_dispatch.config import Config

    cfg = Config(db_path=str(tmp_path / "tasks.db"))
    assert source_start.behavior_identity(cfg) == source_start.behavior_identity(
        replace(cfg, token="fixture-data", control_token="fixture-control"),
    )


@pytest.mark.parametrize("incumbent_base", ["runtime", "caller"])
def test_relative_database_matches_the_coordinator_working_directory(
    tmp_path, monkeypatch, incumbent_base,
):
    from agent_dispatch import coordinator_observation, source_start
    from agent_dispatch.config import Config
    from zdd import diagnostics, routing

    runtime = tmp_path / "runtime"
    caller = tmp_path / "caller"
    runtime.mkdir()
    caller.mkdir()
    routes = tmp_path / "routes"
    cfg = Config(host="127.0.0.1", db_path=str(Path("data") / "tasks.db"))
    pid = os.getpid()
    birth = diagnostics.process_start_time(pid)
    assert birth
    routing.publish_active(
        routes, bind="127.0.0.1", port=43123, pid=pid,
        version=source_start.__version__, process_start_time=birth,
    )
    active = routing.read_table(routes, strict=True)["active"]
    monkeypatch.chdir(runtime if incumbent_base == "runtime" else caller)
    incumbent_identity = source_start.config_identity(cfg)
    monkeypatch.chdir(caller)
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(runtime))
    monkeypatch.setattr(source_start, "install_dir", lambda: runtime)
    health = {
        "status": "ok", "version": source_start.__version__,
        "runtime_config_identity": incumbent_identity,
        "runtime_behavior_identity_v1": source_start.behavior_identity(cfg),
        "data_auth_required": False, "data_token_match_v1": False,
        "slot": {"pid": pid, "process_start_time": birth, "role": "active", "active": active},
    }

    class Client:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def health(self):
            return health

    monkeypatch.setattr(coordinator_observation, "_local_client", Client)

    def observe():
        return coordinator_observation.observe(
            cfg, routes, run_dir=tmp_path / "run", token=None, environment={},
        )

    def reconcile():
        return source_start.run_coordinator(
            cfg, routes, token=None, spawn_passive=lambda _port: pytest.fail("unexpected spawn"),
            pick_free_port=lambda: 43124, timeout=1.0, verify_selected=lambda: None,
            start_if_missing=False, confirm_inactive=lambda _table: True,
        )

    if incumbent_base == "runtime":
        assert observe() == {"state": "ready", "generation": source_start.__version__}
        assert reconcile()["status"] == "reused"
    else:
        with pytest.raises(coordinator_observation.ServingObservationError):
            observe()
        with pytest.raises(RuntimeError, match="effective configuration"):
            reconcile()


@pytest.mark.skipif(os.name != "nt", reason="Windows drive-relative paths")
def test_drive_relative_database_cannot_be_bound_to_a_service_directory(tmp_path):
    from agent_dispatch import source_start
    from agent_dispatch.config import Config

    cfg = Config(db_path=tmp_path.drive + "queue.db")
    with pytest.raises(ValueError, match="drive-relative"):
        source_start.config_identity(cfg, base_dir=tmp_path)


@pytest.mark.parametrize(("behavior", "expected"), [
    ("missing", "transitioned"), ("different", "transitioned"), ("matching", "reused"),
])
def test_reconcile_checks_same_generation_behavior(tmp_path, monkeypatch, behavior, expected):
    from agent_dispatch import client, source_start
    from agent_dispatch.config import Config
    from zdd import cutover, diagnostics, routing

    cfg = Config(host="127.0.0.1", db_path=str(tmp_path / "tasks.db"))
    pid = os.getpid()
    birth = diagnostics.process_start_time(pid)
    assert birth
    routing.publish_active(
        tmp_path, bind="127.0.0.1", port=43123, pid=pid,
        version=source_start.__version__, process_start_time=birth,
    )
    health = {
        "status": "ok", "version": source_start.__version__, "data_auth_required": False,
        "data_token_match_v1": False,
        "runtime_config_identity": source_start.config_identity(cfg),
        "slot": {"pid": pid, "process_start_time": birth, "role": "active"},
    }
    if behavior == "different":
        health["runtime_behavior_identity_v1"] = "different"
    elif behavior == "matching":
        health["runtime_behavior_identity_v1"] = source_start.behavior_identity(cfg)

    class Client:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def health(self):
            return health

    class Orchestrator:
        def __init__(self, *_args, **_kwargs):
            pass

        def run(self, **_kwargs):
            return SimpleNamespace(to_dict=lambda: {"ok": True, "status": "transitioned"})

    monkeypatch.setattr(client, "DispatchClient", Client)
    monkeypatch.setattr(cutover, "CutoverOrchestrator", Orchestrator)
    result = source_start.run_coordinator(
        cfg, tmp_path, token=None, spawn_passive=lambda _port: pytest.fail("fixture must not spawn"),
        pick_free_port=lambda: 43124, timeout=1.0, verify_selected=lambda: None,
        start_if_missing=False, confirm_inactive=lambda _table: True,
    )
    assert result["status"] == expected


@pytest.mark.parametrize(("status", "manual"), [
    ({"enabled": True, "armed": True}, False),
    ({"enabled": True, "armed": False}, True),
    ({"enabled": False, "armed": False}, True),
    ({}, True),
])
def test_reconcile_requires_explicit_retirement_when_predecessor_cannot_self_retire(
    tmp_path, monkeypatch, status, manual,
):
    from agent_dispatch import client, source_start
    from agent_dispatch.config import Config
    from zdd import cutover, diagnostics, routing

    cfg = Config(host="127.0.0.1", db_path=str(tmp_path / "tasks.db"))
    pid = os.getpid()
    birth = diagnostics.process_start_time(pid)
    assert birth
    routing.publish_active(
        tmp_path, bind="127.0.0.1", port=43123, pid=pid,
        version="older", process_start_time=birth,
    )
    health = {
        "status": "ok", "version": "older", "data_auth_required": False,
        "runtime_config_identity": source_start.config_identity(cfg),
        "slot": {"pid": pid, "process_start_time": birth, "role": "active", "self_retire": status},
    }

    class Client:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def health(self):
            return health

    captured = {}

    class Orchestrator:
        def __init__(self, *_args, **kwargs):
            captured.update(kwargs)

        def run(self, **_kwargs):
            return SimpleNamespace(to_dict=lambda: {"ok": True})

    monkeypatch.setattr(client, "DispatchClient", Client)
    monkeypatch.setattr(cutover, "CutoverOrchestrator", Orchestrator)
    assert source_start.run_coordinator(
        cfg, tmp_path, token=None, spawn_passive=lambda _port: pytest.fail("fixture must not spawn"),
        pick_free_port=lambda: 43124, timeout=1.0, verify_selected=lambda: None,
        start_if_missing=False, confirm_inactive=lambda _table: True,
    )["ok"] is True
    assert bool(captured.get("retire_failed_predecessor")) is manual
    assert bool(captured.get("require_predecessor_retirement")) is manual


@pytest.mark.parametrize("outcome", ["exited", "drain-refused", "successor-changed", "exit-unconfirmed"])
def test_explicit_retirement_drains_only_the_pinned_predecessor(tmp_path, monkeypatch, outcome):
    from agent_dispatch import coordinator_observation, source_start
    from agent_dispatch.config import Config
    from zdd import cutover, diagnostics, routing

    cfg = Config(host="127.0.0.1", db_path=str(tmp_path / "tasks.db"))
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: str(pid * 10))
    old = routing.publish_active(
        tmp_path, bind="127.0.0.1", port=43123, pid=111, version="older", process_start_time="1110",
    )
    state = {"old": "matching"}
    monkeypatch.setattr(source_start, "_route_owner_state", lambda _endpoint: state["old"])
    calls = []

    class Client:
        def __init__(self, url):
            self.url = url

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def health(self):
            new = self.url.endswith(":43124")
            pid = 222 if new else 111
            return {
                "status": "ok", "version": source_start.__version__ if new else "older",
                "runtime_config_identity": source_start.config_identity(cfg),
                "runtime_behavior_identity_v1": source_start.behavior_identity(cfg),
                "data_auth_required": False, "data_token_match_v1": False,
                "slot": {
                    "pid": pid, "process_start_time": str(pid * 10), "role": "active",
                    "self_retire": {"enabled": False, "armed": False},
                },
            }

        def drain(self, *, timeout, poll, force):
            calls.append(("drain", force))
            if outcome == "successor-changed":
                routing.publish_active(
                    tmp_path, bind="127.0.0.1", port=43125, pid=333,
                    version="foreign", process_start_time="3330",
                )
            clean = outcome != "drain-refused"
            return {"drained": clean, "clean": clean, "forced": False}

        def shutdown(self):
            calls.append(("shutdown",))
            if outcome == "exited":
                state["old"] = "dead"
            return {"shutdown": True}

    monkeypatch.setattr(coordinator_observation, "_local_client", lambda url, **_kwargs: Client(url))

    class Orchestrator:
        def __init__(self, *_args, **kwargs):
            self.options = kwargs

        def run(self, **_kwargs):
            self.options["spawn_passive"](43124)
            new = routing.publish_active(
                tmp_path, bind="127.0.0.1", port=43124, pid=222,
                version=source_start.__version__, process_start_time="2220",
            )
            self.options["activate_successor"](new)
            retirement = self.options["retire_failed_predecessor"](old)
            return SimpleNamespace(to_dict=lambda: retirement)

    monkeypatch.setattr(cutover, "CutoverOrchestrator", Orchestrator)
    result = source_start.run_coordinator(
        cfg, tmp_path, token=None, spawn_passive=lambda _port: SimpleNamespace(pid=222, poll=lambda: None),
        pick_free_port=lambda: 43124, timeout=0.25, verify_selected=lambda: None,
        start_if_missing=False, confirm_inactive=lambda _table: True,
    )
    assert result["exit_confirmed"] is (outcome == "exited")
    assert ("drain", False) in calls
    assert (("shutdown",) in calls) is (outcome in {"exited", "exit-unconfirmed"})


@pytest.mark.parametrize("record", ['{"state":"committed","self_retirement":true}', "invalid"])
def test_reconcile_refuses_unsettled_breadcrumb_before_same_generation_reuse(
    tmp_path, monkeypatch, record,
):
    from agent_dispatch import coordinator_observation, source_start
    from agent_dispatch.config import Config
    from zdd import breadcrumb, diagnostics, routing

    cfg = Config(host="127.0.0.1", db_path=str(tmp_path / "tasks.db"))
    pid = os.getpid()
    birth = diagnostics.process_start_time(pid)
    assert birth
    routing.publish_active(
        tmp_path, bind="127.0.0.1", port=43123, pid=pid,
        version=source_start.__version__, process_start_time=birth,
    )
    breadcrumb.breadcrumb_path(tmp_path).write_text(record, encoding="utf-8")

    class Client:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def health(self):
            return {
                "status": "ok", "version": source_start.__version__,
                "runtime_config_identity": source_start.config_identity(cfg),
                "runtime_behavior_identity_v1": source_start.behavior_identity(cfg),
                "data_auth_required": False, "data_token_match_v1": False,
                "slot": {"pid": pid, "process_start_time": birth, "role": "active"},
            }

    monkeypatch.setattr(coordinator_observation, "_local_client", Client)
    with pytest.raises(RuntimeError, match="unsettled"):
        source_start.run_coordinator(
            cfg, tmp_path, token=None, spawn_passive=lambda _port: pytest.fail("unexpected spawn"),
            pick_free_port=lambda: 43124, timeout=1.0, verify_selected=lambda: None,
            start_if_missing=False, confirm_inactive=lambda _table: True,
        )


@pytest.mark.parametrize("enforced", [False, True])
@pytest.mark.parametrize("advertised", [None, True])
def test_coordinator_observation_challenges_legacy_auth_enforcement(tmp_path, monkeypatch, enforced, advertised):
    import httpx
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from agent_dispatch import client, coordinator_observation, source_start
    from agent_dispatch.config import Config
    from agent_dispatch.coordinator_auth import _make_auth
    from zdd import diagnostics, routing

    cfg = Config(host="127.0.0.1", db_path=str(tmp_path / "tasks.db"))
    pid, version = os.getpid(), "1.2.3"
    birth = diagnostics.process_start_time(pid)
    assert birth
    routing.publish_active(
        tmp_path, bind="127.0.0.1", port=43123, pid=pid,
        version=version, process_start_time=birth,
    )
    active = routing.read_table(tmp_path, strict=True)["active"]
    payload = {
        "status": "ok", "version": version, "runtime_config_identity": source_start.config_identity(cfg),
        "slot": {"pid": pid, "process_start_time": birth, "role": "active", "active": active},
    }
    if advertised is not None:
        payload["data_auth_required"] = advertised
    app = FastAPI(dependencies=[Depends(_make_auth("fixture-token" if enforced else None, None))])

    @app.get("/health")
    def health():
        return payload

    with TestClient(app) as service:
        def local_client(url, *, token, timeout):
            def send(request):
                response = service.request(request.method, request.url.path, headers=request.headers)
                return httpx.Response(response.status_code, content=response.content, request=request)

            return client.DispatchClient(url, token=token, timeout=timeout, transport=httpx.MockTransport(send))

        monkeypatch.setattr(coordinator_observation, "_local_client", local_client)
        if enforced:
            with pytest.raises(coordinator_observation.ServingObservationError):
                coordinator_observation.observe(
                    cfg, tmp_path, run_dir=tmp_path, token="fixture-token", environment={},
                )
            assert coordinator_observation.observe(
                cfg, tmp_path, run_dir=tmp_path, token="fixture-token", environment={},
                require_behavior=False,
            ) == {"state": "ready", "generation": version}
        else:
            with pytest.raises(coordinator_observation.ServingObservationError):
                coordinator_observation.observe(
                    cfg, tmp_path, run_dir=tmp_path, token="fixture-token", environment={},
                    require_behavior=False,
                )


@pytest.mark.parametrize(
    ("configured", "supplied", "expected"),
    [
        ("fixture-data", "fixture-data", True),
        ("fixture-data", "fixture-control", False),
        ("fixture-other", "fixture-other", True),
        (None, None, False),
    ],
)
def test_coordinator_health_reports_actual_data_token_match(
    tmp_path, configured, supplied, expected,
):
    import httpx
    from fastapi.testclient import TestClient
    from agent_dispatch.client import DispatchClient
    from agent_dispatch.coordinator import create_app
    from agent_dispatch.queue import TaskQueue

    app = create_app(
        TaskQueue(tmp_path / "tasks.db"), token=configured, control_token="fixture-control",
        sweep_interval=0, verification_interval=0,
    )
    with TestClient(app) as service:
        def send(request):
            response = service.request(request.method, request.url.path, headers=request.headers)
            return httpx.Response(response.status_code, content=response.content, request=request)

        with DispatchClient(
            "http://127.0.0.1", token=supplied, transport=httpx.MockTransport(send),
        ) as client:
            health = client.health()
        assert health["data_auth_required"] is (configured is not None)
        assert health["data_token_match_v1"] is expected


@pytest.mark.parametrize(
    ("selected_token", "legacy", "expected"),
    [
        ("fixture-data", False, "reused"),
        ("fixture-control", False, "transitioned"),
        ("fixture-data", True, "transitioned"),
        ("fixture-control", True, "transitioned"),
        (None, False, "reused"),
        (None, True, "transitioned"),
    ],
)
def test_reconcile_reuses_only_proven_data_auth(
    tmp_path, monkeypatch, selected_token, legacy, expected,
):
    import httpx
    from fastapi.testclient import TestClient
    from agent_dispatch import client, coordinator_observation, coordinator_status, source_start
    from agent_dispatch.config import Config
    from agent_dispatch.coordinator import create_app
    from agent_dispatch.queue import TaskQueue
    from zdd import cutover, diagnostics, routing

    cfg = Config(host="127.0.0.1", db_path=str(tmp_path / "tasks.db"), token=selected_token)
    pid = os.getpid()
    birth = diagnostics.process_start_time(pid)
    assert birth
    routing.publish_active(
        tmp_path, bind="127.0.0.1", port=43123, pid=pid,
        version=source_start.__version__, process_start_time=birth,
    )
    active = routing.read_table(tmp_path, strict=True)["active"]
    app = create_app(
        TaskQueue(tmp_path / "tasks.db"),
        token=None if selected_token is None else "fixture-data",
        control_token="fixture-control", sweep_interval=0, verification_interval=0,
    )
    app.state.runtime_config_identity = source_start.config_identity(cfg)
    app.state.runtime_behavior_identity_v1 = source_start.behavior_identity(cfg)
    monkeypatch.setattr(coordinator_status, "_slot_descriptor", lambda _state: {
        "pid": pid, "process_start_time": birth, "role": "active", "active": active,
    })

    class Orchestrator:
        def __init__(self, *_args, **_kwargs):
            pass

        def run(self, **_kwargs):
            return SimpleNamespace(to_dict=lambda: {"ok": True, "status": "transitioned"})

    monkeypatch.setattr(cutover, "CutoverOrchestrator", Orchestrator)
    with TestClient(app) as service:
        def local_client(url, *, token, timeout):
            def send(request):
                response = service.request(request.method, request.url.path, headers=request.headers)
                if legacy and response.status_code == 200:
                    payload = response.json()
                    payload.pop("data_token_match_v1", None)
                    return httpx.Response(response.status_code, json=payload, request=request)
                return httpx.Response(response.status_code, content=response.content, request=request)

            return client.DispatchClient(url, token=token, timeout=timeout, transport=httpx.MockTransport(send))

        monkeypatch.setattr(coordinator_observation, "_local_client", local_client)
        assert coordinator_observation.observe(
            cfg, tmp_path, run_dir=tmp_path, token=selected_token, environment={},
            require_behavior=False,
        ) == {"state": "ready", "generation": source_start.__version__}
        if expected == "reused":
            assert coordinator_observation.observe(
                cfg, tmp_path, run_dir=tmp_path, token=selected_token, environment={},
            ) == {"state": "ready", "generation": source_start.__version__}
        else:
            with pytest.raises(coordinator_observation.ServingObservationError):
                coordinator_observation.observe(
                    cfg, tmp_path, run_dir=tmp_path, token=selected_token, environment={},
                )
        result = source_start.run_coordinator(
            cfg, tmp_path, token=selected_token,
            spawn_passive=lambda _port: pytest.fail("fixture must not spawn"),
            pick_free_port=lambda: 43124, timeout=1.0, verify_selected=lambda: None,
            start_if_missing=False, confirm_inactive=lambda _table: True,
        )
    assert result["status"] == expected


@pytest.mark.parametrize("match", [True, False, None])
def test_reconcile_successor_requires_proven_data_auth(tmp_path, monkeypatch, match):
    from agent_dispatch import coordinator_observation, source_start
    from agent_dispatch.client_errors import DispatchError
    from agent_dispatch.config import Config
    from zdd import cutover, diagnostics, routing

    cfg = Config(host="127.0.0.1", db_path=str(tmp_path / "tasks.db"), token="fixture-data")
    pid = os.getpid()
    birth = diagnostics.process_start_time(pid)
    assert birth
    routing.publish_active(
        tmp_path, bind="127.0.0.1", port=43123, pid=pid,
        version="previous", process_start_time=birth,
    )

    class Client:
        def __init__(self, url, *, token, timeout):
            self.url = url
            self.token = token

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def health(self):
            if self.token is None:
                raise DispatchError(401, "missing credential")
            successor = self.url.endswith(":43124")
            result = {
                "status": "ok",
                "version": source_start.__version__ if successor else "previous",
                "runtime_config_identity": source_start.config_identity(cfg),
                "runtime_behavior_identity_v1": source_start.behavior_identity(cfg),
                "data_auth_required": True,
                "slot": {"pid": pid, "process_start_time": birth, "role": "active"},
            }
            if successor and match is not None:
                result["data_token_match_v1"] = match
            return result

    class Orchestrator:
        def __init__(self, *_args, **kwargs):
            self.spawn = kwargs["spawn_passive"]
            self.probe = kwargs["health_check"]

        def run(self, **_kwargs):
            self.spawn(43124)
            ready = self.probe("127.0.0.1", 43124)
            return SimpleNamespace(to_dict=lambda: {"ok": True, "ready": ready})

    monkeypatch.setattr(coordinator_observation, "_local_client", Client)
    monkeypatch.setattr(cutover, "CutoverOrchestrator", Orchestrator)
    result = source_start.run_coordinator(
        cfg, tmp_path, token="fixture-data",
        spawn_passive=lambda _port: SimpleNamespace(pid=pid, poll=lambda: None),
        pick_free_port=lambda: 43124, timeout=1.0, verify_selected=lambda: None,
        start_if_missing=False, confirm_inactive=lambda _table: True,
    )
    assert result["ready"] is (match is True)


def test_reconcile_refuses_same_generation_without_configured_auth(tmp_path, monkeypatch):
    from agent_dispatch import client, coordinator_observation, source_start
    from agent_dispatch.config import Config
    from zdd import diagnostics, routing

    cfg = Config(host="127.0.0.1", db_path=str(tmp_path / "tasks.db"), token="fixture-token")
    pid = os.getpid()
    birth = diagnostics.process_start_time(pid)
    assert birth
    routing.publish_active(
        tmp_path, bind="127.0.0.1", port=43123, pid=pid,
        version=source_start.__version__, process_start_time=birth,
    )

    class Client:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def health(self):
            return {
                "status": "ok", "version": source_start.__version__, "data_auth_required": False,
                "runtime_config_identity": source_start.config_identity(cfg),
                "slot": {"pid": pid, "process_start_time": birth, "role": "active"},
            }

    monkeypatch.setattr(client, "DispatchClient", Client)
    with pytest.raises(coordinator_observation.ServingObservationError):
        source_start.run_coordinator(
            cfg, tmp_path, token="fixture-token", spawn_passive=lambda _port: pytest.fail("unexpected start"),
            pick_free_port=lambda: pytest.fail("unexpected allocation"), timeout=1.0,
            verify_selected=lambda: None, start_if_missing=False, confirm_inactive=lambda _table: True,
        )


@pytest.mark.parametrize("exit_phase", ["before", "after-health"])
def test_coordinator_observation_rejects_exited_owner_with_readable_birth(tmp_path, monkeypatch, exit_phase):
    from agent_procutil import _daemon_instance_identity
    from agent_dispatch import coordinator_observation, source_start
    from agent_dispatch.config import Config
    from zdd import diagnostics, routing

    cfg = Config(host="127.0.0.1", db_path=str(tmp_path / "tasks.db"))
    pid, version = os.getpid(), "1.2.3"
    birth = diagnostics.process_start_time(pid)
    assert birth
    routing.publish_active(
        tmp_path, bind="127.0.0.1", port=43123, pid=pid,
        version=version, process_start_time=birth,
    )
    active = routing.read_table(tmp_path, strict=True)["active"]
    health = {
        "status": "ok", "version": version, "runtime_config_identity": source_start.config_identity(cfg),
        "runtime_behavior_identity_v1": source_start.behavior_identity(cfg),
        "slot": {"pid": pid, "process_start_time": birth, "role": "active", "active": active},
    }
    exited = exit_phase == "before"
    original_birth = _daemon_instance_identity.birth_identity
    calls = []

    def live_birth(owner):
        if exited:
            raise ProcessLookupError(owner)
        return original_birth(owner)

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def health(self):
            nonlocal exited
            calls.append("health")
            exited = True
            return health

    monkeypatch.setattr(_daemon_instance_identity, "birth_identity", live_birth)
    monkeypatch.setattr(coordinator_observation, "_local_client", lambda *_args, **_kwargs: Client())
    with pytest.raises(coordinator_observation.ServingObservationError):
        coordinator_observation.observe(cfg, tmp_path, run_dir=tmp_path, token=None, environment={})
    assert calls == ([] if exit_phase == "before" else ["health"])


def test_coordinator_reconcile_probe_is_read_only_and_cli_is_strict(tmp_path, monkeypatch, capsys):
    import json
    from agent_dispatch import config, coordinator_cli, coordinator_observation, install_paths

    monkeypatch.setattr(install_paths, "install_dir", lambda: tmp_path)
    monkeypatch.setattr(install_paths, "capture_service_environment", lambda _root: dict(os.environ))
    monkeypatch.setattr(config, "load_config", lambda: object())
    monkeypatch.setattr(config, "routing_dir", lambda: tmp_path)
    monkeypatch.setattr(config, "run_dir", lambda: tmp_path)
    monkeypatch.setattr(config, "client_token", lambda: None)
    timeouts = []

    def observe(*_args, **kwargs):
        timeouts.append(kwargs.get("timeout"))
        return {"state": "inactive", "generation": None}

    monkeypatch.setattr(coordinator_observation, "observe", observe)
    monkeypatch.setattr(coordinator_cli, "_cmd_cutover", lambda *_args, **_kwargs: pytest.fail("mutation"))
    assert coordinator_observation.main(["--probe", "--timeout", "0.25"]) == 0
    assert timeouts == [0.25]
    assert json.loads(capsys.readouterr().out) == {
        "schema": coordinator_observation.SCHEMA, "version": 1, "ok": True,
        "state": "inactive", "generation": None,
        "lock_path": str(tmp_path / "serve-start.lock"),
    }
    with pytest.raises(SystemExit) as exc:
        coordinator_observation.main(["--apply", "--force"])
    assert exc.value.code == 2


def test_coordinator_probe_reports_the_lock_from_the_captured_service_environment(
    tmp_path, monkeypatch, capsys,
):
    import json
    from agent_dispatch import config, coordinator_observation, install_paths

    routing = tmp_path / "custom-routing"
    routing.mkdir()
    monkeypatch.setattr(install_paths, "install_dir", lambda: tmp_path)
    monkeypatch.setattr(install_paths, "capture_service_environment", lambda _root: {
        **os.environ, "AGENT_DISPATCH_ROUTING_DIR": str(routing), "FIXTURE_SECRET": "not-output",
    })
    monkeypatch.setattr(config, "load_config", lambda: object())
    monkeypatch.setattr(config, "run_dir", lambda: tmp_path)
    monkeypatch.setattr(config, "client_token", lambda: None)
    monkeypatch.setattr(coordinator_observation, "observe", lambda _cfg, root, **_kwargs: (
        {"state": "inactive", "generation": None} if root == routing else pytest.fail("wrong route")
    ))
    assert coordinator_observation.main(["--probe"]) == 0
    output = capsys.readouterr().out
    assert json.loads(output)["lock_path"] == str(config.coordinator_start_lock_path(routing))
    assert "not-output" not in output


def test_coordinator_probe_refuses_a_changed_routing_lock(tmp_path, monkeypatch, capsys):
    import json
    from agent_dispatch import config, coordinator_observation, install_paths

    routing, other = tmp_path / "routing", tmp_path / "other"
    routing.mkdir()
    other.mkdir()
    monkeypatch.setattr(install_paths, "install_dir", lambda: tmp_path)
    monkeypatch.setattr(install_paths, "capture_service_environment", lambda _root: {
        **os.environ, "AGENT_DISPATCH_ROUTING_DIR": str(routing),
    })
    monkeypatch.setattr(config, "load_config", lambda: object())
    monkeypatch.setattr(config, "run_dir", lambda: tmp_path)
    monkeypatch.setattr(config, "client_token", lambda: None)

    def observe(*_args, **_kwargs):
        os.environ["AGENT_DISPATCH_ROUTING_DIR"] = str(other)
        return {"state": "inactive", "generation": None}

    monkeypatch.setattr(coordinator_observation, "observe", observe)
    assert coordinator_observation.main(["--probe"]) == 1
    assert json.loads(capsys.readouterr().out)["state"] == "unknown"


def test_coordinator_reconcile_uses_selected_owner_without_starting_absent_service(
    tmp_path, monkeypatch, capsys,
):
    import json
    from agent_dispatch import config, coordinator_cli, coordinator_observation, install_paths, source_start

    monkeypatch.setattr(install_paths, "install_dir", lambda: tmp_path)
    monkeypatch.setattr(install_paths, "capture_service_environment", lambda _root: dict(os.environ))
    monkeypatch.setattr(source_start, "require_selected_generation", lambda: tmp_path)
    monkeypatch.setattr(config, "load_config", lambda: object())
    monkeypatch.setattr(config, "routing_dir", lambda: tmp_path)
    monkeypatch.setattr(config, "run_dir", lambda: tmp_path)
    monkeypatch.setattr(config, "client_token", lambda: None)
    monkeypatch.setattr(coordinator_observation, "observe", lambda *_args, **_kwargs: {
        "state": "inactive", "generation": None,
    })
    monkeypatch.setenv("UPDATE_DRIVER_WITNESS", "fixture-witness")
    calls = []

    def cutover(_args, **kwargs):
        assert "UPDATE_DRIVER_WITNESS" not in kwargs["source_environment"]
        assert "UPDATE_DRIVER_WITNESS" not in os.environ
        calls.append(kwargs)
        kwargs["result_sink"]({"ok": True, "status": "inactive"})
        return 0

    monkeypatch.setattr(coordinator_cli, "_cmd_cutover", cutover)
    assert coordinator_observation.main(["--apply"]) == 0
    assert os.environ["UPDATE_DRIVER_WITNESS"] == "fixture-witness"
    assert calls[0]["reconcile_existing"] is True
    assert json.loads(capsys.readouterr().out)["state"] == "inactive"


def test_coordinator_reconcile_inhibition_emits_one_json_document(tmp_path, monkeypatch, capsys):
    import json
    from agent_dispatch import action_controls, config, coordinator_observation, install_paths, source_start

    monkeypatch.setattr(install_paths, "install_dir", lambda: tmp_path)
    monkeypatch.setattr(install_paths, "capture_service_environment", lambda _root: dict(os.environ))
    monkeypatch.setattr(source_start, "require_selected_generation", lambda: tmp_path)
    monkeypatch.setattr(config, "load_config", lambda: object())
    monkeypatch.setattr(config, "client_token", lambda: None)
    monkeypatch.setattr(action_controls, "action_decision", lambda *_args, **_kwargs: SimpleNamespace(
        inhibited=True, scope="fixture", source="fixture", reason="fixture", error=None,
    ))

    assert coordinator_observation.main(["--apply"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result == {
        "schema": coordinator_observation.SCHEMA, "version": 1, "ok": False,
        "state": "unknown", "reason": "reconcile-unavailable",
    }


@pytest.mark.parametrize("inactive", [False, True])
def test_coordinator_reconcile_rechecks_selection_after_observation(tmp_path, monkeypatch, capsys, inactive):
    import json
    from agent_dispatch import __version__, config, coordinator_cli, coordinator_observation, install_paths, source_start

    monkeypatch.setattr(install_paths, "install_dir", lambda: tmp_path)
    monkeypatch.setattr(install_paths, "capture_service_environment", lambda _root: dict(os.environ))
    monkeypatch.setattr(config, "load_config", lambda: object())
    monkeypatch.setattr(config, "routing_dir", lambda: tmp_path)
    monkeypatch.setattr(config, "run_dir", lambda: tmp_path)
    monkeypatch.setattr(config, "client_token", lambda: None)
    deselected = False

    def selected():
        if deselected:
            raise ValueError("selection changed")
        return tmp_path

    def cutover(_args, **kwargs):
        kwargs["result_sink"]({"ok": True, "status": "inactive" if inactive else "current"})
        return 0

    def observe(*_args, **_kwargs):
        nonlocal deselected
        deselected = True
        return {"state": "inactive" if inactive else "ready", "generation": None if inactive else __version__}

    monkeypatch.setattr(source_start, "require_selected_generation", selected)
    monkeypatch.setattr(coordinator_cli, "_cmd_cutover", cutover)
    monkeypatch.setattr(coordinator_observation, "observe", observe)
    assert coordinator_observation.main(["--apply"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "unknown"
    assert result["reason"] == "selection-unavailable"


def test_coordinator_reconcile_reports_unselected_owner_before_effects(tmp_path, monkeypatch, capsys):
    import json
    from agent_dispatch import coordinator_cli, coordinator_observation, install_paths, source_start

    monkeypatch.setattr(install_paths, "install_dir", lambda: tmp_path)
    monkeypatch.setattr(source_start, "require_selected_generation", lambda: (
        (_ for _ in ()).throw(ValueError("private detail"))
    ))
    monkeypatch.setattr(coordinator_cli, "_cmd_cutover", lambda *_args, **_kwargs: pytest.fail("mutation"))
    assert coordinator_observation.main(["--apply"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["reason"] == "selection-unavailable"
    assert "private detail" not in repr(result)


def test_coordinator_reconcile_lock_wait_respects_requested_timeout(
    tmp_path, monkeypatch, isolated_service_environment,
):
    from agent_dispatch import action_controls, config, coordinator_cli, install_paths, single_instance, source_start

    clock = SimpleNamespace(value=0.0)
    clock.monotonic = lambda: clock.value
    clock.sleep = lambda seconds: setattr(clock, "value", clock.value + seconds)
    monkeypatch.setattr(coordinator_cli, "_resolve_cli_module", lambda: SimpleNamespace(time=clock))
    monkeypatch.setattr(coordinator_cli, "_client_token_value", lambda: None)
    monkeypatch.setattr(source_start, "require_selected_generation", lambda: tmp_path)
    monkeypatch.setattr(install_paths, "install_dir", lambda: tmp_path)
    monkeypatch.setattr(config, "routing_dir", lambda: tmp_path)
    monkeypatch.setattr(config, "load_config", lambda: SimpleNamespace(host="127.0.0.1"))
    monkeypatch.setattr(action_controls, "action_decision", lambda *_args, **_kwargs: SimpleNamespace(inhibited=False))
    monkeypatch.setattr(single_instance, "SingleInstance", lambda _path: SimpleNamespace(acquire=lambda: False))
    monkeypatch.setattr(single_instance, "read_holder_pid", lambda _path: None)
    args = argparse.Namespace(json=True, health_timeout=0.3, drain_timeout=0.3, force=False, recover=False)
    assert coordinator_cli._cmd_cutover(
        args, source_environment=dict(os.environ), reconcile_existing=True, result_sink=lambda _result: None,
    ) == 2
    assert clock.value <= 0.3


def test_coordinator_reconcile_rejects_inherited_driver_witness(tmp_path, monkeypatch):
    from agent_dispatch import action_controls, coordinator_cli, source_start

    monkeypatch.setattr(source_start, "require_selected_generation", lambda: tmp_path)
    monkeypatch.setattr(action_controls, "action_decision", lambda *_args, **_kwargs: pytest.fail("effect"))
    args = argparse.Namespace(json=True, health_timeout=1.0, drain_timeout=1.0, force=False, recover=False)
    with pytest.raises(ValueError, match="reconcile"):
        coordinator_cli._cmd_cutover(
            args, source_environment={"UPDATE_DRIVER_WITNESS": "fixture"},
            reconcile_existing=True, result_sink=lambda _result: None,
        )


def test_coordinator_reconcile_reuses_native_owner_without_broad_reaping(
    tmp_path, monkeypatch, isolated_service_environment,
):
    from agent_dispatch import action_controls, config, coordinator_cli, coordinator_observation
    from agent_dispatch import install_paths, single_instance, source_start
    from zdd import breadcrumb

    events = []
    monkeypatch.setattr(coordinator_cli, "_resolve_cli_module", lambda: SimpleNamespace())
    monkeypatch.setattr(coordinator_cli, "_client_token_value", lambda: None)
    monkeypatch.setattr(source_start, "require_selected_generation", lambda: tmp_path)
    monkeypatch.setattr(install_paths, "install_dir", lambda: tmp_path)
    monkeypatch.setattr(config, "routing_dir", lambda: tmp_path)
    monkeypatch.setattr(config, "run_dir", lambda: tmp_path)
    monkeypatch.setattr(config, "load_config", lambda: SimpleNamespace(host="127.0.0.1"))
    monkeypatch.setattr(action_controls, "action_decision", lambda *_args, **_kwargs: SimpleNamespace(inhibited=False))
    monkeypatch.setattr(single_instance, "SingleInstance", lambda _path: SimpleNamespace(
        acquire=lambda: events.append("lock") or True, release=lambda: events.append("release"),
    ))
    monkeypatch.setattr(breadcrumb, "recover_stale_cutover", lambda *_args, **_kwargs: pytest.fail("broad recovery"))
    monkeypatch.setattr(coordinator_observation, "observe", lambda *_args, **_kwargs: {
        "state": "inactive", "generation": None,
    })

    def run(_cfg, _routing, **kwargs):
        assert kwargs["start_if_missing"] is False
        assert kwargs["verify_selected"]() == tmp_path
        assert kwargs["confirm_inactive"]({}) is True
        events.append("owner")
        return {"ok": True, "status": "inactive"}

    monkeypatch.setattr(source_start, "run_coordinator", run)
    results = []
    args = argparse.Namespace(json=True, health_timeout=5.0, drain_timeout=5.0, force=False, recover=False)
    assert coordinator_cli._cmd_cutover(
        args, source_environment=dict(os.environ), reconcile_existing=True, result_sink=results.append,
    ) == 0
    assert events == ["lock", "owner", "release"]
    assert results == [{"ok": True, "status": "inactive"}]
    events.clear()
    monkeypatch.setattr(coordinator_observation, "observe", lambda *_args, **_kwargs: (
        (_ for _ in ()).throw(coordinator_observation.ServingObservationError("unknown"))
    ))
    with pytest.raises(coordinator_observation.ServingObservationError):
        coordinator_cli._cmd_cutover(
            args, source_environment=dict(os.environ), reconcile_existing=True, result_sink=results.append,
        )
    assert events == ["lock", "release"]


@pytest.mark.parametrize("configured", [False, True])
def test_source_supervisor_never_treats_unknown_declarations_as_disabled(tmp_path, monkeypatch, configured):
    from agent_dispatch import pool_source_snapshot, source_start

    monkeypatch.setattr(pool_source_snapshot, "read_supervisor_declarations", lambda _: (
        pool_source_snapshot.DeclarationRead((), configured, "unconfirmed" if not configured else "")
    ))
    if configured:
        assert source_start.ensure_supervisor(tmp_path) == {"status": "unconfigured"}
    else:
        with pytest.raises(RuntimeError, match="declaration source is unconfirmed"):
            source_start.ensure_supervisor(tmp_path)


@pytest.mark.parametrize("ready,stale", [(False, False), (True, False), (True, True)])
def test_source_coordinator_uses_actual_guarded_publication_and_reuses_owner(
    tmp_path, monkeypatch, ready, stale,
):
    import os
    from agent_dispatch import client, source_start
    from agent_dispatch.config import Config
    from zdd import diagnostics, routing

    cfg = Config(host="127.0.0.1", db_path=str(tmp_path / "tasks.db"))
    identity = source_start.config_identity(cfg)
    birth = diagnostics.process_start_time(os.getpid())
    assert birth
    monkeypatch.setattr(source_start, "require_selected_source", lambda: tmp_path)
    spawned = []

    class Handle:
        pid = os.getpid()
        exited = False

        def poll(self):
            return 0 if self.exited else None

        def terminate(self):
            self.exited = True

    handle = Handle()
    if stale:
        routing.publish_active(
            tmp_path, bind="127.0.0.1", port=43101, pid=handle.pid,
            version=source_start.__version__, process_start_time=birth,
        )
        monkeypatch.setattr(source_start, "_route_owner_state", lambda owner: (
            "dead" if owner.port == 43101 else "matching"
        ))
        monkeypatch.setattr(routing, "_listening", lambda *_args, **_kwargs: False)

    def spawn(port):
        spawned.append(port)
        return handle

    class Client:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def health(self):
            active = (routing.read_table(tmp_path) or {}).get("active")
            return {
                "status": "ok", "version": source_start.__version__,
                "runtime_config_identity": identity if ready else "wrong-config",
                "slot": {
                    "pid": handle.pid, "process_start_time": birth,
                    "role": "active" if active else "passive",
                },
            }

    monkeypatch.setattr(client, "DispatchClient", Client)
    result = source_start.run_coordinator(
        cfg, tmp_path, token=None, spawn_passive=spawn, pick_free_port=lambda: 43123, timeout=0,
    )
    assert result["ok"] is ready
    if not ready:
        assert handle.exited
        assert (routing.read_table(tmp_path) or {}).get("active") is None
    else:
        active = routing.read_table(tmp_path)["active"]
        assert active["pid"] == os.getpid()
        assert active["process_start_time"] == birth
        repeated = source_start.run_coordinator(
            cfg, tmp_path, token=None, spawn_passive=spawn, pick_free_port=lambda: 43124, timeout=0,
        )
        assert repeated["status"] == "reused"
        assert spawned == [43123]
