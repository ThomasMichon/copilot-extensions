"""Immutable Manager inputs retain source and constructor custody."""
from contextlib import contextmanager
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
import zipfile

import pytest

from worktree_manager import self_install

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import install_source as inputs  # noqa: E402


@pytest.fixture
def snapshot(tmp_path):
    root = tmp_path / "installation"
    payload = root / "source" / "worktree-manager"
    package = payload / "src" / "worktree_manager"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text('__version__ = "1.0.0"\n', encoding="utf-8")
    (payload / "pyproject.toml").write_text(
        '[project]\nname="copilot-extensions-worktree-manager"\nversion="1.0.0"\n',
        encoding="utf-8",
    )
    source = inputs.sources
    digest = source.assembly_hash(payload)
    identity = source.SourceIdentity(
        "worktree-manager", "1.0.0", "a" * 40, "b" * 40, digest,
        source.runtime_version("1.0.0", "a" * 40, digest),
    )
    (payload / source.SOURCE_FILE).write_text(json.dumps(identity.document()), encoding="utf-8")
    record = {
        "schema": inputs.SCHEMA, "version": 1, "kind": "source",
        "source_commit": "a" * 40, "source_tree": "b" * 40,
        "projects": {"worktree-manager": {
            "payload": "source/worktree-manager", "source": identity.document(),
        }},
    }
    (root / inputs.MANIFEST).write_text(json.dumps(record), encoding="utf-8")
    return root


@pytest.mark.parametrize(
    "mutation", ["association", "source", "escape", "schema", "local-version-composition", "main-no-head"],
)
def test_local_source_refuses_changed_or_escaping_inputs(snapshot, mutation):
    selected = inputs.select(snapshot)
    assert selected.identity.source_commit == "a" * 40
    record = selected.record
    if mutation == "association":
        record["source_commit"] = "d" * 40
    elif mutation == "source":
        (selected.payload / "src/worktree_manager/__init__.py").write_text(
            '__version__ = "1.0.0"\nCHANGED = True\n', encoding="utf-8",
        )
    elif mutation == "escape":
        record["projects"]["worktree-manager"]["payload"] = "../another"
    elif mutation == "local-version-composition":
        record["kind"] = "locally-promoted"
    elif mutation == "main-no-head":
        record["kind"] = "main"
    else:
        record["version"] = True
    (snapshot / inputs.MANIFEST).write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError):
        inputs.select(snapshot)


@pytest.mark.parametrize("application", [False, True])
def test_local_source_keeps_wheels_held_until_native_installer_returns(snapshot, monkeypatch, application):
    installer = self_install._runtime_installer()
    selected = inputs.select(snapshot)
    app = snapshot / "applications" / "worktree-manager"
    if application:
        app.mkdir(parents=True)
        selected.project["application"] = "applications/worktree-manager"
    held = []
    wheels = SimpleNamespace(distributions=(("copilot-extensions-worktree-manager", "1", "", ""),))

    @contextmanager
    def retain(path):
        assert path == snapshot
        held.append(True)
        try:
            yield selected, wheels
        finally:
            held.pop()

    def install(args, **options):
        assert held == [True]
        expected = {
            "wheel_inputs": wheels, "local_packages": ("copilot-extensions-worktree-manager",),
        }
        if application:
            expected["application"] = app
        assert options == expected
        return selected.payload

    monkeypatch.setattr(inputs, "manager_inputs", retain)
    monkeypatch.setattr(installer, "_install_payload", install)
    args = SimpleNamespace(install_source=snapshot, payload=selected.payload)
    assert installer._install(args) == selected.payload
    assert not held


def test_source_input_discovery_has_no_installation_or_old_runtime_dependency(monkeypatch, capsys):
    installer = self_install._runtime_installer()
    monkeypatch.setattr(installer, "_execution_driver", lambda *a: pytest.fail("discovery needs no driver"))
    monkeypatch.setattr(installer, "_install", lambda *a: pytest.fail("discovery must not install"))
    assert installer.main(["--describe-source-inputs"]) == 0
    assert json.loads(capsys.readouterr().out)["capabilities"] == ["install-source"]
    with pytest.raises(SystemExit) as refusal:
        installer.main(["--repo", "ambiguous"])
    assert refusal.value.code == 2


def test_prepared_source_activation_uses_existing_transactional_daemon_boundary(snapshot, monkeypatch):
    installer = self_install._runtime_installer()
    selected = inputs.select(snapshot)
    root = snapshot / "installed"
    slot = root / "versions" / selected.identity.runtime_version
    wheels = object()
    app = snapshot / "applications" / "worktree-manager"
    activated = []
    monkeypatch.setattr(
        installer, "_activate_after_bootstrap_update",
        lambda observed_root, observed_slot: activated.append((observed_root, observed_slot)),
    )

    def install(payload, **options):
        assert payload == selected.payload
        assert options["_wheel_inputs"] is wheels
        assert options["_local_packages"] == ("copilot-extensions-worktree-manager",)
        assert options["_application"] == app
        assert options["_transactional_activation"] is True
        options["_before_publish"]()
        options["_after_activate"](slot)
        return SimpleNamespace(action="installed", slot=str(slot))

    monkeypatch.setattr(self_install, "self_install", install)
    args = SimpleNamespace(
        payload=selected.payload, root=root, install_source=snapshot,
        skip_instance_reconcile=True, repair_update=False, recover_adopted=None,
        source_digest=None,
    )
    assert installer._install_payload(
        args, wheel_inputs=wheels, local_packages=("copilot-extensions-worktree-manager",),
        application=app,
    ) == slot
    assert activated == [(root, slot)]


