"""resume-prompt-durable-seed-and-mux-fix: ``_create_worktree_core``'s own
returned launch plan ALSO carries a queued ``pending_seed`` as a durable
``--interactive`` argument on its ``cmd``, for a direct caller that execs
this plan itself (rather than the Picker's own two-hop flow, which discards
this plan and re-resolves by ``--worktree-id`` -- see
``test_resolve_seed_delivery.py`` for that path's own coverage). Reuses
``test_codename_cli.py``'s established internals-stubbing pattern to drive
the real function end to end against a real tracking record.
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


def test_create_worktree_core_appends_pending_seed_as_interactive_on_its_own_launch_cmd(
    tmp_path: Path, monkeypatch,
):
    config = _create_config(tmp_path)
    monkeypatch.setattr(m.cfg, "tracking_dir", lambda: tmp_path / "tracking")
    _stub_create_worktree_core_internals(monkeypatch, tmp_path, config)

    result = m._create_worktree_core(config, pending_seed="do the thing")

    assert result["launch"]["cmd"][-2:] == ["--interactive", "do the thing"]
    # Never the short -i (PowerShell can intercept it on Windows).
    assert "-i" not in result["launch"]["cmd"]


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
    (see test_resolve_seed_delivery.py). This function embedding the seed
    in its OWN unused-by-the-Picker plan must not come at the cost of
    clearing it."""
    from agent_worktrees import tracking

    config = _create_config(tmp_path)
    tracking_path = tmp_path / "tracking"
    monkeypatch.setattr(m.cfg, "tracking_dir", lambda: tracking_path)
    _stub_create_worktree_core_internals(monkeypatch, tmp_path, config)

    result = m._create_worktree_core(config, pending_seed="do the thing")

    worktree_id = result["worktree"]["id"]
    rec = tracking.load_record_by_id(worktree_id, tracking_path=tracking_path)
    assert rec.pending_seed == "do the thing"
