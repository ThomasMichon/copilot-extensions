"""``peer_environment``'s per-plugin forwarding allowlists."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "libs" / "configuration-layers" / "src"))
from configuration_context.readiness import (  # noqa: E402
    CONTRACT_FILE, PROBE_CONTRACT, PROBE_ENV, runtime_contract_path,
    ProbeContractError, unqualified_output,
)

_spec = importlib.util.spec_from_file_location(
    "peer_launch", ROOT / "libs" / "peer-launch" / "peer_launch.py"
)
assert _spec is not None and _spec.loader is not None
peer_launch = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(peer_launch)


def _context(plugin: str) -> dict[str, str]:
    return {
        "pluginId": plugin,
        "installReceipt": "receipt",
        "payloadRoot": "/payload",
        "pluginRoot": "/plugin",
        "stateRoot": "/plugin/state",
    }


def test_agent_dispatch_forwards_control_token_command() -> None:
    """A peer-launched agent-dispatch client must receive the on-demand
    control-token fetch command, not just the raw token -- otherwise a
    deployment that configures only the command (never the raw secret)
    silently produces an unauthenticated peer-launched client."""
    inherited = {
        "AGENT_DISPATCH_CONTROL_TOKEN_COMMAND": "vault get control-token",
        "AGENT_DISPATCH_SHARED_CONTROL_TOKEN_COMMAND": "vault get shared-control-token",
    }
    env = peer_launch.peer_environment(_context("agent-dispatch"), inherited)
    assert env["AGENT_DISPATCH_CONTROL_TOKEN_COMMAND"] == "vault get control-token"
    assert (
        env["AGENT_DISPATCH_SHARED_CONTROL_TOKEN_COMMAND"]
        == "vault get shared-control-token"
    )


def test_agent_dispatch_forwards_raw_control_token() -> None:
    inherited = {"AGENT_DISPATCH_CONTROL_TOKEN": "raw-control-tok"}
    env = peer_launch.peer_environment(_context("agent-dispatch"), inherited)
    assert env["AGENT_DISPATCH_CONTROL_TOKEN"] == "raw-control-tok"


def test_agent_dispatch_omits_unset_control_token_keys() -> None:
    env = peer_launch.peer_environment(_context("agent-dispatch"), {})
    assert "AGENT_DISPATCH_CONTROL_TOKEN" not in env
    assert "AGENT_DISPATCH_CONTROL_TOKEN_COMMAND" not in env


def test_pr_owner_roster_and_state_rebinding() -> None:
    assert peer_launch.PEERS["agent-pull-requests"] == "agent_pull_requests"
    assert "agent-pull-requests" in peer_launch.OWNERS
    env = peer_launch.peer_environment(_context("agent-pull-requests"), {
        "AGENT_PULL_REQUESTS_HOME": "/foreign/state",
        "AGENT_PULL_REQUESTS_PAYLOAD_ROOT": "/foreign/payload",
        "AGENT_PULL_REQUESTS_UNRELATED_OVERRIDE": "foreign",
    })
    assert env["AGENT_PULL_REQUESTS_HOME"] == "/plugin/state"
    assert env["AGENT_PULL_REQUESTS_PAYLOAD_ROOT"] == "/payload"
    assert "AGENT_PULL_REQUESTS_UNRELATED_OVERRIDE" not in env


def test_other_peers_never_inherit_pr_owner_state() -> None:
    env = peer_launch.peer_environment(_context("agent-worktrees"), {
        "AGENT_PULL_REQUESTS_HOME": "/foreign/state",
        "AGENT_PULL_REQUESTS_UNRELATED_OVERRIDE": "foreign",
    })
    assert not any(name.startswith("AGENT_PULL_REQUESTS_") for name in env)


def test_worktrees_read_does_not_inherit_mcp_or_vault_configuration() -> None:
    env = peer_launch.peer_environment(_context("agent-worktrees"), {
        "AGENT_MCP_PLUGIN_ROOTS": "/caller/payload",
        "AGENT_MCP_SERVER": "caller-server",
        "AGENT_VAULT_PORT": "9999",
        "AGENT_VAULT_ENDPOINT": "caller-endpoint",
        "AGENT_CONFIGURATION_CONTEXT": "ordinary-source-projection",
        "HOME": "/user",
    })
    assert not any(name.startswith(("AGENT_MCP_", "AGENT_VAULT_")) for name in env)
    assert env["AGENT_CONFIGURATION_CONTEXT"] == "ordinary-source-projection"
    assert env["HOME"] == "/user"


def test_machines_peer_rebinds_its_owner_without_foreign_runtime_overrides() -> None:
    assert peer_launch.PEERS["agent-machines"] == "agent_machines"
    env = peer_launch.peer_environment(_context("agent-machines"), {
        "AGENT_MACHINES_PAYLOAD_ROOT": "/foreign",
        "AGENT_RT_ROOT": "/other",
        "AGENT_WORKTREES_PAYLOAD_ROOT": "/caller",
        "HOME": "/user",
    })
    assert env["AGENT_RT_ROOT"] == "/plugin"
    assert env["AGENT_MACHINES_PAYLOAD_ROOT"] == "/payload"
    assert env["HOME"] == "/user"
    assert "AGENT_WORKTREES_PAYLOAD_ROOT" not in env


def test_agent_dispatch_retains_authority_and_separate_shared_control() -> None:
    inherited = {
        "AGENT_DISPATCH_TASK_AUTHORITY": "central",
        "AGENT_DISPATCH_SHARED_CONTROL_TOKEN": "shared-control",
    }
    env = peer_launch.peer_environment(_context("agent-dispatch"), inherited)
    assert env["AGENT_DISPATCH_TASK_AUTHORITY"] == "central"
    assert env["AGENT_DISPATCH_SHARED_CONTROL_TOKEN"] == "shared-control"
    other = peer_launch.peer_environment(_context("agent-worktrees"), inherited)
    assert "AGENT_DISPATCH_TASK_AUTHORITY" not in other
    assert "AGENT_DISPATCH_SHARED_CONTROL_TOKEN" not in other


def test_vault_rebinds_native_owner_without_caller_credential_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(peer_launch, "_vault_service_suffix", lambda _root: "f" * 12)
    root = tmp_path / "Vault Owner"
    context = {
        **_context("agent-vault"), "pluginRoot": str(root),
        "marketplaceId": "example-cell",
    }
    inherited = {
        "AGENT_VAULT_HOME": "/foreign",
        "AGENT_VAULT_ENDPOINT": "caller-endpoint",
        "AGENT_VAULT_PINNED_CONTEXT": "caller-cache",
        "AGENT_VAULT_CONFIG": "/foreign/config",
        "_AGENT_WORKTREES_VAULT_RUNTIME_GUARD": "1",
        "KPDB": "/foreign/database",
        "VAULT_GROUP": "foreign-group",
        "AGENT_VAULT": "foreign-vault",
        "HOME": "/user",
    }
    env = peer_launch.peer_environment(context, inherited)
    suffix = env["AGENT_VAULT_PIPE"].removeprefix(r"\\.\pipe\agent-vault-")
    assert suffix == "f" * 12
    assert env["AGENT_VAULT_HOME"] == str(root)
    assert env["AGENT_VAULT_VALIDATED_CONTEXT"] == context["installReceipt"]
    assert env["AGENT_VAULT_INSTALLATION_ID"] == "example-cell/agent-vault"
    assert env["AGENT_VAULT_RUN_DIR"] == str(root / "run")
    assert env["AGENT_VAULT_CORE_RUN_DIR"] == str(root / "core")
    assert env["AGENT_VAULT_CACHE_DIR"] == str(root / "cache")
    assert env["AGENT_VAULT_SOCKET"] == str(root / "run" / "agent-vault.sock")
    assert env["AGENT_VAULT_PIPE"] == rf"\\.\pipe\agent-vault-{suffix}"
    assert env["AGENT_VAULT_PID"] == str(root / "run" / "agent-vault-service.pid")
    assert env["AGENT_VAULT_LOG"] == str(root / "logs" / "agent-vault-service.log")
    assert env["AGENT_VAULT_PORT"] == "0"
    assert env["AGENT_VAULT_SYSTEMD_UNIT"] == f"agent-vault-{suffix}.service"
    assert env["AGENT_VAULT_TASK_NAME"] == f"AgentVault-{suffix}"
    assert env["AGENT_VAULT_NO_SELFPROVISION"] == "1"
    assert env["HOME"] == "/user"
    assert not set(inherited).difference({"AGENT_VAULT_HOME", "HOME"}) & env.keys()
    assert not root.exists()


def test_vault_suffix_refuses_unsupported_posix_checksum_without_a_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(peer_launch.os, "name", "posix")
    monkeypatch.setattr(peer_launch.sys, "platform", "linux")
    monkeypatch.setattr(peer_launch.shutil, "which", lambda _name: None)
    with pytest.raises(ValueError, match="SHA-256 suffix support"):
        peer_launch._vault_service_suffix("/example/Vault")


def test_vault_peer_refuses_unqualified_native_platform_without_owner_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(peer_launch.os, "name", "posix")
    monkeypatch.setattr(peer_launch.sys, "platform", "darwin")
    monkeypatch.setattr(peer_launch.shutil, "which", lambda _name: pytest.fail("unqualified owner lookup"))
    with pytest.raises(ValueError, match="native Windows and Linux only"):
        peer_launch._vault_service_suffix("/example/Vault")


@pytest.mark.parametrize("allow_native", [False, True])
@pytest.mark.parametrize("qualification", ["payload-missing", "runtime-missing", "qualified", "ordinary"])
def test_probe_admits_peer_payload_before_import_and_exact_runtime_before_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, allow_native: bool, qualification: str,
) -> None:
    own_root = tmp_path / "marketplaces" / "cell" / "plugins" / "agent-machines"
    payload = tmp_path / "peer-payload"
    scripts = payload / "scripts"
    primitive = scripts / "installation-context" / "installation_context.py"
    primitive.parent.mkdir(parents=True)
    imported = tmp_path / "peer-code-imported"
    primitive.write_text(
        f"from pathlib import Path\nPath({str(imported)!r}).touch()\n", encoding="utf-8",
    )
    root = own_root.with_name("agent-worktrees")
    python = root / "versions" / "selected" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.touch()
    (scripts / "versioned_runtime.py").write_text(
        f"from pathlib import Path\ndef resolve_python(root):\n    return Path({str(python)!r})\n",
        encoding="utf-8",
    )
    own = {"cellRoot": str(own_root.parent.parent)}
    peer = {
        **_context("agent-worktrees"), "payloadRoot": str(payload),
        "pluginRoot": str(root), "versionsRoot": str(root / "versions"),
    }
    monkeypatch.setattr(peer_launch, "validate_owner", lambda *args: own)
    monkeypatch.setattr(peer_launch, "_packaged_primitive", lambda: object())
    monkeypatch.setattr(peer_launch, "_active_context", lambda *args: peer)
    monkeypatch.setattr(peer_launch, "_governance", lambda *args: {})
    monkeypatch.setenv(PROBE_ENV, PROBE_CONTRACT)
    descriptor = json.dumps({
        "schema": "copilot-extensions.readiness-isolation", "version": 1,
        "plugin": "agent-worktrees", "module": "agent_worktrees",
    })
    if qualification not in {"payload-missing", "ordinary"}:
        (payload / CONTRACT_FILE).write_text(descriptor, encoding="utf-8")
    if qualification == "qualified":
        contract = python.parent.parent / runtime_contract_path("agent_worktrees")
        contract.parent.mkdir(parents=True)
        contract.write_text(descriptor, encoding="utf-8")
    if qualification == "ordinary":
        monkeypatch.delenv(PROBE_ENV)
    arguments = ("agent-machines", own_root, "receipt", "agent-worktrees")
    if qualification.endswith("-missing"):
        with pytest.raises(ProbeContractError, match="unqualified"):
            peer_launch._resolve_peer(*arguments, allow_native=allow_native)
        assert imported.exists() == (qualification == "runtime-missing")
    else:
        _, environment, selected = peer_launch._resolve_peer(*arguments, allow_native=allow_native)
        assert selected == python
        assert imported.exists()
        assert environment.get(PROBE_ENV) == (
            PROBE_CONTRACT if qualification == "qualified" else None
        )


def test_peer_entry_preserves_typed_unknown(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    def refuse(*args, **kwargs):
        raise ProbeContractError("unqualified peer")

    monkeypatch.setattr(peer_launch, "launch", refuse)
    monkeypatch.setattr(sys, "argv", ["peer-launch", "owner", "root", "receipt", "peer"])
    assert peer_launch.main() == 126
    output = capsys.readouterr()
    assert unqualified_output(output.out.encode())
    assert "unqualified peer" in output.err


def test_peer_primitive_loader_does_not_convert_nested_unknown_to_configuration_failure(
    tmp_path: Path,
) -> None:
    path = tmp_path / "primitive.py"
    path.write_text(
        "from configuration_context.readiness import ProbeContractError\n"
        "raise ProbeContractError('nested contract gap')\n", encoding="utf-8",
    )
    with pytest.raises(ProbeContractError, match="nested contract gap"):
        peer_launch._load_primitive(path, "_probe_nested_primitive")
