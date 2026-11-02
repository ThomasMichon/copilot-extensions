"""Durable readonly controls gate owned CLI task/mode admission, never ordinary input."""

import asyncio
import json
import logging
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from agent_bridge.acquisition_cli import acquire
from agent_bridge.acquisition_contract import AcquisitionRefused, AcquisitionRequest
from agent_bridge.client import BridgeClientError
from agent_bridge.live_controls import ControlAckRequest, SET_MODE_READY_CONTROL, SetModeRequest
from agent_bridge.plugin_readiness import (
    READINESS_CONTROL, PluginReadinessError, PluginReadinessRequest,
    PluginReadinessResult, wait_plugin_readiness,
)
from agent_bridge.protocol import PLUGIN_READINESS_PROTOCOL_VERSION
from agent_bridge.routes.live_sessions import ack_live_controls, list_live_messages, set_live_mode
from test_acquisition_runtime import req
from test_acquisition_runtime import rig as rig


def register(db, cwd, sid="native"):
    db.register_live_session(
        sid, machine="synthetic", cwd=str(cwd), worktree_id="synthetic-worktree",
        repo="synthetic", branch=None, pid=None, role=None, now=time.time(),
    )


def expectation(cwd=None, **updates):
    return PluginReadinessRequest.model_validate({
        "owner": "bridge", "cwd": str(cwd) if cwd else None,
        "plugins": [{"projected_source": "helper@projected", "logical_source": "helper@original"}],
        "timeout_seconds": 1, **updates,
    })


async def claimed(db, sid, *, kind=READINESS_CONTROL):
    for _ in range(200):
        rows = db.claim_live_controls(sid, time.time(), 130)
        matching = [row for row in rows if row["kind"] == kind]
        if matching:
            return matching[0]
        await asyncio.sleep(0.005)
    raise AssertionError(f"no control was queued: {kind}")


def observation(control, status="ready", **updates):
    payload = json.loads(control["body"])
    return PluginReadinessResult(
        status=status, reason="selected-loadout-registered" if status == "ready" else "selected-plugin-disabled",
        session_id=payload["expected_session_id"], cwd=payload["cwd"],
        selection_digest=payload["selection_digest"], **updates,
    )


def reply(db, sid, control, **updates):
    result = observation(control, **updates)
    assert db.ack_live_messages(
        sid, [control["id"]], time.time(), controls=True, outcome=result.model_dump_json(),
    ) == 1


@pytest.mark.asyncio
async def test_readonly_control_waits_for_bound_result_then_allows_retry(tmp_db, tmp_path):
    register(tmp_db, tmp_path)
    task = asyncio.create_task(wait_plugin_readiness(tmp_db, "native", expectation(tmp_path)))
    control = await claimed(tmp_db, "native")
    assert not task.done()
    reply(tmp_db, "native", control, status="failed")
    with pytest.raises(PluginReadinessError, match="selected-plugin-disabled"):
        await task
    task = asyncio.create_task(wait_plugin_readiness(tmp_db, "native", expectation(tmp_path)))
    retried = await claimed(tmp_db, "native")
    assert retried["id"] != control["id"]
    reply(tmp_db, "native", retried)
    assert (await task).status == "ready"
    assert not tmp_db.execute_read("SELECT id FROM live_messages WHERE kind NOT LIKE 'control:%'")


@pytest.mark.asyncio
@pytest.mark.parametrize("claim", [False, True])
async def test_cancellation_settles_query_and_late_ack_cannot_authorize_work(tmp_db, tmp_path, claim):
    register(tmp_db, tmp_path)
    task = asyncio.create_task(wait_plugin_readiness(tmp_db, "native", expectation(tmp_path)))
    await asyncio.sleep(0)
    control = await claimed(tmp_db, "native") if claim else tmp_db.execute_read(
        "SELECT * FROM live_messages",
    )[0]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert tmp_db.live_control_state("native", control["id"])["outcome"] in {"withdrawn", "cancelled"}
    assert tmp_db.ack_live_messages(
        "native", [control["id"]], time.time(), controls=True,
        outcome=observation(control).model_dump_json(),
    ) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["sid", "digest", "malformed", "old-extension"])
