"""Execute the real install lock helper without running a machine installer."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest
from agent_procutil import no_window_kwargs


_GIT_BASH = Path(r"C:\Program Files\Git\bin\bash.exe")
_BASH = (str(_GIT_BASH) if _GIT_BASH.is_file() else None) if os.name == "nt" else shutil.which("bash")
_INSTALL_SH = Path(__file__).resolve().parents[1] / "scripts" / "install.sh"


@pytest.mark.skipif(_BASH is None, reason="requires a real Bash, not the Windows WSL launcher")
@pytest.mark.parametrize("open_fails", [False, True])
def test_install_sh_lock_preserves_caller_stderr_and_open_fd(tmp_path, open_fails):
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    if open_fails:
        (install_dir / ".install.lock").mkdir()
    source = _INSTALL_SH.read_text(encoding="utf-8")
    start = source.index("_enter_install_lock()")
    end = source.index("\n}\n", start) + 2
    script = "set -eu\n" + source[start:end] + """
INSTALL_DIR=$1
printf 'before-lock\\n' >&2
_enter_install_lock || exit 17
printf 'after-lock\\n' >&2
if [ ! -d "$INSTALL_DIR/.install.lock" ]; then
    printf 'owned-fd\\n' >&8
    if command -v flock >/dev/null 2>&1; then
        if flock -n "$INSTALL_DIR/.install.lock" true 8>&-; then
            printf 'lock was not retained\\n' >&2
            exit 18
        fi
    fi
fi
"""
    env = {key: value for key, value in os.environ.items() if key not in {"BASH_ENV", "ENV"}}
    result = subprocess.run(
        [_BASH, "--noprofile", "--norc", "-c", script, "install-lock-test", install_dir.as_posix()],
        capture_output=True, text=True, env=env, timeout=10, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == "before-lock\nafter-lock\n"
    if not open_fails:
        assert (install_dir / ".install.lock").read_bytes() == b"owned-fd\n"
