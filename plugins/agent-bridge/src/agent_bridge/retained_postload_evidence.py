"""Associate an acknowledged load with its surviving original local Host."""

from __future__ import annotations

import json
import math
from typing import Any

from . import retained_load_acceptance as load
from .acquisition_contract import WorktreeWriterRequirement
from .models import RetainedRecoveryRequest
from .retained_session_recovery import _refuse
from .retained_load_configuration import configuration_event


def original_frontend(
    manager: Any, session: Any, client: Any, host: Any,
    request: RetainedRecoveryRequest, events: list[dict[str, Any]],
) -> None:
    """Qualify the inactive RPC owner's frontend before detaching its Host socket."""
    from .acp_client import AcpClient
    from .retained_session_recovery import RetainedRecovery

    owner = getattr(session, "_retained_recovery_owner", None)
    if (
        not isinstance(client, AcpClient) or not isinstance(owner, RetainedRecovery)
        or owner.manager is not manager or owner.session is not session or owner.request != request
        or owner.active or not owner.native_rpc_started or not owner.native_started
        or owner.host_record is None or load.host_binding(owner.host_record) != load.host_binding(host)
        or owner.socket is None or getattr(client, "session_host_client", None) is not owner.socket
        or owner.streams is None
        or not client._host_mode or client.pid != host.child_pid
        or client.acp_session_id not in {None, request.native_session_id}
        or client._idle_operations or client._prompt_in_flight or client._prompt_lock.locked()
        or client._pending_permission_future is not None or client._pending_elicitations
        or client._out_of_turn_open
        or [item["data"] for item in events if item["event_type"] == load.EVENT] != [owner.accepted_load]
    ):
        raise _refuse("Original accepted load retains an unqualified frontend transport")


def _configuration_timeout(item: dict[str, Any]) -> bool:
    return bool(
        item["event_type"] == "acp_launch_timeout" and item["data"].get("stage") == "LAUNCH_ACP"
        and item["data"].get("mode") == "session-host"
    )


def physical(host: Any, request: RetainedRecoveryRequest) -> dict[str, Any]:
    """Corroborate current births, independently of any historical load claim."""
    try:
        before = load.epoch()
        observed = {name: load.process(getattr(host, f"{name}_pid")) for name in ("host", "child")}
        if (
            host.boundary != "local" or host.endpoint or not host.nonce
            or observed["child"]["parent_pid"] != host.host_pid
            or any(observed[name]["start_time"] != host.extra.get(f"{name}_process_start")
                   for name in observed)
            or any(load.process(observed[name]["pid"]) != observed[name] for name in observed)
            or load.epoch() != before
        ):
            raise ValueError("changed physical association")
        return {
            "version": 1, "operation_id": request.operation_id,
            "session_id": request.native_session_id, "head_revision": request.head_revision,
            "epoch": before,
            **{name: {key: observed[name][key] for key in ("pid", "start_time")} for name in observed},
        }
    except (OSError, ValueError, IndexError, AttributeError) as exc:
        raise _refuse("Original accepted load has unconfirmed physical Host custody") from exc


def _span(events: list[dict[str, Any]], request: RetainedRecoveryRequest) -> list[dict[str, Any]]:
    from .retained_recovery_settlement import START_EVENT

    if not events or any(
        type(item.get("event_id")) is not int or not isinstance(item.get("data"), dict)
        or item["event_id"] != index for index, item in enumerate(events, 1)
    ):
        raise _refuse("Accepted load requires its complete original event history")
    markers = [
        index for index, item in enumerate(events)
        if item.get("event_type") == START_EVENT
        and isinstance(item["data"].get("request"), dict)
        and item["data"].get("request", {}).get("operation_id") == request.operation_id
    ]
    if len(markers) != 1 or events[markers[0]]["data"] != {
        "version": 1, "request": request.model_dump(),
    }:
        raise _refuse("Accepted load lost its original operation marker")
    span = events[markers[0] + 1:]
    if any(not configuration_event(item["event_type"], item["data"]) and not _configuration_timeout(item)
           and (item.get("event_type") not in {
        "startup_progress", "connect_checkpoint", load.EVENT, "session_state_changed",
    } or item["event_type"] == "session_state_changed" and item["data"].get("status") != "stopped"
    ) for item in span):
        raise _refuse("Original accepted load has conflicting subsequent activity")
    progress = [
        (item["data"].get("phase"), item["data"].get("state"))
        for item in span if item["event_type"] == "startup_progress"
        and item["data"].get("state") != "waiting"
    ]
    if (
        not progress or progress[0] != ("resume", "started")
        or [(phase, state) for phase, state in progress if phase == "resume"]
        not in ([("resume", "started")], [("resume", "started"), ("resume", "failed")])
        or [(phase, state) for phase, state in progress if phase == "native-session-load"]
        not in (
            [("native-session-load", "started")],
            [("native-session-load", "started"), ("native-session-load", "finished")],
            [("native-session-load", "started"), ("native-session-load", "failed")],
        )
        or any(phase in {"native-session-new", "ready"} for phase, _ in progress)
    ):
        raise _refuse("Original history does not prove a single acknowledged retained load")
    return span


