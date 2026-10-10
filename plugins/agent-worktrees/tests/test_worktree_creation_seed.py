"""New creation stages one intent; returned plans do not consume or duplicate it."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import json

import pytest

import agent_worktrees.__main__ as m
from agent_worktrees import config as cfg


def _create_config(tmp_path: Path) -> cfg.Config:
    anchor = tmp_path / "anchor"
    anchor.mkdir()
    config = cfg.Config(
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
    project_dir = cfg.project_dir(config.repo_name)
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "config.yaml").write_text(json.dumps({
        "repo_name": config.repo_name, "machine": config.machine,
        "repos": {config.repo_name: {"anchor": str(anchor)}},
    }), encoding="utf-8")
    return config


def _stub_create_worktree_core_internals(monkeypatch, tmp_path: Path, config: cfg.Config) -> None:
    monkeypatch.setattr(m.git_ops, "resolve_start_point", lambda *_a, **_k: "HEAD")
    monkeypatch.setattr(
        m, "_prepare_worktree_source", lambda *_a, **_k: SimpleNamespace(start_point="HEAD"),
    )
    monkeypatch.setattr(m.git_ops, "create_worktree", lambda *_a, **_k: None)
    monkeypatch.setattr(m.permissions, "clone_permissions", lambda *_a: False)
    monkeypatch.setattr(m.permissions, "add_trusted_folder", lambda *_a: False)
    monkeypatch.setattr(m.activity, "log_event", lambda *_a, **_k: None)
    monkeypatch.setattr(
        m.state_root_mod,
        "resolve_state_root",
        lambda config, cwd=None: m.state_root.StateRoot(
            path=None, source="knowledge_repo", repo="", stateless=False,
            requires_external=False, bound=False, error=None,
        ),
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
    monkeypatch.setattr(m.cfg, "load_config", lambda *a, **k: config)


def test_create_worktree_core_does_not_embed_pending_seed_in_its_own_launch_cmd(
    tmp_path: Path, monkeypatch,
):
    """Single-owner invariant: this plan's own `cmd` must NOT also carry
    `--interactive <pending_seed>` -- only the Picker's real delivery point
    (the later --worktree-id re-resolve) does, so there is never a window
    where both this plan's direct execution AND a later fallback claim
    could deliver the same prompt twice."""
    config = _create_config(tmp_path)
    monkeypatch.setattr(m.cfg, "tracking_dir", lambda: tmp_path / "tracking")
    _stub_create_worktree_core_internals(monkeypatch, tmp_path, config)

    result = m._create_worktree_core(config, pending_seed="do the thing")

    assert "--interactive" not in result["launch"]["cmd"]


def test_create_worktree_core_with_no_seed_leaves_launch_cmd_unchanged(
    tmp_path: Path, monkeypatch,
):
    config = _create_config(tmp_path)
    monkeypatch.setattr(m.cfg, "tracking_dir", lambda: tmp_path / "tracking")
    _stub_create_worktree_core_internals(monkeypatch, tmp_path, config)

    result = m._create_worktree_core(config)

    assert "--interactive" not in result["launch"]["cmd"]


def test_create_worktree_core_stages_seed_in_worktree_state_folder(
    tmp_path: Path, monkeypatch,
):
    """The Picker's own two-hop new-worktree flow discards THIS plan and
    re-resolves by --worktree-id moments later -- it needs pending_seed
    still sitting on the record for that re-resolve to pick up and deliver
    (see test_resolve_seed_delivery.py). Confirms persistence is unaffected
    by the single-owner fix above."""
    from agent_worktrees import launch_seed_state, tracking

    config = _create_config(tmp_path)
    tracking_path = tmp_path / "tracking"
    monkeypatch.setattr(m.cfg, "tracking_dir", lambda: tracking_path)
    _stub_create_worktree_core_internals(monkeypatch, tmp_path, config)

    result = m._create_worktree_core(config, pending_seed="do the thing")

    worktree_id = result["worktree"]["id"]
    rec = tracking.load_record_by_id(worktree_id, tracking_path=tracking_path)
    assert rec.pending_seed is None
    saved = launch_seed_state.peek(rec.yaml_path)
    assert saved.kind == "new" and saved.text == "do the thing"
    assert result["launch"]["seed_id"] == saved.seed_id


@pytest.mark.parametrize("committed", [False, True])
def test_post_creation_staging_failure_returns_existing_identity_for_recovery(
    tmp_path, monkeypatch, capfd, committed,
):
    from agent_worktrees import launch_seed_state, resolve_cli, tracking, tracking_write
    from test_resolve_seed_delivery import _args, _stub_launch_plumbing

    config = _create_config(tmp_path)
    tracking_path = tmp_path / "tracking"
    monkeypatch.setattr(m.cfg, "tracking_dir", lambda: tracking_path)
    _stub_create_worktree_core_internals(monkeypatch, tmp_path, config)
    real_stage = launch_seed_state.stage
    created = []
    def fail_stage(path, **kwargs):
        created.append(path)
        if committed:
            real_stage(path, **kwargs)
            raise tracking_write.AmbiguousWriteOutcome("response was lost")
        raise OSError("state storage unavailable")
    monkeypatch.setattr(launch_seed_state, "stage", fail_stage)
    assert resolve_cli.cmd_resolve(_args(new_worktree=True, worktree_id=None, seed="New task")) == 3
    failure = json.loads(capfd.readouterr().out)
    assert failure["created"] is True
    assert failure["worktree"]["id"] == created[0].stem
    assert failure["recovery"]["worktree_id"] == created[0].stem
    assert failure["recovery"]["repeat_new"] is False
    assert "--worktree-id" in failure["error"] and "Do not retry --new" in failure["error"]
    assert len(tracking.list_records(tracking_path)) == 1
    if committed:
        saved = launch_seed_state.peek(created[0])
        assert saved.seed_id == failure["launch_seed"]["seed_id"]
        assert failure["launch_seed"]["status"] == "unknown"
        monkeypatch.setattr(launch_seed_state, "stage", real_stage)
        _stub_launch_plumbing(monkeypatch, config)
        assert resolve_cli.cmd_resolve(_args(worktree_id=created[0].stem)) == 0
        recovered = json.loads(capfd.readouterr().out)
        assert recovered["launch"]["seed_id"] == saved.seed_id
        assert len(tracking.list_records(tracking_path)) == 1


@pytest.mark.parametrize("failure_site", ["pair-stamp", "launch-plan"])
def test_any_post_record_tail_failure_preserves_recovery_identity(
    tmp_path, monkeypatch, capfd, failure_site,
):
    from agent_worktrees import launch_seed_state, resolve_cli, tracking
    from test_resolve_seed_delivery import _args

    config = _create_config(tmp_path)
    records = tmp_path / "tracking"
    monkeypatch.setattr(m.cfg, "tracking_dir", lambda: records)
    _stub_create_worktree_core_internals(monkeypatch, tmp_path, config)
    def fail(*a, **k):
        raise OSError("post-record tail failed")
    if failure_site == "pair-stamp":
        monkeypatch.setattr(m, "_stamp_and_compose_paired_knowledge", fail)
    else:
        monkeypatch.setattr(m, "_build_launch_cmd", fail)
    assert resolve_cli.cmd_resolve(_args(new_worktree=True, worktree_id=None, seed="New task")) == 3
    payload = json.loads(capfd.readouterr().out)
    assert payload["created"] is True and payload["recovery"]["repeat_new"] is False
    path = records / f"{payload['worktree']['id']}.yaml"
    assert path.exists() and launch_seed_state.peek(path).text == "New task"
    assert len(tracking.list_records(records)) == 1
