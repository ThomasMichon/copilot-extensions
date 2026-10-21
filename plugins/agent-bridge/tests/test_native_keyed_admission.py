"""Pure turn-journal contracts; mock SDK, not native-host qualification."""

import asyncio
import hashlib
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_bridge.models import SessionStatus
from agent_bridge.events import EventLog
from agent_bridge.client import BridgeClientError
from agent_bridge.native_keyed_admission import KeyedNativePrompt, submit
from agent_bridge.native_keyed_client import send
from agent_bridge.routes.sessions import router
from agent_bridge.send_outcome import SendRefused
from test_native_bound_prompt import prepare


def _keyed_cli_vector():
    proposed = os.environ.get("BRIDGE_HTTP45_PROPOSED_KEYED")
    path = Path(proposed) if proposed else (
        Path(__file__).resolve().parents[1] / "contract/inputs/http45/native-keyed-admission.json"
    )
    raw = path.read_bytes()
    if proposed:
        assert hashlib.sha256(raw).hexdigest() == os.environ["BRIDGE_HTTP45_PROPOSED_SHA256"]
    return json.loads(raw)


@pytest.mark.parametrize("options", [
    ["--interrupt"], ["--delivery", "interrupt"], ["--queue"], ["--delivery", "queue"],
    ["--notify"], ["--kind", "notify"], ["--status-check"], ["--kind", "status-check"],
    ["--steer"], ["--sender", "synthetic"], ["--from", "synthetic"],
    ["--reply-to", "live:synthetic:fixture:wt-a"], ["--wait-reply"],
    ["--reply-timeout", "1"], ["--full-history"], ["--new"],
    ["alternate prompt"], ["--prompt-file", "alternate.json"], ["--force"],
    ["--expected-session-id", "other"], ["--idempotency-key", "other"],
    ["--plugin-context-stdin"], [""], ["--prompt-file", ""],
])
@pytest.mark.parametrize("source", ["file", "stdin"])
def test_real_parser_mixed_keyed_options_refuse_before_input_or_client(
    monkeypatch, tmp_path, capsys, options, source,
):
    from agent_bridge import __main__ as core
    vector = _keyed_cli_vector()
    missing = tmp_path / "nonexistent-invocation.json"
    assert not missing.exists()
    args = core.build_parser().parse_args([
        "--json", "send", "bridge-a", *options, "--native-invocation-file",
        "-" if source == "stdin" else str(missing),
        "--expected-native-session-id", "native-a",
    ])
    client = MagicMock()
    get_client = MagicMock(return_value=client)
    monkeypatch.setattr(core, "_get_client", get_client)
    reads = []

    def forbidden_read(*_args, **_kwargs):
        reads.append(source)
        pytest.fail("mixed keyed options must refuse before input decoding")

    monkeypatch.setattr(Path, "read_text", forbidden_read)
    monkeypatch.setattr("sys.stdin", SimpleNamespace(buffer=SimpleNamespace(read=forbidden_read)))
    with pytest.raises(SystemExit) as refused:
        args.func(args)
    assert refused.value.code == 2
    assert json.loads(capsys.readouterr().out) == vector["cli_mixed_option_refusal"]
    assert reads == []
    get_client.assert_not_called()
    client._request.assert_not_called()


