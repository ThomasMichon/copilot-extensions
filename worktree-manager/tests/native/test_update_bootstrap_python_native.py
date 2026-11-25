"""The existing caller-owned native wait reaches explicit helper preparation."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

from agent_procutil import no_window_kwargs


@pytest.mark.parametrize("exit_code", [0, 29])
def test_update_bootstrap_runs_native_preparation_owner(tmp_path: Path, exit_code: int) -> None:
    payload = tmp_path / "payload with spaces"
    scripts = payload / "scripts"
    selectors = scripts / "_bootstrap_python"
    selectors.mkdir(parents=True)
    (scripts / "bootstrap-python-launcher.txt").write_bytes(b"uv\r\n")
    witness = tmp_path / "prepared"
    if os.name == "nt":
        (selectors / "select.ps1").write_text(
            "param([string]$Operation)\n"
            "if ($Operation -ne 'Prepare') { exit 71 }\n"
            "[IO.File]::WriteAllText($env:FIXTURE_PREPARED, $env:UV_PYTHON_INSTALL_DIR)\n"
            "[Console]::Out.WriteLine('prepared')\n"
            f"exit {exit_code}\n",
            encoding="utf-8",
        )
    else:
        (selectors / "select.sh").write_text(
            '#!/bin/sh\n[ "$1" = Prepare ] || exit 71\n'
            'printf "%s" "$UV_PYTHON_INSTALL_DIR" > "$FIXTURE_PREPARED"\n'
            f"printf 'prepared\\n'\nexit {exit_code}\n",
            encoding="utf-8",
        )
    result = subprocess.run(
        [
            sys.executable, "-c",
            "import os,sys; from pathlib import Path; "
            "from worktree_manager.update_bootstrap import _wait; "
            "from worktree_manager.update_bootstrap_python import prepare; "
            "sys.exit(prepare(Path(sys.argv[1]), dict(os.environ), _wait))",
            str(payload),
        ],
        env={
            **os.environ, "FIXTURE_PREPARED": str(witness),
            "UV_PYTHON_INSTALL_DIR": str(tmp_path / "managed python"),
        },
        capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == exit_code, result.stdout + result.stderr
    assert result.stdout.splitlines() == ["prepared"]
    assert witness.read_text() == str(tmp_path / "managed python")
