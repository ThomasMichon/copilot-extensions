"""Real launch wrappers refuse before diagnostics or automatic preparation."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / "worktree-manager/bin"
BASH = shutil.which("bash") if os.name != "nt" else None
PWSH = shutil.which("pwsh")

OWNER = r'''
import json, os, subprocess, sys
from pathlib import Path

args = sys.argv[1:]
if args[:1] == ["-c"]:
    result = subprocess.run(
        [sys.executable, *args], input=sys.stdin.buffer.read(), check=False, timeout=25,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    raise SystemExit(result.returncode)
if args[:2] != ["-m", "agent_worktrees"]:
    raise SystemExit(91)
argv = args[2:]
with Path(os.environ["TEST_CALLS"]).open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(argv) + "\n")
if argv[:1] == ["--project"]:
    argv = argv[2:]
target = Path(os.environ["TEST_TARGET"])
if argv[:1] == ["resolve"]:
    action = os.environ.get("TEST_ACTION", "exec")
    plan = {
        "action": action, "work_dir": str(target), "status_path": str(target),
        "worktree_id": "fixture-wt", "project": "fixture",
        "cmd": ["must-not-launch-copilot"], "no_mux": True,
    }
    if os.environ.get("TEST_NO_PLAN_PATH") == "1":
        plan.pop("work_dir")
        plan.pop("status_path")
    print(json.dumps(plan))
elif argv[:2] == ["agent-policy", "check"]:
    if os.environ.get("TEST_MALFORMED") == "1":
        print('{"version":1,"allowed":true}')
    else:
        from repository_policy import AgentHostingError, require_agent_hosting
        try:
            policy = require_agent_hosting(argv[argv.index("--path") + 1])
        except AgentHostingError as error:
            print(json.dumps({"version":1, "code":error.code, "error":str(error)}))
            raise SystemExit(3)
        print(json.dumps({**policy.to_dict(), "allowed":True}))
elif argv[:2] == ["get", "repo-dir"]:
    print(target)
elif argv[:1] in [["status"], ["repos"], ["create"]]:
    print('{"inspection":true}')
else:
    Path(os.environ["TEST_EFFECT"]).write_text(json.dumps(argv), encoding="utf-8")
    raise SystemExit(92)
'''


def _quoted_ps(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def _fixture(tmp_path: Path, *, malformed: bool = False) -> tuple[dict[str, str], Path, Path]:
    runtime = tmp_path / "runtime"
    binary = runtime / "bin"
    binary.mkdir(parents=True)
    # The fake AW candidate must carry its declared policy dependency.
    shutil.copytree(
        ROOT / "libs/repository-policy/src/repository_policy",
        runtime / "lib/repository_policy",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    owner = tmp_path / "owner.py"
    owner.write_text(f"#!{sys.executable}\n" + OWNER, encoding="utf-8")
    owner.chmod(0o700)
    ps_owner = tmp_path / "owner.ps1"
    ps_owner.write_text(
        "if ($MyInvocation.ExpectingInput) {\n"
        f"    $input | & {_quoted_ps(Path(sys.executable))} {_quoted_ps(owner)} @args\n"
        "} else {\n"
        f"    & {_quoted_ps(Path(sys.executable))} {_quoted_ps(owner)} @args\n"
        "}\nexit $LASTEXITCODE\n",
        encoding="utf-8",
    )
    (binary / "resolve-runtime.sh").write_text(f"AW_PY='{owner}'\n", encoding="utf-8")
    (binary / "resolve-runtime.ps1").write_text(f"$AwPy = {_quoted_ps(ps_owner)}\n", encoding="utf-8")
    target = tmp_path / "execution"
    target.mkdir()
    (target / ".git").mkdir()
    policy = target / ".copilot-extensions/agent-worktrees/config.yaml"
    policy.parent.mkdir(parents=True)
    policy.write_text(f"expose_agent: {'true' if malformed else 'false'}\n", encoding="utf-8")
    setup = target / "tools/setup"
    setup.mkdir(parents=True)
    (setup / "setup.sh").write_text("touch setup-was-run\n", encoding="utf-8")
    (setup / "setup.ps1").write_text("Set-Content setup-was-run yes\n", encoding="utf-8")
    temp = tmp_path / "temporary"
    logs = temp / "worktree-setup-logs"
    logs.mkdir(parents=True)
    for index in range(12):
        (logs / f"setup-old-{index}.log").write_text(f"retained {index}\n", encoding="utf-8")
    env = dict(os.environ)
    for key in (
        "COPILOT_EXTENSIONS_CONTEXT", "COPILOT_PLUGIN_ROOT", "WORKTREE_SETUP_LOG",
        "AGENT_WORKTREES_LAUNCH_RECOVERY_ANCHOR", "WORKTREE_NO_UPDATE", "WORKTREE_NO_RECONCILE",
    ):
        env.pop(key, None)
    env.update({
        "AGENT_RT_ROOT": str(runtime), "HOME": str(tmp_path), "USERPROFILE": str(tmp_path),
        "TEMP": str(temp), "TMPDIR": str(temp), "WORKTREE_BLOCKING_INSTALL": "1",
        "WORKTREE_NO_MUX": "1",
        "AGENT_WORKTREES_LAUNCH_TRACE": str(tmp_path / "trace.jsonl"),
        "TEST_TARGET": str(target), "TEST_CALLS": str(tmp_path / "calls.jsonl"),
        "TEST_EFFECT": str(tmp_path / "unexpected-preparation"),
        "PYTHONPATH": str(runtime / "lib"),
    })
    if malformed:
        env["TEST_MALFORMED"] = "1"
    return env, target, logs


def _calls(env: dict[str, str]) -> list[list[str]]:
    path = Path(env["TEST_CALLS"])
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def _assert_no_preparation(tmp_path: Path, logs: Path, before: dict[str, bytes]) -> None:
    assert {p.name: p.read_bytes() for p in logs.iterdir()} == before
    assert not (tmp_path / "trace.jsonl").exists()
    assert not (tmp_path / "unexpected-preparation").exists()
    assert not list(logs.glob("progress-*"))
    assert not (tmp_path / "execution/setup-was-run").exists()


def _run(shell: str, target: Path, env: dict[str, str], args: list[str]) -> subprocess.CompletedProcess:
    if shell == "bash":
        argv = [BASH, str(BIN / "launch-session.sh"), *args]
    else:
        # The read-only Git-root probe in recovery must not touch a host checkout.
        entry = target.parent / "entry.ps1"
        entry.write_text(
            "function git { $global:LASTEXITCODE = 1 }\n"
            "Set-Alias -Name pwsh.exe -Value (Get-Command pwsh).Source\n"
            f"& {_quoted_ps(BIN / 'launch-session.ps1')} @args\nexit $LASTEXITCODE\n",
            encoding="utf-8",
        )
        argv = [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(entry), *args]
    return subprocess.run(
        argv, cwd=target, env=env, text=True, capture_output=True, check=False, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


@pytest.mark.parametrize("exit_code", [0, 23])
def test_owner_fixture_preserves_python_code_stdio_arguments_and_exit(
    tmp_path: Path, exit_code: int,
) -> None:
    env, target, _logs = _fixture(tmp_path)
    code = (
        "import json,sys\n"
        "print(json.dumps({'args':sys.argv[1:],'input':sys.stdin.read()}))\n"
        "sys.exit(int(sys.argv[1]))\n"
    )
    arguments = [str(exit_code), "argument with spaces", 'quoted "value"']
    result = subprocess.run(
        [sys.executable, str(tmp_path / "owner.py"), "-c", code, *arguments],
        cwd=target, env=env, text=True, input="fixture stdin\n",
        capture_output=True, check=False, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == exit_code, result.stderr
    assert json.loads(result.stdout) == {"args": arguments, "input": "fixture stdin\n"}


@pytest.mark.parametrize("shell", ["bash", "pwsh"])
@pytest.mark.parametrize("mode", ["exec", "refresh", "refresh-anchor", "recovery", "malformed"])
def test_denied_target_precedes_log_pruning_update_staging_registration_and_setup(
    tmp_path: Path, shell: str, mode: str,
) -> None:
    if (shell == "bash" and BASH is None) or (shell == "pwsh" and PWSH is None):
        pytest.skip(f"{shell} unavailable")
    env, target, logs = _fixture(tmp_path, malformed=mode == "malformed")
    env["TEST_ACTION"] = "refresh" if mode.startswith("refresh") else "exec"
    if mode == "refresh-anchor":
        env["TEST_NO_PLAN_PATH"] = "1"
    if shell == "bash" and mode == "recovery":
        fake_bin = tmp_path / "fake-bin"
        fake_bin.mkdir()
        git = fake_bin / "git"
        git.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        git.chmod(0o700)
        env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
    before = {p.name: p.read_bytes() for p in logs.iterdir()}
    result = _run(shell, target, env, ["--recovery"] if mode == "recovery" else ["--worktree-id", "fixture-wt"])
    assert result.returncode == 3, result.stdout + result.stderr
    _assert_no_preparation(tmp_path, logs, before)
    calls = _calls(env)
    assert any(call[:2] == ["agent-policy", "check"] for call in calls)
    if mode == "refresh-anchor":
        assert any(call[-2:] == ["get", "repo-dir"] for call in calls)
    assert not any(
        any(verb in call for verb in ("stage-update", "launch-update", "register-launch", "update"))
        for call in calls
    )


@pytest.mark.parametrize("shell", ["bash", "pwsh"])
@pytest.mark.parametrize("args", [
    ["repos", "list", "--json"], ["status", "--json"],
    ["create", "--json", "--no-owner", "--no-pair"],
])
def test_source_only_direct_commands_have_no_implicit_maintenance_or_logs(
    tmp_path: Path, shell: str, args: list[str],
) -> None:
    if (shell == "bash" and BASH is None) or (shell == "pwsh" and PWSH is None):
        pytest.skip(f"{shell} unavailable")
    env, target, logs = _fixture(tmp_path)
    before = {p.name: p.read_bytes() for p in logs.iterdir()}
    result = _run(shell, target, env, args)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["inspection"] is True
    assert _calls(env) == [args]
    _assert_no_preparation(tmp_path, logs, before)


@pytest.mark.parametrize("shell", ["bash", "pwsh"])
def test_project_input_validation_precedes_owner_calls_and_all_preparation(
    tmp_path: Path, shell: str,
) -> None:
    if (shell == "bash" and BASH is None) or (shell == "pwsh" and PWSH is None):
        pytest.skip(f"{shell} unavailable")
    env, target, logs = _fixture(tmp_path)
    before = {p.name: p.read_bytes() for p in logs.iterdir()}
    result = _run(shell, target, env, ["--project"])
    assert result.returncode == 2, result.stdout + result.stderr
    assert _calls(env) == []
    _assert_no_preparation(tmp_path, logs, before)


@pytest.mark.parametrize("shell", ["bash", "pwsh"])
def test_allowed_recovery_keeps_setup_usable_after_positive_owner_answer(
    tmp_path: Path, shell: str,
) -> None:
    if (shell == "bash" and BASH is None) or (shell == "pwsh" and PWSH is None):
        pytest.skip(f"{shell} unavailable")
    env, target, logs = _fixture(tmp_path, malformed=True)
    env.pop("TEST_MALFORMED")
    if shell == "bash":
        fake_bin = tmp_path / "fake-bin"
        fake_bin.mkdir()
        git = fake_bin / "git"
        git.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        git.chmod(0o700)
        env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
    result = _run(shell, target, env, ["--recovery"])
    assert result.returncode == 0, result.stdout + result.stderr
    assert (target / "setup-was-run").exists()
    assert (tmp_path / "trace.jsonl").exists()
    assert list(logs.glob("setup-*.log"))
    assert not (tmp_path / "unexpected-preparation").exists()
    assert len(_calls(env)) == 1
    assert _calls(env)[0][:2] == ["agent-policy", "check"]
