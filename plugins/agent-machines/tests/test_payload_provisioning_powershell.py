"""Exercise the real payload dispatcher with isolated, offline installers."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest


PLUGIN = Path(__file__).resolve().parents[1]
HOSTS = [
    host for name in ("powershell.exe", "pwsh.exe")
    if (host := shutil.which(name))
] if os.name == "nt" else []


@pytest.mark.skipif(not HOSTS, reason="Windows PowerShell process boundary")
@pytest.mark.parametrize("host", HOSTS, ids=lambda host: Path(host).stem)
@pytest.mark.parametrize("mode", ["legacy", "namespaced"])
def test_first_use_preserves_installer_output_and_exit_codes(
    tmp_path: Path, host: str, mode: str,
) -> None:
    home = tmp_path / "home with spaces"
    payload = tmp_path / "payload with spaces"
    scripts = payload / "scripts"
    context_scripts = scripts / "installation-context"
    context_scripts.mkdir(parents=True)
    home.mkdir()
    runtime = home / (".agent-machines" if mode == "legacy" else "cell runtime")
    context = home / "cell context.json"
    dispatcher = scripts / "invoke-payload-runtime.ps1"
    shutil.copyfile(PLUGIN / "scripts" / dispatcher.name, dispatcher)
    (context_scripts / "installation-context.ps1").write_text(
        "[Console]::Out.WriteLine($env:FIXTURE_RESOLUTION)\nexit 0\n",
        encoding="utf-8",
    )
    (scripts / "resolve-runtime.ps1").write_text(
        "$AgentRtPy = $null\n"
        "if (Test-Path -LiteralPath (Join-Path $env:AGENT_RT_ROOT 'ready')) {\n"
        "    $AgentRtPy = $env:FIXTURE_PYTHON\n"
        "}\n",
        encoding="utf-8",
    )
    (scripts / "init.ps1").write_text(
        "param([string]$Action, [string]$Context, [string]$ExpectedMarketplaceId)\n"
        "$ErrorActionPreference = 'Stop'\n"
        "Add-Content -LiteralPath $env:FIXTURE_CALLS -Value $Action\n"
        "Remove-Item -LiteralPath $env:FIXTURE_PROGRESS_ACK -ErrorAction SilentlyContinue\n"
        "[Console]::Error.WriteLine('installer progress awaiting reader')\n"
        "$deadline = [DateTime]::UtcNow.AddSeconds(5)\n"
        "while (-not (Test-Path -LiteralPath $env:FIXTURE_PROGRESS_ACK)) {\n"
        "    if ([DateTime]::UtcNow -ge $deadline) { exit 91 }\n"
        "    Start-Sleep -Milliseconds 20\n"
        "}\n"
        "Write-Progress -Activity 'Preparing runtime' -Status 'First use'\n"
        # Simulate CLIXML mixed with native text; exceed pipe capacity on both streams.
        "[Console]::Error.WriteLine('#< CLIXML')\n"
        "foreach ($i in 1..220) {\n"
        "    [Console]::Error.WriteLine(\"stderr $i \" + ('x' * 1024))\n"
        "    [Console]::Out.WriteLine(\"stdout $i \" + ('x' * 1024))\n"
        "}\n"
        "[Console]::Error.WriteLine(\"native stderr: $Action\")\n"
        "[Console]::Out.WriteLine(\"installer stdout: $Action\")\n"
        "[Console]::Error.WriteLine('stderr caf' + [char]0xE9)\n"
        "[Console]::Out.WriteLine('stdout caf' + [char]0xE9)\n"
        "if ($Action -eq 'cell-provision' -and (\n"
        "    $Context -cne $env:FIXTURE_CONTEXT -or\n"
        "    $ExpectedMarketplaceId -cne 'example--0123456789abcdef'\n"
        ")) { exit 89 }\n"
        "if ($Action -eq $env:FIXTURE_FAIL_ACTION) {\n"
        "    [Console]::Error.Write('final diagnostic without newline')\n"
        "    exit 37\n"
        "}\n"
        "if ($Action -eq 'stamp') {\n"
        "    Set-Content -LiteralPath (Join-Path $env:AGENT_RT_ROOT 'payload-dir') "
        "-Value $env:AGENT_MACHINES_PAYLOAD_ROOT\n"
        "} else {\n"
        "    New-Item -ItemType Directory -Path $env:AGENT_RT_ROOT -Force | Out-Null\n"
        "    Set-Content -LiteralPath (Join-Path $env:AGENT_RT_ROOT 'ready') -Value ready\n"
        "}\n"
        "exit 0\n",
        encoding="utf-8",
    )
    modules = tmp_path / "modules"
    modules.mkdir()
    (modules / "agent_machines.py").write_text(
        "import json, sys\nprint(json.dumps(sys.argv[1:]))\n", encoding="utf-8",
    )
    temporary = tmp_path / "temporary"
    temporary.mkdir()
    calls = tmp_path / "calls"
    env = os.environ.copy()
    for key in (
        "COPILOT_EXTENSIONS_CONTEXT", "AGENT_MACHINES_NO_SELFPROVISION",
        "COPILOT_AGENT_SESSION_ID", "AGENT_WORKTREES_OWNER_REF",
    ):
        env.pop(key, None)
    env.update({
        "HOME": str(home), "USERPROFILE": str(home),
        "TEMP": str(temporary), "TMP": str(temporary),
        "AGENT_MACHINES_PAYLOAD_ROOT": str(payload),
        "FIXTURE_RESOLUTION": json.dumps({
            "status": "ready", "reason": (
                "policy-default-false" if mode == "legacy" else "namespaced-active"
            ),
            "actualMode": mode, "desiredMode": mode,
            "runtimeRoot": str(runtime), "context": str(context),
            "marketplaceId": "example--0123456789abcdef",
        }),
        "FIXTURE_CONTEXT": str(context), "FIXTURE_PYTHON": sys.executable,
        "FIXTURE_CALLS": str(calls), "PYTHONPATH": str(modules),
        "FIXTURE_PROGRESS_ACK": str(tmp_path / "progress-ack"),
    })
    launcher = tmp_path / "launch.ps1"
    launcher.write_text(
        "$previousEncoding = [Console]::OutputEncoding\n"
        "try {\n"
        "    [Console]::OutputEncoding = [Text.Encoding]::GetEncoding(437)\n"
        f"    & '{str(dispatcher).replace(chr(39), chr(39) * 2)}' @args\n"
        "    exit $LASTEXITCODE\n"
        "} finally {\n"
        "    [Console]::OutputEncoding = $previousEncoding\n"
        "}\n",
        encoding="utf-8",
    )
    command = [
        host, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", str(launcher), "version", "argument with spaces",
    ]
    actions = ["stamp", "provision"] if mode == "legacy" else ["cell-provision"]

    # Fail each provisioning stage, then retry successfully and take the warm path.
    for fail_action in [*actions, ""]:
        calls.unlink(missing_ok=True)
        env["FIXTURE_FAIL_ACTION"] = fail_action
        stderr_lines: list[str] = []
        with subprocess.Popen(
            command, env=env, cwd=tmp_path, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, encoding="cp437",
        ) as process:
            def read_progress() -> None:
                assert process.stderr is not None
                for line in process.stderr:
                    stderr_lines.append(line)
                    if "installer progress awaiting reader" in line:
                        Path(env["FIXTURE_PROGRESS_ACK"]).touch()

            reader = threading.Thread(target=read_progress, daemon=True)
            reader.start()
            try:
                process.wait(timeout=20)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                reader.join(timeout=5)
            assert not reader.is_alive()
            assert process.stdout is not None
            result = subprocess.CompletedProcess(
                command, process.returncode, process.stdout.read(), "".join(stderr_lines),
            )
        assert result.returncode == (37 if fail_action else 0), result.stderr
        assert "ProcessStreamReader_CliXmlError" not in result.stderr
        expected = actions[:actions.index(fail_action) + 1] if fail_action else actions
        assert calls.read_text(encoding="utf-8-sig").splitlines() == expected
        for action in expected:
            assert f"native stderr: {action}" in result.stderr
            assert f"installer stdout: {action}" in result.stderr
        for stream in ("stdout", "stderr"):
            for index in range(1, 221):
                assert result.stderr.count(f"{stream} {index} " + "x" * 1024) == len(expected)
        assert "stderr caf\u00e9" in result.stderr
        assert "stdout caf\u00e9" in result.stderr
        assert len(result.stderr.splitlines()) == 2 + 446 * len(expected) + bool(fail_action)
        assert list(temporary.iterdir()) == []
        if fail_action:
            assert result.stdout == ""
            assert result.stderr.endswith("final diagnostic without newline")
            assert not (runtime / "ready").exists()
        else:
            assert json.loads(result.stdout) == ["version", "argument with spaces"]

    calls.unlink()
    warm = subprocess.run(
        command, env=env, cwd=tmp_path, capture_output=True,
        encoding="cp437", timeout=20,
    )
    assert warm.returncode == 0, warm.stderr
    assert json.loads(warm.stdout) == ["version", "argument with spaces"]
    assert not calls.exists()
