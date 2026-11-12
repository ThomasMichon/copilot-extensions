"""Launcher controls with no actual daemon, registry or host-runtime effects."""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_mcp import custody_launch as launch, incoming
from agent_mcp.custody_contract import CONTRACT, backend_descriptor
from agent_mcp.execution_context import SharingUnavailable
from .test_custody_conduit import Connection


@pytest.fixture
def admitted(tmp_path, monkeypatch):
    root = tmp_path / "installation"
    root.mkdir()
    selector = root / "current-version"
    selector.write_text("incoming", encoding="utf-8")
    handle = tmp_path / "current" / "serve.sock"
    reply = {
        "ok": True, "pong": True, "ready": True, "draining": False,
        "version": "incoming", "runtime_root": str(root),
        "fresh_admission": incoming.CONTRACT, "custody_backend": CONTRACT,
    }
    calls, spawns, backend_connections = [], [], []

    def connect(path, **kwargs):
        assert Path(path) == handle
        connection = Connection([reply])
        backend_connections.append(connection)
        return connection, "private-fixture-token"
    monkeypatch.setattr(launch.sockio, "_connect", connect)
    client = Connection([])

    def attach(keeper, bridge, runtime_root, **kwargs):
        calls.append((keeper, bridge, runtime_root, kwargs))
        return client
    monkeypatch.setattr(launch, "attach_conduit", attach)
    monkeypatch.setattr(launch, "_spawn", lambda *args: spawns.append(args))
    return SimpleNamespace(
        root=root, selector=selector, handle=handle, reply=reply,
        admission=incoming.Admission("incoming", str(root)), calls=calls, spawns=spawns,
        client=client, backend_connections=backend_connections,
    )


def test_existing_keeper_uses_admitted_root_and_separate_namespace(admitted):
    fixture = admitted
    assert launch.attach(fixture.handle, "fixture", fixture.admission) is fixture.client
    assert fixture.calls == [(
        fixture.handle.parent / "custody-v1" / fixture.handle.name,
        "fixture", fixture.root, {},
    )]
    assert not fixture.spawns and all(item.closed for item in fixture.backend_connections)


def test_required_trace_binding_rejects_legacy_backend_before_keeper_attach(admitted):
    with pytest.raises(SharingUnavailable, match="backend cannot enforce"):
        launch.attach(
            admitted.handle, "fixture", admitted.admission, required_trace_bookkeeping=1,
        )
    assert not admitted.calls and not admitted.spawns
    assert all(item.closed for item in admitted.backend_connections)


@pytest.mark.parametrize("cold", [False, True])
def test_required_trace_binding_survives_existing_and_cold_keeper_paths(admitted, monkeypatch, cold):
    admitted.reply["custody_backend"] = backend_descriptor()
    attempts = []
    def attach(*args, **kwargs):
        attempts.append(kwargs)
        if cold and len(attempts) == 1:
            raise launch.sockio.HostUnreachableError("fixture keeper absent")
        return admitted.client
    monkeypatch.setattr(launch, "attach_conduit", attach)
    assert launch.attach(
        admitted.handle, "fixture", admitted.admission, required_trace_bookkeeping=1,
    ) is admitted.client
    assert len(attempts) == 1 + cold
    assert all(item["required_trace_bookkeeping"] == 1 for item in attempts)
    assert len(admitted.spawns) == int(cold)


@pytest.mark.parametrize("case", ["legacy", "stale", "foreign", "draining", "unselected"])
def test_unqualified_backend_never_uses_or_spawns_keeper(admitted, case):
    fixture = admitted
    if case == "legacy":
        fixture.reply.pop("custody_backend")
    elif case == "stale":
        fixture.reply["version"] = "outgoing"
    elif case == "foreign":
        fixture.reply["runtime_root"] = str(fixture.root.parent)
    elif case == "draining":
        fixture.reply["draining"] = True
    else:
        fixture.selector.write_text("newer", encoding="utf-8")
    with pytest.raises(OSError):
        launch.attach(fixture.handle, "fixture", fixture.admission)
    assert not fixture.calls and not fixture.spawns


def test_missing_backend_authentication_is_not_admission(admitted, monkeypatch):
    connection = Connection([admitted.reply])
    monkeypatch.setattr(launch.sockio, "_connect", lambda *args, **kwargs: (connection, None))
    with pytest.raises(SharingUnavailable, match="authentication"):
        launch.attach(admitted.handle, "fixture", admitted.admission)
    assert connection.closed and not connection.requests
    assert not admitted.calls and not admitted.spawns


