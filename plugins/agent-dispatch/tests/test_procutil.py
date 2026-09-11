"""Tests for the shared no-console-window spawn helper."""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import venv
from ctypes import wintypes
from pathlib import Path

import pytest

from agent_dispatch import procutil
from agent_dispatch import _installation_context as installation_context

FIRST_MARKETPLACE_ID = "example--9caa0da95f327099"
SECOND_MARKETPLACE_ID = "corp-git--22c1380840c88801"
SOURCE_IDENTITIES = {
    FIRST_MARKETPLACE_ID: {
        "kind": "github",
        "canonical": "github:example-org/example-marketplace",
        "ref": "",
        "fingerprint": (
            "sha256:9caa0da95f327099983348a3d2353fa93e57ef6428b69f354cc3bdc76ada0894"
        ),
    },
    SECOND_MARKETPLACE_ID: {
        "kind": "git",
        "canonical": "git:https://example.com/Org/Repo",
        "ref": "release/One",
        "fingerprint": (
            "sha256:22c1380840c88801bc99a968c3012bf8293c62c63d0912438676094382a0c285"
        ),
    },
}


def test_no_window_kwargs_on_windows(monkeypatch):
    monkeypatch.setattr(procutil.os, "name", "nt")
    kw = procutil.no_window_kwargs()
    expected = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    assert kw == {"creationflags": expected}


def test_no_window_kwargs_off_windows(monkeypatch):
    monkeypatch.setattr(procutil.os, "name", "posix")
    assert procutil.no_window_kwargs() == {}


def test_agent_worktrees_capture_bypasses_cmd_with_no_window_console_tree(
    tmp_path, monkeypatch
):
    runtime = tmp_path / ".agent-worktrees"
    python = _make_slot(runtime, "1.5.3-dev9")
    (runtime / "current-version").write_text("1.5.3-dev9")
    captured = {}

    class FakeProc:
        returncode = 0

        def communicate(self, *, timeout):
            return "host-a\n", ""

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return FakeProc()

    monkeypatch.setattr(procutil.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(procutil.os, "name", "nt")
    monkeypatch.setattr(procutil.sys, "platform", "win32")
    monkeypatch.setattr(
        procutil.shutil, "which", lambda _n: r"C:\bin\agent-worktrees.CMD"
    )
    monkeypatch.setattr(procutil.subprocess, "Popen", fake_popen)
    result = procutil.run_agent_worktrees_capture("get", "machine", timeout=15)

    assert result is not None and result.stdout == "host-a\n"
    assert captured["cmd"] == [
        str(python), "-m", "agent_worktrees", "get", "machine"
    ]
    assert not any(arg.lower().endswith((".cmd", ".bat")) for arg in captured["cmd"])
    flags = captured["kwargs"]["creationflags"]
    assert flags == getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    assert not (flags & 0x00000008)  # DETACHED_PROCESS would free descendants
    assert captured["kwargs"]["stdout"] is subprocess.PIPE
    assert captured["kwargs"]["stderr"] is subprocess.PIPE
    assert captured["kwargs"]["stdin"] is subprocess.DEVNULL


def test_agent_worktrees_launch_has_no_windows_path_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(procutil.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(procutil.os, "name", "nt")
    monkeypatch.setattr(
        procutil.shutil, "which", lambda _n: r"C:\bin\agent-worktrees.CMD"
    )
    assert procutil.agent_worktrees_launch_prefix() is None


def _make_namespaced_context(
    cell_root: Path,
    *,
    plugin_id: str = "agent-dispatch",
    plugin_root: Path | None = None,
) -> Path:
    root = plugin_root or cell_root / "plugins" / plugin_id
    root.mkdir(parents=True, exist_ok=True)
    payload = cell_root / "payloads" / plugin_id
    payload.mkdir(parents=True, exist_ok=True)
    namespace = cell_root / "namespace.json"
    if not namespace.exists():
        namespace.write_text(
            json.dumps(
                {
                    "schema": "copilot-extensions.marketplace-namespace",
                    "version": 1,
                    "marketplaceId": cell_root.name,
                    "source": SOURCE_IDENTITIES[cell_root.name],
                    "locators": [],
                    "generation": 1,
                    "state": "active",
                }
            ),
            encoding="utf-8",
        )
    context = root / "install.json"
    context.write_text(
        json.dumps(
            {
                "schema": "copilot-extensions.plugin-installation",
                "version": 1,
                "marketplaceId": cell_root.name,
                "pluginId": plugin_id,
                "pluginRoot": str(root.resolve()),
                "namespaceReceipt": str(namespace.resolve()),
                "payload": {
                    "root": str(payload.resolve()),
                    "version": "1.0.0",
                    "origin": "explicit",
                },
                "roots": {
                    "versions": "versions",
                    "snapshots": "snapshots",
                    "state": "state",
                    "run": "run",
                    "logs": "logs",
                    "cache": "cache",
                    "launchers": "launchers",
                },
                "generation": 1,
                "state": "active",
            }
        ),
        encoding="utf-8",
    )
    environment, _ = installation_context._current_environment(
        environment=os.environ, os_profile=None, platform=None, wsl_distro=None,
    )
    (root / "installation-activation.json").write_text(json.dumps({
        "schema": "copilot-extensions.installation-activation", "version": 1,
        "marketplaceId": cell_root.name, "pluginId": plugin_id,
        "mode": "namespaced", "state": "active", "environment": environment,
        "context": str(context), "namespaceGeneration": 1,
        "installGeneration": 1, "generation": 1,
        "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-01T00:00:00Z",
        "legacy": {
            "disposition": "absent",
            "probe": {"declared": True, "result": "absent",
                      "checkedAt": "2026-01-01T00:00:00Z"},
        },
    }), encoding="utf-8")
    return context


def _update_receipt(path: Path, update) -> None:
    value = json.loads(path.read_text(encoding="utf-8"))
    update(value)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_loop_governance_loads_packaged_primitive_without_payload_copy(tmp_path, monkeypatch):
    from agent_dispatch import _installation_context, loop_governance

    cell = tmp_path / "marketplaces" / FIRST_MARKETPLACE_ID
    own = _make_namespaced_context(cell)
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(own))
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(own.parent))
    assert not (own.parent / "deploy-manifest.json").exists()
    assert not (cell / "payloads" / "agent-dispatch" / "scripts").exists()

    helper = loop_governance._load_governance_module()
    assert helper is not None and "error" not in helper
    assert helper["module"] is _installation_context
    assert helper["context"] == str(own)
    assert helper["durable_home"] == str(tmp_path)
    assert callable(helper["module"].recheck_loop_governance)


