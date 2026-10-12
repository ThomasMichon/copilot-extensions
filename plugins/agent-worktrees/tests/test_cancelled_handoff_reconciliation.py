"""Catalog observations cannot reclaim a yielded or superseded head."""

from __future__ import annotations

import pytest
from copy import deepcopy

from agent_worktrees import session_catalog, tracking, tracking_lifecycle
from test_session_catalog import _record, _wire


@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.usefixtures("monkeypatch_config")
def test_stale_catalog_head_preserves_handoff_and_rebind(
    tmp_path, monkeypatch, cancel,
):
    rec = _record(
        "wt-cancel",
        str(tmp_path / "wt-cancel"),
        sessions_list=[tracking.SessionEntry("old", "2026-06-01T10:00:00")],
        head="old",
    )
    tracking_dir, _ = _wire(tmp_path, monkeypatch, [rec])
    path = tracking_dir / "wt-cancel.yaml"
    monkeypatch.setattr(tracking, "_owning_tracking_dir", lambda _: tracking_dir)
    monkeypatch.setattr(session_catalog, "_session_live_pid", lambda _: None)
    reconciler = session_catalog.ResidentSessionReconciler()
    reconciler._paths[session_catalog._path_key(rec.worktree_path)] = (
        "project-a", rec.worktree_id, path, rec.worktree_path
    )
    reconciler._heads[("project-a", rec.worktree_id)] = "old"
    rec = tracking.load_record(path)
    tracking.open_handoff(rec, "old", "handoff-old")
    tracking.register_session(rec.worktree_id, "current")
    rec = tracking.load_record(path)
    tracking.open_handoff(rec, "current", "handoff-current")
    if cancel:
        assert tracking_lifecycle.cancel_handoff(rec, "handoff-current")
        tracking.save_record(rec, path)
    revision = tracking.load_record(path).head_revision
    reconciler._reconcile_session(
        {"cwd": rec.worktree_path, "session_id": "old", "entry": {},
         "started_at": "2026-06-01T10:00:00"}
    )
    after = tracking.load_record(path)
    assert after.head_revision == revision
    assert after.head_transitions[-1].session_id == "current"
    assert after.resolved_head_session == ("current" if cancel else None)
    tracking.register_session(rec.worktree_id, "current", source="bind")
    after = tracking.load_record(path)
    assert after.resolved_head_session == "current"
    assert after.session_entry("current").state == "active"
    assert after.handoffs[-1].state == "cancelled"


@pytest.mark.parametrize("state", ["yielded", "handed-off", "concluded"])
def test_observation_adoption_refuses_nonactive_session(tmp_path, monkeypatch, state):
    rec = _record(
        "wt-refuse", str(tmp_path / "wt-refuse"),
        sessions_list=[tracking.SessionEntry("old", "2026-06-01T10:00:00", state=state)],
    )
    before = rec.head_revision
    with pytest.raises(tracking.SessionLifecycleError, match="not active"):
        tracking.set_head_session(rec, "old", save=False)
    assert rec.head_revision == before


@pytest.mark.parametrize("state", ["yielded", "handed-off", "concluded"])
def test_refused_legacy_adoption_does_not_seed_ledger(tmp_path, state):
    rec = _record(
        "wt-legacy", str(tmp_path / "wt-legacy"),
        sessions_list=[
            tracking.SessionEntry("current", "2026-06-01T10:00:00"),
            tracking.SessionEntry("target", "2026-06-01T11:00:00", state=state),
        ],
        head="current",
    )
    before = deepcopy(rec)
    with pytest.raises(tracking.SessionLifecycleError, match="not active"):
        tracking.set_head_session(rec, "target", save=False)
    assert rec == before
    assert rec.head_transitions == []
