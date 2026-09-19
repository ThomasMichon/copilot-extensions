"""Payload-gate contracts; disposable runtimes and fake owner installers only."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import types
import venv

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN / "scripts"


def load_verifier():
    spec = importlib.util.spec_from_file_location(
        "bridge_current_payload", SCRIPTS / "current-payload.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def fingerprint(payload, windows):
    entries = []
    for path in [payload / "pyproject.toml", *(payload / "src").rglob("*")]:
        if path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            relative = path.relative_to(payload).as_posix()
            entries.append(f"{relative}:{digest}" if windows else f"{digest}  {relative}")
    data = "\n".join(sorted(entries)) + ("" if windows else "\n")
    return hashlib.sha256(data.encode()).hexdigest()


@pytest.fixture
def installed(tmp_path):
    version = json.loads((PLUGIN / "plugin.json").read_text())["version"]
    payload = tmp_path / "payload"
    payload.mkdir()
    write_json(payload / "plugin.json", {"name": "agent-bridge", "version": version})
    (payload / "pyproject.toml").write_text(f'version = "{version}"\n')
    (payload / "src").mkdir()
    (payload / "src" / "example.py").write_text("value = 1\n")
    root = tmp_path / "runtime"
    slot = root / "versions" / version
    venv.EnvBuilder(with_pip=False).create(slot)
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    site = (
        slot / "Lib" / "site-packages" if os.name == "nt" else
        slot / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    )
    package = site / "agent_bridge"
    package.mkdir()
    (package / "__init__.py").write_text("")
    metadata = site / "agent_bridge.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: agent-bridge\nVersion: {version.replace('-dev', '.dev')}\n"
    )
    write_json(slot / ".install-complete.json", {
        "version": version, "completed_at": "2026-01-01T00:00:00Z", "pid": 1,
        "payload_hash": fingerprint(payload, os.name == "nt"),
    })
    (root / "current-version").write_text(version)
    return types.SimpleNamespace(
        payload=payload, root=root, slot=slot, python=python, site=site, version=version,
    )


def test_verifier_requires_selected_complete_current_runtime(installed):
    item = installed
    argv = [
        str(item.python), "-I", "-B", str(SCRIPTS / "current-payload.py"),
        "--payload", str(item.payload), "--root", str(item.root), "--mode", "legacy",
        "--payload-hash", fingerprint(item.payload, os.name == "nt"),
    ]
    result = subprocess.run(argv, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["status"] == "ready"
    assert receipt["serviceChecked"] is False
    assert receipt["currentPayload"] is True
    assert receipt["runtimeVersion"] == item.version
    before = (item.slot / ".install-complete.json").read_bytes()
    # Valid old slots and fallback selections are not current-payload readiness.
    (item.root / "current-version").write_text("0.0.1")
    result = subprocess.run(argv, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0 and not result.stdout
    assert "current marker" in result.stderr
    (item.root / "current-version").write_text(item.version)
    result = subprocess.run(
        [*argv[:-1], "0" * 64], capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0 and not result.stdout
    assert "fingerprint" in result.stderr
    # Matching markers cannot mask a foreign/editable import.
    (item.site / "agent_bridge" / "__init__.py").unlink()
    result = subprocess.run(argv, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0 and "outside" in result.stderr
    assert (item.slot / ".install-complete.json").read_bytes() == before


def test_posix_gate_resolver_preserves_stale_marker_fallback(installed):
    if os.name == "nt":
        bash = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git/bin/bash.exe"
        if not bash.is_file():
            pytest.skip("Git Bash unavailable")
    else:
        bash = shutil.which("bash")
        if not bash:
            pytest.skip("Bash unavailable")
    gate = (SCRIPTS / "runtime-gate.sh").read_text()
    function = "resolve_runtime() {" + gate.split("resolve_runtime() {", 1)[1].split(
        "\n}\n", 1,
    )[0] + "\n}\n"
    (installed.root / "current-version").write_text("0.0.1")
    result = subprocess.run(
        [str(bash), "--noprofile", "--norc"],
        input='set -euo pipefail\n' + function + '\nresolve_runtime\nprintf "%s" "$AGENT_RT_PY"\n',
        text=True, capture_output=True, timeout=10,
        env={
            **os.environ, "RUNTIME_ROOT": installed.root.as_posix(),
            "RUNTIME_RESOLVER": (SCRIPTS / "resolve-runtime.sh").as_posix(),
        },
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == installed.python.as_posix()


def test_namespace_verifier_delegates_receipt_proof_without_mutation(
    installed, monkeypatch, tmp_path,
):
    verifier = load_verifier()
    item = installed
    context = tmp_path / "cells" / "marketplaces" / "example" / "plugins" / "agent-bridge" / "install.json"
    context.parent.mkdir(parents=True)
    write_json(context, {})
    write_json(item.slot / ".runtime-slot-ownership.json", {"snapshot": {"id": "snapshot"}})
    calls = []

    def validate_context(*args, **kwargs):
        calls.append(("context", args, kwargs))
        return {"generation": 7}

    def validate_completion(**kwargs):
        calls.append(("completion", (), kwargs))
        return {"slotRoot": str(item.slot), "payloadSha256": "snapshot-digest"}

    fake = types.SimpleNamespace(
        validate_context_receipt=validate_context,
        validate_runtime_slot_completion=validate_completion,
        read_json=lambda path: json.loads(path.read_text()),
        RUNTIME_SLOT_OWNERSHIP_FILE=".runtime-slot-ownership.json",
        _snapshot_content_sha256=lambda path: "snapshot-digest",
    )
    monkeypatch.setitem(sys.modules, "installation_context", fake)
    monkeypatch.setattr(sys, "prefix", str(item.slot))
    monkeypatch.setattr(verifier.importlib.metadata, "distribution", lambda name: types.SimpleNamespace(
        version=item.version, locate_file=lambda value: item.site,
    ))
    monkeypatch.setattr(verifier.importlib.util, "find_spec", lambda name: types.SimpleNamespace(
        origin=str(item.site / "agent_bridge" / "__init__.py"),
    ))
    receipt = verifier.verify(
        item.payload, item.root, "namespaced", "", str(context), "7", "example", "unchanged",
    )
    assert receipt["mode"] == "namespaced" and receipt["action"] == "unchanged"
    assert calls[1][2]["expected_payload_root"] == item.payload
    assert calls[1][2]["expected_payload_version"] == item.version
    assert calls[1][2]["snapshot_id"] == "snapshot"
    with pytest.raises(ValueError, match="changed during convergence"):
        verifier.verify(
            item.payload, item.root, "namespaced", "", str(context), "8", "example", "unchanged",
        )
    fake._snapshot_content_sha256 = lambda path: "different"
    with pytest.raises(ValueError, match="snapshot differs"):
        verifier.verify(
            item.payload, item.root, "namespaced", "", str(context), "7", "example", "unchanged",
        )


def prepare_gate(item, tmp_path):
    scripts = item.payload / "scripts"
    scripts.mkdir()
    for name in (
        "runtime-gate.sh", "runtime-gate.ps1", "resolve-runtime.sh", "resolve-runtime.ps1",
        "current-payload.py", "versioned_runtime.py", "payload-hash.sh", "payload-hash.ps1",
    ):
        shutil.copy2(SCRIPTS / name, scripts / name)
    runner = scripts / "installation-context"
    runner.mkdir()
    shutil.copy2(SCRIPTS / "installation-context" / "json-query.awk", runner)
    config = tmp_path / "admission.json"
    write_json(config, {
        "status": "ready", "actualMode": "legacy", "desiredMode": "legacy",
        "allowMutation": True, "probeReason": "legacy-active", "reason": "legacy-active",
        "runtimeRoot": str(item.root), "installGeneration": 1,
    })
    # Process-boundary doubles: no installer, service, user profile, or registry effects.
    (runner / "installation-context.sh").write_text(
        '#!/bin/bash\ncat "$TEST_ADMISSION"\n', encoding="utf-8",
    )
    (runner / "installation-context.ps1").write_text(
        '[Console]::Out.Write([IO.File]::ReadAllText($env:TEST_ADMISSION))\n',
        encoding="utf-8",
    )
    (scripts / "install.sh").write_text(
        '#!/bin/bash\nprintf "%s\\n" "$@" > "$TEST_INSTALL_LOG"\n'
        'if [[ "$TEST_INSTALL_MODE" == fail ]]; then exit 9; fi\n'
        'if [[ "$TEST_INSTALL_MODE" == noop ]]; then exit 0; fi\n'
        'printf "%s" "$TEST_VERSION" > "$AGENT_BRIDGE_INSTALL_DIR/current-version"\n',
        encoding="utf-8",
    )
    (scripts / "install.ps1").write_text(
        '$args | Set-Content -LiteralPath $env:TEST_INSTALL_LOG\n'
        "if ($env:TEST_INSTALL_MODE -eq 'fail') { exit 9 }\n"
        "if ($env:TEST_INSTALL_MODE -eq 'noop') { exit 0 }\n"
        "[IO.File]::WriteAllText((Join-Path $env:AGENT_BRIDGE_INSTALL_DIR 'current-version'), $env:TEST_VERSION)\n",
        encoding="utf-8",
    )
    bin_dir = item.payload / "bin"
    bin_dir.mkdir()
    for name in ("agent-bridge", "agent-bridge.cmd", "agent-bridge.ps1"):
        shutil.copy2(PLUGIN / "bin" / name, bin_dir / name)
    env = {
        **os.environ, "AGENT_BRIDGE_INSTALL_DIR": str(item.root),
        "TEST_ADMISSION": str(config), "TEST_VERSION": item.version,
        "TEST_INSTALL_LOG": str(tmp_path / "installer.log"), "TEST_INSTALL_MODE": "update",
    }
    for name in ("COPILOT_EXTENSIONS_CONTEXT", "AGENT_BRIDGE_NO_SELFPROVISION"):
        env.pop(name, None)
    command = (
        [os.environ["COMSPEC"], "/d", "/c", str(bin_dir / "agent-bridge.cmd")]
        if os.name == "nt" else [str(bin_dir / "agent-bridge")]
    )
    return env, command, config


def run_gate(command, env, *args):
    return subprocess.run(
        [*command, *(args or ("provision", "--current-payload", "--json"))],
        input="", env=env, capture_output=True, text=True, timeout=25,
    )


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes()
            for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts}


def test_payload_gate_legacy_current_stale_and_failed_owner_update(installed, tmp_path):
    item = installed
    env, command, _ = prepare_gate(item, tmp_path)
    before = snapshot(item.root)
    result = run_gate(command, env)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["action"] == "unchanged"
    assert snapshot(item.root) == before
    assert not Path(env["TEST_INSTALL_LOG"]).exists()

    old_slot = item.root / "versions" / "0.0.1"
    shutil.copytree(item.slot, old_slot, symlinks=True)
    old_marker = json.loads((old_slot / ".install-complete.json").read_text())
    old_marker["version"] = "0.0.1"
    write_json(old_slot / ".install-complete.json", old_marker)
    for installer_mode in ("fail", "noop", "update"):
        (item.root / "current-version").write_text("0.0.1")
        env["TEST_INSTALL_MODE"] = installer_mode
        result = run_gate(command, env)
        assert Path(env["TEST_INSTALL_LOG"]).exists(), (
            result.returncode, result.stdout, result.stderr,
        )
        log = Path(env["TEST_INSTALL_LOG"]).read_text(encoding="utf-8-sig")
        assert log.splitlines() == [
            "update", "-InstallDir" if os.name == "nt" else "--install-dir", str(item.root),
        ]
        if installer_mode == "update":
            assert result.returncode == 0, result.stderr
            receipt = json.loads(result.stdout)
            assert receipt["action"] == "updated"
            assert receipt["runtimeRoot"] == str(item.root)
        else:
            assert result.returncode != 0 and not result.stdout
            assert (item.root / "current-version").read_text() == "0.0.1"


@pytest.mark.parametrize("case", ["stale", "missing", "deactivating", "ambiguous", "denied"])
def test_payload_gate_namespace_and_authorization_refusals_are_effect_free(
    installed, tmp_path, case,
):
    item = installed
    env, command, config = prepare_gate(item, tmp_path)
    decision = json.loads(config.read_text())
    if case == "denied":
        decision.update(allowMutation=False, probeReason="legacy-owned-by-other-cell")
    elif case == "ambiguous":
        decision.update(status="provenance-blocked", reason="ambiguous")
    else:
        decision.update(
            actualMode="namespaced", desiredMode="namespaced", reason="namespaced-active",
            context=str(tmp_path / "cells/marketplaces/example/plugins/agent-bridge/install.json"),
            marketplaceId="example", generation=1,
        )
        if case == "deactivating":
            decision.update(status="deactivation-required", desiredMode="legacy")
        elif case == "missing":
            decision["runtimeRoot"] = str(tmp_path / "absent-cell")
        else:
            (item.root / "current-version").write_text("0.0.1")
    write_json(config, decision)
    before = snapshot(item.root)
    result = run_gate(command, env)
    assert result.returncode != 0 and not result.stdout, result
    assert snapshot(item.root) == before
    assert not Path(env["TEST_INSTALL_LOG"]).exists()
    assert not (tmp_path / "absent-cell").exists()


def test_payload_gate_rejects_invalid_explicit_arguments_before_resolution(installed, tmp_path):
    env, command, config = prepare_gate(installed, tmp_path)
    config.unlink()
    before = snapshot(installed.root)
    result = run_gate(command, env, "provision", "--current-payload")
    assert result.returncode == 2 and "usage:" in result.stderr
    assert snapshot(installed.root) == before


def test_payload_gate_real_legacy_governance_and_no_selfprovision(installed, tmp_path):
    env, command, _ = prepare_gate(installed, tmp_path)
    runner = installed.payload / "scripts" / "installation-context"
    for name in ("installation-context.sh", "installation-context.ps1"):
        shutil.copy2(SCRIPTS / "installation-context" / name, runner / name)
    # The test containment runner supplies empty user/Copilot policy roots.
    result = run_gate(command, env)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["action"] == "unchanged"
    (installed.root / "current-version").write_text("0.0.1")
    env["AGENT_BRIDGE_NO_SELFPROVISION"] = "1"
    result = run_gate(command, env)
    assert result.returncode != 0 and not result.stdout
    assert "NO_SELFPROVISION" in result.stderr
    assert not Path(env["TEST_INSTALL_LOG"]).exists()


def test_payload_gate_validates_real_namespace_completion_read_only(
    installed, tmp_path, monkeypatch,
):
    item = installed
    env, command, config = prepare_gate(item, tmp_path)
    helper = SCRIPTS / "installation-context" / "installation_context.py"
    shutil.copy2(helper, item.payload / "scripts" / "installation-context")
    spec = importlib.util.spec_from_file_location("convergence_context_fixture", helper)
    ic = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, ic)
    spec.loader.exec_module(ic)
    vector = json.loads((
        PLUGIN.parents[1] / "libs" / "installation-context" / "fixtures" / "source-identities.json"
    ).read_text())["vectors"][0]
    marketplace = vector["marketplaceId"]
    source = {**vector["normalized"], "fingerprint": "sha256:" + vector["sha256"]}
    durable = tmp_path / "cells"
    cell = durable / "marketplaces" / marketplace
    root = cell / "plugins" / "agent-bridge"
    root.mkdir(parents=True)
    context = root / "install.json"
    write_json(cell / "namespace.json", {
        "schema": "copilot-extensions.marketplace-namespace", "version": 1,
        "marketplaceId": marketplace, "source": source, "locators": [],
        "generation": 1, "state": "active",
    })
    write_json(context, {
        "schema": "copilot-extensions.plugin-installation", "version": 1,
        "marketplaceId": marketplace, "pluginId": "agent-bridge",
        "pluginRoot": str(root), "namespaceReceipt": str(cell / "namespace.json"),
        "payload": {"root": str(item.payload), "version": item.version, "origin": "explicit"},
        "roots": {name: name for name in (
            "versions", "snapshots", "state", "run", "logs", "cache", "launchers",
        )},
        "generation": 2, "state": "active",
    })
    snapshot_root = root / "snapshots" / item.version
    shutil.copytree(item.payload, snapshot_root)
    common = dict(
        context=context, expected_marketplace_id=marketplace,
        expected_plugin_id="agent-bridge", snapshot_id=item.version,
        durable_home=durable, environment={},
    )
    ic.stamp_snapshot_provenance(
        **common, expected_namespace_generation=1, expected_install_generation=2,
    )
    slot_args = dict(
        **common, runtime_version=item.version, expected_payload_root=item.payload,
        expected_payload_version=item.version,
    )
    ic.provision_runtime_slot(**slot_args)
    slot = root / "versions" / item.version
    shutil.copytree(item.slot, slot, dirs_exist_ok=True, symlinks=True)
    marker = json.loads((slot / ".install-complete.json").read_text())
    marker["payload_hash"] = ic._snapshot_content_sha256(snapshot_root)
    write_json(slot / ".install-complete.json", marker)
    ic.complete_runtime_slot(**slot_args)
    (root / "current-version").write_text(item.version)
    write_json(config, {
        "status": "ready", "reason": "namespaced-active",
        "actualMode": "namespaced", "desiredMode": "namespaced",
        "runtimeRoot": str(root), "context": str(context),
        "marketplaceId": marketplace, "generation": 2, "installGeneration": 2,
    })
    before = snapshot(root)
    legacy_before = snapshot(item.root)
    result = run_gate(command, env)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["mode"] == "namespaced" and receipt["action"] == "unchanged"
    assert receipt["runtimeRoot"] == str(root)
    assert snapshot(root) == before and snapshot(item.root) == legacy_before
    assert not Path(env["TEST_INSTALL_LOG"]).exists()
    # Matching version plus valid old completion cannot hide same-version payload drift.
    (item.payload / "src" / "example.py").write_text("value = 2\n")
    result = run_gate(command, env)
    assert result.returncode != 0 and not result.stdout
    assert "snapshot differs" in result.stderr
    assert snapshot(root) == before and snapshot(item.root) == legacy_before
