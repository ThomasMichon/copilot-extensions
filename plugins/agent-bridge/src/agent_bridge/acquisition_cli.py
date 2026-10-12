"""Version-gated acquisition client and CLI surface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .acquisition_contract import AcquisitionRequest, AcquisitionResult
from .client import BridgeClientError

from .protocol import ACQUISITION_RETIREMENT_RECOVERY_PROTOCOL_VERSION

ACQUISITION_PROTOCOL_VERSION = ACQUISITION_RETIREMENT_RECOVERY_PROTOCOL_VERSION


def acquire(client: Any, request: AcquisitionRequest) -> AcquisitionResult:
    if not client.daemon_supports(ACQUISITION_PROTOCOL_VERSION):
        raise BridgeClientError(426, {
            "code": "unsupported_acquisition_protocol",
            "message": "daemon does not support strict session acquisition",
            "effect": "not_started",
        })
    payload = request.model_dump(mode="json")
    payload["protocol_version"] = ACQUISITION_PROTOCOL_VERSION
    result = client._request("POST", "/api/v1/acquisitions", payload, request_timeout=max(240, request.verify_timeout + 240))
    return AcquisitionResult.model_validate(result)


def command(args: argparse.Namespace) -> None:
    from . import __main__ as core
    text = sys.stdin.read() if args.request_file == "-" else Path(args.request_file).read_text(encoding="utf-8")
    request = AcquisitionRequest.model_validate_json(text)
    try:
        result = acquire(core._get_client(), request)
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


def readiness_command(args: argparse.Namespace) -> None:
    from . import __main__ as core
    supported = core._get_client().daemon_supports(ACQUISITION_PROTOCOL_VERSION)
    print(json.dumps({"supported": supported, "required_protocol": ACQUISITION_PROTOCOL_VERSION}))
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
