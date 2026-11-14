"""Reporter context changes independently of persisted rows and feed cursors."""

from __future__ import annotations

import argparse
import json
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
        before = durable_bytes(owner.store.db_path)
        rename("renamed-reporter")
        heartbeat = next(stream)
        assert heartbeat["type"] == "heartbeat"
        assert receiver.receive(heartbeat) == [heartbeat]
        assert receiver.current["reporting_context"]["execution_machine"] == "renamed-reporter"
        assert receiver.current["worktrees"] == first["worktrees"]
        assert receiver.current["projection"] == first["projection"]
        assert receiver.current.get("observations") == first.get("observations")
        assert durable_bytes(owner.store.db_path) == before

        # The unchanged Manager receiver is a real prior consumer of both wire versions.
        monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "worktree-manager" / "src"))
        from worktree_manager.worktree_feed_contract import CurrentFeed

        legacy = CurrentFeed()
        for frame in frames:
            legacy.receive(frame)
        rows = legacy.rows
        assert legacy.receive(heartbeat) is None
        assert legacy.rows == rows
    finally:
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
        assert row["wt"] == {"id": "one"}
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
