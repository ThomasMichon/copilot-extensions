"""The resident enrolls existing facts once, without admitting growing ledgers."""

import json
import sqlite3
from types import SimpleNamespace

import pytest

from agent_worktrees import __main__ as core
from agent_worktrees import git_ops, mux_link, reclaim, sessions, tracking
from agent_worktrees import worktrees_producer as producer
from agent_worktrees.worktrees_runtime import WorktreesRuntime
from agent_worktrees.hook_facts_publication import HookFactsPublisher


@pytest.mark.parametrize(("failure", "managed_state"), [
    (None, None), (sqlite3.OperationalError, None), (KeyError, None),
    (None, "live"), (None, "stale"), (None, "other-project"),
    (None, "live-cleanup"),
    ("mux-unavailable", None), ("mux-unavailable", "live"),
], ids=["direct", "sqlite-failure", "key-failure", "managed-live",
        "managed-stale", "other-project", "managed-cleanup",
        "mux-unavailable", "managed-mux-unavailable"])
def test_worktrees_producer_real_serialization_keeps_counts_and_owner_facts(
    tmp_path, monkeypatch, failure, managed_state,
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
        creation_nonce="producer-incarnation",
        head_session="canonical-head",
        sessions=[tracking.SessionEntry("canonical-head", "2026-10-13T00:00:00")],
    )
    monkeypatch.setattr(producer.cfg, "project_name", lambda: "project")
    monkeypatch.setattr(tracking, "list_records", lambda *a, **kw: [record])
    monkeypatch.setattr(sessions, "scan_sessions_fast", lambda _: sessions.SessionContext(
        active_sessions={sessions._normalize_path(str(checkout)): ["canonical-head"]},
    ))
    monkeypatch.setattr(
        sessions,
        "mux_status_many",
        lambda _: {
            "one": sessions.MuxInfo(
                exists=managed_state is None, clients=1 if managed_state is None else 0,
            ),
        },
    )
    if failure == "mux-unavailable":
        from agent_worktrees.mux_list_result import MuxObservationUnavailable

        record.mux_live = True
        record.mux_live_at = "2026-10-13T00:00:00"

        def unavailable(_):
            raise MuxObservationUnavailable("passive observation unsupported")

        monkeypatch.setattr(sessions, "mux_status_many", unavailable)
    monkeypatch.setattr(
        core,
        "_classify_records_live",
        lambda *a, **kw: {
            "one": git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.WIP, dirty=4),
        },
    )
    monkeypatch.setattr(reclaim, "bare_orphan_worktree_ids", lambda: set())
    monkeypatch.setattr(reclaim, "live_bridge_worktrees", lambda: set())
    project = producer.cfg.project_dir("project")
    (project / "worktrees").mkdir(exist_ok=True)
    publisher = HookFactsPublisher(tmp_path / "runtime", None, project_root=lambda _: project)
    owner = WorktreesRuntime(tmp_path / "current.sqlite3", hook_facts=publisher)
    owner.activate()
    options = {}
    if managed_state is not None:
        cache = mux_link.ManagedMuxCache()
        if managed_state == "stale":
            monkeypatch.setattr(mux_link.time, "time", lambda: 1.0)
        cache.apply_observation({
            "project": "other" if managed_state == "other-project" else "project",
            "worktree_id": "one", "mux_session": "managed-one",
            "mapping_revision": 1, "live": True, "attached_clients": 2,
            "panes": [], "worktree_path": str(checkout),
        })
        if managed_state == "stale":
            monkeypatch.setattr(
                mux_link.time, "time", lambda: 2.0 + mux_link.MAPPING_STALE_AFTER_SECONDS,
            )
        options["managed_mux_cache"] = cache
        if managed_state == "live-cleanup":
            monkeypatch.setattr(git_ops, "_windows_observation", lambda: True)
            options["cleanup_owner"] = SimpleNamespace(
                observation_blocked=lambda _: False,
                run_observation=lambda _project, _anchor, _session, observe: observe(),
            )
    observed = []
    assert producer.publish_active_project(
        owner, observe_records=lambda project, records: observed.append((project, records)),
        **options,
    ) == 1
    assert observed == [("project", [record])]
    result = owner.handle({"contract_version": 1, "project": "project", "worktree_id": "one"})
    row = result["worktrees"][0]
    assert row["dirty"] == 4
    if failure == "mux-unavailable":
        assert "mux_session" not in row and "mux_clients" not in row
        assert row["fact_provenance"]["mux"] == {
            "quality": "unavailable", "observed_at": None,
        }
        assert row["mux_last_known"] == {
            "present": True, "observed_at": record.mux_live_at,
        }
        v2 = owner.handle({"contract_version": 2, "project": "project"})
        assert v2["worktrees"][0]["mux_last_known"] == row["mux_last_known"]
        assert v2["worktrees"][0]["fact_provenance"]["mux"] == {"quality": "unavailable"}
    else:
        assert row["mux_session"] is (managed_state in (None, "live", "live-cleanup"))
        if managed_state in ("live", "live-cleanup"):
            assert row["mux_clients"] == 2 and row["mux_attached"] is True
    assert row["claims_assets"]["held_count"] == 1000
    assert len(row["claims_links"]) <= 8 and "resources" not in row
    assert row["checkout_present"] is True
    assert row["fact_provenance"]["git"]["quality"] == "current"
    assert "record_head_session" not in row
    hook_path = project / "worktrees" / "one" / "hook-facts" / f"{publisher.generation}.json"
    assert json.loads(hook_path.read_bytes())["facts"]["record_head_session"] == "canonical-head"
    if failure is not None and failure != "mux-unavailable":
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
