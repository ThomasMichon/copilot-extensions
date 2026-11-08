"""Owning-plugin synthetic tracking setup for optional publication consumers."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import config as cfg, pr_ops, providers, tracking


def tracked_state(scope: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple:
    target = {
        "provider": scope["provider"], "api_base": scope["endpoint"],
        "repo": "project-name/repository-name", "number": scope["number"],
    }
    home = tmp_path / "tracked"
    home.mkdir()
    monkeypatch.setattr(cfg, "tracking_dir", lambda *a, **k: home)
    config = SimpleNamespace(default_repo=SimpleNamespace(pr=SimpleNamespace(
        provider=target["provider"], api_base=target["api_base"],
    )))
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: config)
    monkeypatch.setattr(cfg, "_ACTIVE_PROJECT", "selected-project")
    record = tracking.WorktreeRecord(
        worktree_id="selected-worktree", branch="worktree/example", worktree_path=str(tmp_path),
        repo=target["repo"], machine="example", platform="linux", started_at="", last_resumed_at="",
        resume_count=0, title=None, status="active", completed_at=None,
        prs=[tracking.PRRecord(state="open", number=scope["number"], provider=scope["provider"])],
    )
    tracking.save_record(record, home / "selected-worktree.yaml")

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("selected consumer entered legacy reconcile/provider/credential path")

    monkeypatch.setattr(pr_ops, "_reconcile_active_pr", forbidden)
    monkeypatch.setattr(providers, "get_provider", forbidden)
    monkeypatch.setattr(providers, "account_token_for_slug", forbidden)
    return target, config, home


def change_association(home: Path, field: str, *, provider: str) -> None:
    path = home / "selected-worktree.yaml"
    record = tracking.load_record(path)
    if field == "number":
        record.prs.append(tracking.PRRecord(
            state="open", number=43, branch="pr/another", provider=provider,
            opened_at="2026-10-31T00:00:00Z",
        ))
    elif field == "repo":
        record.prs[0].repo = "another/repository"
    elif field == "provider":
        record.prs[0].provider = "github"
    else:
        raise ValueError("unsupported synthetic tracking change")
    tracking.save_record(record, path)
    if field == "number":
        assert tracking.load_record(path).active_pr().number == 43
