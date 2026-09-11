"""Stable native launch, presentation, inspection and retirement commands."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from .native_store import NativeError


def command(args) -> None:
    from .__main__ import _get_client
    from .native_terminal import attach
    from .client import BridgeClientError

    action = args.native_action
    try:
        if action == "resource":
            from .native_resource_cli import command as resource_command
            value = resource_command(args)
            print(json.dumps(value, indent=2))
            return
        if action in {"attach", "resume"}:
            raise SystemExit(asyncio.run(attach(args.execution_id, args.expected_generation)))
        if action != "start":
            client = _get_client(ensure=action == "stop")
        if action == "start":
            try:
                payload = Path(args.command_file).read_bytes().decode("utf-8-sig")
            except (OSError, UnicodeError) as exc:
                raise NativeError("invalid_command_file", "Native command file could not be read as UTF-8", 400) from exc
            request = {
                "requestId": args.request_id, "codespace": args.codespace,
                "owner": args.owner, "cwd": args.cwd, "command": payload,
                "noPluginStaging": True, "requireRelay": True,
                "localForward": args.local_forward, "reverseForward": args.reverse_forward,
            }
            if getattr(args, "host_resources_file", None):
                from .native_resources import read_json_file, validate_definitions
                request["hostResources"] = validate_definitions(read_json_file(args.host_resources_file))
            value = _get_client(ensure=True).native_start(request)
        elif action == "stop":
            value = client.native_stop(args.execution_id, args.expected_generation)
        elif action == "status":
            value = client.native_status(args.execution_id, generation=args.expected_generation)
        elif action == "list":
            value = client.native_list()
        else:
            value = client.native_capabilities()
        print(json.dumps(value, indent=2))
    except NativeError as exc:
        print(json.dumps({"error": exc.code, "detail": exc.detail}))
        raise SystemExit(75 if exc.code in {"venue_busy", "native_incumbent"} else 69)
    except BridgeClientError as exc:
        print(json.dumps({"error": "native_operation_failed", "detail": exc.detail}))
        raise SystemExit(2 if exc.status == 400 else 69)