@pytest.mark.parametrize("receipt_only", [False, True])
@pytest.mark.parametrize("output_options", [
    [],
    ["--no-wait", "--no-color", "--caller", "fixture-output", "--expand", "tools",
     "--kind", "prompt", "--delivery", "steer", "--reply-timeout", "120"],
])
def test_real_parser_default_keyed_send_and_receipt_preserve_wire(
    monkeypatch, tmp_path, capsys, receipt_only, output_options,
):
    from agent_bridge import __main__ as core
    vector = _keyed_cli_vector()
    file = tmp_path / "invocation.json"
    file.write_text(json.dumps(vector["request"], ensure_ascii=False), encoding="utf-8")
    calls = []

    class Client:
        _connect_grace = 0
        _base = "http://testserver"

        def _request(self, method, path, body=None, **_kwargs):
            calls.append((method, path, body))
            if path == "/health":
                return vector["response"]
            assert body == vector["request"]
            return vector["admission_response"]

    monkeypatch.setattr(core, "_get_client", lambda: Client())
    args = core.build_parser().parse_args([
        "--json", "send", "bridge-a", "--native-invocation-file", str(file),
        "--expected-native-session-id", "native-a",
        *(["--receipt-only"] if receipt_only else []), *output_options,
    ])
    args.func(args)
    assert json.loads(capsys.readouterr().out) == {
        **vector["admission_response"], "outcome": "delivered",
    }
    endpoint = "/api/v1/sessions/bridge-a/turns/native-bound/keyed"
    assert [call[1] for call in calls] == [
        "/health", endpoint + ("/receipt" if receipt_only else ""),
    ]


@pytest.mark.parametrize("options", [
    [], ["alternate prompt"], ["--notify"], ["--receipt-only"],
])
def test_real_parser_empty_native_selector_never_falls_back(monkeypatch, capsys, options):
    from agent_bridge import __main__ as core
    vector = _keyed_cli_vector()
    args = core.build_parser().parse_args([
        "--json", "send", "bridge-a", *options, "--native-invocation-file", "",
        "--expected-native-session-id", "native-a",
    ])
    assert args.native_invocation_file == ""
    client = MagicMock()
    get_client = MagicMock(side_effect=AssertionError("empty keyed selector reached client/ordinary handling"))
    monkeypatch.setattr(core, "_get_client", get_client)

    def forbidden_read(*_args, **_kwargs):
        pytest.fail("empty keyed selector must refuse without input reads")

    monkeypatch.setattr(Path, "read_text", forbidden_read)
    monkeypatch.setattr("sys.stdin", SimpleNamespace(buffer=SimpleNamespace(read=forbidden_read)))
    with pytest.raises(SystemExit) as refused:
        args.func(args)
    assert refused.value.code == 2
    expected = ("cli_mixed_option_refusal" if options in (["alternate prompt"], ["--notify"])
                else "cli_empty_selector_refusal")
    assert json.loads(capsys.readouterr().out) == vector[expected]
    get_client.assert_not_called()
    client._request.assert_not_called()


@pytest.fixture
def keyed(session_manager, tmp_db, mock_acp_client):
    session = prepare(session_manager, tmp_db, mock_acp_client)
    session.target.worktree_id = "wt-a"
    session.target.project = "fixture"
    session.event_log = EventLog(db=tmp_db, session_id=session.session_id)
    tmp_db.execute_write(
        "INSERT INTO workspace_embodiments "
        "(key_digest,facts_digest,state,session_id,worktree_json,native_session_id) "
        "VALUES ('test','test','complete','bridge-a',?,'native-a')",
        (json.dumps({"creation_nonce": "nonce", "id": "wt-a", "repo": "fixture", "path": "."}),),
    )
    request = KeyedNativePrompt(
        invocation_key="stable", expected_native_session_id="native-a",
        prompt='préambule "quoted"\n第二行\r\nlast',
        binding={"worktree_id": "wt-a", "project": "fixture", "path": ".", "creation_nonce": "nonce",
                 "task_id": "task-a", "admission_generation": 0,
                 "original_reservation_key": "reservation-a", "creation_key": "creation-a",
                 "allocation_revision": 1, "expected_turn_index": 0, "origin_kind": "created"},
    )
    return session_manager, session, request


@pytest.mark.asyncio
@pytest.mark.parametrize("settled", [False, True])
async def test_running_or_settled_replay_is_original_admission(keyed, tmp_db, settled):
    manager, session, request = keyed
    first = await submit(manager, session.session_id, request)
    await session._prompt_task
    events_before = tmp_db.execute_read("SELECT * FROM events")
    if settled:
        session.status = SessionStatus.IDLE
    replay = await submit(manager, session.session_id, request)
    assert first == replay and replay["turn_index"] == 0
    assert len(tmp_db.get_turns(session.session_id)) == session.turn_count == 1
    assert tmp_db.execute_read("SELECT * FROM events") == events_before
    manager._run_prompt.assert_awaited_once()


