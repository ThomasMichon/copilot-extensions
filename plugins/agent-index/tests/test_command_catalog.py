"""Contract tests for the agent-index session command catalog."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]


def test_posix_catalog_uses_exact_payload_command() -> None:
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
    assert "\r" not in envelope["additionalContext"]
    match = re.search(r"```json\n(.*)\n```", envelope["additionalContext"])
    assert match
    catalog = json.loads(match.group(1))
    command = catalog["commands"][0]
    assert catalog["schema"] == "copilot-extensions.session-command-catalog"
    assert command["id"] == "agent-index"
    assert command["argv"] == [str(PLUGIN / "bin" / "agent-index")]
    assert command["availability"] == "ready"


def test_catalog_rejects_conflicting_payload_context() -> None:
    env = os.environ.copy()
    env["COPILOT_PLUGIN_ROOT"] = str(PLUGIN.parent)
    result = subprocess.run(
        ["bash", str(PLUGIN / "scripts" / "emit-command-catalog.sh")],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    assert json.loads(result.stdout) == {}


def test_powershell_catalog_declares_same_schema_and_command() -> None:
    source = (PLUGIN / "scripts" / "emit-command-catalog.ps1").read_text(
        encoding="utf-8"
    )
    assert "copilot-extensions.session-command-catalog" in source
    assert "bin\\agent-index.ps1" in source
    assert "COPILOT_PLUGIN_ROOT" in source


def test_powershell_catalog_uses_exact_payload_command() -> None:
    pwsh = shutil.which("pwsh")
    if not pwsh:
        return
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
    match = re.search(r"```json\n(.*)\n```", envelope["additionalContext"])
    assert match
    catalog = json.loads(match.group(1))
    command = catalog["commands"][0]
    assert command["argv"] == [str(PLUGIN / "bin" / "agent-index.ps1")]
    assert command["availability"] == "ready"


def test_hooks_use_runtime_payload_context_and_emit_catalog() -> None:
    hooks = json.loads((PLUGIN / "hooks.json").read_text(encoding="utf-8"))
    session_start = hooks["hooks"]["sessionStart"]
    assert any("emit-command-catalog.sh" in hook["bash"] for hook in session_start)
    assert any(
        "emit-command-catalog.ps1" in hook["powershell"] for hook in session_start
    )
    for hook in session_start:
        assert "COPILOT_PLUGIN_ROOT" in hook["bash"]
        assert "COPILOT_PLUGIN_ROOT" in hook["powershell"]
