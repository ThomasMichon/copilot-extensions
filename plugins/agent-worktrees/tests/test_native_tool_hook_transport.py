from __future__ import annotations

import importlib.util
import ctypes
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from agent_procutil import no_window_kwargs
from agent_worktrees.hook_ipc import HookIpcServer
from agent_worktrees.native_tool_policy import POLICY_SWITCHES, decide_pre

PLUGIN = Path(__file__).resolve().parents[1]
pytestmark = [
    pytest.mark.portfolio_tier("T2"),
    pytest.mark.effect("process"),
    pytest.mark.effect("filesystem"),
    pytest.mark.effect("network"),
    pytest.mark.skipif(os.name != "nt", reason="Native Windows command-hook contract"),
]


def test_original_guards_execute_without_any_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("native policy started a process for an ordinary tool")

    def load(name: str) -> ModuleType:
        spec = importlib.util.spec_from_file_location(name, PLUGIN / "scripts" / name)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("AGENT_HOME", str(tmp_path))
    client = load("hook_client.py")
    for tool, command in (
        ("view", ""), ("edit", ""), ("powershell", "Get-Date"),
        ("powershell", "gh pr close 42 --repo example/project"),
    ):
        result = decide_pre(
            {
                "toolName": tool, "cwd": str(tmp_path),
                "toolArgs": {"path": "sample", "command": command},
                "_agentWorktrees": {"nativePolicy": 1, "environment": dict.fromkeys(POLICY_SWITCHES)},
            },
            load_sibling=load, merge=client._merge_pre_decisions, deadline=time.time() + 0.5,
        )
        assert result.get("permissionDecision") != "allow"
        assert "evaluation failed" not in result.get("additionalContext", "")


@pytest.mark.parametrize("engine", ["powershell.exe", "pwsh"])
@pytest.mark.parametrize("kind", ["preToolUse", "postToolUse"])
@pytest.mark.parametrize("case", ["warm", "large", "large-html", "cold", "unsupported", "closed", "invalid", "timeout"])
def test_command_entry_never_launches_python(
    tmp_path: Path, engine: str, kind: str, case: str,
    record_property: Any,
) -> None:
    shell = shutil.which(engine)
    if not shell:
        pytest.skip(f"{engine} is unavailable")
    scripts = tmp_path / "payload" / "scripts"
    scripts.mkdir(parents=True)
    for name in ("pre-tool-hook.ps1", "post-tool-hook.ps1", "managed-session.ps1", "hook-facts.ps1"):
        shutil.copyfile(PLUGIN / "scripts" / name, scripts / name)
    # Force cold native membership. Calling the compatibility admission is a regression.
    with (scripts / "managed-session.ps1").open("a", encoding="utf-8") as output:
        output.write(
            "\nfunction Test-PreparedManagedWorktreesSession { return $false }\n"
            "function Test-ManagedWorktreesSession { throw 'compatibility admission requested' }\n"
        )
    sentinel = tmp_path / "python-ran"
    for name in ("hook_policy_process.py", "hook_client.py", "managed_session.py"):
        (scripts / name).write_text(
            f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('started')\n",
            encoding="utf-8",
        )
    home = tmp_path / "home"
    registry = home / ".agent-worktrees"
    registry.mkdir(parents=True)
    seen: list[tuple[str, dict[str, Any]]] = []

    def decide(operation: str, payload: dict[str, Any], deadline: float) -> dict[str, Any]:
        seen.append((operation, payload))
        if case in {"large", "large-html"}:
            def sibling(name: str) -> ModuleType:
                if name in {"tool_call_forms.py", "hook_pre_policy.py", "hook_client.py"}:
                    spec = importlib.util.spec_from_file_location(name, PLUGIN / "scripts" / name)
                    assert spec and spec.loader
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    return module
                module = ModuleType(name)
                module.decide = lambda *_args, **_kwargs: {  # type: ignore[attr-defined]
                    "additionalContext": (
                        "notice <>&'\"" if case == "large-html" else "notice \U0001f680"
                    ) * 12000,
                    "permissionDecision": "deny", "permissionDecisionReason": "protected",
                }
                return module

            bounded = decide_pre(
                payload, load_sibling=sibling,
                merge=sibling("hook_client.py")._merge_pre_decisions, deadline=deadline,
            )
            return bounded if kind == "preToolUse" else {
                "additionalContext": bounded["additionalContext"],
            }
        if case == "timeout":
            time.sleep(0.65)
        if case == "invalid":
            return (
                {"permissionDecision": ["allow"]}
                if kind == "preToolUse" else {"additionalContext": []}
            )
        return (
            {"permissionDecision": "deny", "permissionDecisionReason": "resident refusal \u00e9"}
            if kind == "preToolUse" else {"additionalContext": "resident accounting"}
        )

    server = HookIpcServer(decide)
    server.start()
    try:
        discovery = server.rendezvous()
        if case == "unsupported":
            discovery["hook_capabilities"] = []
        if case != "cold":
            (registry / "status-monitor.lock").write_text(json.dumps(discovery), encoding="utf-8")
        if case == "closed":
            server.close_admission()
        environment = dict(os.environ)
        for key in (
            "COPILOT_EXTENSIONS_CONTEXT", "AGENT_WORKTREES_PRE_TOOL_IMPLEMENTATION",
            "AGENT_HOME", "PRETOOL_KILLSWITCH_STATE_FILE",
        ):
            environment.pop(key, None)
        environment.update(
            HOME=str(home), USERPROFILE=str(home), COPILOT_PLUGIN_ROOT=str(scripts.parent),
        )
        command = json.loads((PLUGIN / "hooks.json").read_text())["hooks"][kind][0]["powershell"]
        harness = tmp_path / "entry.ps1"
        harness.write_text(
            "function Get-Command { throw 'executable lookup requested' }\n" + command,
            encoding="utf-8",
        )
        started = time.monotonic()
        result = subprocess.run(
            [shell, "-NoProfile", "-NonInteractive", "-File", str(harness)],
            input=json.dumps({"sessionId": "fixture", "cwd": str(home), "toolName": "edit"}),
            encoding="utf-8", capture_output=True, timeout=10, cwd=home, env=environment,
            **no_window_kwargs(),
        )
        record_property("entry_seconds", time.monotonic() - started)
        assert result.returncode == 0, result.stderr
        assert not sentinel.exists(), "a direct or membership Python fallback ran"
        if case in {"warm", "large", "large-html"}:
            assert len(seen) == 1
            assert seen[0][0] == kind
            metadata = seen[0][1]["_agentWorktrees"]
            assert metadata["nativePolicy"] == 1
            if kind == "preToolUse":
                assert type(metadata["callerElevated"]) is bool
                assert metadata["callerElevated"] == bool(
                    getattr(ctypes, "windll").shell32.IsUserAnAdmin(),
                )
            else:
                assert "callerElevated" not in metadata
            if case in {"large", "large-html"}:
                response = json.loads(result.stdout)
                assert len(result.stdout.encode("utf-8")) <= 65536
                assert "truncat" in response["additionalContext"]
                prefix = "notice <>&'\"" if case == "large-html" else "notice \U0001f680"
                assert response["additionalContext"].startswith(prefix)
                if kind == "preToolUse":
                    assert response["permissionDecision"] == "deny"
                    assert response["permissionDecisionReason"] == "protected"
            elif kind == "preToolUse":
                assert json.loads(result.stdout) == {
                    "permissionDecision": "deny", "permissionDecisionReason": "resident refusal \u00e9",
                }
            else:
                assert json.loads(result.stdout)["additionalContext"] == "resident accounting"
        elif kind == "preToolUse":
            response = json.loads(result.stdout)
            assert "permissionDecision" not in response
            assert "not authorization" in response["additionalContext"]
        else:
            assert "unconfirmed" in result.stderr
        if case in {"cold", "unsupported", "closed"}:
            assert not seen
        else:
            assert len(seen) == 1
    finally:
        server.close()


