"""Actual callback/Future evidence without an SDK actor or retained history."""

from __future__ import annotations

import asyncio
import gc
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from acp.schema import ElicitationFormSessionMode, ElicitationSchema, PermissionOption, ToolCallUpdate

from agent_bridge.acp_client import AcpClient
from agent_bridge.acp_callbacks import _BridgeClientImpl
from agent_bridge.acp_current_requests import MAX_CANDIDATES, MAX_REQUESTS
from agent_bridge.session_observation_contract import ObservationError


def client() -> AcpClient:
    result = AcpClient()
    result._connection = object()
    result._process = SimpleNamespace(
        returncode=None,
        stdin=SimpleNamespace(is_closing=lambda: False),
        stdout=SimpleNamespace(at_eof=lambda: False),
    )
    result._acp_session_id = "synthetic-session"
    result.current_requests.bind(_BridgeClientImpl(result), result._connection, result._process)
    result.current_requests.session_changed(result.acp_session_id)
    result.auto_approve = False
    return result


def mode(tool: str = "tool-1") -> ElicitationFormSessionMode:
    return ElicitationFormSessionMode(
        session_id="synthetic-session",
        tool_call_id=tool,
        requested_schema=ElicitationSchema.model_validate(
            {"type": "object", "properties": {"choice": {"type": "string"}}}
        ),
    )


def state(owner: AcpClient) -> dict:
    return owner.current_requests.snapshot(owner).as_dict()


async def ask(owner: AcpClient, tool: str = "tool-1") -> asyncio.Task:
    task = asyncio.create_task(
        owner.current_requests._producer.create_elicitation("Private question", mode(tool))
    )
    await asyncio.sleep(0)
    return task


async def permission(owner: AcpClient) -> asyncio.Task:
    task = asyncio.create_task(
        owner.current_requests._producer.request_permission(
            [PermissionOption(option_id="allow", name="Private option", kind="allow_once")],
            session_id="synthetic-session",
            tool_call=ToolCallUpdate(tool_call_id="permission-tool", title="Private title"),
        )
    )
    await asyncio.sleep(0)
    return task


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["accept", "decline", "cancel", "withdraw", "future-cancel"])
async def test_actual_elicitation_lifetime_is_independent_of_history(action: str) -> None:
    owner = client()
    before = state(owner)
    task = await ask(owner)
    pending = state(owner)
    assert pending["availability"] == "observed" and pending["completeness"] == "complete"
    assert pending["revision"] > before["revision"]
    assert pending["requests"][0]["kind"] == "input"
    raw = json.dumps(pending)
    for private in ("Private question", "choice", "tool-1", "synthetic-session"):
        assert private not in raw
    assert state(owner)["revision"] == pending["revision"]
    if action == "withdraw":
        owner._withdraw_elicitation("tool-1")
    elif action == "future-cancel":
        task.cancel()
    else:
        assert owner.resolve_elicitation("tool-1", {"choice": "answer"}, action=action)
    cleared = state(owner)
    assert cleared["requests"] == []
    assert cleared["revision"] > pending["revision"]
    await asyncio.gather(task, return_exceptions=True)
    assert state(owner)["requests"] == []


@pytest.mark.asyncio
async def test_reask_gets_a_new_view_only_occurrence_without_reusing_answer_selectors() -> None:
    owner = client()
    first = await ask(owner)
    old = state(owner)["requests"][0]["occurrence_id"]
    second = await ask(owner)
    new = state(owner)["requests"][0]["occurrence_id"]
    assert old != new and old != "tool-1" and new != "tool-1"
    assert not owner.resolve_elicitation(old, {})
    assert not owner.resolve_elicitation(new, {})
    await first
    assert state(owner)["requests"][0]["occurrence_id"] == new
    owner.resolve_elicitation("tool-1", {}, action="cancel")
    await second


@pytest.mark.asyncio
async def test_actual_manual_permission_waiter_clears_before_handler_resumes() -> None:
    owner = client()
    task = await permission(owner)
    pending = state(owner)
    actual_id = owner._pending_permission_id
    occurrence = pending["requests"][0]
    assert occurrence["kind"] == "permission" and occurrence["occurrence_id"] != actual_id
    assert not owner.resolve_permission(occurrence["occurrence_id"], "allow")
    assert actual_id not in json.dumps(pending)
    assert owner.resolve_permission(actual_id, "allow")
    assert not task.done()
    assert state(owner)["requests"] == []
    assert state(owner)["revision"] > pending["revision"]
    await task