def _time(item: dict[str, Any]) -> float:
    value = item["timestamp"]
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError("invalid original event time")
    return float(value)


def _historical(
    host: Any, span: list[dict[str, Any]], activation: dict[str, Any], worktree_path: str,
) -> None:
    """Adapt only affirmative tagged history and its immutable launch state."""
    try:
        started, finished = [
            item for item in span if item["event_type"] == "startup_progress"
            and item["data"].get("phase") == "session-host-start"
            and item["data"].get("state") != "waiting"
        ]
        if (
            started["data"]["state"] != "started" or finished["data"]["state"] != "finished"
            or not _time(started) <= host.created_at <= _time(finished)
            or _time(finished) > _time(next(
                item for item in span if item["event_type"] == "startup_progress"
                and item["data"].get("phase") == "native-session-load"
                and item["data"].get("state") == "started"
            ))
        ):
            raise ValueError("original Host launch span is unconfirmed")
        with open(host.state_file, encoding="utf-8") as stream:
            raw = stream.read(16385)
        if len(raw) > 16384:
            raise ValueError("original Host state exceeds its budget")
        state = json.loads(raw)
        if (
            type(state.get("version")) is not int or state["version"] != 2
            or any(state.get(key) != getattr(host, key) for key in (
                "session_id", "port", "host_pid", "child_pid", "protocol_version", "nonce",
            ))
            or state.get("cwd") != worktree_path
            or type(state.get("created_at")) not in (float, int)
            or not _time(started) <= state["created_at"] <= host.created_at
            or state.get("boot_id") != activation["epoch"]["boot_id"].split(":")[0]
            or any(str(state.get(f"{name}_start_ticks")) != activation[name]["start_time"]
                   for name in ("host", "child"))
        ):
            raise ValueError("original Host state is not causally associated")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise _refuse("Historical load lacks its affirmative original Host association") from exc


def qualify(
    events: list[dict[str, Any]], host: Any, request: RetainedRecoveryRequest,
    requirement: WorktreeWriterRequirement, worktree_path: str,
) -> dict[str, Any]:
    """Return original activation evidence, never infer an ACK from current health."""
    span = _span(events, request)
    activation = physical(host, request)
    accepted = [(index, item["data"]) for index, item in enumerate(span) if item["event_type"] == load.EVENT]
    if not accepted:
        progress = [
            (item["data"].get("phase"), item["data"].get("state"))
            for item in span if item["event_type"] == "startup_progress"
            and item["data"].get("state") != "waiting"
        ]
        if progress[-1] != ("resume", "failed") or (
            "native-session-load", "finished"
        ) not in progress:
            raise _refuse("Historical load lacks its original terminal acknowledgement")
        load_steps = [
            index for index, item in enumerate(span) if item["event_type"] == "startup_progress"
            and item["data"].get("phase") == "native-session-load"
            and item["data"].get("state") != "waiting"
        ]
        if any(
            _configuration_timeout(item) or configuration_event(item["event_type"], item["data"])
            and not load_steps[0] < index < load_steps[1]
            for index, item in enumerate(span)
        ):
            raise _refuse("Historical configuration escaped its original load")
        _historical(host, span, activation, worktree_path)
        return activation
    try:
        if len(accepted) != 1:
            raise ValueError("multiple accepted loads")
        index, document = accepted[0]
        writer = WorktreeWriterRequirement.model_validate(document["writer"])
        load_steps = [
            index for index, item in enumerate(span) if item["event_type"] == "startup_progress"
            and item["data"].get("phase") == "native-session-load"
            and item["data"].get("state") != "waiting"
        ]
        if (
            set(document) - {"configuration"} != {
                "version", "request", "bridge_session_id", "writer", "host", "activation",
            }
            or type(document["version"]) is not int or document["version"] != 1
            or document["request"] != request.model_dump()
            or document["bridge_session_id"] != host.session_id
            or document["host"] != load.host_binding(host) or document["activation"] != activation
            or writer.creation_nonce != requirement.creation_nonce
            or writer.identity.installation_root != requirement.identity.installation_root
            or not load_steps[0] < index
            or len(load_steps) > 1 and index >= load_steps[1]
            or any(
                position < index and (
                    configuration_event(item["event_type"], item["data"])
                    or _configuration_timeout(item)
                    or item["event_type"] == "startup_progress"
                    and item["data"].get("phase") == "resume" and item["data"].get("state") == "failed"
                )
                for position, item in enumerate(span)
            )
        ):
            raise ValueError("changed accepted association")
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        raise _refuse("Accepted load journal conflicts with its original owner") from exc
    return activation
