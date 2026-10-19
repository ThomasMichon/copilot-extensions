"""Historical command convergence without implicit installation transfers."""

from __future__ import annotations

import base64
import json
import os
import shlex
import subprocess
import threading
from pathlib import Path

import pytest

from agent_worktrees import installer as inst

pytestmark = pytest.mark.guard

READ_PROJECTS_REGISTRY = inst.read_projects_registry


def _registry(monkeypatch, projects):
    snapshot = {"projects": projects}
    path = inst.projects_yaml_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    monkeypatch.setattr(inst, "read_projects_registry", lambda **_kwargs: snapshot)


@pytest.fixture(params=["Windows", "Linux"])
def command_home(request, monkeypatch, tmp_path):
    monkeypatch.setattr(inst.platform, "system", lambda: request.param)
    runtime = tmp_path / "runtime"
    current = tmp_path / ".copilot" / "installed-plugins" / "example" / "agent-worktrees"
    old = runtime / "snapshots" / "1.0.0"
    for root, version in ((old, "1.0.0"), (current, "1.1.0")):
        root.mkdir(parents=True)
        (root / "plugin.json").write_text(json.dumps({
            "name": "agent-worktrees", "version": version,
            "repository": "https://example.invalid/suite.git",
        }), encoding="utf-8")
    (runtime / "deploy-manifest.json").write_text(json.dumps({
        "source": {"path": current.as_posix(), "plugin": "agent-worktrees"},
    }), encoding="utf-8")
    monkeypatch.setattr(inst, "install_dir", lambda: runtime)
    monkeypatch.setattr(inst, "local_bin", lambda: tmp_path / "bin")
    monkeypatch.setattr(inst, "_command_arbitration_dir", lambda: runtime / "receipts")
    monkeypatch.setattr(inst, "projects_yaml_path", lambda: runtime / "projects.yaml")
    monkeypatch.setattr(inst.registry_paths, "installation_context", lambda: None)
    monkeypatch.setenv("AGENT_WORKTREES_PAYLOAD_ROOT", str(current))
    _registry(monkeypatch, {"demo": {"path": str(tmp_path / "repo")}})
    monkeypatch.setattr(inst, "_project_is_knowledge_only", lambda _name: False)
    monkeypatch.setattr(inst, "_project_identity", lambda name, repo_dir=None: {
        "name": inst._stub_key(name), "path": str(tmp_path / "repo"),
        "remote": "https://example.invalid/project.git",
    })
    return current, old


def seed_old(monkeypatch, roots, *, receipted=True, family="payload"):
    from agent_worktrees import project_command_templates as templates

    current, old = roots
    monkeypatch.setenv("AGENT_WORKTREES_PAYLOAD_ROOT", str(old))
    original_specs = inst._project_binstub_specs
    with monkeypatch.context() as patch:
        patch.setattr(inst, "_project_binstub_specs", lambda project, **_kwargs: [
            (inst.local_bin() / name, content)
            for name, content in templates.render(
                project, old, windows=inst.platform.system() == "Windows",
                family=family, user_bin=inst.local_bin(),
            ).items()
        ])
        inst._deploy_project_binstub("demo")
    receipt = inst._read_receipt("demo")
    receipt["runtime"]["resolver"] = "payload-local" if family == "payload" else "user-binstub"
    inst._write_receipt("demo", receipt)
    if not receipted:
        inst._receipt_path("demo").unlink()
    monkeypatch.setenv("AGENT_WORKTREES_PAYLOAD_ROOT", str(current))
    return {p: p.read_bytes() for p, _ in original_specs("demo")}


@pytest.mark.parametrize("receipted", [True, False])
def test_snapshot_command_migration(command_home, monkeypatch, receipted):
    before = seed_old(monkeypatch, command_home, receipted=receipted)
    result = inst.reconcile_binstubs()
    assert result["migrated"] == ["demo"]
    assert result["preserved"] == result["blocked"] == []
    assert all(p.read_bytes() != data for p, data in before.items())
    assert inst._read_receipt("demo")["owner"]["payload_root"] == command_home[0].as_posix()
    mtimes = {p: p.stat().st_mtime_ns for p in before}
    receipt_mtime = inst._receipt_path("demo").stat().st_mtime_ns
    second = inst.reconcile_binstubs()
    assert second["added"] == 0
    assert second["migrated"] == []
    assert mtimes == {p: p.stat().st_mtime_ns for p in before}
    assert inst._receipt_path("demo").stat().st_mtime_ns == receipt_mtime


@pytest.mark.parametrize("damage", [None, "removed-checkout", "source", "version", "repository", "cell", "edited", "project"])
def test_checkout_to_installed_owner_migrates_only_proven_generated_commands(
    command_home, monkeypatch, tmp_path, damage,
):
    current, snapshot = command_home
    checkout = tmp_path / "checkout" / "plugins" / "agent-worktrees"
    checkout.mkdir(parents=True)
    (checkout / "plugin.json").write_bytes((snapshot / "plugin.json").read_bytes())
    before = seed_old(monkeypatch, (current, checkout))
    manifest = inst.install_dir() / "deploy-manifest.json"
    source = {
        "kind": "marketplace", "plugin": "agent-worktrees",
        "path": current.as_posix(), "version": "1.1.0",
    }
    if damage in ("source", "version"):
        source["path" if damage == "source" else "version"] = "another-installation"
    manifest.write_text(json.dumps({"source": source}), encoding="utf-8")
    receipt = inst._read_receipt("demo")
    if damage == "repository":
        receipt["owner"]["repository"] = "https://example.invalid/other.git"
    elif damage == "cell":
        receipt["owner"]["install_receipt"] = "another-cell"
    elif damage == "project":
        receipt["project"]["path"] = str(tmp_path / "another-project")
    elif damage == "edited":
        next(iter(before)).write_bytes(b"operator-edited\n")
    elif damage == "removed-checkout":
        (checkout / "plugin.json").unlink()
        checkout.rmdir()
    inst._write_receipt("demo", receipt)
    before = {p: p.read_bytes() for p in before}
    receipt_before = inst._receipt_path("demo").read_bytes()
    result = inst.reconcile_binstubs()
    if damage not in (None, "removed-checkout"):
        assert result["blocked"] == result["preserved"] == ["demo"]
        assert {p: p.read_bytes() for p in before} == before
        assert inst._receipt_path("demo").read_bytes() == receipt_before
    else:
        assert result["migrated"] == ["demo"]
        assert result["blocked"] == []
        assert inst._read_receipt("demo")["runtime"]["resolver"] == "user-binstub"
        assert all(str(checkout).encode() not in p.read_bytes() for p in before)
        assert all(str(inst.local_bin()).encode() in p.read_bytes() for p in before)
        assert inst.reconcile_binstubs()["added"] == 0


def test_installed_payload_command_is_automatically_repaired(command_home, monkeypatch):
    current, _ = command_home
    seed_old(monkeypatch, (current, current))
    result = inst.reconcile_binstubs()
    assert result["migrated"] == ["demo"]
    assert result["blocked"] == []
    assert inst.reconcile_binstubs()["added"] == 0


@pytest.mark.parametrize("registered", [False, True])
def test_receipt_free_user_shortcut_does_not_identify_an_owner(
    command_home, monkeypatch, tmp_path, registered,
):
    inst._deploy_project_binstub("demo")
    inst._receipt_path("demo").unlink()
    before = {p: p.read_bytes() for p, _ in inst._project_binstub_specs("demo")}
    foreign = tmp_path / "foreign" / "agent-worktrees"
    foreign.mkdir(parents=True)
    (foreign / "plugin.json").write_bytes((command_home[0] / "plugin.json").read_bytes())
    monkeypatch.setenv("AGENT_WORKTREES_PAYLOAD_ROOT", str(foreign))
    if not registered:
        _registry(monkeypatch, {})
    result = inst.reconcile_binstubs()
    assert result["preserved"] == ["demo"]
    assert result["blocked"] == (["demo"] if registered else [])
    assert result["removed"] == []
    assert {p: p.read_bytes() for p in before} == before
    assert not inst._receipt_path("demo").exists()