@pytest.mark.asyncio
@pytest.mark.parametrize("forwarded", [False, True])
async def test_auto_and_forwarded_permissions_are_not_local_waiters(forwarded: bool) -> None:
    owner = client()
    if forwarded:
        async def forward(*_args):
            assert state(owner)["requests"] == []
            return object()
        owner._on_permission = forward
    else:
        owner.auto_approve = True
    task = await permission(owner)
    await task
    assert state(owner)["requests"] == []
    assert state(owner)["completeness"] == "complete"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "loss",
    ["connection", "process", "replaced-connection", "replaced-process",
     "exited", "session", "remote", "host",
     "stdin", "stdout", "closing", "eof", "lost-error", "lost-event"],
)
async def test_qualification_loss_with_a_live_waiter_is_unknown_not_cleared(loss: str) -> None:
    owner = client()
    task = await ask(owner)
    pending = state(owner)
    if loss == "connection":
        owner._connection = None
    elif loss == "process":
        owner._process = None
    elif loss == "replaced-connection":
        owner._connection = object()
    elif loss == "replaced-process":
        owner._process = SimpleNamespace(
            returncode=None, stdin=owner._process.stdin, stdout=owner._process.stdout,
        )
    elif loss == "exited":
        owner._process.returncode = 1
    elif loss == "session":
        owner._acp_session_id = None
    elif loss == "remote":
        owner._remote_transport = object()
    elif loss == "host":
        owner._host_mode = True
    elif loss == "stdin":
        owner._process.stdin = None
    elif loss == "stdout":
        owner._process.stdout = None
    elif loss == "closing":
        owner._process.stdin.is_closing = lambda: True
    elif loss == "eof":
        owner._process.stdout.at_eof = lambda: True
    elif loss == "lost-error":
        owner._connection_loss_error = "transport lost"
    else:
        owner._transport_lost_event.set()
    unknown = state(owner)
    assert unknown["availability"] == unknown["completeness"] == "unknown"
    assert unknown["requests"] == [] and unknown["revision"] > pending["revision"]
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_shutdown_and_buffer_reset_cannot_leave_observed_pending_permission() -> None:
    owner = client()
    task = await permission(owner)
    assert state(owner)["requests"]
    owner._reset_buffers()
    assert state(owner)["requests"] == []
    assert state(owner)["completeness"] == "partial"
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    question = await ask(owner)
    owner._connection = None
    owner._process = None
    await owner.shutdown()
    await question
    assert state(owner)["availability"] == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("loss", ["overlap", "reset"])
async def test_permission_slot_loss_never_proves_complete_absence(loss: str) -> None:
    owner = client()
    first = await permission(owner)
    if loss == "overlap":
        second = await permission(owner)
    else:
        owner._reset_buffers()
        second = None
    missing = state(owner)
    assert not first.done()
    assert missing["availability"] == "observed"
    assert missing["completeness"] == "partial" and missing["reason"] == "waiter-coverage"
    assert len(missing["requests"]) == (1 if second is not None else 0)
    if second is not None:
        owner.resolve_permission(owner._pending_permission_id, "allow")
        assert state(owner)["completeness"] == "partial"
        await second
        assert not first.done()
        assert state(owner)["requests"] == []
        assert state(owner)["completeness"] == "partial"
    first.cancel()
    await asyncio.gather(first, return_exceptions=True)
    await asyncio.sleep(0)
    settled = state(owner)
    assert settled["availability"] == "observed"
    assert settled["completeness"] == "complete" and settled["requests"] == []


@pytest.mark.asyncio
async def test_independent_clients_and_reconnection_retain_correct_identity() -> None:
    owner = client()
    task = await ask(owner)
    pending = state(owner)
    connection = owner._connection
    owner._connection = None
    assert state(owner)["availability"] == "unknown"
    owner._connection = connection
    restored = state(owner)
    assert restored["client_incarnation"] == pending["client_incarnation"]
    assert restored["requests"] == pending["requests"]
    assert restored["revision"] > pending["revision"]
    assert state(client())["client_incarnation"] != restored["client_incarnation"]
    owner.resolve_elicitation("tool-1", {}, action="cancel")
    await task


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["input", "permission"])
async def test_real_connection_replacement_never_requalifies_old_callbacks(
    monkeypatch, kind: str,
) -> None:
    owner = client()
    callbacks = []

    def connect(callback, *_args):
        callbacks.append(callback)
        return SimpleNamespace(initialize=AsyncMock())

    monkeypatch.setattr("agent_bridge.acp_client.ClientSideConnection", connect)
    await owner._init_connection(owner._process.stdin, owner._process.stdout)
    assert state(owner)["availability"] == "unknown"
    owner.adopt_session("synthetic-session")
    task = await (ask(owner) if kind == "input" else permission(owner))
    pending = state(owner)
    await owner._init_connection(owner._process.stdin, owner._process.stdout)
    assert state(owner)["availability"] == "unknown"
    owner.adopt_session("synthetic-session")
    replaced = state(owner)
    assert replaced["client_incarnation"] != pending["client_incarnation"]
    assert replaced["availability"] == "unknown" and replaced["requests"] == []
    assert replaced["reason"] == "producer-replaced"
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert state(owner)["availability"] == "observed"
    # A late callback still belongs to the old connection, not the current owner.
    if kind == "input":
        late = asyncio.create_task(callbacks[0].create_elicitation("Private", mode()))
    else:
        late = asyncio.create_task(callbacks[0].request_permission(
            [PermissionOption(option_id="allow", name="Private", kind="allow_once")],
            session_id="synthetic-session", tool_call=ToolCallUpdate(tool_call_id="late"),
        ))
    await asyncio.sleep(0)
    assert state(owner)["availability"] == "unknown"
    late.cancel()
    await asyncio.gather(late, return_exceptions=True)
    fresh = await (ask(owner) if kind == "input" else permission(owner))
    current = state(owner)
    assert current["availability"] == "observed"
    assert current["requests"][0]["occurrence_id"] != pending["requests"][0]["occurrence_id"]
    fresh.cancel()
    await asyncio.gather(fresh, return_exceptions=True)