async def test_bad_observation_never_authorizes_task(tmp_db, tmp_path, bad):
    register(tmp_db, tmp_path)
    task = asyncio.create_task(wait_plugin_readiness(tmp_db, "native", expectation(tmp_path)))
    control = await claimed(tmp_db, "native")
    value = observation(control).model_dump()
    if bad == "sid":
        value["session_id"] = "stale"
    if bad == "digest":
        value["selection_digest"] = "another-selection"
    outcome = "not-json{" if bad == "malformed" else "rejected" if bad == "old-extension" else json.dumps(value)
    tmp_db.ack_live_messages("native", [control["id"]], time.time(), controls=True, outcome=outcome)
    with pytest.raises(PluginReadinessError):
        await task


@pytest.mark.asyncio
async def test_timeout_and_expected_native_mismatch_fail_without_prompt(tmp_db, tmp_path):
    register(tmp_db, tmp_path)
    with pytest.raises(PluginReadinessError, match="native-session-changed"):
        await wait_plugin_readiness(
            tmp_db, "native", expectation(tmp_path, expected_session_id="another-native"),
        )
    with pytest.raises(PluginReadinessError, match="readiness-timeout"):
        await wait_plugin_readiness(tmp_db, "native", expectation(tmp_path, timeout_seconds=0.02))
    assert not tmp_db.execute_read("SELECT id FROM live_messages WHERE kind NOT LIKE 'control:%'")


@pytest.mark.asyncio
@pytest.mark.parametrize("claim", [False, True])
async def test_readiness_timeout_logs_only_last_observed_stage(tmp_db, tmp_path, caplog, claim):
    register(tmp_db, tmp_path)
    with caplog.at_level(logging.WARNING, logger="agent_bridge.plugin_readiness"):
        task = asyncio.create_task(wait_plugin_readiness(
            tmp_db, "native", expectation(tmp_path, timeout_seconds=0.1),
        ))
        if claim:
            await claimed(tmp_db, "native")
        with pytest.raises(PluginReadinessError, match="readiness-timeout"):
            await task
    stage = "claimed" if claim else "queued"
    assert len(caplog.records) == 1
    message = caplog.records[0].getMessage()
    assert f"last_observed_stage={stage}" in message
    assert "code=readiness-timeout" in message
    assert "missing_count=0" in message
    assert "helper@" not in message and str(tmp_path) not in message


@pytest.mark.asyncio
@pytest.mark.parametrize("bad,code", [
    ("sid", "readiness-result-context-changed"),
    ("json", "malformed-readiness-result"),
    ("control", "readiness-control-rejected"),
    ("reason", "unrecognized-readiness-reason"),
])
async def test_readiness_diagnostics_exclude_peer_supplied_text(tmp_db, tmp_path, caplog, bad, code):
    register(tmp_db, tmp_path)
    with caplog.at_level(logging.WARNING, logger="agent_bridge.plugin_readiness"):
        task = asyncio.create_task(wait_plugin_readiness(tmp_db, "native", expectation(tmp_path)))
        control = await claimed(tmp_db, "native")
        value = observation(control, status="failed", missing=["private-loadout"]).model_dump()
        value["reason"] = "private-peer-detail"
        if bad == "sid":
            value["session_id"] = "private-other-session"
        outcome = "{private-malformed" if bad == "json" else (
            "private-control-outcome" if bad == "control" else json.dumps(value)
        )
        tmp_db.ack_live_messages("native", [control["id"]], time.time(), controls=True, outcome=outcome)
        with pytest.raises(PluginReadinessError):
            await task
    assert len(caplog.records) == 1
    message = caplog.records[0].getMessage()
    assert f"control_id={control['id']}" in message
    assert "last_observed_stage=outcome" in message
    assert f"code={code}" in message
    assert "private-" not in message and str(tmp_path) not in message


