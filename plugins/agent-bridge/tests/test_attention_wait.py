"""Attention-boundary position and evaluator tests."""

from __future__ import annotations

import asyncio
import time
from threading import Thread
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from agent_bridge import __main__ as cli
from agent_bridge.acp_client import AcpClient
from agent_bridge.app import create_app
from agent_bridge.attention_wait import (
    AttentionHistoryChangedError,
    AttentionTokenError,
    evaluate_owned_attention,
)
from agent_bridge.events import EventLog
from agent_bridge.client import BridgeConnectionError
from agent_bridge.models import AttentionReason, ServiceConfig, SessionStatus
from agent_bridge.protocol import ATTENTION_WAIT_PROTOCOL_VERSION
from agent_bridge.session_manager import Session, SessionManager
from agent_bridge.transport import SpawnTarget


def _session(tmp_db, session_id: str = "sess-1") -> Session:
    target = SpawnTarget(type="local", cwd="/wt", worktree_id="wt-1")
    session = Session(session_id, "calm-lake", target, "test-agent")
    session.status = SessionStatus.RUNNING
    tmp_db.create_session(
        session_id,
        "calm-lake",
        "test-agent",
        "/wt",
        "local",
        "running",
        time.time(),
    )
    session.event_log = EventLog(
        db=tmp_db, session_id=session_id, worktree_id="wt-1"
    )
    return session


@pytest.fixture
def app(tmp_path):
    cfg = ServiceConfig(port=0, bind="127.0.0.1", db_path=str(tmp_path / "test.db"))
    return create_app(config=cfg, token="test-token")


@pytest.fixture
def client(app):
    with TestClient(app) as value:
        value.headers["Authorization"] = "Bearer test-token"
        yield value


def _app_session(app, session_id: str = "sess-1") -> Session:
    mgr: SessionManager = app.state.session_manager
    session = Session(
        session_id,
        "calm-lake",
        SpawnTarget(type="local", cwd="/wt", worktree_id="wt-1"),
        "test-agent",
    )
    session.status = SessionStatus.RUNNING
    mgr.db.create_session(
        session_id,
        "calm-lake",
        "test-agent",
        "/wt",
        "local",
        "running",
        time.time(),
    )
    session.event_log = EventLog(
        db=mgr.db, session_id=session_id, worktree_id="wt-1"
    )
    mgr._sessions[session_id] = session
    return session


def _evaluate(tmp_db, session, reasons, position=None):
    return evaluate_owned_attention(
        db=tmp_db,
        session=session,
        requested_ref="wt-1",
        reasons=reasons,
        position=position,
    )


def test_earliest_selected_boundary_is_deterministic(tmp_db) -> None:
    session = _session(tmp_db)
    session.event_log.append(
        "ask_user_request",
        {"tool_call_id": "ask-1", "message": "Choose"},
    )
    session.event_log.append("turn_complete", {"stop_reason": "end_turn"})

    first = _evaluate(
        tmp_db,
        session,
        [AttentionReason.INPUT_REQUIRED, AttentionReason.TURN_COMPLETE],
    )
    replay = _evaluate(
        tmp_db,
        session,
        [AttentionReason.INPUT_REQUIRED, AttentionReason.TURN_COMPLETE],
    )

    assert first.settled is True
    assert first.reason == AttentionReason.INPUT_REQUIRED
    assert first.boundary_event_id == 1
    assert first.position == replay.position
    assert first.reference is not None
    assert first.reference.kind == "input"
    assert first.reference.availability == "unknown_after_restart"


def test_selected_reason_filter_advances_to_matching_boundary(tmp_db) -> None:
    session = _session(tmp_db)
    session.event_log.append(
        "ask_user_request",
        {"tool_call_id": "ask-1", "message": "Choose"},
    )
    session.event_log.append("turn_complete", {"stop_reason": "end_turn"})

    result = _evaluate(tmp_db, session, [AttentionReason.TURN_COMPLETE])

    assert result.reason == AttentionReason.TURN_COMPLETE
    assert result.boundary_event_id == 2