def _assert_peer_refused(prefix: list[str]) -> None:
    result = subprocess.run(
        [*prefix, "must-not-execute"], capture_output=True, encoding="utf-8",
        timeout=15, **procutil.no_window_kwargs(),
    )
    assert result.returncode == 126, (result.stdout, result.stderr)
    assert result.stdout == ""
    assert "peer launch refused:" in result.stderr


def _make_live_peer(cell: Path, plugin: str, version: str) -> Path:
    """Disposable real venv + actual shipped governance and runtime resolver."""
    receipt = _make_namespaced_context(cell, plugin_id=plugin)
    root = receipt.parent
    payload = cell / "payloads" / plugin
    scripts = payload / "scripts"
    (scripts / "installation-context").mkdir(parents=True, exist_ok=True)
    source = Path(__file__).resolve().parents[2] / plugin / "scripts"
    for name in ("resolve-runtime.ps1", "resolve-runtime.sh"):
        shutil.copyfile(source / name, scripts / name)
    shutil.copyfile(
        source / "installation-context" / "installation_context.py",
        scripts / "installation-context" / "installation_context.py",
    )
    environment, _ = installation_context._current_environment(
        environment=os.environ, os_profile=None, platform=None, wsl_distro=None,
    )
    (root / "installation-activation.json").write_text(json.dumps({
        "schema": "copilot-extensions.installation-activation", "version": 1,
        "marketplaceId": cell.name, "pluginId": plugin,
        "mode": "namespaced", "state": "active", "environment": environment,
        "context": str(receipt), "namespaceGeneration": 1,
        "installGeneration": 1, "generation": 1,
        "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-01T00:00:00Z",
        "legacy": {
            "disposition": "absent",
            "probe": {"declared": True, "result": "absent",
                      "checkedAt": "2026-01-01T00:00:00Z"},
        },
    }), encoding="utf-8")
    slot = root / "versions" / version
    venv.EnvBuilder(with_pip=False, symlinks=os.name != "nt").create(slot)
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    site = (
        slot / "Lib" / "site-packages" if os.name == "nt"
        else slot / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    )
    module = site / plugin.replace("-", "_")
    module.mkdir()
    (module / "__init__.py").write_text("", encoding="utf-8")
    (module / "__main__.py").write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "if os.environ.get('PEER_EXECUTION_SENTINEL'):\n"
        " Path(os.environ['PEER_EXECUTION_SENTINEL']).write_text('executed')\n"
        "if sys.argv[1:] == ['streams']:\n"
        " print(sys.stdin.read(), end='')\n"
        " print('peer-stderr', file=sys.stderr)\n"
        " sys.exit(7)\n"
        "print(json.dumps({'argv': sys.argv[1:], 'env': dict(os.environ), "
        "'python': sys.executable, 'isolated': sys.flags.isolated, "
        "'utf8': sys.flags.utf8_mode}, ensure_ascii=True))\n",
        encoding="utf-8",
    )
    (slot / ".install-complete.json").write_text(json.dumps({
        "version": version, "completed_at": "2026-01-01T00:00:00Z", "pid": 1,
    }), encoding="utf-8")
    (root / "current-version").write_text(version, encoding="utf-8")
    return python