@pytest.mark.asyncio
async def test_concurrent_identical_admission_calls_sdk_once(keyed):
    manager, session, request = keyed
    receipts = await asyncio.gather(*(submit(manager, session.session_id, request) for _ in range(4)))
    await session._prompt_task
    assert all(r == receipts[0] for r in receipts) and session.turn_count == 1
    manager._run_prompt.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["prompt", "binding"])
async def test_same_key_changed_facts_refuses(keyed, field):
    manager, session, request = keyed
    await submit(manager, session.session_id, request)
    await session._prompt_task
    changed = KeyedNativePrompt.model_validate({
        **request.model_dump(),
        field: "different" if field == "prompt"
        else {**request.binding.model_dump(), "admission_generation": 1},
    })
    with pytest.raises(ValueError, match="conflict"):
        await submit(manager, session.session_id, changed)
    assert session.turn_count == 1


@pytest.mark.asyncio
async def test_receipt_read_missing_never_grants_admission(keyed, tmp_db):
    manager, session, request = keyed
    assert await submit(manager, session.session_id, request, read_only=True) is None
    assert not tmp_db.get_turns(session.session_id) and session.turn_count == 0
    manager._run_prompt.assert_not_awaited()


@pytest.mark.asyncio
async def test_native_replacement_refuses_even_cached_receipt(keyed):
    manager, session, request = keyed
    await submit(manager, session.session_id, request)
    await session._prompt_task
    session.acp_session_id = "replacement"
    with pytest.raises(ValueError, match="expected_native"):
        await submit(manager, session.session_id, request, read_only=True)


@pytest.mark.asyncio
async def test_persist_before_sdk_crash_is_not_resubmitted(keyed, monkeypatch):
    manager, session, request = keyed
    monkeypatch.setattr(manager.db, "update_session_status",
                        MagicMock(side_effect=RuntimeError("crash after durable admission")))
    with pytest.raises(RuntimeError, match="crash after"):
        await submit(manager, session.session_id, request)
    result = await submit(manager, session.session_id, request)
    assert result["turn_index"] == 0 and session.turn_count == 1
    manager._run_prompt.assert_not_awaited()


@pytest.mark.asyncio
async def test_unkeyed_intentional_second_turn_is_unchanged(keyed):
    manager, session, request = keyed
    await submit(manager, session.session_id, request)
    await session._prompt_task
    session.status = SessionStatus.IDLE
    assert await manager.submit_prompt(session.session_id, "intentional follow-up") == 1
    await session._prompt_task
    assert session.turn_count == manager._run_prompt.await_count == 2


@pytest.mark.asyncio
async def test_old_or_corrupt_delivery_history_never_becomes_fresh(keyed):
    manager, session, request = keyed
    manager.db.create_turn(session.session_id, 0, request.prompt, 1,
                           delivery_id=request.delivery_id())
    with pytest.raises(ValueError, match="history_unproven"):
        await submit(manager, session.session_id, request)
    manager._run_prompt.assert_not_awaited()


def test_typed_api_forbids_queue_and_reads_historical_facts(keyed):
    manager, session, request = keyed
    app = FastAPI()
    app.state.session_manager = manager
    app.include_router(router)
    with TestClient(app) as api:
        path = "/api/v1/sessions/bridge-a/turns/native-bound/keyed"
        assert api.post(path, json={**request.model_dump(), "queue": True}).status_code == 422
        assert api.post(path + "/receipt", json=request.model_dump()).status_code == 404
        accepted = api.post(path, json=request.model_dump())
        assert accepted.status_code == 200
        assert api.post(path + "/receipt", json=request.model_dump()).json() == accepted.json()