@pytest.mark.parametrize("relocation", ["none", "snapshot", "checkout"])
@pytest.mark.parametrize("final_owner", ["recorded", "unrelated"])
@pytest.mark.parametrize("resolver", ["payload-local", "user-binstub"])
def test_pending_command_journal_recovers_across_proven_relocation(
    command_home, monkeypatch, tmp_path, relocation, final_owner, resolver,
):
    current, snapshot = command_home
    old = current if relocation == "none" else snapshot
    if relocation == "checkout":
        old = tmp_path / "checkout" / "plugins" / "agent-worktrees"
        old.mkdir(parents=True)
        (old / "plugin.json").write_bytes((snapshot / "plugin.json").read_bytes())
        (inst.install_dir() / "deploy-manifest.json").write_text(json.dumps({
            "source": {"kind": "marketplace", "plugin": "agent-worktrees",
                       "path": current.as_posix(), "version": "1.1.0"},
        }), encoding="utf-8")
    before = seed_old(
        monkeypatch, (current, old),
        family="payload" if resolver == "payload-local" else "current",
    )
    receipt = inst._read_receipt("demo")
    assert receipt["runtime"]["resolver"] == resolver
    receipt["transaction"] = {
        "version": 1,
        "before": {p.name: {"bytes": None, "mode": 0o755} for p in before},
        "after": {
            p.name: {"bytes": base64.b64encode(data).decode("ascii"), "mode": 0o755}
            for p, data in before.items()
        },
        "receipt": json.loads(json.dumps(receipt)),
    }
    if final_owner == "unrelated":
        receipt["transaction"]["receipt"]["owner"]["payload_root"] = str(tmp_path / "unrelated")
    inst._write_receipt("demo", receipt)
    result = inst.reconcile_binstubs()
    if final_owner == "unrelated":
        assert result["blocked"] == ["demo"]
        assert inst._read_receipt("demo") == receipt
        assert {p: p.read_bytes() for p in before} == before
        return
    assert result["blocked"] == []
    assert result["migrated"] == ["demo"]
    final = inst._read_receipt("demo")
    assert "transaction" not in final
    assert final["runtime"]["resolver"] == "user-binstub"
    assert all((data != p.read_bytes()) == (resolver == "payload-local") for p, data in before.items())
    assert inst.reconcile_binstubs()["added"] == 0


def test_registration_repairs_checkout_command_without_manual_transfer(
    command_home, monkeypatch, tmp_path,
):
    current, old = command_home
    checkout = tmp_path / "checkout" / "plugins" / "agent-worktrees"
    checkout.mkdir(parents=True)
    (checkout / "plugin.json").write_bytes((old / "plugin.json").read_bytes())
    seed_old(monkeypatch, (current, checkout))
    (inst.install_dir() / "deploy-manifest.json").write_text(json.dumps({
        "source": {"kind": "marketplace", "plugin": "agent-worktrees",
                   "path": current.as_posix(), "version": "1.1.0"},
    }), encoding="utf-8")
    with inst.project_binstub_registration("demo", repo_dir=None) as registration:
        registration.commit()
    receipt = inst._read_receipt("demo")
    assert receipt["owner"]["payload_root"] == current.as_posix()
    assert receipt["runtime"]["resolver"] == "user-binstub"
    assert all(str(checkout).encode() not in p.read_bytes()
               for p, _ in inst._project_binstub_specs("demo"))


@pytest.mark.parametrize("action", ["install", "update"])
@pytest.mark.parametrize("activated", [False, True])
def test_native_activation_publishes_manifest_before_checkout_reconciliation(
    command_home, monkeypatch, tmp_path, request, action, activated,
):
    windows = inst.platform.system() == "Windows"
    if windows != (os.name == "nt"):
        pytest.skip("native installer ordering boundary")
    current, old = command_home
    checkout = tmp_path / "checkout" / "plugins" / "agent-worktrees"
    checkout.mkdir(parents=True)
    (checkout / "plugin.json").write_bytes((old / "plugin.json").read_bytes())
    before = seed_old(monkeypatch, (current, checkout))
    manifest = inst.install_dir() / "deploy-manifest.json"
    previous = json.dumps({"source": {"kind": "local", "path": checkout.as_posix()}})
    selected = json.dumps({"source": {
        "kind": "marketplace", "plugin": "agent-worktrees",
        "path": current.as_posix(), "version": "1.1.0",
    }})
    manifest.write_text(previous, encoding="utf-8")
    plugin = Path(__file__).resolve().parents[1]
    ext = "ps1" if windows else "sh"
    source = (plugin / "scripts" / f"install.{ext}").read_text(encoding="utf-8")
    marker = f"    '{action}' {{" if windows else f"    {action})"
    body = source.split(marker, 1)[1].split("# -- Shared runtime", 1)[1]
    body = body[body.index("\n"):].split("# -- Project-specific", 1)[0]
    if windows:
        host = request.getfixturevalue("native_powershell")
        setup = "$ErrorActionPreference='Stop'\n$ContextualInstall=$false\n"
        for name in (
            "Deploy-VenvAndPackage", "Deploy-CopilotPlugin", "Deploy-GlobalBinstub",
            "Ensure-CopilotExperimental", "Assert-PathIncludes", "Remove-LegacyBinstubs",
            "Deploy-TerminalScripts", "Ensure-Psmux",
        ):
            setup += f"function {name} {{ }}\n"
        setup += f"""
function Deploy-Wrappers {{ return $true }}
function Invoke-VersionedActivate {{ return ${str(activated).lower()} }}
function Write-V3Manifest {{ [IO.File]::WriteAllText('{manifest}', '{selected}') }}
function Reconcile-Binstubs {{
    if ([IO.File]::ReadAllText('{manifest}') -cne '{selected}') {{ throw 'previous manifest still selected' }}
}}
"""
        command = [str(host), "-NoProfile", "-NonInteractive", "-File"]
    else:
        host = request.getfixturevalue("native_bash")
        setup = "set -e\nCONTEXTUAL_INSTALL=false\n"
        for name in (
            "_deploy_venv_and_package", "deploy_wrappers", "remove_legacy_scripts",
            "remove_legacy_binstubs", "deploy_tool_binstub", "deploy_copilot_plugin",
            "ensure_copilot_experimental", "assert_path",
        ):
            setup += f"{name}() {{ :; }}\n"
        setup += f"_versioned_activate() {{ {'true' if activated else 'false'}; }}\n"
        setup += f"write_deploy_manifest() {{ printf '%s' {shlex.quote(selected)} > {shlex.quote(str(manifest))}; }}\n"
        setup += f"reconcile_binstubs() {{ [[ $(cat {shlex.quote(str(manifest))}) == {shlex.quote(selected)} ]]; }}\n"
        command = [str(host)]
    script = tmp_path / f"native-order.{ext}"
    script.write_text(setup + body, encoding="utf-8")
    result = subprocess.run(
        command + [str(script)], capture_output=True, text=True,
        timeout=15, **inst.no_window_kwargs(),
    )
    assert result.returncode == (0 if activated else 1), result.stdout + result.stderr
    if activated:
        assert manifest.read_text(encoding="utf-8") == selected
        assert inst.reconcile_binstubs()["blocked"] == []
        assert inst._read_receipt("demo")["runtime"]["resolver"] == "user-binstub"
    else:
        assert manifest.read_text(encoding="utf-8") == previous
        assert {p: p.read_bytes() for p in before} == before


@pytest.mark.parametrize("receipted", [True, False])
@pytest.mark.parametrize("registered", [False, True])
def test_snapshot_obsolete_retirement(command_home, monkeypatch, registered, receipted):
    before = seed_old(monkeypatch, command_home, receipted=receipted)
    if registered:
        monkeypatch.setattr(inst, "_project_is_knowledge_only", lambda _name: True)
    else:
        _registry(monkeypatch, {})
    result = inst.reconcile_binstubs()
    assert set(result["removed"]) == {str(p) for p in before}
    assert result["preserved"] == result["blocked"] == []
    assert not inst._receipt_path("demo").exists()


@pytest.mark.parametrize("damage", ["edited", "foreign", "cell", "corrupt", "project", "source"])
def test_migration_preserves_unproven_command(command_home, monkeypatch, damage):
    before = seed_old(monkeypatch, command_home)
    receipt = inst._read_receipt("demo")
    if damage == "edited":
        path = next(iter(before))
        path.write_bytes(b"operator-owned command\n")
        before[path] = path.read_bytes()
    elif damage == "corrupt":
        inst._receipt_path("demo").write_text("{", encoding="utf-8")
    elif damage == "source":
        (inst.install_dir() / "deploy-manifest.json").write_text(
            json.dumps({"source": {"path": str(command_home[0].parent / "other")}}),
            encoding="utf-8")
    else:
        section, key = {
            "foreign": ("owner", "marketplace"),
            "cell": ("owner", "install_receipt"),
            "project": ("project", "path"),
        }[damage]
        receipt[section][key] = "another-owner"
        inst._write_receipt("demo", receipt)
    receipt_before = inst._receipt_path("demo").read_bytes()
    result = inst.reconcile_binstubs()
    assert result["preserved"] == result["blocked"] == ["demo"]
    assert {p: p.read_bytes() for p in before} == before
    assert inst._receipt_path("demo").read_bytes() == receipt_before


