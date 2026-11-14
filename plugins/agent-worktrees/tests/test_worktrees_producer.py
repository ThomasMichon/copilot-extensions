"""The resident enrolls existing facts once, without admitting growing ledgers."""

import json
import sqlite3
import subprocess
from types import SimpleNamespace

import pytest
import yaml

from agent_worktrees import __main__ as core
from agent_worktrees import git_ops, list_cli, mux_link, reclaim, sessions, tracking
from agent_worktrees import worktrees_producer as producer
from agent_worktrees.record_io import read_record_mapping
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
    (None, False), (None, True), ("registered", False), ("unindexed", False),
    ("legacy-no-nonce", False), ("legacy-unindexed", False),
    ("bound", False), ("mux", False),
    ("later-mux", False), ("publication", False), ("ambiguous", False), ("cleanup", False),
    ("unreadable-workspace", False), ("contended", False), ("unpersisted", False),
    ("unreadable-record", False), ("malformed-record", False),
    ("status-collision", False), ("platform-collision", False), ("checkout-collision", False),
    ("malformed-registry", False), ("malformed-entry", False), ("malformed-id", False),
    ("partial-registry", False), ("numeric-id", False),
    ("malformed-during-observation", False),
    ("lifecycle-changed", False),
    *[(f"parse-{kind}-{phase}", False)
      for kind in ("yaml", "type", "key", "overflow")
      for phase in ("selection", "observation")],
])
def test_requested_point_refresh_reconciles_bound_hint_before_acknowledgement(
    tmp_path, monkeypatch, failure, managed,
):
    from datetime import datetime
    from dataclasses import replace
    import threading

    collision = failure is not None and failure.endswith("-collision")
    parse_failure = failure is not None and failure.startswith("parse-")
    selected_id = "two-one" if collision else "one"
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    record = tracking.WorktreeRecord(
        worktree_id="one", branch="worktree/one", worktree_path=str(checkout),
        repo="project", machine="host", platform="windows", title="Task",
        started_at="", last_resumed_at="", resume_count=0, status="active",
        completed_at=None, sessions=None if failure == "unreadable-workspace" else [],
        creation_nonce="" if failure in {"legacy-no-nonce", "legacy-unindexed"} else "fixture-incarnation",
        bound_live=True,
        bound_live_at=datetime.now().isoformat(),
    )
    if failure == "registered":
        record.sessions = [tracking.SessionEntry(session_id="registered-session", started_at="")]
    elif failure in {"unindexed", "legacy-unindexed"}:
        record.sessions = None
    monkeypatch.setattr(producer.cfg, "project_name", lambda: "project")
    config_path = producer.cfg.project_dir("project") / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps({"repos": {"project": {"anchor": str(checkout)}}}))
    records = [record]
    if failure == "ambiguous":
        records.append(replace(record, worktree_id="two-one", creation_nonce="second-incarnation"))
    elif collision:
        second_checkout = tmp_path / "second-checkout"
        second_checkout.mkdir()
        (second_checkout / ".git").write_text("gitdir: fixture")
        records.append(replace(
            record, worktree_id=selected_id, creation_nonce="second-incarnation",
            worktree_path=str(second_checkout),
        ))
        if failure == "status-collision":
            record.status = "archived"
        elif failure == "platform-collision":
            record.platform = "linux" if producer.cfg.detect_platform() != "linux" else "windows"
            # Canonical writes replace this obsolete label with current reporting.
            selected_id = "one"
    tracking_dir = producer.cfg.tracking_dir("project")
    tracking_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for item in records:
        path = tracking_dir / (item.worktree_id + ".yaml")
        tracking.save_record(item, path)
        paths.append(path)
    initial_bytes = paths[0].read_bytes()
    initial_record_bytes = {path: path.read_bytes() for path in paths}
    selected_path = next(path for path in paths if path.stem == selected_id)
    selected_record = next(item for item in records if item.worktree_id == selected_id)
    malformed = {
        "malformed-registry": {"session_id": "unobserved"},
        "malformed-entry": ["unobserved"],
        "malformed-id": [{"session_id": None}],
        "partial-registry": [{"session_id": "unobserved"}, {}],
        "numeric-id": [{"session_id": 17}],
    }

    def corrupt_sessions(value):
        nonlocal initial_bytes

        data = read_record_mapping(paths[0])
        data["sessions"] = value
        paths[0].write_text(json.dumps(data, default=str), encoding="utf-8")
        initial_bytes = paths[0].read_bytes()

    def corrupt_record():
        nonlocal initial_bytes

        data = read_record_mapping(paths[0])
        kind = failure.split("-")[1]
        if kind == "yaml":
            content = "sessions: ["
        else:
            if kind == "key":
                del data["worktree_id"]
            else:
                data["sessions"] = [{
                    "session_id": "legacy", "pid": [1] if kind == "type" else float("inf"),
                }]
            content = yaml.safe_dump(data)
        paths[0].write_text(content, encoding="utf-8")
        initial_bytes = paths[0].read_bytes()

    if failure in malformed:
        corrupt_sessions(malformed[failure])
    if failure == "malformed-record":
        paths[0].write_text("[invalid: yaml")
    elif failure == "unreadable-record":
        import builtins

        with tracking.record_cache._cache_lock:
            tracking.record_cache._cache.pop(str(paths[0]), None)
        original_open = builtins.open
        denied_reads = []

        def unavailable_record(file, *args, **kwargs):
            if str(file) == str(paths[0]):
                denied_reads.append(file)
                raise PermissionError("tracking record is unreadable")
            return original_open(file, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", unavailable_record)
    monkeypatch.setattr(tracking, "stamp_bound_live", lambda *a, **kw: None)
    monkeypatch.setattr(tracking, "stamp_mux_live", lambda *a, **kw: None)
    bound_reads = []

    def bound(**kwargs):
        nonlocal initial_bytes

        bound_reads.append(kwargs)
        if failure == "malformed-during-observation":
            corrupt_sessions(malformed["malformed-registry"])
        elif parse_failure and failure.endswith("-observation"):
            corrupt_record()
        elif failure == "lifecycle-changed":
            changed = tracking.load_record(paths[0])
            tracking._next_lifecycle_revision(changed)
            tracking.save_record(changed, paths[0])
            initial_bytes = paths[0].read_bytes()
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
        return {} if failure == "mux" else {
            selected_id: sessions.MuxInfo(exists=False, clients=0),
        }

    monkeypatch.setattr(sessions, "mux_status_many", mux)
    monkeypatch.setattr(core, "_classify_records_live", lambda *a, **kw: {
        "one": git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.WIP),
    })
    owner = WorktreesRuntime(tmp_path / "current.sqlite3")
    owner.activate()
    options = {}
    if parse_failure and failure.endswith("-selection"):
        def corrupt_after_selection(_project, selected):
            if selected:
                corrupt_record()

        options["observe_records"] = corrupt_after_selection
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
    elif collision:
        query.update({
            "status-collision": {"status_filter": "active"},
            "platform-collision": {"platform_filter": producer.cfg.detect_platform()},
            "checkout-collision": {"include_missing": False},
        }[failure])
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
        elif failure and not collision and failure not in {
            "registered", "unindexed", "legacy-no-nonce", "legacy-unindexed",
        }:
            count = 0 if failure in {"unreadable-record", "malformed-record"} else 1
            assert producer.publish_active_project(owner, **options) == count
            rejected = owner.handle(query)
            assert rejected["error"]["code"] == "refresh-unavailable"
            assert "point_refresh" not in rejected
            assert owner.handle({"contract_version": 1, "project": "project"})["ok"] is True
            assert owner.refreshes.pending("project") == ()
            assert producer.publish_active_project(owner, **options) == (0 if parse_failure else count)
            assert owner.handle(query) == rejected
            if failure == "unreadable-record":
                assert denied_reads
                assert paths[0].read_bytes() == initial_bytes
            if failure in {"unreadable-workspace", "contended", "unpersisted", "lifecycle-changed"}:
                assert paths[0].read_bytes() == initial_bytes
            if failure in malformed or failure == "malformed-during-observation" or parse_failure:
                assert paths[0].read_bytes() == initial_bytes
            if failure in {"contended", "unpersisted"}:
                release.set()
                if contender is not None:
                    contender.join(3)
                    assert not contender.is_alive() and not contention_errors
                monkeypatch.setattr(tracking, "save_record", original_save)
                retry = {**query, "refresh_id": "b" * 32}
                assert owner.handle(retry)["error"]["code"] == "unready"
                assert producer.publish_active_project(owner, **options) == len(records)
                assert owner.handle(retry)["point_refresh"]["request_id"] == "b" * 32
                assert tracking.load_record(paths[-1]).bound_live is False
        else:
            assert producer.publish_active_project(owner, **options) == len(records)
            result = owner.handle(query)
            assert result["ok"] is True and result["point_refresh"]["request_id"] == "a" * 32, result
            assert tracking.load_record(selected_path).bound_live is False
            assert result["point_refresh"]["worktree_id"] == selected_id
            assert result["point_refresh"]["creation_nonce"] == (selected_record.creation_nonce or None)
            if failure in {"legacy-no-nonce", "legacy-unindexed"}:
                assert "creation_nonce" not in result["worktrees"][0]
                assert tracking.load_record(paths[-1]).creation_nonce == ""
            assert result["point_refresh"]["path"] == selected_record.worktree_path
            assert result["worktrees"][0]["id"] == selected_id
            if collision:
                unselected = next(path for path in paths if path != selected_path)
                assert tracking.load_record(unselected).bound_live is True
                assert unselected.read_bytes() == initial_record_bytes[unselected]
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
        calls = 0 if failure in malformed or failure in {
            "ambiguous", "unreadable-workspace", "unreadable-record", "malformed-record",
        } or parse_failure and failure.endswith("-selection") else (
            2 if failure in {"contended", "unpersisted"} else 1
        )
        assert bound_reads == [
            {"worktree_id": selected_id,
             "session_ids": [entry.session_id for entry in record.sessions or []],
             "strict": True},
        ] * calls
    finally:
        release.set()
        if contender is not None:
            contender.join(3)
            assert not contender.is_alive() and not contention_errors
        owner.shutdown()


