"""Interactive join hints retain prompt custody and require positive mux proof."""

import argparse
import json
from pathlib import Path

import pytest
from machine_transport import detect_platform

from agent_worktrees import config as cfg, launch_seed_state, profile_assignment
from agent_worktrees import resolve_launch_cli as planner, sessions, tracking
from agent_worktrees.launch_preflight import LaunchPreflight
from agent_worktrees.tracking_lifecycle import create_new_record


@pytest.mark.parametrize("verdict", [
    sessions.LiveVerdict(active=True, mux_live=True),
    sessions.LiveVerdict(
        active=True, bare=True, live_session_ids=["live"], mux_probe_ok=False, probes_ok=False,
    ),
])
def test_retained_open_uses_only_positive_mux_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, monkeypatch_config: None,
    verdict: sessions.LiveVerdict,
) -> None:
    project = tmp_path / "project"
    ledger = project / "worktrees"
    ledger.mkdir(parents=True)
    checkout_root = tmp_path / "checkouts"
    checkout = checkout_root / "wt-fixture"
    checkout.mkdir(parents=True)
    anchor = tmp_path / "anchor"
    anchor.mkdir()
    config = cfg.Config(
        srcroot=str(tmp_path), machine="fixture", platform=detect_platform(), repo_name="demo",
        repos={"demo": cfg.RepoConfig(
            anchor=str(anchor), worktree_root=str(checkout_root),
            launch={"windows": ["copilot"], "linux": ["copilot"]},
        )},
    )
    (project / "config.yaml").write_text(json.dumps({
        "repo_name": "demo", "machine": "fixture",
        "repos": {"demo": {"anchor": str(anchor), "worktree_root": str(checkout_root)}},
    }), encoding="utf-8")
    cfg.set_active_project("demo")
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: project)
    monkeypatch.setattr(cfg, "tracking_dir", lambda: ledger)
    monkeypatch.setattr(cfg, "load_config", lambda *args, **kwargs: config)
    monkeypatch.setattr(cfg, "load_project_config", lambda *args, **kwargs: config)
    monkeypatch.setattr(cfg, "load_machines_yaml", lambda *args, **kwargs: {})
    monkeypatch.setenv("AGENT_WORKTREES_RETAINED_SEED_PLANS", "1")
    record = create_new_record(
        "wt-fixture", "worktree/wt-fixture", str(checkout), "demo",
        "fixture", config.platform, ledger,
    )
    seed = launch_seed_state.stage(record.yaml_path, kind="new", text="must not replay")
    assert seed is not None
    launch_seed_state.take(record.yaml_path, seed_id=seed.seed_id)
    retained = launch_seed_state.peek(record.yaml_path)
    monkeypatch.setattr(sessions, "verify_worktree_active", lambda *args: sessions.LiveVerdict())
    monkeypatch.setattr(sessions, "resolve_resume_target", lambda *args: None)
    monkeypatch.setattr(tracking, "stamp_mux_live", lambda *args, **kwargs: None)
    monkeypatch.setattr(tracking, "stamp_bound_live", lambda *args, **kwargs: None)
    monkeypatch.setattr(planner, "_preflight_launch", lambda *args: LaunchPreflight())
    monkeypatch.setattr(planner, "_build_launch_cmd", lambda *args, **kwargs: ["copilot"])
    monkeypatch.setattr(planner, "_build_env", lambda *args, **kwargs: {})
    monkeypatch.setattr(planner, "_repo_session_env", lambda *args: {})
    monkeypatch.setattr(
        planner, "_dispatch_launch_profile_selection",
        lambda *args, **kwargs: profile_assignment.LaunchProfileSelection(profile=None),
    )
    plans: list[dict[str, object]] = []
    monkeypatch.setattr(planner, "_emit_plan", plans.append)
    args = argparse.Namespace(
        json=False, dry_run=False, no_mux=False, bare_resume=False, no_resume=True,
        profile=None, defer_projections=True, worktree_id=record.worktree_id,
    )
    context = planner.ResolveLaunchContext(config=config, args=args, record=record)
    assert planner._resolve_resume_context(context) == 0
    original = plans[-1]
    assert original["mux_join_existing"] is False
    assert original["seed_preserved_id"] == seed.seed_id
    monkeypatch.setattr(sessions, "verify_worktree_active", lambda *args: verdict)

    def forbid_prompt_mutation(*args: object, **kwargs: object) -> None:
        pytest.fail("live Open changed prompt custody")

    for name in ("take", "finish", "restore"):
        monkeypatch.setattr(launch_seed_state, name, forbid_prompt_mutation)
    args.preserve_seed_id = original["seed_preserved_id"]
    assert planner._resolve_resume_context(context) == 0
    plan = plans[-1]
    assert plan["mux_join_existing"] is bool(verdict.mux_probe_ok and verdict.mux_live)
    assert plan["seed_pending"] is False
    assert plan["seed_preserved_id"] == seed.seed_id
    command = plan["cmd"]
    assert isinstance(command, list)
    assert "--admission-only" in command and "--interactive" not in command
    assert launch_seed_state.peek(record.yaml_path) == retained
