"""Reporter context changes independently of persisted rows and feed cursors."""

from __future__ import annotations

import argparse
import json
import sqlite3
import threading
from pathlib import Path

import pytest
import yaml

from agent_worktrees import config as cfg, record_reporting
from agent_worktrees import status_monitor_cutover as control
from agent_worktrees import worktrees_client, worktrees_feed, worktrees_feed_client
from agent_worktrees import worktrees_reporting
from agent_worktrees.worktrees_projection_index import ProjectionUnavailable
from agent_worktrees.worktrees_runtime import WorktreesRuntime


@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.setattr(
        record_reporting, "configured_repository_paths", lambda project: {project: tmp_path},
    )
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: "observed-host")
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: pytest.fail("full Config loaded"))
    path = tmp_path / "machines.yaml"

    def write(name="first", environment="linux"):
        monkeypatch.setattr(cfg, "detect_platform", lambda: environment)
        path.write_text(yaml.safe_dump({"machines": {name: {
            "hostname": "observed-host", "physical_host": "physical",
            "execution_platform": environment,
        }}}), encoding="utf-8")

    write()
    return path, write


def request(version=1):
    return {"contract_version": version, "project": "project", "worktree_id": "one"}


def durable_bytes(path):
    return {item.name: item.read_bytes() for item in path.parent.glob(path.name + "*")}


@pytest.mark.parametrize("version", [1, 2])
def test_roster_publication_drops_own_and_caller_names_but_preserves_filtered_wire_contract(
    tmp_path, registry, version,
):
    _path, rename = registry
    names = {
        "machine": "obsolete-host", "platform": "linux",
        "machine_identity_version": 1, "environment": "linux",
        "execution_machine": "obsolete-host",
    }
    locator = "obsolete-host/project/parent"
    caller = {
        "state": "ambiguous", "worktree_id": "parent", **names,
        "candidates": [{"worktree_id": "parent", "repo": "project", **names}],
    }
    row = {
        "id": "one", "status": "active", "creation_nonce": "incarnation",
        "path": "checkout", "caller_worktree": locator, "caller_state": caller, **names,
    }
    owner = WorktreesRuntime(tmp_path / "current.sqlite3")
    owner.activate()
    try:
        owner.publish("project", [row], observed_at=1)
        with sqlite3.connect(owner.store.db_path) as conn:
            stored = json.loads(conn.execute(
                "SELECT payload FROM worktrees_projection_row WHERE project='project'",
            ).fetchone()[0])
            scopes = [json.loads(scope) for (scope,) in conn.execute(
                "SELECT scope FROM worktrees_projection_scope",
            )]
            storage_version = conn.execute(
                "SELECT schema_version FROM worktrees_projection_head WHERE project='project'",
            ).fetchone()[0]
        assert not names.keys() & stored.keys()
        assert not names.keys() & stored["caller_state"].keys()
        assert not names.keys() & stored["caller_state"]["candidates"][0].keys()
        assert stored["caller_worktree"] == locator
        assert all(scope[1] is None for scope in scopes)
        assert row["caller_state"] == caller and row["machine"] == "obsolete-host"

        query = {**request(version), "platform_filter": "linux"}
        first = worktrees_client.validate_result(owner.handle(query), query)
        assert storage_version == 2 and first["projection"]["schema_version"] == 1
        assert [item["id"] for item in first["worktrees"]] == ["one"]
        assert first["worktrees"][0]["platform"] == "linux"
        assert not (names.keys() - {"platform"}) & first["worktrees"][0].keys()
        receiver = worktrees_feed_client.FeedReceiver(query)
        for frame in worktrees_feed.publication_frames(first):
            receiver.receive(frame)
        before = durable_bytes(owner.store.db_path)
        rename("renamed-reporter")
        second = worktrees_client.validate_result(owner.handle(query), query)
        assert second["reporting_context"]["execution_machine"] == "renamed-reporter"
        assert second["worktrees"] == first["worktrees"]
        assert second.get("observations") == first.get("observations")
        assert second["projection"] == first["projection"]
        assert durable_bytes(owner.store.db_path) == before
        assert receiver.current["worktrees"] == first["worktrees"]
        _path.write_text("machines: []\n", encoding="utf-8")
        unavailable = worktrees_client.validate_result(owner.handle(query), query)
        assert unavailable["reporting_context"] == {"version": 1, "status": "unavailable"}
        assert unavailable["worktrees"] == first["worktrees"]
        assert unavailable["projection"] == first["projection"]
        saved = worktrees_client.saved_result(query, "fixture", db_path=owner.store.db_path)
        assert saved["worktrees"] == first["worktrees"]
        assert saved.get("observations") == first.get("observations")
        assert durable_bytes(owner.store.db_path) == before
    finally:
        owner.shutdown()


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("selector", [{"worktree_id": "one"}, {"codename": "duplicate"}])
def test_other_platform_is_empty_before_alias_resolution_or_row_decoding(
    tmp_path, monkeypatch, registry, version, selector,
):
    from agent_worktrees import worktrees_projection

    owner = WorktreesRuntime(tmp_path / "current.sqlite3")
    owner.activate()
    try:
        owner.publish("project", [
            {"id": "first-one", "codename": "duplicate"},
            {"id": "second-one", "codename": "duplicate"},
        ], observed_at=1)
        monkeypatch.setattr(
            worktrees_projection, "_decode_row", lambda *a: pytest.fail("decoded an excluded row"),
        )
        query = {
            "contract_version": version, "project": "project",
            "platform_filter": "windows", "include_missing": False, **selector,
        }
        live = worktrees_client.validate_result(owner.handle(query), query)
        assert live["worktrees"] == []
        assert live["projection"]["selected_count"] == 0
        assert live["projection"]["roster_count"] == 2
        saved = worktrees_client.saved_result(query, "fixture", db_path=owner.store.db_path)
        assert saved["worktrees"] == []
    finally:
        owner.shutdown()