@pytest.mark.parametrize(
    ("resolver", "sibling_id", "module"),
    [
        (procutil.agent_worktrees_launch_prefix, "agent-worktrees", "agent_worktrees"),
        (procutil.agent_bridge_launch_prefix, "agent-bridge", "agent_bridge"),
    ],
)
@pytest.mark.parametrize("owner", ["agent-dispatch", "agent-codespaces"])
def test_namespaced_sibling_resolution_stays_in_active_marketplace_cell(
    tmp_path, monkeypatch, resolver, sibling_id, module, owner
):
    if owner == "agent-codespaces" and sibling_id != "agent-worktrees":
        pytest.skip("CodeSpaces only composes with worktrees")
    tmp_path = tmp_path / "cells & ' \u96ea"
    first_cell = tmp_path / "marketplaces" / FIRST_MARKETPLACE_ID
    second_cell = tmp_path / "marketplaces" / SECOND_MARKETPLACE_ID
    first_context = _make_namespaced_context(first_cell, plugin_id=owner)
    _make_namespaced_context(second_cell, plugin_id=owner)
    _make_namespaced_context(first_cell, plugin_id=sibling_id)
    _make_namespaced_context(second_cell, plugin_id=sibling_id)
    first_python = _make_live_peer(first_cell, sibling_id, "1.0.0-dev1")
    second_python = _make_live_peer(second_cell, sibling_id, "2.0.0-dev1")
    monkeypatch.setenv(
        "AGENT_DISPATCH_INSTALL_DIR",
        str(first_cell / "plugins" / "agent-dispatch"),
    )
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(first_context))
    monkeypatch.setattr(
        procutil.shutil, "which", lambda _name: str(second_python)
    )

    raw_args = [
        "get", "", 'quoted "seed"', "space here", "\u96ea \U0001f680",
        "&|<>^%!;$(touch never)`echo no`", "ends-with\\", "\nline\tbreak",
        '{"prompt":"quoted \\"seed\\"","empty":"","path":"C:\\\\a b"}',
    ]
    for cell, expected in ((first_cell, first_python), (second_cell, second_python)):
        own_root = cell / "plugins" / owner
        monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(own_root))
        monkeypatch.setenv("AGENT_CODESPACES_HOME", str(own_root))
        monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(own_root / "install.json"))
        monkeypatch.setenv("COPILOT_PLUGIN_ROOT", str(cell / "payloads" / owner))
        for name in (
            "AGENT_RT_ROOT", "AGENT_RT_PY", "AGENT_HOME",
            "AGENT_WORKTREES_PAYLOAD_ROOT", "AGENT_WORKTREES_LAUNCH_RUNTIME_ROOT",
            "AGENT_BRIDGE_INSTALL_DIR", "AGENT_BRIDGE_CONFIG_DIR",
            "AGENT_BRIDGE_PAYLOAD_ROOT", "AGENT_BRIDGE_BASE_URL", "PYTHONPATH", "PYTHONHOME",
            "AGENT_DISPATCH_TOKEN", "AGENT_DISPATCH_CONTROL_TOKEN", "AGENT_DISPATCH_URL",
            "AGENT_CODESPACES_TOKEN", "GH_TOKEN", "GITHUB_TOKEN",
            "AGENT_BRIDGE_SESSION_HOST_NONCE", "AGENT_BRIDGE_NO_ROUTING_TABLE",
            "AGENT_WORKTREES_OWNER_REF", "AGENT_WORKTREES_AHP_AUTH_TOKEN",
            "AGENT_WORKTREES_BIND", "AGENT_WORKTREES_PROJECT",
        ):
            monkeypatch.setenv(name, str(tmp_path / "foreign"))
        if owner == "agent-codespaces":
            adapter = _codespaces_adapter(monkeypatch)
            result = adapter.run(*raw_args)
        else:
            prefix = resolver()
            result = subprocess.run(
                [*prefix, *raw_args], capture_output=True, encoding="utf-8",
                timeout=15, **procutil.no_window_kwargs(),
            )
        assert result.returncode == 0, result.stderr
        observed = json.loads(result.stdout)
        assert observed["argv"] == raw_args
        assert Path(observed["python"]) == expected
        assert observed["isolated"] == observed["utf8"] == 1
        child_env = observed["env"]
        assert child_env["COPILOT_EXTENSIONS_CONTEXT"] == str(
            cell / "plugins" / sibling_id / "install.json"
        )
        assert child_env["COPILOT_PLUGIN_ROOT"] == str(cell / "payloads" / sibling_id)
        assert child_env["AGENT_RT_ROOT"] == str(cell / "plugins" / sibling_id)
        assert child_env[sibling_id.upper().replace("-", "_") + "_PAYLOAD_ROOT"] == str(
            cell / "payloads" / sibling_id
        )
        assert not set(child_env) & {
            "PYTHONPATH", "PYTHONHOME", "AGENT_HOME", "AGENT_RT_PY",
            "AGENT_WORKTREES_LAUNCH_RUNTIME_ROOT", "AGENT_DISPATCH_INSTALL_DIR",
            "AGENT_BRIDGE_BASE_URL", "AGENT_DISPATCH_TOKEN",
            "AGENT_DISPATCH_CONTROL_TOKEN", "AGENT_DISPATCH_URL",
            "AGENT_CODESPACES_HOME", "AGENT_CODESPACES_TOKEN", "GH_TOKEN", "GITHUB_TOKEN",
            "AGENT_BRIDGE_SESSION_HOST_NONCE", "AGENT_BRIDGE_NO_ROUTING_TABLE",
            "AGENT_WORKTREES_OWNER_REF", "AGENT_WORKTREES_AHP_AUTH_TOKEN",
            "AGENT_WORKTREES_BIND", "AGENT_WORKTREES_PROJECT",
        }
        assert os.environ["COPILOT_EXTENSIONS_CONTEXT"] == str(own_root / "install.json")
        if os.name != "nt":
            assert expected.is_symlink()


def _codespaces_adapter(monkeypatch):
    source = Path(__file__).resolve().parents[2] / "agent-codespaces" / "src"
    monkeypatch.syspath_prepend(str(source))
    from agent_codespaces import worktrees

    return worktrees


def test_codespaces_peer_refusals_are_not_optional_absence(tmp_path, monkeypatch):
    adapter = _codespaces_adapter(monkeypatch)
    cell = tmp_path / "marketplaces" / FIRST_MARKETPLACE_ID
    own = _make_namespaced_context(cell, plugin_id="agent-codespaces")
    monkeypatch.setenv("AGENT_CODESPACES_HOME", str(own.parent))
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(own))
    # Valid owner with no peer at all is the sole installation absence case.
    assert adapter.run("get", "owner-ref") is None
    # A source hook has the explicit receipt but need not have runtime-gate env.
    monkeypatch.delenv("AGENT_CODESPACES_HOME")
    assert adapter.run("get", "owner-ref") is None
    monkeypatch.setenv("AGENT_CODESPACES_HOME", str(own.parent))
    owner_activation = own.with_name("installation-activation.json")
    original_activation = owner_activation.read_bytes()
    try:
        owner_activation.write_text("{", encoding="utf-8")
        with pytest.raises(adapter.ContextRefused, match="activation-invalid"):
            adapter.run("get", "owner-ref")
    finally:
        owner_activation.write_bytes(original_activation)
    maintenance = own.parent / "maintenance"
    try:
        maintenance.touch()
        with pytest.raises(adapter.ContextRefused, match="maintenance"):
            adapter.validate_context()
        with pytest.raises(adapter.ContextRefused, match="maintenance"):
            adapter.run("get", "owner-ref")
    finally:
        maintenance.unlink()
    python = _make_live_peer(cell, "agent-worktrees", "1.0.0-dev1")
    peer = cell / "plugins" / "agent-worktrees" / "install.json"
    activation = peer.with_name("installation-activation.json")
    sentinel = tmp_path / "executed"
    monkeypatch.setenv("PEER_EXECUTION_SENTINEL", str(sentinel))
    for receipt in (own, owner_activation, peer, cell / "namespace.json", activation):
        original = receipt.read_bytes()
        try:
            for content in (b"{", original.replace(b'"active"', b'"inactive"')):
                receipt.write_bytes(content)
                with pytest.raises(adapter.ContextRefused):
                    adapter.run("claim", "must-not-execute")
                assert not sentinel.exists()
            receipt.unlink()
            with pytest.raises(adapter.ContextRefused):
                adapter.run("claim", "must-not-execute")
            assert not sentinel.exists()
        finally:
            receipt.write_bytes(original)
    foreign = _make_namespaced_context(
        tmp_path / "marketplaces" / SECOND_MARKETPLACE_ID, plugin_id="agent-codespaces",
    )
    for raw in ("{", " ", str(foreign), str(peer)):
        monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", raw)
        with pytest.raises(adapter.ContextRefused):
            adapter.run("claim", "must-not-execute")
        assert not sentinel.exists()
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(own))
    # The source hook must work outside the repo and without CodeSpaces (or
    # agent-procutil) installed in the bootstrap interpreter's disposable venv.
    script = (
        Path(__file__).resolve().parents[2] / "agent-codespaces"
        / "scripts" / "emit_codespace_map.py"
    )
    result = subprocess.run(
        [str(python), "-I", str(script), "--cwd", str(tmp_path)],
        cwd=tmp_path, capture_output=True, encoding="utf-8", timeout=15,
        **procutil.no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {}
    assert sentinel.read_text() == "executed"


@pytest.mark.parametrize("state", ["inactive", "orphaned", "removing"])
@pytest.mark.parametrize("receipt_owner", ["namespace", "agent-dispatch", "peer"])
def test_namespaced_sibling_resolution_requires_active_lifecycle_receipts(
    tmp_path, monkeypatch, state, receipt_owner
):
    cell = tmp_path / "marketplaces" / FIRST_MARKETPLACE_ID
    own_context = _make_namespaced_context(cell)
    peer_context = _make_namespaced_context(cell, plugin_id="agent-bridge")
    peer_root = cell / "plugins" / "agent-bridge"
    _make_slot(peer_root, "1.0.0-dev1", complete=True)
    (peer_root / "current-version").write_text("1.0.0-dev1")
    target = {
        "namespace": cell / "namespace.json",
        "agent-dispatch": own_context,
        "peer": peer_context,
    }[receipt_owner]
    _update_receipt(target, lambda receipt: receipt.__setitem__("state", state))
    monkeypatch.setenv(
        "AGENT_DISPATCH_INSTALL_DIR",
        str(cell / "plugins" / "agent-dispatch"),
    )
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(own_context))

    _assert_peer_refused(procutil.agent_bridge_launch_prefix())


