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
    monkeypatch.setattr(m.cfg, "load_config", lambda *a, **k: config)
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
    return cfg.Config(**base)


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


def test_no_seed_at_all_leaves_launch_cmd_unchanged(tmp_path: Path, monkeypatch, capfd):
    monkeypatch.setattr(cfg, "tracking_dir", lambda: tmp_path)
    config = _create_config(tmp_path)
    tracking.create_new_record(
        "wt-a", "worktree/wt-a", str(tmp_path / "wt-a"), "demo-repo", "test",
        "windows", tmp_path,
    )
    _stub_launch_plumbing(monkeypatch, config)

    rc = resolve_cli.cmd_resolve(_args(seed=None))

    assert rc == 0
    payload = json.loads(capfd.readouterr().out)
    assert "--interactive" not in payload["launch"]["cmd"]


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell consumer unavailable")
def test_cold_resume_plan_executes_with_exact_prompt_at_powershell_boundary(
    tmp_path: Path, monkeypatch, capfd,
):
    monkeypatch.setattr(cfg, "tracking_dir", lambda: tmp_path)
    config = _create_config(tmp_path)
    tracking.create_new_record(
        "wt-a", "worktree/wt-a", str(tmp_path / "wt-a"), "demo-repo", "test",
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
    assert resolve_cli.cmd_resolve(_args(seed=seed, no_mux=True)) == 0
    plan = json.loads(capfd.readouterr().out)["launch"]
    proc = subprocess.run(
        plan["cmd"], capture_output=True, text=True, check=True, timeout=20,
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
