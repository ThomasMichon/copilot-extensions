"""invoke-payload-runtime.sh's no-flock provisioning lock never unlinks a live
lock that it did not judge stale itself.

The acquisition loop is driven deterministically: ``ln``/``readlink``/``rm``/
``kill``/``sleep`` are replaced by shell functions over plain files (a "link"
is a file holding its target), so another process's action can be injected at
an exact point of the loop. This runs under any bash, including Git Bash.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "invoke-payload-runtime.sh"
LOCK_START = 'LOCK_LINK=""\n'
LOCK_END = "trap unlock_provision EXIT INT TERM\n"

# ``rm`` records a VIOLATION whenever the lock link is removed while it names
# a live owner -- i.e. whenever a contender deletes a lock it does not own.
# Scenarios hook in through before_read/after_read, called with the 1-based
# count of reads of the lock link.
_SHIM = r"""
LINK="$RUNTIME_ROOT/.provision.lock.pid"
_count() {
    local c; c=$(( $(cat "$STATE/$1" 2>/dev/null || echo 0) + 1 ))
    echo "$c" > "$STATE/$1"; echo "$c"
}
sleep() { :; }
kill() { [[ "$1" == -0 ]] && grep -qx -- "$2" "$STATE/live"; }
ln() { [[ "$1" == -s && ! -e "$3" ]] || return 1; printf '%s' "$2" > "$3"; }
rm() {
    local p
    for p in "$@"; do
        if [[ "$p" == "$LINK" && -e "$p" ]] && kill -0 "$(cat "$p")"; then
            echo VIOLATION >> "$STATE/violations"
        fi
    done
    command rm "$@"
}
readlink() {
    local n="" rc=1
    if [[ "$1" == "$LINK" ]]; then n="$(_count reads)"; before_read "$n"; fi
    if [[ -e "$1" ]]; then cat "$1"; rc=0; fi
    if [[ -n "$n" ]]; then after_read "$n"; fi
    return "$rc"
}
"""


def _bash() -> str | None:
    if os.name != "nt":
        return shutil.which("bash")
    git = shutil.which("git")
    if git is None:
        return None
    root = Path(git).resolve().parents[1]
    for candidate in (root / "bin" / "bash.exe", root / "usr" / "bin" / "bash.exe"):
        if candidate.is_file():
            return str(candidate)
    return None


def _posix(path: Path) -> str:
    return str(path).replace("\\", "/")


def _lock_section() -> str:
    text = SCRIPT.read_text(encoding="utf-8").replace("\r\n", "\n")
    start = text.index(LOCK_START)
    return text[start : text.index(LOCK_END, start)]


@pytest.mark.guard
def test_reaper_never_unlinks_a_newer_holder_released_mid_check(tmp_path: Path) -> None:
    """The owner is dead at the first check, but by the under-mutex re-read
    another reaper has removed it and a live holder P has taken the lock. P
    then releases and Q re-creates the lock before the reaper's liveness
    probe. The reaper may only remove the owner it judged stale itself, so
    Q's live lock must survive."""
    bash = _bash()
    if bash is None:
        pytest.skip("bash not available")
    root = tmp_path / "rt"
    root.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    (root / ".provision.lock.pid").write_bytes(b"6161")
    (state / "live").write_bytes(b"")
    hooks = (
        'before_read() {\n'
        '    case "$1" in\n'
        '        2) printf 7171 > "$LINK"; echo 7171 > "$STATE/live" ;;\n'
        '        3) command rm -f "$LINK" ;;\n'
        '    esac\n'
        '}\n'
        'after_read() {\n'
        '    if [[ "$1" == 2 ]]; then echo 8181 > "$STATE/live"; printf 8181 > "$LINK"; fi\n'
        '}\n'
    )
    script = (
        f"RUNTIME_ROOT='{_posix(root)}'\nSTATE='{_posix(state)}'\n"
        + _SHIM + hooks + _lock_section()
        + 'echo "acquired $$ $(cat "$LINK")"\n'
    )
    harness = tmp_path / "harness.sh"
    harness.write_bytes(script.encode())
    result = subprocess.run(
        [bash, _posix(harness)], capture_output=True, text=True, timeout=60,
        env={**os.environ, "COPILOT_EXT_NO_FLOCK": "1"},
    )
    assert result.returncode == 0, result.stderr
    violations = state / "violations"
    assert not violations.exists(), violations.read_text()
    status, pid, holder = result.stdout.split()
    assert status == "acquired" and holder == pid
