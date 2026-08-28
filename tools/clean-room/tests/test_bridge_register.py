from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


_SCRIPT = Path(__file__).resolve().parents[1] / "bridge_register.py"


def _register(tmp_path: Path, *options: str) -> dict[str, object]:
    providers_dir = tmp_path / "providers.d"
    env = {
        **os.environ,
        "AGENT_BRIDGE_PROVIDERS_DIR": str(providers_dir),
    }
    result = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "--acp-command",
            "copilot --acp",
            *options,
            "register",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(
        (providers_dir / "cleanroom.json").read_text(encoding="utf-8")
    )


def test_register_omits_empty_acp_cwd_from_manifest_command(tmp_path: Path):
    manifest = _register(tmp_path)
    command = manifest["command"]

    assert isinstance(command, list)
    assert all(isinstance(value, str) and value for value in command)
    assert "--acp-cwd" not in command

    manifest = _register(tmp_path, "--acp-cwd", "/workspace")
    command = manifest["command"]
    cwd_index = command.index("--acp-cwd")
    assert command[cwd_index + 1] == "/workspace"
