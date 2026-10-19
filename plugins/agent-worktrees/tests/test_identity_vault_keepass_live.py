"""Opt-in combined CLI proof against a disposable, PID-owned real vault."""

from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("AGENT_WORKTREES_IDENTITY_REAL_KEEPASS") != "1",
    reason="opt-in combined disposable KeePass proof",
)


def _fixture_module():
    path = Path(__file__).resolve().parents[2] / "agent-vault" / "tests" / "test_identity_key_keepass_live.py"
    spec = importlib.util.spec_from_file_location("_pr_identity_disposable_vault", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _gate_module():
    path = Path(__file__).resolve().parents[2] / "agent-vault" / "tests" / "test_identity_payload_gate.py"
    spec = importlib.util.spec_from_file_location("_identity_vault_payload_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _worktrees(fixture, *args: str, env=None, input=None):
    selected = dict(env or fixture.env)
    selected["AGENT_HOME"] = str(fixture.root / "worktrees-home")
    selected.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    return subprocess.run(
        [sys.executable, "-m", "agent_worktrees", *args],
        env=selected, cwd=fixture.root, input=input,
        capture_output=True, text=True, timeout=25,
    )


def _files(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_real_configured_owner_setup_status_and_locked_fresh_consumer(tmp_path):
    helper = _fixture_module()
    with helper.disposable_vault(tmp_path / "fixture") as fixture:
        argv = json.dumps(fixture.argv)
        before = _files(fixture.root)
        status = _worktrees(fixture, "identity-vault", "status", "--vault-argv", argv, "--json")
        helper.require(status.returncode == 2, "Fresh unpopulated identity unexpectedly ready")
        report = json.loads(status.stdout)
        helper.require(report["database"] is True and report["cache"] is False,
                       "Fresh source readiness fields are not independent")
        helper.require(_files(fixture.root) == before, "Read-only status mutated fixture files")
        setup = _worktrees(fixture, "identity-vault", "setup", "--vault-argv", argv, "--json")
        helper.require(setup.returncode == 0, "Combined configured-owner setup failed")
        helper.require(json.loads(setup.stdout) == {
            "version": 1, "configured": True, "ready": True, "key": "reused",
        }, "Combined setup did not reuse the existing owner key")
        helper.require(fixture.expected_key not in setup.stdout + setup.stderr,
                       "Combined setup exposed fixture key material")
        helper.require(Path(fixture.database).read_bytes() == before["disposable.kdbx"],
                       "Ordinary setup changed authoritative database contents")
        source_path = fixture.root / "worktrees-home" / ".agent-worktrees" / "identity-vault.json"
        helper.require(source_path.is_file(), "Combined setup did not persist local source configuration")
        source = json.loads(source_path.read_text())
        helper.require(fixture.expected_key not in source_path.read_text(),
                       "Source configuration contains fixture key material")
        helper.require(source["argv"] == fixture.argv, "Setup changed the selected vault invocation")
        helper.require(fixture.request({"action": "lock", "kpdb": fixture.database}).get("ok") is True,
                       "Could not lock the disposable daemon")
        conflicting = dict(fixture.env, KPDB="wrong-database.kdbx", VAULT_GROUP="Wrong",
                           AGENT_VAULT="wrong-vault", AGENT_VAULT_CACHE_DIR="wrong-cache")
        verify = _worktrees(fixture, "identity-vault", "verify", "--json", env=conflicting)
        helper.require(verify.returncode == 0, "Fresh normal consumer did not use persisted pinned source")
        helper.require(json.loads(verify.stdout) == {
            "version": 1, "ready": True, "cache_only": True, "roundtrip": True,
        }, "Fresh normal reader did not prove its encryption roundtrip")
        helper.require(fixture.request({"action": "ping", "kpdb": fixture.database}).get("cli") == "locked",
                       "Normal verification unlocked the disposable daemon")


def test_real_missing_owner_refuses_then_explicit_initialization_reuses(tmp_path):
    helper = _fixture_module()
    with helper.disposable_vault(tmp_path / "fixture", seed_owner=False) as fixture:
        argv = json.dumps(fixture.argv)
        common = ["identity-vault", "setup", "--vault-argv", argv, "--json"]
        missing = _worktrees(fixture, *common)
        helper.require(missing.returncode == 1, "Ordinary setup initialized a missing owner")
        helper.require(json.loads(missing.stderr).get("error") == "entry_missing",
                       "Missing owner did not return a safe actionable reason")
        source_path = fixture.root / "worktrees-home" / ".agent-worktrees" / "identity-vault.json"
        helper.require(not source_path.exists(), "Failed setup published source configuration")
        created = _worktrees(fixture, *common, "--initialize-owner", "--confirm-first-owner")
        helper.require(created.returncode == 0 and json.loads(created.stdout).get("key") == "created",
                       "Explicit first-owner initialization failed through the combined CLI")
        authoritative = Path(fixture.database).read_bytes()
        repeated = _worktrees(fixture, "identity-vault", "setup", "--json")
        helper.require(repeated.returncode == 0 and json.loads(repeated.stdout).get("key") == "reused",
                       "Repeat combined setup did not reuse the owner")
        helper.require(Path(fixture.database).read_bytes() == authoritative,
                       "Repeat setup rewrote the authoritative database")


def test_stopped_service_is_not_activated_inside_contained_setup(tmp_path):
    helper = _fixture_module()
    with helper.disposable_vault(tmp_path / "fixture") as fixture:
        helper.require(fixture.request({"action": "stop"}).get("ok") is True,
                       "Could not stop the owned fixture daemon")
        fixture.process.wait(timeout=5)
        before = _files(fixture.root)
        setup = _worktrees(
            fixture, "identity-vault", "setup", "--vault-argv", json.dumps(fixture.argv), "--json",
        )
        helper.require(setup.returncode == 1, "Contained setup unexpectedly activated a stopped service")
        helper.require(json.loads(setup.stderr).get("error") == "service_unavailable",
                       "Stopped service did not require independent activation")
        helper.require(_files(fixture.root) == before, "Contained setup started or mutated a stopped service")


@pytest.mark.parametrize("mode", ["legacy", "namespaced"])
def test_worktrees_context_crosses_actual_vault_payload_without_foreign_receipt(tmp_path, mode):
    from test_registry_paths import _payload, _stamp

    helper = _fixture_module()
    gate = _gate_module()
    home, vault_payload, runtime, installation, vault_context = gate.prepare_payload(tmp_path, mode)
    gate.prepare_runtime(runtime, vault_payload)
    aw_payload = _payload(tmp_path, "worktrees-payload")
    aw_context, aw_root = _stamp(
        tmp_path / "worktrees-durable", aw_payload,
        marketplace="example", repository="Example/Plugins",
    )
    suffix = hashlib.sha256(
        str(runtime).replace("\\", "/").lower().rstrip("/").encode(),
    ).hexdigest()[:12]
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
    gate.write_json(tmp_path / "vault-config.json", {"port": port})
    updates = {
        "HOME": str(home), "USERPROFILE": str(home),
        "COPILOT_PLUGIN_ROOT": str(vault_payload), "COPILOT_EXTENSIONS_CONTEXT": vault_context,
        "AGENT_VAULT_HOME": str(runtime), "AGENT_VAULT_INSTALLATION_ID": installation,
        "AGENT_VAULT_RUN_DIR": str(runtime / "run"),
        "AGENT_VAULT_CACHE_DIR": str(runtime / "cache"),
        "AGENT_VAULT_CONFIG": str(tmp_path / "vault-config.json"),
        "AGENT_VAULT_PORT": "0" if installation else str(port),
        "AGENT_VAULT_PIPE": rf"\\.\pipe\agent-vault-{suffix}",
        "AGENT_VAULT_SOCKET": str(tmp_path / "vault.sock"),
        "AGENT_VAULT_PID": str(runtime / "run" / "agent-vault-service.pid"),
        "AGENT_VAULT_LOG": str(runtime / "logs" / "agent-vault-service.log"),
    }
    with helper.disposable_vault(tmp_path, runtime_environment=updates) as fixture:
        caller = dict(
            fixture.env,
            COPILOT_EXTENSIONS_CONTEXT=str(aw_context),
            AGENT_WORKTREES_PAYLOAD_ROOT=str(aw_payload),
            COPILOT_PLUGIN_ROOT=str(aw_payload),
        )
        command = [
            sys.executable, "-m", "agent_worktrees", "identity-vault", "setup",
            "--vault-argv", json.dumps(gate.launcher(vault_payload)), "--json",
        ]
        if vault_context:
            command.extend(["--vault-context", vault_context])
        result = subprocess.run(command, env=caller, cwd=tmp_path, capture_output=True, text=True, timeout=125)
        helper.require(result.returncode == 0, "AW-to-payload setup failed: " + result.stderr)
        selected_path = aw_root / "identity-vault.json"
        helper.require(selected_path.is_file(), "Setup lost its owning worktrees installation")
        selected = json.loads(selected_path.read_text())
        helper.require(selected["env"]["COPILOT_EXTENSIONS_CONTEXT"] == vault_context,
                       "Source stored a foreign installation receipt")
        helper.require(Path(selected["env"]["AGENT_VAULT_CACHE_DIR"]).is_relative_to(runtime / "cache" / "identity"),
                       "Payload discarded the scoped identity cache")
        before_status = _files(runtime)
        status = subprocess.run(
            [sys.executable, "-m", "agent_worktrees", "identity-vault", "status", "--json"],
            env=caller, cwd=tmp_path, capture_output=True, text=True, timeout=60,
        )
        helper.require(status.returncode == 0, "Configured payload status failed")
        helper.require(_files(runtime) == before_status, "Configured status mutated vault runtime state")
        fixture.request({"action": "lock", "kpdb": fixture.database})
        verified = subprocess.run(
            [sys.executable, "-m", "agent_worktrees", "identity-vault", "verify", "--json"],
            env=caller, cwd=tmp_path, capture_output=True, text=True, timeout=60,
        )
        helper.require(verified.returncode == 0, "Persisted normal reader failed across payload boundary")
        helper.require(fixture.process.poll() is None, "Contained caller retired an independent resident")


@pytest.mark.parametrize("mode", ["legacy", "namespaced"])
@pytest.mark.parametrize("verb", ["status", "setup"])
def test_cold_payload_discovery_never_provisions_or_activates(tmp_path, mode, verb):
    gate = _gate_module()
    home, payload, runtime, _installation, context = gate.prepare_payload(tmp_path, mode)
    env = {
        name: value for name, value in os.environ.items()
        if not name.startswith(("AGENT_VAULT", "VAULT_", "IDENTITY_", "COPILOT_EXTENSIONS_"))
        and name not in ("KPDB", "COPILOT_PLUGIN_ROOT")
    }
    env.update(HOME=str(home), USERPROFILE=str(home), AGENT_HOME=str(tmp_path / "aw-home"))
    command = [
        sys.executable, "-m", "agent_worktrees", "identity-vault", verb,
        "--vault-argv", json.dumps(gate.launcher(payload)), "--json",
    ]
    if context:
        command.extend(["--vault-context", context])
    # Native PowerShell may create its own OS startup cache. Snapshot every
    # product-owned state root, not the shell's unrelated profile cache.
    state_roots = (home / ".copilot", home / ".copilot-extensions", runtime, tmp_path / "aw-home")
    before = [_files(root) for root in state_roots]
    result = subprocess.run(command, env=env, cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert result.returncode == 1
    assert json.loads(result.stderr)["ready"] is False
    assert not (runtime / "versions").exists()
    assert not (runtime / ".provision-status").exists()
    assert [_files(root) for root in state_roots] == before
