"""Unit routing contracts, not evidence that a native Windows shell ran."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path

import pytest
from agent_procutil import owned_process
from native_shell_support import bash_path
from test_update_entry import PLUGIN, _arguments
from test_update_entry import update_entry_case as shared_update_entry_case

update_entry_case = shared_update_entry_case


@pytest.mark.parametrize("project_position", ["before", "after"])
def test_posix_project_controller_precedes_manager_bootstrap(
    tmp_path, native_bash, project_position,
) -> None:
    home = tmp_path / "home"
    tools = tmp_path / "tools"
    runtime = home / ".agent-worktrees"
    (runtime / "bin").mkdir(parents=True)
    tools.mkdir()
    (runtime / "current-version").write_text("selected\n")
    engine = tools / "engine"
    engine.write_text(
        '#!/bin/sh\n'
        'case " $* " in\n'
        '  *" get update-controller "*) printf "%s\\0" "$@" > "$PROBE_ARGS"; '
        'printf "tools/update.py\\n"; exit 0 ;;\n'
        'esac\n'
        'printf "%s\\0" "$@" > "$UPDATE_ARGS"\nexit 47\n'
    )
    engine.chmod(0o755)
    (runtime / "bin" / "resolve-runtime.sh").write_text(
        "AW_PY=" + shlex.quote(bash_path(native_bash, engine)) + "\n",
    )
    uname = tools / "uname"
    uname.write_text('#!/bin/sh\nprintf "Linux\\n"\n')
    uname.chmod(0o755)
    startup = tmp_path / "bash-env"
    startup.write_text('uname() { printf "Linux\\n"; }\n')
    for name in ("curl", "git", "pwsh", "powershell.exe"):
        unavailable = tools / name
        unavailable.write_text('#!/bin/sh\nexit 96\n')
        unavailable.chmod(0o755)
    arguments = (
        ["--project", "sample", "update", "--force", "--skip-modules", "a b"]
        if project_position == "before"
        else ["update", "--force", "--project", "sample", "--skip-modules", "a b"]
    )
    env = os.environ.copy()
    env.update({
        "HOME": bash_path(native_bash, home),
        "USERPROFILE": str(home),
        "PATH": os.pathsep.join([str(tools), str(Path(native_bash).parent), env["PATH"]]),
        "BASH_ENV": bash_path(native_bash, startup),
        "PROBE_ARGS": bash_path(native_bash, tmp_path / "probe.args"),
        "UPDATE_ARGS": bash_path(native_bash, tmp_path / "update.args"),
        "AGENT_WORKTREES_NO_SELFPROVISION": "1",
        "WORKTREE_MANAGER_ROOT": bash_path(native_bash, home / ".worktree-manager"),
        "AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR": bash_path(
            native_bash, home / "providers",
        ),
    })
    result = owned_process.run_owned(
        [native_bash, bash_path(native_bash, PLUGIN / "bin" / "agent-worktrees"), *arguments],
        timeout=20, env=env,
    )
    assert result.returncode == 47, result.stderr
    assert _arguments(tmp_path / "probe.args")[-4:] == [
        "--project", "sample", "get", "update-controller",
    ]
    assert _arguments(tmp_path / "update.args")[-len(arguments):] == arguments


def _windows_shell_double(tmp, monkeypatch) -> None:
    tools = tmp / "tools"
    uname = tools / "uname"
    uname.write_text('#!/bin/sh\nprintf "%s\\n" "MINGW64_NT-fixture"\n')
    uname.chmod(0o755)
    convert = tools / "cygpath"
    convert.write_text(
        '#!/bin/sh\nprintf "%s\\0" "$@" > "$CYGPATH_ARGS"\n'
        'printf "%s\\n" \'C:\\fixture\\agent-worktrees-update-entry.ps1\'\n',
    )
    convert.chmod(0o755)
    host = tools / "pwsh"
    host.write_text(
        '#!/bin/sh\nprintf "%s\\0" "$@" > "$WINDOWS_ENTRY_ARGS"\n'
        'printf "%s" "$MSYS2_ARG_CONV_EXCL" > "$WINDOWS_CONVERSION"\n'
        'printf "%s" "$AGENT_WORKTREES_INTERNAL_UPDATE_PAYLOAD" > "$WINDOWS_PAYLOAD_MODE"\n'
        'exit 37\n',
    )
    host.chmod(0o755)
    monkeypatch.setenv("CYGPATH_ARGS", str(tmp / "cygpath.args"))
    monkeypatch.setenv("WINDOWS_ENTRY_ARGS", str(tmp / "windows.args"))
    monkeypatch.setenv("WINDOWS_CONVERSION", str(tmp / "windows.conversion"))
    monkeypatch.setenv("WINDOWS_PAYLOAD_MODE", str(tmp / "windows-payload-mode"))


@pytest.mark.parametrize("front", ["global", "payload"])
def test_update_entry_git_sh_delegates_before_windows_provider_parsing(
    update_entry_case, native_bash, monkeypatch, front,
) -> None:
    tmp, home, _resolver = update_entry_case
    _windows_shell_double(tmp, monkeypatch)
    registry = home / ".agent-worktrees/control-plane-providers.d"
    registry.mkdir()
    (registry / "worktree-manager.json").write_text(json.dumps({
        "schema_version": 1, "provider": "worktree-manager",
        "provider_root": r"C:\selected\manager",
        "command": [r"C:\selected\bin\worktree-manager.cmd"],
    }))
    relative = "bin/agent-worktrees" if front == "global" else "bin/payload/agent-worktrees"
    entry = PLUGIN / relative
    args = ["--project", "sample", "update", "--force", "--skip-modules",
            "module with spaces", "/literal/value"]
    result = subprocess.run(
        [native_bash, str(entry), *args], capture_output=True, timeout=15,
    )
    assert result.returncode == 37, result.stderr.decode()
    assert _arguments(tmp / "windows.args") == [
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
        r"C:\fixture\agent-worktrees-update-entry.ps1", *args,
    ]
    assert _arguments(tmp / "cygpath.args") == [
        "-w", "--", str(PLUGIN / "bin/agent-worktrees-update-entry.ps1"),
    ]
    assert (tmp / "windows.conversion").read_text() == "*"
    assert (tmp / "windows-payload-mode").read_text() == ("1" if front == "payload" else "0")
    assert not (tmp / "resolved").exists()
    assert not (tmp / "python.args").exists()
    assert not (tmp / "manager-called").exists()
    assert not (tmp / "curl.args").exists()


@pytest.mark.parametrize("args", [
    ["status"], ["update", "--no-manager"], ["unknown", "update"],
    ["update", "--request-status", "synthetic-request"],
    ["update", "--request-status=synthetic-request"],
    ["update", "--recovery-status"],
    ["update", "--recover-after-restart"],
    ["update", "--recover-for-repair"],
])
def test_update_entry_git_sh_does_not_redirect_other_dispatch(
    update_entry_case, native_bash, monkeypatch, args,
) -> None:
    tmp, _home, _resolver = update_entry_case
    _windows_shell_double(tmp, monkeypatch)
    result = subprocess.run(
        [native_bash, str(PLUGIN / "bin/agent-worktrees"), *args],
        capture_output=True, timeout=15,
    )
    assert result.returncode == 19
    assert not (tmp / "windows.args").exists()
    assert (tmp / "resolved").exists()
