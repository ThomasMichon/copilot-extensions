"""resume-prompt-durable-seed-and-mux-fix: ``resolve --worktree-id --json``
delivers a seed as a durable ``--interactive`` argument on the returned
launch command itself -- never a mux pane send-keys side-channel -- so it
works identically whether the real launcher ends up wrapping this command in
a mux pane or running it directly (``--no-mux``). Exercises the REAL
``_resolve_json_mode`` worktree-id branch end to end (the one
``agent-worktrees resolve --worktree-id <id> --json`` -- what Worktree
Manager's Picker and ``launch-session.{ps1,sh}`` actually call -- hits),
stubbing only true I/O boundaries (git, mux, the launch-profile/env
plumbing), exactly mirroring ``test_codename_cli.py``'s own established
pattern for driving ``cmd_resolve`` against a real tracking record.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import agent_worktrees.__main__ as m
from agent_worktrees import config as cfg
from agent_worktrees import launch_seed_state, resolve_cli, tracking
from agent_worktrees.sessions import LiveVerdict


def _stub_launch_plumbing(monkeypatch, config: cfg.Config) -> None:
    for record in tracking.list_records(cfg.tracking_dir()):
        Path(record.worktree_path).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(m.cfg, "load_config", lambda *a, **k: config)
    monkeypatch.setattr(m.cfg, "detect_platform", lambda: config.platform)
    # Seed-routing fixtures use synthetic checkout paths; native trust has its own suite.
    monkeypatch.setattr(m.permissions, "ensure_trusted_folder", lambda *a: False)
    monkeypatch.setattr(
        m, "_preflight_launch",
        lambda *_a, **_k: SimpleNamespace(error=None, config_root_path=None),
    )
    monkeypatch.setattr(
        m, "_launch_profile_selection",
        lambda *_a, **_k: SimpleNamespace(profile=None, assignment=None),
    )
    monkeypatch.setattr(m, "_reflect_assignment", lambda *_a, **_k: None)
    monkeypatch.setattr(m, "_build_launch_cmd", lambda *_a, **_k: ["copilot"])
    monkeypatch.setattr(m, "_repo_session_env", lambda *_a, **_k: {})
    monkeypatch.setattr(m, "_build_env", lambda *_a, **_k: {})
    monkeypatch.setattr(m, "_apply_assignment_env", lambda env, _selection: env)
    monkeypatch.setattr(m.activity, "log_event", lambda *_a, **_k: None)
    monkeypatch.setattr(m.sessions, "find_latest_session_id_fast", lambda *_a, **_k: None)
    monkeypatch.setattr(m.sessions, "verify_worktree_active", lambda *_a, **_k: LiveVerdict())


def _create_config(tmp_path: Path, **overrides) -> cfg.Config:
    anchor = tmp_path / "anchor"
    anchor.mkdir()
    base = dict(
        srcroot=str(tmp_path),
        machine="test",
        platform="windows",
        repo_name="demo-repo",
        repos={
            "demo-repo": cfg.RepoConfig(
                anchor=str(anchor),
                worktree_root=str(tmp_path / "worktrees"),
            )
        },
    )
    base.update(overrides)
    config = cfg.Config(**base)
    project_dir = cfg.project_dir(config.repo_name)
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "config.yaml").write_text(json.dumps({
        "repo_name": config.repo_name, "machine": config.machine,
        "repos": {config.repo_name: {"anchor": str(anchor)}},
    }), encoding="utf-8")
    return config


def _args(**overrides):
    base = dict(
        json=True, base=False, new_worktree=False, auto=False, machine=None,
        environment=None, worktree_id="wt-a", codename=None, seed=None,
        bare_resume=False, restore=False, target_no_mux=False, no_mux=False,
        no_resume=False,
        defer_new_seed=True,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def _resume_plan(config, record, monkeypatch, capfd, *, surface, command=None, selection_calls=None, **kwargs):
    from agent_worktrees import resolve_launch_cli as rlc

    args = _args(
        json=surface == "json", dry_run=False, no_fast_forward=True,
        profile=None, **kwargs,
    )
    command = command if command is not None else ["copilot"]
    def selection(*args, **kwargs):
        if selection_calls is not None:
            selection_calls.append(kwargs)
        return SimpleNamespace(profile=None, assignment=None)
    monkeypatch.setattr(m, "_build_launch_cmd", lambda *a, **kw: command)
    if surface == "json":
        if selection_calls is not None:
            monkeypatch.setattr(m, "_launch_profile_selection", selection)
        code = resolve_cli.cmd_resolve(args)
        payload = json.loads(capfd.readouterr().out)
        return code, payload.get("launch", payload)
    monkeypatch.setattr(rlc, "_dispatch_validate_profile_assignment_config", lambda *a: None)
    monkeypatch.setattr(rlc.sessions, "resolve_resume_target", lambda *a: None)
    monkeypatch.setattr(rlc.tracking, "stamp_mux_live", lambda *a, **kw: None)
    monkeypatch.setattr(rlc.tracking, "stamp_bound_live", lambda *a, **kw: None)
    monkeypatch.setattr(
        rlc, "_dispatch_launch_profile_selection",
        selection,
    )
    monkeypatch.setattr(rlc, "_dispatch_reflect_assignment", lambda *a: None)
    monkeypatch.setattr(rlc, "_dispatch_apply_assignment_env", lambda env, selection: env)
    monkeypatch.setattr(rlc, "_build_launch_cmd", lambda *a, **kw: command)
    monkeypatch.setattr(rlc, "_build_env", lambda *a, **kw: {})
    monkeypatch.setattr(rlc, "_repo_session_env", lambda *a: {})
    monkeypatch.setattr(rlc, "_preflight_launch", lambda *a: SimpleNamespace(error=None))
    plans = []
    monkeypatch.setattr(rlc, "_emit_plan", plans.append)
    code = rlc._resolve_resume_context(rlc.ResolveLaunchContext(
        config=config, args=args, record=record,
    ))
    return code, plans[-1]


@pytest.mark.parametrize("surface", ["json", "interactive"])
def test_native_resume_is_carried_to_the_real_deferred_owner_boundary(
    tmp_path, monkeypatch, capfd, surface,
):
    monkeypatch.setattr(cfg, "tracking_dir", lambda: tmp_path)
    config = _create_config(tmp_path, auto_fast_forward=False)
    record = tracking.create_new_record(
        "wt-a", "worktree/wt-a", str(tmp_path / "wt-a"), "demo-repo", "test",
        "windows", tmp_path, codename="already-set",
    )
    _stub_launch_plumbing(monkeypatch, config)
    selected = []
    code, plan = _resume_plan(
        config, record, monkeypatch, capfd, surface=surface,
        no_resume=True, native_resume_session="native-original", selection_calls=selected,
    )
    assert code == 0
    command = plan["cmd"]
    assert command[command.index("--native-resume-session") + 1] == "native-original"
    assert "--admission-only" in command
    assert command[-2:] == ["--", "copilot"]
    assert len(selected) == 1
    assert selected[0]["resume_session"] == "native-original"
    assert selected[0]["allocate_new"] is False


@pytest.mark.parametrize("surface", ["json", "interactive"])
@pytest.mark.parametrize("intent", ["plain", "typed", "seed_id"])
def test_live_open_held_backend_admission_preserves_intent_and_refuses_mutation(
    tmp_path, monkeypatch, capfd, surface, intent,
):
    from agent_worktrees import launch_admission

    monkeypatch.setattr(cfg, "tracking_dir", lambda: tmp_path)
    config = _create_config(tmp_path, auto_fast_forward=False)
    record = tracking.create_new_record(
        "wt-a", "worktree/wt-a", str(tmp_path / "wt-a"), "demo-repo", "test",
        "windows", tmp_path, codename="already-set",
    )
    _stub_launch_plumbing(monkeypatch, config)
    saved = launch_seed_state.stage(record.yaml_path, kind="new", text="retain the original")
    before = launch_seed_state.state_path(record.yaml_path).read_bytes()
    monkeypatch.setattr(m.sessions, "verify_worktree_active", lambda *a: LiveVerdict(active=True))
    changes = (
        {"seed": "do not replace"} if intent == "typed"
        else {"seed_id": saved.seed_id} if intent == "seed_id" else {}
    )
    with launch_admission.guard(record.yaml_path):
        code, plan = _resume_plan(
            config, record, monkeypatch, capfd, surface=surface, **changes,
        )
    if intent == "plain":
        assert code == 0 and not plan["seed_pending"]
        assert "--admission-only" in plan["cmd"] and plan["cmd"][-2:] == ["--", "copilot"]
    else:
        assert code == 3 and "launch admission is busy" in plan["error"]
    assert launch_seed_state.state_path(record.yaml_path).read_bytes() == before
    assert launch_seed_state.peek(record.yaml_path) == saved


@pytest.mark.parametrize("surface", ["json", "interactive"])
def test_preplanned_bare_launch_is_fenced_and_preserves_retained_seed(
    tmp_path, monkeypatch, capfd, surface,
):
    from agent_worktrees import launch_admission

    monkeypatch.setattr(cfg, "tracking_dir", lambda: tmp_path)
    config = _create_config(tmp_path, auto_fast_forward=False)
    record = tracking.create_new_record(
        "wt-a", "worktree/wt-a", str(tmp_path / "wt-a"), "demo-repo", "test",
        "windows", tmp_path, codename="already-set",
    )
    _stub_launch_plumbing(monkeypatch, config)
    before_seed = launch_seed_state.stage(record.yaml_path, kind="new", text="retained private intent")
    before = launch_seed_state.state_path(record.yaml_path).read_bytes()
    consumer = [sys.executable, "-c", "print('bare-backend-started')"]
    code, plan = _resume_plan(
        config, record, monkeypatch, capfd, surface=surface, command=consumer, bare_resume=True,
    )
    assert code == 0 and "--admission-only" in plan["cmd"]
    assert "--preserve-seed-id" in plan["cmd"] and before_seed.seed_id in plan["cmd"]
    assert not plan["seed_pending"] and "--interactive" not in plan["cmd"]
    options = {
        "capture_output": True, "text": True, "timeout": 15,
        "env": {
            **os.environ,
            "PYTHONPATH": str(Path(resolve_cli.__file__).resolve().parents[1]),
        },
        "creationflags": subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    }
    with launch_admission.guard(record.yaml_path):
        blocked = subprocess.run(plan["cmd"], **options)
    assert blocked.returncode == 3 and "launch admission is busy" in blocked.stderr
    assert "bare-backend-started" not in blocked.stdout
    admitted = subprocess.run(plan["cmd"], **options)
    assert admitted.returncode == 0, admitted.stderr
    assert admitted.stdout.strip() == "bare-backend-started"
    assert launch_seed_state.state_path(record.yaml_path).read_bytes() == before
    assert launch_seed_state.peek(record.yaml_path) == before_seed


@pytest.mark.parametrize("surface", ["json", "interactive"])
def test_live_plan_cannot_start_backend_after_original_exits_during_repair(
    tmp_path, monkeypatch, capfd, surface,
):
    from agent_worktrees import launch_admission, launch_seed_exec

    monkeypatch.setattr(cfg, "tracking_dir", lambda: tmp_path)
    config = _create_config(tmp_path, auto_fast_forward=False)
    record = tracking.create_new_record(
        "wt-a", "worktree/wt-a", str(tmp_path / "wt-a"), "demo-repo", "test",
        "windows", tmp_path, codename="already-set",
    )
    _stub_launch_plumbing(monkeypatch, config)
    options = {
        "env": {
            **os.environ,
            "PYTHONPATH": str(Path(resolve_cli.__file__).resolve().parents[1]),
        },
        "creationflags": subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    }
    original = subprocess.Popen(
        launch_seed_exec.deferred_command(
            [sys.executable, "-c", "print('backend-ready',flush=True); input()"],
            record.yaml_path, admission_only=True,
        ),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, **options,
    )
    try:
        assert original.stdout.readline().strip() == "backend-ready"
        monkeypatch.setattr(m.sessions, "verify_worktree_active", lambda *a: LiveVerdict(active=True))
        consumer = [sys.executable, "-c", "print('replacement-started')"]
        code, plan = _resume_plan(
            config, record, monkeypatch, capfd, surface=surface, command=consumer,
        )
        assert code == 0 and "--admission-only" in plan["cmd"]
        original.stdin.write("\n")
        original.stdin.flush()
        assert original.wait(timeout=10) == 0
        monkeypatch.setattr(m.sessions, "verify_worktree_active", lambda *a: LiveVerdict())
        with launch_admission.guard(record.yaml_path):
            blocked = subprocess.run(
                plan["cmd"], capture_output=True, text=True, timeout=15, **options,
            )
        assert blocked.returncode == 3 and "launch admission is busy" in blocked.stderr
        assert "replacement-started" not in blocked.stdout
        admitted = subprocess.run(
            plan["cmd"], capture_output=True, text=True, timeout=15, **options,
        )
        assert admitted.returncode == 0, admitted.stderr
        assert admitted.stdout.strip() == "replacement-started"
    finally:
        if original.poll() is None:
            original.kill()
            original.wait(timeout=10)
        for stream in (original.stdin, original.stdout, original.stderr):
            stream.close()


@pytest.mark.parametrize("no_mux", [False, True])
def test_explicit_resume_seed_is_staged_with_history_and_provenance(
    tmp_path: Path, monkeypatch, capfd, no_mux,
):
    monkeypatch.setattr(cfg, "tracking_dir", lambda: tmp_path)
    config = _create_config(tmp_path)
    tracking.create_new_record(
        "wt-a", "worktree/wt-a", str(tmp_path / "wt-a"), "demo-repo", "test",
        "windows", tmp_path,
    )
    _stub_launch_plumbing(monkeypatch, config)
    monkeypatch.setattr(m.sessions, "find_latest_session_id_fast", lambda *_a, **_k: "history-s1")

    rc = resolve_cli.cmd_resolve(_args(seed="do the thing", no_mux=no_mux))

    assert rc == 0
    payload = json.loads(capfd.readouterr().out)
    cmd = payload["launch"]["cmd"]
    assert payload["launch"]["seed_pending"] is True
    assert payload["launch"]["seed_kind"] == "resume"
    assert launch_seed_state.peek(tmp_path / "wt-a.yaml").text == "do the thing"
    assert "--resume=history-s1" in cmd
    assert tracking.load_record(tmp_path / "wt-a.yaml").pending_seed is None
    # The real, full flag name -- never the short -i, which PowerShell's own
    # argument parser can intercept before it reaches copilot on Windows.
    assert "-i" not in cmd


def test_pending_seed_is_retained_until_the_returned_command_executes(
    tmp_path: Path, monkeypatch, capfd,
):
    """Planning alone cannot consume New ownership before final rejection."""
    monkeypatch.setattr(cfg, "tracking_dir", lambda: tmp_path)
    config = _create_config(tmp_path)
    tracking.create_new_record(
        "wt-a", "worktree/wt-a", str(tmp_path / "wt-a"), "demo-repo", "test",
        "windows", tmp_path, pending_seed="queued at creation",
    )
    _stub_launch_plumbing(monkeypatch, config)

    rc = resolve_cli.cmd_resolve(_args(seed=None))

    assert rc == 0
    payload = json.loads(capfd.readouterr().out)
    cmd = payload["launch"]["cmd"]
    assert cmd[1:3] == ["-m", "agent_worktrees.launch_seed_exec"]
    assert "--interactive" not in cmd
    reloaded = tracking.load_record(tmp_path / "wt-a.yaml")
    assert reloaded.pending_seed is None
    assert launch_seed_state.peek(tmp_path / "wt-a.yaml").text == "queued at creation"


@pytest.mark.parametrize("bare_resume", [False, True])
@pytest.mark.parametrize("live", [False, True])
def test_no_seed_cold_launch_has_execution_admission_fence(
    tmp_path: Path, monkeypatch, capfd, bare_resume, live,
):
    monkeypatch.setattr(cfg, "tracking_dir", lambda: tmp_path)
    config = _create_config(tmp_path)
    tracking.create_new_record(
        "wt-a", "worktree/wt-a", str(tmp_path / "wt-a"), "demo-repo", "test",
        "windows", tmp_path,
    )
    _stub_launch_plumbing(monkeypatch, config)
    monkeypatch.setattr(m.sessions, "verify_worktree_active", lambda *a: LiveVerdict(active=live))

    rc = resolve_cli.cmd_resolve(_args(seed=None, bare_resume=bare_resume))

    assert rc == 0
    payload = json.loads(capfd.readouterr().out)
    assert "--interactive" not in payload["launch"]["cmd"]
    assert "--admission-only" in payload["launch"]["cmd"]
    assert payload["launch"]["cmd"][-2:] == ["--", "copilot"]
    assert not payload["launch"]["seed_pending"]


@pytest.mark.parametrize(("machine", "environment"), [
    ("Example-Host", "WSL"),
    ("example-host-wsl", None),
])
def test_resume_preserves_historical_host_and_separate_wsl_platform(
    tmp_path: Path, monkeypatch, capfd, machine: str, environment: str | None,
) -> None:
    directory = tmp_path / "state" / "worktrees"
    directory.mkdir(parents=True)
    monkeypatch.setattr(cfg, "tracking_dir", lambda: directory)
    monkeypatch.setattr(cfg, "project_dir", lambda _project=None: directory.parent)
    monkeypatch.setattr(cfg, "detect_platform", lambda: "wsl")
    config = _create_config(tmp_path, machine="example-host-wsl", platform="wsl")
    (directory.parent / "config.yaml").write_text(json.dumps({
        "repo_name": config.repo_name, "machine": config.machine, "platform": "wsl",
        "repos": {config.repo_name: {
            "anchor": config.default_repo.anchor,
            "worktree_root": config.default_repo.worktree_root,
        }},
    }), encoding="utf-8")
    work_dir = Path(config.default_repo.worktree_root) / "wt-a"
    work_dir.mkdir(parents=True)
    (Path(config.default_repo.anchor) / "machines.yaml").write_text(
        "machines:\n"
        "  example-host:\n"
        "    ssh:\n"
        "      environments:\n"
        "        - {name: windows, alias: native-ssh}\n"
        "        - {name: wsl, alias: guest-ssh}\n",
        encoding="utf-8",
    )
    tracking.create_new_record(
        "wt-a", "worktree/wt-a", str(work_dir), "demo-repo", "example-host",
        "wsl", directory,
    )
    _stub_launch_plumbing(monkeypatch, config)
    monkeypatch.setattr(
        resolve_cli, "_emit_remote_plan_for_env",
        lambda *_args, **_kwargs: pytest.fail("The local WSL record must not dispatch through SSH"),
    )

    assert resolve_cli.cmd_resolve(_args(machine=machine, environment=environment)) == 0
    payload = json.loads(capfd.readouterr().out)
    assert payload["launch"]["action"] == "exec"
    assert payload["launch"]["work_dir"] == str(work_dir)
    record = tracking.load_record(directory / "wt-a.yaml")
    assert (record.machine, record.platform, record.resume_count) == ("example-host", "wsl", 1)


from machine_transport import legacy_alias_forms

ALIAS_RESUME_CELLS = [
    (platform, alias)
    for platform in ("windows", "wsl")
    for base in ("Example-Host", "Generated-Host", "Legacy-Alias", "Example Workstation")
    for form in legacy_alias_forms(base, platform)
    for alias in (form, form.swapcase())
]


@pytest.mark.parametrize("platform,alias", ALIAS_RESUME_CELLS)
def test_actual_local_resume_alias_grid_preserves_record(tmp_path, monkeypatch, capfd, platform, alias):
    directory = tmp_path / "state" / "worktrees"
    directory.mkdir(parents=True)
    monkeypatch.setattr(cfg, "tracking_dir", lambda: directory)
    monkeypatch.setattr(cfg, "project_dir", lambda _project=None: directory.parent)
    monkeypatch.setattr(cfg, "detect_platform", lambda: platform)
    current = "Example-Host-wsl" if platform == "wsl" else "Example-Host"
    config = _create_config(tmp_path, machine=current, platform=platform)
    (directory.parent / "config.yaml").write_text(json.dumps({
        "repo_name": config.repo_name, "machine": current, "platform": platform,
        "repos": {config.repo_name: {
            "anchor": config.default_repo.anchor, "worktree_root": config.default_repo.worktree_root,
        }},
    }), encoding="utf-8")
    (Path(config.default_repo.anchor) / "machines.yaml").write_text(json.dumps({"machines": {
        "Example-Host": {
            "hostname": "Generated-Host", "alias": "Legacy-Alias",
            "display_name": "Example Workstation", "ssh": {"environments": [
                {"name": "windows", "alias": "native-profile"},
                {"name": "wsl", "alias": "guest-profile"},
            ]},
        },
    }}), encoding="utf-8")
    checkout = Path(config.default_repo.worktree_root) / "wt-a"
    checkout.mkdir(parents=True)
    tracking.create_new_record(
        "wt-a", "worktree/wt-a", str(checkout), config.repo_name, alias, platform, directory,
    )
    _stub_launch_plumbing(monkeypatch, config)
    monkeypatch.setattr(
        resolve_cli, "_emit_remote_plan_for_env",
        lambda *_args, **_kwargs: pytest.fail("same execution tuple must not self-SSH"),
    )
    assert resolve_cli.cmd_resolve(_args(machine=alias, environment=platform)) == 0
    assert json.loads(capfd.readouterr().out)["launch"]["work_dir"] == str(checkout)
    record = tracking.load_record(directory / "wt-a.yaml")
    assert (record.machine, record.platform, record.resume_count) == (alias, platform, 1)


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell consumer unavailable")
def test_cold_resume_plan_executes_with_exact_prompt_at_powershell_boundary(
    tmp_path: Path, monkeypatch,
):
    from agent_worktrees import output

    monkeypatch.setattr(cfg, "tracking_dir", lambda: tmp_path)
    config = _create_config(tmp_path)
    work_dir = tmp_path / "wt-a"
    work_dir.mkdir()
    tracking.create_new_record(
        "wt-a", "worktree/wt-a", str(work_dir), "demo-repo", "test",
        "windows", tmp_path,
    )
    _stub_launch_plumbing(monkeypatch, config)
    consumer = tmp_path / "capture.ps1"
    consumer.write_text("ConvertTo-Json -InputObject @($args) -Compress\n", encoding="utf-8")
    monkeypatch.setattr(
        m, "_build_launch_cmd",
        lambda *_a, **_k: [
            shutil.which("pwsh"), "-NoProfile", "-NoLogo", "-File",
            str(Path(__file__).resolve().parents[1] / "scripts/default-setup.ps1"),
            "-RuntimePython", sys.executable, "-CopilotPath", str(consumer),
        ],
    )
    monkeypatch.setattr(m.sessions, "find_latest_session_id_fast", lambda *_a, **_k: "history-s1")
    seed = "quotes ' and \" ; $value\nsecond line"
    with output.capture_json_output() as captured:
        assert resolve_cli.cmd_resolve(_args(seed=seed, no_mux=True)) == 0
    plan = json.loads(captured.getvalue())["launch"]
    proc = subprocess.run(
        plan["cmd"], capture_output=True, text=True, check=True, timeout=20,
        cwd=work_dir,
        env={**os.environ, "AGENT_WORKTREES_MACHINE_SETTINGS_RECONCILED": "1"},
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert json.loads(proc.stdout.splitlines()[-1]) == ["--resume=history-s1", "--interactive", seed]
    assert tracking.load_record(tmp_path / "wt-a.yaml").pending_seed is None
    assert launch_seed_state.peek(tmp_path / "wt-a.yaml") is None


def test_no_mux_launch_still_gets_the_seed(tmp_path: Path, monkeypatch, capfd):
    """resume-prompt-durable-seed-and-mux-fix's whole point: the seed is
    carried durably in the launch args, so a --no-mux resume gets it exactly
    the same way a muxed one does -- there is no pane either way for the old
    send-keys mechanism to even target."""
    monkeypatch.setattr(cfg, "tracking_dir", lambda: tmp_path)
    config = _create_config(tmp_path)
    tracking.create_new_record(
        "wt-a", "worktree/wt-a", str(tmp_path / "wt-a"), "demo-repo", "test",
        "windows", tmp_path,
    )
    _stub_launch_plumbing(monkeypatch, config)

    rc = resolve_cli.cmd_resolve(_args(seed="do the thing", no_mux=True))

    assert rc == 0
    payload = json.loads(capfd.readouterr().out)
    assert payload["launch"]["seed_pending"] is True
    assert launch_seed_state.peek(tmp_path / "wt-a.yaml").text == "do the thing"


def test_bare_resume_leaves_a_persisted_pending_seed_queued_in_json_mode(
    tmp_path: Path, monkeypatch, capfd,
):
    """A persisted `pending_seed` must stay queued (never claimed/injected)
    for a `--bare-resume --json` resume, the same as the non-JSON
    `_resolve_resume_context` path already guarantees -- bare resume
    launches Copilot in HOME with no resumed conversation for a seed to
    join. Without this guard the JSON path's own claim-and-embed block ran
    unconditionally and silently consumed the queued seed even though
    `--bare-resume` never delivers it."""
    monkeypatch.setattr(cfg, "tracking_dir", lambda: tmp_path)
    config = _create_config(tmp_path)
    tracking.create_new_record(
        "wt-a", "worktree/wt-a", str(tmp_path / "wt-a"), "demo-repo", "test",
        "windows", tmp_path, pending_seed="queued at creation",
    )
    _stub_launch_plumbing(monkeypatch, config)

    rc = resolve_cli.cmd_resolve(_args(seed=None, bare_resume=True))

    assert rc == 0
    payload = json.loads(capfd.readouterr().out)
    assert "--interactive" not in payload["launch"]["cmd"]
    reloaded = tracking.load_record(tmp_path / "wt-a.yaml")
    assert reloaded.pending_seed is None
    assert launch_seed_state.peek(tmp_path / "wt-a.yaml").text == "queued at creation"


@pytest.mark.parametrize("verdict", [
    LiveVerdict(active=True, mux_live=True, live_session_ids=["s1"]),
    LiveVerdict(active=True, live_session_ids=["bare-s1"], bare=True),
    LiveVerdict(probes_ok=False, mux_probe_ok=False),
    LiveVerdict(probes_ok=False, mux_probe_ok=True),
    None,
])
def test_live_or_uncertain_json_resume_rejects_and_keeps_seed_for_next_attempt(
    tmp_path: Path, monkeypatch, capfd, verdict,
):
    """Failed Resume attempts retain their typed intent, never inject live."""
    from agent_worktrees import sessions as sessions_mod

    monkeypatch.setattr(cfg, "tracking_dir", lambda: tmp_path)
    config = _create_config(tmp_path)
    tracking.create_new_record(
        "wt-a", "worktree/wt-a", str(tmp_path / "wt-a"), "demo-repo", "test",
        "windows", tmp_path,
    )
    _stub_launch_plumbing(monkeypatch, config)
    monkeypatch.setattr(
        sessions_mod, "verify_worktree_active",
        lambda *_a, **_k: verdict,
    )

    rc = resolve_cli.cmd_resolve(_args(seed="do the thing"))

    assert rc == 3
    payload = json.loads(capfd.readouterr().out)
    assert "launch" not in payload
    assert "seed remains staged" in payload["error"]
    reloaded = tracking.load_record(tmp_path / "wt-a.yaml")
    assert reloaded.pending_seed is None
    assert reloaded.resume_count == 0
    saved = launch_seed_state.peek(tmp_path / "wt-a.yaml")
    assert saved.kind == "resume" and saved.text == "do the thing"
    monkeypatch.setattr(sessions_mod, "verify_worktree_active", lambda *_a, **_k: LiveVerdict())
    assert resolve_cli.cmd_resolve(_args()) == 0
    retry = json.loads(capfd.readouterr().out)["launch"]
    assert retry["seed_pending"] is True and retry["seed_id"] == saved.seed_id


def test_explicit_newer_resume_intent_supersedes_old_creation_without_live_delivery(
    tmp_path: Path, monkeypatch, capfd,
):
    """An explicit replacement changes the saved intent, not the live session."""
    from agent_worktrees import pending_seed as pending_seed_mod
    from agent_worktrees import sessions as sessions_mod

    monkeypatch.setattr(cfg, "tracking_dir", lambda: tmp_path)
    config = _create_config(tmp_path)
    tracking.create_new_record(
        "wt-a", "worktree/wt-a", str(tmp_path / "wt-a"), "demo-repo", "test",
        "windows", tmp_path, pending_seed="new-worktree prompt",
    )
    _stub_launch_plumbing(monkeypatch, config)
    monkeypatch.setattr(
        sessions_mod, "verify_worktree_active",
        lambda *_a, **_k: LiveVerdict(active=True, mux_live=True),
    )
    def unexpected_queue(*_a, **_k):
        raise AssertionError("Resume must use typed daemon state, not legacy New queue")
    monkeypatch.setattr(pending_seed_mod, "set_pending_seed", unexpected_queue)

    rc = resolve_cli.cmd_resolve(_args(seed="do the thing"))

    assert rc == 3
    payload = json.loads(capfd.readouterr().out)
    assert "launch" not in payload
    assert tracking.load_record(tmp_path / "wt-a.yaml").pending_seed is None
    saved = launch_seed_state.peek(tmp_path / "wt-a.yaml")
    assert saved.kind == "resume" and saved.text == "do the thing"


def test_degraded_mux_probe_is_treated_as_uncertain_not_confirmed_absent(
    tmp_path: Path, monkeypatch, capfd,
):
    """A failed probe is not confirmation that the worktree is stopped."""
    from agent_worktrees import sessions as sessions_mod

    monkeypatch.setattr(cfg, "tracking_dir", lambda: tmp_path)
    config = _create_config(tmp_path)
    tracking.create_new_record(
        "wt-a", "worktree/wt-a", str(tmp_path / "wt-a"), "demo-repo", "test",
        "windows", tmp_path,
    )
    _stub_launch_plumbing(monkeypatch, config)
    monkeypatch.setattr(
        sessions_mod, "verify_worktree_active",
        lambda *_a, **_k: LiveVerdict(probes_ok=False, mux_probe_ok=False),
    )

    rc = resolve_cli.cmd_resolve(_args(seed="do the thing"))

    assert rc == 3
    payload = json.loads(capfd.readouterr().out)
    assert "launch" not in payload
    reloaded = tracking.load_record(tmp_path / "wt-a.yaml")
    assert reloaded.pending_seed is None


def test_reclaim_probe_failure_blocks_explicit_no_mux_resume(
    tmp_path: Path, monkeypatch, capfd,
):
    """No mux does not prove no live Copilot; bound-process certainty matters."""
    from agent_worktrees import sessions as sessions_mod

    monkeypatch.setattr(cfg, "tracking_dir", lambda: tmp_path)
    config = _create_config(tmp_path)
    tracking.create_new_record(
        "wt-a", "worktree/wt-a", str(tmp_path / "wt-a"), "demo-repo", "test",
        "windows", tmp_path,
    )
    _stub_launch_plumbing(monkeypatch, config)
    monkeypatch.setattr(
        sessions_mod, "verify_worktree_active",
        lambda *_a, **_k: LiveVerdict(
            mux_live=False, mux_probe_ok=True, probes_ok=False,
        ),
    )

    rc = resolve_cli.cmd_resolve(_args(seed="do the thing", no_mux=True))

    assert rc == 3
    payload = json.loads(capfd.readouterr().out)
    assert "launch" not in payload
    assert tracking.load_record(tmp_path / "wt-a.yaml").pending_seed is None


def test_planning_never_claims_staged_seed_or_guesses_configured_argv(
    tmp_path: Path, monkeypatch, capfd,
):
    """Explicit provenance: a delegated caller must not
    guess whether `cmd`'s own trailing argv is a claimed seed -- the
    returned plan's `seed_claimed` field is the sole source of truth, true
    only when `with_seed` actually ran in THIS call."""
    monkeypatch.setattr(cfg, "tracking_dir", lambda: tmp_path)
    config = _create_config(tmp_path)
    tracking.create_new_record(
        "wt-a", "worktree/wt-a", str(tmp_path / "wt-a"), "demo-repo", "test",
        "windows", tmp_path,
    )
    _stub_launch_plumbing(monkeypatch, config)

    rc = resolve_cli.cmd_resolve(_args(seed="do the thing"))

    assert rc == 0
    payload = json.loads(capfd.readouterr().out)
    assert payload["launch"]["seed_claimed"] is False
    assert payload["launch"]["seed_pending"] is True

    tracking.create_new_record(
        "wt-b", "worktree/wt-b", str(tmp_path / "wt-b"), "demo-repo", "test",
        "windows", tmp_path,
    )
    (tmp_path / "wt-b").mkdir()
    rc = resolve_cli.cmd_resolve(_args(worktree_id="wt-b", seed=None))

    assert rc == 0
    payload = json.loads(capfd.readouterr().out)
    assert payload["launch"]["seed_claimed"] is False


@pytest.mark.parametrize("no_mux", [False, True])
@pytest.mark.parametrize("verdict", [
    LiveVerdict(),
    LiveVerdict(active=True, mux_live=True, live_session_ids=["s1"]),
    LiveVerdict(active=True, live_session_ids=["bare-s1"], bare=True),
    LiveVerdict(probes_ok=False),
    None,
])
def test_non_json_resume_seed_is_cold_start_only(
    tmp_path: Path, monkeypatch, capfd, verdict, no_mux,
):
    """Both resolve surfaces enforce cold-only delivery and retry staging."""
    from agent_worktrees import resolve_launch_cli as rlc

    monkeypatch.setattr(cfg, "tracking_dir", lambda: tmp_path)
    config = _create_config(tmp_path, auto_fast_forward=False)
    record = tracking.create_new_record(
        "wt-a", "worktree/wt-a", str(tmp_path / "wt-a"), "demo-repo", "test",
        "windows", tmp_path, codename="already-set",
    )
    _stub_launch_plumbing(monkeypatch, config)
    monkeypatch.setattr(
        rlc, "_dispatch_validate_profile_assignment_config", lambda *_a, **_k: None)
    monkeypatch.setattr(
        rlc.sessions, "verify_worktree_active",
        lambda *_a, **_k: verdict,
    )
    monkeypatch.setattr(rlc.sessions, "resolve_resume_target", lambda *_a, **_k: "history-s1")
    monkeypatch.setattr(rlc.tracking, "stamp_mux_live", lambda *_a, **_k: None)
    monkeypatch.setattr(rlc.tracking, "stamp_bound_live", lambda *_a, **_k: None)
    monkeypatch.setattr(rlc.local_cache_refresh, "refresh_local_cache", lambda *_a, **_k: None)
    monkeypatch.setattr(
        rlc, "_dispatch_launch_profile_selection",
        lambda *_a, **_k: SimpleNamespace(profile=None, assignment=None),
    )
    monkeypatch.setattr(rlc, "_dispatch_reflect_assignment", lambda *_a, **_k: None)
    monkeypatch.setattr(rlc, "_dispatch_apply_assignment_env", lambda env, _sel: env)
    monkeypatch.setattr(rlc, "_build_launch_cmd", lambda *_a, **_k: ["copilot"])
    monkeypatch.setattr(rlc, "_build_env", lambda *_a, **_k: {})
    monkeypatch.setattr(rlc, "_repo_session_env", lambda *_a, **_k: {})
    monkeypatch.setattr(rlc, "_preflight_launch", lambda *_a, **_k: SimpleNamespace(error=None))
    monkeypatch.setattr(rlc.activity, "log_event", lambda *_a, **_k: None)
    plans = []
    monkeypatch.setattr(rlc, "_emit_plan", plans.append)

    args = argparse.Namespace(
        json=False, base=False, dry_run=False, no_resume=False, no_mux=no_mux,
        bare_resume=False, seed="do the thing", no_fast_forward=False,
        profile=None,
    )
    context = rlc.ResolveLaunchContext(config=config, args=args, record=record)

    rc = rlc._resolve_resume_context(context)

    cold = verdict is not None and verdict.probes_ok and not verdict.active
    assert rc == (0 if cold else 3)
    if cold:
        assert "--resume=history-s1" in plans[-1]["cmd"]
        assert plans[-1]["seed_pending"] is True
        assert plans[-1]["seed_claimed"] is False
        assert plans[-1]["no_mux"] is no_mux
    else:
        assert plans[-1]["action"] == "error"
    reloaded = tracking.load_record(tmp_path / "wt-a.yaml")
    assert reloaded.pending_seed is None
    assert launch_seed_state.peek(tmp_path / "wt-a.yaml").text == "do the thing"


@pytest.mark.parametrize("verdict", [
    LiveVerdict(active=True, mux_live=True, probes_ok=False),
    LiveVerdict(active=True, bare=True, live_session_ids=["live"], mux_probe_ok=False, probes_ok=False),
])
def test_positive_live_open_keeps_staged_prompt_despite_secondary_probe_failure(
    tmp_path, monkeypatch, capfd, verdict,
):
    monkeypatch.setattr(cfg, "tracking_dir", lambda: tmp_path)
    config = _create_config(tmp_path)
    record = tracking.create_new_record(
        "wt-a", "worktree/wt-a", str(tmp_path / "wt-a"), "demo-repo", "test",
        "windows", tmp_path,
    )
    _stub_launch_plumbing(monkeypatch, config)
    saved = launch_seed_state.stage(record.yaml_path, kind="resume", text="retry later")
    monkeypatch.setattr(m.sessions, "verify_worktree_active", lambda *a: verdict)
    assert resolve_cli.cmd_resolve(_args()) == 0
    plan = json.loads(capfd.readouterr().out)["launch"]
    assert not plan["seed_pending"] and "--admission-only" in plan["cmd"]
    assert plan["cmd"][-2:] == ["--", "copilot"]
    assert launch_seed_state.peek(record.yaml_path) == saved
