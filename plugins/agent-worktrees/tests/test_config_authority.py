"""Bounded receiving-side authority configuration."""

from __future__ import annotations

import time

from agent_worktrees import config as cfg, config_authority, repos


def test_authority_config_clamps_a_stalled_git_probe(tmp_path, monkeypatch):
    anchor = tmp_path / "harness"
    cfg.inrepo_config_path(anchor).parent.mkdir(parents=True)
    cfg.inrepo_config_path(anchor).write_text(
        "stateless: true\nrequires_external_state_root: true\n",
        encoding="utf-8",
    )
    registry = repos.ReposRegistry(
        repos={
            "harness": repos.RepoEntry(
                name="harness",
                repo_class="worktree",
                paths={
                    "windows": str(anchor),
                    "wsl": str(anchor),
                    "linux": str(anchor),
                },
            )
        }
    )
    monkeypatch.setattr(repos, "read_registry", lambda: registry)
    project_root = tmp_path / ".harness"
    project_root.mkdir()
    (project_root / "config.yaml").write_text(
        "machine: workstation-windows\nplatform: windows\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: project_root)
    monkeypatch.setattr(
        cfg, "global_config_path", lambda: tmp_path / "missing-global.yaml",
    )
    observed_timeouts = []

    def stalled_git(*_args, timeout, **_kwargs):
        observed_timeouts.append(timeout)
        time.sleep(timeout + 0.05)
        raise TimeoutError("stalled git probe")

    monkeypatch.setattr(cfg.inrepo_config_source.git_ops, "git", stalled_git)

    started = time.monotonic()
    with cfg.inrepo_config_source.resolution_budget(30):
        config = config_authority.load_project_authority_config("harness")
    elapsed = time.monotonic() - started

    assert observed_timeouts
    assert max(observed_timeouts) <= config_authority._INREPO_AUTHORITY_BUDGET_S
    assert elapsed < 2.0
    assert config.default_repo.stateless is True
    assert config.default_repo.requires_external_state_root is True