def test_position_resumes_after_exact_boundary_without_touching_cursor(tmp_db) -> None:
    session = _session(tmp_db)
    session.event_log.append("turn_complete", {"stop_reason": "end_turn"})
    first = _evaluate(tmp_db, session, [AttentionReason.TURN_COMPLETE])
    tmp_db.set_cursor("caller-a", session.session_id, 1, time.time())
    session.event_log.append("turn_complete", {"stop_reason": "end_turn"})

    second = _evaluate(
        tmp_db,
        session,
        [AttentionReason.TURN_COMPLETE],
        position=first.position,
    )

    assert second.boundary_event_id == 2
    assert tmp_db.get_cursor("caller-a", session.session_id) == 1
    assert tmp_db.get_cursor("caller-b", session.session_id) == 0


def test_position_is_bound_to_delegate_and_continuity(tmp_db) -> None:
    session = _session(tmp_db)
    session.event_log.append("turn_complete", {"stop_reason": "end_turn"})
    first = _evaluate(tmp_db, session, [AttentionReason.TURN_COMPLETE])

    other = _session(tmp_db, "sess-2")
    other.target.worktree_id = "wt-2"
    other.event_log.append("turn_complete", {"stop_reason": "end_turn"})
    with pytest.raises(AttentionTokenError, match="different delegate"):
        _evaluate(
            tmp_db,
            other,
            [AttentionReason.TURN_COMPLETE],
            position=first.position,
        )

    session.event_log.rebuild(
        [("turn_complete", {"stop_reason": "end_turn"})]
    )
    with pytest.raises(AttentionHistoryChangedError, match="history was replaced"):
        _evaluate(
            tmp_db,
            session,
            [AttentionReason.TURN_COMPLETE],
            position=first.position,
        )


def test_evaluation_does_not_mix_rebuilt_history(tmp_db) -> None:
    session = _session(tmp_db)
    session.event_log.append("agent_message", {"text": "old history"})
    snapshot_history = session.event_log.snapshot_history

    def snapshot_then_rebuild(*, durable=False):
        snapshot = snapshot_history(durable=durable)
        session.event_log.rebuild(
            [("turn_complete", {"stop_reason": "end_turn"})]
        )
        return snapshot

    session.event_log.snapshot_history = snapshot_then_rebuild

    result = _evaluate(tmp_db, session, [AttentionReason.TURN_COMPLETE])

    assert result.settled is False
    with pytest.raises(AttentionHistoryChangedError, match="history was replaced"):
        _evaluate(
            tmp_db,
            session,
            [AttentionReason.TURN_COMPLETE],
            position=result.position,
        )


def test_interrupted_is_not_claimed_as_explicit_cancellation(tmp_db) -> None:
    session = _session(tmp_db)
    session.event_log.append("turn_complete", {"stop_reason": "interrupted"})

    result = _evaluate(
        tmp_db,
        session,
        [AttentionReason.TURN_CANCELLED, AttentionReason.TURN_COMPLETE],
    )

    assert result.settled is False


@pytest.mark.parametrize(
    ("event_type", "data", "reason"),
    [
        ("turn_complete", {"stop_reason": "end_turn"}, AttentionReason.TURN_COMPLETE),
        (
            "turn_complete",
            {"stop_reason": "cancelled"},
            AttentionReason.TURN_CANCELLED,
        ),
        (
            "session_state_changed",
            {"status": "failed"},
            AttentionReason.FAILED,
        ),
        (
            "ask_user_request",
            {"tool_call_id": "ask-1"},
            AttentionReason.INPUT_REQUIRED,
        ),
        (
            "permission_request",
            {"request_id": "permission-1"},
            AttentionReason.PERMISSION_REQUIRED,
        ),
        ("terminal_unreachable", {}, AttentionReason.UNREACHABLE),
        (
            "policy_required",
            {"action_id": "policy-1"},
            AttentionReason.POLICY_REQUIRED,
        ),
        (
            "session_state_changed",
            {"status": "stopped"},
            AttentionReason.STOPPED,
        ),
    ],
)
def test_authoritative_reason_events_settle(
    tmp_db, event_type, data, reason
) -> None:
    session = _session(tmp_db)
    session.event_log.append(event_type, data)

    result = _evaluate(tmp_db, session, [reason])

    assert result.settled is True
    assert result.reason == reason
    assert result.boundary_event_id == 1
    assert result.position is not None
    assert result.reference is not None


