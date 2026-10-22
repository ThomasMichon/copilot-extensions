"""Execute generated popup commands with an installed fixture CLI, not a TUI."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs

from _manager_process_fixture import synthetic_runtime

BIN = Path(__file__).resolve().parents[1] / "bin"
PREFIX = 'bind-key -T root C-k display-popup -E -w 80% -h 80% "'
pytestmark = pytest.mark.skipif(os.name != "nt", reason="psmux popup command uses the Windows command shell")

PROGRAM = """
import json, os, sys
from pathlib import Path
runtime = sys.modules.get('_retained_versioned_runtime')
retained = False
if runtime is not None:
    prefix = Path(sys.prefix)
    try:
        with runtime._kernel_lease(prefix.parent.parent, prefix.name,
                                   kind='retention', shared=False, timeout=0.05):
            pass
    except TimeoutError:
        retained = True
print(json.dumps({'arguments':sys.argv[1:], 'prefix':sys.prefix, 'file':__file__,
                  'root':os.environ.get('WORKTREE_MANAGER_ROOT'), 'retained':retained}))
print('popup-stderr', file=sys.stderr)
print('popup-input:' + sys.stdin.readline().strip())
raise SystemExit(17)
"""


def _quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def _fragment(payload, env):
    pwsh = shutil.which("pwsh")
    assert pwsh
    result = subprocess.run([
        pwsh, "-NoProfile", "-NonInteractive", "-Command",
        f". {_quote(BIN / 'session-options.ps1')}; "
        f"Get-AwMuxCompanionKeybindFragment -ManagerRoot {_quote(payload)}",
    ], env=env, capture_output=True, text=True, timeout=15, **no_window_kwargs())
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _execute(fragment, env):
    assert fragment.startswith(PREFIX) and fragment.endswith('"')
    command = fragment[len(PREFIX):-1].replace('\\"', '"')
    assert "-NonInteractive" not in command and "mux_callback" not in command
    return subprocess.run(
        f'"{os.environ["COMSPEC"]}" /d /s /c "{command}"',
        env=env, input="fixture-stdin\n", capture_output=True, text=True,
        timeout=15, **no_window_kwargs(),
    )


def test_generated_retained_popup_command_runs_fixture_cli_without_uv(tmp_path):
    root = tmp_path / "Manager root with spaces"
    slot = synthetic_runtime(root, "1.2.3", program=PROGRAM)
    (root / "current-version").write_text("9.9.9\n")
    pwsh = shutil.which("pwsh")
    assert pwsh
    env = dict(os.environ, PATH=os.pathsep.join([
        str(Path(pwsh).parent), str(Path(os.environ["SystemRoot"]) / "System32"),
    ]), WORKTREE_MANAGER_ROOT=str(tmp_path / "wrong-root"),
       PYTHONPATH=str(tmp_path / "foreign"), __PYVENV_LAUNCHER__=sys.executable)
    assert shutil.which("uv", path=env["PATH"]) is None
    fragment = _fragment(slot / "payload", env)
    # Exercise the real shell-command boundary after source-file quote decoding.
    result = _execute(fragment, env)
    assert result.returncode == 17, result.stdout + result.stderr
    lines = result.stdout.strip().splitlines()
    captured = json.loads(lines[0])
    assert captured["arguments"] == ["companion"]
    assert Path(captured["prefix"]).resolve() == slot.resolve()
    assert Path(captured["file"]).resolve().is_relative_to(slot.resolve())
    assert Path(captured["root"]).resolve() == root.resolve()
    assert captured["retained"] is True
    assert lines[1] == "popup-input:fixture-stdin"
    assert "popup-stderr" in result.stderr


@pytest.mark.parametrize("legacy", [False, True])
def test_generated_source_and_legacy_popup_keep_project_entry(tmp_path, legacy):
    payload = tmp_path / "versions" / "1.2.3" if legacy else tmp_path / "source with spaces"
    (payload / "bin").mkdir(parents=True)
    shutil.copyfile(BIN / "manager-runtime.ps1", payload / "bin" / "manager-runtime.ps1")
    capture = tmp_path / "capture.py"
    capture.write_text(PROGRAM, encoding="utf-8")
    uv = tmp_path / "uv.cmd"
    uv.write_text(
        f'@echo off\n"{sys.executable}" "{capture}" %*\nexit /b %errorlevel%\n',
        encoding="utf-8",
    )
    pwsh = shutil.which("pwsh")
    assert pwsh
    env = dict(os.environ, PATH=os.pathsep.join([
        str(tmp_path), str(Path(pwsh).parent),
        str(Path(os.environ["SystemRoot"]) / "System32"),
    ]))
    for name in ("PYTHONHOME", "PYTHONPATH", "__PYVENV_LAUNCHER__"):
        env.pop(name, None)
    result = _execute(_fragment(payload, env), env)
    assert result.returncode == 17, result.stdout + result.stderr
    lines = result.stdout.strip().splitlines()
    captured = json.loads(lines[0])
    assert captured["arguments"] == [
        "run", "--quiet", "--project", str(payload), "-m", "worktree_manager", "companion",
    ]
    assert captured["retained"] is False
    assert lines[1] == "popup-input:fixture-stdin"
    assert "popup-stderr" in result.stderr
