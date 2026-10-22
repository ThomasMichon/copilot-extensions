"""Run installer identity output before prerequisite admission, without deployment."""

from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPTS = REPO / "plugins" / "agent-bridge" / "scripts"


@pytest.mark.parametrize("action", ["install", "update"])
@pytest.mark.parametrize("shell", ["bash", "pwsh", "powershell"])
def test_installer_identity_is_one_progress_line(tmp_path, monkeypatch, action, shell):
    if shell == "bash" and os.name == "nt":
        pytest.skip("exercise the POSIX installer on a POSIX host")
    executable = shutil.which(shell)
    if not executable:
        pytest.skip(f"{shell} unavailable")
    powershell = shell != "bash"
    suffix = ".ps1" if powershell else ".sh"
    source = (SCRIPTS / ("install" + suffix)).read_text(encoding="utf-8")
    name = "Invoke-" + action.title() if powershell else "do_" + action
    declaration = "function " + name if powershell else name + r"\(\)"
    body = re.search(rf"^{declaration} \{{.*?^\}}$", source, re.M | re.S)
    assert body is not None
    helper = next(
        line for line in source.splitlines()
        if line.startswith("function Write-Step " if powershell else "_step()")
    )
    if powershell:
        script = (
            "$ErrorActionPreference = 'Stop'\n"
            + helper + "\n"
            + "function Ensure-Uv {\n"
            + "Write-Host '=== prerequisite diagnostic ==='\n"
            + "Write-Host ''\nreturn $false\n}\n"
            + body[0] + "\n" + name
        )
        argv = [executable, "-NoProfile", "-NonInteractive", "-File"]
    else:
        script = (
            "set -euo pipefail\n" + helper + "\n"
            + "_ensure_uv() { printf '%s\\n' '=== prerequisite diagnostic ===' ''; return 1; }\n"
            + body[0] + "\n" + name
        )
        argv = [executable]
    harness = tmp_path / ("installer" + suffix)
    harness.write_text(script, encoding="utf-8")
    argv.append(str(harness))
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    standalone = subprocess.run(
        argv, capture_output=True, text=True, timeout=10, check=False, **options,
    )
    assert standalone.returncode == 1, standalone.stderr
    assert standalone.stderr == ""
    assert standalone.stdout.splitlines() == [
        f"  ...    agent-bridge {action}", "=== prerequisite diagnostic ===", "",
    ]

    monkeypatch.syspath_prepend(str(REPO / "libs" / "update-telemetry" / "src"))
    from update_telemetry.human import HumanOperation, native_output

    output = io.StringIO()
    with HumanOperation("installer", stream=output) as operation:
        with native_output() as sink:
            result = subprocess.run(
                argv, stdout=sink, stderr=sink, timeout=10, check=False, **options,
            )
        operation.complete("failed", f"exit {result.returncode}")
    assert result.returncode == 1
    lines = output.getvalue().splitlines()
    assert len(lines) == 5, lines
    assert re.fullmatch(
        rf"\[\d{{2}}:\d{{2}}:\d{{2}}\.\d{{3}}Z\] installer running "
        rf"agent-bridge {action} \[elapsed \d+\.\d+s\]", lines[1],
    )
    assert "installer === prerequisite diagnostic === [elapsed " in lines[2]
    assert re.fullmatch(
        r"\[\d{2}:\d{2}:\d{2}\.\d{3}Z\] installer \[elapsed \d+\.\d+s\]", lines[3],
    )
    assert "installer failed exit 1 [duration " in lines[4]