def test_handoff_precedes_later_predecessor_stop(tmp_db) -> None:
    session = _session(tmp_db)
    successor = _session(tmp_db, "sess-2")
    tmp_db.link_succession(session.session_id, successor.session_id, time.time())
    session.event_log.append(
        "session_handoff",
        {"rolled_from": session.session_id, "rolled_to": successor.session_id},
    )
    session.event_log.append(
        "session_state_changed", {"status": SessionStatus.STOPPED.value}
    )

    result = _evaluate(tmp_db, session, [AttentionReason.STOPPED])

    assert result.settled is False
    assert result.identity.current_session_id == successor.session_id
    assert result.identity.successor_id == successor.session_id
    assert any("successor compatibility" in item for item in result.limitations)

    replay = _evaluate(
        tmp_db,
        session,
        [AttentionReason.STOPPED],
        position=result.position,
    )
    assert replay.settled is False


def test_succession_link_is_ignored_until_handoff_event(tmp_db) -> None:
    session = _session(tmp_db)
    successor = _session(tmp_db, "sess-2")
    tmp_db.link_succession(session.session_id, successor.session_id, time.time())
    session.event_log.append(
        "session_state_changed", {"status": SessionStatus.STOPPED.value}
    )

    result = _evaluate(tmp_db, session, [AttentionReason.STOPPED])

    assert result.settled is True
    assert result.reason == AttentionReason.STOPPED
    assert result.identity.current_session_id == session.session_id
    assert result.identity.successor_id is None


def test_gated_reasons_require_authoritative_events(tmp_db) -> None:
    session = _session(tmp_db)
    session.event_log.append("error", {"message": "recoverable"})
    session.event_log.append("context_critical", {"pct": 75})
    session.event_log.append("permission_request", {"title": "Run tool"})

    result = _evaluate(
        tmp_db,
        session,
        [
            AttentionReason.FAILED,
            AttentionReason.UNREACHABLE,
            AttentionReason.POLICY_REQUIRED,
            AttentionReason.PERMISSION_REQUIRED,
        ],
    )

    assert result.settled is False
    assert len(result.limitations) == 3


def test_empty_reason_selection_is_rejected(tmp_db) -> None:
    session = _session(tmp_db)

    with pytest.raises(AttentionTokenError, match="at least one"):
        _evaluate(tmp_db, session, [])


def test_attention_route_returns_existing_boundary_without_cursor_move(
    client, app
) -> None:
    session = _app_session(app)
    session.event_log.append("turn_complete", {"stop_reason": "end_turn"})
    app.state.session_manager.db.set_cursor("caller-a", "sess-1", 1, time.time())

    response = client.get(
        "/api/v1/sessions/wt-1/attention",
        params={"reason": "turn_complete", "timeout_seconds": 0},
    )

    assert response.status_code == 200
    assert response.json()["reason"] == "turn_complete"
    assert app.state.session_manager.db.get_cursor("caller-a", "sess-1") == 1