def test_partial_pair_is_repaired(command_home, monkeypatch):
    before = seed_old(monkeypatch, command_home)
    if inst.platform.system() != "Windows":
        pytest.skip("Windows sibling repair")
    next(iter(before)).unlink()
    result = inst.reconcile_binstubs()
    assert result["blocked"] == []
    assert all(p.exists() for p in before)


def test_obsolete_edited_sibling_protects_entire_command(command_home, monkeypatch):
    before = seed_old(monkeypatch, command_home)
    target = next(iter(before))
    target.write_bytes(b"edited without generated signature\n")
    before[target] = target.read_bytes()
    _registry(monkeypatch, {})
    result = inst.reconcile_binstubs()
    assert result["removed"] == []
    assert result["preserved"] == ["demo"]
    assert result["blocked"] == []
    assert {p: p.read_bytes() for p in before} == before


def test_foreign_inventory_is_advisory(command_home, monkeypatch):
    seed_old(monkeypatch, command_home)
    receipt = inst._read_receipt("demo")
    receipt["owner"]["marketplace"] = "another-marketplace"
    inst._write_receipt("demo", receipt)
    _registry(monkeypatch, {})
    result = inst.reconcile_binstubs()
    assert result["preserved"] == ["demo"]
    assert result["blocked"] == []


@pytest.mark.parametrize("edited", [False, True])
def test_windows_remove_uses_registered_spelling(command_home, monkeypatch, edited):
    if inst.platform.system() != "Windows" or os.name != "nt":
        pytest.skip("native Windows command casing")
    _registry(monkeypatch, {"Demo": {}})
    inst._deploy_project_binstub("Demo")
    before = {p: p.read_bytes() for p, _ in inst._project_binstub_specs("Demo")}
    if edited:
        target = next(iter(before))
        target.write_bytes(b"operator edit\n")
        before[target] = target.read_bytes()
        with pytest.raises(inst.BinstubOwnershipError, match="modified"):
            inst.remove_project_binstub("demo")
        assert {p: p.read_bytes() for p in before} == before
    else:
        assert set(inst.remove_project_binstub("demo")) == set(before)
        assert not any(p.exists() for p in before)
        assert not inst._receipt_path("Demo").exists()


@pytest.mark.parametrize("invalid_keys", [None, "before", "after", "unexpected"])
def test_windows_receipt_only_retirement_recovers_casing(
    command_home, monkeypatch, capsys, invalid_keys
):
    if inst.platform.system() != "Windows" or os.name != "nt":
        pytest.skip("native Windows receipt-only recovery")
    _registry(monkeypatch, {"Demo": {}})
    inst._deploy_project_binstub("Demo")
    _registry(monkeypatch, {})
    receipt_path = inst._receipt_path("Demo")
    unlink = Path.unlink

    def interrupt_receipt_unlink(path, *args, **kwargs):
        if path == receipt_path:
            raise KeyboardInterrupt("interrupted before retirement receipt removal")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", interrupt_receipt_unlink)
    with pytest.raises(KeyboardInterrupt):
        inst.remove_project_binstub("Demo")
    monkeypatch.setattr(Path, "unlink", unlink)
    assert not any(p.exists() for p, _ in inst._project_binstub_specs("Demo"))
    receipt = inst._read_receipt("Demo")
    # Existing version-one journals used the original generated spelling.
    for section in ("before", "after"):
        states = receipt["transaction"][section]
        receipt["transaction"][section] = {
            "Demo" + Path(key).suffix: value for key, value in states.items()
        }
    if invalid_keys in ("before", "after"):
        states = receipt["transaction"][invalid_keys]
        states["demo.ps1"] = dict(states["Demo.ps1"])
    elif invalid_keys == "unexpected":
        receipt["transaction"]["after"]["other.cmd"] = {"bytes": None, "mode": 0o755}
    inst._write_receipt("Demo", receipt)
    capsys.readouterr()
    result = inst.reconcile_binstubs()
    if invalid_keys is None:
        assert result["blocked"] == result["preserved"] == []
        assert not receipt_path.exists()
    else:
        assert result["preserved"] == ["demo"]
        assert inst._read_receipt("Demo") == receipt
        assert "invalid interrupted command transaction" in capsys.readouterr().out


@pytest.mark.parametrize("section", ["project", "owner", "runtime", "stubs"])
def test_corrupt_nested_final_receipt_preserves_command_and_continues_peer(
    command_home, monkeypatch, section
):
    before = seed_old(monkeypatch, command_home)
    receipt = inst._read_receipt("demo")
    states = {
        p.name: {"bytes": base64.b64encode(data).decode("ascii"), "mode": 0o755}
        for p, data in before.items()
    }
    final = json.loads(json.dumps(receipt))
    final[section] = None
    receipt["transaction"] = {
        "version": 1, "before": states, "after": states, "receipt": final,
    }
    inst._write_receipt("demo", receipt)
    _registry(monkeypatch, {"demo": {}, "valid-peer": {}})
    result = inst.reconcile_binstubs()
    assert result["blocked"] == result["preserved"] == ["demo"]
    assert {p: p.read_bytes() for p in before} == before
    assert inst._read_receipt("demo") == receipt
    assert all(p.exists() for p, _ in inst._project_binstub_specs("valid-peer"))


@pytest.mark.parametrize("receipted", [True, False])
def test_admitted_unregistered_retirement_failure_is_required(
    command_home, monkeypatch, capsys, receipted
):
    from agent_worktrees import project_command_transaction as tx

    before = seed_old(monkeypatch, command_home, receipted=receipted)
    _registry(monkeypatch, {})

    def fail_publication(*_args, **_kwargs):
        raise PermissionError("retirement publication denied")

    monkeypatch.setattr(tx, "publish", fail_publication)
    result = inst.reconcile_binstubs()
    assert result["blocked"] == result["preserved"] == ["demo"]
    assert result["removed"] == []
    assert {p: p.read_bytes() for p in before} == before
    assert "in sync" not in capsys.readouterr().out


@pytest.mark.parametrize("failure", ["permission", "edited", "foreign"])
def test_interrupted_unregistered_retirement_retains_admission(
    command_home, monkeypatch, capsys, failure
):
    from agent_worktrees import project_command_transaction as tx

    before = seed_old(monkeypatch, command_home)
    _registry(monkeypatch, {})
    original = tx._replace

    def interrupt(path, data, mode, **kwargs):
        original(path, data, mode, **kwargs)
        raise KeyboardInterrupt("interrupted retirement")

    monkeypatch.setattr(tx, "_replace", interrupt)
    with pytest.raises(KeyboardInterrupt):
        inst.reconcile_binstubs()
    monkeypatch.setattr(tx, "_replace", original)
    if failure == "permission":
        def denied(*_args, **_kwargs):
            raise PermissionError("retirement recovery denied")
        monkeypatch.setattr(tx, "_replace", denied)
    elif failure == "edited":
        next(iter(before)).write_bytes(b"edited during retirement\n")
    else:
        receipt = inst._read_receipt("demo")
        receipt["owner"]["marketplace"] = "other-marketplace"
        inst._write_receipt("demo", receipt)
    intermediate = {p: p.read_bytes() if p.exists() else None for p in before}
    capsys.readouterr()
    result = inst.reconcile_binstubs()
    assert result["preserved"] == ["demo"]
    assert result["blocked"] == ([] if failure == "foreign" else ["demo"])
    assert result["removed"] == []
    assert {p: p.read_bytes() if p.exists() else None for p in before} == intermediate
    if failure != "foreign":
        assert "in sync" not in capsys.readouterr().out


@pytest.mark.parametrize("publication", ["command", "receipt"])
def test_interrupted_publication_recovers(command_home, monkeypatch, publication):
    before = seed_old(monkeypatch, command_home)
    original = inst._write_receipt
    armed = True

    def interrupted(project, data):
        nonlocal armed
        if armed and (
            ("transaction" in data and publication == "command")
            or ("transaction" not in data and publication == "receipt")
        ):
            armed = False
            if publication == "command":
                original(project, data)
            raise KeyboardInterrupt("simulated process interruption")
        original(project, data)

    monkeypatch.setattr(inst, "_write_receipt", interrupted)
    with pytest.raises(KeyboardInterrupt):
        inst.reconcile_binstubs()
    result = inst.reconcile_binstubs()
    assert result["blocked"] == []
    assert all(p.read_bytes() != data for p, data in before.items())
    assert "transaction" not in inst._read_receipt("demo")


