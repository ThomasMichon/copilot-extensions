"""Version-gated acquisition client and CLI surface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .acquisition_contract import AcquisitionRequest, AcquisitionResult
from .client import BridgeClientError

from .protocol import ACQUISITION_WORKSPACE_PROTOCOL_VERSION, PLUGIN_READINESS_PROTOCOL_VERSION

ACQUISITION_PROTOCOL_VERSION = ACQUISITION_WORKSPACE_PROTOCOL_VERSION


def acquire(client: Any, request: AcquisitionRequest) -> AcquisitionResult:
    required = PLUGIN_READINESS_PROTOCOL_VERSION if request.mode == "cli" or any(
        getattr(request, field) is not None
        for field in ("caller_worktree", "caller_owner_ref", "caller_session_id")
    ) \
        else ACQUISITION_PROTOCOL_VERSION
    if not client.daemon_supports(required):
        raise BridgeClientError(426, {
            "code": "unsupported_acquisition_protocol",
            "message": "daemon does not support strict session acquisition",
            "effect": "not_started",
        })
    payload = request.model_dump(mode="json")
    payload["protocol_version"] = required
    result = client._request("POST", "/api/v1/acquisitions", payload, request_timeout=max(240, request.verify_timeout + 240))
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
    inspect.set_defaults(func=inspect_command)
    readiness = subparsers.add_parser(
        "acquisition-readiness", help="Prove daemon support for current acquisition safety",
    )
    readiness.set_defaults(func=readiness_command)
    from .native_replacement import readiness_command as replacement_readiness
    replacement = subparsers.add_parser(
        "native-replacement-readiness", help="Read support for authorized same-workspace native replacement",
    )
    replacement.set_defaults(func=replacement_readiness)


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
    print(json.dumps(client._request(
        "GET", f"/api/v1/acquisitions/{quote(args.delivery_id, safe='')}",
    )))