def test_attention_route_long_polls_until_selected_boundary(client, app) -> None:
    session = _app_session(app)

    def append_boundary() -> None:
        time.sleep(0.05)
        session.event_log.append("turn_complete", {"stop_reason": "end_turn"})

    thread = Thread(target=append_boundary)
    thread.start()
    response = client.get(
        "/api/v1/sessions/sess-1/attention",
        params={"reason": "turn_complete", "timeout_seconds": 1},
    )
    thread.join()

    assert response.status_code == 200
    assert response.json()["settled"] is True
    assert response.json()["boundary_event_id"] == 1


def test_attention_route_timeout_is_unsettled(client, app) -> None:
    _app_session(app)

    response = client.get(
        "/api/v1/sessions/sess-1/attention",
        params={"reason": "turn_complete", "timeout_seconds": 0},
    )

    assert response.status_code == 200
    assert response.json()["settled"] is False
    assert response.json()["reason"] is None


def test_attention_route_resumes_predecessor_position_across_handoff(
    client, app
) -> None:
    predecessor = _app_session(app)
    predecessor.event_log.append("agent_message", {"text": "working"})
    initial = client.get(
        "/api/v1/sessions/wt-1/attention",
        params={"reason": "input_required", "timeout_seconds": 0},
    ).json()
    successor = _app_session(app, "sess-2")
    app.state.session_manager.db.link_succession(
        predecessor.session_id, successor.session_id, time.time()
    )
    predecessor.event_log.append(
        "session_handoff",
        {
            "rolled_from": predecessor.session_id,
            "rolled_to": successor.session_id,
        },
    )

    response = client.get(
        "/api/v1/sessions/wt-1/attention",
        params={
            "reason": "input_required",
            "position": initial["position"],
            "timeout_seconds": 0,
        },
    )

    assert response.status_code == 200
    assert response.json()["settled"] is False
    assert response.json()["identity"]["observed_session_id"] == "sess-1"
    assert response.json()["identity"]["successor_id"] == "sess-2"


@pytest.mark.asyncio
async def test_permission_request_has_live_correlation_and_resolution() -> None:
    events: list[tuple[str, dict]] = []
    acp: AcpClient

    def record(event: str, data: dict) -> None:
        if event == "permission_request":
            assert acp.has_pending_permission(data["request_id"])
        events.append((event, data))

    acp = AcpClient(on_event=record)
    acp.auto_approve = False
    option = SimpleNamespace(
        option_id="allow-once", name="Allow once", kind="allow_once"
    )
    tool_call = SimpleNamespace(title="Run command")

    task = asyncio.create_task(
        acp._handle_permission_request([option], tool_call)
    )
    await asyncio.sleep(0)
    request = next(data for event, data in events if event == "permission_request")
    request_id = request["request_id"]

    assert acp.has_pending_permission(request_id)
    assert acp.resolve_permission(request_id, "allow-once")
    response = await task

    assert response.outcome.option_id == "allow-once"
    resolved = next(data for event, data in events if event == "permission_resolved")
    assert resolved["request_id"] == request_id
    assert not acp.has_pending_permission(request_id)


def test_permission_attention_is_actionable(tmp_db) -> None:
    session = _session(tmp_db)
    session.event_log.append(
        "permission_request",
        {
            "request_id": "permission-1",
            "title": "Run command",
            "options": [
                {
                    "optionId": "allow-once",
                    "name": "Allow once",
                    "kind": "allow_once",
                }
            ],
        },
    )

    result = _evaluate(
        tmp_db, session, [AttentionReason.PERMISSION_REQUIRED]
    )

    assert result.settled is True
    assert result.reference is not None
    assert result.reference.value == {
        "request_id": "permission-1",
        "options": [
            {
                "option_id": "allow-once",
                "name": "Allow once",
                "kind": "allow_once",
            }
        ],
    }


