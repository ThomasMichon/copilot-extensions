"""Execution tests for the canonical file-only runtime resolvers."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _slot(root: Path, version: str, *, complete: bool, interpreter: bool) -> Path:
    slot = root / "versions" / version
    slot.mkdir(parents=True)
    if complete:
        (slot / ".install-complete.json").write_text(
            f'{{"version":"{version}"}}', encoding="utf-8"
        )
    if interpreter:
        subpath = Path("Scripts/python.exe") if os.name == "nt" else Path("bin/python")
        python = slot / subpath
        python.parent.mkdir(parents=True)
        python.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
        if os.name != "nt":
            python.chmod(0o755)
    return slot


def test_powershell_resolver_skips_incomplete_current_without_spawning(
    tmp_path: Path,
) -> None:
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if pwsh is None:
        pytest.skip("PowerShell is unavailable")
    root = tmp_path / "runtime"
    current = _slot(root, "2.0.0", complete=True, interpreter=False)
    lkg = _slot(root, "1.0.0", complete=True, interpreter=True)
    (root / "current-version").write_text("2.0.0\n", encoding="utf-8")
    (root / "last-known-good").write_text("1.0.0\n", encoding="utf-8")

    command = (
        f"$env:AGENT_RT_ROOT = '{root}'; "
        f". '{SCRIPTS / 'resolve-runtime.ps1'}'; "
        "[Console]::Write($AgentRtPy)"
    )
    result = subprocess.run(
        [pwsh, "-NoProfile", "-Command", command],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    expected = lkg / (
        Path("Scripts/python.exe") if os.name == "nt" else Path("bin/python")
    )
    assert Path(result.stdout) == expected
    assert not (current / "spawned").exists()
    assert not (lkg / "spawned").exists()


def test_powershell_tier3_is_version_aware_and_cross_platform(
    tmp_path: Path,
) -> None:
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if pwsh is None:
        pytest.skip("PowerShell is unavailable")
    root = tmp_path / "runtime"
    _slot(root, "0.4.0-dev9", complete=True, interpreter=True)
    newest = _slot(root, "0.4.0-dev10", complete=True, interpreter=True)
    command = (
        f"$env:AGENT_RT_ROOT = '{root}'; "
        f". '{SCRIPTS / 'resolve-runtime.ps1'}'; "
        "[Console]::Write($AgentRtPy)"
    )

    result = subprocess.run(
        [pwsh, "-NoProfile", "-Command", command],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    subpath = Path("Scripts/python.exe") if os.name == "nt" else Path("bin/python")
    assert Path(result.stdout) == newest / subpath


@pytest.mark.skipif(
    os.name == "nt" or shutil.which("bash") is None,
    reason="a native POSIX bash is unavailable",
)
def test_posix_resolver_requires_completion_marker_without_spawning(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    incomplete = _slot(root, "2.0.0", complete=False, interpreter=True)
    healthy = _slot(root, "1.0.0", complete=True, interpreter=True)
    (root / "current-version").write_text("2.0.0\n", encoding="utf-8")
    (root / "last-known-good").write_text("1.0.0\n", encoding="utf-8")
    command = (
        f"AGENT_RT_ROOT='{root}'; export AGENT_RT_ROOT; "
        f". '{SCRIPTS / 'resolve-runtime.sh'}'; printf '%s' \"$AGENT_RT_PY\""
    )

    result = subprocess.run(
        [shutil.which("bash"), "-c", command],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert Path(result.stdout) == healthy / "bin" / "python"
    assert not (incomplete / "spawned").exists()
    assert not (healthy / "spawned").exists()


@pytest.mark.skipif(
    os.name == "nt" or shutil.which("bash") is None,
    reason="a native POSIX bash is unavailable",
)
def test_posix_tier3_orders_dev_versions_without_sort_v(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    _slot(root, "0.4.0-dev9", complete=True, interpreter=True)
    newest = _slot(root, "0.4.0-dev10", complete=True, interpreter=True)
    real_sort = shutil.which("sort")
    assert real_sort is not None
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_sort = fake_bin / "sort"
    fake_sort.write_text(
        "#!/bin/sh\n"
        'for arg in "$@"; do [ "$arg" = "-V" ] && exit 64; done\n'
        f"exec {shlex.quote(real_sort)} \"$@\"\n",
        encoding="utf-8",
    )
    fake_sort.chmod(0o755)
    command = (
        f"AGENT_RT_ROOT={shlex.quote(str(root))}; export AGENT_RT_ROOT; "
        f". {shlex.quote(str(SCRIPTS / 'resolve-runtime.sh'))}; "
        "printf '%s' \"$AGENT_RT_PY\""
    )
    env = os.environ.copy()
    env["PATH"] = str(fake_bin) + os.pathsep + env["PATH"]

    result = subprocess.run(
        [shutil.which("bash"), "-c", command],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert Path(result.stdout) == newest / "bin" / "python"