def test_namespaced_saved_prefix_revalidates_before_peer_execution(tmp_path, monkeypatch):
    cell = tmp_path / "marketplaces" / FIRST_MARKETPLACE_ID
    own = _make_namespaced_context(cell)
    _make_live_peer(cell, "agent-bridge", "1.0.0-dev1")
    peer = cell / "plugins" / "agent-bridge" / "install.json"
    activation = peer.with_name("installation-activation.json")
    namespace = cell / "namespace.json"
    sentinel = tmp_path / "executed"
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(own.parent))
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(own))
    monkeypatch.setenv("PEER_EXECUTION_SENTINEL", str(sentinel))
    prefix = procutil.agent_bridge_launch_prefix()
    changes = [
        (own, lambda value: value.update(state="inactive")),
        (peer, lambda value: value.update(state="inactive")),
        (namespace, lambda value: value.update(state="inactive")),
        (peer, lambda value: value.update(pluginId="agent-worktrees")),
        (peer, lambda value: value["payload"].pop("version")),
        (activation, lambda value: value.update(installGeneration=99)),
        (activation, lambda value: value.update(state="inactive")),
        (activation, lambda value: value["environment"].update(
            homeRealPath=str(tmp_path / "foreign-profile"))),
    ]
    for receipt, update in changes:
        original = receipt.read_bytes()
        try:
            _update_receipt(receipt, update)
            _assert_peer_refused(prefix)
            assert not sentinel.exists()
        finally:
            receipt.write_bytes(original)
    for receipt in (own, peer, namespace, activation):
        original = receipt.read_bytes()
        try:
            receipt.unlink()
            _assert_peer_refused(prefix)
            assert not sentinel.exists()
        finally:
            receipt.write_bytes(original)
    # The same saved prefix succeeds when its actual receipts are valid again.
    result = subprocess.run(
        [*prefix, "streams"], input='seed "quoted" \u96ea\n', capture_output=True,
        encoding="utf-8", timeout=15, **procutil.no_window_kwargs(),
    )
    assert result.returncode == 7, result.stderr
    assert result.stdout == 'seed "quoted" \u96ea\n'
    assert result.stderr == "peer-stderr\n"
    assert sentinel.read_text() == "executed"


