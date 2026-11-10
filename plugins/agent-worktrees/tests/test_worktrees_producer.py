"""The resident enrolls existing facts once, without admitting growing ledgers."""

import json
import sqlite3
import subprocess
from types import SimpleNamespace

import pytest

from agent_worktrees import __main__ as core
from agent_worktrees import git_ops, mux_link, reclaim, sessions, tracking
from agent_worktrees import worktrees_producer as producer
from agent_worktrees.worktrees_runtime import CONTRACT_VERSION, WorktreesRuntime
from agent_worktrees.hook_facts_publication import HookFactsPublisher


def test_unavailable_windows_mux_does_not_hide_real_git_facts(tmp_path, monkeypatch):
    from agent_procutil import no_window_kwargs

    checkout = tmp_path / "checkout"
    checkout.mkdir()

    def git(*args):
        subprocess.run(
            ["git", "-C", str(checkout), "-c", "user.name=Example",
             "-c", "user.email=example@example.invalid", *args],
            check=True, capture_output=True, **no_window_kwargs(),
        )

    git("init", "--initial-branch=worktree/one")
    git("commit", "--allow-empty", "-m", "Base")
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    (checkout / "change.txt").write_text("committed")
    git("add", "change.txt")
    git("commit", "-m", "Ahead")
    (checkout / "change.txt").write_text("dirty")
    record = tracking.WorktreeRecord(
        worktree_id="one", branch="worktree/one", worktree_path=str(checkout),
        repo="project", machine="host", platform="windows", title="Task",
        started_at="", last_resumed_at="", resume_count=0,
        status="active", completed_at=None, sessions=[],
        mux_live=False, mux_live_at="2020-01-01T00:00:00",
    )
    monkeypatch.setattr(producer.cfg, "project_name", lambda: "project")
    config_path = producer.cfg.project_dir("project") / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps({
        "repos": {"project": {"anchor": str(checkout), "default_branch": "main"}},
    }))
    monkeypatch.setattr(tracking, "list_records", lambda *a, **kw: [record])
    monkeypatch.setattr(sessions, "platform", SimpleNamespace(system=lambda: "Windows"))
    monkeypatch.setattr(sessions, "scan_sessions_fast", lambda _: sessions.SessionContext())
    monkeypatch.setattr(sessions, "has_mux_session", lambda *a, **kw: pytest.fail("PSMux probe"))
    monkeypatch.setattr(reclaim, "bare_orphan_worktree_ids", lambda: set())
    monkeypatch.setattr(reclaim, "live_bridge_worktrees", lambda: set())
    owner = WorktreesRuntime(tmp_path / "current.sqlite3")
    owner.activate()
    try:
        assert producer.publish_active_project(owner) == 1
        row = owner.handle({"contract_version": 1, "project": "project"})["worktrees"][0]
        assert row["state"] != git_ops.WorktreeState.ACTIVE.value
        assert row["dirty"] == 1 and row["ahead"] == 1 and row["behind"] == 0
        assert row["fact_provenance"]["mux"]["quality"] == "unavailable"
    finally:
        owner.shutdown()


def test_slow_fact_collection_counts_against_the_published_roster_age(tmp_path, monkeypatch):
    """Facts collected before a stall are already that old when published: a
    stalled collection must not publish an empty roster as freshly current."""
    clock = [100.0]
    monkeypatch.setattr(producer.cfg, "project_name", lambda: "project")
    config_path = producer.cfg.project_dir("project") / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps({
        "repos": {"project": {"anchor": str(tmp_path), "default_branch": "main"}},
    }))

    def _stalled_records(*_args, **_kwargs):
        clock[0] += 120.0
        return []

    monkeypatch.setattr(tracking, "list_records", _stalled_records)
    monkeypatch.setattr(sessions, "scan_sessions_fast", lambda _: sessions.SessionContext())
    monkeypatch.setattr(sessions, "mux_status_many", lambda _ids: {})
    monkeypatch.setattr(reclaim, "bare_orphan_worktree_ids", lambda: set())
    monkeypatch.setattr(reclaim, "live_bridge_worktrees", lambda: set())
    owner = WorktreesRuntime(
        tmp_path / "current.sqlite3", now=lambda: clock[0], freshness_bound=90.0,
    )
    owner.activate()
    try:
        assert producer.publish_active_project(owner) == 0
        reply = owner.handle({"contract_version": CONTRACT_VERSION, "project": "project", "passive": True})
        assert reply["worktrees"] == []
        assert reply["projection"]["freshness"]["refreshed_age_s"] == 120.0
        assert reply["availability"]["status"] == "stale"
    finally:
        owner.shutdown()