@pytest.fixture
def legacy_registry(tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    monkeypatch.setattr(producer.cfg, "project_name", lambda: "project")
    config_path = producer.cfg.project_dir("project") / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps({"repos": {"project": {"anchor": str(checkout)}}}))
    path = producer.cfg.tracking_dir("project") / "one.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    record = tracking.WorktreeRecord(
        worktree_id="one", branch="worktree/one", worktree_path=str(checkout),
        repo="project", machine="host", platform="windows", title="Original",
        started_at="", last_resumed_at="", resume_count=0, status="active",
        completed_at=None, sessions=None, creation_nonce="fixture-incarnation",
    )
    tracking.save_record(record, path)
    state = tmp_path / "session-state"
    entry = state / "discovered"
    entry.mkdir(parents=True)
    (entry / "workspace.yaml").write_text(json.dumps({"cwd": str(checkout)}), encoding="utf-8")
    (entry / "events.jsonl").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(sessions, "_session_state_dir", lambda: state)
    monkeypatch.setattr(reclaim, "live_bridge_worktrees", lambda **_: set())
    monkeypatch.setattr(reclaim, "resolve_bound_copilots", lambda **_: [])
    monkeypatch.setattr(sessions, "mux_status_many", lambda _: {
        "one": sessions.MuxInfo(exists=False, clients=0),
    })
    stamps = []
    monkeypatch.setattr(tracking, "stamp_bound_live", lambda *a, **kw: stamps.append(("bound", a)))
    monkeypatch.setattr(tracking, "stamp_mux_live", lambda *a, **kw: stamps.append(("mux", a)))
    return path, state, stamps