@pytest.mark.parametrize("version", [1, 2])
def test_platform_filtered_refresh_receipt_uses_the_same_naming_free_selection(
    tmp_path, registry, version,
):
    owner = WorktreesRuntime(tmp_path / "current.sqlite3")
    owner.activate()
    query = {**request(version), "platform_filter": "linux", "refresh_id": "a" * 32}
    try:
        assert owner.handle(query)["error"]["code"] == "unready"
        row = {"id": "one", "creation_nonce": "incarnation", "path": "checkout"}
        owner.publish("project", [row], observed_at=1, refresh_requests=[(
            query["refresh_id"],
            {"worktree_id": "one", "creation_nonce": "incarnation", "path": "checkout"},
        )])
        result = worktrees_client.validate_result(owner.handle(query), query)
        assert result["point_refresh"]["creation_nonce"] == "incarnation"
        assert result["worktrees"][0]["platform"] == "linux"
    finally:
        owner.shutdown()


@pytest.mark.parametrize("version", [1, 2])
def test_legacy_advisory_names_cannot_filter_and_next_publication_removes_them(
    tmp_path, registry, version,
):
    from agent_worktrees.worktrees_projection import WorktreesProjection

    path = tmp_path / "current.sqlite3"
    row = {
        "id": "one", "machine": "obsolete-host", "platform": "wsl",
        "machine_identity_version": 1, "environment": "wsl",
        "execution_machine": "obsolete-guest",
    }
    old = WorktreesProjection(path)
    old.publish("project", [row], observed_at=1)
    old.close()
    before = path.read_bytes()
    saved = worktrees_client.saved_result(request(version), "fixture", db_path=path)
    assert saved["worktrees"][0]["machine"] == "obsolete-host"
    assert saved["reporting_context"]["environment"] == "linux"
    with pytest.raises(ProjectionUnavailable, match="violates selection"):
        worktrees_client.saved_result(
            {**request(version), "platform_filter": "linux"}, "fixture", db_path=path,
        )
    assert worktrees_client.saved_result(
        {**request(version), "platform_filter": "wsl"}, "fixture", db_path=path,
    )["worktrees"] == []
    assert path.read_bytes() == before
    owner = WorktreesRuntime(path)
    owner.activate()
    try:
        owner.publish("project", [row], observed_at=2)
        current = owner.handle({**request(version), "platform_filter": "linux"})
        worktrees_client.validate_result(current, {**request(version), "platform_filter": "linux"})
        assert current["worktrees"][0]["platform"] == "linux"
        assert "machine" not in current["worktrees"][0]
        with sqlite3.connect(path) as conn:
            assert json.loads(conn.execute(
                "SELECT payload FROM worktrees_projection_row WHERE project='project'",
            ).fetchone()[0]) == {"id": "one"}
            assert all(json.loads(scope)[1] is None for (scope,) in conn.execute(
                "SELECT scope FROM worktrees_projection_scope",
            ))
    finally:
        owner.shutdown()


