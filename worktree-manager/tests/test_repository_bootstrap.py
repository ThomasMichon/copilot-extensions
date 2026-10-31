"""Optional caller ordering, refusal and the real inherited-stdio boundary."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import runpy

from agent_procutil import no_window_kwargs
import pytest

from worktree_manager import repository_bootstrap as repository, setup_cli as cli
from worktree_manager.core_install import CoreInstallResult, CoreStatus
from worktree_manager.prereqs import PrereqStatus

URL = "https://example.invalid/team/sample.git"


def providers(monkeypatch, *, ready: bool = True) -> list[str]:
    events: list[str] = []
    monkeypatch.setattr(cli, "detect_baseline", lambda: [])
    monkeypatch.setattr(cli, "provision_plan", lambda _: [])

    def prerequisites(*_, **__):
        events.append("prerequisites")
        return []

    def install(**_):
        events.append("core")
        return CoreInstallResult(None, False, reason="already-installed")

    def status():
        events.append("readiness")
        return CoreStatus("installed" if ready else "absent", ready, ready, "stub", "runtime")

    monkeypatch.setattr(cli, "provision_apply", prerequisites)
    monkeypatch.setattr(cli, "core_status", status)
    monkeypatch.setattr(cli, "install_command", lambda: None)
    monkeypatch.setattr(cli, "install_core", install)
    return events


@pytest.mark.parametrize("argv", [
    ["--repository-target", "somewhere"],
    ["--repository-target", ""],
    ["--context", "harness-a"],
    ["--repository", URL, "--context", ""],
    ["--repository", URL, "--context", "../invalid"],
    ["--trust-repository-setup"],
    ["--repository-commit", "a" * 40],
    ["--repository", URL, "--repository-commit", ""],
    ["--repository", URL, "--repository-commit", "main"],
    ["--repository", URL, "--repository-commit", "a" * 39],
    ["--repository", URL, "--repository-commit", "g" * 40],
    ["--repository", URL, "--repository-commit", "a" * 41],
    ["--repository"], ["--unknown"], ["--repo", URL],
    ["--repository", "file:///local/source"],
    ["--repository", "https://name:secret@example.invalid/source"],
    ["--repository", "https://example.invalid/source?query"],
    ["--repository", "https://example.invalid/source\u009b"],
    ["--repository", URL, "--repository-target", "relative"],
    ["--repository", URL, "--repository-target", ""],
])
def test_invalid_intent_is_rejected_before_any_provision(monkeypatch, argv) -> None:
    monkeypatch.setattr(cli, "detect_baseline", lambda: pytest.fail("invalid intent provisioned"))
    assert cli.cmd_setup(["--apply", *argv], banner="Example") == 2


@pytest.mark.parametrize("commit", [None, "A" * 40, "B" * 64])
def test_core_then_fresh_readiness_then_repository(monkeypatch, tmp_path, commit) -> None:
    events = providers(monkeypatch)
    calls = []

    def passthrough(project, argv, **kwargs):
        assert kwargs == {"terminal_aware": True}
        events.append("repository")
        calls.append((project, argv))
        return 0

    monkeypatch.setattr(repository, "run_engine_passthrough", passthrough)
    assert cli.cmd_setup([
        "--apply", "--repository", URL, "--repository-target", str(tmp_path / "with spaces"),
        "--trust-repository-setup",
        *(["--repository-commit", commit] if commit else []),
    ], banner="Example") == 0
    assert events[-3:] == ["core", "readiness", "repository"]
    assert calls == [(None, [
        "repos", "setup", URL, "--apply", "--target", str(tmp_path / "with spaces"),
        *(["--commit", commit.lower()] if commit else []),
        "--trust-repository-setup",
    ])]


def test_unready_core_never_reaches_repository(monkeypatch) -> None:
    providers(monkeypatch, ready=False)
    monkeypatch.setattr(repository, "run_engine_passthrough",
                        lambda *a: pytest.fail("unready core reached repository"))
    assert cli.cmd_setup(["--apply", "--repository", URL], banner="Example") == 1


def test_unready_prerequisites_never_reach_repository(monkeypatch) -> None:
    events = providers(monkeypatch)
    monkeypatch.setattr(cli, "detect_baseline", lambda: [PrereqStatus("git", False)])
    monkeypatch.setattr(repository, "run_engine_passthrough",
                        lambda *a: pytest.fail("missing Git reached repository"))
    assert cli.cmd_setup(["--apply", "--repository", URL], banner="Example") == 1
    assert "core" not in events


def test_plan_never_invokes_repository_engine(monkeypatch, capsys) -> None:
    providers(monkeypatch)
    monkeypatch.setattr(repository, "run_engine_passthrough",
                        lambda *a: pytest.fail("plan invoked repository engine"))
    assert cli.cmd_setup(["--repository", URL], banner="Example") == 0
    assert "remains uninspected" in capsys.readouterr().out


def test_no_repository_preserves_existing_setup(monkeypatch) -> None:
    events = providers(monkeypatch)
    monkeypatch.setattr(repository, "run", lambda *a: pytest.fail("no repository requested"))
    assert cli.cmd_setup(["--apply"], banner="Example") == 0
    assert events == ["readiness", "prerequisites", "core"]


@pytest.mark.parametrize("status", [1, 7, 130])
def test_repository_failure_is_preserved_without_repeating_core(monkeypatch, status) -> None:
    events = providers(monkeypatch)
    monkeypatch.setattr(repository, "run_engine_passthrough", lambda *a, **k: status)
    assert cli.cmd_setup(["--apply", "--repository", URL], banner="Example") == status
    assert events.count("core") == 1


def test_old_engine_refuses_only_optional_stage(monkeypatch, capsys) -> None:
    from worktree_manager.engine_client import EngineError

    events = providers(monkeypatch)

    def unavailable(*_, **kwargs):
        raise EngineError("repos setup is unsupported")

    monkeypatch.setattr(repository, "run_engine_passthrough", unavailable)
    assert cli.cmd_setup(["--apply", "--repository", URL], banner="Example") == 1
    assert events.count("core") == 1
    assert "core is retained" in capsys.readouterr().out


def test_real_engine_boundary_inherits_input_exact_argv_and_exit(tmp_path: Path) -> None:
    engine = tmp_path / "synthetic engine.py"
    log = tmp_path / "owner result.json"
    engine.write_text(
        "import json, pathlib, sys\n"
        "pathlib.Path(sys.argv[1]).write_text(json.dumps({"
        "'argv':sys.argv[2:], 'input':sys.stdin.readline()}))\n"
        "print('owner-progress', flush=True)\n"
        "raise SystemExit(7)\n", encoding="utf-8",
    )
    driver = """
