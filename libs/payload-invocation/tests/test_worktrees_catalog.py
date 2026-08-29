"""Contract tests for the agent-worktrees payload command catalog."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
PLUGIN = REPO / "plugins" / "agent-worktrees"


def _catalog(context: str) -> dict:
    match = re.search(r"```json\n(.*)\n```", context)
    assert match
    return json.loads(match.group(1))


@pytest.mark.skipif(os.name == "nt", reason="POSIX catalog test")
def test_posix_catalog_uses_nested_payload_command() -> None:
    env = os.environ.copy()
    env["COPILOT_PLUGIN_ROOT"] = str(PLUGIN)
    result = subprocess.run(
        ["bash", str(PLUGIN / "scripts" / "emit-command-catalog.sh")],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    envelope = json.loads(result.stdout)
    command = _catalog(envelope["additionalContext"])["commands"][0]
    assert command["argv"] == [
        str(PLUGIN / "bin" / "payload" / "agent-worktrees")
    ]
    assert command["availability"] == "ready"


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh is not installed")
def test_powershell_catalog_uses_nested_payload_command() -> None:
    pwsh = shutil.which("pwsh")
    assert pwsh
    env = os.environ.copy()
    env["COPILOT_PLUGIN_ROOT"] = str(PLUGIN)
    result = subprocess.run(
        [
            pwsh,
            "-NoProfile",
            "-File",
            str(PLUGIN / "scripts" / "emit-command-catalog.ps1"),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    envelope = json.loads(result.stdout)
    command = _catalog(envelope["additionalContext"])["commands"][0]
    assert Path(command["argv"][0]).is_absolute()
    assert command["argv"][1:5] == [
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
    ]
    assert command["argv"][-1] == str(
        PLUGIN / "bin" / "payload" / "agent-worktrees.ps1"
    )
    assert command["shell"] == "direct"
    assert command["availability"] == "ready"


def test_hooks_emit_payload_command_catalog() -> None:
    hooks = json.loads((PLUGIN / "hooks.json").read_text(encoding="utf-8"))
    session_start = hooks["hooks"]["sessionStart"]
    catalog = next(
        hook for hook in session_start if "emit-command-catalog" in hook["bash"]
    )
    assert "COPILOT_PLUGIN_ROOT" in catalog["bash"]
    assert "COPILOT_PLUGIN_ROOT" in catalog["powershell"]
    assert "printf '{}'" in catalog["bash"]
    assert "Write('{}')" in catalog["powershell"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX payload command test")
def test_posix_nested_payload_command_resolves_plugin_root(tmp_path: Path) -> None:
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(tmp_path),
            "COPILOT_PLUGIN_ROOT": str(PLUGIN),
            "AGENT_WORKTREES_NO_SELFPROVISION": "1",
        }
    )
    result = subprocess.run(
        [str(PLUGIN / "bin" / "payload" / "agent-worktrees"), "status"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "payload context mismatch" not in result.stderr
    assert "runtime not provisioned" in result.stderr


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh is not installed")
def test_powershell_nested_payload_command_resolves_plugin_root(
    tmp_path: Path,
) -> None:
    pwsh = shutil.which("pwsh")
    assert pwsh
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(tmp_path),
            "USERPROFILE": str(tmp_path),
            "COPILOT_PLUGIN_ROOT": str(PLUGIN),
            "AGENT_WORKTREES_NO_SELFPROVISION": "1",
        }
    )
    result = subprocess.run(
        [
            pwsh,
            "-NoProfile",
            "-File",
            str(PLUGIN / "bin" / "payload" / "agent-worktrees.ps1"),
            "status",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "payload context mismatch" not in result.stderr
    assert "runtime not provisioned" in result.stderr
