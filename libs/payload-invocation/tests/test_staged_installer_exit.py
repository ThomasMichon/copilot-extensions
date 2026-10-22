"""A staged install failure must survive the native watchdog parent."""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.skipif(os.name != "nt", reason="native Windows child process handles")
@pytest.mark.portfolio_tier("T3")
@pytest.mark.parametrize("host", ["powershell", "pwsh"])
@pytest.mark.parametrize("status", [0, 37])
def test_watchdog_preserves_staged_child_exit(
    tmp_path: Path, host: str, status: int,
) -> None:
    executable = shutil.which(host)
    if executable is None:
        pytest.skip(f"{host} unavailable")
    blocks = []
    for source in sorted((ROOT / "plugins").glob("*/scripts/*.ps1")):
        text = source.read_text(encoding="utf-8")
        if "$__wdChild = Start-Process" not in text:
            continue
        match = re.search(
            r"(?m)^ +\$__wdChild = Start-Process.*?^ +exit \$__wdChild\.ExitCode",
            text, re.S,
        )
        assert match, source
        blocks.append(match.group())
    assert len(blocks) == 13
    assert len(set(blocks)) == 1
    runner = tmp_path / "watchdog.ps1"
    runner.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "$__selfStageExe = (Get-Process -Id $PID).Path\n"
        f"$__selfStageArgs = '-NoProfile -Command \"exit {status}\"'\n"
        "$__wdDeadline = 0\n"
        + blocks[0] + "\n", encoding="utf-8",
    )
    result = subprocess.run(
        [executable, "-NoLogo", "-NoProfile", "-File", str(runner)],
        cwd=tmp_path, capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == status, result.stdout + result.stderr
