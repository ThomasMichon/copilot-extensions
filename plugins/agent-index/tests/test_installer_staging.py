from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest

PLUGIN = Path(__file__).resolve().parents[1]


@pytest.mark.guard
@pytest.mark.parametrize("extension", ["ps1", "sh"])
def test_read_only_and_cell_actions_never_allocate_legacy_stage(
    tmp_path: Path, extension: str,
) -> None:
    interpreter = shutil.which("pwsh" if extension == "ps1" else "bash")
    if extension == "sh" and os.name == "nt":
        git_bash = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe"
        interpreter = str(git_bash) if git_bash.is_file() else None
    if not interpreter:
        pytest.skip(f"{extension} interpreter unavailable")
    home = tmp_path / "home"
    home.mkdir()
    payload = tmp_path / ".copilot" / "installed-plugins" / "example" / "agent-index"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    (payload / "plugin.json").write_text('{"name":"agent-index"}\n', encoding="utf-8")
    text = (PLUGIN / "scripts" / f"install.{extension}").read_text(encoding="utf-8")
    start = text.index("# Status and dependency-light cell-slot actions")
    end = text.index("# === install-contract:v4 smoke seam", start)
    header = (
        "param([string]$Action)\n$ErrorActionPreference = 'Stop'\n"
        if extension == "ps1"
        else '#!/usr/bin/env bash\nset -euo pipefail\n__legacy_action="$1"\n'
    )
    entry = scripts / f"install.{extension}"
    entry.write_text(header + text[start:end], encoding="utf-8", newline="\n")
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("COPILOT_", "AGENT_"))
    }
    environment.update({"HOME": str(home), "USERPROFILE": str(home)})
    command = (
        [interpreter, "-NoProfile", "-File", str(entry)]
        if extension == "ps1"
        else [interpreter, entry.as_posix()]
    )
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    for action in ("status", "slot-provision"):
        for stage_flag in ("", "1"):
            environment["COPILOT_PLUGIN_INSTALL_STAGED"] = stage_flag
            result = subprocess.run(
                [*command, action], cwd=home, env=environment,
                capture_output=True, text=True, timeout=15, **kwargs,
            )
            assert result.returncode == 0, (result.stdout, result.stderr)
            assert not (home / ".agent-index").exists()
