"""Cell-qualified metadata and inert installer boundaries; never register a Task."""

from __future__ import annotations

import json
import os
import shutil
import stat
import venv
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_procutil import windows_broker_cell
from test_cell_installer_slice import activate_fixture, cell as cell  # noqa: F401

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture
def tmp_path(tmp_path_factory):
    # Keep the real nested cell/retained-helper layout within native path budgets.
    return tmp_path_factory.mktemp("cell")


@pytest.fixture
def qualified(cell, monkeypatch):
    adapter, api, receipt, arguments, _, home = cell
    version = adapter.management_version()
    snapshot_id = "broker-qualified"
    snapshot = Path(receipt["snapshotsRoot"]) / snapshot_id
    scripts = snapshot / "scripts"
    scripts.mkdir(parents=True)
    for name in (
        "versioned_runtime.py", "versioned_runtime_leases.py", "versioned_runtime_source.py",
        "versioned_runtime_construction.py", "versioned_runtime_publication.py",
    ):
        shutil.copyfile(REPO / "libs" / "versioned-runtime" / name, scripts / name)
    shutil.copyfile(REPO / "libs" / "installer-engine" / "runtime_launcher.py", scripts / "runtime_launcher.py")
    for suffix in ("ps1", "sh"):
        (scripts / f"installer-engine.{suffix}").write_text("# inert fixture\n", encoding="utf-8")
    (snapshot / "pyproject.toml").write_text("[project]\nname='cell-fixture'\n", encoding="utf-8")
    for name in adapter.LIBRARIES:
        project = snapshot / "libs" / name / "pyproject.toml"
        project.parent.mkdir(parents=True)
        project.write_text(f"[project]\nname='{name}'\n", encoding="utf-8")
    package = snapshot / "src" / "agent_pull_requests"
    package.mkdir(parents=True)
    for source in (adapter.PAYLOAD / "src" / "agent_pull_requests").glob("_installation_context*.py"):
        shutil.copyfile(source, package / source.name)
    api.stamp_snapshot_provenance(
        context=receipt["installReceipt"], durable_home=home.parent / "durable",
        expected_marketplace_id=receipt["marketplaceId"], expected_plugin_id=adapter.PLUGIN,
        snapshot_id=snapshot_id, expected_namespace_generation=receipt["namespaceGeneration"],
        expected_install_generation=receipt["generation"], environment={},
    )
    activate_fixture(cell, monkeypatch)
    arguments = arguments.copy()
    arguments[arguments.index("--snapshot-id") + 1] = snapshot_id
    arguments[arguments.index("--runtime-version") + 1] = version
    events = []

    def build(slot, root, payload_version):
        assert root == snapshot and payload_version == version
        python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        python.parent.mkdir(parents=True)
        python.write_bytes(b"inert target, never executed")
        events.append("build")

    def adopt(args, root, slot=None):
        validated = windows_broker_cell.validate(root, slot, require_active=True)
        assert validated["context"]["pluginRoot"] == str(root)
        assert validated["completion"]["runtimeVersion"] == version
        events.append("adopt")

    def activate(command):
        assert command[-4:] == [
            "--expected-namespace-generation", str(receipt["namespaceGeneration"]),
            "--expected-install-generation", str(receipt["generation"]),
        ]
        events.append("activate")
        return {"status": "ready", "activated": True}

    monkeypatch.setattr(adapter, "build", build)
    monkeypatch.setattr(adapter, "broker_operation", adopt)
    monkeypatch.setattr(adapter, "activate", activate)
    return adapter, api, receipt, arguments, events


@pytest.mark.parametrize("action", ["install", "update", "provision"])
def test_cell_completion_and_retention_precede_adoption_and_selection(qualified, action):
    adapter, api, _, arguments, events = qualified
    result = adapter.execute(adapter.parse([action, *arguments]), api)
    assert result["status"] == "ready"
    assert events == ["build", "adopt", *(["activate"] if action != "provision" else [])]


def test_completed_cell_reuse_verifies_helpers_without_rewriting(qualified):
    adapter, api, receipt, arguments, events = qualified
    result = adapter.execute(adapter.parse(["provision", *arguments]), api)
    slot = Path(result["slotRoot"])
    before = {path.relative_to(slot): path.read_bytes() for path in slot.rglob("*") if path.is_file()}
    events.clear()
    adapter.execute(adapter.parse(["install", *arguments]), api)
    assert events == ["adopt", "activate"]
    assert before == {path.relative_to(slot): path.read_bytes() for path in slot.rglob("*") if path.is_file()}
    assert not (Path(receipt["pluginRoot"]) / "current-version").exists()


