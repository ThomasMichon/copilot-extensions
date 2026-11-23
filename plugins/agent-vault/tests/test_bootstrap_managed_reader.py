"""The actual POSIX hook reads metadata only through its prepared managed base."""

from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from typing import cast

from agent_procutil import no_window_kwargs
import pytest

_bootstrap = import_module(".test_bootstrap_check_reconcile_always_on", __package__)
_clean_env = cast(Callable[[dict[str, str]], dict[str, str]], _bootstrap._clean_env)
_make_fake_install = cast(Callable[[Path, str], Path], _bootstrap._make_fake_install)
_make_fake_plugin = cast(Callable[[Path, str], Path], _bootstrap._make_fake_plugin)


@pytest.mark.skipif(os.name == "nt", reason="POSIX bootstrap reader")
@pytest.mark.parametrize("profile", [False, True])
@pytest.mark.parametrize("scenario", ["managed", "inactive", "unprepared", "failed-lookup", "missing"])
def test_actual_bootstrap_never_uses_ambient_metadata_or_profile_python(
    tmp_path: Path, profile: bool, scenario: str,
) -> None:
    host = shutil.which("bash")
    assert host, "native Bash is required"
    home, project = tmp_path / "home", tmp_path / "project"
    home.mkdir()
    project.mkdir()
    plugin = _make_fake_plugin(tmp_path, "agent-vault")
    installation = _make_fake_install(home, "agent-vault")
    marker = project / "profile-read.json"
    if profile:
        (installation / "runtime-profile.json").write_text("{}", encoding="utf-8")
        (plugin / "scripts" / "runtime_profile.py").write_text(
            "import json,sys\nfrom pathlib import Path\n"
            f"Path({str(marker)!r}).write_text(json.dumps(sys.argv[1:]))\n",
            encoding="utf-8",
        )
    if scenario == "inactive":
        (tmp_path / ".agent-vault.json").write_text('{"enabled":false}', encoding="utf-8")
    commands = tmp_path / "commands"
    commands.mkdir()
    ambient = tmp_path / "ambient-python-ran"
    for name in ("python", "python3", "python3.11", "python3.12", "python3.13"):
        command = commands / name
        command.write_text(
            "#!/bin/sh\nprintf invoked > " + shlex.quote(str(ambient)) + "\nexit 77\n",
            encoding="utf-8",
        )
        command.chmod(0o700)
    trace = tmp_path / "uv-calls"
    if scenario != "missing":
        uv = commands / "uv"
        uv.write_text(
            "#!/bin/sh\nprintf '%s\\n' \"$@\" >> " + shlex.quote(str(trace)) + "\n"
            '[ -z "${UV_PYTHON+x}${VIRTUAL_ENV+x}" ] || exit 94\n'
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
    for name in ("bash", "dirname", "wc", "cat", "grep", "sed", "uname", "tr"):
        executable = shutil.which(name)
        assert executable, f"{name} unavailable"
        (commands / name).symlink_to(executable)
    env = _clean_env({
        "HOME": str(home), "USERPROFILE": str(home), "PATH": str(commands),
        "UV_PYTHON": "unrelated", "VIRTUAL_ENV": "unrelated",
        "BOOTSTRAP_KILLSWITCH_STATE_FILE": str(home / "killswitch.json"),
    })
    result = subprocess.run(
        [host, str(plugin / "scripts" / "bootstrap-check.sh")],
        cwd=project, env=env, input="caller input\n", capture_output=True,
        text=True, encoding="utf-8", timeout=30, **no_window_kwargs(),
    )
    refused = scenario in {"missing", "unprepared", "failed-lookup"}
    assert result.returncode == (3 if refused else 0), result.stdout + result.stderr
    assert json.loads(result.stdout) == {}
    assert not ambient.exists()
    assert not (plugin / "scripts" / "inline-entered").exists()
    assert not (installation / "versions").exists()
    if refused:
        assert "prepared" in result.stderr and "Python" in result.stderr
    assert marker.exists() is (profile and scenario == "managed")
    assert (project / "handoff").exists() is (not profile and scenario == "managed")
    if marker.exists():
        assert json.loads(marker.read_text()) == [
            "--root", str(installation), "current", "--package-version", "1.0.1",
        ]
    if scenario == "missing":
        assert not trace.exists()
    else:
        assert trace.read_text().splitlines() == [
            "--version", "python", "find", "--no-project", "--system",
            "--managed-python", "--no-python-downloads", "cpython@3.11.18",
        ]