@pytest.mark.parametrize("metadata_name", [
    "copilot-extensions-worktree-manager", "Copilot_Extensions_Worktree_Manager",
])
def test_local_source_retains_real_inspected_wheels(snapshot, metadata_name):
    selected = inputs.select(snapshot)
    filename = "copilot_extensions_worktree_manager-1.0.0-py3-none-any.whl"
    wheel_dir = snapshot / "wheels"
    wheel_dir.mkdir()
    prefix = "copilot_extensions_worktree_manager-1.0.0.dist-info/"
    members = {
        "worktree_manager/__init__.py": b'__version__ = "1.0.0"\n',
        prefix + "METADATA": (
            "Metadata-Version: 2.1\nName: " + metadata_name
            + "\nVersion: 1.0.0\nRequires-Python: >=3.11\n"
        ).encode(),
        prefix + "WHEEL": b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    record = "".join(
        f"{name},sha256={base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip('=')},{len(data)}\n"
        for name, data in members.items()
    ) + prefix + "RECORD,,\n"
    members[prefix + "RECORD"] = record.encode()
    path = wheel_dir / filename
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    profile = {
        "component": "worktree-manager",
        "release": {
            "identity": "source:" + selected.identity.source_commit,
            "sha256": "sha256:" + selected.identity.assembly_hash,
        },
        "wheels": [{
            "filename": filename, "name": "copilot-extensions-worktree-manager",
            "version": "1.0.0", "sha256": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
            "requires_python": ">=3.11", "requires_dist": [], "provides_extras": [],
        }],
    }
    (snapshot / "profile.json").write_text(json.dumps(profile), encoding="utf-8")
    selected.record["projects"]["worktree-manager"].update(profile="profile.json", wheel_inputs="wheels")
    (snapshot / inputs.MANIFEST).write_text(json.dumps(selected.record), encoding="utf-8")
    with inputs.manager_inputs(snapshot) as (retained, wheels):
        assert retained.identity == selected.identity
        wheels.verify()
        assert wheels.distributions[0][:2] == ("copilot-extensions-worktree-manager", "1.0.0")


@pytest.mark.parametrize("component,name,key", [
    ("agent-worktrees", "agent-worktrees", "agent-worktrees"),
    ("fleet-manager", "fleet-manager", "standalone:fleet-manager"),
])
def test_shared_source_inputs_select_only_the_associated_project(snapshot, component, name, key):
    manager = inputs.select(snapshot)
    payload = snapshot / "source" / component
    payload.mkdir()
    (payload / "pyproject.toml").write_text(
        f'[project]\nname="{name}"\nversion="1.0.0"\n', encoding="utf-8",
    )
    if component == "agent-worktrees":
        (payload / "plugin.json").write_text(json.dumps({
            "name": component, "version": "1.0.0",
        }), encoding="utf-8")
    digest = inputs.sources.assembly_hash(payload)
    identity = inputs.sources.SourceIdentity(
        component, "1.0.0", manager.identity.source_commit, manager.identity.source_tree,
        digest, inputs.sources.runtime_version("1.0.0", manager.identity.source_commit, digest),
    )
    (payload / inputs.sources.SOURCE_FILE).write_text(json.dumps(identity.document()), encoding="utf-8")
    manager.record["projects"][key] = {
        "payload": "source/" + component, "source": identity.document(),
    }
    (snapshot / inputs.MANIFEST).write_text(json.dumps(manager.record), encoding="utf-8")
    selected = inputs.select_project(snapshot, component)
    assert selected.payload == payload and selected.identity == identity
    assert inputs.select(snapshot).identity == manager.identity
    with pytest.raises(ValueError, match="first-party inputs only"):
        with inputs.project_inputs(snapshot, component):
            pytest.fail("an unprepared project must never supply construction inputs")
    selected.record["projects"][key]["source"] = manager.identity.document()
    (snapshot / inputs.MANIFEST).write_text(json.dumps(selected.record), encoding="utf-8")
    with pytest.raises(ValueError, match="immutable source association"):
        inputs.select_project(snapshot, component)


def test_shared_source_reader_refuses_ambiguous_standalone_alias(snapshot):
    selected = inputs.select(snapshot)
    selected.record["projects"]["standalone:worktree-manager"] = selected.project
    (snapshot / inputs.MANIFEST).write_text(json.dumps(selected.record), encoding="utf-8")
    with pytest.raises(ValueError, match="requires one"):
        inputs.select(snapshot)


@pytest.mark.parametrize("shell", ["powershell", "pwsh"])
@pytest.mark.skipif(os.name != "nt", reason="native Windows bootstrap discovery")
def test_native_bootstrap_declares_source_support_before_config_or_prerequisites(
    tmp_path, shell,
):
    executable = shutil.which(shell)
    if executable is None:
        pytest.skip(f"{shell} is unavailable")
    root = tmp_path / "unread-installation"
    root.mkdir()
    (root / "config.toml").write_text("[source]\nunsupported = true\n", encoding="utf-8")
    environment = dict(os.environ)
    environment["WORKTREE_MANAGER_ROOT"] = str(root)
    environment["HOME"] = environment["USERPROFILE"] = str(tmp_path)
    environment["HOMEDRIVE"], environment["HOMEPATH"] = os.path.splitdrive(str(tmp_path))
    before = sorted(root.rglob("*"))
    result = subprocess.run(
        [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-File",
         str(SCRIPTS.parent / "bootstrap.ps1"), "--describe-source-inputs"],
        capture_output=True, text=True, env=environment, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["capabilities"] == ["install-source"]
    assert sorted(root.rglob("*")) == before
