"""Exercise the launcher's post-exit return contract in a real PowerShell host."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from agent_procutil import no_window_kwargs


@pytest.mark.parametrize("exit_code", [0, 7, None])
def test_post_exit_returns_only_native_status(tmp_path, exit_code):
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if not shell:
        pytest.skip("PowerShell is unavailable")
    launcher = Path(__file__).resolve().parents[1] / "bin" / "launch-session.ps1"
    (tmp_path / "agent_worktrees.py").write_text(
        "import sys\n"
        "assert sys.argv[1:] == ['--project', 'fixture', 'post-exit', 'done']\n"
        "print('cleanup completed')\n"
        "print('fixture diagnostic', file=sys.stderr)\n"
        f"raise SystemExit({exit_code})\n",
        encoding="utf-8",
    )
    harness = tmp_path / "post-exit.ps1"
    harness.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "$PSNativeCommandUseErrorActionPreference = $false\n"
        "$tokens = $null; $errors = $null\n"
        "$ast = [Management.Automation.Language.Parser]::ParseFile("
        "$env:FIXTURE_LAUNCHER, [ref]$tokens, [ref]$errors)\n"
        "if ($errors.Count) { throw 'launcher parse failed' }\n"
        "$function = $ast.Find({ param($node) "
        "$node -is [Management.Automation.Language.FunctionDefinitionAst] "
        "-and $node.Name -eq 'Invoke-AwPostExit' }, $true)\n"
        ". ([scriptblock]::Create($function.Extent.Text))\n"
        "function Resolve-RuntimePython { return $env:FIXTURE_PYTHON }\n"
        "function Write-SetupLog { param($message, $level) }\n"
        "$script:LaunchProject = 'fixture'\n"
        "$result = Invoke-AwPostExit 'done'\n"
        "[Console]::WriteLine('RESULT:' + (ConvertTo-Json -Compress "
        "@{value=$result; type=$result.GetType().FullName}))\n",
        encoding="utf-8",
    )
    env = dict(
        os.environ,
        FIXTURE_LAUNCHER=str(launcher),
        FIXTURE_PYTHON=sys.executable if exit_code is not None else str(tmp_path / "missing.exe"),
        PYTHONPATH=str(tmp_path),
    )
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-File", str(harness)],
        cwd=tmp_path, env=env, capture_output=True, text=True,
        encoding="utf-8", timeout=20, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    receipt = next(line[7:] for line in result.stdout.splitlines() if line.startswith("RESULT:"))
    assert json.loads(receipt) == {"value": 1 if exit_code is None else exit_code, "type": "System.Int32"}
    if exit_code is None:
        assert "Post-exit finalization skipped" in result.stdout
        assert "cleanup completed" not in result.stdout
    else:
        assert "cleanup completed" in result.stdout
        assert "fixture diagnostic" in result.stderr
