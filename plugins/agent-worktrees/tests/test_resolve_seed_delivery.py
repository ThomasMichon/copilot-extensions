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
from pathlib import Path
from types import SimpleNamespace

import agent_worktrees.__main__ as m
from agent_worktrees import config as cfg
from agent_worktrees import resolve_cli, tracking


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
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def test_explicit_seed_is_appended_as_interactive_on_the_resume_launch_cmd(
    tmp_path: Path, monkeypatch, capfd,
):
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
    cmd = payload["launch"]["cmd"]
    assert cmd[-2:] == ["--interactive", "do the thing"]
    # The real, full flag name -- never the short -i, which PowerShell's own
    # argument parser can intercept before it reaches copilot on Windows.
    assert "-i" not in cmd


def test_pending_seed_is_delivered_and_cleared_not_double_delivered(
    tmp_path: Path, monkeypatch, capfd,
):
    """A seed queued at creation time (`resolve --new --seed`, persisted as
    `pending_seed`) is picked up and delivered here -- the Picker's own
    two-hop new-worktree flow re-resolves by --worktree-id, landing in this
    exact branch. Must be CLEARED on the record so a later fallback
    (`agent-worktrees embody`'s own claim-and-send-keys path) never finds it
    again and delivers the same turn twice."""
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
    assert cmd[-2:] == ["--interactive", "queued at creation"]
    reloaded = tracking.load_record(tmp_path / "wt-a.yaml")
    assert reloaded.pending_seed is None


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
    assert payload["launch"]["cmd"][-2:] == ["--interactive", "do the thing"]


def test_bare_resume_leaves_a_persisted_pending_seed_queued_in_json_mode(
    tmp_path: Path, monkeypatch, capfd,
):
    """Review finding: a persisted `pending_seed` must stay queued (never
    claimed/injected) for a `--bare-resume --json` resume, the same as the
    non-JSON `_resolve_resume_context` path already guarantees -- bare
    resume launches Copilot in HOME with no resumed conversation for a
    seed to join. Without this guard the JSON path's own claim-and-embed
    block ran unconditionally and silently consumed the queued seed even
    though `--bare-resume` never delivers it."""
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
    assert reloaded.pending_seed == "queued at creation"


def test_live_mux_resume_queues_explicit_seed_instead_of_embedding_unused_argv(
    tmp_path: Path, monkeypatch, capfd,
):
    """Review finding: when a live mux session already exists, the external
    launcher reattaches it and never execs the returned launch command at
    all (see ``worktree-manager/bin/launch-session.{sh,ps1}``'s own
    live-mux handling) -- so embedding/claiming a seed into that unused argv
    would silently lose it. The non-JSON `_resolve_resume_context` path
    must instead persist an explicit `--seed` as `pending_seed` (for the
    older send-keys delivery, which CAN reach an already-live pane) rather
    than clearing/embedding it into the command nobody will run."""
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
        lambda *_a, **_k: SimpleNamespace(mux_live=True, live_session_ids=["s1"]),
    )
    monkeypatch.setattr(rlc.sessions, "resolve_resume_target", lambda *_a, **_k: None)
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
    monkeypatch.setattr(rlc, "_emit_plan", lambda *_a, **_k: None)

    args = argparse.Namespace(
        json=False, base=False, dry_run=False, no_resume=False, no_mux=False,
        bare_resume=False, seed="do the thing", no_fast_forward=False,
        profile=None,
    )
    context = rlc.ResolveLaunchContext(config=config, args=args, record=record)

    rc = rlc._resolve_resume_context(context)

    assert rc == 0
    out = capfd.readouterr().out
    assert "do the thing" not in out  # never embedded into the unused argv
    reloaded = tracking.load_record(tmp_path / "wt-a.yaml")
    assert reloaded.pending_seed == "do the thing"