def test_concurrent_reconcile_is_idempotent(command_home, monkeypatch):
    seed_old(monkeypatch, command_home)
    results, errors = [], []

    def run():
        try:
            results.append(inst.reconcile_binstubs())
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
        assert not thread.is_alive()
    assert errors == []
    assert len(results) == 2
    assert all(result["blocked"] == [] for result in results)
    assert sum(bool(result["migrated"]) for result in results) == 1


def historical_fixture(old: Path, *, ambient: bool) -> dict[str, bytes]:
    """Literal #1901 predecessor / #1909 predecessor templates (not recognizer output)."""
    if inst.platform.system() != "Windows":
        header = "" if ambient else "# agent-worktrees project binstub\n"
        launch = (
            '  export WORKTREE_PROJECT=demo\n  exec "$HOME/.agent-worktrees/bin/launch-session.sh"'
            if ambient else '  exec "$HOME/.agent-worktrees/bin/launch-session.sh" --project demo'
        )
        return {"demo": (
            f"#!/usr/bin/env bash\n{header}"
            "export PYTHONUTF8=1\n"
            "export AGENT_WORKTREES_LAUNCH_ID=demo-$$-$RANDOM-$(date +%s)\n"
            'export AGENT_WORKTREES_BINSTUB_STARTED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"\n'
            'export AGENT_WORKTREES_LAUNCH_TRACE="$HOME/.agent-worktrees/logs/picker-launches.jsonl"\n'
            'mkdir -p "$(dirname "$AGENT_WORKTREES_LAUNCH_TRACE")" 2>/dev/null || true\n'
            """printf '%s\\n' '{"event":"binstub_start","timestamp":"'"$AGENT_WORKTREES_BINSTUB_STARTED"'","launch_id":"'"$AGENT_WORKTREES_LAUNCH_ID"'","project":"demo"}' >>"$AGENT_WORKTREES_LAUNCH_TRACE" 2>/dev/null || true\n"""
            f"if [[ $# -eq 0 ]]; then\n{launch}\nfi\n"
            "# This attributable project entry point is pinned to its owning payload.\n"
            f'exec {shlex.quote(str(old / "bin" / "payload" / "agent-worktrees"))} --project demo "$@"\n'
        ).encode("utf-8")}
    cmd_header = "" if ambient else "rem agent-worktrees project binstub\n"
    cmd_launch = (
        '  set "WORKTREE_PROJECT=demo"\n  call "%USERPROFILE%\\.agent-worktrees\\bin\\launch-session.cmd"'
        if ambient else '  call "%USERPROFILE%\\.agent-worktrees\\bin\\launch-session.cmd" --project demo'
    )
    cmd = (
        f"@echo off\n{cmd_header}"
        'set "PYTHONUTF8=1"\nset "AGENT_WORKTREES_LAUNCH_ID=demo-%RANDOM%-%RANDOM%"\n'
        'set "AGENT_WORKTREES_BINSTUB_STARTED=%DATE% %TIME%"\n'
        'set "AGENT_WORKTREES_LAUNCH_TRACE=%USERPROFILE%\\.agent-worktrees\\logs\\picker-launches.jsonl"\n'
        'if not exist "%USERPROFILE%\\.agent-worktrees\\logs" mkdir "%USERPROFILE%\\.agent-worktrees\\logs" >nul 2>&1\n'
        '(>>"%AGENT_WORKTREES_LAUNCH_TRACE%" echo {"event":"binstub_start","timestamp":"%AGENT_WORKTREES_BINSTUB_STARTED%","launch_id":"%AGENT_WORKTREES_LAUNCH_ID%","project":"demo"}) 2>nul\n'
        f'if "%~1"=="" (\n{cmd_launch}\n  exit /b %ERRORLEVEL%\n)\n'
        "rem This attributable project entry point is pinned to its owning payload.\n"
        f'"{old / "bin" / "payload" / "agent-worktrees.cmd"}" --project demo %*\n'
        "exit /b %ERRORLEVEL%"
    )
    ps_header = "" if ambient else "# agent-worktrees project binstub\n"
    ps_launch = (
        "    $_savedProj = $env:WORKTREE_PROJECT\n"
        "    $env:WORKTREE_PROJECT = 'demo'\n"
        "    try {\n"
        '        & "$env:USERPROFILE\\.agent-worktrees\\bin\\launch-session.ps1"\n'
        "        $_rc = $LASTEXITCODE\n    } finally {\n"
        "        if ($null -eq $_savedProj) { Remove-Item Env:WORKTREE_PROJECT -ErrorAction SilentlyContinue } else { $env:WORKTREE_PROJECT = $_savedProj }\n"
        "    }\n    exit $_rc"
        if ambient else
        '    & "$env:USERPROFILE\\.agent-worktrees\\bin\\launch-session.ps1" --project \'demo\'\n'
        "    exit $LASTEXITCODE"
    )
    ps = (
        f"{ps_header}$env:PYTHONUTF8 = '1'\n"
        "$env:AGENT_WORKTREES_LAUNCH_ID = 'demo-' + [guid]::NewGuid().ToString('N')\n"
        "$env:AGENT_WORKTREES_BINSTUB_STARTED = [DateTime]::UtcNow.ToString('o')\n"
        "$_awTraceDir = Join-Path $env:USERPROFILE '.agent-worktrees\\logs'\n"
        "$env:AGENT_WORKTREES_LAUNCH_TRACE = Join-Path $_awTraceDir 'picker-launches.jsonl'\n"
        "try {\n    [IO.Directory]::CreateDirectory($_awTraceDir) | Out-Null\n"
        "    $_awEvent = [ordered]@{ event = 'binstub_start'; timestamp = $env:AGENT_WORKTREES_BINSTUB_STARTED; launch_id = $env:AGENT_WORKTREES_LAUNCH_ID; project = 'demo' }\n"
        "    [IO.File]::AppendAllText($env:AGENT_WORKTREES_LAUNCH_TRACE, ($_awEvent | ConvertTo-Json -Compress) + [Environment]::NewLine)\n"
        f"}} catch {{}}\nif ($args.Count -eq 0) {{\n{ps_launch}\n}}\n"
        "# This attributable project entry point is pinned to its owning payload.\n"
        f"& '{old / 'bin' / 'payload' / 'agent-worktrees.ps1'}' --project 'demo' @args\n"
        "exit $LASTEXITCODE"
    )
    return {name: data.replace("\n", "\r\n").encode("utf-8")
            for name, data in (("demo.cmd", cmd), ("demo.ps1", ps))}


@pytest.mark.parametrize("ambient", [True, False])
@pytest.mark.parametrize("receipted", [True, False])
def test_historical_template_families(command_home, monkeypatch, ambient, receipted):
    before = seed_old(monkeypatch, command_home, receipted=receipted)
    golden = historical_fixture(command_home[1], ambient=ambient)
    for p in before:
        p.write_bytes(golden[p.name])
    if receipted:
        receipt = inst._read_receipt("demo")
        receipt["stubs"] = {p.name: inst._file_sha256(p) for p in before}
        inst._write_receipt("demo", receipt)
    result = inst.reconcile_binstubs()
    assert result["migrated"] == ["demo"]
    assert result["blocked"] == []
    assert all(b"WORKTREE_PROJECT" not in p.read_bytes() for p in before)


def test_old_caller_cannot_downgrade_command(command_home, monkeypatch):
    inst._deploy_project_binstub("demo")
    before = {p: p.read_bytes() for p, _ in inst._project_binstub_specs("demo")}
    monkeypatch.setenv("AGENT_WORKTREES_PAYLOAD_ROOT", str(command_home[1]))
    result = inst.reconcile_binstubs()
    assert result["blocked"] == ["demo"]
    assert {p: p.read_bytes() for p in before} == before


def test_receipt_source_can_differ_from_exact_snapshot_route(command_home, monkeypatch):
    """The receipt can name the installed source while bytes pin its snapshot."""
    seed_old(monkeypatch, command_home)
    receipt = inst._read_receipt("demo")
    receipt["owner"] = inst._binstub_owner()
    inst._write_receipt("demo", receipt)
    result = inst.reconcile_binstubs()
    assert result["migrated"] == ["demo"]
    assert result["blocked"] == []