def test_owner_reader_refusal_is_typed_and_never_submits(keyed, monkeypatch):
    from agent_bridge.acquisition_contract import AcquisitionRefused
    from unittest.mock import AsyncMock
    manager, _, request = keyed
    monkeypatch.setattr(
        "agent_bridge.routes.prompts.keyed_submit",
        AsyncMock(side_effect=AcquisitionRefused("owner_unavailable", "unavailable")),
    )
    app = FastAPI()
    app.state.session_manager = manager
    app.include_router(router)
    with TestClient(app) as api:
        refused = api.post("/api/v1/sessions/bridge-a/turns/native-bound/keyed",
                           json=request.model_dump())
    assert refused.status_code == 409
    assert refused.json() == {"detail": {"code": "owner_unavailable"}}
    manager._run_prompt.assert_not_awaited()


@pytest.mark.parametrize("settled", [False, True])
def test_independent_http_golden_admission_and_replay(keyed, settled):
    manager, session, _ = keyed
    fixture = json.loads((Path(__file__).resolve().parents[1] / "contract" / "fixtures" /
                          "http" / "current" / "ordinary-native-admission-generation-45.json"
                          ).read_text(encoding="utf-8"))
    app = FastAPI()
    app.state.session_manager = manager
    app.include_router(router)
    with TestClient(app) as api:
        path = "/api/v1/sessions/bridge-a/turns/native-bound/keyed"
        missing = api.post(path + "/receipt", json=fixture["request"])
        assert missing.status_code == 404
        assert missing.json() == fixture["missing_receipt_refusal"]
        accepted = api.post(path, json=fixture["request"])
        assert accepted.status_code == 200
        assert accepted.json() == fixture["admission_response"]
        if settled:
            session.status = SessionStatus.IDLE
        events = manager.db.execute_read("SELECT * FROM events")
        for endpoint in (path, path + "/receipt"):
            replay = api.post(endpoint, json=fixture["request"])
            assert replay.status_code == 200
            assert replay.json() == fixture["admission_response"]
        conflict = api.post(path, json={**fixture["request"], "prompt": "different"})
        assert conflict.status_code == 409
        assert conflict.json() == fixture["changed_prompt_refusal"]
        assert manager.db.execute_read("SELECT * FROM events") == events
        assert len(manager.db.get_turns(session.session_id)) == session.turn_count == 1
        manager._run_prompt.assert_awaited_once()


@pytest.mark.parametrize("source", ["stdin", "file"])
def test_unicode_request_codec_and_actual_protocol_floor(keyed, monkeypatch, tmp_path, capsys, source):
    _, _, request = keyed
    raw = json.dumps(request.model_dump(), ensure_ascii=False)
    filename = tmp_path / "request.json"
    filename.write_text(raw, encoding="utf-8")
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(raw.encode("utf-8")), encoding="utf-8"))
    calls = []

    class Client:
        _connect_grace = 0
        _base = "http://testserver"

        def _request(self, method, path, body=None, **kwargs):
            calls.append((method, path, body))
            if path == "/health":
                return {"protocol_version": 45}
            assert body == request.model_dump()
            return {"version": 1, "invocation_key": "stable", "native_session_id": "native-a",
                    "session_id": "bridge-a", "turn_index": 0, "queued": False,
                    "admission": "accepted"}

    send(SimpleNamespace(native_invocation_file="-" if source == "stdin" else str(filename),
                         expected_native_session_id="native-a", target="bridge-a",
                         queue=False, receipt_only=False), Client())
    assert json.loads(capsys.readouterr().out)["outcome"] == "delivered"
    assert len(calls) == 2


def test_unsupported_receiver_never_gets_post(keyed, tmp_path):
    _, _, request = keyed
    file = tmp_path / "request.json"
    file.write_text(request.model_dump_json(), encoding="utf-8")
    client = MagicMock()
    client._connect_grace = 0
    client._base = "http://testserver"
    client._request.return_value = {"protocol_version": 44}
    original = client._request
    with pytest.raises(SendRefused):
        send(SimpleNamespace(native_invocation_file=str(file), expected_native_session_id="native-a",
                             target="bridge-a", queue=False, receipt_only=False), client)
    assert original.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["worktree_id", "project", "path", "creation_nonce"])
