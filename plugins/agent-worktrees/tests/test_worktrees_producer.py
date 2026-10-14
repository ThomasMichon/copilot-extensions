"""The resident enrolls existing facts once, without admitting growing ledgers."""

import json
import sqlite3

import pytest

from agent_worktrees import __main__ as core
from agent_worktrees import git_ops, reclaim, sessions, tracking
from agent_worktrees import worktrees_producer as producer
from agent_worktrees.worktrees_runtime import WorktreesRuntime


@pytest.mark.parametrize("failure", [None, sqlite3.OperationalError, KeyError])
def test_worktrees_producer_real_serialization_keeps_counts_and_owner_facts(
    tmp_path, monkeypatch, failure,
):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / ".git").write_text("gitdir: fixture")
    config_path = producer.cfg.project_dir("project") / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({"repos": {"project": {"anchor": str(checkout)}}}),
        encoding="utf-8",
    )
    record = tracking.WorktreeRecord(
        worktree_id="one",
        branch="worktree/one",
        worktree_path=str(checkout),
        repo="project",
        machine="host",
        platform="windows",
        title="Current task",
        started_at="2026-10-13T00:00:00",
        last_resumed_at="2026-10-13T00:00:00",
        resume_count=0,
        status="active",
        completed_at=None,
        resources=[tracking.ResourceClaim(kind="worktree", ref=f"child-{i}") for i in range(1000)],
    )
    monkeypatch.setattr(producer.cfg, "project_name", lambda: "project")
    monkeypatch.setattr(tracking, "list_records", lambda *a, **kw: [record])
    monkeypatch.setattr(sessions, "scan_sessions_fast", lambda _: sessions.SessionContext())
    monkeypatch.setattr(
        sessions,
        "mux_status_many",
        lambda _: {
            "one": sessions.MuxInfo(exists=True, clients=1),
        },
    )
    monkeypatch.setattr(
        core,
        "_classify_records_live",
        lambda *a, **kw: {
            "one": git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.WIP, dirty=4),
        },
    )
    monkeypatch.setattr(reclaim, "bare_orphan_worktree_ids", lambda: set())
    monkeypatch.setattr(reclaim, "live_bridge_worktrees", lambda: set())
    owner = WorktreesRuntime(tmp_path / "current.sqlite3")
    owner.activate()
    assert producer.publish_active_project(owner) == 1
    result = owner.handle({"contract_version": 1, "project": "project", "worktree_id": "one"})
    row = result["worktrees"][0]
    assert row["dirty"] == 4 and row["mux_session"] is True
    assert row["claims_assets"]["held_count"] == 1000
    assert len(row["claims_links"]) <= 8 and "resources" not in row
    assert row["checkout_present"] is True
    assert row["fact_provenance"]["git"]["quality"] == "current"
    if failure is not None:
        error = failure("fixture producer publication failure")
        stream = owner.feed.stream({"contract_version": 1, "project": "project"}, lambda: False)
        try:
            for frame in stream:
                if frame["type"] == "done":
                    break

            def fail_publish(*args, **kwargs):
                raise error

            monkeypatch.setattr(owner.store, "publish", fail_publish)
            with pytest.raises(failure) as raised:
                producer.publish_active_project(owner)
            assert raised.value is error
            stale = owner.handle({"contract_version": 1, "project": "project"})
            assert stale["worktrees"] == [row]
            assert stale["projection"] == result["projection"]
            assert stale["availability"] == {"status": "stale", "reason": str(error)}
            notice = next(stream)
            assert notice["type"] == "availability"
            assert notice["availability"] == stale["availability"]
        finally:
            stream.close()
    owner.shutdown()
