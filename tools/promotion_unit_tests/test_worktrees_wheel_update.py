"""Actor-free checks for prepared Worktrees wheel-source selection."""
from __future__ import annotations

import dataclasses
import importlib.util
import json
import sys
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "plugins" / "agent-worktrees" / "src"),
    str(ROOT / "libs" / "agent-procutil" / "src"),
]
from agent_worktrees import update_runtime_source as source_owner
from agent_worktrees import update_runtime_target as target_owner


@pytest.fixture
def native_driver(monkeypatch):
    class Refused(RuntimeError):
        pass

    modules = {}
    for name in (
        "installer_engine", "versioned_runtime", "versioned_runtime_source",
        "runtime_install_publication", "runtime_install_driver",
        "runtime_install_governance", "install_source",
    ):
        modules[name] = ModuleType(name)
        monkeypatch.setitem(sys.modules, name, modules[name])
    modules["runtime_install_driver"].owned = lambda function: function
    modules["runtime_install_driver"].SOURCE_SELECTION_ENV = target_owner.WHEEL_SELECTION_ENV
    modules["runtime_install_governance"].Governance = object
    modules["runtime_install_governance"].InstallationRefused = Refused
    monkeypatch.setattr(sys, "path", list(sys.path))
    path = ROOT / "plugins" / "agent-worktrees" / "scripts" / "runtime_install.py"
    spec = importlib.util.spec_from_file_location("_worktrees_wheel_install_unit", path)
    assert spec and spec.loader
    driver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(driver)
    return driver, modules, Refused


@pytest.mark.parametrize(("platform", "input_option", "descriptor_option"), [
    ("windows", "-InstallSource", "-RuntimeSourceDescriptor"),
    ("linux", "--install-source", "--runtime-source-descriptor"),
])
def test_wheel_update_hands_the_same_source_to_shell_and_lifecycle(
    tmp_path, monkeypatch, platform, input_option, descriptor_option,
):
    import agent_worktrees

    reconcile = ModuleType("agent_worktrees.reconcile")
    update_cli = ModuleType("agent_worktrees.update_cli")
    update_cli._installer_timeout = lambda *_args: 60
    for name, module in (("reconcile", reconcile), ("update_cli", update_cli)):
        monkeypatch.setitem(sys.modules, f"agent_worktrees.{name}", module)
        monkeypatch.setattr(agent_worktrees, name, module, raising=False)
    path = ROOT / "plugins" / "agent-worktrees" / "src" / "agent_worktrees" / "update_runtime_plan.py"
    spec = importlib.util.spec_from_file_location("agent_worktrees._wheel_plan_unit", path)
    assert spec and spec.loader
    plan_owner = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, plan_owner)
    spec.loader.exec_module(plan_owner)

    payload, owner, snapshot = (tmp_path / name for name in ("payload", "owner", "snapshot"))
    for path in (payload, owner, snapshot):
        path.mkdir()
    identity = {
        "plugin": "agent-worktrees", "package_version": "1.0", "runtime_version": "1.0+source",
    }
    source = source_owner.PreparedRuntimeSource(
        payload, json.dumps({"runtime_source": identity, "runtime_version": identity["runtime_version"]}),
        snapshot,
    )
    descriptor = {"kind": "github", "canonical": "github:example/repo", "ref": ""}
    target = target_owner.SourceWheelTarget(json.dumps({
        "source_descriptor": descriptor, "runtime_version": "1.0+source.profile." + "a" * 16,
    }))
    runtime = tmp_path / "runtime"
    monkeypatch.setattr(plan_owner, "_select_inputs", lambda step, *_a, **_kw: dataclasses.replace(
        step, source=source, source_target=target, runtime_root=runtime,
        target_version=target.version, project_root=tmp_path,
    ))
    monkeypatch.setattr(plan_owner, "_deployed_version", lambda _step: None)
    monkeypatch.setattr(plan_owner, "_shell_prefix", lambda *_args: ([sys.executable, "installer"], True))
    monkeypatch.setattr(source_owner.shutil, "which", lambda _name: sys.executable)

    step = plan_owner.plan_engine(owner, platform, force=True, source=source)
    assert not step.blocked
    assert step.argv[step.argv.index(input_option) + 1] == str(snapshot)
    assert json.loads(step.argv[step.argv.index(descriptor_option) + 1]) == descriptor
    command = source.lifecycle_argv(owner, "agent-worktrees", runtime, tmp_path, step.argv, target=target)
    assert json.loads(command[command.index("--source-descriptor") + 1]) == descriptor


