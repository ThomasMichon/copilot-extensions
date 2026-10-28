"""Execute Bash lock parsing with deterministic OS observations, not native OS proof."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
GUARD = ROOT / "libs" / "bootstrap-killswitch" / "bootstrap-killswitch-guard.sh"
WATCHDOG = ROOT / "libs" / "installer-engine" / "installer-watchdog.sh"
BASH = shutil.which(os.environ.get("BOOTSTRAP_KILLSWITCH_TEST_BASH", "bash"))
if os.name == "nt" and BASH and Path(BASH).parent.name.lower() in {"windowsapps", "system32"}:
    BASH = None  # Do not silently substitute a WSL venue for a host-side shell test.


@pytest.mark.skipif(BASH is None, reason="Bash unavailable")
@pytest.mark.parametrize("helper_available", [False, True])
@pytest.mark.parametrize(
    "record,written,expected",
    [
        ("42 2000", 3, 0),
        ("42", 3, 0),
        ("42 1000", 3, 1),
        ("42", 0, 1),
        ("42 2000 extra", 3, 1),
        ("0 2000", 3, 1),
        ("not-a-pid", 3, 1),
        ("42 invalid", 3, 1),
    ],
)
def test_bash_lock_reader_contract(tmp_path, helper_available, record, written, expected):
    lock = tmp_path / "reconcile.lock"
    lock.write_text(record, encoding="utf-8")
    original = lock.read_bytes(), lock.stat().st_mtime_ns
    script = GUARD.read_text(encoding="utf-8").split('cmd="${1:-check}"', 1)[0]
    if helper_available:
        script += "\nsource " + shlex.quote(WATCHDOG.as_posix()) + "\n"
    script += f"""
kill() {{ [[ "$#" -eq 2 && "$1" == -0 && "$2" == 42 ]]; }}
stat() {{ printf '%s\\n' {written}; }}
get_install_process_birth() {{ printf '2000\\n'; }}
_reconcile_process_birth() {{ printf '2000\\n'; }}
_reconcile_lock_live "$1"
"""
    result = subprocess.run(
        [BASH, "-s", "--", lock.as_posix()],
        input=script,
        capture_output=True,
        text=True,
        timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == expected, result.stderr
    assert result.stdout == ""
    assert (lock.read_bytes(), lock.stat().st_mtime_ns) == original
