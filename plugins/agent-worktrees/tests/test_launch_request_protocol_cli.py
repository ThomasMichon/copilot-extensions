"""Native CLI protocol refusals stay structured and never touch launch state."""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

pytestmark = pytest.mark.guard


@pytest.mark.parametrize("protocol", [
    ["--launch-request-b64", "e30="],
    ["--launch-request-status", "a" * 32],
])
def test_protocol_machine_modifier_cli_refuses_without_state(tmp_path, protocol):
    home = tmp_path / "home"
    home.mkdir()
    environment = os.environ.copy()
    environment.update({
        "HOME": str(home), "USERPROFILE": str(home), "AGENT_HOME": str(home),
        "APPDATA": str(home / "appdata"), "LOCALAPPDATA": str(home / "localappdata"),
        "XDG_CONFIG_HOME": str(home / "config"), "PYTHONDONTWRITEBYTECODE": "1",
    })
    result = subprocess.run(
        [
            sys.executable, "-m", "agent_worktrees", "--project", "protocol-test",
            "resolve", "--json",
            *protocol, "--machine", "target",
        ],
        cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 3, result.stdout + result.stderr
    assert "Structured launch requests require local --json execution" in json.loads(
        result.stdout,
    )["error"]
    assert list(home.rglob("*")) == []