@pytest.mark.parametrize("fault", ["", "v1", "foreign", "missing-provider", "changed-selection"])
def test_prepared_wheel_packet_requires_both_owner_capabilities_and_exact_selection(tmp_path, monkeypatch, fault):
    payload, owner, snapshot = (tmp_path / name for name in ("payload", "owner", "snapshot"))
    for path in (payload, owner, snapshot):
        path.mkdir()
    identity = {
        "schema": "copilot-extensions.runtime-source", "version": 1, "plugin": "agent-worktrees",
        "package_version": "1.0", "source_commit": "a" * 40, "source_tree": "b" * 40,
        "assembly_hash": "c" * 64, "runtime_version": "1.0+source." + "a" * 40 + "." + "c" * 16,
    }
    fields = {"runtime_source": identity, "runtime_version": identity["runtime_version"]}
    record = {"payload": str(payload), "identity": fields, "install_source": str(snapshot)}
    packet = {
        "schema": source_owner.SOURCES_SCHEMA, "version": 1 if fault == "v1" else 2,
        "sources": {"agent-example" if fault == "foreign" else "agent-worktrees": record},
    }
    if fault in ("v1", "foreign"):
        with pytest.raises(ValueError):
            source_owner.parse_sources(json.dumps(packet))
        return
    source = source_owner.parse_sources(json.dumps(packet))["agent-worktrees"]
    provider = {
        "schema": target_owner.WHEEL_PROVIDER_SCHEMA, "version": 1,
        "selection_env": target_owner.WHEEL_SELECTION_ENV,
    }
    for path in (payload, owner):
        (path / "scripts").mkdir()
        (path / "scripts" / "runtime_install.py").write_text("# installer\n", encoding="utf-8")
        (path / "runtime-wheel-source.json").write_text(json.dumps(provider), encoding="utf-8")
    (owner / "scripts" / "install_source.py").write_text("# validator\n", encoding="utf-8")
    if fault == "missing-provider":
        (payload / "runtime-wheel-source.json").unlink()
    selection = {
        "identity": fields, "source_descriptor": {"kind": "github", "canonical": "github:example/repo", "ref": ""},
        "runtime_version": source.version + ".profile." + "d" * 16,
        "payload_hash": "e" * 64, "extras": [], "local_packages": ["agent-worktrees"],
    }
    calls = []

    def run(argv, **_kwargs):
        calls.append(argv)
        value = selection | ({"payload_hash": "f" * 64} if fault == "changed-selection" and len(calls) > 1 else {})
        return SimpleNamespace(returncode=0, stdout=json.dumps(value), stderr="")

    monkeypatch.setattr(target_owner.subprocess, "run", run)
    if fault == "missing-provider":
        with pytest.raises(ValueError, match="support"):
            target_owner.plan_target(source, "agent-worktrees", owner, tmp_path / "runtime", {})
        assert not calls
        return
    target = target_owner.plan_target(source, "agent-worktrees", owner, tmp_path / "runtime", {})
    assert target.version == selection["runtime_version"] and target.payload_hash == selection["payload_hash"]
    assert Path(calls[0][3]) == owner / "scripts" / "install_source.py"
    if fault == "changed-selection":
        with pytest.raises(ValueError, match="target changed"):
            target_owner.check_target(target, source, "agent-worktrees", owner, tmp_path / "runtime", {})
    else:
        target_owner.check_target(target, source, "agent-worktrees", owner, tmp_path / "runtime", {})


@pytest.mark.parametrize("outcome", ["success", "changed", "missing-snapshot"])
def test_native_worktrees_owner_rechecks_planned_wheel_selection(
    native_driver, tmp_path, monkeypatch, outcome,
):
    driver, modules, Refused = native_driver
    identity = SimpleNamespace(package_version="1.0")
    plugin, snapshot = tmp_path / "plugin", tmp_path / "snapshot"
    selected = SimpleNamespace(payload=plugin, identity=identity, source_descriptor=None)
    wheels = object()
    selection = {"runtime_version": "fixture.profile." + "a" * 16, "payload_hash": "b" * 64}

    @contextmanager
    def inputs(path, component):
        assert path == snapshot and component == "agent-worktrees"
        yield selected, wheels

    modules["versioned_runtime_source"].read_source_identity = lambda *_a, **_kw: identity
    modules["install_source"].project_inputs = inputs
    modules["install_source"].native_selection = lambda *_args: (
        selection | {"payload_hash": "c" * 64} if outcome == "changed" else selection
    )
    calls = []

    def install(_root, _plugin, **options):
        calls.append(options)
        return {"runtime_version": selection["runtime_version"]}

    monkeypatch.setattr(driver, "install", install)
    monkeypatch.setenv(
        target_owner.WHEEL_SELECTION_ENV, json.dumps(selection, sort_keys=True, separators=(",", ":")),
    )
    result = tmp_path / "result.json"
    arguments = [
        "--root", str(tmp_path / "runtime"), "--plugin", str(plugin), "--payload", str(plugin),
        "--uv", str(tmp_path / "uv"), "--base-python", sys.executable, "--result", str(result),
    ]
    if outcome != "missing-snapshot":
        arguments += ["--install-source", str(snapshot)]
    if outcome == "success":
        assert driver.main(arguments) == 0
        assert calls[0]["expected_wheel_selection"] == selection
        assert json.loads(result.read_text())["runtime_version"] == selection["runtime_version"]
    else:
        with pytest.raises(SystemExit if outcome == "missing-snapshot" else Refused):
            driver.main(arguments)
        assert not calls and not result.exists()


@pytest.mark.parametrize("outcome", ["success", "exit", "malformed"])
def test_wheel_marker_probe_is_isolated_and_fail_closed(native_driver, tmp_path, monkeypatch, outcome):
    driver, _modules, Refused = native_driver
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(
            returncode=1 if outcome == "exit" else 0,
            stdout=json.dumps({"python_version": "3.11"}) if outcome == "success" else "invalid",
        )

    monkeypatch.setattr(driver.subprocess, "run", run)
    monkeypatch.setenv("PYTHONPATH", "untrusted")
    monkeypatch.setenv("PYTHONHOME", "untrusted")
    python = tmp_path / "python"
    if outcome == "success":
        assert driver._wheel_marker_environment(python) == {"python_version": "3.11"}
    else:
        with pytest.raises(Refused, match="interpreter probe failed"):
            driver._wheel_marker_environment(python)
    assert calls[0][0][:5] == [str(python), "-I", "-S", "-B", "-c"]
    assert calls[0][1]["timeout"] == 30
    assert "PYTHONPATH" not in calls[0][1]["env"]
    assert "PYTHONHOME" not in calls[0][1]["env"]