@pytest.mark.parametrize("context", ["{", " ", "missing-install.json"])
def test_explicit_context_never_uses_legacy_install_dir(tmp_path, monkeypatch, context):
    monkeypatch.setattr(procutil.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("AGENT_DISPATCH_INSTALL_DIR", raising=False)
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", context)
    _make_slot(tmp_path / ".agent-worktrees", "1.0.0-dev1", complete=True)
    monkeypatch.setattr(procutil.shutil, "which", lambda name: pytest.fail("PATH fallback"))
    _assert_peer_refused(procutil.agent_worktrees_launch_prefix())


def test_no_context_keeps_real_legacy_argv_and_environment(tmp_path, monkeypatch):
    cell = tmp_path / "marketplaces" / FIRST_MARKETPLACE_ID
    python = _make_live_peer(cell, "agent-bridge", "1.0.0-dev1")
    legacy = tmp_path / ".agent-bridge"
    shutil.move(str(cell / "plugins" / "agent-bridge"), legacy)
    python = legacy / "versions" / "1.0.0-dev1" / python.parent.name / python.name
    monkeypatch.setattr(procutil.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(cell / "plugins" / "agent-dispatch"))
    monkeypatch.setenv("COPILOT_PLUGIN_ROOT", str(tmp_path / "legacy-caller"))
    monkeypatch.setenv("AGENT_RT_ROOT", str(tmp_path / "legacy-override"))
    prefix = procutil.agent_bridge_launch_prefix()
    assert prefix == [str(python), "-m", "agent_bridge"]
    result = subprocess.run(
        [*prefix, "", '{"raw":"seed \\"text\\""}', "&|<>\u96ea"],
        capture_output=True, encoding="utf-8", timeout=15,
        **procutil.no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["argv"] == ["", '{"raw":"seed \\"text\\""}', "&|<>\u96ea"]
    assert observed["isolated"] == 0
    assert observed["env"]["COPILOT_PLUGIN_ROOT"] == str(tmp_path / "legacy-caller")
    assert observed["env"]["AGENT_RT_ROOT"] == str(tmp_path / "legacy-override")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows windowless parent contract")
def test_namespaced_peer_from_windowless_parent(tmp_path, monkeypatch):
    cell = tmp_path / "marketplaces" / FIRST_MARKETPLACE_ID
    own = _make_namespaced_context(cell)
    _make_live_peer(cell, "agent-bridge", "1.0.0-dev1")
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(own.parent))
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(own))
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    assert pythonw.is_file()
    # Exercise the production prefix's conversion back to a console root.
    monkeypatch.setattr(procutil.sys, "executable", str(pythonw))
    prefix = procutil.agent_bridge_launch_prefix()
    assert Path(prefix[0]).name == "python.exe"
    input_path = tmp_path / "launch.json"
    output_path = tmp_path / "result.json"
    input_path.write_text(json.dumps(prefix), encoding="utf-8")
    parent = tmp_path / "windowless_parent.py"
    parent.write_text(
        "import json, subprocess, sys, time\n"
        "from pathlib import Path\n"
        "from agent_procutil import no_window_kwargs\n"
        "prefix = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))\n"
        "results = []\n"
        "for _ in range(2):\n"
        " result = subprocess.run(prefix + ['cycle', ''], stdin=subprocess.DEVNULL,\n"
        "  capture_output=True, encoding='utf-8', timeout=15, **no_window_kwargs())\n"
        " results.append([result.returncode, result.stdout, result.stderr])\n"
        " time.sleep(.1)\n"
        "Path(sys.argv[2]).write_text(json.dumps(results), encoding='utf-8')\n",
        encoding="utf-8",
    )
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND, ctypes.POINTER(wintypes.DWORD),
    ]
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]

    def visible_windows():
        windows = set()

        @callback_type
        def visit(hwnd, _):
            if user32.IsWindowVisible(hwnd):
                windows.add(hwnd)
            return True

        user32.EnumWindows(visit, 0)
        return windows

    def process_name(hwnd):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = kernel32.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return ""
        try:
            length = wintypes.DWORD(32768)
            name = ctypes.create_unicode_buffer(length.value)
            if kernel32.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(length)):
                return Path(name.value).name.lower()
            return ""
        finally:
            kernel32.CloseHandle(handle)

    baseline = visible_windows()
    foreground = user32.GetForegroundWindow()
    process_names = {
        "python.exe", "pythonw.exe", "powershell.exe", "conhost.exe",
        "openconsole.exe", "windowsterminal.exe",
    }
    surfaced = set()
    focus_changes = set()
    with subprocess.Popen([str(pythonw), "-I", str(parent), str(input_path), str(output_path)]) as proc:
        deadline = time.monotonic() + 25
        while proc.poll() is None and time.monotonic() < deadline:
            surfaced.update(
                hwnd for hwnd in visible_windows() - baseline
                if process_name(hwnd) in process_names
            )
            current = user32.GetForegroundWindow()
            # Switching among existing desktop windows is operator activity,
            # not evidence that this probe acquired a console.
            if (
                current != foreground and current not in baseline
                and process_name(current) in process_names
            ):
                focus_changes.add(current)
            time.sleep(.02)
        if proc.poll() is None:
            procutil.terminate_process_tree(proc)
            pytest.fail("windowless peer probe exceeded its deadline")
        assert proc.returncode == 0
    results = json.loads(output_path.read_text(encoding="utf-8"))
    assert len(results) == 2
    for code, stdout, stderr in results:
        assert code == 0, stderr
        assert json.loads(stdout)["argv"] == ["cycle", ""]
    assert surfaced == focus_changes == set()


@pytest.mark.parametrize(
    ("receipt_owner", "remove"),
    [
        ("namespace", lambda receipt: receipt.pop("source")),
        (
            "namespace",
            lambda receipt: receipt["source"].__setitem__(
                "fingerprint",
                "sha256:" + ("0" * 64),
            ),
        ),
        ("agent-dispatch", lambda receipt: receipt.pop("payload")),
        ("agent-dispatch", lambda receipt: receipt["payload"].pop("version")),
        ("peer", lambda receipt: receipt["payload"].pop("origin")),
        ("peer", lambda receipt: receipt["roots"].pop("snapshots")),
    ],
)
def test_namespaced_sibling_resolution_rejects_incomplete_receipts(
    tmp_path, monkeypatch, receipt_owner, remove
):
    cell = tmp_path / "marketplaces" / FIRST_MARKETPLACE_ID
    own_context = _make_namespaced_context(cell)
    peer_context = _make_namespaced_context(cell, plugin_id="agent-worktrees")
    peer_root = cell / "plugins" / "agent-worktrees"
    _make_slot(peer_root, "1.0.0-dev1", complete=True)
    (peer_root / "current-version").write_text("1.0.0-dev1")
    target = {
        "namespace": cell / "namespace.json",
        "agent-dispatch": own_context,
        "peer": peer_context,
    }[receipt_owner]
    _update_receipt(target, remove)
    monkeypatch.setenv(
        "AGENT_DISPATCH_INSTALL_DIR",
        str(cell / "plugins" / "agent-dispatch"),
    )
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(own_context))

    _assert_peer_refused(procutil.agent_worktrees_launch_prefix())


def test_namespaced_install_dir_without_context_preserves_legacy_resolution(
    tmp_path, monkeypatch
):
    cell = tmp_path / "marketplaces" / FIRST_MARKETPLACE_ID
    own_root = cell / "plugins" / "agent-dispatch"
    _make_namespaced_context(cell)
    _make_namespaced_context(cell, plugin_id="agent-bridge")
    _make_slot(
        cell / "plugins" / "agent-bridge", "1.0.0-dev1", complete=True
    )
    (cell / "plugins" / "agent-bridge" / "current-version").write_text(
        "1.0.0-dev1"
    )
    legacy_python = _make_slot(
        tmp_path / ".agent-bridge", "0.9.0-dev1", complete=True
    )
    (tmp_path / ".agent-bridge" / "current-version").write_text("0.9.0-dev1")
    monkeypatch.setattr(procutil.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(own_root))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)

    assert procutil.agent_bridge_launch_prefix() == [
        str(legacy_python),
        "-m",
        "agent_bridge",
    ]


