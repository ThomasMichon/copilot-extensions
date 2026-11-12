"""Health check endpoint."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from ssh_manager import get_default_manager

from .. import __version__
from ..config import config_dir
from ..protocol import (
    HTTP_PROTOCOL_MIN_SUPPORTED,
    HTTP_PROTOCOL_VERSION,
    NATIVE_SEND_RECEIPT_CAPABILITY,
    NATIVE_SEND_RECEIPT_CAPABILITY_VERSION,
    NATIVE_SEND_NO_RESUME_CAPABILITY,
    NATIVE_SEND_NO_RESUME_CAPABILITY_VERSION,
    PORTABLE_MUX_CAPABILITY,
    READONLY_PLUGIN_CONTROLS_CAPABILITY,
    PORTABLE_INBOX_CAPABILITY,
)
from ..drain_contract import DRAIN_CONTRACT_VERSION
from ..hosting_admission_cli import HOSTING_CAPABILITY, HOSTING_CAPABILITY_VERSION
from ..self_retire import slot_descriptor
from ..live_keyed_receipt import CAPABILITY as LIVE_KEYED_RECEIPT_CAPABILITY
from ..live_exact_notify import CAPABILITY as LIVE_EXACT_NOTIFY_CAPABILITY
from ..session_observation_contract import CURRENT_REQUESTS, HISTORY, OBSERVATIONS, TOOL_STATISTICS
from ..owner_membership import CAPABILITY as OWNER_MEMBERSHIP
from ..worktree_writer_observation import observe as observe_worktree_writer
from .._peer_launch import ContextRefused
from ..acquisition_worktree_writer import CAPABILITY as RETAINED_WRITER_CAPABILITY
from ..acquisition_writer_inspection import CAPABILITY as WRITER_INSPECTION_CAPABILITY
from ..creation_worktree_writer import CAPABILITY as CREATION_WRITER_CAPABILITY
from ..creation_worktree_writer import RESUME_CAPABILITY as CREATION_WRITER_RESUME_CAPABILITY
from ..creation_writer_inspection import CAPABILITY as CREATION_WRITER_INSPECTION_CAPABILITY
from ..live_questions import CAPABILITY as LIVE_QUESTION_ANSWERS_CAPABILITY
from ..live_delivery_state import CAPABILITY as LIVE_DELIVERY_OBSERVATION_CAPABILITY
from ..mux_questions import CAPABILITY as MUX_QUESTION_ANSWERS_CAPABILITY
from ..status_schema import CAPABILITY as HOST_STATUS_CAPABILITY

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> dict:
    mgr = getattr(request.app.state, "session_manager", None)
    draining = bool(getattr(mgr, "is_draining", False)) if mgr else False
    # protocol_version / min_protocol_version advertise the HTTP wire-contract
    # version + supported range so a client can gate a version-introduced
    # capability on the daemon's support instead of blind-sending (dotfiles #632).
    # Additive: a client predating these fields simply ignores them.
    body = {
        "status": "ok",
        "service": "agent-bridge",
        "version": __version__,
        "ready": bool(getattr(request.app.state, "ready", False)),
        "topology_ready": bool(
            getattr(request.app.state, "topology_ready", False)
        ),
        "credential_relay_ready": bool(
            getattr(request.app.state, "credential_relay_ready", False)
        ),
        "draining": draining,
        "protocol_version": HTTP_PROTOCOL_VERSION,
        "min_protocol_version": HTTP_PROTOCOL_MIN_SUPPORTED,
        "capabilities": {
            HOSTING_CAPABILITY: HOSTING_CAPABILITY_VERSION,
            NATIVE_SEND_RECEIPT_CAPABILITY: NATIVE_SEND_RECEIPT_CAPABILITY_VERSION,
            NATIVE_SEND_NO_RESUME_CAPABILITY: NATIVE_SEND_NO_RESUME_CAPABILITY_VERSION,
            LIVE_KEYED_RECEIPT_CAPABILITY: 1,
            LIVE_EXACT_NOTIFY_CAPABILITY: 1,
            OBSERVATIONS: 1,
            HISTORY: 1,
            CURRENT_REQUESTS: 1,
            TOOL_STATISTICS: 1,
            OWNER_MEMBERSHIP: 1,
            PORTABLE_MUX_CAPABILITY: 1,
            READONLY_PLUGIN_CONTROLS_CAPABILITY: 1,
            PORTABLE_INBOX_CAPABILITY: 1,
            "worktree_record_delegation": 1,
            RETAINED_WRITER_CAPABILITY: 1,
            WRITER_INSPECTION_CAPABILITY: 1,
            CREATION_WRITER_CAPABILITY: 1,
            CREATION_WRITER_RESUME_CAPABILITY: 1,
            CREATION_WRITER_INSPECTION_CAPABILITY: 1,
            "retained_native_session_recovery": 1,
            LIVE_QUESTION_ANSWERS_CAPABILITY: 1,
            LIVE_DELIVERY_OBSERVATION_CAPABILITY: 1,
            MUX_QUESTION_ANSWERS_CAPABILITY: 1,
            HOST_STATUS_CAPABILITY: 1,
        },
        "drain_contract": {"minimum": DRAIN_CONTRACT_VERSION, "maximum": DRAIN_CONTRACT_VERSION},
        "ssh_carriers": get_default_manager().carrier_diagnostics(),
    }
    # process-slot-ownership Phase 5 (private-downstream-repo): "doctor"/"activity"/cockpit
    # render process -> slot -> owner -> alive?. Read-only and best-effort; see
    # slot_descriptor's own docstring for why agent-bridge's shape omits the
    # abandoned_passive_reap key agent-dispatch's coordinator publishes.
    body["slot"] = slot_descriptor(
        config_dir(),
        self_retire_status=getattr(request.app.state, "self_retire_status", None),
    )
    status_runtime = getattr(request.app.state, "status_runtime", None)
    body["host_status"] = {
        "availability": "unavailable" if (
            status_runtime is None or status_runtime.error
            or status_runtime.task is None or status_runtime.task.done()
        ) else "available",
        "reason": status_runtime.error if status_runtime is not None else "runtime_unavailable",
    }
    try:
        writer = await asyncio.to_thread(observe_worktree_writer)
    except (ContextRefused, OSError, ValueError, ImportError):
        writer = {"installation_root": None, "runtime_generation": None}
    body["worktree_record_writer"] = writer
    readiness_error = getattr(request.app.state, "readiness_error", None)
    if readiness_error:
        body["readiness_error"] = readiness_error
    # Live Session Host census (dotfiles#1656): how many independent Session
    # Hosts (each owning a possibly-mid-turn child that survives a frontend
    # restart) this daemon is fronting. Always surfaced so a drain/cutover is
    # never judged "clean" while live hosts it must preserve go unaccounted for.
    if mgr is not None and hasattr(mgr, "live_host_count"):
        body["live_host_count"] = mgr.live_host_count
    resolver = getattr(request.app.state, "resolver", None)
    if resolver is not None:
        errors = getattr(resolver, "topology_errors", [])
        warnings = getattr(resolver, "topology_warnings", [])
        body["topology_error_count"] = len(errors) if isinstance(errors, list) else 0
        body["topology_warning_count"] = (
            len(warnings) if isinstance(warnings, list) else 0
        )
    # When drained, surface *how long* and *why* so a stuck/aborted drain is
    # visible to monitoring without grepping logs (#1757).
    if draining and mgr is not None:
        drain_status = getattr(mgr, "drain_status", None)
        if callable(drain_status):
            body["drain"] = drain_status()
    return body
