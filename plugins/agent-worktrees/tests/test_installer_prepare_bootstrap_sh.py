"""POSIX installer actions prepare the declared managed bootstrap Python."""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
pytestmark = pytest.mark.guard


def _bash() -> str | None:
    found = shutil.which("bash")
    if found and os.name == "nt" and "system32" in found.lower():
        return None
    return found


BASH = _bash()


def _candidates_function() -> str:
    source = (SCRIPTS / "install.sh").read_text(encoding="utf-8")
    start = source.index("_aw_prepare_bootstrap_python() (\n")
    end = source.index("\n}\n", source.index("_bootstrap_python_candidates() {\n")) + 3
    return source[start:end]


def _run(
    tmp_path: Path, action: str, selector: str, timeout: str = "",
) -> subprocess.CompletedProcess[str]:
    scripts = tmp_path / "scripts"
    (scripts / "_bootstrap_python").mkdir(parents=True)
    (scripts / "bootstrap-python-launcher.txt").write_text("uv\n", encoding="ascii")
    (scripts / "_bootstrap_python" / "select.sh").write_text(
        selector, encoding="utf-8", newline="\n",
    )
    program = (
        "set -euo pipefail\n"
        'SCRIPT_DIR="$1"\n__aw_action="$2"\n'
        + _candidates_function()
        + "_bootstrap_python_candidates\n"
    )
    environment = {
        key: value for key, value in os.environ.items()
        if key not in ("COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON", "AGENT_WORKTREES_BOOTSTRAP_PREPARE_TIMEOUT_SEC")
    }
    if timeout:
        environment["AGENT_WORKTREES_BOOTSTRAP_PREPARE_TIMEOUT_SEC"] = timeout
    assert BASH is not None
    return subprocess.run(
        [BASH, "-c", program, "test", scripts.as_posix(), action],
        env=environment, capture_output=True, text=True, timeout=60, check=False,
    )


@pytest.mark.skipif(BASH is None, reason="bash is unavailable")
@pytest.mark.parametrize(
    ("action", "operation"),
    [
        ("install", "Prepare"),
        ("update", "Prepare"),
        ("repair", "Prepare"),
        ("uninstall", "Inspect"),
        ("status", "Inspect"),
        ("start", "Inspect"),
        ("stop", "Inspect"),
    ],
)
def test_installer_action_selects_bootstrap_operation(
    tmp_path: Path, action: str, operation: str,
) -> None:
    result = _run(tmp_path, action, 'printf "%s\\n" "${1:-Inspect}"\n')
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == operation


@pytest.mark.skipif(BASH is None, reason="bash is unavailable")
def test_stalled_prepare_is_bounded_and_reaps_its_children(tmp_path: Path) -> None:
    marker = (tmp_path / "grandchild.pid").as_posix()
    selector = (
        f"sleep 300 &\nprintf '%s' \"$!\" > '{marker}'\nwait\n"
    )
    started = time.monotonic()
    result = _run(tmp_path, "update", selector, timeout="2")
    assert time.monotonic() - started < 30
    assert result.returncode == 124, result.stdout + result.stderr
    assert "exceeded 2s" in result.stderr
    assert BASH is not None
    grandchild = (tmp_path / "grandchild.pid").read_text(encoding="ascii")
    alive = subprocess.run(
        [BASH, "-c", 'kill -0 "$1" 2>/dev/null', "probe", grandchild],
        capture_output=True, timeout=20, check=False,
    )
    assert alive.returncode != 0