def test_stream_attention_settles_only_after_render_and_ack() -> None:
    order: list[str] = []

    class Client:
        def get_cursor(self, session_id, caller_id=None):
            return 0

        def wait_for_attention(
            self, session_id, *, reasons, position=None, timeout_seconds=0
        ):
            order.append("probe")
            if "ack" not in order:
                return {
                    "settled": False,
                    "identity": {"current_session_id": session_id},
                }
            return {
                "settled": True,
                "reason": "input_required",
                "boundary_event_id": 1,
                "identity": {"current_session_id": session_id},
                "position": "aba1.position",
            }

        def stream_events(self, session_id, after=0, caller_id=None):
            yield {
                "id": "1",
                "event": "ask_user_request",
                "data": {"tool_call_id": "ask-1"},
            }

        def ack_cursor(self, session_id, up_to, caller_id=None):
            order.append("ack")
            return up_to

    renderer = MagicMock()
    renderer.render_event.side_effect = lambda event, data: (
        order.append("render") or "question\n"
    )

    result = cli._stream_feed(
        Client(),
        "sess-1",
        caller_id="caller",
        renderer=renderer,
        attention_reasons=["input_required"],
    )

    assert isinstance(result, dict)
    assert result["reason"] == "input_required"
    assert order.index("render") < order.index("ack") < len(order) - 1


@pytest.mark.parametrize("reason", list(AttentionReason))
def test_human_attention_render_identifies_each_reason(reason) -> None:
    rendered = cli._render_attention_result(
        {
            "settled": True,
            "reason": reason.value,
            "identity": {"current_session_id": "sess-1"},
            "reference": {"availability": "available"},
        }
    )

    assert reason.value in rendered
    assert "sess-1" in rendered


def test_json_attention_wait_does_not_open_delivery_stream(
    monkeypatch, capsys
) -> None:
    client = MagicMock()
    client.daemon_supports.return_value = True
    client.wait_for_attention.return_value = {
        "settled": True,
        "reason": "turn_complete",
        "boundary_event_id": 4,
        "identity": {
            "observed_session_id": "sess-1",
            "current_session_id": "sess-1",
        },
        "position": "aba1.position",
        "limitations": [],
    }
    args = SimpleNamespace(
        session_id="sess-1",
        attention=["turn_complete"],
        all_attention=False,
        position=None,
        json=True,
        caller=None,
        expand=None,
        no_color=True,
    )
    monkeypatch.setattr(cli, "_get_client", lambda: client)
    monkeypatch.setattr(cli, "_caller_id_for", lambda _args: "caller")
    monkeypatch.setattr(
        cli,
        "_phased_timeouts",
        lambda: SimpleNamespace(command=1.0),
    )

    cli._cmd_wait(args)

    assert '"reason": "turn_complete"' in capsys.readouterr().out
    client.stream_events.assert_not_called()
    client.ack_cursor.assert_not_called()


def test_json_attention_wait_follows_compatible_successor(
    monkeypatch, capsys
) -> None:
    client = MagicMock()
    client.daemon_supports.return_value = True
    client.daemon_protocol.return_value = (ATTENTION_WAIT_PROTOCOL_VERSION, 1)
    client.wait_for_attention.side_effect = [
        {
            "settled": False,
            "identity": {
                "observed_session_id": "sess-1",
                "current_session_id": "sess-2",
                "successor_id": "sess-2",
            },
            "position": "aba1.predecessor",
            "limitations": [],
        },
        {
            "settled": True,
            "reason": "input_required",
            "boundary_event_id": 1,
            "identity": {
                "observed_session_id": "sess-2",
                "current_session_id": "sess-2",
                "successor_id": None,
            },
            "position": "aba1.successor",
            "limitations": [],
        },
    ]
    args = SimpleNamespace(
        session_id="sess-1",
        attention=["input_required"],
        all_attention=False,
        position="aba1.start",
        json=True,
        caller=None,
        expand=None,
        no_color=True,
    )
    monkeypatch.setattr(cli, "_phased_timeouts", lambda: SimpleNamespace(command=1.0))

    cli._cmd_attention_wait(client, args, "caller", ["input_required"])

    calls = client.wait_for_attention.call_args_list
    assert calls[0].args[0] == "sess-1"
    assert calls[0].kwargs["position"] == "aba1.start"
    assert calls[1].args[0] == "sess-2"
    assert calls[1].kwargs["position"] is None
    assert '"reason": "input_required"' in capsys.readouterr().out


