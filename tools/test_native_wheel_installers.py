"""Native recipe admission before any installation effects."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(os.name != "nt", reason="native PowerShell installer arguments")
@pytest.mark.parametrize("plugin,entry,action", [
    (name, "install.ps1", "update") for name in (
        "budget-guidance", "agent-ssh", "agent-bridge", "agent-codespaces",
        "agent-dispatch", "agent-vault", "agent-index", "agent-pull-requests",
        "agent-logger", "agent-worktrees",
    )
] + [
    (name, "init.ps1", "install")
    for name in ("agent-containers", "agent-machines", "agent-mcp")
])
def test_incomplete_wheel_input_refuses_before_native_effects(tmp_path, plugin, entry, action):
    shell = shutil.which("powershell.exe")
    if shell is None:
        pytest.skip("native PowerShell unavailable")
    home = tmp_path / "home"
    home.mkdir()
    runtime = home / ("." + plugin)
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("AGENT_", "BUDGET_GUIDANCE_", "COPILOT_"))
    }
    environment.update(
        HOME=str(home), USERPROFILE=str(home), COPILOT_EXTENSIONS_TEST_CONTAINED="1",
    )
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-File",
         str(ROOT / "plugins" / plugin / "scripts" / entry),
         "-Action", action, "-InstallDir", str(runtime),
         "-WheelProfile", str(tmp_path / "not-selected.json")],
        cwd=home, env=environment, capture_output=True, text=True,
        timeout=20, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode != 0
    assert "Closed wheels require" in result.stderr
    assert not runtime.exists()
    assert not (home / ".local").exists()