def test_same_owner_edited_pair_is_preserved(command_home, monkeypatch):
    inst._deploy_project_binstub("demo")
    before = {p: p.read_bytes() for p, _ in inst._project_binstub_specs("demo")}
    p = next(iter(before))
    p.write_bytes(b"edited\n")
    before[p] = p.read_bytes()
    monkeypatch.setattr(inst, "_project_is_knowledge_only", lambda _name: True)
    result = inst.reconcile_binstubs()
    assert result["removed"] == []
    assert result["blocked"] == ["demo"]
    assert {p: p.read_bytes() for p in before} == before


def test_failed_publication_restores_usable_command(command_home, monkeypatch):
    before = seed_old(monkeypatch, command_home)
    previous = inst._receipt_path("demo").read_bytes()
    original = inst._write_receipt

    def failed(project, data):
        if "transaction" not in data:
            raise OSError("publication failed")
        original(project, data)

    monkeypatch.setattr(inst, "_write_receipt", failed)
    result = inst.reconcile_binstubs()
    assert result["blocked"] == ["demo"]
    assert {p: p.read_bytes() for p in before} == before
    assert inst._receipt_path("demo").read_bytes() == previous


@pytest.mark.parametrize("retire", [False, True])
@pytest.mark.parametrize("edited", [False, True])
def test_interrupted_sibling_transition(command_home, monkeypatch, retire, edited):
    from agent_worktrees import project_command_transaction as tx

    before = seed_old(monkeypatch, command_home)
    if retire:
        monkeypatch.setattr(inst, "_project_is_knowledge_only", lambda _name: True)
    original = tx._replace
    interrupted_path = None

    def interrupted(path, data, mode, **kwargs):
        nonlocal interrupted_path
        original(path, data, mode, **kwargs)
        if interrupted_path is None:
            interrupted_path = path
            raise KeyboardInterrupt("interrupted after first sibling")

    monkeypatch.setattr(tx, "_replace", interrupted)
    with pytest.raises(KeyboardInterrupt):
        inst.reconcile_binstubs()
    monkeypatch.setattr(tx, "_replace", original)
    if edited:
        interrupted_path.write_bytes(b"edited intermediate command\n")
        intermediate = {p: p.read_bytes() if p.exists() else None for p in before}
    result = inst.reconcile_binstubs()
    if edited:
        assert result["blocked"] == ["demo"]
        assert {p: p.read_bytes() if p.exists() else None for p in before} == intermediate
    else:
        assert result["blocked"] == []
        assert all(p.exists() != retire for p in before)
        if retire:
            assert not inst._receipt_path("demo").exists()
        else:
            assert "transaction" not in inst._read_receipt("demo")


def test_corrupt_transaction_is_preserved(command_home, monkeypatch):
    seed_old(monkeypatch, command_home)
    receipt = inst._read_receipt("demo")
    receipt["transaction"] = {"version": 1}
    inst._write_receipt("demo", receipt)
    before = {p: p.read_bytes() for p, _ in inst._project_binstub_specs("demo")}
    result = inst.reconcile_binstubs()
    assert result["blocked"] == ["demo"]
    assert {p: p.read_bytes() for p in before} == before
    assert inst._read_receipt("demo") == receipt


def test_explicit_transfer_repairs_non_utf8_receipt(command_home, monkeypatch):
    inst._deploy_project_binstub("demo")
    paths = [p for p, _ in inst._project_binstub_specs("demo")]
    paths[0].write_bytes(b"unknown command\n")
    before = {p: p.read_bytes() for p in paths}
    receipt_path = inst._receipt_path("demo")
    corrupt = b"\xff\xfeinvalid receipt"
    receipt_path.write_bytes(corrupt)

    result = inst.reconcile_binstubs()
    assert result["blocked"] == result["preserved"] == ["demo"]
    assert {p: p.read_bytes() for p in paths} == before
    assert receipt_path.read_bytes() == corrupt
    with pytest.raises(inst.BinstubOwnershipError, match="unreadable"):
        inst._deploy_project_binstub("demo")

    assert inst.transfer_project_binstub("demo") > 0
    receipt = inst._read_receipt("demo")
    assert receipt["owner"] == inst._binstub_owner()
    for p, content in inst._project_binstub_specs("demo"):
        assert p.read_bytes() == content.encode("utf-8")
        assert inst._stub_hash(receipt, p) == inst._file_sha256(p)
    timestamps = {p: p.stat().st_mtime_ns for p in paths + [receipt_path]}
    assert inst.transfer_project_binstub("demo") == 0
    assert inst.reconcile_binstubs()["blocked"] == []
    assert timestamps == {p: p.stat().st_mtime_ns for p in paths + [receipt_path]}


