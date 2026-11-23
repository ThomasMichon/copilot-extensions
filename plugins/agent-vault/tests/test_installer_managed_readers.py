"""Explicit POSIX setup readers reuse the existing prepared managed binding."""

from __future__ import annotations

import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

from agent_procutil import no_window_kwargs
import pytest

PLUGIN = Path(__file__).resolve().parents[1]


def _function(script: Path, name: str) -> str:
    text = script.read_text(encoding="utf-8")
    start = text.index(name + "() {")
    return text[start:text.index("\n}", start) + 2]


def _environment(root: Path, scenario: str) -> tuple[dict[str, str], Path, Path]:
    home, commands = root / "home", root / "commands"
    home.mkdir()
    commands.mkdir()
    marker, trace = root / "ambient-ran", root / "uv-calls"
    for name in ("python", "python3", "python3.11", "python3.12", "python3.13", "pip", "pip3"):
        command = commands / name
        command.write_text(
            "#!/bin/sh\nprintf invoked > " + shlex.quote(str(marker))
            + "\nprintf 'https://wrong.invalid/simple\\n'\n",
            encoding="utf-8",
        )
        command.chmod(0o700)
    if scenario != "missing":
        uv = commands / "uv"
        uv.write_text(
            "#!/bin/sh\nprintf '%s\\n' \"$@\" >> " + shlex.quote(str(trace)) + "\n"
            "if IFS= read -r ignored; then exit 95; fi\n"
            'case "$1" in\n'
            "  --version) printf 'uv 0.13.7\\n'; exit 0 ;;\n"
            '  python) [ "$2" = find ] || exit 91 ;;\n'
            "  *) exit 92 ;;\nesac\n"
            + ("exit 2\n" if scenario == "unprepared" else
               "printf '%s\\n' " + shlex.quote(sys.executable) + "\n"
               + ("exit 9\n" if scenario == "failed-lookup" else "")),
            encoding="utf-8",
        )
        uv.chmod(0o700)
    for name in ("dirname", "wc", "cat", "grep", "sed", "head", "tr"):
        executable = shutil.which(name)
        assert executable, f"{name} unavailable"
        (commands / name).symlink_to(executable)
    config = home / "uv.toml"
    config.write_text("", encoding="utf-8")
    return {
        "HOME": str(home), "PATH": str(commands), "UV_CONFIG_FILE": str(config),
        "UV_NO_SYSTEM_CONFIG": "1",
    }, trace, marker


@pytest.mark.skipif(os.name == "nt", reason="POSIX installer reader")
@pytest.mark.parametrize("function", ["_bootstrap_python", "_find_python"])
@pytest.mark.parametrize("profile", [False, True])
@pytest.mark.parametrize("scenario", ["managed", "unprepared", "failed-lookup", "missing"])
def test_setup_bootstrap_never_discovers_ambient_python(
    tmp_path: Path, function: str, profile: bool, scenario: str,
) -> None:
    bash = shutil.which("bash")
    assert bash
    env, trace, marker = _environment(tmp_path, scenario)
    installer = PLUGIN / "scripts" / "install.sh"
    script = (
        "set -uo pipefail\nSCRIPT_DIR=" + shlex.quote(str(installer.parent))
        + "\nINSTALL_DIR=" + shlex.quote(str(tmp_path / "runtime"))
        + "\nPROFILE=" + ("ssh" if profile else "''") + "\n"
        + _function(installer, "_bootstrap_python") + "\n"
        + _function(installer, "_find_python") + "\n" + function + "\n"
    )
    result = subprocess.run(
        [bash, "-c", script], env=env, cwd=tmp_path, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert not marker.exists(), result.stdout + result.stderr
    if scenario == "managed":
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == sys.executable
    else:
        assert result.returncode != 0, result.stdout + result.stderr
        assert not result.stdout
        assert "prepared" in result.stderr
    if scenario == "missing":
        assert not trace.exists()
    else:
        assert trace.read_text().splitlines() == [
            "--version", "python", "find", "--no-project", "--system",
            "--managed-python", "--no-python-downloads", "cpython@3.11.18",
        ]
    assert not (tmp_path / "runtime").exists()