@pytest.mark.parametrize(
    "context_kind",
    ["invalid", "relative-root", "foreign-cell", "foreign-plugin"],
)
def test_namespaced_sibling_resolution_rejects_invalid_or_foreign_context(
    tmp_path, monkeypatch, context_kind
):
    first_cell = tmp_path / "marketplaces" / FIRST_MARKETPLACE_ID
    second_cell = tmp_path / "marketplaces" / SECOND_MARKETPLACE_ID
    first_root = first_cell / "plugins" / "agent-dispatch"
    _make_namespaced_context(first_cell)
    _make_namespaced_context(first_cell, plugin_id="agent-worktrees")
    _make_slot(
        first_cell / "plugins" / "agent-worktrees",
        "1.0.0-dev1",
        complete=True,
    )
    (first_cell / "plugins" / "agent-worktrees" / "current-version").write_text(
        "1.0.0-dev1"
    )
    context = first_root / "install.json"
    if context_kind == "invalid":
        context.write_text("{", encoding="utf-8")
    elif context_kind == "relative-root":
        context.write_text(
            json.dumps(
                {
                    "marketplaceId": first_cell.name,
                    "pluginId": "agent-dispatch",
                    "pluginRoot": "plugins/agent-dispatch",
                    "cellRoot": str(first_cell),
                }
            ),
            encoding="utf-8",
        )
    elif context_kind == "foreign-cell":
        context.write_text(
            json.dumps(
                {
                    "marketplaceId": second_cell.name,
                    "pluginId": "agent-dispatch",
                    "pluginRoot": str(first_root),
                    "cellRoot": str(second_cell),
                }
            ),
            encoding="utf-8",
        )
    else:
        context.write_text(
            json.dumps(
                {
                    "marketplaceId": first_cell.name,
                    "pluginId": "agent-bridge",
                    "pluginRoot": str(first_root),
                    "cellRoot": str(first_cell),
                }
            ),
            encoding="utf-8",
        )
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(first_root))
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(context))
    monkeypatch.setattr(
        procutil.shutil, "which", lambda _name: "/foreign/agent-worktrees"
    )

    _assert_peer_refused(procutil.agent_worktrees_launch_prefix())


def test_namespaced_sibling_resolution_rejects_incomplete_slot(
    tmp_path, monkeypatch
):
    cell = tmp_path / "marketplaces" / FIRST_MARKETPLACE_ID
    own_context = _make_namespaced_context(cell)
    _make_namespaced_context(cell, plugin_id="agent-worktrees")
    python = _make_live_peer(cell, "agent-worktrees", "1.0.0-dev1")
    (python.parent.parent / ".install-complete.json").unlink()
    monkeypatch.setenv(
        "AGENT_DISPATCH_INSTALL_DIR",
        str(cell / "plugins" / "agent-dispatch"),
    )
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(own_context))

    _assert_peer_refused(procutil.agent_worktrees_launch_prefix())


def test_namespaced_sibling_resolution_accepts_completed_last_known_good(
    tmp_path, monkeypatch
):
    cell = tmp_path / "marketplaces" / FIRST_MARKETPLACE_ID
    own_context = _make_namespaced_context(cell)
    sibling_root = cell / "plugins" / "agent-bridge"
    _make_namespaced_context(cell, plugin_id="agent-bridge")
    python = _make_live_peer(cell, "agent-bridge", "1.0.0-dev1")
    (sibling_root / "current-version").unlink()
    (sibling_root / "last-known-good").write_text("1.0.0-dev1")
    monkeypatch.setenv(
        "AGENT_DISPATCH_INSTALL_DIR",
        str(cell / "plugins" / "agent-dispatch"),
    )
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(own_context))

    for marker in ("last-known-good", None):
        if marker is None:
            (sibling_root / "last-known-good").unlink()
        result = subprocess.run(
            [*procutil.agent_bridge_launch_prefix(), "probe"],
            capture_output=True, encoding="utf-8", timeout=15,
            **procutil.no_window_kwargs(),
        )
        assert result.returncode == 0, result.stderr
        assert Path(json.loads(result.stdout)["python"]) == python


def test_namespaced_sibling_resolution_rejects_linked_cell_hierarchy(
    tmp_path, monkeypatch
):
    durable = tmp_path / "durable"
    outside = tmp_path / "outside"
    real_cell = outside / FIRST_MARKETPLACE_ID
    own_context = _make_namespaced_context(real_cell)
    _make_namespaced_context(real_cell, plugin_id="agent-bridge")
    python = _make_slot(
        real_cell / "plugins" / "agent-bridge",
        "1.0.0-dev1",
        complete=True,
    )
    (real_cell / "plugins" / "agent-bridge" / "current-version").write_text(
        "1.0.0-dev1"
    )
    marketplaces = durable / "marketplaces"
    marketplaces.mkdir(parents=True)
    linked_cell = marketplaces / real_cell.name
    try:
        linked_cell.symlink_to(real_cell, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory links are unavailable: {error}")
    monkeypatch.setenv(
        "AGENT_DISPATCH_INSTALL_DIR",
        str(linked_cell / "plugins" / "agent-dispatch"),
    )
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(own_context))

    assert python.is_file()
    _assert_peer_refused(procutil.agent_bridge_launch_prefix())


def test_namespaced_sibling_resolution_rejects_linked_peer_root(
    tmp_path, monkeypatch
):
    cell = tmp_path / "durable" / "marketplaces" / FIRST_MARKETPLACE_ID
    own_context = _make_namespaced_context(cell)
    outside_root = tmp_path / "outside-agent-bridge"
    python = _make_slot(outside_root, "1.0.0-dev1", complete=True)
    (outside_root / "current-version").write_text("1.0.0-dev1")
    linked_root = cell / "plugins" / "agent-bridge"
    try:
        linked_root.symlink_to(outside_root, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory links are unavailable: {error}")
    monkeypatch.setenv(
        "AGENT_DISPATCH_INSTALL_DIR",
        str(cell / "plugins" / "agent-dispatch"),
    )
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(own_context))

    assert python.is_file()
    _assert_peer_refused(procutil.agent_bridge_launch_prefix())