async def test_fresh_binding_changes_refuse_without_sdk(keyed, field):
    manager, session, request = keyed
    changed = KeyedNativePrompt.model_validate({
        **request.model_dump(), "binding": {**request.binding.model_dump(), field: "changed"},
    })
    with pytest.raises(ValueError, match="binding_changed"):
        await submit(manager, session.session_id, changed)
    assert session.turn_count == 0
    manager._run_prompt.assert_not_awaited()


@pytest.mark.asyncio
async def test_retired_workspace_refuses_fresh_admission(keyed):
    manager, session, request = keyed
    manager.db.execute_write("UPDATE workspace_embodiments SET state='terminal'")
    with pytest.raises(ValueError, match="history_unproven"):
        await submit(manager, session.session_id, request)
    assert session.turn_count == 0


@pytest.mark.asyncio
async def test_missing_accepted_turn_is_not_permission_to_resubmit(keyed):
    manager, session, request = keyed
    await submit(manager, session.session_id, request)
    await session._prompt_task
    manager.db.execute_write("DELETE FROM turns")
    session.status = SessionStatus.IDLE
    assert await submit(manager, session.session_id, request, read_only=True) is None
    with pytest.raises(ValueError, match="history_unproven"):
        await submit(manager, session.session_id, request)
    manager._run_prompt.assert_awaited_once()


@pytest.mark.asyncio
async def test_holder_is_rechecked_after_admission_await(keyed, monkeypatch):
    manager, session, request = keyed

    async def replacement(*_args):
        session.acp_session_id = "replaced-during-await"

    monkeypatch.setattr("agent_bridge.remote_relationships.assert_admission", replacement)
    with pytest.raises(ValueError, match="expected_native"):
        await submit(manager, session.session_id, request)
    assert not manager.db.get_turns(session.session_id)
    manager._run_prompt.assert_not_awaited()


@pytest.mark.asyncio
async def test_real_claim_g1_can_only_read_original_g0_receipt(keyed, tmp_path, monkeypatch):
    from dataclasses import asdict
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "agent-dispatch/src"))
    from agent_dispatch.queue import TaskQueue
    queue = TaskQueue(tmp_path / "actual-claim.db")
    task = queue.create(title="claim transition", repo="fixture")
    manager, session, original = keyed
    request = KeyedNativePrompt.model_validate({
        **original.model_dump(), "binding": {
            **original.binding.model_dump(), "task_id": task.id,
            "admission_generation": task.generation,
        },
    })
    admitted = await submit(manager, session.session_id, request)
    await session._prompt_task
    assert queue.claim_one("worker", task_id=task.id).generation == 1
    before_task, before_events = asdict(queue.get(task.id)), queue.events(task.id)
    assert await submit(manager, session.session_id, request, read_only=True) == admitted
    assert asdict(queue.get(task.id)) == before_task and queue.events(task.id) == before_events
    manager._run_prompt.assert_awaited_once()


@pytest.mark.parametrize("version", [True, 2, "1"])
def test_unknown_facts_version_is_explicit_refusal(keyed, version):
    _, _, request = keyed
    with pytest.raises(ValueError):
        KeyedNativePrompt.model_validate({**request.model_dump(), "version": version})


def test_receiver_cutover_404_never_falls_back(keyed, tmp_path):
    _, _, request = keyed
    file = tmp_path / "request.json"
    file.write_text(request.model_dump_json(), encoding="utf-8")
    paths = []

    class Client:
        _connect_grace = 0
        _base = "http://testserver"

        def _request(self, method, path, body=None, **kwargs):
            paths.append(path)
            if path == "/health":
                return {"protocol_version": 45}
            raise BridgeClientError(404, "Not Found")

    with pytest.raises(SendRefused, match="Not Found"):
        send(SimpleNamespace(native_invocation_file=str(file),
                             expected_native_session_id="native-a",
                             target="bridge-a", receipt_only=False, queue=False), Client())
    assert paths == ["/health", "/api/v1/sessions/bridge-a/turns/native-bound/keyed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_nonce", [False, True])
