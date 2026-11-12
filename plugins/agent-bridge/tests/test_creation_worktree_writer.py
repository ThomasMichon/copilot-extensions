"""Ordinary keyed creation retains its writer without acquisition-journal custody."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from agent_bridge import acquisition_authority, acquisition_worktree_writer as writers
from agent_bridge import creation_worktree_writer as creation, session_lifecycle_cli, workspace_creation
from agent_bridge.acquisition_contract import AcquisitionRefused, WorktreeWriterRequirement
from agent_bridge.client import BridgeClient, BridgeClientError
from agent_bridge.models import SessionStatus, StartSessionRequest
from agent_bridge.protocol import HTTP_PROTOCOL_VERSION, HTTP_PROTOCOL_MIN_SUPPORTED
from test_workspace_creation import creation_rig  # noqa: F401
from test_workspace_creation import rig as _connected_rig

rig = _connected_rig

pytestmark = pytest.mark.contract("agent_bridge.workspace_creation")


def test_creation_writer_preparation_preserves_unpinned_rows_and_readonly_refuses(tmp_path):
    from agent_bridge import creation_writer_schema
    from agent_bridge.db import Database

    path = tmp_path / "sessions.db"
    original = Database(path)
    original.execute_write(
        "INSERT INTO workspace_embodiments (key_digest,facts_digest,state,session_id) "
        "VALUES ('prior-key','prior-facts','allocated','prior-session')",
    )
    original.close()
    with sqlite3.connect(path) as conn:
        for name in creation_writer_schema.TRIGGERS:
            conn.execute(f"DROP TRIGGER {name}")
        for name in creation_writer_schema.COLUMNS:
            conn.execute(f"ALTER TABLE workspace_embodiments DROP COLUMN {name}")
        conn.execute("UPDATE schema_version SET version=40")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="current schema"):
        Database(path, read_only=True)
    assert path.read_bytes() == before
    prepared = Database(path)
    try:
        row = dict(prepared.execute_read("SELECT * FROM workspace_embodiments")[0])
        assert row["session_id"] == "prior-session" and row["state"] == "allocated"
        assert all(row[name] is None for name in creation_writer_schema.COLUMNS)
    finally:
        prepared.close()
    readonly = Database(path, read_only=True)
    readonly.close()


@pytest.fixture
def writer_creation(creation_rig, monkeypatch, tmp_path):  # noqa: F811
    rig = creation_rig
    requirement = WorktreeWriterRequirement.model_validate({
        "identity": {"installation_root": str(tmp_path / "owner"), "runtime_generation": "a" * 32},
        "creation_nonce": rig.row["creation_nonce"],
    })
    selected = MagicMock(return_value=(
        ("retained-owner", "original-build"), tmp_path / "owner", tmp_path / "python",
    ))
    monkeypatch.setattr(session_lifecycle_cli, "_agent_worktrees_pinned_selection", selected)
    original = workspace_creation.worktree_command

    async def command(args, project, *, owner_prefix=None):
        if args[0] == "resource-relationship":
            assert owner_prefix == ("retained-owner", "original-build")
            return {
                "schema": "copilot-extensions.resource-relationship", "version": 1,
                "expected_creation_nonce": True, "expected_writer_identity": True,
                "writer_scope": "executing-process", "retained_session_writes": 1,
                "writer_identity": requirement.identity.model_dump(),
                "writer_contract": {
                    "schema": "copilot-extensions.worktree-record-writer",
                    "version": 1, "retained_relationships": 1, "creation_nonce": 1,
                },
            }
        if owner_prefix is None:
            assert writers.current().prefix == ("retained-owner", "original-build")
        else:
            assert owner_prefix == ("retained-owner", "original-build")
        if args[0] == "bind-session":
            rows = rig.db.execute_read("SELECT * FROM workspace_embodiments")
            assert len(rows) == 1
            assert json.loads(rows[0]["creation_writer_json"])["worktree_writer"] == requirement.model_dump()
            assert not rig.db.execute_read("SELECT * FROM acquisition_attempts")
        return await original(args, project)

    monkeypatch.setattr(acquisition_authority, "worktree_command", command)
    monkeypatch.setattr(workspace_creation, "worktree_command", command)
    from agent_bridge import worktree_lineage

    def register(*_args, **_kwargs):
        assert isinstance(writers.current().request, creation.CreationWriterScope)
        assert rig.db.execute_read("SELECT creation_writer_json FROM workspace_embodiments")[0][0]
        return True

    monkeypatch.setattr(worktree_lineage, "register_session", register)
    starts = []
    connect = rig.manager._connect_via_session_host

    async def host_connect(*args, **kwargs):
        result = await connect(*args, **kwargs)
        starts.append(result[1])
        return result

    monkeypatch.setattr(rig.manager, "_connect_via_session_host", host_connect)
    rig.target = replace(rig.target, worktree_id=rig.row["id"])
    return SimpleNamespace(
        rig=rig, requirement=requirement, selected=selected, command=command, starts=starts,
    )


@pytest.mark.asyncio
async def test_keyed_creation_writer_retains_original_scope_and_strict_resume(writer_creation):
    fixture = writer_creation
    rig = fixture.rig
    first, receipt = await workspace_creation.start(
        rig.manager, rig.target, key="original-key", worktree_writer=fixture.requirement,
    )
    saved = dict(rig.db.execute_read("SELECT * FROM workspace_embodiments")[0])
    assert first.target.venue[creation.JOURNAL_KEY] == saved["key_digest"]
    assert not rig.db.execute_read("SELECT * FROM acquisition_attempts")
    assert not any(args[0] == "create" for args, _project in rig.calls)
    fixture.selected.side_effect = AssertionError("must not select another writer")
    replay, replay_receipt = await workspace_creation.start(
        rig.manager, rig.target, key="original-key", worktree_writer=fixture.requirement,
    )
    assert replay is first and replay_receipt["native_session_id"] == receipt["native_session_id"]
    await rig.manager.stop_session(first.session_id, force=True)
    assert first.status == SessionStatus.STOPPED
    resumed = await rig.manager.resume_session(
        first.session_id, drain=False, allow_recreate=True,
        worktree_writer=fixture.requirement,
    )
    assert resumed is first and resumed.acp_session_id == receipt["native_session_id"]
    assert fixture.selected.call_count == 1
    assert writers.current() is None
    assert dict(rig.db.execute_read("SELECT * FROM workspace_embodiments")[0]) == saved


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["requirement", "pointer", "native", "alias"])
async def test_required_creation_resume_refuses_changed_custody_before_effect(
    writer_creation, monkeypatch, changed,
):
    fixture = writer_creation
    rig = fixture.rig
    session, _receipt = await workspace_creation.start(
        rig.manager, rig.target, key="original-key", worktree_writer=fixture.requirement,
    )
    await rig.manager.stop_session(session.session_id, force=True)
    requirement = fixture.requirement.model_dump()
    target = session.session_id
    if changed == "requirement":
        requirement["creation_nonce"] = "replacement"
    elif changed == "pointer":
        session.target = replace(session.target, venue={})
    elif changed == "native":
        session.acp_session_id = "replacement"
    else:
        target = "alias"
        monkeypatch.setattr(rig.manager, "_resolve_ref", lambda _target: session.session_id)
    fixture.selected.side_effect = AssertionError("must not select a replacement writer")
    connect = AsyncMock(side_effect=AssertionError("refused resume must not launch"))
    monkeypatch.setattr(rig.manager, "_connect_via_session_host", connect)
    with pytest.raises(AcquisitionRefused):
        await rig.manager.resume_session(target, worktree_writer=requirement)
    connect.assert_not_awaited()
    assert session.status == SessionStatus.STOPPED


@pytest.mark.asyncio
async def test_keyed_creation_writer_rehydrates_same_native_without_reselection(writer_creation):
    from agent_bridge.session_manager import SessionManager

    fixture = writer_creation
    rig = fixture.rig
    first, receipt = await workspace_creation.start(
        rig.manager, rig.target, key="original-key", worktree_writer=fixture.requirement,
    )
    saved = dict(rig.db.execute_read("SELECT * FROM workspace_embodiments")[0])
    fixture.selected.side_effect = AssertionError("a new frontend cannot select another writer")
    recovered = SessionManager(rig.db)
    restored, replay = await workspace_creation.start(
        recovered, rig.target, key="original-key", worktree_writer=fixture.requirement,
    )
    assert restored is not first and restored.session_id == first.session_id
    assert restored.acp_session_id == receipt["native_session_id"] == replay["native_session_id"]
    assert restored.status == SessionStatus.IDLE
    assert restored.target.venue[creation.JOURNAL_KEY] == saved["key_digest"]
    assert dict(rig.db.execute_read("SELECT * FROM workspace_embodiments")[0]) == saved
    assert fixture.selected.call_count == 1


@pytest.mark.asyncio
async def test_keyed_creation_writer_concurrent_replay_and_changed_key_do_not_reallocate(writer_creation):
    fixture = writer_creation
    rig = fixture.rig
    results = await asyncio.gather(*(
        workspace_creation.start(
            rig.manager, rig.target, key="original-key", worktree_writer=fixture.requirement,
        ) for _ in range(2)
    ), return_exceptions=True)
    assert any(isinstance(result, tuple) for result in results)
    assert all(isinstance(result, (tuple, AcquisitionRefused)) for result in results)
    assert len(fixture.starts) == len(rig.manager.list_sessions()) == 1
    fixture.selected.side_effect = AssertionError("changed key cannot choose another writer")
    with pytest.raises(AcquisitionRefused, match="original creation key"):
        await workspace_creation.start(
            rig.manager, rig.target, key="another-key", worktree_writer=fixture.requirement,
        )
    assert len(rig.db.execute_read("SELECT * FROM workspace_embodiments")) == 1


@pytest.mark.asyncio
async def test_keyed_creation_writer_reconciles_lost_completion_without_new_native(
    writer_creation, monkeypatch,
):
    fixture = writer_creation
    rig = fixture.rig
    update = workspace_creation._update

    def lose_completion(db, digest, **fields):
        if fields.get("state") == "complete":
            raise RuntimeError("lost completion")
        update(db, digest, **fields)

    monkeypatch.setattr(workspace_creation, "_update", lose_completion)
    with pytest.raises(RuntimeError, match="lost completion"):
        await workspace_creation.start(
            rig.manager, rig.target, key="original-key", prompt="Original turn",
            worktree_writer=fixture.requirement,
        )
    original = rig.manager.list_sessions()[0]
    monkeypatch.setattr(workspace_creation, "_update", update)
    fixture.selected.side_effect = AssertionError("no replacement selection")
    replay, _receipt = await workspace_creation.start(
        rig.manager, rig.target, key="original-key", prompt="Original turn",
        worktree_writer=fixture.requirement,
    )
    assert replay is original
    assert len(rig.manager.list_sessions()) == len(rig.db.execute_read("SELECT * FROM turns")) == 1
    assert fixture.starts == [original.acp_session_id]
    await asyncio.sleep(0)


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["dropped", "nonce", "path", "pointer", "missing-journal"])
async def test_keyed_creation_writer_refuses_changed_custody_before_effects(writer_creation, changed):
    fixture = writer_creation
    rig = fixture.rig
    first, _receipt = await workspace_creation.start(
        rig.manager, rig.target, key="original-key", worktree_writer=fixture.requirement,
    )
    fixture.selected.side_effect = AssertionError("no replacement selection")
    await rig.manager.stop_session(first.session_id, force=True)
    before = rig.client.new_session.await_count
    if changed == "dropped":
        call = workspace_creation.start(rig.manager, rig.target, key="original-key")
    elif changed == "nonce":
        rig.row["creation_nonce"] = "replacement-allocation"
        call = rig.manager.resume_session(first.session_id, drain=False)
    elif changed == "path":
        first.target = replace(first.target, cwd="/another/worktree")
        call = rig.manager.resume_session(first.session_id, drain=False)
    elif changed == "pointer":
        first.target.venue.pop(creation.JOURNAL_KEY)
        call = rig.manager.resume_session(first.session_id, drain=False)
    else:
        first.target.venue[creation.JOURNAL_KEY] = "b" * 64
        call = rig.manager.resume_session(first.session_id, drain=False)
    with pytest.raises(AcquisitionRefused):
        await call
    assert rig.client.new_session.await_count == before
    assert first.status == SessionStatus.STOPPED
    assert writers.current() is None


@pytest.mark.asyncio
async def test_keyed_creation_writer_cannot_retrofit_or_replace_original_journal(writer_creation):
    fixture = writer_creation
    rig = fixture.rig
    await workspace_creation.start(
        rig.manager, rig.target, key="original-key", worktree_writer=fixture.requirement,
    )
    for sql in (
        "UPDATE workspace_embodiments SET creation_writer_json=NULL",
        "UPDATE workspace_embodiments SET facts_digest='replacement'",
        "DELETE FROM workspace_embodiments",
    ):
        with pytest.raises(sqlite3.IntegrityError, match="writer_retained"):
            rig.db.execute_write(sql)
    rig.db.execute_write(
        "INSERT INTO workspace_embodiments (key_digest,facts_digest,state,session_id) "
        "VALUES ('prior-key','prior-facts','allocated','prior-session')",
    )
    with pytest.raises(sqlite3.IntegrityError, match="writer_retained"):
        rig.db.execute_write(
            "UPDATE workspace_embodiments SET creation_writer_json='{}' WHERE key_digest='prior-key'",
        )


@pytest.mark.parametrize("changes", [
    {"creation_key": None}, {"worktree_id": None}, {"provider_instance": True},
    {"target_dir": "/another"}, {"remote_session_id": "remote"},
    {"parity_fault": "fault"},
])
def test_required_creation_writer_parser_rejects_unsupported_forms(changes):
    with pytest.raises(ValidationError, match="prepared local worktree"):
        StartSessionRequest.model_validate({
            "creation_key": "original-key", "worktree_id": "worktree-a",
            "worktree_writer": {
                "identity": {"installation_root": "/owner", "runtime_generation": "a" * 32},
                "creation_nonce": "original-allocation",
            }, **changes,
        })


@pytest.mark.parametrize("capability", [None, False, "1", 0])
def test_required_creation_writer_client_refuses_unsupported_active_provider(capability):
    client = BridgeClient("http://fixture", token="fixture")
    client.daemon_supports = lambda _floor: True
    calls = []

    def request(method, path, *args, **kwargs):
        calls.append((method, path))
        assert (method, path) == ("GET", "/health")
        return {
            "protocol_version": HTTP_PROTOCOL_VERSION, "min_protocol_version": HTTP_PROTOCOL_MIN_SUPPORTED,
            "capabilities": {creation.CAPABILITY: capability},
        }

    client._request = request
    with pytest.raises(BridgeClientError) as refused:
        client.start_session(
            agent="worker", creation_key="original-key", worktree_id="worktree-a",
            worktree_writer={
                "identity": {"installation_root": "/owner", "runtime_generation": "a" * 32},
                "creation_nonce": "original-allocation",
            },
        )
    assert refused.value.status == 426
    assert calls == [("GET", "/health")]


def test_required_creation_writer_rechecks_capability_after_provider_cutover():
    client = BridgeClient("http://fixture", token="fixture", connect_grace=2)
    client.daemon_supports = lambda _floor: True
    client._reresolve = lambda: "http://older"
    posts = []

    def request(method, path, *args, **kwargs):
        assert kwargs["allow_retry"] is False
        if path == "/health":
            return {
                "protocol_version": HTTP_PROTOCOL_VERSION, "min_protocol_version": 1,
                "capabilities": {creation.CAPABILITY: 1} if not posts else {},
            }
        posts.append((method, path))
        raise BridgeClientError(503, "draining")

    client._request = request
    with pytest.raises(BridgeClientError) as refused:
        client.start_session(
            agent="worker", creation_key="original-key", worktree_id="worktree-a",
            worktree_writer={
                "identity": {"installation_root": "/owner", "runtime_generation": "a" * 32},
                "creation_nonce": "original-allocation",
            },
        )
    assert refused.value.status == 426
    assert posts == [("POST", "/api/v1/sessions/workspace-creations")]


@pytest.mark.parametrize("from_file", [False, True])
@pytest.mark.parametrize("supported", [False, True])
def test_required_creation_writer_cli_carries_requirement_to_actual_client(
    monkeypatch, tmp_path, capsys, from_file, supported,
):
    import io
    import sys
    from agent_bridge import __main__ as core, session_start_cli, session_targeting_cli

    requirement = {
        "version": 1,
        "identity": {"installation_root": "/owner", "runtime_generation": "a" * 32},
        "creation_nonce": "original-allocation",
    }
    text = json.dumps(requirement)
    path = tmp_path / "requirement.json"
    if from_file:
        path.write_text(text, encoding="utf-8")
    else:
        monkeypatch.setattr(sys, "stdin", io.StringIO(text))
    args = core.build_parser().parse_args([
        "--json", "create", "example@local", "--creation-key", "original-key",
        "--worktree-id", "worktree-a", "--worktree-writer-file", str(path) if from_file else "-",
    ])
    client = BridgeClient("http://fixture", token="fixture", connect_grace=0)
    monkeypatch.setattr(client, "daemon_supports", lambda _floor: True)
    posts = []

    def request(method, endpoint, *values, **kwargs):
        if endpoint == "/health":
            return {
                "protocol_version": HTTP_PROTOCOL_VERSION, "min_protocol_version": 1,
                "capabilities": {creation.CAPABILITY: 1} if supported else {},
            }
        if method == "POST":
            assert endpoint == "/api/v1/sessions/workspace-creations"
            posts.append(values[0])
            return {
                "session_id": "bridge-original",
                "creation": {
                    "status": "complete", "native_session_id": "native-original",
                    "worktree": {"id": "worktree-a", "path": "/workspace"},
                },
            }
        assert endpoint == "/api/v1/sessions/bridge-original"
        return {"status": "idle", "turn_count": 0}

    monkeypatch.setattr(client, "_request", request)
    monkeypatch.setattr(core, "_get_client", lambda: client)
    monkeypatch.setattr(core, "_caller_id_for", lambda _args: None)
    monkeypatch.setattr(core, "_get_caller_session_id", lambda: None)
    monkeypatch.setattr(core, "_worktrees_get", lambda _field: None)
    monkeypatch.setattr(core, "_connection_identity", lambda *_args: {})
    monkeypatch.setattr(session_start_cli, "_read_agent_inventory", lambda _client: {})
    monkeypatch.setattr(
        session_start_cli, "_prepare_sender_preference",
        lambda *_args, **_kwargs: SimpleNamespace(project="example"),
    )
    if not supported:
        with pytest.raises(BridgeClientError) as failure:
            session_targeting_cli._cmd_create(args)
        assert failure.value.status == 426 and posts == []
        return
    session_targeting_cli._cmd_create(args)
    assert len(posts) == 1
    assert posts[0]["worktree_writer"] == requirement
    assert posts[0]["creation_key"] == "original-key" and posts[0]["worktree_id"] == "worktree-a"
    assert json.loads(capsys.readouterr().out)["connection"]["native_session_id"] == "native-original"


@pytest.mark.parametrize("options,text", [
    ([], "{}"),
    (["--creation-key", "original"], "{}"),
    (["--creation-key", "original", "--worktree-id", "w", "--provider-instance"], "{}"),
    (["--creation-key", "original", "--worktree-id", "w", "--cli"], "{}"),
    (["--creation-key", "original", "--worktree-id", "w", "--target-dir", "/elsewhere"], "{}"),
    (["--creation-key", "original", "--worktree-id", "w", "--prompt-file", "-"], "{}"),
    (["--creation-key", "original", "--worktree-id", "w", "-"], "{}"),
    (["--creation-key", "original", "--worktree-id", "w"], "not json"),
    (["--creation-key", "original", "--worktree-id", "w"], "{}"),
    (["--creation-key", "original", "--worktree-id", "w"], "[]"),
])
def test_required_creation_writer_cli_invalid_input_refuses_before_connect(
    monkeypatch, options, text,
):
    import io
    import sys
    from agent_bridge import __main__ as core, session_targeting_cli

    monkeypatch.setattr(sys, "stdin", io.StringIO(text))
    monkeypatch.setattr(core, "_get_client", lambda: pytest.fail("invalid writer must not connect"))
    with pytest.raises(SystemExit) as failure:
        args = core.build_parser().parse_args([
            "create", "example@local", *options, "--worktree-writer-file", "-",
        ])
        session_targeting_cli._cmd_create(args)
    assert failure.value.code == 2


@pytest.mark.parametrize("target", ["remote-acp:fixture", "https://example.com/acp"])
def test_required_creation_writer_cli_rejects_remote_before_connect(monkeypatch, target):
    import io
    import sys
    from agent_bridge import __main__ as core, session_targeting_cli

    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({
        "version": 1,
        "identity": {"installation_root": "/owner", "runtime_generation": "a" * 32},
        "creation_nonce": "original-allocation",
    })))
    monkeypatch.setattr(core, "_get_client", lambda: pytest.fail("remote writer must not connect"))
    args = core.build_parser().parse_args([
        "create", target, "--creation-key", "original-key", "--worktree-id", "worktree-a",
        "--worktree-writer-file", "-",
    ])
    with pytest.raises(SystemExit) as failure:
        session_targeting_cli._cmd_create(args)
    assert failure.value.code == 2


def test_required_creation_writer_round_trips_through_actual_http_owner(
    writer_creation, monkeypatch, capsys,
):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from agent_bridge import creation_writer_inspection as inspection
    from agent_bridge import __main__ as core
    from agent_bridge.routes import health, sessions

    fixture = writer_creation
    rig = fixture.rig
    app = FastAPI()
    app.include_router(sessions.router)
    app.include_router(health.router)
    app.state.session_manager = rig.manager
    app.state.ready = True
    activations = []

    async def resolve(_name, *, sender_repo=None, activate=True):
        activations.append(activate)
        return rig.target

    app.state.resolver = SimpleNamespace(resolve_async=resolve)
    monkeypatch.setattr(health, "observe_worktree_writer", lambda: fixture.requirement.identity.model_dump())
    monkeypatch.setattr(health, "slot_descriptor", lambda *_a, **_kw: {})
    monkeypatch.setattr(health, "get_default_manager", lambda: SimpleNamespace(carrier_diagnostics=lambda: {}))
    instance = {"installation_root": "/bridge", "pid": 42, "generation": 1}
    monkeypatch.setattr(inspection, "_instance", lambda: dict(instance))

    with TestClient(app) as http:
        client = BridgeClient("http://fixture", token="fixture", connect_grace=0)

        def send(method, path, body=None, **_kwargs):
            response = http.request(method, path, json=body)
            if response.status_code >= 400:
                raise BridgeClientError(response.status_code, response.json().get("detail"))
            return response.json()

        client._request = send
        result = client.start_session(
            agent="worker", creation_key="original-key", worktree_id=rig.row["id"],
            worktree_writer=fixture.requirement.model_dump(),
        )
        original = dict(rig.db.execute_read("SELECT * FROM workspace_embodiments")[0])
        fixture.selected.side_effect = AssertionError("inspection must not select a writer")
        requirement = fixture.requirement.model_dump()
        assert http.post(
            f"/api/v1/sessions/{result['session_id']}/stop", params={"force": "true"},
        ).status_code == 204
        resumed = client.resume_session(result["session_id"], worktree_writer=requirement)
        assert resumed["session_id"] == result["session_id"]
        assert resumed["acp_session_id"] == result["creation"]["native_session_id"]
        with pytest.raises(BridgeClientError) as changed:
            client.resume_session(
                result["session_id"],
                worktree_writer={**requirement, "creation_nonce": "replacement"},
            )
        assert changed.value.status == 409
        assert http.post(
            f"/api/v1/sessions/{result['session_id']}/resume",
            json={"worktree_writer": {"unknown": True}},
        ).status_code == 422
        assert http.post(
            f"/api/v1/sessions/{result['session_id']}/resume", json={"unknown": True},
        ).status_code == 422
        observed = client.get_session(result["session_id"], worktree_writer=True)
        assert observed["worktree_writer_observation"]["worktree_writer"] == fixture.requirement.model_dump()
        assert observed["worktree_writer_observation"]["bridge_instance"] == instance
        monkeypatch.setattr(core, "_get_client", lambda **_: client)
        cli_args = core.build_parser().parse_args([
            "status", result["session_id"], "--full", "--worktree-writer",
        ])
        cli_args.func(cli_args)
        assert json.loads(capsys.readouterr().out) == observed
        cli_args = core.build_parser().parse_args(["--json", "status"])
        cli_args.func(cli_args)
        health_info = json.loads(capsys.readouterr().out)
        assert health_info["capabilities"]["retained_workspace_creation_writer_inspection"] == 1
        assert health_info["worktree_record_writer"] == fixture.requirement.identity.model_dump()
        assert "worktree_writer_observation" not in client.get_session(result["session_id"])
        assert dict(rig.db.execute_read("SELECT * FROM workspace_embodiments")[0]) == original
        assert not rig.db.execute_read("SELECT * FROM acquisition_attempts")
    assert activations == [False]
    assert len(fixture.starts) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["instance", "journal", "readiness", "nonce"])
async def test_creation_writer_inspection_refuses_changed_owner_without_effects(
    writer_creation, monkeypatch, changed,
):
    from agent_bridge import creation_writer_inspection as inspection

    fixture = writer_creation
    rig = fixture.rig
    session, _receipt = await workspace_creation.start(
        rig.manager, rig.target, key="original-key", worktree_writer=fixture.requirement,
    )
    instances = iter([{"generation": 1}, {"generation": 2} if changed == "instance" else {"generation": 1}])
    monkeypatch.setattr(inspection, "_instance", lambda: next(instances))
    command = fixture.command
    ready = [True]

    async def changing_owner(args, project, **kwargs):
        result = await command(args, project, **kwargs)
        if args[0] == "record":
            if changed == "journal":
                rig.db.execute_write("UPDATE workspace_embodiments SET state='terminal'")
            elif changed == "readiness":
                ready[0] = False
            elif changed == "nonce":
                result["worktree"]["creation_nonce"] = "replacement"
        return result

    monkeypatch.setattr(acquisition_authority, "worktree_command", changing_owner)
    fixture.selected.side_effect = AssertionError("inspection must not select a writer")
    with pytest.raises(AcquisitionRefused):
        await inspection.inspect_writer(rig.manager, session, lambda: ready[0])
    assert len(fixture.starts) == 1
    assert writers.current() is None


def test_required_creation_writer_inspection_rejects_ignored_legacy_query():
    from agent_bridge.creation_writer_inspection import CAPABILITY

    client = BridgeClient("http://fixture", token="fixture", connect_grace=0)

    def request(_method, path, **_kwargs):
        if path == "/health":
            return {
                "protocol_version": HTTP_PROTOCOL_VERSION, "min_protocol_version": 1,
                "capabilities": {CAPABILITY: 1},
            }
        return {"session_id": "original-session"}

    client._request = request
    with pytest.raises(BridgeClientError) as refused:
        client.get_session("original-session", worktree_writer=True)
    assert refused.value.status == 502


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", ["link", "seed"])
async def test_keyed_creation_handoff_retains_original_successor_and_seed(
    writer_creation, monkeypatch, failed,
):
    from agent_bridge import worktree_lineage

    fixture = writer_creation
    rig = fixture.rig
    predecessor, _receipt = await workspace_creation.start(
        rig.manager, rig.target, key="original-key", worktree_writer=fixture.requirement,
    )
    predecessor.status = SessionStatus.IDLE
    rig.client.acp_session_id = "native-successor"
    failures = [failed]

    def link(_worktree, _predecessor, successor, **_kwargs):
        if failures == ["link"]:
            failures.clear()
            raise AcquisitionRefused("owner_pending", "lineage pending")
        rig.head["id"] = successor

    monkeypatch.setattr(worktree_lineage, "link_succession", link)
    for name in ("note_handoff", "session_role", "history_digest"):
        monkeypatch.setattr(worktree_lineage, name, lambda *_a, **_kw: None)

    async def seed(*_args, **_kwargs):
        if failures == ["seed"]:
            failures.clear()
            raise AcquisitionRefused("owner_pending", "seed pending")
        return 0

    submit = AsyncMock(side_effect=seed)
    monkeypatch.setattr(rig.manager, "submit_prompt", submit)
    with pytest.raises(AcquisitionRefused):
        await rig.manager.handoff_session(
            predecessor.session_id, reason="fixture", seed_text="Original continuation",
        )
    assert len(fixture.starts) == 2
    original = dict(rig.db.execute_read("SELECT * FROM workspace_embodiments")[0])
    handoff = json.loads(original["creation_handoffs_json"])[predecessor.session_id]
    assert handoff["successor_id"] != predecessor.session_id
    assert handoff["seed_prompt"] == "Original continuation"
    fixture.selected.side_effect = AssertionError("must retain the original command")
    if failed == "seed":
        with pytest.raises(AcquisitionRefused, match="seed"):
            await rig.manager.handoff_session(
                predecessor.session_id, reason="fixture", seed_text="Original continuation",
            )
        assert submit.await_count == 1
    else:
        successor = await rig.manager.handoff_session(
            predecessor.session_id, reason="fixture", seed_text="Original continuation",
        )
        assert successor.session_id == handoff["successor_id"]
        assert submit.await_count == 1
        assert successor.target.venue[creation.JOURNAL_KEY] == original["key_digest"]
    assert len(fixture.starts) == 2
    assert not rig.db.execute_read("SELECT * FROM acquisition_attempts")


@pytest.mark.asyncio
async def test_keyed_creation_registration_failure_retains_native_and_dormancy(writer_creation, monkeypatch):
    from agent_bridge import worktree_lineage

    fixture = writer_creation
    rig = fixture.rig

    def refuse_registration(*_args, **_kwargs):
        raise AcquisitionRefused("owner_pending", "registration pending")

    monkeypatch.setattr(worktree_lineage, "register_session", refuse_registration)
    with pytest.raises(AcquisitionRefused, match="registration is pending"):
        await workspace_creation.start(
            rig.manager, rig.target, key="original-key", worktree_writer=fixture.requirement,
        )
    session = rig.manager.list_sessions()[0]
    original = dict(rig.db.execute_read("SELECT * FROM workspace_embodiments")[0])
    assert original["native_session_id"] == session.acp_session_id
    assert not session.background_recovery_enabled and session.client is None
    assert session.status == SessionStatus.STOPPED
    assert not rig.head["id"]
    fixture.selected.side_effect = AssertionError("registration retry must retain the original writer")
    with pytest.raises(AcquisitionRefused, match="registration pending"):
        await workspace_creation.start(
            rig.manager, rig.target, key="original-key", worktree_writer=fixture.requirement,
        )
    monkeypatch.setattr(worktree_lineage, "register_session", lambda *_a, **_kw: True)
    resumed, receipt = await workspace_creation.start(
        rig.manager, rig.target, key="original-key", worktree_writer=fixture.requirement,
    )
    assert resumed is session and receipt["native_session_id"] == original["native_session_id"]
    assert session.background_recovery_enabled and len(fixture.starts) == 1


@pytest.mark.asyncio
@pytest.mark.timeout(120)
async def test_keyed_creation_uses_actual_retained_serializer_and_preserves_claim(
    rig, tmp_path, monkeypatch,
):
    import time

    started = time.monotonic()
    import os
    from pathlib import Path
    import sys
    from agent_procutil import direct_python
    from agent_bridge import local_launch_preparation, worktree_lineage
    from agent_bridge.transport import SpawnTarget
    from test_worktree_lineage_authority import snapshot as owner_command

    root = Path(__file__).resolve().parents[3]
    state = tmp_path / "authority"
    state.mkdir()
    monkeypatch.syspath_prepend(str(root / "plugins" / "agent-worktrees" / "src"))
    from agent_worktrees import tracking

    def snapshot():
        record = tracking.load_record(state / "project" / "worktrees" / "worktree-a.yaml")
        return {
            "creation_nonce": record.creation_nonce,
            "resources": [item.to_dict() for item in record.resources],
            "head": record.resolved_head_session,
            "sessions": [item.session_id for item in record.sessions],
        }

    identity = {"installation_root": str(state / "owner"), "runtime_generation": "a" * 32}
    identity_file = state / "writer-identity.json"
    identity_file.write_text(json.dumps(identity), encoding="utf-8")
    interpreter, interpreter_env = direct_python(sys.executable)
    for key, value in interpreter_env.items():
        monkeypatch.setenv(key, value)
    prefix = (
        interpreter, str(root / "plugins/agent-worktrees/tests/fixtures/worktree_record_owner.py"),
        str(root), str(state), "test-project", "worktree-a",
    )
    environment = session_lifecycle_cli._agent_worktrees_environment()
    environment.update(interpreter_env)
    from bridge_http47_runtime import REQUEST

    if REQUEST in environment:
        environment["PYTHONPATH"] = os.pathsep.join((
            str(root / "tools/http47_capture_bootstrap"), str(root / "tools"),
        ))
        if os.name == "nt":
            # Source-audited cold imports are not production lineage latency evidence.
            monkeypatch.setattr(worktree_lineage, "_LINEAGE_TIMEOUT_S", 30.0)
        lineage_run = worktree_lineage._run

        def traced_lineage(command, *args, **kwargs):
            print(f"lineage {command[0]} start {time.monotonic() - started:.3f}s", flush=True)
            try:
                return lineage_run(command, *args, **kwargs)
            finally:
                print(f"lineage {command[0]} end {time.monotonic() - started:.3f}s", flush=True)

        async def traced_owner(command, *args, **kwargs):
            print(f"owner {command[0]} start {time.monotonic() - started:.3f}s", flush=True)
            try:
                return await rig.owner_command(command, *args, **kwargs)
            finally:
                print(f"owner {command[0]} end {time.monotonic() - started:.3f}s", flush=True)

        monkeypatch.setattr(worktree_lineage, "_run", traced_lineage)
        receiving_command = traced_owner
    else:
        receiving_command = rig.owner_command
    for owner in (acquisition_authority, worktree_lineage):
        monkeypatch.setattr(owner, "_agent_worktrees_environment", lambda: environment.copy())
    protected = owner_command(list(prefix), "protect")
    before = snapshot()
    checkout = state / "trees" / "worktree-a"
    assert not rig.register_session(
        "worktree-a", "legacy-resident-probe", worktree_dir=str(checkout), owner_prefix=prefix,
    )
    resident_calls = (state / "resident-calls").read_text().splitlines()
    selected = MagicMock(return_value=(prefix, Path(identity["installation_root"]), Path(sys.executable)))
    monkeypatch.setattr(session_lifecycle_cli, "_agent_worktrees_pinned_selection", selected)
    monkeypatch.setattr(
        session_lifecycle_cli, "_agent_worktrees_selection",
        MagicMock(side_effect=AssertionError("must not use a dynamic writer")),
    )
    monkeypatch.setattr(acquisition_authority, "worktree_command", receiving_command)
    monkeypatch.setattr(workspace_creation, "worktree_command", receiving_command)
    monkeypatch.setattr(
        acquisition_authority, "_agent_worktrees_launch_prefix",
        session_lifecycle_cli._agent_worktrees_launch_prefix,
    )
    monkeypatch.setattr(
        worktree_lineage, "_agent_worktrees_launch_prefix",
        session_lifecycle_cli._agent_worktrees_launch_prefix,
    )
    monkeypatch.setattr(worktree_lineage, "register_session", rig.register_session)

    async def resolve(target, **_kwargs):
        return ["copilot", "--acp", "--stdio"], target.cwd, {}

    monkeypatch.setattr(local_launch_preparation, "resolve_local_launch", resolve)
    target = SpawnTarget(
        type="local", project="test-project", worktree_id="worktree-a", cwd=str(checkout),
    )
    requirement = WorktreeWriterRequirement.model_validate({
        "identity": identity, "creation_nonce": before["creation_nonce"],
    })
    session, receipt = await workspace_creation.start(
        rig.manager, target, key="original-key", worktree_writer=requirement,
    )
    after = snapshot()
    assert after["head"] == receipt["native_session_id"]
    assert after["creation_nonce"] == before["creation_nonce"]
    assert [item for item in after["resources"] if item["ref"] == protected["ref"]] == before["resources"]
    assert (state / "resident-calls").read_text().splitlines() == resident_calls
    calls = [json.loads(line) for line in (state / "calls").read_text().splitlines()]
    capability = next(i for i, args in enumerate(calls) if "capabilities" in args)
    for verb in ("register-session", "bind-session"):
        assert capability < next(
            i for i, args in enumerate(calls) if verb in args and "--retained-record-writer" in args
        )
    selected.side_effect = AssertionError("continuation must retain its original writer")
    session.status = SessionStatus.IDLE
    rig.client.acp_session_id = "native-successor"

    async def stop(session_id, **_kwargs):
        rig.manager.get_session(session_id).status = SessionStatus.STOPPED

    monkeypatch.setattr(rig.manager, "stop_session", AsyncMock(side_effect=stop))
    successor = await rig.manager.handoff_session(
        session.session_id, reason="bounded test", seed=False, seed_text="Continue",
    )
    linked = snapshot()
    assert linked["head"] == successor.acp_session_id
    assert linked["creation_nonce"] == before["creation_nonce"]
    assert [item for item in linked["resources"] if item["ref"] == protected["ref"]] == before["resources"]
    assert (state / "resident-calls").read_text().splitlines() == resident_calls
    identity_file.write_text(json.dumps({**identity, "runtime_generation": "b" * 32}), encoding="utf-8")
    with pytest.raises(AcquisitionRefused, match="serializer"):
        await rig.manager.handoff_session(
            successor.session_id, reason="changed writer", seed=False, seed_text="Continue",
        )
    assert snapshot() == linked
    assert selected.call_count == 1
    assert not rig.db.execute_read("SELECT * FROM acquisition_attempts")
