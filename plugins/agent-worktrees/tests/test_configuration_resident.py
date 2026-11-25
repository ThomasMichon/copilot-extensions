"""The optional resident path retains fresh native reads and legacy fallback."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from agent_worktrees import config as cfg
from agent_worktrees import configuration_resident as resident
from agent_worktrees.status_monitor_cutover import ControlServer


Owner = tuple[Path, ControlServer, list[str], dict[str, Any], dict[str, Any]]
NativeConfigurationPaths = tuple[Path, dict[str, Path], dict[str, Path]]


def _publication(
    port: int, *, ready: bool = True, published: bool = True,
) -> dict[str, Any]:
    from agent_worktrees.status_monitor_publication import lock_extra

    empty = SimpleNamespace(lock_extra=lambda: {})
    return lock_extra(
        prefix="fixture", mux=None, generation="a" * 32,
        version="fixture", slot="fixture-slot", installation_context=None,
        hook_server=None, classify_server=None, tracking_write_server=None,
        published=published,
        worktrees_runtime=SimpleNamespace(hook_facts=SimpleNamespace(generation=1)),
        worktree_status_runtime=empty, managed_mux_runtime=empty,
        configuration_ready=ready, control_port=port,
        classify_fields=lambda value: {}, tracking_fields=lambda value: {},
    )


@pytest.fixture
def owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Owner]:
    root = tmp_path / "runtime"
    root.mkdir()
    monkeypatch.setattr(resident, "_root", lambda: root)
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.delenv("AGENT_CONFIGURATION_CONTEXT", raising=False)
    token = "fixture-control-token"
    (root / "status-monitor-control.token").write_text(token, encoding="utf-8")
    actions: list[str] = []
    report: dict[str, Any] = {
        "schema": "agent-worktrees.configuration", "version": 1,
        "context": {"execution_machine": "first"},
    }
    health: dict[str, Any] = {
        "status": "ready", "published": True, "pid": os.getpid(),
        "process_start_time": "123", "capabilities": [resident.CAPABILITY],
        "runtime_generation": "a" * 32,
    }

    def handle(action: str, payload: dict[str, Any]) -> dict[str, Any]:
        actions.append(action)
        if action == "health":
            return health
        assert action == resident.ACTION
        assert payload["registry_root"] == str(root)
        return {"contract_version": 1, "status": "ok", "report": report}

    server = ControlServer(handle, token=token)
    server.start()
    (root / "status-monitor.lock").write_text(json.dumps({
        "pid": os.getpid(), "start_time": "123", **_publication(server.port),
    }), encoding="utf-8")
    try:
        yield root, server, actions, report, health
    finally:
        server.close()


def test_native_transport_reads_each_time_and_old_provider_skips_optional_action(
    owner: Owner,
) -> None:
    _root, _server, actions, report, health = owner
    first = resident.try_read(os.getcwd())
    assert first is not None
    assert first["context"]["execution_machine"] == "first"
    report["context"]["execution_machine"] = "second"
    second = resident.try_read(os.getcwd())
    assert second is not None
    assert second["context"]["execution_machine"] == "second"
    assert actions == ["health", resident.ACTION, "health", resident.ACTION]
    health["capabilities"] = []
    assert resident.try_read(os.getcwd()) is None
    assert actions[-1] == "health"
    assert actions.count(resident.ACTION) == 2


def test_early_entry_does_not_import_full_config_for_a_resident_read(
    owner: Owner, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    import builtins
    from agent_worktrees import configuration_entry

    original = builtins.__import__

    def forbid_config(
        name: str, globals: Mapping[str, Any] | None = None,
        locals: Mapping[str, Any] | None = None, fromlist: Sequence[str] = (),
        level: int = 0,
    ) -> ModuleType:
        assert name != "agent_worktrees.config"
        assert "config" not in (fromlist or ())
        return original(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", forbid_config)
    assert configuration_entry._run() == 0
    assert json.loads(capsys.readouterr().out)["schema"] == "agent-worktrees.configuration"


@pytest.mark.parametrize("field,value", [
    ("published", False), ("status", "draining"), ("pid", -1),
    ("process_start_time", "replaced"), ("capabilities", None),
    ("runtime_generation", "replaced"),
])
def test_unqualified_resident_retains_standalone_fallback(
    owner: Owner, field: str, value: object,
) -> None:
    _root, _server, actions, _report, health = owner
    health[field] = value
    assert resident.try_read(os.getcwd()) is None
    assert actions == ["health"]


def test_invalid_authentication_does_not_read_configuration(owner: Owner) -> None:
    root, _server, actions, _report, _health = owner
    (root / "status-monitor-control.token").write_text("wrong-token", encoding="utf-8")
    assert resident.try_read(os.getcwd()) is None
    assert actions == []


def test_changed_route_discards_read_data(
    owner: Owner, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _server, _actions, _report, _health = owner
    first = resident._read_discovery(root)
    answers = iter([first, None])
    monkeypatch.setattr(resident, "_read_discovery", lambda *a, **k: next(answers))
    assert resident.try_read(os.getcwd()) is None


@pytest.mark.parametrize("change", ["old-version", "foreign-scope", "non-loopback"])
def test_unsupported_discovery_does_not_send_optional_requests(owner: Owner, change: str) -> None:
    root, _server, actions, _report, _health = owner
    path = root / "status-monitor.lock"
    lock = json.loads(path.read_text(encoding="utf-8"))
    if change == "old-version":
        lock["configuration_owner"]["contract_version"] = 2
    elif change == "foreign-scope":
        lock["installReceipt"] = "foreign"
    else:
        lock["configuration_owner"]["endpoint"] = "192.0.2.1:1234"
    path.write_text(json.dumps(lock), encoding="utf-8")
    assert resident.try_read(os.getcwd()) is None
    assert actions == []


@pytest.mark.parametrize("ready,published", [(False, True), (True, False)])
def test_unready_or_unpublished_provider_does_not_advertise_the_optional_read(
    owner: Owner, ready: bool, published: bool,
) -> None:
    root, server, actions, _report, _health = owner
    (root / "status-monitor.lock").write_text(json.dumps({
        "pid": os.getpid(), "start_time": "123",
        **_publication(server.port, ready=ready, published=published),
    }), encoding="utf-8")
    assert resident.try_read(os.getcwd()) is None
    assert actions == []


def test_preparing_the_provider_does_not_acquire_or_cache_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "_machine_config_layers", lambda *a, **k: pytest.fail("configuration read"))
    assert resident.prepare()


@pytest.mark.parametrize("key", ["COPILOT_EXTENSIONS_CONTEXT", "AGENT_CONFIGURATION_CONTEXT"])
def test_scoped_context_does_not_use_legacy_resident(
    monkeypatch: pytest.MonkeyPatch, key: str,
) -> None:
    monkeypatch.setenv(key, "{}")
    monkeypatch.setattr(resident, "_root", lambda: pytest.fail("legacy root acquired"))
    assert resident.try_read(os.getcwd()) is None


def test_receiver_uses_fresh_native_config_without_changing_active_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees import record_config, repos

    anchor = tmp_path / "anchor"
    anchor.mkdir()
    local = tmp_path / "project"
    local.mkdir()
    path = local / "config.yaml"
    raw: dict[str, Any] = {"machine": "first", "repos": {"sample": {"anchor": str(anchor)}}}
    path.write_text(json.dumps(raw), encoding="utf-8")
    registry = repos.ReposRegistry(repos={
        "sample": repos.RepoEntry(
            name="sample", remote="https://example.test/owner/sample.git",
            paths={platform: str(anchor) for platform in ("windows", "linux", "wsl")},
        ),
    })
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.delenv("AGENT_CONFIGURATION_CONTEXT", raising=False)
    monkeypatch.setattr(resident, "_root", lambda: tmp_path)
    monkeypatch.setattr(record_config, "metadata_project", lambda *a, **k: "sample")
    monkeypatch.setattr(cfg, "project_dir", lambda *a: local)
    monkeypatch.setattr(cfg, "global_config_path", lambda: tmp_path / "absent.yaml")
    monkeypatch.setattr(cfg, "_ACTIVE_PROJECT", "unrelated")
    monkeypatch.setattr(repos, "read_registry", lambda **k: registry)
    monkeypatch.setattr(cfg, "_build_repo_config", lambda *a, **k: pytest.fail("launch policy"))
    payload: dict[str, Any] = {
        "contract_version": 1, "cwd": str(anchor),
        "registry_root": str(tmp_path), "platform": sys.platform,
    }
    result = resident.read_configuration(payload, runtime_home=tmp_path)
    assert result["status"] == "ok"
    assert result["report"]["context"]["execution_machine"] == "first"
    raw["machine"] = "second"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert resident.read_configuration(payload, runtime_home=tmp_path)[
        "report"
    ]["context"]["execution_machine"] == "second"
    assert cfg.active_project() == "unrelated"
    raw["repos"]["sample"]["anchor"] = "relative"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert resident.read_configuration(payload, runtime_home=tmp_path)["status"] == "unavailable"
    raw["repos"]["sample"]["anchor"] = str(anchor)
    raw["knowledge_repo"] = "knowledge"
    path.write_text(json.dumps(raw), encoding="utf-8")
    registry.repos["knowledge"] = repos.RepoEntry(
        name="knowledge", paths={cfg.detect_platform(): "relative-knowledge"},
    )
    (tmp_path / "relative-knowledge").mkdir()
    monkeypatch.chdir(tmp_path)
    assert resident.read_configuration(payload, runtime_home=tmp_path)["status"] == "unavailable"
    payload["registry_root"] = str(tmp_path / "foreign")
    assert resident.read_configuration(payload, runtime_home=tmp_path)["status"] == "unavailable"


@pytest.fixture
def native_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> NativeConfigurationPaths:
    from agent_worktrees import repos

    runtime = tmp_path / ".agent-worktrees"
    runtime.mkdir()
    anchors: dict[str, Path] = {}
    local_roots: dict[str, Path] = {}

    def project_dir(name: str | None = None) -> Path:
        return runtime / "project-settings" / (name or cfg.active_project() or "unrelated")

    for name in ("discovered", "configured", "global-project"):
        anchors[name] = tmp_path / name
        (anchors[name] / ".git").mkdir(parents=True)
        local_roots[name] = project_dir(name)
        local_roots[name].mkdir(parents=True)
    (runtime / "projects.yaml").write_text(json.dumps({
        "projects": {"discovered": {"anchor": str(anchors["discovered"])}},
    }), encoding="utf-8")
    registry = repos.ReposRegistry(repos={
        name: repos.RepoEntry(
            name=name, remote=f"https://example.test/owner/{name}.git",
            paths={platform: str(anchor) for platform in ("windows", "linux", "wsl")},
        )
        for name, anchor in anchors.items()
    })
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.delenv("AGENT_CONFIGURATION_CONTEXT", raising=False)
    monkeypatch.setenv("AGENT_HOME", str(tmp_path))
    monkeypatch.setattr(cfg, "install_dir", lambda: runtime)
    monkeypatch.setattr(cfg, "project_dir", project_dir)
    monkeypatch.setattr(cfg, "global_config_path", lambda: runtime / "global.yaml")
    monkeypatch.setattr(cfg, "_ACTIVE_PROJECT", "unrelated")
    monkeypatch.setattr(cfg, "_load_inrepo_config", lambda *a, **k: {})
    monkeypatch.setattr(cfg, "_build_repo_config", lambda *a, **k: pytest.fail("launch policy"))
    monkeypatch.setattr(repos, "read_registry", lambda **k: registry)
    return runtime, anchors, local_roots


@pytest.mark.parametrize("configured_by", ["machine", "global"])
def test_discovery_does_not_override_native_repository_name(
    native_configuration: NativeConfigurationPaths, configured_by: str,
) -> None:
    from agent_worktrees import config_sources, project_state, record_config

    runtime, anchors, local_roots = native_configuration
    caller = anchors["discovered"]
    path = local_roots["discovered"] / "config.yaml"
    machine: dict[str, Any] = {
        "machine": "fixture-host",
        "repos": {name: {"anchor": str(anchor)} for name, anchor in anchors.items()},
    }
    if configured_by == "machine":
        machine["repo_name"] = "configured"
    path.write_text(json.dumps(machine), encoding="utf-8")
    (runtime / "global.yaml").write_text(json.dumps({
        "repo_name": "global-project" if configured_by == "machine" else "configured",
    }), encoding="utf-8")
    assert record_config.metadata_project(None, cwd=caller, absolute_only=True) == "discovered"
    standalone = cfg.load_configuration_identity(path)
    assert standalone.repo_name == "configured"
    expected = config_sources.configuration_report(standalone, cwd=str(caller))
    result = resident.read_configuration({
        "contract_version": 1, "cwd": str(caller),
        "registry_root": str(runtime), "platform": sys.platform,
    }, runtime_home=runtime)
    assert result["status"] == "ok"
    assert result["report"] == expected
    assert result["report"]["context"]["repository_id"] == project_state.canonical_repository_id(
        "configured",
    )
    assert result["report"]["context"]["repository_id"] != project_state.canonical_repository_id(
        "discovered",
    )
    assert result["report"]["context"]["base_root"] == str(anchors["configured"])
    assert result["report"]["repository_machine_root"] == str(local_roots["configured"])
    assert cfg.load_configuration_identity(
        path, project="discovered", fallback_project="configured",
    ).repo_name == "discovered"
    assert cfg.active_project() == "unrelated"


@pytest.mark.parametrize("failure_at", ["directory", "fragment"])
def test_resident_refuses_retained_native_dropins_but_standalone_keeps_them(
    native_configuration: NativeConfigurationPaths, monkeypatch: pytest.MonkeyPatch,
    failure_at: str,
) -> None:
    from agent_worktrees import config_acquisition, config_dropins

    runtime, anchors, local_roots = native_configuration
    path = local_roots["discovered"] / "config.yaml"
    path.write_text(json.dumps({
        "repo_name": "discovered",
        "repos": {"discovered": {"anchor": str(anchors["discovered"])}},
    }), encoding="utf-8")
    directory = path.parent / "config.d"
    directory.mkdir()
    fragment = directory / "machine.yaml"
    fragment.write_text("machine: first-fixture-host\n", encoding="utf-8")
    monkeypatch.setattr(config_dropins, "_LAST_KNOWN", {})
    payload: dict[str, Any] = {
        "contract_version": 1, "cwd": str(anchors["discovered"]),
        "registry_root": str(runtime), "platform": sys.platform,
    }
    first = resident.read_configuration(payload, runtime_home=runtime)
    assert first["status"] == "ok"
    assert first["report"]["context"]["execution_machine"] == "first-fixture-host"
    denied: list[Path] = []
    original_iterdir = Path.iterdir
    original_read_text = Path.read_text

    def unreadable_directory(source: Path) -> Iterator[Path]:
        if source == directory:
            denied.append(source)
            raise PermissionError("fixture directory read denied")
        return original_iterdir(source)

    def unreadable_fragment(
        source: Path, encoding: str | None = None, errors: str | None = None,
    ) -> str:
        if source == fragment:
            denied.append(source)
            raise PermissionError("fixture fragment read denied")
        return original_read_text(source, encoding=encoding, errors=errors)

    with monkeypatch.context() as blocked:
        if failure_at == "directory":
            blocked.setattr(Path, "iterdir", unreadable_directory)
        else:
            blocked.setattr(Path, "read_text", unreadable_fragment)
        with config_acquisition.capture_config_observations() as observations:
            standalone = cfg.load_configuration_identity(path)
        assert standalone.machine == "first-fixture-host"
        assert any(
            observation.retained and observation.source == str(fragment)
            for observation in observations
        )
        denied.clear()
        assert resident.read_configuration(payload, runtime_home=runtime) == {
            "contract_version": 1, "status": "unavailable",
        }
        assert denied
    fragment.write_text("machine: second-fixture-host\n", encoding="utf-8")
    recovered = resident.read_configuration(payload, runtime_home=runtime)
    assert recovered["status"] == "ok"
    assert recovered["report"]["context"]["execution_machine"] == "second-fixture-host"
    assert cfg.active_project() == "unrelated"


def test_new_action_rejects_unknown_frame_version(owner: Owner) -> None:
    _root, server, actions, _report, _health = owner
    import socket

    with socket.create_connection(("127.0.0.1", server.port), timeout=1) as stream:
        stream.sendall(json.dumps({
            "version": 2, "token": "fixture-control-token",
            "action": resident.ACTION, "payload": {},
        }).encode() + b"\n")
        stream.shutdown(socket.SHUT_WR)
        response = json.loads(stream.makefile("rb").readline())
    assert response["result"]["status"] == "unavailable"
    assert actions == []