@pytest.mark.skipif(os.name != "nt", reason="Native Windows installed-cell factory")
def test_native_cell_factory_and_installed_discovery_use_retained_completion(
    qualified, monkeypatch,
):
    from agent_procutil import windows_broker_installation as installation
    from agent_procutil import windows_task_broker as broker
    from agent_procutil.owned_process import run_caller_owned

    adapter, api, receipt, arguments, _ = qualified

    def build(slot, snapshot, version):
        venv.EnvBuilder(with_pip=False).create(slot)
        for executable in (slot / "Scripts").glob("python*.exe"):
            info = executable.stat()
            assert info.st_nlink == 1 and not executable.is_symlink()
            executable.chmod(info.st_mode | stat.S_IWRITE)
        packages = slot / "Lib" / "site-packages"
        for library, package in (("agent-procutil", "agent_procutil"), ("zdd", "zdd")):
            shutil.copytree(
                REPO / "libs" / library / "src" / package, packages / package,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )

    monkeypatch.setattr(adapter, "build", build)
    completed = adapter.execute(adapter.parse(["provision", *arguments]), api)
    root, slot = Path(receipt["pluginRoot"]), Path(completed["slotRoot"])
    config = installation.registration(root, slot)
    assert config.installation.startswith("cell/")
    assert Path(config.entrypoint).is_relative_to(slot)
    broker._publish(config)
    expected = json.dumps(config.frame())
    code = (
        "import json,sys;sys.path.insert(0,sys.argv[1]);"
        "from agent_procutil.windows_broker_installation import load_current;"
        "assert load_current().frame()==json.loads(sys.argv[2]);print('cell-discovery-ready')"
    )
    process = run_caller_owned(
        [str(slot / "Scripts" / "python.exe"), "-I", "-S", "-c", code,
         str(slot / "Lib" / "site-packages"), expected],
        timeout=30, max_stdout_bytes=4096, max_stderr_bytes=4096,
    )
    assert process.returncode == 0, process.stderr
    assert process.stdout.strip() == "cell-discovery-ready"
    before = (slot / ".runtime-slot-completion.json").read_bytes()
    adapter.execute(adapter.parse(["provision", *arguments]), api)
    assert installation.registration(root, slot) == config
    assert (slot / ".runtime-slot-completion.json").read_bytes() == before


def test_completed_cell_missing_helpers_refuses_without_retrofit_or_adoption(qualified):
    adapter, api, _, arguments, events = qualified
    result = adapter.execute(adapter.parse(["provision", *arguments]), api)
    slot = Path(result["slotRoot"])
    helper = slot / ".runtime-bootstrap" / "installation-context" / "installation_context.py"
    helper.unlink()
    marker = (slot / ".runtime-slot-completion.json").read_bytes()
    events.clear()
    with pytest.raises((OSError, RuntimeError)):
        adapter.execute(adapter.parse(["install", *arguments]), api)
    assert events == [] and not helper.exists()
    assert (slot / ".runtime-slot-completion.json").read_bytes() == marker


def test_cell_adoption_refusal_precedes_selection(qualified, monkeypatch):
    adapter, api, receipt, arguments, events = qualified

    def refuse(*args, **kwargs):
        events.append("refused")
        raise RuntimeError("synthetic broker refusal")

    monkeypatch.setattr(adapter, "broker_operation", refuse)
    with pytest.raises(RuntimeError, match="synthetic broker refusal"):
        adapter.execute(adapter.parse(["install", *arguments]), api)
    assert events == ["build", "refused"]
    assert not (Path(receipt["pluginRoot"]) / "current-version").exists()


@pytest.mark.parametrize("field", ["namespace", "install"])
def test_cell_admission_retains_exact_receipt_generations(qualified, field):
    adapter, api, receipt, arguments, events = qualified
    args = adapter.parse(["install", *arguments])
    adapter.bind_admission(args, api)
    path = Path(receipt["namespaceReceipt" if field == "namespace" else "installReceipt"])
    document = json.loads(path.read_text(encoding="utf-8"))
    document["generation"] += 1
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="generation changed"):
        adapter.execute(args, api)
    assert events == []


@pytest.mark.parametrize("damage", ["ownership", "snapshot", "completion", "inactive", "helpers"])
def test_shared_cell_validator_refuses_unqualified_or_changed_bindings(qualified, damage):
    adapter, api, receipt, arguments, _ = qualified
    completed = adapter.execute(adapter.parse(["provision", *arguments]), api)
    slot = Path(completed["slotRoot"])
    root = Path(receipt["pluginRoot"])
    if damage == "ownership":
        (slot / ".runtime-slot-ownership.json").write_text("{}", encoding="utf-8")
    elif damage == "snapshot":
        snapshot = root / "snapshots" / completed["snapshotId"]
        (snapshot / "pyproject.toml").write_text("changed", encoding="utf-8")
    elif damage == "completion":
        path = slot / ".runtime-slot-completion.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["pluginId"] = "foreign-plugin"
        path.write_text(json.dumps(value), encoding="utf-8")
    elif damage == "inactive":
        path = Path(receipt["namespaceReceipt"])
        value = json.loads(path.read_text(encoding="utf-8"))
        value["state"] = "inactive"
        path.write_text(json.dumps(value), encoding="utf-8")
    else:
        (slot / ".runtime-bootstrap" / "installation-context" / "installation_context.py").unlink()
    with pytest.raises((OSError, ValueError, RuntimeError)):
        windows_broker_cell.validate(root, slot, require_active=True)
    assert not (root / "windows-launch-broker.json").exists()


@pytest.mark.parametrize("expected", [(1, 3), (2, 2), (1, None)])
def test_running_activation_refuses_a_different_admitted_generation(monkeypatch, tmp_path, expected, capsys):
    from agent_pull_requests import watch_install

    target = SimpleNamespace(installation=SimpleNamespace(namespace_generation=1, install_generation=2))
    monkeypatch.setattr(watch_install, "invocation", lambda *args: target)
    monkeypatch.setattr(
        watch_install, "_activate_until", lambda *args: pytest.fail("changed generation activated"),
    )
    arguments = [
        "--context", str(tmp_path / "install.json"), "--payload-root", str(tmp_path),
        "--snapshot-id", "snapshot", "--runtime-version", "1.0.0",
        "--expected-namespace-generation", str(expected[0]),
    ]
    if expected[1] is not None:
        arguments.extend(["--expected-install-generation", str(expected[1])])
    assert watch_install.main(arguments) == 1
    assert "activation refused" in capsys.readouterr().err.lower()
