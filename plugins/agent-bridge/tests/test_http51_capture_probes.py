"""Actual standard-send parser/ASGI/journal evidence, not native SDK proof."""

import json
import io
import os
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

import bridge_http51_evidence as evidence
from bridge_http_retained_probes import capture_request, record_observations
from http51_receipt_probes import observe as observe_receipt
from http51_live_cli_probes import observe as observe_live_cli
from test_live_exact_rollback import (
    test_interrupted_pre30_upgrade_reopens_every_legacy_guard_checkpoint
    as test_interrupted_pre30_upgrade_reopens_every_legacy_guard_checkpoint,
)
from agent_bridge import __main__ as core
from agent_bridge.models import SessionStatus
from agent_bridge.protocol import HTTP_PROTOCOL_VERSION
from agent_bridge.client import BridgeClient
from agent_bridge.events import EventLog
from agent_bridge.session_manager import Session
from agent_bridge.transport import SpawnTarget
from test_live_inbox_runtime import (
    installation as installation, serving as serving,
)
from test_native_send_admission import _command, native_send as native_send
from test_native_send_admission import (
    test_concurrent_stopped_retries_reconcile_after_actual_resume_state_race
    as test_concurrent_stopped_retries_reconcile_after_actual_resume_state_race,
    test_failed_resume_without_a_matching_receipt_remains_a_failure
    as test_failed_resume_without_a_matching_receipt_remains_a_failure,
)
from test_session_observations import (
    source as source,
    test_observation_cancellation_preserves_other_subscription
    as test_observation_cancellation_preserves_other_subscription,
    test_observation_http_resource_boundary as test_observation_http_resource_boundary,
)
from test_session_observation_transport import (
    endpoint as endpoint,
    test_observer_cli_http_eof as test_observer_cli_http_eof,
)

ROOT = Path(__file__).resolve().parents[3]


def test_http51_capture_actual_observations(
    serving, native_send, request, monkeypatch, capsys,
) -> None:
    bound = capture_request(evidence, ROOT)
    receipt_client_factory = core._get_client
    assert HTTP_PROTOCOL_VERSION == evidence.GENERATION
    vector = json.loads(evidence.safe(ROOT, evidence.INPUT).read_bytes())
    app, client, db, _ = serving
    manager, session, original = native_send
    app.state.session_manager = manager
    native_send = manager, session, original.model_copy(update={"prompt": vector["requests"]["prompt"]})
    # Keep ordinary middleware/installation binding while allowing an actual HTTP 500 response.
    api = TestClient(app, headers=client.headers, raise_server_exceptions=False)
    observed = {"advertised": {
        key: api.get("/health").json()[key]
        for key in ("protocol_version", "min_protocol_version")
    }}
    args, _ = _command(native_send, monkeypatch, api=api)
    args.func(args)
    receipt = json.loads(capsys.readouterr().out)
    observed["receipt"] = {key: receipt[key] for key in vector["expected"]["receipt"]}
    args.func(args)
    observed["same_retry"] = json.loads(capsys.readouterr().out) == receipt
    path = "/api/v1/sessions/bridge-a/turns/native-bound/send"
    body = native_send[2].model_dump()
    observed["changed_input"] = api.post(path, json={**body, "prompt": "changed"}).status_code
    observed["wrong_native"] = api.post(path, json={
        **body, "expected_native_session_id": "replacement",
    }).status_code
    session.status = SessionStatus.RUNNING
    observed["busy"] = api.post(path, json={**body, "invocation_key": "busy"}).status_code
    old, calls = _command(native_send, monkeypatch, generation=50, api=api)
    with pytest.raises(SystemExit) as refused:
        old.func(old)
    result = json.loads(capsys.readouterr().out)
    observed["old_receiver"] = {
        "exit": refused.value.code, "outcome": result["outcome"], "posts": len(calls),
    }

    session.status = SessionStatus.IDLE
    saved_update = manager.db.update_session_status

    def fail_after_commit(*args, **kwargs):
        raise RuntimeError("synthetic response failure after journal commit")

    lost, _ = _command(native_send, monkeypatch, api=api)
    lost.idempotency_key = "lost-reply"
    monkeypatch.setattr(manager.db, "update_session_status", fail_after_commit)
    with pytest.raises(SystemExit) as unknown:
        lost.func(lost)
    result = json.loads(capsys.readouterr().out)
    observed["post_commit_failure"] = {
        "exit": unknown.value.code,
        **{key: result[key] for key in ("outcome", "reason", "idempotency_key")},
    }
    monkeypatch.setattr(manager.db, "update_session_status", saved_update)
    lost.func(lost)
    observed["post_commit_failure"].update({
        "recovered_turn": json.loads(capsys.readouterr().out)["turn_index"],
        "turns": len(db.get_turns(session.session_id)),
    })
    malformed = [True]
    invalid, _ = _command(
        native_send, monkeypatch, api=api,
        transform=lambda value: {} if malformed[0] else value,
    )
    with pytest.raises(SystemExit) as unknown:
        invalid.func(invalid)
    result = json.loads(capsys.readouterr().out)
    malformed[0] = False
    invalid.func(invalid)
    observed["malformed_receipt"] = {
        "exit": unknown.value.code, "outcome": result["outcome"],
        "recovered_turn": json.loads(capsys.readouterr().out)["turn_index"],
        "turns": len(db.get_turns(session.session_id)),
    }
    event, = [
        event for event in session.event_log.get_events(0) if event.event == "user_message"
    ]
    observed["caller_provenance"] = event.data
    observed["no_inbox"] = db.execute_read("SELECT * FROM live_messages") == []
    observed["receipt_only"] = observe_receipt(
        api, manager, session, body, vector, monkeypatch, capsys,
        client_factory=receipt_client_factory,
    )
    observed["live_cli"] = observe_live_cli(
        api, db, vector, monkeypatch, capsys, client_factory=receipt_client_factory,
    )
    observed["observations"] = observe_sessions(
        api, manager, db, vector, monkeypatch, capsys,
        client_factory=receipt_client_factory,
    )
    api.headers.pop("Authorization")
    observed["unauthenticated"] = api.post(path, json=body).status_code
    api.close()
    assert observed == vector["expected"]
    if bound is not None:
        record_observations(evidence, bound, request.node.callspec.params["installation"], observed)


