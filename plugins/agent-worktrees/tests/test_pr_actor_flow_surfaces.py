"""Actor-effective PR-flow wiring for networked command surfaces."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agent_worktrees import config as cfg
from agent_worktrees import output, pr_ops, providers, tracking
from agent_worktrees import pr_state_cli, worktree_identity


def _config() -> cfg.Config:
    repo = cfg.RepoConfig(
        anchor="/anchor",
        worktree_root="/worktrees",
        default_branch="main",
        pr=cfg.PRConfig(
            enabled=True,
            required=True,
            provider="github",
            roles={
                "maintain": cfg.PRRoleOverride(
                    merge_actor="submitter-direct",
                ),
            },
        ),
    )
    return cfg.Config(
        srcroot="/src",
        machine="host",
        platform="linux",
        repo_name="repo",
        repos={"repo": repo},
    )


def _args(*, no_live: bool) -> SimpleNamespace:
    return SimpleNamespace(
        config=None,
        worktree_id="wt",
        all=False,
        no_live=no_live,
        threads=False,
        resolve_threads=False,
        json=True,
    )


def _patch_status_shell(monkeypatch, config, captured):
    monkeypatch.setattr(cfg, "load_config", lambda path=None: config)
    monkeypatch.setattr(cfg, "tracking_dir", lambda: Path("/tracking"))
    monkeypatch.setattr(
        worktree_identity, "_infer_worktree_id", lambda value, config: value
    )
    monkeypatch.setattr(worktree_identity, "_resolve_worktree_id", lambda value: value)
    monkeypatch.setattr(output, "_json_output", lambda value: captured.update(value))
    monkeypatch.setattr(
        tracking,
        "load_record",
        lambda path: SimpleNamespace(
            active_pr=lambda: SimpleNamespace(repo="o/r"),
            repo="o/r",
        ),
    )


def test_pr_status_reports_effective_actor_profile(monkeypatch):
    config = _config()
    captured = {}
    status_args = {}
    _patch_status_shell(monkeypatch, config, captured)
    monkeypatch.setattr(providers, "get_provider", lambda name: object())
    monkeypatch.setattr(
        providers,
        "account_token_for_slug",
        lambda slug, prcfg: "tok",
    )
    monkeypatch.setattr(
        providers,
        "actor_viewer_permission",
        lambda *args, **kwargs: "maintain",
    )

    def fake_status(worktree_id, **kwargs):
        status_args.update(kwargs)
        return {"worktree_id": worktree_id, "has_pr": False, "pr_count": 0}

    monkeypatch.setattr(pr_ops, "pr_status", fake_status)

    rc = pr_state_cli.cmd_pr_status(_args(no_live=False))

    assert rc == 0
    assert captured["flow"]["profile"] == "pr-self-merge"
    assert captured["flow"]["configured_profile"] == "pr-human-merge"
    assert captured["flow"]["resolution"] == "actor-role"
    assert captured["flow"]["viewer_permission"] == "maintain"
    assert status_args["prcfg"].merge_actor == "submitter-direct"


def test_pr_status_no_live_stays_configured_and_offline(monkeypatch):
    config = _config()
    captured = {}
    _patch_status_shell(monkeypatch, config, captured)
    monkeypatch.setattr(
        providers,
        "get_provider",
        lambda name: (_ for _ in ()).throw(
            AssertionError("offline status must not resolve provider permission")
        ),
    )
    monkeypatch.setattr(
        pr_ops,
        "pr_status",
        lambda worktree_id, **kwargs: {
            "worktree_id": worktree_id,
            "has_pr": False,
            "pr_count": 0,
        },
    )

    rc = pr_state_cli.cmd_pr_status(_args(no_live=True))

    assert rc == 0
    assert captured["flow"]["profile"] == "pr-human-merge"
    assert captured["flow"]["configured_profile"] == "pr-human-merge"
    assert captured["flow"]["resolution"] == "configured"
    assert captured["flow"]["viewer_permission"] == ""