async def test_new_resumed_invocation_requires_canonical_owner_not_creation_fallback(keyed, monkeypatch, bad_nonce):
    from unittest.mock import AsyncMock
    manager, session, original = keyed
    manager.db.execute_write("DELETE FROM workspace_embodiments")
    request = KeyedNativePrompt.model_validate({
        **original.model_dump(),
        "binding": {**original.binding.model_dump(), "origin_kind": "resumed"},
    })
    owner = {
        "id": "wt-a", "repo": "fixture", "path": ".", "status": "active",
        "sessions_indexed": True, "creation_nonce": "changed" if bad_nonce else "nonce",
        "execution_leg": None, "session_backend": None,
    }
    monkeypatch.setattr("agent_bridge.acquisition_authority.read_worktree_record",
                        AsyncMock(return_value=owner))
    monkeypatch.setattr("agent_bridge.acquisition_authority.worktree_command", AsyncMock(return_value={
        "tracked": True, "head_session": "native-a", "active": True, "state": "active",
    }))
    if bad_nonce:
        with pytest.raises(ValueError, match="binding_changed"):
            await submit(manager, session.session_id, request)
        manager._run_prompt.assert_not_awaited()
    else:
        assert (await submit(manager, session.session_id, request))["turn_index"] == 0
        await session._prompt_task
        manager._run_prompt.assert_awaited_once()


@pytest.mark.asyncio
async def test_created_origin_missing_history_cannot_be_reclassified(keyed):
    manager, session, request = keyed
    manager.db.execute_write("DELETE FROM workspace_embodiments")
    with pytest.raises(ValueError, match="history_unproven"):
        await submit(manager, session.session_id, request)
    manager._run_prompt.assert_not_awaited()


@pytest.mark.asyncio
async def test_boolean_is_not_integer_in_stored_receipt(keyed):
    manager, session, request = keyed
    await submit(manager, session.session_id, request)
    await session._prompt_task
    row = manager.db.get_turn(session.session_id, 0)
    saved = json.loads(row["native_admission_json"])
    saved["receipt"]["turn_index"] = False
    manager.db.execute_write("UPDATE turns SET native_admission_json=?", (json.dumps(saved),))
    with pytest.raises(ValueError, match="history_unproven"):
        await submit(manager, session.session_id, request, read_only=True)
    manager._run_prompt.assert_awaited_once()


@pytest.mark.asyncio
async def test_receipt_survives_database_reopen_without_sdk(keyed, tmp_path):
    from agent_bridge.db import Database
    from agent_bridge.native_keyed_admission import stored_receipt
    manager, session, request = keyed
    accepted = await submit(manager, session.session_id, request)
    await session._prompt_task
    reopened = Database(tmp_path / "test.db")
    try:
        assert stored_receipt(reopened, session.session_id, request) == accepted
    finally:
        reopened.close()
    manager._run_prompt.assert_awaited_once()


def test_unique_turn_transaction_serializes_independent_connections(keyed, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from agent_bridge.db import Database
    manager, session, request = keyed
    other = Database(tmp_path / "test.db")

    def admit(db):
        return db.create_turn(session.session_id, 0, request.prompt, 1.0,
                              delivery_id=request.delivery_id(), native_admission=request)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(admit, (manager.db, other)))
        assert sorted(row["inserted"] for row in results) == [False, True]
        assert results[0]["receipt"] == results[1]["receipt"]
        assert len(manager.db.get_turns(session.session_id)) == 1
        manager._run_prompt.assert_not_awaited()
    finally:
        other.close()