def test_republishing_renamed_labels_does_not_change_semantic_rows(tmp_path, registry):
    _path, rename = registry
    owner = WorktreesRuntime(tmp_path / "current.sqlite3")
    owner.activate()
    try:
        owner.publish("project", [{"id": "one", "machine": "first"}], observed_at=1)
        first = owner.handle(request(2))
        retained = owner.store._projects["project"].rows["one"]
        rename("second")
        owner.publish("project", [{"id": "one", "machine": "second"}], observed_at=2)
        second = owner.handle(request(2))
        assert second["reporting_context"]["execution_machine"] == "second"
        assert first["worktrees"] == second["worktrees"]
        assert second["worktrees"][0]["semantic_revision"] == 1
        assert second["projection"]["revision"] == 2
        assert owner.store._projects["project"].rows["one"] == retained
    finally:
        owner.shutdown()


@pytest.mark.parametrize("environment", ["windows", "wsl", "linux"])
@pytest.mark.parametrize("version", [1, 2])
def test_authenticated_reads_use_current_reporter_without_republishing_or_writing(
    tmp_path, monkeypatch, registry, environment, version,
):
    _path, rename = registry
    rename(environment=environment)
    owner = WorktreesRuntime(tmp_path / "current.sqlite3")
    owner.activate()
    owner.publish("project", [
        {"id": "one", "machine": "obsolete"},
        *({"id": f"other-{index}"} for index in range(100)),
    ], observed_at=1)
    original = record_reporting.current_context
    lookups = []

    def counted(project):
        lookups.append(project)
        return original(project)

    monkeypatch.setattr(record_reporting, "current_context", counted)
    server = control.ControlServer(
        lambda action, body: owner.health() if action == "health" else owner.handle(body),
        token="fixture",
    )
    server.start()
    peer = control.ControlClient(f"http://127.0.0.1:{server.port}", token="fixture")
    try:
        assert worktrees_reporting.CAPABILITY in peer.health()["capabilities"]
        before = durable_bytes(owner.store.db_path)
        first = worktrees_client.validate_result(peer.worktrees_read(request(version)), request(version))
        rename("renamed-reporter", environment)
        second = worktrees_client.validate_result(peer.worktrees_read(request(version)), request(version))
        assert first["reporting_context"] == {
            "version": 1, "status": "resolved", "hostname": "observed-host",
            "machine": "physical", "environment": environment, "execution_machine": "first",
        }
        assert second["reporting_context"] == {
            **first["reporting_context"], "execution_machine": "renamed-reporter",
        }
        assert first["worktrees"] == second["worktrees"]
        assert first["projection"] == second["projection"]
        assert durable_bytes(owner.store.db_path) == before
        assert lookups == ["project", "project"]
    finally:
        server.close()
        owner.shutdown()


