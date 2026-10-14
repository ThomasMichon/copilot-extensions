"""The projection CLI imports its own payload under safe-path launchers."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


CLI = (
    Path(__file__).resolve().parents[1]
    / "skills" / "reviewing-customizations" / "scripts"
    / "manage-instruction-projections.py"
)


@pytest.mark.parametrize("flags", [
    [],
    pytest.param(["-P"], marks=pytest.mark.skipif(
        sys.version_info < (3, 11), reason="Python safe-path flag requires 3.11+",
    )),
    ["-I"],
])
def test_projection_cli_uses_own_modules_under_isolation(tmp_path, flags):
    (tmp_path / "instruction_projections.py").write_text(
        'raise AssertionError("renderer imported an untrusted checkout module")\n',
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, *flags, str(CLI), "--help"],
        cwd=tmp_path,
        env={**os.environ, "PYTHONSAFEPATH": "1", "PYTHONPATH": str(tmp_path)},
        capture_output=True, text=True, timeout=15, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "render-local-cache" in result.stdout
    assert result.stderr == ""