@pytest.mark.asyncio
async def test_failed_initialization_and_session_change_withdraw_qualification(monkeypatch) -> None:
    owner = client()
    task = await ask(owner)
    assert state(owner)["requests"]
    owner.adopt_session("different-session")
    assert state(owner)["availability"] == "unknown"
    assert state(owner)["requests"] == []
    owner.adopt_session("synthetic-session")
    assert state(owner)["availability"] == "unknown"
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    task = await ask(owner)
    assert state(owner)["availability"] == "observed"

    async def fail_initialize(**_kwargs):
        assert state(owner)["availability"] == "unknown"
        raise RuntimeError("synthetic initialize failure")

    monkeypatch.setattr(
        "agent_bridge.acp_client.ClientSideConnection",
        lambda *_args: SimpleNamespace(initialize=fail_initialize),
    )
    with pytest.raises(RuntimeError, match="synthetic initialize failure"):
        await owner._init_connection(owner._process.stdin, owner._process.stdout)
    assert state(owner)["availability"] == "unknown"
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_scan_and_result_limits_are_partial_without_unbounded_iteration() -> None:
    owner = client()
    tasks = [await ask(owner, f"tool-{i}") for i in range(MAX_CANDIDATES + 10)]
    class Counted(dict):
        visited = 0

        def values(self):
            for value in super().values():
                self.visited += 1
                assert self.visited <= MAX_CANDIDATES
                yield value

    owner._pending_elicitations = Counted(owner._pending_elicitations)
    bounded = state(owner)
    assert bounded["completeness"] == "partial" and bounded["reason"] == "waiter-budget"
    assert len(bounded["requests"]) == MAX_REQUESTS
    assert len(json.dumps(bounded).encode()) < 8 * 1024
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_done_candidates_consume_scan_budget_without_proving_absence() -> None:
    owner = client()
    for index in range(MAX_CANDIDATES):
        future = asyncio.get_running_loop().create_future()
        future.set_result(None)
        owner._pending_elicitations[f"done-{index}"] = future
    task = await ask(owner, "unvisited")
    snapshot = state(owner)
    assert snapshot["requests"] == [] and snapshot["completeness"] == "partial"
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_exact_result_boundary_and_permission_share_the_same_budget() -> None:
    owner = client()
    permit = await permission(owner)
    questions = [await ask(owner, f"tool-{i}") for i in range(MAX_REQUESTS - 1)]
    complete = state(owner)
    assert len(complete["requests"]) == MAX_REQUESTS
    assert complete["completeness"] == "complete"
    assert sum(item["kind"] == "permission" for item in complete["requests"]) == 1
    extra = await ask(owner, "overflow")
    partial = state(owner)
    assert len(partial["requests"]) == MAX_REQUESTS
    assert partial["completeness"] == "partial"
    for task in [permit, *questions, extra]:
        task.cancel()
    await asyncio.gather(permit, *questions, extra, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["untracked", "wrong-kind", "not-future"])
async def test_malformed_producer_waiters_never_masquerade_as_empty_complete(bad: str) -> None:
    owner = client()
    future = asyncio.get_running_loop().create_future()
    if bad == "wrong-kind":
        owner.current_requests.register(future, "permission")
    owner._pending_elicitations["tool-1"] = object() if bad == "not-future" else future
    with pytest.raises(ObservationError, match="producer|waiter"):
        state(owner)
    future.cancel()


@pytest.mark.asyncio
async def test_notifications_are_read_only_isolated_and_unsubscribed() -> None:
    owner = client()
    wakes = []
    stop = owner.current_requests.subscribe(lambda: wakes.append(True))
    owner.current_requests.subscribe(lambda: 1 / 0)
    task = await ask(owner)
    assert wakes and not task.done()
    count = len(wakes)
    owner.resolve_elicitation("tool-1", {}, action="cancel")
    await task
    await asyncio.sleep(0)
    assert len(wakes) > count
    stop()
    count = len(wakes)
    owner.current_requests.changed()
    assert len(wakes) == count
    del task
    gc.collect()
    assert not owner.current_requests._occurrences
