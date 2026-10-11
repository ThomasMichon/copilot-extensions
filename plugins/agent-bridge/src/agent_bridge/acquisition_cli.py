"""Version-gated acquisition client and CLI surface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .acquisition_contract import AcquisitionRequest, AcquisitionResult
from .client import BridgeClientError

ACQUISITION_PROTOCOL_VERSION = 30


def acquire(client: Any, request: AcquisitionRequest) -> AcquisitionResult:
    if not client.daemon_supports(ACQUISITION_PROTOCOL_VERSION):
        raise BridgeClientError(426, {
            "code": "unsupported_acquisition_protocol",
            "message": "daemon does not support strict session acquisition",
            "effect": "not_started",
        })
    payload = request.model_dump(mode="json")
    payload["protocol_version"] = ACQUISITION_PROTOCOL_VERSION
    result = client._request("POST", "/api/v1/acquisitions", payload, request_timeout=240)
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
