"""Canonical roots, not environment hints, identify an installed broker."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
import venv

import pytest

from agent_procutil import windows_broker_installation as installation


def test_identity_is_stable_across_generations_but_not_installations(tmp_path):
    root = tmp_path / "one"
    root.mkdir()
    other = tmp_path / "two"
    other.mkdir()
    assert installation.installation_identity(root) == installation.installation_identity(root)
    assert installation.installation_identity(root) != installation.installation_identity(other)
    assert installation.installation_identity(root).startswith("runtime/")


def test_relative_identity_is_not_resolved_against_caller_directory():
    with pytest.raises(ValueError, match="absolute unlinked"):
        installation.installation_identity(Path("relative"))


@pytest.fixture
def installed(tmp_path, monkeypatch):
    if os.name != "nt":
        pytest.skip("Windows immutable runtime layout")
    from agent_procutil import windows_task_broker as broker

    monkeypatch.setattr(broker, "native", lambda: SimpleNamespace(current_sid=lambda: "S-1-5-21-123"))
    root = tmp_path / "installation"
    slot = root / "versions" / "1.0.0"
    entry = slot / "Lib" / "site-packages" / "agent_procutil" / "windows_broker_entry.py"
    python = slot / "Scripts" / "pythonw.exe"
    bootstrap = slot / ".runtime-bootstrap"
    entry.parent.mkdir(parents=True)
    python.parent.mkdir()
    bootstrap.mkdir()
    entry.write_text("# installed action\n")
    python.write_bytes(b"fixture")
    python.with_name("python.exe").write_bytes(b"fixture")
    base = tmp_path / "base-python"
    base.mkdir()
    (base / "pythonw.exe").write_bytes(b"fixture")
    (base / "python.exe").write_bytes(b"fixture")
    (slot / "pyvenv.cfg").write_text(f"home = {base}\n")
    library = Path(__file__).resolve().parents[2] / "versioned-runtime"
    for name in ("versioned_runtime.py", "versioned_runtime_leases.py"):
        shutil.copyfile(library / name, bootstrap / name)
    (slot / ".install-complete.json").write_text(json.dumps({
        "version": slot.name, "completed_at": "2026-10-01T00:00:00Z", "pid": 1,
        "payload_hash": "a" * 64,
    }))
    return root, slot


def test_factory_binds_only_fixed_installed_paths_and_read_only_load(installed, monkeypatch):
    from agent_procutil import windows_task_broker as broker

    root, slot = installed
    monkeypatch.setenv(broker.OWNER_ENV, "foreign/owner")
    monkeypatch.setenv(broker.CONFIG_ENV, str(root.parent / "foreign.json"))
    config = installation.registration(root, slot)
    assert config.installation == installation.installation_identity(root)
    assert Path(config.pythonw) == root.parent / "base-python" / "pythonw.exe"
    assert Path(config.entrypoint) == slot / "Lib/site-packages/agent_procutil/windows_broker_entry.py"
    assert not config.config_path.exists()
    broker._publish(config)
    assert installation.load(root) == config


@pytest.mark.parametrize(
    "mutation",
    [
        "missing-marker", "wrong-marker", "missing-pythonw", "missing-helper",
        "foreign-slot", "dev-slot", "dev-generation", "missing-base", "missing-venv",
    ],
)
def test_incomplete_mutable_or_foreign_generation_refuses(installed, mutation):
    root, slot = installed
    marker = slot / ".install-complete.json"
    if mutation == "missing-marker":
        marker.unlink()
    elif mutation == "wrong-marker":
        marker.write_text('{"version":"other"}')
    elif mutation == "missing-pythonw":
        (slot / "Scripts" / "pythonw.exe").unlink()
    elif mutation == "missing-helper":
        (slot / ".runtime-bootstrap" / "versioned_runtime_leases.py").unlink()
    elif mutation == "missing-base":
        (root.parent / "base-python" / "pythonw.exe").unlink()
    elif mutation == "missing-venv":
        (slot / "pyvenv.cfg").unlink()
    elif mutation == "foreign-slot":
        root = root.parent
    else:
        version = "dev" if mutation == "dev-slot" else "dev-" + "a" * 32
        slot = slot.rename(slot.with_name(version))
        data = json.loads((slot / ".install-complete.json").read_text())
        data["version"] = version
        (slot / ".install-complete.json").write_text(json.dumps(data))
    with pytest.raises((OSError, ValueError, RuntimeError)):
        installation.registration(root, slot)
    assert not (root / "windows-launch-broker.json").exists()


def test_published_foreign_identity_is_not_treated_as_missing_adoption(installed):
    from agent_procutil import DaemonSpawnError

    root, slot = installed
    config = installation.registration(root, slot)
    frame = config.frame()
    frame["installation"] = "foreign/owner"
    config.config_path.write_text(json.dumps(frame))
    before = config.config_path.read_bytes()
    with pytest.raises(DaemonSpawnError, match="adoption required"):
        installation.load(root)
    assert config.config_path.read_bytes() == before


def test_current_discovery_uses_module_not_prefix_environment_or_action_slot(
    installed, monkeypatch,
):
    import sys
    from agent_procutil import windows_task_broker as broker

    root, slot = installed
    config = installation.registration(root, slot)
    broker._publish(config)
    caller = root / "versions" / "2.0.0" / "Lib" / "site-packages" / "agent_procutil"
    caller.mkdir(parents=True)
    module = caller / "windows_broker_installation.py"
    module.write_text("# newer installed consumer\n")
    monkeypatch.setattr(installation, "__file__", str(module))
    monkeypatch.setattr(sys, "prefix", str(root.parent / "unrelated-prefix"))
    monkeypatch.setenv(broker.OWNER_ENV, "foreign/owner")
    monkeypatch.setenv(broker.CONFIG_ENV, str(root.parent / "foreign.json"))
    monkeypatch.chdir(root.parent)
    before = config.config_path.read_bytes()
    monkeypatch.setattr(
        broker, "task_operation",
        lambda *args, **kwargs: pytest.fail("read-only discovery invoked the scheduler"),
    )
    assert installation.load_current() == config
    assert config.config_path.read_bytes() == before
    assert not config.pending_path.exists()


@pytest.mark.parametrize("module", [
    Path("relative") / "Lib" / "site-packages" / "agent_procutil"
    / "windows_broker_installation.py",
    Path(__file__).parent / "windows_broker_installation.py",
    Path(__file__).parent / "versions" / "1.0.0" / "src" / "agent_procutil"
    / "windows_broker_installation.py",
    Path(__file__).parent / "runtime" / "1.0.0" / "Lib" / "site-packages"
    / "agent_procutil" / "windows_broker_installation.py",
    Path(__file__).parent / "versions" / "1.0.0" / "Lib" / "site-packages"
    / "agent_procutil" / "foreign.py",
])
def test_current_discovery_refuses_noninstalled_layout_before_reading_adoption(
    module, monkeypatch,
):
    from agent_procutil import DaemonSpawnError

    monkeypatch.setattr(installation, "__file__", str(module))
    monkeypatch.setattr(
        installation, "load",
        lambda root: pytest.fail("noninstalled caller reached adoption metadata"),
    )
    with pytest.raises(DaemonSpawnError, match="installed broker context required"):
        installation.load_current()


def test_current_discovery_does_not_adopt_when_configuration_is_absent(installed, monkeypatch):
    from agent_procutil import DaemonSpawnError
    from agent_procutil import windows_task_broker as broker

    root, slot = installed
    module = slot / "Lib" / "site-packages" / "agent_procutil" / "windows_broker_installation.py"
    module.write_text("# installed consumer\n")
    monkeypatch.setattr(installation, "__file__", str(module))
    monkeypatch.setattr(
        broker, "task_operation",
        lambda *args, **kwargs: pytest.fail("missing adoption invoked the scheduler"),
    )
    with pytest.raises(DaemonSpawnError, match="adoption required"):
        installation.load_current()
    assert not (root / "windows-launch-broker.json").exists()
    assert not (root / "windows-launch-broker.pending.json").exists()


@pytest.mark.skipif(os.name != "nt", reason="real Windows direct base interpreter")
def test_real_venv_action_qualifies_without_redirector_or_ambient_imports(tmp_path, monkeypatch):
    import agent_procutil
    from agent_procutil import windows_task_broker as broker
    import zdd

    root = tmp_path / "installed"
    slot = root / "versions" / "1.0.0"
    venv.EnvBuilder(with_pip=False).create(slot)
    packages = slot / "Lib" / "site-packages"
    for package in (agent_procutil, zdd):
        shutil.copytree(
            Path(package.__file__).parent, packages / package.__name__,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    bootstrap = slot / ".runtime-bootstrap"
    bootstrap.mkdir()
    library = Path(__file__).resolve().parents[2] / "versioned-runtime"
    for name in ("versioned_runtime.py", "versioned_runtime_leases.py"):
        shutil.copyfile(library / name, bootstrap / name)
    (slot / ".install-complete.json").write_text(json.dumps({
        "version": slot.name, "completed_at": "2026-10-01T00:00:00Z", "pid": 1,
    }))
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "unavailable-imports"))
    config = installation.registration(root, slot)
    assert Path(config.pythonw) != slot / "Scripts" / "pythonw.exe"
    broker._qualify(config)
    assert not config.config_path.exists()
    broker._publish(config)
    before = config.config_path.read_bytes()
    code = (
        "import json,sys;"
        "sys.path.insert(0,sys.argv[1]);"
        "from agent_procutil.windows_broker_installation import load_current;"
        "c=load_current();"
        "print(json.dumps({'root':c.root,'prefix':sys.prefix,'entrypoint':c.entrypoint}))"
    )
    result = subprocess.run(
        [str(Path(sys.base_prefix) / "python.exe"), "-I", "-S", "-c", code, str(packages)],
        cwd=root.parent, capture_output=True, text=True, timeout=15, check=True,
        **agent_procutil.no_window_kwargs(),
    )
    discovered = json.loads(result.stdout)
    assert Path(discovered["prefix"]) != slot
    assert Path(discovered["root"]) == root
    assert discovered["entrypoint"] == config.entrypoint
    assert config.config_path.read_bytes() == before
