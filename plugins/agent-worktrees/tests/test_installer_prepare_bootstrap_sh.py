"""POSIX installer actions prepare the declared managed bootstrap Python."""
from __future__ import annotations

import os
import shutil
import subprocess
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
    start = source.index("_bootstrap_python_candidates() {\n")
    end = source.index("\n}\n", start) + 3
    return source[start:end]


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
    scripts = tmp_path / "scripts"
    (scripts / "_bootstrap_python").mkdir(parents=True)
    (scripts / "bootstrap-python-launcher.txt").write_text("uv\n", encoding="ascii")
    (scripts / "_bootstrap_python" / "select.sh").write_text(
        'printf "%s\\n" "${1:-Inspect}"\n', encoding="utf-8", newline="\n",
    )
    program = (
        "set -euo pipefail\n"
        'SCRIPT_DIR="$1"\n__aw_action="$2"\n'
        + _candidates_function()
        + "_bootstrap_python_candidates\n"
    )
    environment = {
        key: value for key, value in os.environ.items()
        if key != "COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON"
    }
    assert BASH is not None
    result = subprocess.run(
        [BASH, "-c", program, "test", scripts.as_posix(), action],
        env=environment, capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == operation