@pytest.mark.parametrize("ensure", [False, True])
def test_keeper_unreachable_never_escapes_as_backend_reclamation(admitted, monkeypatch, ensure):
    attempts = []
    clock = [0.0]
    monkeypatch.setattr(launch.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(launch.time, "sleep", lambda delay: clock.__setitem__(0, clock[0] + 10))

    def unavailable(*args, **kwargs):
        attempts.append((args, kwargs))
        raise launch.sockio.HostUnreachableError("fixture keeper absent")
    monkeypatch.setattr(launch, "attach_conduit", unavailable)
    with pytest.raises(SharingUnavailable) as failure:
        launch.attach(admitted.handle, "fixture", admitted.admission, ensure=ensure)
    assert not isinstance(failure.value, launch.sockio.HostUnreachableError)
    assert len(admitted.spawns) == int(ensure)
    assert len(attempts) == (2 if ensure else 1)


def test_racing_keeper_start_has_one_owned_spawn_and_no_backend_handle_deletion(admitted, monkeypatch):
    attempts = []

    def attach(*args, **kwargs):
        attempts.append((args, kwargs))
        if len(attempts) == 1:
            raise launch.sockio.HostUnreachableError("fixture keeper absent")
        return admitted.client
    monkeypatch.setattr(launch, "attach_conduit", attach)
    assert launch.attach(admitted.handle, "fixture", admitted.admission) is admitted.client
    assert len(admitted.spawns) == 1 and len(attempts) == 2
    assert 0 < attempts[-1][1]["timeout"] <= 1


def test_live_keeper_refusal_never_spawns_replacement(admitted, monkeypatch):
    def refuse(*args, **kwargs):
        raise SharingUnavailable("fixture incompatible keeper")
    monkeypatch.setattr(launch, "attach_conduit", refuse)
    with pytest.raises(SharingUnavailable, match="incompatible"):
        launch.attach(admitted.handle, "fixture", admitted.admission)
    assert not admitted.spawns


def test_selection_drift_closes_only_new_connection(admitted, monkeypatch):
    def attach(*args, **kwargs):
        admitted.selector.write_text("newer", encoding="utf-8")
        return admitted.client
    monkeypatch.setattr(launch, "attach_conduit", attach)
    with pytest.raises(OSError):
        launch.attach(admitted.handle, "fixture", admitted.admission)
    assert admitted.client.closed and not admitted.spawns


def test_spawn_is_attributed_separately_and_never_uses_inherited_production_lifetime(tmp_path, monkeypatch):
    import agent_procutil

    captured = []
    monkeypatch.setattr(launch.sockio, "default_home_dir", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(agent_procutil, "windowless_python", lambda value: value)
    monkeypatch.setattr(agent_procutil, "windowless_python_env", lambda value: {})
    monkeypatch.setattr(agent_procutil, "spawn_daemon", lambda *args, **kwargs: captured.append((args, kwargs)))
    # Exercise the actual constructor, not the mock used by the admission fixture.
    keeper = tmp_path / "current" / "custody-v1" / "serve.sock"
    backend = keeper.parent.parent / "serve.sock"
    launch._spawn(keeper, backend, tmp_path)
    args, kwargs = captured[0]
    assert args[0][1:3] == ["-m", "agent_mcp.custody_host"]
    assert kwargs["instance"].service == "agent-mcp-custody"
    assert kwargs["instance"].runtime_root == keeper.parent
    assert kwargs["contained_fallback"] is False
    assert kwargs["cwd"] == tmp_path
    assert kwargs["env_overrides"]["AGENT_MCP_HOME"] == str(tmp_path)


def test_incompatible_actual_cwd_is_not_rewritten_for_spawn(tmp_path, monkeypatch):
    import agent_procutil

    original = os.getcwd()
    monkeypatch.setattr(launch.sockio, "default_home_dir", lambda: tmp_path)
    monkeypatch.setattr(
        agent_procutil, "spawn_daemon",
        lambda *args, **kwargs: pytest.fail("incompatible caller spawned a keeper"),
    )
    with pytest.raises(SharingUnavailable, match="actual"):
        launch._spawn(tmp_path / "keeper" / "serve.sock", tmp_path / "backend" / "serve.sock", tmp_path)
    assert os.getcwd() == original