@pytest.mark.parametrize("version", [1, 2])
def test_live_heartbeat_renames_reporter_without_changing_row_or_observation_revisions(
    tmp_path, monkeypatch, registry, version,
):
    _path, rename = registry
    monkeypatch.setattr(worktrees_feed, "HEARTBEAT_SECONDS", 0.001)
    owner = WorktreesRuntime(tmp_path / "current.sqlite3")
    owner.activate()
    owner.publish("project", [{"id": "one", "machine": "obsolete"}], observed_at=1)
    heartbeat_lookup = threading.Event()
    renamed = threading.Event()
    original = worktrees_reporting.current
    initial = True

    def synchronized_context(project):
        nonlocal initial
        if initial:
            initial = False
        else:
            heartbeat_lookup.set()
            assert renamed.wait(3), "fixture rename did not release the heartbeat lookup"
        return original(project)

    monkeypatch.setattr(worktrees_reporting, "current", synchronized_context)
    receiver = worktrees_feed_client.FeedReceiver(request(version))
    server = control.ControlServer(
        lambda action, body: owner.health() if action == "health" else owner.handle(body),
        token="fixture",
        stream_handle=lambda action, body, disconnected: owner.feed.stream(body, disconnected),
    )
    server.start()
    peer = control.ControlClient(f"http://127.0.0.1:{server.port}", token="fixture")
    stream = worktrees_feed_client.transport(peer, request(version))
    try:
        frames = []
        while receiver.current is None:
            frame = next(stream)
            frames.append(frame)
            receiver.receive(frame)
        first = receiver.current
        assert heartbeat_lookup.wait(3), "provider did not reach its heartbeat lookup"
        before = durable_bytes(owner.store.db_path)
        rename("renamed-reporter")
        renamed.set()
        heartbeat = next(stream)
        assert heartbeat["type"] == "heartbeat"
        assert receiver.receive(heartbeat) == [heartbeat]
        assert receiver.current["reporting_context"]["execution_machine"] == "renamed-reporter"
        assert receiver.current["worktrees"] == first["worktrees"]
        assert receiver.current["projection"] == first["projection"]
        assert receiver.current.get("observations") == first.get("observations")
        assert durable_bytes(owner.store.db_path) == before

        # The current Manager receiver consumes naming outside semantic wire rows.
        monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "worktree-manager" / "src"))
        from worktree_manager.worktree_feed_contract import CurrentFeed
        from worktree_manager.worktree_rows_contract import publication_wire_rows

        consumer = CurrentFeed()
        for frame in frames:
            consumer.receive(frame)
        rows = publication_wire_rows(consumer.rows)
        head = dict(consumer.head)
        assert consumer.receive(heartbeat) == consumer.rows
        assert consumer.reporting_context == heartbeat["reporting_context"]
        assert publication_wire_rows(consumer.rows) == rows
        assert consumer.head == head
    finally:
        renamed.set()
        stream.close()
        server.close()
        owner.shutdown()


def test_snapshot_cli_stream_preserves_reporter_context(tmp_path, monkeypatch, registry, capfd):
    from agent_worktrees import list_cache

    owner = WorktreesRuntime(tmp_path / "current.sqlite3")
    owner.activate()
    owner.publish("project", [{"id": "one"}], observed_at=1)
    monkeypatch.setattr(cfg, "project_name", lambda: "project")
    monkeypatch.setattr(list_cache, "note_demand", lambda *a, **k: None)
    monkeypatch.setattr(
        worktrees_client, "read_current", lambda payload, **kw: owner.handle(payload),
    )
    try:
        assert worktrees_client.emit_current(argparse.Namespace(
            stream=True, tracking_status="all", all=True, include_other_platforms=True,
        )) == 0
        begin, row, done = [json.loads(line) for line in capfd.readouterr().out.splitlines()]
        assert begin["reporting_context"]["execution_machine"] == "first"
        assert row["wt"] == {"id": "one", "platform": "linux"}
        assert done["projection"] == begin["projection"]
    finally:
        owner.shutdown()


@pytest.mark.parametrize("environment", ["windows", "wsl", "linux"])
def test_unmapped_reporter_uses_shared_standalone_naming(registry, monkeypatch, environment):
    path, _rename = registry
    path.write_text("machines: {}\n", encoding="utf-8")
    monkeypatch.setattr(cfg, "detect_platform", lambda: environment)
    expected = record_reporting.current_context("project")
    assert worktrees_reporting.current("project") == {
        "version": 1, "status": "resolved", "hostname": expected.hostname,
        "machine": expected.machine, "environment": expected.environment,
        "execution_machine": expected.execution_machine,
    }