def test_legacy_sibling_resolution_preserves_home_and_posix_path_fallback(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(procutil.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("AGENT_DISPATCH_INSTALL_DIR", raising=False)
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setattr(procutil.os, "name", "posix")
    python = _make_slot(tmp_path / ".agent-worktrees", "1.5.3-dev9")
    (tmp_path / ".agent-worktrees" / "current-version").write_text("1.5.3-dev9")

    assert procutil.agent_worktrees_launch_prefix() == [
        str(python),
        "-m",
        "agent_worktrees",
    ]

    (tmp_path / ".agent-worktrees" / "current-version").unlink()
    shutil.rmtree(tmp_path / ".agent-worktrees" / "versions")
    monkeypatch.setattr(
        procutil.shutil, "which", lambda _name: "/usr/bin/agent-worktrees"
    )
    assert procutil.agent_worktrees_launch_prefix() == [
        "/usr/bin/agent-worktrees"
    ]


def test_agent_worktrees_capture_preserves_posix_process_semantics(monkeypatch):
    captured = {}

    class FakeProc:
        returncode = 0

        def communicate(self, *, timeout):
            return "host-a\n", ""

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return FakeProc()

    monkeypatch.setattr(procutil.os, "name", "posix")
    monkeypatch.setattr(procutil.sys, "platform", "linux")
    monkeypatch.setattr(
        procutil, "agent_worktrees_launch_prefix",
        lambda: ["/usr/bin/agent-worktrees"],
    )
    monkeypatch.setattr(procutil.subprocess, "Popen", fake_popen)

    procutil.run_agent_worktrees_capture("get", "machine", timeout=15)

    assert captured["cmd"] == ["/usr/bin/agent-worktrees", "get", "machine"]
    assert "creationflags" not in captured["kwargs"]
    # Deliberate: a new session is required so a stalled probe's whole tree
    # (not just the immediate child) can be reaped via os.killpg on timeout --
    # see terminate_process_tree.
    assert captured["kwargs"]["start_new_session"] is True


def test_background_capture_reaps_tree_on_timeout(monkeypatch):
    class FakeProc:
        returncode = None

        def communicate(self, *, timeout):
            raise subprocess.TimeoutExpired("probe", timeout)

    fake_proc = FakeProc()
    reaped = []
    monkeypatch.setattr(procutil.subprocess, "Popen", lambda *_a, **_k: fake_proc)
    monkeypatch.setattr(
        procutil, "terminate_process_tree", lambda proc: reaped.append(proc)
    )

    assert procutil.run_background_capture(["probe"], timeout=3) is None
    assert reaped == [fake_proc]


def test_background_capture_returns_none_when_spawn_fails(monkeypatch):
    monkeypatch.setattr(
        procutil.subprocess,
        "Popen",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("spawn failed")),
    )
    assert procutil.run_background_capture(["probe"], timeout=3) is None


def test_ssh_subprocess_kwargs_uses_no_window_on_windows(monkeypatch):
    monkeypatch.setattr(procutil.sys, "platform", "win32")
    monkeypatch.setattr(procutil, "no_window_flags", lambda: 8)

    assert procutil.ssh_subprocess_kwargs() == {"creationflags": 8}


def test_ssh_capture_reaps_tree_on_timeout(monkeypatch):
    class FakeProc:
        returncode = None

        def communicate(self, *, input, timeout):
            raise subprocess.TimeoutExpired("ssh", timeout)

    fake_proc = FakeProc()
    reaped = []
    monkeypatch.setattr(procutil.subprocess, "Popen", lambda *_a, **_k: fake_proc)
    monkeypatch.setattr(
        procutil, "terminate_ssh_process_tree", lambda proc: reaped.append(proc)
    )

    assert procutil.run_ssh_capture(["ssh", "example"], timeout=3) is None
    assert reaped == [fake_proc]


def test_ssh_command_reaps_tree_on_keyboard_interrupt(monkeypatch):
    class FakeProc:
        returncode = None

        def communicate(self, *, input, timeout):
            raise KeyboardInterrupt

    fake_proc = FakeProc()
    reaped = []
    monkeypatch.setattr(procutil.subprocess, "Popen", lambda *_a, **_k: fake_proc)
    monkeypatch.setattr(
        procutil, "terminate_ssh_process_tree", lambda proc: reaped.append(proc)
    )

    with pytest.raises(KeyboardInterrupt):
        procutil.run_ssh_command(["ssh", "example"], timeout=3)

    assert reaped == [fake_proc]


def test_terminate_ssh_tree_ignores_already_exited_signal_race():
    class FakeProc:
        pid = None

        def poll(self):
            return None

        def terminate(self):
            raise ProcessLookupError

        def wait(self, timeout=None):
            return 0

    procutil.terminate_ssh_process_tree(FakeProc())


@pytest.mark.skipif(sys.platform != "win32", reason="Windows console integration")
@pytest.mark.parametrize(
    "capture",
    [procutil.run_background_capture, procutil.run_ssh_capture],
    ids=["no-window", "hidden-console"],
)
def test_capture_keeps_console_descendants_off_default_terminal(capture):
    """Both console strategies must suppress real console descendants."""
    git = shutil.which("git")
    if git is None:
        pytest.skip("git console executable is unavailable")

    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE

    def process_snapshot() -> dict[int, str]:
        snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
        if snapshot == wintypes.HANDLE(-1).value:
            return {}
        found: dict[int, str] = {}
        try:
            entry = ProcessEntry()
            entry.dwSize = ctypes.sizeof(entry)
            ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
            while ok:
                found[int(entry.th32ProcessID)] = entry.szExeFile
                ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            kernel32.CloseHandle(snapshot)
        return found

    def foreground_state() -> tuple[int, str]:
        hwnd = int(user32.GetForegroundWindow())
        length = user32.GetWindowTextLengthW(hwnd)
        title = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title, length + 1)
        return hwnd, title.value

    child_code = (
        "import subprocess,sys\n"
        "for _ in range(8):\n"
        " subprocess.run([sys.argv[1], '--version'], check=True)\n"
        "print('nested-ok', flush=True)\n"
    )
    baseline_processes = process_snapshot()
    baseline_foreground = foreground_state()
    result: dict[str, subprocess.CompletedProcess[str] | None] = {}

    def run_probe() -> None:
        result["value"] = capture([sys.executable, "-c", child_code, git], timeout=15)

    probe = threading.Thread(target=run_probe)
    probe.start()
    new_openconsole: set[int] = set()
    suspicious_titles: list[str] = []
    while probe.is_alive():
        new_openconsole.update(
            pid
            for pid, name in process_snapshot().items()
            if pid not in baseline_processes and name.lower() == "openconsole.exe"
        )
        state = foreground_state()
        if state != baseline_foreground and (
            "git.exe" in state[1].lower() or "cmd.exe" in state[1].lower()
        ):
            suspicious_titles.append(state[1])
        time.sleep(0.003)
    probe.join()

    completed = result["value"]
    assert completed is not None
    assert completed.returncode == 0
    assert completed.stdout.rstrip().endswith("nested-ok")
    assert completed.stderr == ""
    assert new_openconsole == set()
    assert suspicious_titles == []


