"""Installed-owner selection does not guess another Vault or provision one."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any
import json
import os
import shutil
import sys
import venv

import pytest

from agent_worktrees import repository_vault as vault
from agent_worktrees import _peer_launch, peer_launch_adapter, reconcile
from agent_worktrees.repository_setup import SetupError


@pytest.mark.parametrize("failure", [None, "missing", "foreign", "migration", "no-runtime", "no-retention"])
def test_legacy_vault_uses_selected_completed_retained_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str | None,
) -> None:
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    payload, root = tmp_path / "payload", tmp_path / "runtime"
    python = root / "versions" / "1.0" / "Scripts" / "python.exe"
    monkeypatch.setattr(
        reconcile, "core_installed_payload_dir",
        lambda name: None if failure == "missing" else payload,
    )
    installation = SimpleNamespace(
        actual_mode="namespaced" if failure == "foreign" else "legacy",
        desired_mode="namespaced" if failure == "migration" else "legacy",
        status="ready", context=None, runtime_root=root,
    )
    monkeypatch.setattr(reconcile, "resolve_runtime_installation", lambda *_: installation)
    monkeypatch.setattr(
        _peer_launch, "_peer_python_inprocess", lambda _: None if failure == "no-runtime" else python,
    )

    def identity(*_: object) -> str:
        if failure == "no-retention":
            raise ValueError("No immutable build")
        return "original-build"

    monkeypatch.setattr(_peer_launch, "retained_runtime_identity", identity)
    monkeypatch.setattr(
        _peer_launch, "retained_runtime_prefix",
        lambda selected_root, selected_python, module, **kw: [
            str(selected_python), "-I", module, kw["runtime_identity"],
        ],
    )
    monkeypatch.setenv("KPDB", "foreign-database")
    monkeypatch.setenv("VAULT_GROUP", "foreign-group")
    monkeypatch.setenv("AGENT_VAULT_PINNED_CONTEXT", "foreign-context")
    monkeypatch.setenv("AGENT_VAULT_PORT", "1234")
    monkeypatch.setenv("_AGENT_WORKTREES_VAULT_RUNTIME_GUARD", "1")
    if failure:
        with pytest.raises(SetupError, match="owner is unavailable or refused"):
            vault.selected()
        return
    selected = vault.selected()
    assert selected.argv == (str(python), "-I", "agent_vault", "original-build")
    assert selected.environment["AGENT_VAULT_HOME"] == str(root)
    assert selected.environment["AGENT_VAULT_NO_SELFPROVISION"] == "1"
    assert not {
        "KPDB", "VAULT_GROUP", "AGENT_VAULT_PINNED_CONTEXT", "AGENT_VAULT_PORT",
        "_AGENT_WORKTREES_VAULT_RUNTIME_GUARD",
    } & selected.environment.keys()
    selected.verify()
    installation.runtime_root = tmp_path / "changed"
    with pytest.raises(SetupError, match="selection changed"):
        selected.verify()


@pytest.mark.parametrize("failure", [None, "empty", "foreign", "missing-peer", "no-runtime", "no-retention"])
def test_namespaced_vault_retains_same_cell_and_rejects_changed_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str | None,
) -> None:
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "" if failure == "empty" else "owner-receipt")
    own = {"pluginRoot": str(tmp_path / "agent-worktrees"), "installReceipt": "owner-receipt"}
    reference_root = tmp_path / "reference" / "agent-worktrees"
    peer = {"pluginRoot": str(tmp_path / "agent-vault"), "installReceipt": "peer-receipt"}
    python = tmp_path / "python"
    resolutions: list[str] = []

    def reference() -> tuple[Path, str] | None:
        return None if failure == "empty" else (reference_root, own["installReceipt"])

    def resolve(
        owner_id: str, root: Path, receipt: str, plugin: str, *, allow_native: bool,
    ) -> tuple[dict[str, str], dict[str, str], dict[str, str], Path | None]:
        assert (owner_id, root, receipt, plugin) == (
            "agent-worktrees", reference_root, own["installReceipt"], "agent-vault",
        )
        assert allow_native is False
        resolutions.append(plugin)
        if failure == "foreign":
            raise _peer_launch.ContextRefused("foreign owner")
        if failure == "missing-peer":
            raise ValueError("No peer")
        return own, peer, {}, None if failure == "no-runtime" else python

    def identity(root: Path, selected_python: Path, module: str) -> str:
        assert (root, selected_python, module) == (Path(peer["pluginRoot"]), python, "agent_vault")
        if failure == "no-retention":
            raise ValueError("No immutable build")
        return "retained-build"

    monkeypatch.setattr(peer_launch_adapter, "context_reference", reference)
    monkeypatch.setattr(
        peer_launch_adapter, "validate_context", lambda: pytest.fail("redundant owner validation"),
    )
    monkeypatch.setattr(_peer_launch, "_resolve_peer_observation", resolve)
    monkeypatch.setattr(_peer_launch, "retained_runtime_identity", identity)
    monkeypatch.setattr(
        reconcile, "core_installed_payload_dir", lambda *_: pytest.fail("legacy fallback"),
    )
    if failure:
        with pytest.raises(SetupError, match="owner is unavailable or refused"):
            vault.selected()
        return
    selected = vault.selected()
    assert resolutions == ["agent-vault"]
    assert selected.argv[6:10] == ("--pinned-runtime", str(python), "--runtime-identity", "retained-build")
    assert selected.argv[11:13] == (own["pluginRoot"], own["installReceipt"])
    selected.verify()
    assert resolutions == ["agent-vault", "agent-vault"]
    peer["installReceipt"] = "changed"
    with pytest.raises(SetupError, match="selection changed"):
        selected.verify()


@pytest.mark.parametrize("boundary", ["before", "after"])
def test_namespaced_runtime_change_is_refused_at_both_child_boundaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str,
) -> None:
    from agent_worktrees import repository_ssh_setup

    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "owner-receipt")
    own = {"pluginRoot": str(tmp_path / "agent-worktrees"), "installReceipt": "owner-receipt"}
    peer = {"pluginRoot": str(tmp_path / "agent-vault"), "installReceipt": "peer-receipt"}
    python = tmp_path / "python"
    generation = ["original"]
    launched: list[bool] = []
    monkeypatch.setattr(
        peer_launch_adapter, "context_reference", lambda: (Path(own["pluginRoot"]), own["installReceipt"]),
    )
    monkeypatch.setattr(_peer_launch, "_resolve_peer_observation", lambda *_a, **_kw: (own, peer, {}, python))
    monkeypatch.setattr(_peer_launch, "retained_runtime_identity", lambda *_: generation[0])
    invocation = vault.selected()

    def child(*_args: object, **_kwargs: object) -> SimpleNamespace:
        launched.append(True)
        generation[0] = "changed"
        return SimpleNamespace(returncode=0, stdout='{"version":1}')

    monkeypatch.setattr(repository_ssh_setup, "run_caller_owned", child)
    if boundary == "before":
        generation[0] = "changed"
    with pytest.raises(SetupError):
        repository_ssh_setup._vault_call(
            SimpleNamespace(configuration_root=str(tmp_path), argv=()),
            ["invocation-contract", "--json"], timeout=20, invocation=invocation,
        )
    assert launched == ([True] if boundary == "after" else [])


@pytest.mark.skipif(
    sys.platform != "win32" and not sys.platform.startswith("linux"),
    reason="Native Vault peer invocation supports Windows and Linux only",
)
@pytest.mark.parametrize("changed_owner", ["agent-worktrees", "agent-vault"])
def test_native_installed_vault_binding_rechecks_receipt_before_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changed_owner: str,
) -> None:
    from agent_worktrees import _installation_context as primitive, repository_ssh_setup, repository_setup

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cell = tmp_path / "marketplaces" / "example--9caa0da95f327099"
    cell.mkdir(parents=True)
    namespace = cell / "namespace.json"
    namespace.write_text(json.dumps({
        "schema": "copilot-extensions.marketplace-namespace", "version": 1,
        "marketplaceId": cell.name, "source": {
            "kind": "github", "canonical": "github:example-org/example-marketplace", "ref": "",
            "fingerprint": "sha256:9caa0da95f327099983348a3d2353fa93e57ef6428b69f354cc3bdc76ada0894",
        }, "locators": [], "generation": 1, "state": "active",
    }))
    environment, _ = getattr(primitive, "_current_environment")(
        environment=os.environ, os_profile=None, platform=None, wsl_distro=None,
    )
    for plugin in ("agent-worktrees", "agent-vault"):
        root = cell / "plugins" / plugin
        payload = cell / "payloads" / plugin
        root.mkdir(parents=True)
        payload.mkdir(parents=True)
        receipt = root / "install.json"
        receipt.write_text(json.dumps({
            "schema": "copilot-extensions.plugin-installation", "version": 1,
            "marketplaceId": cell.name, "pluginId": plugin, "pluginRoot": str(root),
            "namespaceReceipt": str(namespace),
            "payload": {"root": str(payload), "version": "1.0.0", "origin": "explicit"},
            "roots": {name: name for name in (
                "versions", "snapshots", "state", "run", "logs", "cache", "launchers",
            )},
            "generation": 1, "state": "active",
        }))
        (root / "installation-activation.json").write_text(json.dumps({
            "schema": "copilot-extensions.installation-activation", "version": 1,
            "marketplaceId": cell.name, "pluginId": plugin, "mode": "namespaced",
            "state": "active", "environment": environment, "context": str(receipt),
            "namespaceGeneration": 1, "installGeneration": 1, "generation": 1,
            "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-01T00:00:00Z",
            "legacy": {"disposition": "absent", "probe": {
                "declared": True, "result": "absent", "checkedAt": "2026-01-01T00:00:00Z",
            }},
        }))
    root = cell / "plugins" / "agent-vault"
    payload = cell / "payloads" / "agent-vault"
    source = Path(__file__).resolve().parents[2] / "agent-vault" / "scripts"
    scripts = payload / "scripts"
    shutil.copytree(source / "installation-context", scripts / "installation-context")
    for path in source.glob("versioned_runtime*.py"):
        shutil.copyfile(path, scripts / path.name)
    slot = root / "versions" / "1.0.0"
    native_environment = slot / ".venv"
    venv.EnvBuilder(with_pip=False, symlinks=os.name != "nt").create(native_environment)
    python = native_environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    site = (
        native_environment / "Lib" / "site-packages" if os.name == "nt" else
        native_environment / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    )
    module = site / "agent_vault"
    module.mkdir()
    (module / "__init__.py").write_text("")
    (module / "__main__.py").write_text(
        "import json,os,sys\n"
        "names=('AGENT_VAULT_HOME','AGENT_VAULT_NO_SELFPROVISION','KPDB','AGENT_VAULT_ENDPOINT')\n"
        "print(json.dumps({'argv':sys.argv[1:],'python':sys.executable,"
        "'env':{name:os.environ[name] for name in names if name in os.environ}}))\n"
    )
    (slot / ".install-complete.json").write_text(json.dumps({
        "version": "1.0.0", "completed_at": "2026-01-01T00:00:00Z", "pid": 1, "payload_hash": "a" * 64,
    }))
    (root / "current-version").write_text("1.0.0")
    bootstrap = slot / ".runtime-bootstrap"
    bootstrap.mkdir()
    for path in (source / "runtime_launcher.py", *source.glob("versioned_runtime*.py")):
        shutil.copyfile(path, bootstrap / path.name)
    (bootstrap / "runtime-generation").write_text("1" * 32)
    monkeypatch.setenv(
        "COPILOT_EXTENSIONS_CONTEXT", str(cell / "plugins" / "agent-worktrees" / "install.json"),
    )
    monkeypatch.setenv("KPDB", str(tmp_path / "foreign.kdbx"))
    monkeypatch.setenv("AGENT_VAULT_ENDPOINT", "foreign")
    owner_observations: list[dict[str, Any]] = []
    validate_owner = _peer_launch.validate_owner

    def observe_owner(owner: str, own_root: Path, raw_context: str) -> dict[str, Any]:
        value = validate_owner(owner, own_root, raw_context)
        owner_observations.append(value)
        return value

    monkeypatch.setattr(_peer_launch, "validate_owner", observe_owner)
    monkeypatch.setattr(peer_launch_adapter, "validate_owner", observe_owner)
    invocation = vault.selected()
    assert len(owner_observations) == 2
    selection = repository_setup.SSHKeySelection(
        (), str(tmp_path), "-SSH/example", str(tmp_path / "keys"), "SHA256:" + "A" * 43,
        owner="agent-vault",
    )
    code, observed = repository_ssh_setup._vault_call(
        selection, ["probe"], timeout=20, invocation=invocation,
    )
    assert code == 0
    assert observed["argv"] == ["--configuration-root", str(tmp_path), "probe"]
    assert observed["python"] == str(python)
    assert observed["env"]["AGENT_VAULT_HOME"] == str(root)
    assert observed["env"]["AGENT_VAULT_NO_SELFPROVISION"] == "1"
    assert "KPDB" not in observed["env"] and "AGENT_VAULT_ENDPOINT" not in observed["env"]
    changed_root = cell / "plugins" / changed_owner
    receipt = changed_root / "install.json"
    original_receipt = receipt.read_text()
    changed = json.loads(original_receipt)
    changed["generation"] = 2
    receipt.write_text(json.dumps(changed))
    activation = changed_root / "installation-activation.json"
    original_activation = activation.read_text()
    changed = json.loads(original_activation)
    changed.update(installGeneration=2, generation=2)
    activation.write_text(json.dumps(changed))
    monkeypatch.setattr(
        repository_ssh_setup, "run_caller_owned", lambda *_a, **_kw: pytest.fail("changed owner executed"),
    )
    with pytest.raises(SetupError, match="selection changed"):
        repository_ssh_setup._vault_call(
            selection, ["ssh-key", "prepare"], timeout=20, invocation=invocation,
        )
    for fields in ({"state": "inactive"}, {"marketplaceId": "foreign--22c1380840c88801"}):
        receipt.write_text(json.dumps(json.loads(original_receipt) | fields))
        activation.write_text(original_activation)
        with pytest.raises(SetupError, match="owner is unavailable or refused"):
            repository_ssh_setup._vault_call(
                selection, ["ssh-key", "prepare"], timeout=20, invocation=invocation,
            )
    assert not (root / "run").exists() and not (root / "state").exists()
    assert not (tmp_path / "keys").exists() and not (tmp_path / "foreign.kdbx").exists()
