"""Actor-free checks for prepared Worktrees wheel-source selection."""
from __future__ import annotations

import dataclasses
import importlib.util
import io
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
    monkeypatch.setattr(target_owner, "preflight_wheel_target", lambda *_args: sys.executable)
    monkeypatch.setattr(source_owner.shutil, "which", lambda _name: sys.executable)

    step = plan_owner.plan_engine(owner, platform, force=True, source=source)
    assert not step.blocked
    assert step.argv[step.argv.index(input_option) + 1] == str(snapshot)
    assert json.loads(step.argv[step.argv.index(descriptor_option) + 1]) == descriptor
    command = source.lifecycle_argv(owner, "agent-worktrees", runtime, tmp_path, step.argv, target=target)
    assert json.loads(command[command.index("--source-descriptor") + 1]) == descriptor


@pytest.mark.parametrize("denied", [None, "helpers", "selector", "invalid"])
def test_parent_publication_recheck_is_ordered_and_fail_closed(denied):
    path = ROOT / "plugins" / "agent-worktrees" / "scripts" / "runtime_install_recheck.py"
    spec = importlib.util.spec_from_file_location("_wheel_publication_recheck_unit", path)
    assert spec and spec.loader
    recheck = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(recheck)
    calls = []

    class Input:
        def __init__(self):
            self.writes = []

        def write(self, value):
            self.writes.append(value)

        def flush(self):
            pass

        def close(self):
            pass

    frames = (
        b"invalid\n" if denied == "invalid" else b"helpers\n"
        if denied == "helpers" else b"helpers\nselector\n"
    )
    process = SimpleNamespace(
        stdin=Input(), stdout=io.BytesIO(frames), args=["fixture"], wait=lambda: 0,
    )

    def verify(phase):
        calls.append(phase)
        if phase == denied:
            raise ValueError("revoked")

    if denied is None:
        recheck.supervise(process, verify)
        assert calls == ["helpers", "selector"] and process.stdin.writes == [b"ok\n", b"ok\n"]
    else:
        with pytest.raises(recheck.HandoverError):
            recheck.supervise(process, verify)
        assert calls == {
            "helpers": ["helpers"], "selector": ["helpers", "selector"], "invalid": [],
        }[denied]
        assert process.stdin.writes[-1] == b"deny\n"


@pytest.mark.parametrize("outcome", [
    "flat", "owned", "revoked", "missing-owner", "missing-selection", "foreign-lock",
])
def test_source_wheel_profile_requires_its_live_owner(outcome):
    path = ROOT / "plugins" / "agent-worktrees" / "scripts" / "runtime_install_recheck.py"
    spec = importlib.util.spec_from_file_location("_source_wheel_profile_unit", path)
    assert spec and spec.loader
    recheck = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(recheck)
    source = SimpleNamespace(
        source_commit="a" * 40, assembly_hash="b" * 64, document=lambda: {"source": "selected"},
    )
    profile = {
        "component": "agent-worktrees", "mode": "source",
        "release": {"identity": "source:" + source.source_commit, "sha256": "sha256:" + source.assembly_hash},
    }
    binding = {
        "profile": profile,
        "source_lock": {"source": {} if outcome == "foreign-lock" else source.document()},
        "build_profile": {}, "build_toolchain": {},
    }
    if outcome == "flat":
        binding = profile
    events = []

    def verify():
        events.append("verified")
        if outcome == "revoked":
            raise ValueError("revoked")

    callback = None if outcome == "missing-owner" else verify
    selection = None if outcome == "missing-selection" else {}
    if outcome in ("flat", "owned"):
        assert recheck.wheel_profile(binding, source, callback, selection, ValueError) == profile
        assert events == ([] if outcome == "flat" else ["verified"])
    else:
        with pytest.raises(ValueError):
            recheck.wheel_profile(binding, source, callback, selection, ValueError)
        assert events == (["verified"] if outcome == "revoked" else [])


@pytest.mark.parametrize("outcome", ["owned", "revoked", "foreign-native", "foreign-lock", "artifact"])
def test_source_adapter_rechecks_the_retained_owner(outcome, monkeypatch):
    scripts = ROOT / "plugins" / "agent-worktrees" / "scripts"
    identity = SimpleNamespace(plugin="agent-worktrees", document=lambda: {"source": "selected"})
    policy = {
        "status": "ready", "owner": {"pluginId": "agent-worktrees", "runtimeRoot": str(ROOT)},
        "selection": {"allowSourceFallback": True},
    }
    plan = SimpleNamespace(
        policy=lambda: policy, source_trigger="release-index-unavailable",
        release_selection=object() if outcome == "artifact" else None,
    )
    profile = {"target": {}}
    binding = {
        "profile": profile,
        "source_lock": {
            "source": identity.document(), "policy": {} if outcome == "foreign-lock" else policy,
            "trigger": plan.source_trigger, "target": {}, "component": "agent-worktrees", "extras": [],
        },
    }
    wheels = SimpleNamespace(receipt=lambda: {
        "binding": binding, "distributions": {"agent-worktrees": {}},
    })
    held, events = [], []

    def verify():
        assert held == [True]
        events.append("verify")
        if outcome == "revoked" and events.count("verify") == 3:
            raise ValueError("revoked")

    selected = SimpleNamespace(
        payload=ROOT, identity=identity, profile=SimpleNamespace(record=lambda: profile), wheels=wheels,
        local_packages=("agent-worktrees",), runtime_version="selected", verify=verify,
    )

    @contextmanager
    def retain(*_args, **_kwargs):
        held.append(True)
        try:
            yield selected
        finally:
            held.pop()

    engine = SimpleNamespace(
        _package_roots=lambda path: (path,), _package_name=lambda _path: "agent-worktrees",
        _source_files=lambda _paths: (), _construction_hash=lambda *_args: "source",
        _wheel_construction_hash=lambda *_args: "e" * 64,
    )
    native = ModuleType("runtime_install")
    native.__file__ = str(scripts / ("foreign.py" if outcome == "foreign-native" else "runtime_install.py"))
    native.engine = engine
    native.runtime_source = SimpleNamespace(manifest_fields=lambda _identity: {"source": "selected"})

    def install(_root, _payload, **options):
        assert options["wheel_inputs"] is wheels
        assert options["expected_wheel_selection"]["payload_hash"] == "e" * 64
        for phase in ("helpers", "selector"):
            options["_before_publish"]()
            events.append(phase)
        return {"runtime_version": "selected"}

    native.install = install
    monkeypatch.setitem(sys.modules, "runtime_install", native)
    monkeypatch.setitem(sys.modules, "runtime_source_build", SimpleNamespace(retain_source_runtime_inputs=retain))
    path = scripts / "runtime_install_source.py"
    spec = importlib.util.spec_from_file_location("_worktrees_source_adapter_unit", path)
    assert spec and spec.loader
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    arguments = {
        "installation": {"payload_root": ROOT}, "component": "agent-worktrees",
        "python": Path(sys.executable), "uv": sys.executable, "deadline": 1e20,
    }
    if outcome != "owned":
        with pytest.raises(ValueError):
            adapter.install_source_runtime(plan, **arguments)
        assert "selector" not in events
    else:
        assert adapter.install_source_runtime(plan, **arguments)["runtime_version"] == "selected"
        assert events == ["verify", "verify", "helpers", "verify", "selector", "verify"]
    assert not held


