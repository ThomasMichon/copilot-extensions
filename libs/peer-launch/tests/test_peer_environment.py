"""``peer_environment``'s per-plugin forwarding allowlists."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace
from typing import Any

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
    monkeypatch.setattr(peer_launch, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(peer_launch, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(peer_launch, "shutil", SimpleNamespace(which=lambda _name: None))
    with pytest.raises(ValueError, match="SHA-256 suffix support"):
        peer_launch._vault_service_suffix("/example/Vault")


def test_vault_peer_refuses_unqualified_native_platform_without_owner_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(peer_launch, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(peer_launch, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(peer_launch, "shutil", SimpleNamespace(
        which=lambda _name: pytest.fail("unqualified owner lookup"),
    ))
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
    def refuse(*args: object, **kwargs: object) -> None:
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


def test_retained_peer_validator_still_observes_each_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = tmp_path / "payload"
    primitive = payload / "scripts" / "installation-context" / "installation_context.py"
    primitive.parent.mkdir(parents=True)
    loaded = tmp_path / "loads"
    primitive.write_text(
        f"from pathlib import Path\np=Path({str(loaded)!r})\n"
        "p.write_text((p.read_text() if p.exists() else '') + 'x')\n", encoding="utf-8",
    )
    root = tmp_path / "marketplaces" / "cell" / "plugins" / "agent-worktrees"
    own = {"cellRoot": str(root.parent.parent)}
    peer = {
        **_context("agent-vault"), "payloadRoot": str(payload),
        "pluginRoot": str(root.with_name("agent-vault")), "marketplaceId": "example",
        "generation": 1,
    }
    owners, receipts = [], []

    def active(*args: object) -> dict[str, Any]:
        receipts.append(peer["generation"])
        return dict(peer)

    def owner(*args: object) -> dict[str, Any]:
        owners.append(True)
        return dict(own)

    monkeypatch.setattr(peer_launch, "validate_owner", owner)
    monkeypatch.setattr(peer_launch, "_packaged_primitive", lambda: object())
    monkeypatch.setattr(peer_launch, "_active_context", active)
    monkeypatch.setattr(peer_launch, "_governance", lambda *args: {})
    monkeypatch.setattr(peer_launch, "_peer_python_inprocess", lambda *args: None)
    monkeypatch.delenv(PROBE_ENV, raising=False)
    first = peer_launch._resolve_peer_observation(
        "agent-worktrees", root, "receipt", "agent-vault", allow_native=False,
    )
    peer["generation"] = 2
    second = peer_launch._resolve_peer_observation(
        "agent-worktrees", root, "receipt", "agent-vault", allow_native=False,
    )
    assert first[1]["generation"] == 1 and second[1]["generation"] == 2
    assert loaded.read_text() == "x"
    assert len(owners) == 4 and receipts == [1, 1, 1, 2, 2, 2]


@pytest.mark.parametrize("change", ["entry", "fragment", "same-metadata", "added"])
def test_retained_peer_validator_refuses_changed_code(
    tmp_path: Path, change: str,
) -> None:
    primitive = tmp_path / "scripts" / "installation-context" / "installation_context.py"
    primitive.parent.mkdir(parents=True)
    primitive.write_text("VALUE = 1\n", encoding="utf-8")
    fragment = primitive.with_name("_installation_context_test.py")
    fragment.write_text("VALUE = 1\n", encoding="utf-8")
    context = {"payloadRoot": str(tmp_path)}
    retained = peer_launch._peer_context_primitive(context)
    assert retained.VALUE == 1
    target = fragment if change == "fragment" else primitive
    before = target.stat()
    if change == "added":
        primitive.with_name("_installation_context_added.py").write_text("VALUE = 2\n", encoding="utf-8")
    else:
        target.write_text("VALUE = 2\n", encoding="utf-8")
        if change == "same-metadata":
            os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
    with pytest.raises(ValueError, match="code changed"):
        peer_launch._peer_context_primitive(context)
    assert retained.VALUE == 1


@pytest.mark.parametrize("qualification", ["qualified", "payload-missing", "runtime-missing", "unsupported"])
def test_isolated_peer_child_uses_validated_caller_readiness_support(
    tmp_path: Path, qualification: str,
) -> None:
    caller = tmp_path / "caller"
    caller_scripts = caller / "scripts"
    caller_scripts.mkdir(parents=True)
    shutil.copyfile(
        ROOT / "libs/configuration-layers/src/configuration_context/readiness.py",
        caller_scripts / "versioned_runtime_probe.py",
    )
    payload = tmp_path / "peer"
    primitive = payload / "scripts/installation-context/installation_context.py"
    primitive.parent.mkdir(parents=True)
    imported = tmp_path / "peer-imported"
    primitive.write_text(
        f"from pathlib import Path\nPath({str(imported)!r}).touch()\n", encoding="utf-8",
    )
    runtime = tmp_path / "marketplaces/cell/plugins/agent-worktrees/versions/selected/bin/python"
    runtime.parent.mkdir(parents=True)
    runtime.touch()
    descriptor = json.dumps({
        "schema": "copilot-extensions.readiness-isolation", "version": 1,
        "plugin": "agent-worktrees", "module": "agent_worktrees",
    })
    if qualification != "payload-missing":
        (payload / CONTRACT_FILE).write_text(descriptor, encoding="utf-8")
    if qualification == "qualified":
        contract = runtime.parent.parent / runtime_contract_path("agent_worktrees")
        contract.parent.mkdir(parents=True)
        contract.write_text(descriptor, encoding="utf-8")
    shadow = tmp_path / "configuration_context"
    shadow.mkdir()
    (shadow / "__init__.py").write_text("raise AssertionError('checkout import')", encoding="utf-8")
    script = """
