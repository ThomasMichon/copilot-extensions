"""``peer_environment``'s per-plugin forwarding allowlists."""
from __future__ import annotations

import importlib.util
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
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


@pytest.mark.parametrize("pinned", [False, True], ids=["dynamic", "pinned"])
@pytest.mark.parametrize(("selected", "executable"), [
    (r"C:\runtime\python.exe", r"C:\runtime\python.exe"),
    ("C:\\" + "x" * 250 + r"\python.exe", "\\\\?\\C:\\" + "x" * 250 + r"\python.exe"),
    ("\\\\?\\C:\\" + "x" * 250 + r"\python.exe", "\\\\?\\C:\\" + "x" * 250 + r"\python.exe"),
    ("\\\\server\\share\\" + "x" * 250 + r"\python.exe",
     "\\\\?\\UNC\\server\\share\\" + "x" * 250 + r"\python.exe"),
], ids=["short-drive", "long-drive", "extended-drive", "long-unc"])
def test_windows_peer_launch_binds_executable_without_rewriting_argv(
    monkeypatch, tmp_path, pinned, selected, executable,
):
    python = PureWindowsPath(selected)
    environment = {"COPILOT_EXTENSIONS_CONTEXT": "validated-peer"}
    calls = []

    def call(command, **kwargs):
        calls.append((command, kwargs))
        return 23

    monkeypatch.setattr(peer_launch, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        peer_launch, "subprocess", SimpleNamespace(call=call, CREATE_NO_WINDOW=0x08000000),
    )
    monkeypatch.setattr(peer_launch, "_resolve_peer", lambda *a, **k: (
        {"pluginRoot": str(tmp_path)}, environment, python,
    ))
    retained = [str(python), "-I", "-B", "retained-launcher", "--runtime-identity", "original-build"]
    monkeypatch.setattr(peer_launch, "retained_runtime_prefix", lambda *a, **k: retained)
    arguments = ["probe", 'quoted " argument', "snowman \u2603"]
    assert peer_launch.launch(
        "agent-dispatch", tmp_path, "validated-owner", "agent-worktrees", arguments,
        pinned_python=python if pinned else None, runtime_identity="original-build" if pinned else None,
    ) == 23
    command, kwargs = calls.pop()
    prefix = retained if pinned else [
        str(python), "-I", "-B", "-X", "utf8", "-m", "agent_worktrees",
    ]
    assert command == [*prefix, *arguments]
    assert kwargs["executable"] == executable
    assert kwargs["env"] is environment
    assert (kwargs["stdin"], kwargs["stdout"], kwargs["stderr"]) == (
        peer_launch.sys.stdin, peer_launch.sys.stdout, peer_launch.sys.stderr,
    )
    assert kwargs["creationflags"] == 0x08000000
    assert "shell" not in kwargs
