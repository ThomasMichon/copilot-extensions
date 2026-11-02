"""Durable reservation requests the existing resident outside origin locks."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from agent_worktrees import allocation_link_demand as demand, tracking
from test_owned_worktree_transactions import call, reserve
from test_resource_producer import owner as owner


def test_new_and_replayed_reservations_wake_current_roster_only_after_commit(owner, monkeypatch):
    path, _ = owner
    seen = []
    monkeypatch.setattr(demand.status_monitor_runtime, "_status_monitor_enabled", lambda: True)

    def locked_read():
        with tracking._RecordLock(path, timeout=1, require_sidecar=True):
            return tracking.load_record(path).resources[0].producer

    def read(request):
        assert request == {
            "contract_version": 1, "project": "p", "worktree_id": "owner", "include_missing": True,
        }
        with ThreadPoolExecutor(max_workers=1) as pool:
            metadata = pool.submit(locked_read).result(timeout=2)
        assert metadata["phase"] == "pending"
        seen.append(metadata["token"])
        return {"ok": True}

    monkeypatch.setattr(demand.worktrees_client, "read_current", read)
    original = reserve(path)
    assert reserve(path) == original
    assert seen == [original["token"], original["token"]]
    assert call(path, "reserve", {"request_id": "bad", "target": {}})["error"]
    assert seen == [original["token"], original["token"]]


def test_unavailable_wake_does_not_discard_durable_responsibility_or_claim_delivery(owner, monkeypatch, caplog):
    path, _ = owner
    monkeypatch.setattr(demand.status_monitor_runtime, "_status_monitor_enabled", lambda: True)

    def unavailable(request):
        raise demand.ProjectionUnavailable("unconfirmed private input")

    monkeypatch.setattr(demand.worktrees_client, "read_current", unavailable)
    result = reserve(path)
    assert result["phase"] == "pending"
    assert tracking.load_record(path).resources[0].producer == result["producer"]
    assert "wake is unconfirmed" in caplog.text
    assert "unconfirmed private input" not in caplog.text