import sys
from worktree_manager import engine_client, repository_bootstrap as repository
engine_client.set_engine_command([sys.executable, sys.argv[1], sys.argv[2]])
raise SystemExit(repository.run(repository.parse([
    '--apply', '--repository', sys.argv[3],
    '--repository-target', sys.argv[4], '--trust-repository-setup'
])))
"""
    before = dict(os.environ)
    result = subprocess.run(
        [sys.executable, "-c", driver, str(engine), str(log), URL, str(tmp_path / "chosen source")],
        input="caller-owned-stdin\n", capture_output=True, text=True, timeout=120,
        **no_window_kwargs(),
    )
    assert result.returncode == 7, result.stderr
    assert "owner-progress" in result.stdout
    assert json.loads(log.read_text()) == {
        "argv": ["repos", "setup", URL, "--apply", "--target", str(tmp_path / "chosen source"),
                 "--trust-repository-setup"],
        "input": "caller-owned-stdin\n",
    }
    assert dict(os.environ) == before


@pytest.mark.parametrize("selection", ["baseline", "pin", "context"])
def test_prior_engine_parser_preserves_baseline_and_refuses_pin_without_replay(tmp_path, selection) -> None:
    engine = tmp_path / "prior engine.py"
    calls = tmp_path / "calls.json"
    effect = tmp_path / "effect"
    engine.write_text(
        "import argparse,json,pathlib,sys\n"
        "with pathlib.Path(sys.argv[1]).open('a') as stream:\n"
        "    stream.write(json.dumps(sys.argv[3:]) + '\\n')\n"
        "parser=argparse.ArgumentParser(allow_abbrev=False)\n"
        "parser.add_argument('verb', choices=['repos'])\n"
        "parser.add_argument('subcommand', choices=['setup'])\n"
        "parser.add_argument('repository')\n"
        "parser.add_argument('--target')\n"
        "parser.add_argument('--apply', action='store_true')\n"
        "parser.add_argument('--trust-repository-setup', action='store_true')\n"
        "parser.parse_args(sys.argv[3:])\n"
        "pathlib.Path(sys.argv[2]).write_text('baseline accepted')\n",
        encoding="utf-8",
    )
    driver = """
