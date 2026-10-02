"""Guard for init.sh's `__payload_fstat`/`__payload_descriptor_stat`
helpers (macOS device-id false positive): on Darwin, a path-based `stat -L`
of the synthetic `/dev/fd/<n>` devfs node does NOT behave like a real
fstat(2) -- devfs can report its own device number for that node rather than
passing through the real underlying file's device, false-positiving
"Payload content changed during hashing" for an untouched `.gitignore` on
every macOS install (confirmed by a direct probe from a downstream report).
`__payload_fstat` instead performs a genuine fstat(2) on the inherited
descriptor via Python's `os.fstat(0)`, preserving full
type+device+inode+size+mtime+ctime identity/replacement-race protection
(a naive fix that simply dropped the device field would have weakened that
protection: inode numbers are only unique within one device, so a
cross-filesystem replacement with a matching inode could otherwise pass
undetected). Exercises the real extracted bash functions against a real
open file descriptor rather than restating the logic, so a regression here
would still pass a Python-only test while silently reintroducing the macOS
false positive or the replacement-race weakness. Mirrors
agent-index/tests/test_payload_hash_darwin_device_id.py -- both plugins
carry an identical copy of this installer function."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
INSTALLER = PLUGIN / "scripts" / "init.sh"

pytestmark = pytest.mark.guard


def _bash() -> str:
    bash = shutil.which("bash")
    if os.name == "nt" or not bash:
        pytest.skip("native POSIX bash is unavailable")
    return bash


def _extract(text: str, start: str, end: str) -> str:
    assert text.count(start) == 1, f"expected exactly one {start!r} definition"
    return start + text.split(start, 1)[1].split(end, 1)[0]


def _extract_functions() -> str:
    text = INSTALLER.read_text(encoding="utf-8")
    payload_stat = _extract(
        text, "__payload_stat() {", "\n        }\n        __payload_is_directory() {"
    ) + "\n        }"
    payload_fstat = _extract(
        text, "__payload_fstat() {", "\n        }\n        __payload_descriptor_stat() {"
    ) + "\n        }"
    descriptor_stat = _extract(
        text, "__payload_descriptor_stat() {", "\n        }\n        __payload_size() {"
    ) + "\n        }"
    return "\n".join((payload_stat, payload_fstat, descriptor_stat))


def _run(script_body: str, *, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    bash = _bash()
    fn = _extract_functions()
    script = f"""
set -euo pipefail
_fail() {{ printf 'FAIL:%s\\n' "$1" >&2; }}
_bootstrap_python() {{ command -v python3 || command -v python; }}
{fn}
{script_body}
"""
    return subprocess.run(
        [bash, "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )


def test_payload_fstat_matches_real_pathname_stat_of_the_same_file(tmp_path: Path) -> None:
    # The core regression proof: a real fstat(2) via __payload_fstat on an
    # open descriptor must report the SAME device+inode+size+mtime+ctime as
    # an independent pathname-based stat of the identical, untouched file --
    # no devfs/procfs layer introducing a spurious mismatch.
    target = tmp_path / "probe.txt"
    target.write_text("hello world\n", encoding="utf-8")
    real = os.stat(target)

    script = f"""
__kernel=Linux
exec {{fd}}<'{target}'
__payload_fstat "$fd"
"""
    result = _run(script, tmp_path=tmp_path)
    assert result.returncode == 0, result.stderr
    kind, dev, ino, size, mtime, ctime = result.stdout.strip().split("|")
    assert kind == "Regular File"
    assert int(dev) == real.st_dev
    assert int(ino) == real.st_ino
    assert int(size) == real.st_size
    assert int(mtime) == int(real.st_mtime)
    assert int(ctime) == int(real.st_ctime)


def test_descriptor_stat_dispatches_to_fstat_on_darwin(tmp_path: Path) -> None:
    # Forcing __kernel=Darwin (even on this test's actual Linux host) proves
    # the dispatcher takes the real-fstat path rather than a path-based
    # /dev/fd stat, and that its result still matches the real file.
    target = tmp_path / "probe.txt"
    target.write_text("hello world\n", encoding="utf-8")
    real = os.stat(target)

    script = f"""
__kernel=Darwin
exec {{fd}}<'{target}'
__payload_descriptor_stat "$fd"
"""
    result = _run(script, tmp_path=tmp_path)
    assert result.returncode == 0, result.stderr
    kind, dev, ino, size, mtime, ctime = result.stdout.strip().split("|")
    assert kind == "Regular File"
    assert int(dev) == real.st_dev
    assert int(ino) == real.st_ino
    assert int(size) == real.st_size


def test_descriptor_stat_uses_proc_fd_on_non_darwin(tmp_path: Path) -> None:
    # On a non-Darwin kernel, the cheaper /proc/<pid>/fd path-stat is used
    # instead of the Python fstat helper -- confirm it still reports the
    # real file's identity (this was already correct pre-fix; guards against
    # accidentally routing every kernel through the new Python path).
    target = tmp_path / "probe.txt"
    target.write_text("hello world\n", encoding="utf-8")
    real = os.stat(target)

    script = f"""
__kernel=Linux
exec {{fd}}<'{target}'
__payload_descriptor_stat "$fd"
"""
    result = _run(script, tmp_path=tmp_path)
    assert result.returncode == 0, result.stderr
    _kind, dev, ino, _size, _mtime, _ctime = result.stdout.strip().split("|")
    assert int(dev) == real.st_dev
    assert int(ino) == real.st_ino


def test_replacement_with_matching_inode_on_a_different_device_is_still_detected(
    tmp_path: Path,
) -> None:
    # The exact replacement-race scenario review flagged against a naive
    # device-stripping fix: __payload_fstat's real device field must still
    # differ from a forged "original" metadata string that claims a
    # different device, even when every other field (type, inode, size,
    # mtime, ctime) happens to match -- proving device-based detection is
    # fully preserved, not weakened, by this fix.
    target = tmp_path / "probe.txt"
    target.write_text("hello world\n", encoding="utf-8")
    real = os.stat(target)

    forged_metadata = (
        f"Regular File|{real.st_dev + 1}|{real.st_ino}|{real.st_size}|"
        f"{int(real.st_mtime)}|{int(real.st_ctime)}"
    )
    script = f"""
__kernel=Darwin
exec {{fd}}<'{target}'
__opened="$(__payload_descriptor_stat "$fd")"
__metadata={forged_metadata!r}
if [[ "$__opened" == "$__metadata" ]]; then
    echo MATCHED
else
    echo MISMATCHED
fi
"""
    result = _run(script, tmp_path=tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "MISMATCHED"


def test_descriptor_comparisons_use_descriptor_stat_not_a_raw_dev_fd_path() -> None:
    # Guards against reintroducing the path-based `stat -L "$__descriptor"`
    # comparisons that caused the macOS false positive, even if the new
    # helpers survive unused.
    text = INSTALLER.read_text(encoding="utf-8")
    assert '__opened="$(__payload_descriptor_stat "$__fd")"' in text
    assert '__opened_after="$(__payload_descriptor_stat "$__fd")"' in text
    assert '[[ "$__opened" == "$__metadata" ]]' in text
    assert '[[ "$__opened_after" == "$__opened" &&' in text
    assert "__descriptor=" not in text
