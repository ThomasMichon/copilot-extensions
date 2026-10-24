"""Unit routing contracts, not evidence that a native Windows shell ran."""
from __future__ import annotations

import json
import subprocess

import pytest
from test_update_entry import PLUGIN, _arguments
from test_update_entry import update_entry_case as shared_update_entry_case

update_entry_case = shared_update_entry_case


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
        'printf "%s" "$MSYS2_ARG_CONV_EXCL" > "$WINDOWS_CONVERSION"\nexit 37\n',
    )
    host.chmod(0o755)
    monkeypatch.setenv("CYGPATH_ARGS", str(tmp / "cygpath.args"))
    monkeypatch.setenv("WINDOWS_ENTRY_ARGS", str(tmp / "windows.args"))
    monkeypatch.setenv("WINDOWS_CONVERSION", str(tmp / "windows.conversion"))


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