import importlib.util, json, os, sys
from pathlib import Path
source, caller, payload, runtime, qualification = sys.argv[1:]
assert importlib.util.find_spec("configuration_context") is None
spec = importlib.util.spec_from_file_location("isolated_peer", source)
peer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(peer)
runtime = Path(runtime)
own = {"cellRoot": str(runtime.parents[5]), "payloadRoot": caller}
context = {
    "pluginId": "agent-worktrees", "payloadRoot": payload,
    "pluginRoot": str(runtime.parents[3]), "versionsRoot": str(runtime.parents[2]),
    "installReceipt": "receipt", "stateRoot": "state",
}
peer.validate_owner = lambda *args: own
peer._packaged_primitive = lambda: None
peer._active_context = lambda *args: context
peer._governance = lambda *args: {}
peer._peer_python_inprocess = lambda *args: runtime
os.environ["COPILOT_EXTENSIONS_READONLY_PROBE"] = (
    "unsupported" if qualification == "unsupported" else "isolated-imports-v1"
)
try:
    _, environment, selected = peer._resolve_peer(
        "agent-index", Path(caller), "receipt", "agent-worktrees", allow_native=False,
    )
except Exception as error:
    peer.launch = lambda *args, **kwargs: (_ for _ in ()).throw(error)
    sys.argv = ["peer", "owner", "root", "receipt", "peer"]
    raise SystemExit(peer.main())
assert selected == runtime
support = sys.modules["configuration_context.readiness"]
assert Path(support.__file__) == Path(caller) / "scripts/versioned_runtime_probe.py"
print(json.dumps({"selected": str(selected), "probe": environment["COPILOT_EXTENSIONS_READONLY_PROBE"]}))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-X", "utf8", "-c", script,
         str(ROOT / "libs/peer-launch/peer_launch.py"), str(caller), str(payload),
         str(runtime), qualification],
        cwd=tmp_path, capture_output=True, text=True, timeout=20,
        **peer_launch.no_window_kwargs(),
    )
    if qualification == "qualified":
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["selected"] == str(runtime)
    else:
        assert result.returncode == 126, result.stderr
        assert unqualified_output(result.stdout.encode()), result.stderr
        assert "ModuleNotFoundError" not in result.stderr
    assert imported.exists() == (qualification in {"qualified", "runtime-missing"})
    assert not list(tmp_path.rglob("*.pyc"))


@pytest.mark.parametrize("failure", ["unpinned", "site-enabled", "foreign-python"])
def test_inprocess_peer_refuses_unretained_interpreter_before_execution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str,
) -> None:
    python = Path(sys.executable)
    monkeypatch.setattr(peer_launch, "_resolve_peer", lambda *a, **k: (
        {"pluginRoot": str(tmp_path)}, {}, python,
    ))
    monkeypatch.setattr(peer_launch, "sys", SimpleNamespace(
        executable=str(tmp_path / "foreign-python") if failure == "foreign-python" else str(python),
        flags=SimpleNamespace(
            isolated=True, no_site=failure != "site-enabled", dont_write_bytecode=True,
        ),
    ))
    monkeypatch.setattr(
        peer_launch, "_retention_launcher",
        lambda *a: pytest.fail("unretained execution reached the runtime bootstrap"),
    )
    with pytest.raises((ValueError, OSError), match="retained|isolated|No such file|cannot find"):
        peer_launch.launch(
            "agent-worktrees", tmp_path, "owner", "agent-vault", ["probe"],
            pinned_python=None if failure == "unpinned" else python,
            runtime_identity="original-build", inprocess=True,
        )


@pytest.mark.parametrize("pinned", [False, True], ids=["dynamic", "pinned"])
@pytest.mark.parametrize(("selected", "executable"), [
    (r"C:\runtime\python.exe", r"C:\runtime\python.exe"),
    ("C:\\" + "x" * 250 + r"\python.exe", "\\\\?\\C:\\" + "x" * 250 + r"\python.exe"),
    ("\\\\?\\C:\\" + "x" * 250 + r"\python.exe", "\\\\?\\C:\\" + "x" * 250 + r"\python.exe"),
    ("\\\\server\\share\\" + "x" * 250 + r"\python.exe",
     "\\\\?\\UNC\\server\\share\\" + "x" * 250 + r"\python.exe"),
    ("C:\\" + "x" * 235 + "\U0001f600" * 10 + r"\python.exe",
     "\\\\?\\C:\\" + "x" * 235 + "\U0001f600" * 10 + r"\python.exe"),
    ("C:\\runtime\\\ud800\\python.exe", "C:\\runtime\\\ud800\\python.exe"),
], ids=["short-drive", "long-drive", "extended-drive", "long-unc",
        "supplementary-long-drive", "unpaired-surrogate"])
def test_windows_peer_launch_binds_executable_without_rewriting_argv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, pinned: bool, selected: str, executable: str,
) -> None:
    python = PureWindowsPath(selected)
    environment = {"COPILOT_EXTENSIONS_CONTEXT": "validated-peer"}
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def call(command: list[str], **kwargs: Any) -> int:
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
    inprocess = peer_launch.retained_peer_prefix(
        "agent-dispatch", tmp_path, "validated-owner", "agent-worktrees", python,
        runtime_identity="original-build",
    )
    assert inprocess[0] == executable
    assert inprocess[8:12] == [
        "--pinned-runtime", str(python), "--runtime-identity", "original-build",
    ]