def test_json_attention_wait_reports_explicit_successor_incompatibility(
    monkeypatch, capsys
) -> None:
    client = MagicMock()
    client.daemon_supports.return_value = True
    client.daemon_protocol.return_value = (ATTENTION_WAIT_PROTOCOL_VERSION - 1, 1)
    client.wait_for_attention.return_value = {
        "settled": False,
        "identity": {
            "observed_session_id": "sess-1",
            "current_session_id": "sess-2",
            "successor_id": "sess-2",
        },
        "position": "aba1.predecessor",
        "limitations": [],
    }
    args = SimpleNamespace(
        session_id="sess-1",
        attention=["input_required"],
        all_attention=False,
        position=None,
        json=True,
        caller=None,
        expand=None,
        no_color=True,
    )
    monkeypatch.setattr(cli, "_phased_timeouts", lambda: SimpleNamespace(command=1.0))

    cli._cmd_attention_wait(client, args, "caller", ["input_required"])

    output = capsys.readouterr().out
    assert '"reason": "contract_changed"' in output
    assert '"successor_id": "sess-2"' in output


def test_json_attention_wait_exhausts_finite_command_deadline(
    monkeypatch, capsys
) -> None:
    client = MagicMock()
    client.daemon_supports.return_value = True
    client.wait_for_attention.return_value = {
        "settled": False,
        "reason": None,
        "identity": {
            "observed_session_id": "sess-1",
            "current_session_id": "sess-1",
            "successor_id": None,
        },
        "position": "aba1.position",
        "limitations": [],
    }
    args = SimpleNamespace(
        session_id="sess-1",
        attention=["input_required"],
        all_attention=False,
        position=None,
        json=True,
        caller=None,
        expand=None,
        no_color=True,
    )
    ticks = iter([0.0, 0.0, 0.4, 0.8, 1.1])
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(cli, "_phased_timeouts", lambda: SimpleNamespace(command=1.0))

    cli._cmd_attention_wait(client, args, "caller", ["input_required"])

    assert client.wait_for_attention.call_count == 3
    assert [
        call.kwargs["timeout_seconds"]
        for call in client.wait_for_attention.call_args_list
    ] == pytest.approx([1.0, 0.6, 0.2])
    assert '"settled": false' in capsys.readouterr().out


def test_json_attention_wait_retries_connection_loss_from_same_position(
    monkeypatch, capsys
) -> None:
    client = MagicMock()
    client.daemon_supports.return_value = True
    client.wait_for_attention.side_effect = [
        BridgeConnectionError("daemon restarting"),
        {
            "settled": True,
            "reason": "input_required",
            "boundary_event_id": 2,
            "identity": {
                "observed_session_id": "sess-1",
                "current_session_id": "sess-1",
                "successor_id": None,
            },
            "position": "aba1.next",
            "limitations": [],
        },
    ]
    args = SimpleNamespace(
        session_id="sess-1",
        attention=["input_required"],
        all_attention=False,
        position="aba1.start",
        json=True,
        caller=None,
        expand=None,
        no_color=True,
    )
    monkeypatch.setattr(cli, "_RECONNECT_BACKOFF", 0)
    monkeypatch.setattr(cli, "_phased_timeouts", lambda: SimpleNamespace(command=1.0))

    cli._cmd_attention_wait(client, args, "caller", ["input_required"])

    assert client.refresh_endpoint.call_count == 1
    assert [
        call.kwargs["position"]
        for call in client.wait_for_attention.call_args_list
    ] == ["aba1.start", "aba1.start"]
    assert '"reason": "input_required"' in capsys.readouterr().out
