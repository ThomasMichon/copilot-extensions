from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import socketserver
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
SCRIPT = PLUGIN / "scripts" / "post-tool-hook.ps1"


@pytest.mark.guard
def test_post_hook_uses_same_shell_and_installs_the_client():
    hooks = json.loads((PLUGIN / "hooks.json").read_text(encoding="utf-8"))
    command = hooks["hooks"]["postToolUse"][0]["powershell"]
    assert ". $s; Invoke-PostToolHook -Raw $raw" in command
    assert "ReadToEnd()" in command
    assert "OpenStandardInput()" in command
    assert "$OutputEncoding = $utf8" in command
    assert "post-tool-hook.ps1" in command
    assert "$raw | & $py $fallback postToolUse" in command
    for name in ("install.ps1", "install.sh"):
        assert "post-tool-hook.ps1" in (PLUGIN / "scripts" / name).read_text(
            encoding="utf-8"
        )
    assert "post-tool-hook.ps1" in (
        PLUGIN / "src" / "agent_worktrees" / "installer.py"
    ).read_text(encoding="utf-8")


@pytest.mark.guard
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

    shell = shutil.which(os.environ.get("AGENT_WORKTREES_TEST_SHELL", "pwsh"))
    shell = shell or shutil.which("powershell")
    if shell is None:
        pytest.skip("PowerShell unavailable in explicit integration venue")
    seen = []
    def decide(kind, payload, deadline):
        seen.append((kind, payload))
        if payload.get("toolName") == "empty":
            return {}
        if payload.get("toolName") == "deny":
            return {"permissionDecision": "deny", "permissionDecisionReason": "policy"}
        if payload.get("toolName") == "manifest":
            return {"additionalContext": payload["text"]}
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
            "if ($PSVersionTable.PSVersion.Major -lt 6 -and "
            "(Get-Module Microsoft.PowerShell.Utility)) {\n"
            "  throw 'ordinary post-tool JSON loaded the utility module'\n"
            "}\n"
            "Invoke-PostToolHook -Raw '{\"toolName\":\"view\"}'\n"
            "Invoke-PostToolHook -Raw '{\"toolName\":\"empty\"}'\n"
            "$deny = Invoke-ResidentPostTool -Endpoint "
            "(Get-Content (Join-Path $env:USERPROFILE '.agent-worktrees/status-monitor.lock') "
            "-Raw | ConvertFrom-Json | ForEach-Object {"
            "$h=@{}; foreach($p in $_.PSObject.Properties){$h[$p.Name]=$p.Value};$h}) "
            "-PayloadJson '{\"toolName\":\"deny\"}'\n"
            "if (($deny | ConvertFrom-Json).permissionDecision -ne 'deny') { throw 'denial lost' }\n"
            "$bad = @{hook_transport='tcp';hook_endpoint='127.0.0.1:70000';hook_token='x'}\n"
            "if ($null -ne (Invoke-ResidentPostTool $bad '{}')) { throw 'bad port accepted' }\n"
            "$bad.hook_endpoint='example.invalid:80'\n"
            "if ($null -ne (Invoke-ResidentPostTool $bad '{}')) { throw 'remote endpoint accepted' }\n"
            "function Invoke-ResidentPostTool { return $null }\n"
            "function Get-Command { [pscustomobject]@{Source='Invoke-FakePython'} }\n"
            "function Invoke-FakePython {\n"
            "  if ($args[-1] -ne '--local-fallback') { throw 'request would be resent' }\n"
            "  [Console]::Out.Write('local-fallback')\n"
            "}\n"
            "Invoke-PostToolHook -Raw '{\"toolName\":\"view\"}'\n"
            "$env:COPILOT_EXTENSIONS_CONTEXT='selected-cell'\n"
            "function Invoke-FakePython {\n"
            "  if ($args[-1] -ne 'postToolUse') { throw 'cell bypassed validated client' }\n"
            "  [Console]::Out.Write('cell-client')\n"
            "}\n"
            "Invoke-PostToolHook -Raw '{\"toolName\":\"view\"}'\n",
            encoding="utf-8",
        )
        env = os.environ.copy()
        env["USERPROFILE"] = str(tmp_path)
        env.pop("COPILOT_EXTENSIONS_CONTEXT", None)
        result = subprocess.run(
            [shell, "-NoProfile", "-NonInteractive", "-File", str(harness)],
            capture_output=True, text=True, encoding="utf-8", env=env, timeout=15,
            **no_window_kwargs(),
        )
        assert result.returncode == 0, result.stderr
        assert result.stderr == ""
        assert result.stdout == (
            '{"additionalContext":"resident"}'
            '{"additionalContext":"resident"}'
            'local-fallbackcell-client'
        )
        assert seen == [
            ("postToolUse", {"toolName": "view"}),
            ("postToolUse", {"toolName": "view"}),
            ("postToolUse", {"toolName": "empty"}),
            ("postToolUse", {"toolName": "deny"}),
        ]
        bin_dir = root / "bin"
        bin_dir.mkdir()
        shutil.copyfile(SCRIPT, bin_dir / SCRIPT.name)
        command = json.loads((PLUGIN / "hooks.json").read_text(encoding="utf-8"))[
            "hooks"
        ]["postToolUse"][0]["powershell"]
        manifest_harness = tmp_path / "manifest.ps1"
        manifest_harness.write_text(
            "function Get-Command { throw 'manifest requested a child client' }\n"
            + command,
            encoding="utf-8",
        )
        payload = {"toolName": "manifest", "text": "\u00e9\u4e2d\U0001f680"}
        manifest_result = subprocess.run(
            [shell, "-NoProfile", "-NonInteractive", "-File", str(manifest_harness)],
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True, text=True, encoding="utf-8", env=env, timeout=15,
            **no_window_kwargs(),
        )
        assert manifest_result.returncode == 0, manifest_result.stderr
        assert manifest_result.stderr == ""
        assert json.loads(manifest_result.stdout) == {"additionalContext": payload["text"]}
        assert seen[-1] == ("postToolUse", payload)
        (bin_dir / SCRIPT.name).unlink()
        (bin_dir / "hook_client.py").write_text(
            "import json,sys\n"
            "payload=json.loads(sys.stdin.buffer.read().decode('utf-8'))\n"
            "sys.stdout.buffer.write(json.dumps(payload,ensure_ascii=False).encode('utf-8'))\n",
            encoding="utf-8",
        )
        python = sys.executable.replace("'", "''")
        manifest_harness.write_text(
            f"function Get-Command {{ [pscustomobject]@{{Source='{python}'}} }}\n" + command,
            encoding="utf-8",
        )
        fallback_result = subprocess.run(
            [shell, "-NoProfile", "-NonInteractive", "-File", str(manifest_harness)],
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True, text=True, encoding="utf-8", env=env, timeout=15,
            **no_window_kwargs(),
        )
        assert fallback_result.returncode == 0, fallback_result.stderr
        assert fallback_result.stderr == ""
        assert json.loads(fallback_result.stdout) == payload
    finally:
        server.close()


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("network")
@pytest.mark.effect("filesystem")
@pytest.mark.skipif(
    os.environ.get("AGENT_WORKTREES_SHELL_HOOK_INTEGRATION") != "1",
    reason="explicit native shell/daemon integration",
)
def test_real_shell_json_values_and_legacy_metadata_compatibility(tmp_path):
    from agent_procutil import no_window_kwargs
    from agent_worktrees.hook_ipc import HookIpcServer

    shell = shutil.which(os.environ.get("AGENT_WORKTREES_TEST_SHELL", "pwsh"))
    shell = shell or shutil.which("powershell")
    if shell is None:
        pytest.skip("PowerShell unavailable in explicit integration venue")

    values = {
        "array": [{}, [], None, True, False, 42, 1.25, "\u00e9\u4e2d\U0001f680"],
        "integer": 9223372036854775807,
        "iso": "2026-01-02T03:04:05Z",
        "epoch": "/Date(0)/",
        "text": "quote'\"\\\n\t",
    }
    raw_payloads = [
        json.dumps(values),
        '{"__type":null,"value":42}',
        '{"nested":{"__type":null,"value":42}}',
        r'{"epoch":"\/Date(0)\/","offset":"\/Date(0+0500)\/"}',
        '{"small":1e-100,"big":1.234567890123456789e30,'
        '"long":0.12345678901234567890123456789}',
        json.dumps({"nested": {"__type": "ordinary-data", "value": values}}),
    ]
    seen = []

    def decide(kind, payload, deadline):
        seen.append((kind, payload))
        return payload

    server = HookIpcServer(decide)
    server.start()
    try:
        root = tmp_path / ".agent-worktrees"
        root.mkdir()
        (root / "status-monitor.lock").write_text(
            json.dumps(server.rendezvous()), encoding="utf-8"
        )
        harness = tmp_path / "values.ps1"
        escaped = str(SCRIPT).replace("'", "''")
        env = os.environ.copy()
        env["USERPROFILE"] = str(tmp_path)
        env.pop("COPILOT_EXTENSIONS_CONTEXT", None)
        reference_harness = tmp_path / "legacy-json.ps1"
        expected_requests = []
        for payload in raw_payloads:
            raw = payload.replace("'", "''")
            harness.write_text(
                "$ErrorActionPreference='Stop'\n"
                f". '{escaped}'\n"
                "function Get-Command { throw 'a client/CLI child was requested' }\n"
                f"Invoke-PostToolHook -Raw '{raw}'\n",
                encoding="utf-8",
            )
            reference_harness.write_text(
                "$ErrorActionPreference='Stop'\n"
                "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false,$true)\n"
                f"$request=ConvertFrom-Json -InputObject '{raw}'\n"
                "$wire=ConvertTo-Json -InputObject $request -Depth 100 -Compress\n"
                "[Console]::Out.WriteLine($wire)\n"
                "$reply=ConvertFrom-Json -InputObject $wire\n"
                "[Console]::Out.WriteLine((ConvertTo-Json -InputObject $reply -Depth 100 -Compress))\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                [shell, "-NoProfile", "-NonInteractive", "-File", str(harness)],
                capture_output=True, text=True, encoding="utf-8", env=env, timeout=15,
                **no_window_kwargs(),
            )
            assert result.returncode == 0, result.stderr
            assert result.stderr == ""
            legacy = subprocess.run(
                [shell, "-NoProfile", "-NonInteractive", "-File", str(reference_harness)],
                capture_output=True, text=True, encoding="utf-8", env=env, timeout=15,
                **no_window_kwargs(),
            )
            assert legacy.returncode == 0, legacy.stderr
            assert legacy.stderr == ""
            expected = [json.loads(line) for line in legacy.stdout.splitlines()]
            assert len(expected) == 2
            assert json.loads(result.stdout) == expected[1], payload
            expected_requests.append(("postToolUse", expected[0]))
        assert seen == expected_requests
    finally:
        server.close()


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("network")
@pytest.mark.effect("filesystem")
@pytest.mark.skipif(
    os.environ.get("AGENT_WORKTREES_SHELL_HOOK_INTEGRATION") != "1",
    reason="explicit native shell/daemon integration",
)
def test_real_shell_rejects_invalid_or_unbounded_responses(tmp_path):
    from agent_procutil import no_window_kwargs

    shell = shutil.which(os.environ.get("AGENT_WORKTREES_TEST_SHELL", "pwsh"))
    shell = shell or shutil.which("powershell")
    if shell is None:
        pytest.skip("PowerShell unavailable in explicit integration venue")

    requests = []

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            request = json.loads(self.rfile.readline())
            mode = request["payload"]["mode"]
            requests.append(mode)
            responses = {
                "version": b'{"version":2,"result":{}}\n',
                "fallback": b'{"version":1,"fallback":true}\n',
                "shape": b'{"version":1,"result":[]}\n',
                "malformed": b'{"version":1,"result":{bad}}\n',
                "oversized": b"x" * 65536 + b"\n",
                "unterminated": b'{"version":1,"result":{}}',
            }
            if mode == "timeout":
                time.sleep(0.8)
                return
            self.wfile.write(responses[mode])

    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        harness = tmp_path / "responses.ps1"
        root = tmp_path / ".agent-worktrees"
        root.mkdir()
        (root / "status-monitor.lock").write_text(json.dumps({
            "hook_transport": "tcp",
            "hook_endpoint": f"127.0.0.1:{server.server_address[1]}",
            "hook_token": "fixture",
        }), encoding="utf-8")
        escaped = str(SCRIPT).replace("'", "''")
        harness.write_text(
            f". '{escaped}'\n"
            f"$endpoint=@{{hook_transport='tcp';hook_endpoint='127.0.0.1:{server.server_address[1]}';hook_token='fixture'}}\n"
            "foreach($mode in @('version','fallback','shape','oversized','unterminated')) {\n"
            "  $payload=ConvertTo-Json @{mode=$mode} -Compress\n"
            "  if($null -ne (Invoke-ResidentPostTool $endpoint $payload)){throw \"$mode accepted\"}\n"
            "}\n"
            "$timer=[Diagnostics.Stopwatch]::StartNew()\n"
            "try { $null=Invoke-ResidentPostTool $endpoint '{\"mode\":\"timeout\"}' } catch [IO.IOException] {}\n"
            "if($timer.Elapsed.TotalSeconds -gt 2){throw 'unbounded response wait'}\n"
            "function Get-Command { [pscustomobject]@{Source='Invoke-FakePython'} }\n"
            "function Invoke-FakePython {\n"
            "  if ($args[-1] -ne '--local-fallback') { throw 'request would be resent' }\n"
            "  [Console]::Out.Write('local-fallback')\n"
            "}\n"
            "Invoke-PostToolHook -Raw '{\"mode\":\"malformed\"}'\n"
            "$endpoint.installReceipt='another-cell'\n"
            "if($null -ne (Invoke-ResidentPostTool $endpoint '{}')){throw 'foreign cell accepted'}\n",
            encoding="utf-8",
        )
        env = os.environ.copy()
        env["USERPROFILE"] = str(tmp_path)
        env.pop("COPILOT_EXTENSIONS_CONTEXT", None)
        result = subprocess.run(
            [shell, "-NoProfile", "-NonInteractive", "-File", str(harness)],
            capture_output=True, text=True, env=env, timeout=15, **no_window_kwargs(),
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == "local-fallback"
        assert result.stderr == (
            "agent-worktrees postToolUse: resident request unavailable; using local advisory fallback\n"
        )
        assert requests.count("malformed") == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