@pytest.mark.asyncio
async def test_success_is_silent_and_cancellation_logs_without_changing_settlement(tmp_db, tmp_path, caplog):
    register(tmp_db, tmp_path)
    with caplog.at_level(logging.WARNING, logger="agent_bridge.plugin_readiness"):
        success = asyncio.create_task(wait_plugin_readiness(tmp_db, "native", expectation(tmp_path)))
        first = await claimed(tmp_db, "native")
        reply(tmp_db, "native", first)
        assert (await success).status == "ready"
        assert not caplog.records
        cancelled = asyncio.create_task(wait_plugin_readiness(tmp_db, "native", expectation(tmp_path)))
        second = await claimed(tmp_db, "native")
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
    assert len(caplog.records) == 1
    assert "code=cancelled" in caplog.records[0].getMessage()
    assert tmp_db.live_control_state("native", second["id"])["outcome"] == "cancelled"
    assert tmp_db.ack_live_messages(
        "native", [second["id"]], time.time(), controls=True,
        outcome=observation(second).model_dump_json(),
    ) == 0


@pytest.mark.asyncio
async def test_ack_route_rejects_result_from_another_selection(tmp_db, tmp_path):
    register(tmp_db, tmp_path)
    task = asyncio.create_task(wait_plugin_readiness(tmp_db, "native", expectation(tmp_path)))
    control = await claimed(tmp_db, "native")
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=tmp_db)))
    forged = observation(control).model_copy(update={"selection_digest": "wrong"})
    with pytest.raises(HTTPException) as error:
        await ack_live_controls("native", ControlAckRequest(ids=[control["id"]], plugin_readiness=forged), request)
    assert error.value.status_code == 409
    await ack_live_controls(
        "native", ControlAckRequest(ids=[control["id"]], plugin_readiness=observation(control)), request,
    )
    assert (await task).status == "ready"


@pytest.mark.asyncio
async def test_owned_autopilot_is_not_queued_until_plugins_ready(tmp_db, tmp_path, monkeypatch):
    register(tmp_db, tmp_path)
    monkeypatch.setattr("agent_bridge.routes.live_sessions.MODE_POLL_SECONDS", 0.005)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=tmp_db)))
    task = asyncio.create_task(set_live_mode(
        "native", SetModeRequest(
            mode="autopilot", plugin_readiness=expectation(tmp_path), wait_timeout=1,
        ), request,
    ))
    readiness = await claimed(tmp_db, "native")
    assert not tmp_db.execute_read("SELECT id FROM live_messages WHERE kind LIKE 'control:set-mode%'")
    reply(tmp_db, "native", readiness)
    mode = await claimed(tmp_db, "native", kind=SET_MODE_READY_CONTROL)
    mode_payload = json.loads(mode["body"])
    assert mode_payload["mode"] == "autopilot"
    assert mode_payload["plugin_readiness"]["expected_session_id"] == "native"
    assert mode_payload["plugin_readiness"]["selection_digest"] == json.loads(readiness["body"])["selection_digest"]
    tmp_db.ack_live_messages("native", [mode["id"]], time.time(), controls=True, outcome="applied")
    assert (await task).applied is True


@pytest.mark.asyncio
async def test_cancelled_owned_autopilot_never_queues_mode(tmp_db, tmp_path):
    register(tmp_db, tmp_path)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=tmp_db)))
    task = asyncio.create_task(set_live_mode(
        "native", SetModeRequest(mode="autopilot", plugin_readiness=expectation(tmp_path)), request,
    ))
    await claimed(tmp_db, "native")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not tmp_db.execute_read("SELECT id FROM live_messages WHERE kind LIKE 'control:set-mode%'")


