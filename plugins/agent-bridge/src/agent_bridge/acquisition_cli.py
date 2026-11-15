"""Version-gated acquisition client and CLI surface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .acquisition_contract import AcquisitionRequest, AcquisitionResult
from .client import BridgeClientError

from .protocol import (
    ACQUISITION_WORKSPACE_PROTOCOL_VERSION, PLUGIN_READINESS_PROTOCOL_VERSION,
)

ACQUISITION_PROTOCOL_VERSION = ACQUISITION_WORKSPACE_PROTOCOL_VERSION


def acquire(client: Any, request: AcquisitionRequest) -> AcquisitionResult:
    required = PLUGIN_READINESS_PROTOCOL_VERSION if request.mode == "cli" or any(
        getattr(request, field) is not None
        for field in ("caller_worktree", "caller_owner_ref", "caller_session_id")
    ) \
        else ACQUISITION_PROTOCOL_VERSION
    if request.worktree_writer is not None:
        required = PLUGIN_READINESS_PROTOCOL_VERSION
    if not client.daemon_supports(required):
        raise BridgeClientError(426, {
            "code": "unsupported_acquisition_protocol",
            "message": "daemon does not support strict session acquisition",
            "effect": "not_started",
        })
    payload = request.model_dump(mode="json")
    payload["protocol_version"] = required
    send = client._request
    capabilities = {}
    if request.worktree_writer is not None:
        from .acquisition_worktree_writer import CAPABILITY

        capabilities[CAPABILITY] = 1
    if request.dispatch_origin is not None:
        from .dispatch_origin import CAPABILITY as ACP_ORIGIN
        from .dispatch_cli_origin import CAPABILITY as CLI_ORIGIN

        capabilities[CLI_ORIGIN if request.mode == "cli" else ACP_ORIGIN] = 1
    if capabilities:
        from .client_protocol_floor import protocol_floored_request

        send = protocol_floored_request(client, required, capabilities=capabilities)
    result = send("POST", "/api/v1/acquisitions", payload, request_timeout=max(240, request.verify_timeout + 240))
    return AcquisitionResult.model_validate(result)


def command(args: argparse.Namespace) -> None:
    from . import __main__ as core
    text = sys.stdin.read() if args.request_file == "-" else Path(args.request_file).read_text(encoding="utf-8")
    request = AcquisitionRequest.model_validate_json(text)
    try:
        client = core._get_client()
        if client.daemon_supports(PLUGIN_READINESS_PROTOCOL_VERSION) and all(
            getattr(request, field) is None
            for field in ("caller_worktree", "caller_owner_ref", "caller_session_id")
        ):
            from urllib.parse import quote

            try:
                prior = client._request("GET", f"/api/v1/acquisitions/{quote(request.delivery_id, safe='')}")
            except BridgeClientError as exc:
                if not isinstance(exc.detail, dict) or exc.detail.get("code") != "acquisition_not_found":
                    raise
                context = {
                    "caller_worktree": core._get_caller_id(),
                    "caller_owner_ref": core._worktrees_get("owner-ref"),
                    "caller_session_id": core._get_caller_session_id(),
                }
            else:
                context = prior.get("caller_context", {})
            request = request.model_copy(update={
                field: value for field, value in context.items()
                if field in {"caller_worktree", "caller_owner_ref", "caller_session_id"}
                and getattr(request, field) is None and value is not None
            })
        result = acquire(client, request)
    except BridgeClientError as exc:
        print(json.dumps({"ok": False, "status": exc.status, "detail": exc.detail}))
        raise SystemExit(1) from exc
    print(json.dumps(result.model_dump(mode="json")))


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser("acquire", help="Acquire one task-bound CLI/ACP conversation without fresh fallback")
    parser.add_argument("--request-file", required=True, help="Versioned acquisition JSON; '-' reads stdin")
    parser.set_defaults(func=command)
    inspect = subparsers.add_parser("acquisition-inspect", help="Read durable bridge acquisition identity")
    inspect.add_argument("delivery_id")
    inspect.add_argument(
        "--worktree-writer", action="store_true",
        help="Observe the original journal's executing writer and workspace without acquiring",
    )
    inspect.set_defaults(func=inspect_command)
    readiness = subparsers.add_parser(
        "acquisition-readiness", help="Prove daemon support for current acquisition safety",
    )
    readiness.set_defaults(func=readiness_command)


def readiness_command(args: argparse.Namespace) -> None:
    from . import __main__ as core
    supported = core._get_client().daemon_supports(PLUGIN_READINESS_PROTOCOL_VERSION)
    print(json.dumps({"supported": supported, "required_protocol": PLUGIN_READINESS_PROTOCOL_VERSION}))
    if not supported:
        raise SystemExit(1)


def inspect_command(args: argparse.Namespace) -> None:
    from . import __main__ as core
    client = core._get_client()
    if not client.daemon_supports(ACQUISITION_PROTOCOL_VERSION):
        raise BridgeClientError(426, "acquisition inspection unsupported")
    from urllib.parse import quote

    path = f"/api/v1/acquisitions/{quote(args.delivery_id, safe='')}"
    send = client._request
    if getattr(args, "worktree_writer", False):
        from .acquisition_writer_inspection import CAPABILITY
        from .client_protocol_floor import protocol_floored_request

        send = protocol_floored_request(
            client, PLUGIN_READINESS_PROTOCOL_VERSION, capabilities={CAPABILITY: 1},
        )
        path += "?worktree_writer=true"
    result = send("GET", path)
    if getattr(args, "worktree_writer", False):
        from .acquisition_contract import WorktreeWriterRequirement
        from .acquisition_writer_inspection import SCHEMA

        observed = result.get("worktree_writer_observation") if isinstance(result, dict) else None
        if not (
            isinstance(observed, dict) and observed.get("schema") == SCHEMA
            and type(observed.get("version")) is int and observed["version"] == 1
            and observed.get("delivery_id") == args.delivery_id == result.get("delivery_id")
            and observed.get("worktree_id") == result.get("worktree_id")
            and observed.get("writer_scope") == "executing-process"
            and type(observed.get("retained_session_writes")) is int
            and observed["retained_session_writes"] == 1
        ):
            raise BridgeClientError(502, "original acquisition writer observation is unconfirmed")
        try:
            WorktreeWriterRequirement.model_validate(observed.get("worktree_writer"))
        except ValueError as exc:
            raise BridgeClientError(502, "original acquisition writer requirement is unconfirmed") from exc
    print(json.dumps(result))