@pytest.mark.parametrize("strict", [False, True])
def test_legacy_backfill_ledger_survives_an_unrelated_stale_writer(legacy_registry, strict):
    path, _state, _stamps = legacy_registry
    stale = tracking.load_record(path)
    original_revision = stale.lifecycle_revision

    list_cli._refresh_list_record(tracking.load_record(path), strict=strict)
    indexed = tracking.load_record(path)
    assert [entry.session_id for entry in indexed.sessions] == ["discovered"]
    stale.title = "Unrelated edit"
    tracking.save_record(stale, path)
    preserved = tracking.load_record(path)
    assert preserved.title == "Unrelated edit"
    assert [entry.session_id for entry in preserved.sessions or []] == ["discovered"]
    assert preserved.resolved_head_session == "discovered"
    assert preserved.lifecycle_revision == indexed.lifecycle_revision > original_revision
    assert preserved.sessions[0].relation_revision == preserved.lifecycle_revision
    list_cli._refresh_list_record(preserved, strict=strict)
    assert tracking.load_record(path).lifecycle_revision == preserved.lifecycle_revision


@pytest.mark.parametrize("winner", ["registration", "refresh", "incarnation"])
def test_legacy_backfill_cannot_overwrite_interleaved_initialization(
    legacy_registry, monkeypatch, winner,
):
    path, state, stamps = legacy_registry
    stale = tracking.load_record(path)
    discover = sessions.backfill_sessions
    committed = []

    def interleave(records):
        found = discover(records)
        if winner != "incarnation":
            entry = state / "winner"
            entry.mkdir()
            (entry / "workspace.yaml").write_text(
                json.dumps({"cwd": stale.worktree_path}), encoding="utf-8",
            )
            (entry / "events.jsonl").write_text("{}\n", encoding="utf-8")
        with tracking._RecordLock(path, require_sidecar=True):
            if winner == "incarnation":
                data = read_record_mapping(path)
                data["creation_nonce"] = "replacement"
                path.write_text(json.dumps(data, default=str), encoding="utf-8")
            else:
                current = tracking.load_record(path)
                ids = ["winner"] if winner == "registration" else ["discovered", "winner"]
                current.sessions = [
                    tracking.SessionEntry(session_id=session_id, started_at="")
                    for session_id in ids
                ]
                current.title = "Concurrent title"
                tracking._next_lifecycle_revision(current, *ids)
                tracking.save_record(current, path)
            committed.append(path.read_bytes())
        return found

    monkeypatch.setattr(sessions, "backfill_sessions", interleave)
    list_cli._refresh_list_record(stale)
    current = tracking.load_record(path)
    assert path.read_bytes() == committed[0]
    if winner == "incarnation":
        assert current.creation_nonce == "replacement" and current.sessions is None
        assert not stamps
    else:
        expected = ["winner"] if winner == "registration" else ["discovered", "winner"]
        assert [entry.session_id for entry in current.sessions] == expected
        assert [entry.session_id for entry in stale.sessions] == expected
        assert current.resolved_head_session == "winner"
        assert current.lifecycle_revision == 1
        assert current.title == "Concurrent title"


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