@pytest.mark.parametrize(("failure", "managed"), [
    (None, False), (None, True), ("bound", False), ("mux", False),
    ("later-mux", False), ("publication", False), ("ambiguous", False), ("cleanup", False),
    ("unreadable-workspace", False), ("contended", False), ("unpersisted", False),
])
def test_requested_point_refresh_reconciles_bound_hint_before_acknowledgement(
    tmp_path, monkeypatch, failure, managed,
):
    from datetime import datetime
    import threading

    checkout = tmp_path / "checkout"
    checkout.mkdir()
    record = tracking.WorktreeRecord(
        worktree_id="one", branch="worktree/one", worktree_path=str(checkout),
        repo="project", machine="host", platform="windows", title="Task",
        started_at="", last_resumed_at="", resume_count=0, status="active",
        completed_at=None, sessions=None if failure == "unreadable-workspace" else [],
        creation_nonce="fixture-incarnation", bound_live=True,
        bound_live_at=datetime.now().isoformat(),
    )
    monkeypatch.setattr(producer.cfg, "project_name", lambda: "project")
    config_path = producer.cfg.project_dir("project") / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps({"repos": {"project": {"anchor": str(checkout)}}}))
    records = [record]
    if failure == "ambiguous":
        from dataclasses import replace

        records.append(replace(record, worktree_id="two-one", creation_nonce="second-incarnation"))
    tracking_dir = producer.cfg.tracking_dir("project")
    tracking_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for item in records:
        path = tracking_dir / (item.worktree_id + ".yaml")
        tracking.save_record(item, path)
        paths.append(path)
    initial_bytes = paths[0].read_bytes()
    monkeypatch.setattr(
        tracking, "list_records",
        lambda *a, **kw: [tracking.load_record(path) for path in paths],
    )
    monkeypatch.setattr(tracking, "stamp_bound_live", lambda *a, **kw: None)
    monkeypatch.setattr(tracking, "stamp_mux_live", lambda *a, **kw: None)
    bound_reads = []

    def bound(**kwargs):
        bound_reads.append(kwargs)
        if failure == "bound":
            raise OSError("bound observation failed")
        if failure == "cleanup":
            raise producer.push_timeout.ObservationCleanupError("owned process is not settled")
        return []

    monkeypatch.setattr(reclaim, "resolve_bound_copilots", bound)
    monkeypatch.setattr(reclaim, "bare_orphan_worktree_ids", lambda: set())
    monkeypatch.setattr(reclaim, "live_bridge_worktrees", lambda **_: set())
    monkeypatch.setattr(sessions, "scan_sessions_fast", lambda _: sessions.SessionContext())
    mux_reads = []

    def mux(ids):
        mux_reads.append(ids)
        if failure == "later-mux" and len(mux_reads) > 1:
            raise producer.MuxObservationUnavailable("publication mux observation failed")
        return {} if failure == "mux" else {"one": sessions.MuxInfo(exists=False, clients=0)}

    monkeypatch.setattr(sessions, "mux_status_many", mux)
    monkeypatch.setattr(core, "_classify_records_live", lambda *a, **kw: {
        "one": git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.WIP),
    })
    owner = WorktreesRuntime(tmp_path / "current.sqlite3")
    owner.activate()
    options = {}
    if managed:
        cache = mux_link.ManagedMuxCache()
        cache.apply_observation({
            "project": "project", "worktree_id": "one", "mux_session": "managed-one",
            "mapping_revision": 1, "live": True, "attached_clients": 2,
            "panes": [], "worktree_path": str(checkout),
        })
        options["managed_mux_cache"] = cache
    query = {"contract_version": 1, "project": "project", "worktree_id": "one", "refresh_id": "a" * 32}
    if failure == "ambiguous":
        query["worktree_id"] = "ne"
    release, entered = threading.Event(), threading.Event()
    contender, contention_errors = None, []
    original_save = tracking.save_record
    try:
        owner.publish("project", [{"id": "one", "session_bound_live": True}], observed_at=1)
        assert owner.handle(query)["error"]["code"] == "unready"
        if failure == "unreadable-workspace":
            import builtins

            state = tmp_path / "session-state"
            legacy = state / "legacy"
            legacy.mkdir(parents=True)
            workspace = legacy / "workspace.yaml"
            workspace.write_text(json.dumps({"cwd": str(checkout)}), encoding="utf-8")
            (legacy / "events.jsonl").write_text("fixture", encoding="utf-8")
            monkeypatch.setattr(sessions, "_session_state_dir", lambda: state)
            original_open = builtins.open

            def unavailable(file, *args, **kwargs):
                if str(file) == str(workspace):
                    raise PermissionError("workspace is unreadable")
                return original_open(file, *args, **kwargs)

            monkeypatch.setattr(builtins, "open", unavailable)
        elif failure == "contended":
            def occupy():
                try:
                    with tracking._RecordLock(paths[0], require_sidecar=True):
                        entered.set()
                        assert release.wait(10)
                except BaseException as error:
                    contention_errors.append(error)

            contender = threading.Thread(target=occupy)
            contender.start()
            assert entered.wait(3)
        elif failure == "unpersisted":
            monkeypatch.setattr(tracking, "save_record", lambda *a, **kw: None)
        if failure == "publication":
            def failed_publish(*a, **kw):
                raise OSError("publication failed")

            monkeypatch.setattr(owner, "publish", failed_publish)
        if failure == "ambiguous":
            assert producer.publish_active_project(owner, **options) == 2
            assert owner.handle(query)["error"]["code"] == "invalid-request"
            assert owner.handle({"contract_version": 1, "project": "project"})["ok"] is True
            assert owner.refreshes.pending("project") == ()
        elif failure in {"publication", "cleanup"}:
            with pytest.raises((OSError, producer.push_timeout.ObservationCleanupError)):
                producer.publish_active_project(owner, **options)
            assert owner.handle(query)["error"]["code"] == "unready"
            assert owner.refreshes.pending("project")
        elif failure:
            assert producer.publish_active_project(owner, **options) == 1
            assert owner.handle(query)["error"]["code"] == "refresh-unavailable"
            assert owner.handle({"contract_version": 1, "project": "project"})["ok"] is True
            assert owner.refreshes.pending("project") == ()
            assert producer.publish_active_project(owner, **options) == 1
            if failure in {"unreadable-workspace", "contended", "unpersisted"}:
                assert paths[0].read_bytes() == initial_bytes
            if failure in {"contended", "unpersisted"}:
                release.set()
                if contender is not None:
                    contender.join(3)
                    assert not contender.is_alive() and not contention_errors
                monkeypatch.setattr(tracking, "save_record", original_save)
                retry = {**query, "refresh_id": "b" * 32}
                assert owner.handle(retry)["error"]["code"] == "unready"
                assert producer.publish_active_project(owner, **options) == 1
                assert owner.handle(retry)["point_refresh"]["request_id"] == "b" * 32
                assert tracking.load_record(paths[0]).bound_live is False
        else:
            assert producer.publish_active_project(owner, **options) == 1
            result = owner.handle(query)
            assert result["ok"] is True and result["point_refresh"]["request_id"] == "a" * 32
            assert tracking.load_record(paths[0]).bound_live is False
            assert result["worktrees"][0].get("session_bound_live") is not True
            assert result["worktrees"][0]["mux_session"] is False
            if managed:
                assert producer.publish_active_project(owner, **options) == 1
                assert owner.handle(query)["worktrees"][0]["mux_session"] is False
                cache.apply_observation({
                    "project": "project", "worktree_id": "one", "mux_session": "managed-one",
                    "mapping_revision": 2, "live": True, "attached_clients": 2,
                    "panes": [], "worktree_path": str(checkout),
                })
                assert producer.publish_active_project(owner, **options) == 1
                assert owner.handle(query)["worktrees"][0]["mux_session"] is True
        calls = 0 if failure in {"ambiguous", "unreadable-workspace"} else (
            2 if failure in {"contended", "unpersisted"} else 1
        )
        assert bound_reads == [{"worktree_id": "one", "session_ids": [], "strict": True}] * calls
    finally:
        release.set()
        if contender is not None:
            contender.join(3)
            assert not contender.is_alive() and not contention_errors
        owner.shutdown()


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
