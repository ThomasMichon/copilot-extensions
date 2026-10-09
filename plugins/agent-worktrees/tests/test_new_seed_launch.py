"""Creation prompt ownership survives both Manager resolve hops and rejection."""

from __future__ import annotations

import importlib.util
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import launch_seed_exec as new_seed_launch, launch_seed_state, resolve_cli, tracking
from agent_worktrees.sessions import LiveVerdict
from test_resolve_seed_delivery import _args, _stub_launch_plumbing
from test_worktree_creation_seed import _create_config, _stub_create_worktree_core_internals

ROOT = Path(__file__).resolve().parents[3]
BASH = (
    shutil.which("bash", path=r"C:\Program Files\Git\usr\bin")
    or shutil.which("bash", path=r"C:\Program Files\Git\bin")
    or (shutil.which("bash") if os.name != "nt" else None)
)


def test_resolve_parser_defaults_to_deferred_new_ownership():
    parser = argparse.ArgumentParser()
    resolve_cli.add_parsers(parser.add_subparsers())
    args = parser.parse_args(["resolve", "--json", "--worktree-id", "wt-a"])
    assert args.defer_new_seed is True


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell unavailable")
@pytest.mark.parametrize("case", ["live", "unknown"])
def test_new_two_hop_guard_rejection_and_retry(tmp_path, monkeypatch, capfd, case):
    import agent_worktrees.__main__ as core

    config = _create_config(tmp_path)
    record_root = tmp_path / "tracking"
    monkeypatch.setattr(core.cfg, "tracking_dir", lambda: record_root)
    _stub_create_worktree_core_internals(monkeypatch, tmp_path, config)
    seed = "New task with quotes ' \"\nand a second line"
    first = core._create_worktree_core(config, pending_seed=seed)
    worktree_id = first["worktree"]["id"]
    record_path = record_root / f"{worktree_id}.yaml"
    capfd.readouterr()

    # Execute the Manager's real delegation code without importing its TUI.
    spec = importlib.util.spec_from_file_location(
        "relocated", ROOT / "worktree-manager/src/worktree_manager/relocated_launch.py",
    )
    relocated = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(relocated)
    monkeypatch.setattr(relocated, "_core", lambda: SimpleNamespace(_is_windows=lambda: True))
    forwarded = []
    monkeypatch.setattr(
        relocated, "subprocess", SimpleNamespace(
            Popen=lambda argv: forwarded.append(argv) or SimpleNamespace(wait=lambda: 0),
        ),
    )
    req = SimpleNamespace(
        mode="new", project="demo-repo", worktree_id=None, seed_prompt=seed,
        new_window=False, no_mux=False,
    )
    assert relocated._run_relocated_mux_launch(
        req, SimpleNamespace(**first["launch"], raw=first["launch"]), Path("launch-session.ps1"),
    ) == 0
    assert "--seed" not in forwarded[0]
    seed_id = first["launch"]["seed_id"]
    assert forwarded[0][-5:] == [
        "--worktree-id", worktree_id, "--stage-launch-seed", "--seed-id", seed_id,
    ]
    assert launch_seed_state.peek(record_path).text == seed

    _stub_launch_plumbing(monkeypatch, config)
    monkeypatch.setattr(
        core.sessions, "verify_worktree_active",
        lambda *_a, **_k: (
            LiveVerdict(active=True, mux_live=True) if case == "live"
            else LiveVerdict(probes_ok=False, mux_probe_ok=False)
        ),
    )
    capture = tmp_path / "capture.ps1"
    capture.write_text("ConvertTo-Json -InputObject @($args) -Compress\n", encoding="utf-8")
    monkeypatch.setattr(
        core, "_build_launch_cmd",
        lambda *_a, **_k: [
            shutil.which("pwsh"), "-NoProfile", "-NoLogo", "-File",
            str(ROOT / "plugins/agent-worktrees/scripts/default-setup.ps1"),
            "-RuntimePython", sys.executable, "-CopilotPath", str(capture),
        ],
    )

    def second_resolve(expected=0):
        assert resolve_cli.cmd_resolve(_args(
            worktree_id=worktree_id, seed_id=seed_id,
        )) == expected
        if expected:
            return json.loads(capfd.readouterr().out)
        return json.loads(capfd.readouterr().out)["launch"]

    assert "staged" in second_resolve(3)["error"]
    assert launch_seed_state.peek(record_path).text == seed
    monkeypatch.setattr(core.sessions, "verify_worktree_active", lambda *_a, **_k: LiveVerdict())
    plan = second_resolve()
    assert plan["seed_pending"] is True and plan["seed_claimed"] is False
    assert launch_seed_state.peek(record_path).text == seed
    launcher = (ROOT / "worktree-manager/bin/launch-session.ps1").read_text(encoding="utf-8")
    start = launcher.index("function Assert-AwColdResume {")
    guard = launcher[start:launcher.index("\n}", start) + 2]
    bundle = {
        "worktree_id": worktree_id,
        "facts": {"liveness": {
            "confirmed": case != "unknown", "value": {"active": case == "live"},
        }},
    }
    engine = tmp_path / "engine.ps1"
    engine.write_text(f"Write-Output '{json.dumps(bundle)}'\nexit 0\n", encoding="utf-8")
    script = tmp_path / "reject.ps1"
    script.write_text(
        f"$plan = '{json.dumps(plan)}' | ConvertFrom-Json\n"
        "$CopilotArgs = @()\n"
        f"$VenvPython = '{engine}'\n"
        "function Write-SetupLog { param($Message,$Level) }\n"
        f"{guard}\nAssert-AwColdResume\nthrow 'Must not execute'\n",
        encoding="utf-8",
    )
    rejected = subprocess.run(
        [shutil.which("pwsh"), "-NoProfile", "-File", str(script)],
        capture_output=True, text=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert rejected.returncode == 3, rejected.stderr
    assert launch_seed_state.peek(record_path).text == seed

    monkeypatch.setattr(core.sessions, "verify_worktree_active", lambda *_a, **_k: LiveVerdict())
    retry = second_resolve()
    # Execute the actual deferred CLI and the real PowerShell argv consumer.
    def execute():
        return subprocess.run(
            retry["cmd"], capture_output=True, text=True, timeout=20,
            env={**os.environ, "AGENT_WORKTREES_MACHINE_SETTINGS_RECONCILED": "1"},
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

    delivered = execute()
    assert delivered.returncode == 0, delivered.stderr
    assert json.loads(delivered.stdout.splitlines()[-1]) == ["--interactive", seed]
    assert launch_seed_state.peek(record_path) is None
    repeated = execute()
    assert repeated.returncode == 3
    assert "missing or superseded" in repeated.stderr


def test_new_exec_failure_restores_creation_prompt(tmp_path, monkeypatch):
    record = tracking.create_new_record(
        "wt-a", "worktree/wt-a", str(tmp_path / "wt-a"), "demo", "test",
        "windows", tmp_path, pending_seed="New task",
    )
    monkeypatch.setattr(
        new_seed_launch.subprocess, "Popen",
        lambda *_a, **_k: (_ for _ in ()).throw(FileNotFoundError("missing executable")),
    )
    monkeypatch.setattr(
        new_seed_launch.os, "execvp",
        lambda *_a, **_k: (_ for _ in ()).throw(FileNotFoundError("missing executable")),
    )
    assert new_seed_launch.launch(record.yaml_path, ["missing"], invoke=True) == 3
    assert launch_seed_state.peek(record.yaml_path).text == "New task"
    assert launch_seed_state.peek(record.yaml_path).handoff_id is None


def test_uninstrumented_setup_rejects_without_claim_or_execution(tmp_path, monkeypatch):
    record = tracking.create_new_record(
        "wt-a", "worktree/wt-a", str(tmp_path / "wt-a"), "demo", "test",
        "windows", tmp_path, pending_seed="New task",
    )
    def unexpected(*args, **kwargs):
        raise AssertionError("unsupported setup must not execute")
    monkeypatch.setattr(new_seed_launch.subprocess, "Popen", unexpected)
    monkeypatch.setattr(new_seed_launch.os, "execvp", unexpected)
    assert new_seed_launch.launch(record.yaml_path, ["custom-launcher"]) == 3
    assert launch_seed_state.peek(record.yaml_path).text == "New task"


@pytest.mark.parametrize("shell", ["powershell", "bash"])
@pytest.mark.parametrize("kind", ["new", "resume"])
def test_real_setup_child_rejects_before_copilot_and_keeps_seed_for_retry(tmp_path, kind, shell):
    if shell == "powershell" and shutil.which("pwsh") is None:
        pytest.skip("PowerShell unavailable")
    if shell == "bash" and BASH is None:
        pytest.skip("Bash unavailable")
    record = tracking.create_new_record(
        "wt-a", "worktree/wt-a", str(tmp_path / "wt-a"), "demo", "test",
        "windows", tmp_path,
    )
    saved = launch_seed_state.stage(
        record.yaml_path, kind=kind, text="task with ' quotes\nsecond line",
    )
    if shell == "powershell":
        setup = ROOT / "plugins/agent-worktrees/scripts/default-setup.ps1"
        capture = tmp_path / "capture.ps1"
        capture.write_text("ConvertTo-Json -InputObject @($args) -Compress\n", encoding="utf-8")
    else:
        setup = ROOT / "plugins/agent-worktrees/scripts/default-setup.sh"
        capture = tmp_path / "capture.py"
        capture.write_text(
            "import json,sys\nprint(json.dumps(sys.argv[1:]))\n", encoding="utf-8",
        )
    def execute(backend):
        if shell == "powershell":
            command = [
                shutil.which("pwsh"), "-NoProfile", "-NoLogo", "-File", str(setup),
                "-RuntimePython", sys.executable, "-CopilotPath", str(backend),
            ]
        else:
            command = [
                BASH, setup.as_posix(), "--runtime-python", Path(sys.executable).as_posix(),
                "--copilot-path", Path(backend).as_posix(),
            ]
            if Path(backend) == Path(sys.executable):
                command.append(capture.as_posix())
        return subprocess.run(
            new_seed_launch.deferred_command(command, record.yaml_path, seed_id=saved.seed_id),
            capture_output=True, text=True, timeout=25,
            env={**os.environ, "AGENT_WORKTREES_MACHINE_SETTINGS_RECONCILED": "1"},
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    rejected = execute(tmp_path / "nonexistent-copilot.exe")
    assert rejected.returncode != 0
    assert "Configured Copilot executable not found" in rejected.stderr
    seed = launch_seed_state.peek(record.yaml_path).text
    assert seed == "task with ' quotes\nsecond line"
    delivered = execute(capture if shell == "powershell" else sys.executable)
    assert delivered.returncode == 0, delivered.stderr
    assert json.loads(delivered.stdout.splitlines()[-1]) == ["--interactive", seed]
    assert launch_seed_state.peek(record.yaml_path) is None


@pytest.mark.parametrize("verdict", [
    LiveVerdict(), LiveVerdict(active=True, mux_live=True),
    LiveVerdict(probes_ok=False, mux_probe_ok=False),
])
def test_non_json_delegated_resolve_retains_new_owner(tmp_path, monkeypatch, verdict):
    import agent_worktrees.__main__ as core
    from agent_worktrees import resolve_launch_cli as rlc

    config = _create_config(tmp_path)
    monkeypatch.setattr(core.cfg, "tracking_dir", lambda: tmp_path)
    record = tracking.create_new_record(
        "wt-a", "worktree/wt-a", str(tmp_path / "wt-a"), "demo-repo", "test",
        "windows", tmp_path, pending_seed="New task", codename="already-set",
    )
    _stub_launch_plumbing(monkeypatch, config)
    monkeypatch.setattr(rlc.sessions, "verify_worktree_active", lambda *_a, **_k: verdict)
    monkeypatch.setattr(rlc.sessions, "resolve_resume_target", lambda *_a, **_k: None)
    monkeypatch.setattr(rlc.local_cache_refresh, "refresh_local_cache", lambda *_a, **_k: None)
    plans = []
    monkeypatch.setattr(rlc, "_emit_plan", plans.append)
    saved = launch_seed_state.stage(record.yaml_path)
    args = _args(json=False, seed_id=saved.seed_id, dry_run=False, no_fast_forward=True)
    context = rlc.ResolveLaunchContext(config=config, args=args, record=record)
    cold = verdict.probes_ok and not verdict.active
    assert rlc._resolve_resume_context(context) == (0 if cold else 3)
    if cold:
        assert plans[-1]["seed_pending"] is True
        assert plans[-1]["seed_claimed"] is False
    assert launch_seed_state.peek(record.yaml_path).text == "New task"
