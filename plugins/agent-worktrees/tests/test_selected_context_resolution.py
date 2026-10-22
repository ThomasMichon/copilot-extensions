"""Selected-context validation retains payload authority without a shell hop."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from agent_worktrees import installation_probe, reconcile
from test_reconcile import env as env


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("plugin", ["agent-index", "agent-machines"])
def test_selected_context_keeps_backend_arguments_and_root_routing(env, monkeypatch, native, plugin):
    if native and os.name != "nt":
        pytest.skip("Windows native compatibility route")
    selected = env.install_payload("agent-index", "2.0.0", scope="universal")
    payload = selected if plugin == "agent-index" else env.install_payload(
        plugin, "2.0.0", scope="universal",
    )
    context = (
        env.home / ".copilot-extensions" / "marketplaces" / reconcile.CORE_MARKETPLACE_ID
        / "plugins" / "agent-index" / "install.json"
    )
    context.parent.mkdir(parents=True)
    context.write_text(json.dumps({
        "marketplaceId": reconcile.CORE_MARKETPLACE_ID, "pluginId": "agent-index",
    }), encoding="utf-8")
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(context))
    monkeypatch.setenv("COPILOT_PLUGIN_ROOT", "foreign-payload")
    monkeypatch.setenv("PYTHONPATH", "foreign-imports")
    helper = payload / "scripts" / "installation-context" / "installation_context.py"
    if native:
        helper.unlink()
    root = env.home / "caf\u00e9"
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assert kwargs["timeout"] == 15
        assert kwargs["encoding"] == "utf-8"
        assert "PYTHONPATH" not in kwargs["env"]
        assert "COPILOT_PLUGIN_ROOT" not in kwargs["env"]
        if os.name == "nt":
            assert kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW
        return subprocess.CompletedProcess(
            command, 0, json.dumps({"pluginRoot": str(root)}, ensure_ascii=False), "",
        )

    monkeypatch.setattr(installation_probe.subprocess, "run", run)
    assert reconcile._selected_runtime_root(plugin, payload) == (
        (root, True) if plugin == "agent-index" else (env.home / f".{plugin}", False)
    )
    assert len(calls) == 1
    command = calls[0]
    if native:
        assert command[1:3] == ["-NoProfile", "-File"]
        assert command[3] == str(helper.with_name("installation-context.ps1"))
        assert command[4] == "resolve"
        context_flag, root_flag = "-Context", (
            "-PayloadRoot" if plugin == "agent-index" else "-ExpectedCellRoot"
        )
    else:
        assert command[:7] == [
            sys.executable, "-I", "-S", "-X", "utf8", str(helper), "resolve",
        ]
        context_flag, root_flag = "--context", (
            "--payload-root" if plugin == "agent-index" else "--expected-cell-root"
        )
    assert command[command.index(context_flag) + 1] == str(context)
    assert command[command.index(root_flag) + 1] == str(
        payload if plugin == "agent-index" else context.parents[2]
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows payload admission")
def test_portable_preference_does_not_waive_required_native_helper(env, monkeypatch):
    payload = env.install_payload("agent-index", "2.0.0", scope="universal")
    helpers = payload / "scripts" / "installation-context"
    (helpers / "installation-context.ps1").unlink()
    context = env.home / "install.json"
    context.write_text(json.dumps({
        "marketplaceId": reconcile.CORE_MARKETPLACE_ID, "pluginId": "agent-index",
    }), encoding="utf-8")
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(context))
    monkeypatch.setattr(
        installation_probe.subprocess, "run", lambda *args, **kwargs: pytest.fail("spawn"),
    )
    with pytest.raises(ValueError, match="no trusted installation-context validator"):
        reconcile._selected_runtime_root("agent-index", payload)


@pytest.mark.parametrize(
    ("code", "stdout", "stderr", "message"),
    [
        (23, "", "owner refused", "owner refused"),
        (3, '{"pluginRoot":"ignored"}', "", "pluginRoot"),
        (0, "not JSON", "", "invalid output"),
        (0, "[]", "", "invalid output"),
        (0, "null", "", "invalid output"),
    ],
)
def test_context_transport_never_turns_failure_into_a_root(monkeypatch, code, stdout, stderr, message):
    monkeypatch.setattr(
        installation_probe.subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], code, stdout, stderr),
    )
    with pytest.raises(ValueError, match=message):
        installation_probe.run_context_resolution(["helper"], {})


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (subprocess.TimeoutExpired(["helper"], 15), "validation timed out"),
        (OSError("unavailable"), "could not run"),
        (UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid"), "could not run"),
    ],
)
def test_context_transport_reports_unavailable_validator(monkeypatch, error, message):
    def run(*args, **kwargs):
        raise error
    monkeypatch.setattr(installation_probe.subprocess, "run", run)
    with pytest.raises(ValueError, match=message):
        installation_probe.run_context_resolution(["helper"], {})


def _snapshot(root):
    # Desktop PowerShell writes its own CLR JIT profile, not installation state.
    host_profile = (
        root / "AppData" / "Local" / "Microsoft" / "Windows" / "PowerShell"
        / "StartupProfileData-NonInteractive"
    )
    return {
        str(path.relative_to(root)): (
            path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in root.rglob("*") if path.is_file() and path != host_profile
    }


@pytest.mark.skipif(os.name != "nt", reason="real Windows compatibility boundary")
@pytest.mark.parametrize("scenario", ["own", "cross", "unicode", "tampered"])
@pytest.mark.parametrize("shell", ["pwsh", "powershell"])
def test_real_portable_context_resolution_matches_native_without_mutation(env, monkeypatch, scenario, shell):
    host = reconcile.shutil.which(shell)
    if host is None:
        pytest.skip(f"{shell} unavailable")
    original_home = env.home
    if scenario == "unicode":
        env.home = env.home / "caf\u00e9-\u6d4b\u8bd5"
        env.home.mkdir()
        monkeypatch.setattr(reconcile, "_home", lambda: env.home)
    monkeypatch.setenv("HOME", str(env.home))
    monkeypatch.setenv("USERPROFILE", str(env.home))
    for key in ("DOTNET_MultiCoreJitNoProfileGather", "COMPlus_MultiCoreJitNoProfileGather",
                "POWERSHELL_TELEMETRY_OPTOUT"):
        monkeypatch.setenv(key, "1")
    selected = env.install_payload("agent-index", "2.0.0", scope="universal")
    if scenario == "unicode":
        relocated = env.home / selected.relative_to(original_home)
        relocated.parent.mkdir(parents=True)
        selected.rename(relocated)
        selected = relocated
    native_run = subprocess.run
    with monkeypatch.context() as patch:
        patch.setattr(
            subprocess, "run",
            lambda command, **kwargs: native_run(command, **dict(kwargs, encoding="utf-8")),
        )
        context, root = env.select_context("agent-index", selected, "2.0.0", "2.0.0")
    plugin = "agent-machines" if scenario == "cross" else "agent-index"
    payload = selected if plugin == "agent-index" else env.install_payload(
        plugin, "2.0.0", scope="universal",
    )
    if scenario == "tampered":
        receipt = json.loads(context.read_text(encoding="utf-8"))
        Path(receipt["namespaceReceipt"]).write_text("invalid json\n", encoding="utf-8")
    if scenario == "unicode":
        monkeypatch.setattr(subprocess, "_text_encoding", lambda: "cp1252")
    before = _snapshot(env.home)

    def resolve():
        if scenario == "tampered":
            with pytest.raises(ValueError):
                reconcile._selected_runtime_root(plugin, payload)
            return None
        return reconcile._selected_runtime_root(plugin, payload)

    portable = resolve()
    assert _snapshot(env.home) == before
    helper = payload / "scripts" / "installation-context" / "installation_context.py"
    is_file = Path.is_file
    # Force only backend selection; both validators still read the same real fixture.
    with monkeypatch.context() as patch:
        patch.setattr(Path, "is_file", lambda path: False if path == helper else is_file(path))
        which = reconcile.shutil.which
        patch.setattr(
            reconcile.shutil, "which",
            lambda name: (host if name == shell else None) if name in {"pwsh", "powershell"} else which(name),
        )
        native = resolve()
    assert _snapshot(env.home) == before
    assert portable == native
    if scenario != "tampered":
        assert portable == (
            (root, True) if plugin == "agent-index" else (env.home / ".agent-machines", False)
        )