def test_runtime_root_is_under_home_not_payload():
    root = procutil.runtime_root()
    assert root == Path.home() / ".agent-dispatch"
    # The runtime root must never be inside the Copilot plugin payload tree.
    assert "installed-plugins" not in root.parts


def test_relocate_off_payload_chdirs_to_runtime_root(tmp_path, monkeypatch):
    # Simulate a daemon lazy-started with the plugin payload as its CWD.
    payload = tmp_path / ".copilot" / "installed-plugins" / "x" / "agent-dispatch"
    payload.mkdir(parents=True)
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(procutil.Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.chdir(payload)
    assert Path.cwd() == payload

    procutil.relocate_off_payload()

    # It relocated OFF the payload to the runtime root (which it created).
    assert Path.cwd() == fake_home / ".agent-dispatch"
    assert "installed-plugins" not in Path.cwd().parts


def test_relocate_off_payload_is_best_effort(monkeypatch):
    # A chdir failure must never be fatal (the daemon still starts).
    monkeypatch.setattr(procutil.os, "chdir", lambda *_a, **_k: (_ for _ in ()).throw(OSError("boom")))
    procutil.relocate_off_payload()  # does not raise


# -- resolve_runtime_python (the standardized versioned-runtime resolver) -----


def _make_slot(root: Path, version: str, *, complete: bool = False) -> Path:
    """Create a ``versions/<version>`` slot with a fake interpreter; return it."""
    sub = "Scripts/python.exe" if procutil.os.name == "nt" else "bin/python"
    py = root / "versions" / version / sub
    py.parent.mkdir(parents=True)
    py.write_text("")
    if complete:
        (root / "versions" / version / ".install-complete.json").write_text(
            json.dumps(
                {
                    "version": version,
                    "completed_at": "2026-01-01T00:00:00Z",
                    "pid": 1,
                }
            )
        )
    return py


def test_resolve_runtime_python_tier1_current_version_marker(tmp_path):
    root = tmp_path / ".agent-bridge"
    py = _make_slot(root, "0.1.0-dev9")
    _make_slot(root, "0.1.0-dev99")  # newer slot exists but marker wins
    (root / "current-version").write_text("0.1.0-dev9")
    assert procutil.resolve_runtime_python(root) == py


def test_resolve_runtime_python_tier2_last_known_good(tmp_path):
    root = tmp_path / ".agent-bridge"
    py = _make_slot(root, "0.1.0-dev9")
    (root / "last-known-good").write_text("0.1.0-dev9")  # marker absent -> LKG
    assert procutil.resolve_runtime_python(root) == py


def test_resolve_runtime_python_tier3_prefers_newest_complete_slot(tmp_path):
    root = tmp_path / ".agent-worktrees"
    _make_slot(root, "1.5.3-dev50", complete=True)
    py_new = _make_slot(root, "1.5.3-dev185", complete=True)
    _make_slot(root, "1.5.3-dev200")  # newest but INCOMPLETE -> not preferred
    # No marker, no LKG: newest *complete* slot wins, numeric-aware (185 > 50).
    assert procutil.resolve_runtime_python(root) == py_new


def test_resolve_runtime_python_none_when_no_runtime(tmp_path):
    assert procutil.resolve_runtime_python(tmp_path / ".agent-bridge") is None


def test_resolve_runtime_python_ignores_venv_junction_layout(tmp_path):
    # A bare ``venv``/``.venv`` dir (the old hard-coded path) is NOT a versioned
    # slot, so it is never resolved -- the #974 regression guard.
    root = tmp_path / ".agent-bridge"
    (root / "venv" / "Scripts").mkdir(parents=True)
    (root / "venv" / "Scripts" / "python.exe").write_text("")
    assert procutil.resolve_runtime_python(root) is None


# -- resolve_own_runtime_python (this plugin's own canonical spawn target) ----


def test_resolve_own_runtime_python_uses_installed_slot(tmp_path, monkeypatch):
    """The canonical resolver's result must win over sys.executable: this is the
    exact fix for the production incident where a self-relaunch site fell back
    to whatever interpreter happened to be running instead of the installed
    current-version slot."""
    root = tmp_path / ".agent-dispatch"
    py = _make_slot(root, "0.1.2-dev49")
    (root / "current-version").write_text("0.1.2-dev49")
    monkeypatch.setattr("agent_dispatch.runtime_version.install_dir", lambda: root)
    assert procutil.resolve_own_runtime_python() == str(py)


def test_resolve_own_runtime_python_falls_back_to_sys_executable_when_unresolved(
    tmp_path, monkeypatch,
):
    """No installed runtime at all (e.g. a dev/test environment) must degrade to
    sys.executable rather than raise or return None -- callers need *some*
    interpreter to spawn."""
    root = tmp_path / ".agent-dispatch-empty"
    monkeypatch.setattr("agent_dispatch.runtime_version.install_dir", lambda: root)
    assert procutil.resolve_own_runtime_python() == procutil.sys.executable
