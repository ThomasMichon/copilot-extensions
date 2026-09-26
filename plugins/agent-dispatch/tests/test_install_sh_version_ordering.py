"""Regression guard: `install.sh`'s `_version_lt` must order the
`MAJOR.MINOR.PATCH[-devN]` build stream correctly on every platform (#377).

The prior implementation piped both versions through `sort -V` and checked
which line sorted first. GNU coreutils' `sort -V` orders a trailing `devN`
suffix numerically, but Apple's `sort -V` (macOS) orders it lexically -- so
`0.1.2.dev102` sorted *before* `0.1.2.dev74` there, making the downgrade
guard misfire and permanently block a legitimate upgrade. `_version_lt` now
compares dot-separated components as integers instead of delegating to
`sort -V`, so behavior no longer depends on which `sort` implementation is
on `PATH`.

These tests extract only the `_version_lt` function body from `install.sh`
(never the whole script -- it takes an action argument and performs a real
install/update when run standalone) and exercise it directly via bash.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_INSTALL_SH = _PLUGIN_ROOT / "scripts" / "install.sh"


def _resolve_bash() -> str | None:
    """Resolve a REAL bash, not Windows' WSL-launcher `bash.exe` shim.

    On Windows, ``shutil.which("bash")`` can resolve to a WSL launcher stub
    under ``WindowsApps`` or the classic ``C:\\Windows\\System32\\bash.exe``
    -- neither is a real POSIX bash for this test's purposes (the System32
    launcher invokes an actual WSL distro, which runs this script in a
    different environment than the one under test). Prefer the real Git
    Bash location when present, then fall back to a PATH-resolved bash with
    both known WSL-launcher locations excluded.
    """
    git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
    if git_bash.is_file():
        return str(git_bash)
    path = os.environ.get("PATH")
    if path:
        filtered = os.pathsep.join(
            part for part in path.split(os.pathsep)
            if "WindowsApps" not in part
            and part.rstrip("\\").lower() != r"c:\windows\system32"
        )
        bash = shutil.which("bash", path=filtered)
        if bash:
            return bash
    bash = shutil.which("bash")
    if bash and "WindowsApps" not in bash and "\\system32\\" not in bash.lower():
        return bash
    return None


_BASH = _resolve_bash()


def _version_lt(a: str, b: str) -> bool:
    """Invoke the real `_version_lt` function extracted from install.sh.

    Writes the extracted function plus a real call to a temp **script file**
    and runs `bash <file>`, rather than passing the script as a `bash -c`
    string argument: on Windows, an argv string handed to the WSL bash
    launcher via PowerShell/subprocess can arrive with variable expansions
    silently emptied out (observed directly -- `x=05; echo $x` printed
    nothing under `-c`, while the identical script run from a file behaved
    correctly), which is an argv-marshalling quirk of that launcher path, not
    of `_version_lt` or of a real invocation (install.sh always runs as a
    script file with its own real argv, never `-c`).
    """
    assert _BASH is not None
    text = _INSTALL_SH.read_text(encoding="utf-8")
    start = text.index("_version_lt() {")
    end = text.index("\n}\n", start) + len("\n}")
    func_src = text[start:end]
    script = f"{func_src}\n_version_lt {shlex.quote(a)} {shlex.quote(b)}\n"
    # Written next to install.sh (not a bare OS temp dir) and referenced by a
    # bare filename with cwd=its directory, so the WSL launcher's relative
    # path resolution (which worked reliably in manual testing) applies --
    # an absolute Windows path (`C:\...`) does not translate for it.
    fd, script_path = tempfile.mkstemp(
        suffix=".sh", dir=str(_INSTALL_SH.parent)
    )
    try:
        with os.fdopen(fd, "w", newline="\n", encoding="utf-8") as f:
            f.write(script)
        result = subprocess.run(
            [_BASH, os.path.basename(script_path)],
            cwd=str(_INSTALL_SH.parent),
            capture_output=True,
            text=True,
            timeout=10,
        )
    finally:
        os.unlink(script_path)
    return result.returncode == 0


def test_function_present():
    assert _INSTALL_SH.is_file()
    assert "_version_lt() {" in _INSTALL_SH.read_text(encoding="utf-8")


@pytest.mark.skipif(_BASH is None, reason="bash not available")
def test_devn_digit_count_regression():
    # The exact #377 repro: a lexical compare (or a sort -V that treats the
    # devN suffix lexically, as Apple's does) puts "dev102" before "dev74"
    # because "1" < "7". A numeric compare must not.
    assert _version_lt("0.1.2-dev74", "0.1.2-dev102") is True
    assert _version_lt("0.1.2-dev102", "0.1.2-dev74") is False
    assert _version_lt("0.1.2.dev74", "0.1.2.dev102") is True
    assert _version_lt("0.1.2.dev102", "0.1.2.dev74") is False


@pytest.mark.skipif(_BASH is None, reason="bash not available")
def test_equal_versions_are_not_lt():
    assert _version_lt("0.1.2-dev74", "0.1.2-dev74") is False
    assert _version_lt("0.1.2.dev74", "0.1.2-dev74") is False


@pytest.mark.skipif(_BASH is None, reason="bash not available")
def test_major_minor_patch_ordering():
    assert _version_lt("0.1.2", "0.1.3") is True
    assert _version_lt("0.1.3", "0.1.2") is False
    assert _version_lt("0.1.9", "0.1.10") is True
    assert _version_lt("0.9.0", "0.10.0") is True
    assert _version_lt("0.10.0", "0.9.0") is False


@pytest.mark.skipif(_BASH is None, reason="bash not available")
def test_release_outranks_dev_prerelease_of_same_prefix():
    assert _version_lt("0.1.2.dev5", "0.1.2") is True
    assert _version_lt("0.1.2", "0.1.2.dev5") is False
