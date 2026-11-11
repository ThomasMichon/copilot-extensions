"""Explicit native SDK provider lane using a prebuilt, locked application."""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import threading
import time

import pytest

from agent_procutil.owned_process import run_caller_owned
from agent_remote_driver.session_host.osutil import pid_alive, process_start_time
from test_headed_extension import _logs, offline_cli as offline_cli

pytestmark = pytest.mark.portfolio_tier("T3")


@pytest.mark.parametrize("mode", [
    "plain", "permission", "schedule", "cancel", "shell-pending", "agent-pending",
])
def test_native_sdk_provider_through_real_broker(tmp_path, offline_cli, mode):
    interpreter = os.environ.get("COPILOT_NATIVE_TEST_DRIVER_SDK_PYTHON")
    if not interpreter:
        pytest.skip("Select an already-built optional SDK application interpreter")
    assert Path(interpreter).is_absolute() and Path(interpreter).is_file()
    if mode in {"permission", "shell-pending"} and os.name != "nt":
        pytest.skip("Native PowerShell permission fixture requires Windows")
    if mode in {"permission", "schedule", "shell-pending", "agent-pending"}:
        command = "Write-Output 'SDK_PERMISSION_FIXTURE'"
        if mode == "shell-pending":
            command = (
                f"[System.IO.File]::WriteAllText('{offline_cli.workspace / 'shell-pid'}.pending', [string]$PID); "
                f"[System.IO.File]::Move('{offline_cli.workspace / 'shell-pid'}.pending', '{offline_cli.workspace / 'shell-pid'}'); "
                "$deadline = [DateTime]::UtcNow.AddSeconds(15); "
                f"while (-not (Test-Path -LiteralPath '{offline_cli.workspace / 'release-shell'}')) {{ "
                "if ([DateTime]::UtcNow -gt $deadline) { throw 'Pending shell was not observed' }; "
                "Start-Sleep -Milliseconds 50 }; Write-Output 'SDK_SHELL_SETTLED'"
            )
        offline_cli.env["DRIVER_FIXTURE_SHELL_COMMAND"] = command
        arguments = {
            "command": command, "description": "Bounded native permission fixture",
            "mode": "sync", "initial_wait": 30,
        } if mode in {"permission", "shell-pending"} else {
            "action": "create", "interval": "2h", "prompt": "ISOLATED_FUTURE_SCHEDULE_FIXTURE",
        }
        if mode == "agent-pending":
            arguments = {
                "name": "sdk-pending-fixture", "description": "Bounded native agent fixture",
                "prompt": "Return SDK_AGENT_SETTLED without using any tools.",
                "agent_type": "explore", "mode": "sync",
            }
            offline_cli.response_gate.select = lambda request: (
                offline_cli.release_response if any(
                    message["role"] == "user" and "SDK_AGENT_SETTLED" in str(message.get("content", ""))
                    for message in request["messages"]
                ) else None
            )
        offline_cli.responses.append({
            "delta": {"role": "assistant", "tool_calls": [{
                "index": 0, "id": "fixture-sdk-tool", "type": "function",
                "function": {
                    "name": (
                        "powershell" if mode in {"permission", "shell-pending"}
                        else "task" if mode == "agent-pending" else "manage_schedule"
                    ),
                    "arguments": json.dumps(arguments),
                },
            }]}, "finish_reason": "tool_calls",
        })
    if mode == "cancel":
        offline_cli.pause_response.set()
    settled = threading.Event()
    identities = []
    with ThreadPoolExecutor(max_workers=1) as executor:
        execution = executor.submit(
            run_caller_owned, [
                interpreter, str(Path(__file__).with_name("native_sdk_backend_probe.py")),
                offline_cli.executable, str(offline_cli.workspace), str(offline_cli.logs),
                str(tmp_path), mode,
            ], cwd=offline_cli.workspace, env=offline_cli.env, timeout=90,
            max_stdout_bytes=128_000, max_stderr_bytes=128_000, on_settled=settled.set,
        )
        try:
            deadline = time.monotonic() + 85
            pinned = set()
            while not execution.done() and time.monotonic() < deadline:
                for incarnation in range(2 if mode == "plain" else 1):
                    if incarnation in pinned:
                        continue
                    path = tmp_path / f"native-{incarnation}.json"
                    try:
                        identity = json.loads(path.read_text(encoding="utf-8"))
                    except (FileNotFoundError, json.JSONDecodeError):
                        continue
                    for pid in (identity["helperPid"], identity["pid"]):
                        if any(old == pid for old, _ in identities):
                            continue
                        birth = process_start_time(pid)
                        assert birth and pid_alive(pid)
                        identities.append((pid, birth))
                    (tmp_path / f"pinned-{incarnation}").write_text("pinned", encoding="utf-8")
                    pinned.add(incarnation)
                if (tmp_path / "tool-observed").exists() and not (tmp_path / "tool-pinned").exists():
                    if mode == "shell-pending":
                        pid = int((offline_cli.workspace / "shell-pid").read_text(encoding="utf-8"))
                        birth = process_start_time(pid)
                        assert birth and pid_alive(pid)
                        identities.append((pid, birth))
                    (tmp_path / "tool-pinned").write_text("pinned", encoding="utf-8")
                    offline_cli.release_response.set()
                time.sleep(.025)
            result = execution.result(timeout=15)
        finally:
            offline_cli.release_response.set()
            execution.result(timeout=100)
            assert settled.is_set()
            assert all(
                not pid_alive(pid) or (
                    (current := process_start_time(pid)) is not None and current != birth
                ) for pid, birth in identities
            ), identities
    assert result.returncode == 0, result.stdout + result.stderr + _logs(offline_cli.logs)
    observed = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert len(identities) == (3 if mode in {"plain", "shell-pending"} else 2)
    assert observed["permissions"] == (["shell"] if mode in {"permission", "shell-pending"} else [])
    for facts in observed["facts"]:
        assert facts["backend"] == "sdk" and facts["automatic_idle_retirement"] == "unavailable"
    if mode == "plain":
        requests = offline_cli.requests
        assert len(requests) == 3
        assert observed["observations"][0]["nonempty"]["tasks"]["items"][0]["type"] == "client"
        assert observed["observations"][1]["history"] >= 4
        assert "SDK_FIRST_LITERAL_INPUT" in json.dumps(requests[-1]["messages"])
    if mode.endswith("-pending"):
        assert observed["observations"][0]["pending"]["type"] == mode.split("-")[0]
        assert observed["observations"][0]["pending"]["status"] == "running"
