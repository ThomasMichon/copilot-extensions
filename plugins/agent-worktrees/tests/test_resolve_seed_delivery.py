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


def _create_config(tmp_path: Path) -> cfg.Config:
    anchor = tmp_path / "anchor"
    anchor.mkdir()
    return cfg.Config(
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