@pytest.mark.parametrize("defect", ["invalid", "ambiguous", "unreadable"])
def test_bad_current_registry_withdraws_names_without_falling_back_to_rows(
    tmp_path, monkeypatch, registry, defect,
):
    path, _rename = registry
    owner = WorktreesRuntime(tmp_path / "current.sqlite3")
    owner.activate()
    owner.publish("project", [{"id": "one", "machine": "obsolete"}], observed_at=1)
    try:
        assert owner.handle(request())["reporting_context"]["status"] == "resolved"
        if defect == "unreadable":
            def unavailable(*a, **k):
                raise OSError("fixture registry unreadable")
            monkeypatch.setattr(record_reporting, "configured_repository_paths", unavailable)
        else:
            value = {"machines": []} if defect == "invalid" else {"machines": {
                name: {"hostname": "observed-host", "execution_platform": "linux"}
                for name in ("first", "second")
            }}
            path.write_text(yaml.safe_dump(value), encoding="utf-8")
        result = owner.handle(request())
        assert result["ok"] is True
        assert result["reporting_context"] == {"version": 1, "status": "unavailable"}
        worktrees_client.validate_result(result, request())
    finally:
        owner.shutdown()


def test_advisory_context_is_current_but_remote_response_never_uses_reader_identity(
    tmp_path, monkeypatch, registry,
):
    _path, rename = registry
    owner = WorktreesRuntime(tmp_path / "current.sqlite3")
    owner.activate()
    owner.publish("project", [{"id": "one"}], observed_at=1)
    owner.shutdown()
    rename("current-reporter")
    before = durable_bytes(owner.store.db_path)
    saved = worktrees_client.saved_result(request(), "fixture saved rows", db_path=owner.store.db_path)
    assert saved["reporting_context"]["execution_machine"] == "current-reporter"
    assert saved["projection"]["quality"] == "advisory"
    assert durable_bytes(owner.store.db_path) == before
    remote = {
        **saved, "reporting_context": {
            "version": 1, "status": "resolved", "hostname": "remote-observed",
            "machine": "remote-physical", "environment": "wsl", "execution_machine": "remote-guest",
        },
    }
    monkeypatch.setattr(
        record_reporting, "current_context", lambda *a: pytest.fail("reader derived remote identity"),
    )
    assert worktrees_client.validate_result(remote, request(), quality="advisory") == remote
    # Supported provider rollback lacks the optional envelope, not the roster itself.
    prior = {key: value for key, value in remote.items() if key != "reporting_context"}
    assert worktrees_client.validate_result(prior, request(), quality="advisory") == prior


@pytest.mark.parametrize("context", [
    None, {"version": True, "status": "unavailable"}, {"version": 2, "status": "unavailable"},
    {"version": 1, "status": "unavailable", "machine": "stale"},
    {"version": 1, "status": "resolved"},
    {"version": 1, "status": "resolved", "hostname": "host", "machine": "host",
     "environment": [], "execution_machine": "host"},
])
def test_malformed_reporter_is_rejected_at_point_and_heartbeat_boundaries(tmp_path, context):
    owner = WorktreesRuntime(tmp_path / "current.sqlite3")
    owner.activate()
    owner.publish("project", [{"id": "one"}], observed_at=1)
    try:
        good = owner.handle(request())
        with pytest.raises(ProjectionUnavailable, match="reporting"):
            worktrees_client.validate_result({**good, "reporting_context": context}, request())
        receiver = worktrees_feed_client.FeedReceiver(request())
        for frame in worktrees_feed.publication_frames(good):
            receiver.receive(frame)
        before = receiver.current
        with pytest.raises(ProjectionUnavailable, match="reporting"):
            receiver.receive({
                "type": "heartbeat", "generation": good["projection"]["generation"],
                "cursor": good["projection"]["revision"], "reporting_context": context,
            })
        assert receiver.current == before
    finally:
        owner.shutdown()