@pytest.mark.parametrize("engine", ["powershell.exe", "pwsh"])
@pytest.mark.parametrize("case", ["warm", "refused", "timeout"])
def test_native_connect_does_not_use_expected_socket_exceptions(
    tmp_path: Path, engine: str, case: str,
) -> None:
    shell = shutil.which(engine)
    if not shell:
        pytest.skip(f"{engine} is unavailable")
    seen: list[str] = []

    def decide(operation: str, payload: dict[str, Any], deadline: float) -> dict[str, Any]:
        seen.append(operation)
        if case == "timeout":
            time.sleep(0.65)
        return {"permissionDecision": "deny", "permissionDecisionReason": "fixture refusal"}

    server = HookIpcServer(decide)
    server.start()
    try:
        discovery = server.rendezvous()
        if case == "refused":
            server.close()
        source = str(PLUGIN / "scripts" / "post-tool-hook.ps1").replace("'", "''")
        endpoint = json.dumps(discovery).replace("'", "''")
        harness = tmp_path / "connect.ps1"
        harness.write_text(
            f". '{source}'\n"
            f"$endpoint = ConvertFrom-PostToolJson '{endpoint}'\n"
            "$Error.Clear()\n"
            "$result = $null\n"
            "try {\n"
            "    $result = Invoke-ResidentPostTool -Endpoint $endpoint "
            "-PayloadJson '{\"toolName\":\"edit\"}' -Kind preToolUse\n"
            "} catch {}\n"
            "$errors = $Error.Count\n"
            "ConvertTo-Json -Compress @{ result = $result; errors = $errors }\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            [shell, "-NoProfile", "-NonInteractive", "-File", str(harness)],
            encoding="utf-8", capture_output=True, timeout=10, cwd=tmp_path,
            **no_window_kwargs(),
        )
        assert result.returncode == 0, result.stderr
        reply = json.loads(result.stdout)
        if case == "warm":
            assert json.loads(reply["result"]) == {
                "permissionDecision": "deny", "permissionDecisionReason": "fixture refusal",
            }
            assert reply["errors"] == 0
        else:
            assert reply["result"] is None
        assert seen == ([] if case == "refused" else ["preToolUse"])
    finally:
        server.close()