@pytest.mark.parametrize("receipted", [True, False])
@pytest.mark.parametrize("damage", [
    "yaml", "root", "projects", "entry", "name", "read", "utf8", "missing", "empty", "no-targets",
])
def test_invalid_authoritative_registry_never_prunes_or_retires(
    command_home, monkeypatch, receipted, damage
):
    before = seed_old(monkeypatch, command_home, receipted=receipted)
    receipt_path = inst._receipt_path("demo")
    receipt_before = receipt_path.read_bytes() if receipt_path.exists() else None
    path = inst.install_dir() / "projects.yaml"
    monkeypatch.setattr(inst, "projects_yaml_path", lambda: path)
    monkeypatch.setattr(inst, "read_projects_registry", READ_PROJECTS_REGISTRY)
    writes = []
    monkeypatch.setattr(inst, "write_projects_registry", lambda *args, **kwargs: writes.append(args))
    contents = {
        "yaml": "projects: [\n",
        "root": "[]\n",
        "projects": "projects: [demo]\n",
        "entry": "projects:\n  agent-worktrees: {}\n  demo: 3\n",
        "name": "projects:\n  agent-worktrees: {}\n  ../bad: {}\n",
        "read": "projects: {}\n",
        "utf8": "",
        "empty": "",
        "no-targets": "schema_version: 2\n",
    }
    if damage != "missing":
        path.write_text(contents[damage], encoding="utf-8")
    else:
        path.unlink()
    if damage == "utf8":
        path.write_bytes(b"\xff")
    if damage == "read":
        original_read = Path.read_text

        def denied(target, *args, **kwargs):
            if target == path:
                raise PermissionError("registry read denied")
            return original_read(target, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", denied)
    with pytest.raises(inst.BinstubOwnershipError, match="projects registry"):
        inst.reconcile_binstubs()
    assert writes == []
    assert {p: p.read_bytes() for p in before} == before
    assert (receipt_path.read_bytes() if receipt_path.exists() else None) == receipt_before


@pytest.mark.parametrize("contents", [None, "projects: {}\n", "projects:\n  demo: {}\n"])
def test_fresh_registry_and_legacy_targets_remain_supported(command_home, monkeypatch, contents):
    path = inst.install_dir() / "projects.yaml"
    monkeypatch.setattr(inst, "projects_yaml_path", lambda: path)
    monkeypatch.setattr(inst, "read_projects_registry", READ_PROJECTS_REGISTRY)
    path.unlink(missing_ok=True)
    if contents is not None:
        path.write_text(contents, encoding="utf-8")
    result = inst.reconcile_binstubs()
    assert result["blocked"] == []
    assert result["registered"] == (["demo"] if contents and "demo" in contents else [])


def test_reconcile_reuses_one_strict_registry_snapshot(command_home, monkeypatch):
    path = inst.install_dir() / "projects.yaml"
    path.write_text("projects:\n  agent-worktrees: {}\n  demo: {}\n", encoding="utf-8")
    monkeypatch.setattr(inst, "projects_yaml_path", lambda: path)
    calls, writes = [], []

    def read_once(*, strict=False):
        calls.append(strict)
        assert calls == [True], "reconciliation reread the target registry"
        return READ_PROJECTS_REGISTRY(strict=strict)

    monkeypatch.setattr(inst, "read_projects_registry", read_once)
    monkeypatch.setattr(inst, "write_projects_registry", lambda data: writes.append(data))
    result = inst.reconcile_binstubs()
    assert result["registered"] == ["demo"]
    assert result["blocked"] == []
    assert calls == [True]
    assert len(writes) == 1
    assert set(writes[0]["projects"]) == {"demo"}


@pytest.mark.parametrize("phase", ["publication", "recovery", "rollback"])
@pytest.mark.parametrize("retire", [False, True])
@pytest.mark.parametrize("edit_after", ["first", "last"])
def test_interleaved_windows_sibling_edit_is_never_overwritten(
    command_home, monkeypatch, phase, retire, edit_after
):
    from agent_worktrees import project_command_transaction as tx

    if inst.platform.system() != "Windows":
        pytest.skip("Windows command pair interleaving")
    seed_old(monkeypatch, command_home)
    paths = [p for p, _ in inst._project_binstub_specs("demo")]
    if retire:
        monkeypatch.setattr(inst, "_project_is_knowledge_only", lambda _name: True)
    original_write = inst._write_receipt
    if phase == "recovery":
        def interrupt(project, data):
            original_write(project, data)
            raise KeyboardInterrupt("interrupt after journal admission")

        monkeypatch.setattr(inst, "_write_receipt", interrupt)
        with pytest.raises(KeyboardInterrupt):
            inst.reconcile_binstubs()
        monkeypatch.setattr(inst, "_write_receipt", original_write)

    rollback = False
    if phase == "rollback":
        if retire:
            original_unlink = Path.unlink

            def fail_retirement(path, *args, **kwargs):
                nonlocal rollback
                if path == inst._receipt_path("demo") and not rollback:
                    rollback = True
                    raise PermissionError("receipt retirement denied")
                return original_unlink(path, *args, **kwargs)

            monkeypatch.setattr(Path, "unlink", fail_retirement)
        else:
            def fail_receipt(project, data):
                nonlocal rollback
                if "transaction" not in data and not rollback:
                    rollback = True
                    raise PermissionError("final receipt publication denied")
                original_write(project, data)

            monkeypatch.setattr(inst, "_write_receipt", fail_receipt)

    original_replace = tx._replace
    edited = False
    user_bytes = b"operator edit between sibling mutations\n"
    trigger = paths[0] if edit_after == "first" else paths[1]
    edited_path = paths[1] if edit_after == "first" else paths[0]

    def interleaved(path, data, mode, **kwargs):
        nonlocal edited
        original_replace(path, data, mode, **kwargs)
        if path == trigger and not edited and (phase != "rollback" or rollback):
            edited_path.write_bytes(user_bytes)
            edited = True

    monkeypatch.setattr(tx, "_replace", interleaved)
    result = inst.reconcile_binstubs()
    assert edited
    assert result["blocked"] == result["preserved"] == ["demo"]
    assert edited_path.read_bytes() == user_bytes
    assert "transaction" in inst._read_receipt("demo")
    interrupted_state = {p: p.read_bytes() if p.exists() else None for p in paths}
    assert inst.reconcile_binstubs()["blocked"] == ["demo"]
    assert {p: p.read_bytes() if p.exists() else None for p in paths} == interrupted_state


@pytest.mark.parametrize("phase", ["publication", "recovery", "rollback"])
def test_edit_during_staging_is_rechecked_at_replace_boundary(command_home, monkeypatch, phase):
    from agent_worktrees import project_command_transaction as tx

    if inst.platform.system() != "Windows":
        pytest.skip("Windows command pair staging")
    seed_old(monkeypatch, command_home)
    paths = [p for p, _ in inst._project_binstub_specs("demo")]
    original_write = inst._write_receipt
    if phase == "recovery":
        def interrupt(project, data):
            original_write(project, data)
            raise KeyboardInterrupt("interrupt after admission")

        monkeypatch.setattr(inst, "_write_receipt", interrupt)
        with pytest.raises(KeyboardInterrupt):
            inst.reconcile_binstubs()
        monkeypatch.setattr(inst, "_write_receipt", original_write)
    rollback = False
    if phase == "rollback":
        def fail_receipt(project, data):
            nonlocal rollback
            if "transaction" not in data and not rollback:
                rollback = True
                raise PermissionError("final receipt denied")
            original_write(project, data)

        monkeypatch.setattr(inst, "_write_receipt", fail_receipt)

    active_path = None
    original_replace, original_fsync = tx._replace, os.fsync
    edited = False
    user_bytes = b"operator edit while replacement was staged\n"

    def replacing(path, data, mode, **kwargs):
        nonlocal active_path
        active_path = path
        try:
            return original_replace(path, data, mode, **kwargs)
        finally:
            active_path = None

    def interleaved_fsync(fd):
        nonlocal edited
        original_fsync(fd)
        if active_path == paths[1] and not edited and (phase != "rollback" or rollback):
            paths[1].write_bytes(user_bytes)
            edited = True

    monkeypatch.setattr(tx, "_replace", replacing)
    monkeypatch.setattr(os, "fsync", interleaved_fsync)
    result = inst.reconcile_binstubs()
    assert edited
    assert result["blocked"] == ["demo"]
    assert paths[1].read_bytes() == user_bytes
    assert "transaction" in inst._read_receipt("demo")


@pytest.mark.parametrize("generation", ["current", "historical", "pre-receipt"])
@pytest.mark.parametrize("operation", ["refresh", "knowledge", "unregistered"])
def test_admission_to_capture_edit_never_becomes_authorized(
    command_home, monkeypatch, generation, operation
):
    from agent_worktrees import project_command_transaction as tx

    from agent_worktrees import project_command_migration as migration

    if generation == "current":
        inst._deploy_project_binstub("demo")
    else:
        seed_old(monkeypatch, command_home, receipted=generation != "pre-receipt")
    identity = inst._project_identity("demo")
    paths = [p for p, _ in inst._project_binstub_specs("demo")]
    receipt_path = inst._receipt_path("demo")
    receipt_before = receipt_path.read_bytes() if receipt_path.exists() else None
    user_bytes = b"operator edit after validation before capture\n"
    original_capture = tx._capture
    injected = False
    owner = inst._binstub_owner()
    admitted = migration.validate(
        "demo", inst._read_receipt("demo"), owner, None if operation == "unregistered" else identity
    )

    def interleaved_capture(targets):
        nonlocal injected
        if not injected:
            paths[-1].write_bytes(user_bytes)
            injected = True
        return original_capture(targets)

    monkeypatch.setattr(tx, "_capture", interleaved_capture)
    with pytest.raises(inst.BinstubContentError, match="admission"):
        tx.publish("demo", owner, identity, inst._project_binstub_specs("demo"),
                   admitted=admitted, retire=operation != "refresh")
    assert injected
    assert paths[-1].read_bytes() == user_bytes
    assert all(p.exists() for p in paths)
    assert (receipt_path.read_bytes() if receipt_path.exists() else None) == receipt_before


def test_transfer_admission_does_not_authorize_later_edit(command_home, monkeypatch):
    from agent_worktrees import project_command_transaction as tx
    from agent_worktrees import project_command_migration as migration

    owner, identity = inst._binstub_owner(), inst._project_identity("demo")
    inst.local_bin().mkdir(parents=True, exist_ok=True)
    tx.publish("demo", owner, identity, inst._project_binstub_specs("demo"),
               admitted=migration.validate("demo", None, owner, identity))
    paths = [p for p, _ in inst._project_binstub_specs("demo")]
    paths[0].write_bytes(b"explicitly authorized prior custom command\n")
    receipt_path = inst._receipt_path("demo")
    receipt_path.write_bytes(b"\xffinvalid prior receipt")
    receipt_before = receipt_path.read_bytes()
    original_capture = tx._capture
    user_bytes = b"later edit not authorized by explicit transfer\n"
    admitted = migration.admit_transfer("demo")

    def interleaved_capture(targets):
        paths[-1].write_bytes(user_bytes)
        return original_capture(targets)

    monkeypatch.setattr(tx, "_capture", interleaved_capture)
    with pytest.raises(inst.BinstubOwnershipError, match="admi"):
        tx.publish("demo", owner, identity, inst._project_binstub_specs("demo"),
                   admitted=admitted, transfer=True)
    assert paths[-1].read_bytes() == user_bytes
    assert paths[0].read_bytes() == (
        user_bytes if paths[0] == paths[-1] else b"explicitly authorized prior custom command\n"
    )
    assert receipt_path.read_bytes() == receipt_before


def test_admission_evidence_is_immutable_and_fresh_creation_is_bound(command_home, monkeypatch):
    from agent_worktrees import project_command_migration as migration
    from agent_worktrees import project_command_transaction as tx

    owner, identity = inst._binstub_owner(), inst._project_identity("demo")
    admitted = migration.validate("demo", None, owner, identity)
    with pytest.raises(TypeError):
        admitted.stubs["demo"] = b"cannot widen admission"
    paths = [p for p, _ in inst._project_binstub_specs("demo")]
    original_capture = tx._capture

    def interleaved_capture(targets):
        paths[0].write_bytes(b"new command after absent-path admission\n")
        return original_capture(targets)

    inst.local_bin().mkdir(parents=True)
    monkeypatch.setattr(tx, "_capture", interleaved_capture)
    with pytest.raises(inst.BinstubContentError, match="admission"):
        tx.publish("demo", owner, identity, inst._project_binstub_specs("demo"), admitted=admitted)
    assert paths[0].read_bytes() == b"new command after absent-path admission\n"
    assert not inst._receipt_path("demo").exists()


def test_validation_checks_only_the_captured_admission_bytes(command_home, monkeypatch):
    from agent_worktrees import project_command_migration as migration
    from agent_worktrees import project_command_templates as templates
    from agent_worktrees import project_command_transaction as tx

    seed_old(monkeypatch, command_home, receipted=False)
    owner, identity = inst._binstub_owner(), inst._project_identity("demo")
    paths = [p for p, _ in inst._project_binstub_specs("demo")]
    before = {inst._stub_key(p.name): p.read_bytes() for p in paths}
    original_matches = templates.matches
    edited = False

    def interleaved_validation(data, content):
        nonlocal edited
        if not edited:
            paths[-1].write_bytes(b"edit during template verification\n")
            edited = True
        return original_matches(data, content)

    monkeypatch.setattr(templates, "matches", interleaved_validation)
    owner = inst._binstub_owner()
    admitted = migration.validate("demo", None, owner, identity)
    assert dict(admitted.stubs) == before
    with pytest.raises(inst.BinstubContentError, match="admission"):
        tx.publish("demo", owner, identity, inst._project_binstub_specs("demo"), admitted=admitted)
    assert paths[-1].read_bytes() == b"edit during template verification\n"
    assert not inst._receipt_path("demo").exists()


@pytest.mark.parametrize("generation", ["current", "historical", "pre-receipt"])
@pytest.mark.parametrize("operation", ["refresh", "knowledge", "unregistered", "transfer"])
def test_installer_preserves_admission_through_publication(
    command_home, monkeypatch, generation, operation
):
    from agent_worktrees import project_command_transaction as tx

    if generation == "current":
        inst._deploy_project_binstub("demo")
    else:
        seed_old(monkeypatch, command_home, receipted=generation != "pre-receipt")
    if operation == "knowledge":
        monkeypatch.setattr(inst, "_project_is_knowledge_only", lambda _name: True)
    elif operation == "unregistered":
        _registry(monkeypatch, {})
    paths = [p for p, _ in inst._project_binstub_specs("demo")]
    receipt_path = inst._receipt_path("demo")
    receipt_before = receipt_path.read_bytes() if receipt_path.exists() else None
    original_capture = tx._capture
    user_bytes = b"operator edit between installer admission and publication\n"

    def interleaved_capture(targets):
        paths[-1].write_bytes(user_bytes)
        return original_capture(targets)

    monkeypatch.setattr(tx, "_capture", interleaved_capture)
    if operation == "transfer":
        with pytest.raises(inst.BinstubContentError, match="admission"):
            inst.transfer_project_binstub("demo")
    else:
        result = inst.reconcile_binstubs()
        assert result["blocked"] == result["preserved"] == ["demo"]
    assert paths[-1].read_bytes() == user_bytes
    assert all(p.exists() for p in paths)
    assert (receipt_path.read_bytes() if receipt_path.exists() else None) == receipt_before


def test_registration_does_not_recapture_after_preflight(command_home):
    paths = [p for p, _ in inst._project_binstub_specs("demo")]
    with pytest.raises(inst.BinstubContentError, match="admission"):
        with inst.project_binstub_registration("demo", repo_dir=None) as registration:
            paths[0].write_bytes(b"new operator command after registration preflight\n")
            registration.commit()
    assert paths[0].read_bytes() == b"new operator command after registration preflight\n"
    assert not inst._receipt_path("demo").exists()


def test_publication_without_admission_fails_before_capture(command_home, monkeypatch):
    from agent_worktrees import project_command_transaction as tx

    monkeypatch.setattr(tx, "_capture", lambda _paths: pytest.fail("missing evidence recaptured authority"))
    with pytest.raises(inst.BinstubOwnershipError, match="admission"):
        tx.publish("demo", inst._binstub_owner(), inst._project_identity("demo"),
                   inst._project_binstub_specs("demo"))
    assert not inst._receipt_path("demo").exists()


def _seed_foreign_transfer(command_home, monkeypatch, receipt_kind):
    current, _old = command_home
    foreign = current.parent.parent / "foreign" / "agent-worktrees"
    foreign.mkdir(parents=True)
    (foreign / "plugin.json").write_bytes((current / "plugin.json").read_bytes())
    seed_old(monkeypatch, (foreign, foreign))
    receipt_path = inst._receipt_path("demo")
    if receipt_kind == "foreign-project":
        receipt = inst._read_receipt("demo")
        receipt["project"] = {"name": "prior-project", "path": "/prior/project", "remote": ""}
        inst._write_receipt("demo", receipt)
    elif receipt_kind == "cell":
        receipt = inst._read_receipt("demo")
        receipt["owner"]["marketplace_id"] = "prior-cell"
        receipt["owner"]["install_receipt"] = "prior-installation-receipt"
        receipt["runtime"]["root"] = "/prior/runtime"
        inst._write_receipt("demo", receipt)
    elif receipt_kind == "corrupt":
        receipt_path.write_bytes(b"\xffinvalid prior receipt")
    elif receipt_kind == "unreceipted":
        receipt_path.unlink()
    monkeypatch.setenv("AGENT_WORKTREES_PAYLOAD_ROOT", str(current))
    return foreign


@pytest.mark.parametrize("receipt_kind", ["foreign", "foreign-project", "cell", "corrupt", "unreceipted"])
def test_interrupted_explicit_transfer_recovers_without_retransfer(
    command_home, monkeypatch, receipt_kind
):
    from agent_worktrees import project_command_transaction as tx

    _seed_foreign_transfer(command_home, monkeypatch, receipt_kind)
    paths = [p for p, _ in inst._project_binstub_specs("demo")]
    before = {p: p.read_bytes() for p in paths}
    destination = inst._binstub_owner()
    original = tx._replace
    interrupted = False

    def interrupt_after_first_sibling(path, data, mode, **kwargs):
        nonlocal interrupted
        original(path, data, mode, **kwargs)
        if not interrupted:
            interrupted = True
            raise KeyboardInterrupt("interrupted authorized transfer")

    monkeypatch.setattr(tx, "_replace", interrupt_after_first_sibling)
    with pytest.raises(KeyboardInterrupt):
        inst.transfer_project_binstub("demo")
    monkeypatch.setattr(tx, "_replace", original)
    pending = inst._read_receipt("demo")
    assert pending["owner"] == destination
    assert pending["project"] == inst._project_identity("demo")
    assert pending["runtime"] == pending["transaction"]["receipt"]["runtime"]
    if len(paths) == 2:
        assert paths[0].read_bytes() != before[paths[0]]
        assert paths[1].read_bytes() == before[paths[1]]
    result = inst.reconcile_binstubs()
    assert result["blocked"] == result["preserved"] == []
    receipt = inst._read_receipt("demo")
    assert receipt["owner"] == destination
    assert receipt["project"] == inst._project_identity("demo")
    assert "transaction" not in receipt
    assert all(p.read_bytes() == content.encode("utf-8")
               for p, content in inst._project_binstub_specs("demo"))
    mtimes = {p: p.stat().st_mtime_ns for p in paths + [inst._receipt_path("demo")]}
    assert inst.reconcile_binstubs()["added"] == 0
    assert mtimes == {p: p.stat().st_mtime_ns for p in mtimes}


@pytest.mark.parametrize("interference", ["edit", "foreign-caller"])
def test_interrupted_transfer_still_protects_edited_and_foreign_state(
    command_home, monkeypatch, interference
):
    from agent_worktrees import project_command_transaction as tx

    foreign = _seed_foreign_transfer(command_home, monkeypatch, "foreign")
    paths = [p for p, _ in inst._project_binstub_specs("demo")]
    original = tx._replace

    def interrupt(path, data, mode, **kwargs):
        original(path, data, mode, **kwargs)
        raise KeyboardInterrupt("interrupted authorized transfer")

    monkeypatch.setattr(tx, "_replace", interrupt)
    with pytest.raises(KeyboardInterrupt):
        inst.transfer_project_binstub("demo")
    monkeypatch.setattr(tx, "_replace", original)
    if interference == "edit":
        paths[-1].write_bytes(b"operator edit after interrupted transfer\n")
    else:
        monkeypatch.setenv("AGENT_WORKTREES_PAYLOAD_ROOT", str(foreign))
    before = {p: p.read_bytes() for p in paths}
    receipt_before = inst._receipt_path("demo").read_bytes()
    result = inst.reconcile_binstubs()
    assert result["blocked"] == result["preserved"] == ["demo"]
    assert {p: p.read_bytes() for p in paths} == before
    assert inst._receipt_path("demo").read_bytes() == receipt_before


@pytest.mark.parametrize("receipt_kind", ["foreign", "foreign-project", "cell", "corrupt", "unreceipted"])
def test_failed_explicit_transfer_restores_exact_prior_owner_and_bytes(
    command_home, monkeypatch, receipt_kind
):
    from agent_worktrees import project_command_transaction as tx

    _seed_foreign_transfer(command_home, monkeypatch, receipt_kind)
    paths = [p for p, _ in inst._project_binstub_specs("demo")]
    before = {p: p.read_bytes() for p in paths}
    receipt_path = inst._receipt_path("demo")
    previous_bytes = receipt_path.read_bytes() if receipt_path.exists() else None
    original = tx._replace
    failed = False

    def fail_after_first(path, data, mode, **kwargs):
        nonlocal failed
        original(path, data, mode, **kwargs)
        if not failed:
            failed = True
            raise PermissionError("transfer publication denied")

    monkeypatch.setattr(tx, "_replace", fail_after_first)
    with pytest.raises(PermissionError, match="denied"):
        inst.transfer_project_binstub("demo")
    assert {p: p.read_bytes() for p in paths} == before
    assert (receipt_path.read_bytes() if receipt_path.exists() else None) == previous_bytes


DUPLICATE_PROJECT_DOCUMENTS = [
    "projects:\n  demo: {}\nprojects: {}\n",
    "projects:\n  agent-worktrees: {}\n  demo: {}\n  demo: null\n",
    "projects: {}\nlegacy:\n  anchor: first\n  anchor: second\n",
    "projects:\n  agent-worktrees: {}\n  demo:\n    anchor: first\n    anchor: second\n",
]


@pytest.mark.parametrize("document", DUPLICATE_PROJECT_DOCUMENTS)
@pytest.mark.parametrize("receipted", [True, False])
@pytest.mark.parametrize("reader", ["shared", "secondary-empty"])
def test_duplicate_registry_keys_never_authorize_command_mutation(
    command_home, monkeypatch, document, receipted, reader
):
    before = seed_old(monkeypatch, command_home, receipted=receipted)
    receipt_path = inst._receipt_path("demo")
    receipt_before = receipt_path.read_bytes() if receipt_path.exists() else None
    inst.projects_yaml_path().write_text(document, encoding="utf-8")
    if reader == "shared":
        monkeypatch.setattr(inst, "read_projects_registry", READ_PROJECTS_REGISTRY)
    else:
        monkeypatch.setattr(inst, "read_projects_registry", lambda **_kwargs: {"projects": {}})
    writes = []
    monkeypatch.setattr(inst, "write_projects_registry", lambda *args: writes.append(args))
    with pytest.raises(inst.BinstubOwnershipError, match="duplicate"):
        inst.reconcile_binstubs()
    assert writes == []
    assert {p: p.read_bytes() for p in before} == before
    assert (receipt_path.read_bytes() if receipt_path.exists() else None) == receipt_before


@pytest.mark.parametrize("document", DUPLICATE_PROJECT_DOCUMENTS)
def test_shared_parser_rejects_duplicate_keys_before_migration(
    tmp_path, monkeypatch, document
):
    from agent_worktrees import config_migrations
    from agent_worktrees.projects_registry import parse_projects_registry

    path = tmp_path / "projects.yaml"
    path.write_text(document, encoding="utf-8")
    monkeypatch.setattr(
        config_migrations, "migrate_loaded",
        lambda *args: pytest.fail("duplicate YAML reached migration/defaulting"),
    )
    with pytest.raises(ValueError, match="duplicate"):
        parse_projects_registry(path)


@pytest.mark.parametrize("document", ["projects: !!map []\n", "projects: {demo: !!map []}\n"])
@pytest.mark.parametrize("receipted", [False, True])
@pytest.mark.parametrize("reader", ["shared", "secondary-empty"])
def test_invalid_mapping_tag_never_authorizes_mutation(
    command_home, monkeypatch, document, receipted, reader,
):
    before = seed_old(monkeypatch, command_home, receipted=receipted)
    receipt_path = inst._receipt_path("demo")
    receipt_before = receipt_path.read_bytes() if receipt_path.exists() else None
    inst.projects_yaml_path().write_text(document, encoding="utf-8")
    monkeypatch.setattr(
        inst, "read_projects_registry",
        READ_PROJECTS_REGISTRY if reader == "shared" else lambda **kwargs: {"projects": {}},
    )
    with pytest.raises(inst.BinstubOwnershipError, match="mapping node"):
        inst.reconcile_binstubs()
    assert {path: path.read_bytes() for path in before} == before
    assert (receipt_path.read_bytes() if receipt_path.exists() else None) == receipt_before


def test_duplicate_registry_readonly_view_remains_tolerant(command_home, monkeypatch):
    inst.projects_yaml_path().write_text(DUPLICATE_PROJECT_DOCUMENTS[0], encoding="utf-8")
    assert READ_PROJECTS_REGISTRY() == {"projects": {}}
    with pytest.raises(ValueError, match="duplicate"):
        READ_PROJECTS_REGISTRY(strict=True)


def test_unknown_unreceipted_bytes_are_preserved(command_home, monkeypatch, capsys):
    before = seed_old(monkeypatch, command_home, receipted=False)
    p = next(iter(before))
    p.write_bytes(before[p] + b"\necho extra\n")
    before[p] = p.read_bytes()
    result = inst.reconcile_binstubs()
    assert result["blocked"] == ["demo"]
    assert {p: p.read_bytes() for p in before} == before
    assert "in sync" not in capsys.readouterr().out


def test_native_relocated_command_routes_to_installed_user_binstub(
    command_home, monkeypatch, request
):
    windows = inst.platform.system() == "Windows"
    if windows != (os.name == "nt"):
        pytest.skip("native platform command boundary")
    seed_old(monkeypatch, command_home)
    payload = inst.local_bin()
    if windows:
        native_powershell = request.getfixturevalue("native_powershell")
        (payload / "agent-worktrees.ps1").write_text(
            'Write-Output ("selected:" + ($args -join ","))\nexit 0\n',
            encoding="utf-8")
        argv = [str(native_powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-File", str(inst.local_bin() / "demo.ps1"), "status"]
    else:
        native_bash = request.getfixturevalue("native_bash")
        target = payload / "agent-worktrees"
        target.write_text('#!/usr/bin/env bash\nprintf "selected:%s,%s,%s\\n" "$1" "$2" "$3"\n',
                          encoding="utf-8")
        target.chmod(0o755)
        argv = [str(native_bash), str(inst.local_bin() / "demo"), "status"]
    assert inst.reconcile_binstubs()["blocked"] == []
    result = subprocess.run(argv, capture_output=True, text=True, timeout=15, **inst.no_window_kwargs())
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "selected:--project,demo,status"


@pytest.mark.parametrize("exit_code", [0, 17])
def test_native_cmd_project_binstub_returns_installed_command_exit(
    command_home, monkeypatch, exit_code,
):
    if inst.platform.system() != "Windows" or os.name != "nt":
        pytest.skip("native CMD boundary")
    seed_old(monkeypatch, command_home)
    assert inst.reconcile_binstubs()["blocked"] == []
    command = inst.local_bin() / "agent-worktrees.cmd"
    command.write_text(f"@echo off\n@echo installed:%*\n@exit /b {exit_code}\n", encoding="utf-8")
    caller = inst.local_bin() / "caller.cmd"
    caller.write_text(
        '@echo off\nset "EXPAND_ME=must-not-expand"\nset "INPUT=%%EXPAND_ME%%"\n'
        f'"{inst.local_bin() / "demo.cmd"}" status "two words" "%INPUT%"\n',
        encoding="utf-8",
    )
    result = subprocess.run(
        [os.environ["ComSpec"], "/d", "/c", str(caller)],
        capture_output=True, text=True, timeout=15, **inst.no_window_kwargs(),
    )
    assert result.returncode == exit_code
    assert result.stdout.strip() == 'installed:--project demo status "two words" "%EXPAND_ME%"'