@pytest.mark.asyncio
async def test_typed_cli_acquisition_holds_task_before_readiness(rig):
    request = req(mode="cli", plugin_readiness=expectation())
    task = asyncio.create_task(rig.runtime.acquire(request))
    control = await claimed(rig.db, "cli-native-new")
    assert not rig.db.execute_read("SELECT id FROM live_messages WHERE kind NOT LIKE 'control:%'")
    assert not task.done()
    reply(rig.db, "cli-native-new", control)
    result = await task
    prompts = rig.db.execute_read("SELECT body FROM live_messages WHERE kind NOT LIKE 'control:%'")
    assert len(prompts) == 1
    assert result.native_session_id == "cli-native-new"
    http = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=rig.db)))
    assert not (await list_live_messages(result.session_id, http)).messages
    visible = (await list_live_messages(result.session_id, http, plugin_readiness=True)).messages
    assert len(visible) == 1 and visible[0].kind.startswith("startup:prompt:")
    assert visible[0].plugin_readiness["plugins"][0]["projected_source"] == "helper@projected"
    assert await rig.runtime.acquire(request) == result
    assert len(rig.db.execute_read("SELECT id FROM live_messages WHERE kind NOT LIKE 'control:%'")) == 1


@pytest.mark.asyncio
async def test_failed_cli_readiness_leaves_task_undelivered(rig):
    owned = req(mode="cli", plugin_readiness=expectation())
    task = asyncio.create_task(rig.runtime.acquire(owned))
    control = await claimed(rig.db, "cli-native-new")
    reply(rig.db, "cli-native-new", control, status="unsupported")
    with pytest.raises(AcquisitionRefused, match="unsupported") as failure:
        await task
    assert failure.value.code == "plugins_not_ready"
    assert not rig.db.execute_read("SELECT id FROM live_messages WHERE kind NOT LIKE 'control:%'")
    retried = asyncio.create_task(rig.runtime.acquire(owned))
    new_control = await claimed(rig.db, "cli-native-new")
    reply(rig.db, "cli-native-new", new_control)
    assert (await retried).native_session_id == "cli-native-new"
    assert len(rig.db.execute_read("SELECT id FROM live_messages WHERE kind NOT LIKE 'control:%'")) == 1


@pytest.mark.asyncio
async def test_cancelled_cli_readiness_cannot_deliver_from_a_late_result(rig):
    task = asyncio.create_task(rig.runtime.acquire(req(mode="cli", plugin_readiness=expectation())))
    control = await claimed(rig.db, "cli-native-new")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert rig.db.ack_live_messages(
        "cli-native-new", [control["id"]], time.time(), controls=True,
        outcome=observation(control).model_dump_json(),
    ) == 0
    assert not rig.db.execute_read("SELECT id FROM live_messages WHERE kind NOT LIKE 'control:%'")


@pytest.mark.asyncio
async def test_prepared_acp_does_not_require_cli_extension_observation(rig, monkeypatch):
    async def forbidden(*args):
        raise AssertionError("ACP must not query a joined CLI extension")
    monkeypatch.setattr("agent_bridge.plugin_readiness.wait_plugin_readiness", forbidden)
    assert (await rig.runtime.acquire(req(plugin_readiness=expectation()))).mode == "acp"


def test_legacy_request_serialization_and_new_cli_protocol_floor():
    legacy = req()
    assert "plugin_readiness" not in legacy.model_dump()
    assert "plugin_readiness" not in json.loads(legacy.model_dump_json())
    client = MagicMock()
    client.daemon_supports.return_value = False
    with pytest.raises(BridgeClientError):
        acquire(client, req(mode="cli", plugin_readiness=expectation()))
    client.daemon_supports.assert_called_once_with(PLUGIN_READINESS_PROTOCOL_VERSION)
    client._request.assert_not_called()


def test_expected_projected_identities_are_unique_and_owner_explicit():
    with pytest.raises(ValueError):
        PluginReadinessRequest.model_validate({"plugins": []})
    with pytest.raises(ValueError):
        expectation(plugins=[{"projected_source": "p@m"}, {"projected_source": "p@m"}])
    assert AcquisitionRequest.model_validate(req().model_dump()).model_dump() == req().model_dump()