import sys
from worktree_manager import engine_client, repository_bootstrap as repository
engine_client.set_engine_command([sys.executable, sys.argv[1], sys.argv[2], sys.argv[3]])
raise SystemExit(repository.run(repository.parse([
    '--apply', '--repository', sys.argv[4], *sys.argv[5:]
])))
"""
    result = subprocess.run(
        [sys.executable, "-c", driver, str(engine), str(calls), str(effect), URL,
         *(["--repository-commit", "a" * 40] if selection == "pin" else []),
         *(["--context", "harness-a"] if selection == "context" else [])],
        capture_output=True, text=True, timeout=120, **no_window_kwargs(),
    )
    assert result.returncode == (0 if selection == "baseline" else 2), result.stdout + result.stderr
    assert effect.exists() == (selection == "baseline")
    invocations = [json.loads(line) for line in calls.read_text().splitlines()]
    assert len(invocations) == 1
    if selection != "baseline":
        flag = "--commit" if selection == "pin" else "--context"
        assert "unrecognized arguments: " + flag in result.stderr
        assert flag in invocations[0]


@pytest.mark.skipif(os.name != "nt", reason="native windowless Manager boundary")
def test_native_windowless_manager_does_not_surface_engine(tmp_path: Path) -> None:
    pythonw = Path(getattr(sys, "_base_executable", sys.executable)).with_name("pythonw.exe")
    assert pythonw.is_file()
    engine = tmp_path / "engine.py"
    engine.write_text("raise SystemExit(7)\n", encoding="utf-8")
    paths = tmp_path / "paths.json"
    paths.write_text(json.dumps(sys.path), encoding="utf-8")
    result = tmp_path / "result.json"
    driver = tmp_path / "manager.py"
    driver.write_text("""
import ctypes,json,pathlib,subprocess,sys
sys.path[:0] = json.loads(pathlib.Path(sys.argv[1]).read_text())
from worktree_manager import engine_client, repository_bootstrap as repository
engine_client.set_engine_command([str(pathlib.Path(sys.executable).with_name('python.exe')), sys.argv[2]])
assert ctypes.windll.kernel32.GetConsoleCP() == 0
original = subprocess.run
def run(argv, **kwargs):
    assert kwargs.get('creationflags', 0) & subprocess.CREATE_NO_WINDOW
    return original(argv, **kwargs)
engine_client.subprocess.run = run
status = repository.run(repository.parse([
    '--apply', '--repository', 'https://example.invalid/team/sample.git'
]))
pathlib.Path(sys.argv[3]).write_text(json.dumps({'status': status}))
""", encoding="utf-8")
    observer = runpy.run_path(str(
        Path(__file__).resolve().parents[2] / "libs" / "agent-procutil" / "tests"
        / "test_spawn_daemon_windows_integration.py"
    ))
    with observer["_WindowObserver"](*observer["_configure_win32"]()) as windows:
        process = subprocess.run(
            [str(pythonw), str(driver), str(paths), str(engine), str(result)],
            capture_output=True, text=True, timeout=120, **no_window_kwargs(),
        )
    assert process.returncode == 0, process.stdout + process.stderr
    assert result.is_file(), process.stdout + process.stderr
    assert json.loads(result.read_text()) == {"status": 7}
    assert windows.surfaced == {} and windows.focus_changes == {}