def observe_sessions(api, manager, db, vector, monkeypatch, capsys, *, client_factory):
    """Execute real root/client/frame parsers against private authenticated ASGI."""
    spec = vector["requests"]["observations"]
    capabilities = (
        "bridge-session-observations-v1", "bridge-session-observation-history-v1",
    )
    session = Session(
        spec["session_id"], "observation-fixture",
        SpawnTarget(
            type="local", cwd=".", project=spec["project"], worktree_id=spec["worktree_id"],
        ), preserve_target=True,
    )
    session.acp_session_id = spec["alias"]
    session.event_log = EventLog(db=db, session_id=session.session_id)
    db.create_session(session.session_id, "observation-fixture", None, ".", "local", "idle", 1.0)
    db.create_session(spec["unadmitted"], "unadmitted-fixture", None, ".", "local", "idle", 1.0)
    manager._sessions[session.session_id] = session
    session.event_log.append("permission_request", {"request_id": "observation-permission"})
    session.event_log.append("permission_resolved", {"request_id": "observation-permission"})
    session.event_log.append("compaction_start", {"current_tokens": 35, "token_limit": 100})
    db.flush()
    before = {
        table: db.execute_read("SELECT * FROM " + table)
        for table in ("sessions", "events", "turns", "pending_prompts", "live_messages")
    }
    observed = {"capabilities": {
        name: api.get("/health").json()["capabilities"][name] for name in capabilities
    }}
    reads = []
    old_capability = [False]

    class Response(io.BytesIO):
        def __init__(self, response):
            payload = response.content
            if old_capability[0] and response.request.url.path == "/health":
                body = response.json()
                for name in capabilities:
                    body["capabilities"].pop(name, None)
                payload = json.dumps(body).encode()
            super().__init__(payload)
            self.status = response.status_code
            self.headers = response.headers

    def open_private(request, **_kwargs):
        selected = urlsplit(request.full_url)
        assert selected.netloc == "testserver"
        assert request.get_method() == "GET"
        if selected.path != "/health":
            reads.append(selected.path)
        response = api.get(
            selected.path + ("?" + selected.query if selected.query else ""),
            headers=dict(request.header_items()),
        )
        assert response.status_code == 200
        return Response(response)

    def configured_client(_cls):
        token = api.headers["Authorization"].split(" ", 1)[1]
        return BridgeClient("http://testserver", token, connect_grace=0)

    def command(argv):
        capsys.readouterr()
        try:
            core.main(list(argv))
            code = 0
        except SystemExit as exc:
            code = exc.code
        captured = capsys.readouterr()
        lines = captured.out.splitlines()
        assert len(lines) == 1
        return code, json.loads(lines[0])

    with monkeypatch.context() as patch:
        factory = Mock(wraps=client_factory)
        ensure = Mock(side_effect=AssertionError("observer ensured a daemon"))
        start = Mock(side_effect=AssertionError("observer started a daemon"))
        actor = AsyncMock(side_effect=AssertionError("observer started an actor"))
        resume = AsyncMock(side_effect=AssertionError("observer resumed an actor"))
        handoff = AsyncMock(side_effect=AssertionError("observer handed off an actor"))
        patch.setattr(core, "_get_client", factory)
        patch.setattr(core, "_ensure_daemon", ensure)
        patch.setattr(core, "_launch_bridge_daemon", start)
        patch.setattr(BridgeClient, "from_config", classmethod(configured_client))
        patch.setattr("urllib.request.urlopen", open_private)
        patch.setattr(manager, "start_session", actor)
        patch.setattr(manager, "resume_session", resume)
        patch.setattr(manager, "handoff_session", handoff)
        try:
            code, snapshot = command(spec["snapshot_command"])
            assert snapshot["source"]["session_id"] == session.session_id
            assert snapshot["source"]["project"] == spec["project"]
            assert snapshot["source"]["worktree_id"] == spec["worktree_id"]
            assert snapshot["source"]["journal_incarnation"] == session.event_log.continuity_id
            compaction = snapshot["entries"][-1]["details"]["compaction_evidence"]["pre_usage"]
            observed["snapshot"] = {
                "exit": code, "kind": snapshot["kind"], "registry": snapshot["source"]["registry"],
                "entry_kinds": [item["kind"] for item in snapshot["entries"]],
                "head_session_id": snapshot["source"]["head_session_id"],
                "current_requests": snapshot["current_requests"],
                **{key: compaction[key] for key in (
                    "used_tokens", "active_capacity_tokens", "qualification",
                )},
            }
            code, abbreviated = command(spec["abbreviated_snapshot_command"])
            observed["abbreviated_snapshot"] = {
                "exit": code, "kind": abbreviated["kind"],
                "same_source": abbreviated["source"] == snapshot["source"],
            }
            code, history = command(spec["history_command"])
            observed["history"] = {
                "exit": code, "kind": history["kind"], "entries": len(history["entries"]),
                "has_more": history["has_more"],
            }
            for name in ("unknown", "alias", "unadmitted"):
                argv = [
                    spec[name] if part == spec["session_id"] else part
                    for part in spec["snapshot_command"]
                ]
                code, refused = command(argv)
                observed[name] = {"exit": code, "code": refused["code"]}
            reads.clear()
            old_capability[0] = True
            code, refused = command(spec["snapshot_command"])
            observed["old_capability"] = {
                "exit": code, "code": refused["code"], "source_reads": len(reads),
            }
            old_capability[0] = False
            query = {
                "contract_version": "1", "project": spec["project"],
                "worktree_id": spec["worktree_id"],
            }
            path = "/api/v1/session-observations/" + spec["session_id"]
            from agent_bridge.session_observation_contract import decode

            for name, changes in (
                ("wrong_project", {"project": "foreign"}),
                ("wrong_worktree", {"worktree_id": "foreign"}),
                ("unproven_head", {"head_session_id": "unproven"}),
            ):
                response = api.get(path, params={**query, **changes})
                body = decode(response.content.split(b"data: ", 1)[1].strip())
                observed[name] = body["code"]
            observed["history_follow"] = api.get(
                "/api/v1/session-observation-history/" + spec["session_id"],
                params={**query, "follow": "true"},
            ).status_code
            observed["unauthenticated"] = api.get(
                path, params=query, headers={"Authorization": ""},
            ).status_code
            assert factory.call_count == 7
            assert all(call.args == () and call.kwargs == {"ensure": False}
                       for call in factory.call_args_list)
            ensure.assert_not_called()
            start.assert_not_called()
            actor.assert_not_awaited()
            resume.assert_not_awaited()
            handoff.assert_not_awaited()
            observed["no_ensure"] = ensure.call_count == start.call_count == 0
            observed["no_actor_start"] = (
                actor.await_count == resume.await_count == handoff.await_count == 0
            )
        finally:
            core._set_project_override(None)
    after = {table: db.execute_read("SELECT * FROM " + table) for table in before}
    observed["read_only"] = after == before and session.subscriber_count == 0
    return observed


def test_http51_observation_boundaries(serving, native_send, monkeypatch, capsys):
    """Both source installation modes execute the owning passive read path."""
    app, api, db, _ = serving
    manager, _session, _request = native_send
    app.state.session_manager = manager
    vector = json.loads(evidence.safe(ROOT, evidence.INPUT).read_bytes())
    assert observe_sessions(
        api, manager, db, vector, monkeypatch, capsys, client_factory=core._get_client,
    ) == vector["expected"]["observations"]


@pytest.mark.parametrize("mode", ["legacy", "namespaced"])
def test_http51_observer_payload_abbreviations(tmp_path, mode):
    """Run the actual payload gate with inert resolution/publication/install spies."""
    from test_payload_invocation import test_passive_observer_attributed_payload_never_provisions

    shell = (
        "powershell" if shutil.which("powershell") else "pwsh"
    ) if os.name == "nt" else "bash"
    assert shutil.which(shell), "the capture requires its native fixture shell"
    vector = json.loads(evidence.safe(ROOT, evidence.INPUT).read_bytes())
    for index, argv in enumerate(vector["requests"]["observations"]["payload_commands"]):
        test_passive_observer_attributed_payload_never_provisions(
            tmp_path / str(index), shell, mode, argv,
        )
