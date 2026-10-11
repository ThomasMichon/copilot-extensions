from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
SCRIPT = PLUGIN / "scripts" / "post-tool-hook.ps1"


def test_post_hook_uses_same_shell_and_installs_the_client():
    hooks = json.loads((PLUGIN / "hooks.json").read_text(encoding="utf-8"))
    command = hooks["hooks"]["postToolUse"][0]["powershell"]
    assert ". $s; Invoke-PostToolHook -Raw $raw" in command
    assert "ReadToEnd()" in command
    assert "post-tool-hook.ps1" in command
    assert "$raw | & $py $fallback postToolUse" in command
    for name in ("install.ps1", "install.sh"):
        assert "post-tool-hook.ps1" in (PLUGIN / "scripts" / name).read_text(
            encoding="utf-8"
        )


def test_local_advisory_fallback_does_not_retry_daemon(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "post_tool_python_client", PLUGIN / "scripts" / "hook_client.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    calls = []
    monkeypatch.setattr(
        module, "_fallback_post",
        lambda payload, home: calls.append(payload) or {"additionalContext": "local"},
    )
    monkeypatch.setattr(
        module, "_request",
        lambda *args: pytest.fail("local advisory fallback must not resend"),
    )
    monkeypatch.setattr(module.sys, "stdin", io.StringIO('{"toolName":"view"}'))
    stdout = io.StringIO()
    monkeypatch.setattr(module.sys, "stdout", stdout)
    assert module.main(["postToolUse", "--local-fallback"]) == 0
    assert calls == [{"toolName": "view"}]
    assert json.loads(stdout.getvalue()) == {"additionalContext": "local"}


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("network")
@pytest.mark.effect("filesystem")
@pytest.mark.skipif(
    os.environ.get("AGENT_WORKTREES_SHELL_HOOK_INTEGRATION") != "1",
    reason="explicit native shell/daemon integration",
)
def test_real_powershell_roundtrip_and_validation_without_cli_children(tmp_path):
    from agent_procutil import no_window_kwargs
    from agent_worktrees.hook_ipc import HookIpcServer

    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        pytest.skip("PowerShell unavailable in explicit integration venue")
    seen = []
    def decide(kind, payload, deadline):
        seen.append((kind, payload))
        if payload.get("toolName") == "deny":
            return {"permissionDecision": "deny", "permissionDecisionReason": "policy"}
        return {"additionalContext": "resident"}

    server = HookIpcServer(decide)
    server.start()
    try:
        root = tmp_path / ".agent-worktrees"
        root.mkdir()
        (root / "status-monitor.lock").write_text(
            json.dumps(server.rendezvous()), encoding="utf-8"
        )
        harness = tmp_path / "hook.ps1"
        escaped = str(SCRIPT).replace("'", "''")
        harness.write_text(
            f". '{escaped}'\n"
            "function Get-Command { throw 'a client/CLI child was requested' }\n"
            "Invoke-PostToolHook -Raw '{\"toolName\":\"view\"}'\n"
            "Invoke-PostToolHook -Raw '{\"toolName\":\"view\"}'\n"
            "$deny = Invoke-ResidentPostTool -Endpoint "
            "(Get-Content (Join-Path $env:USERPROFILE '.agent-worktrees/status-monitor.lock') "
            "-Raw | ConvertFrom-Json | ForEach-Object {"
            "$h=@{}; foreach($p in $_.PSObject.Properties){$h[$p.Name]=$p.Value};$h}) "
            "-PayloadJson '{\"toolName\":\"deny\"}'\n"
            "if (($deny | ConvertFrom-Json).permissionDecision -ne 'deny') { throw 'denial lost' }\n"
            "$bad = @{hook_transport='tcp';hook_endpoint='127.0.0.1:70000';hook_token='x'}\n"
            "if ($null -ne (Invoke-ResidentPostTool $bad '{}')) { throw 'bad port accepted' }\n"
            "$bad.hook_endpoint='example.invalid:80'\n"
            "if ($null -ne (Invoke-ResidentPostTool $bad '{}')) { throw 'remote endpoint accepted' }\n",
            encoding="utf-8",
        )
        env = os.environ.copy()
        env["USERPROFILE"] = str(tmp_path)
        env.pop("COPILOT_EXTENSIONS_CONTEXT", None)
        result = subprocess.run(
            [shell, "-NoProfile", "-NonInteractive", "-File", str(harness)],
            capture_output=True, text=True, env=env, timeout=15,
            **no_window_kwargs(),
        )
        assert result.returncode == 0, result.stderr
        assert result.stderr == ""
        assert result.stdout == (
            '{"additionalContext":"resident"}'
            '{"additionalContext":"resident"}'
        )
        assert seen == [
            ("postToolUse", {"toolName": "view"}),
            ("postToolUse", {"toolName": "view"}),
            ("postToolUse", {"toolName": "deny"}),
        ]
    finally:
        server.close()
