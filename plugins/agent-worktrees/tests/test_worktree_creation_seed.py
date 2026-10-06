"""resume-prompt-durable-seed-and-mux-fix: ``_create_worktree_core``'s own
returned launch plan does NOT also embed a queued ``pending_seed`` as a
``--interactive`` argument -- only `resolve_launch_cli._resolve_resume_context`/
`resolve_cli._resolve_json_mode` (the Picker's own two-hop flow's real
delivery point, re-resolving by ``--worktree-id`` -- see
``test_resolve_seed_delivery.py``) do that. Review finding (PR #5442):
embedding it in BOTH this plan's argv AND leaving it persisted would create
two live delivery paths for the same prompt (a direct caller execs this
plan's `cmd` once, then a later `embody`/resume fallback claims the still-
persisted `pending_seed` and delivers it AGAIN). `pending_seed` persistence
stays the single, unambiguous owner of "queued but not yet delivered."
Reuses ``test_codename_cli.py``'s established internals-stubbing pattern to
drive the real function end to end against a real tracking record.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import agent_worktrees.__main__ as m
from agent_worktrees import config as cfg


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
    """Single-owner fix (PR #5442 review): this plan's own `cmd` must NOT
    also carry `--interactive <pending_seed>` -- only the Picker's real
    delivery point (the later --worktree-id re-resolve) does, so there is
    never a window where both this plan's direct execution AND a later
    fallback claim could deliver the same prompt twice."""
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


def test_create_worktree_core_still_persists_pending_seed_on_the_record(
    tmp_path: Path, monkeypatch,
):
    """The Picker's own two-hop new-worktree flow discards THIS plan and
    re-resolves by --worktree-id moments later -- it needs pending_seed
    still sitting on the record for that re-resolve to pick up and deliver
    (see test_resolve_seed_delivery.py). Confirms persistence is unaffected
    by the single-owner fix above."""
    from agent_worktrees import tracking

    config = _create_config(tmp_path)
    tracking_path = tmp_path / "tracking"
    monkeypatch.setattr(m.cfg, "tracking_dir", lambda: tracking_path)
    _stub_create_worktree_core_internals(monkeypatch, tmp_path, config)

    result = m._create_worktree_core(config, pending_seed="do the thing")

    worktree_id = result["worktree"]["id"]
    rec = tracking.load_record_by_id(worktree_id, tracking_path=tracking_path)
    assert rec.pending_seed == "do the thing"