@pytest.mark.parametrize("fault", ["", "target", "helper", "inside-runtime", "probe"])
def test_wheel_interpreter_preflight_is_read_only_and_fail_closed(tmp_path, monkeypatch, fault):
    owner, payload, runtime = (tmp_path / name for name in ("owner", "payload", "runtime"))
    for path in (owner, payload):
        (path / "scripts").mkdir(parents=True)
        (path / "scripts" / "installer_marker_environment.py").write_text(
            "MARKER_ENVIRONMENT_QUERY = 'probe'\n", encoding="utf-8",
        )
    if fault == "helper":
        (payload / "scripts" / "installer_marker_environment.py").write_text("changed\n", encoding="utf-8")
    python = tmp_path / "python"
    python.touch()
    monkeypatch.setattr(sys, "_base_executable", str(python))
    expected = {"python_version": "3.11"}
    target = target_owner.SourceWheelTarget("{}", json.dumps(expected))
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        observed = {"python_version": "3.12" if fault == "target" else "3.11"}
        return SimpleNamespace(returncode=1 if fault == "probe" else 0, stdout=json.dumps(observed))

    monkeypatch.setattr(target_owner.subprocess, "run", run)
    if fault == "inside-runtime":
        runtime = tmp_path
    arguments = (SimpleNamespace(payload=payload), target, owner, runtime, {}, "linux")
    if fault:
        with pytest.raises(ValueError):
            target_owner.preflight_wheel_target(*arguments)
    else:
        assert target_owner.preflight_wheel_target(*arguments) == str(python.resolve())
        assert calls[0][0][:5] == [str(python.resolve()), "-I", "-S", "-B", "-c"]
    if fault != "inside-runtime":
        assert not runtime.exists()


@pytest.mark.parametrize("choice", ["signed", "fallback", "invalid", "skew"])
def test_wheel_interpreter_preflight_uses_the_installed_signed_selector(tmp_path, monkeypatch, choice):
    owner, payload = tmp_path / "owner", tmp_path / "payload"
    for path in (owner, payload):
        (path / "scripts").mkdir(parents=True)
        (path / "scripts" / "installer_marker_environment.py").write_text(
            "MARKER_ENVIRONMENT_QUERY = 'probe'\n", encoding="utf-8",
        )
        (path / "scripts" / "runtime-python.ps1").write_text("# signed selector\n", encoding="utf-8")
    signed = tmp_path / "signed-python"
    signed.touch()
    fallback = tmp_path / "fallback-python"
    fallback.touch()
    monkeypatch.setattr(sys, "_base_executable", str(fallback))
    if choice == "skew":
        (payload / "scripts" / "runtime-python.ps1").write_text("# changed\n", encoding="utf-8")
    monkeypatch.setattr(target_owner.shutil, "which", lambda *_args, **_kwargs: "shell")
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        selected = str(signed) if choice == "signed" else None if choice == "fallback" else 7
        value = {"python": selected} if len(calls) == 1 else {"python_version": "3.12"}
        return SimpleNamespace(returncode=0, stdout=json.dumps(value))

    monkeypatch.setattr(target_owner.subprocess, "run", run)
    target = target_owner.SourceWheelTarget("{}", json.dumps({"python_version": "3.12"}))
    arguments = (SimpleNamespace(payload=payload), target, owner, tmp_path / "runtime", {}, "windows")
    if choice in ("invalid", "skew"):
        with pytest.raises(ValueError):
            target_owner.preflight_wheel_target(*arguments)
        assert len(calls) == (0 if choice == "skew" else 1)
    else:
        chosen = signed if choice == "signed" else fallback
        assert target_owner.preflight_wheel_target(*arguments) == str(chosen.resolve())
        assert Path(calls[0][5]) == owner / "scripts" / "runtime-python.ps1"
        assert calls[1][0] == str(chosen.resolve())


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
        "schema": target_owner.WHEEL_PROVIDER_SCHEMA, "version": 2,
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
        return SimpleNamespace(
            returncode=0, stdout=json.dumps({"selection": value, "environment": {"python_version": "3.11"}}),
            stderr="",
        )

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
