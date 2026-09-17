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
            raise SystemExit(asyncio.run(attach(
                args.execution_id, args.expected_generation,
                observer=args.observer, takeover=args.takeover,
            )))
        if action != "start":
            client = _get_client(ensure=action == "stop")
        if action == "start":
            try:
                payload = Path(args.command_file).read_bytes().decode("utf-8-sig")
            except (OSError, UnicodeError) as exc:
                raise NativeError("invalid_command_file", "Native command file could not be read as UTF-8", 400) from exc
            request = {
                "requestId": args.request_id,
                "owner": args.owner, "cwd": args.cwd, "command": payload,
                "noPluginStaging": True, "requireRelay": True,
                "localForward": args.local_forward, "reverseForward": args.reverse_forward,
            }
            if args.codespace:
                request["codespace"] = args.codespace
            else:
                request["target"] = f"container:{args.container}" if args.container else args.target
            if args.remote_command_file:
                try:
                    with Path(args.remote_command_file).open("rb") as stream:
                        raw = stream.read(65537)
                    if len(raw) > 65536:
                        raise ValueError("remote command descriptor exceeds 64 KiB")
                    request["remoteCommand"] = json.loads(raw.decode("utf-8-sig"))
                except (OSError, UnicodeError, ValueError) as exc:
                    raise NativeError("invalid_remote_command", "Remote command descriptor is not bounded UTF-8 JSON", 400) from exc
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
        elif action == "observe":
            value = client._request("POST", f"/api/v1/native-executions/{args.execution_id}/result", {
                "generation": args.expected_generation, "expectedSessionId": args.expected_session_id,
                "position": args.position,
            })
        else:
            value = client.native_capabilities()
        print(json.dumps(value, indent=2))
    except NativeError as exc:
        print(json.dumps({"error": exc.code, "detail": exc.detail}))
        raise SystemExit(75 if exc.code in {"venue_busy", "native_incumbent"} else 69)
    except BridgeClientError as exc:
        print(json.dumps({"error": "native_operation_failed", "detail": exc.detail}))
        raise SystemExit(2 if exc.status == 400 else 69)


def add_arguments(sub) -> None:
    import argparse

    native = sub.add_parser("native", help="Own native sessions independently of terminal presentation")
    actions = native.add_subparsers(dest="native_action", required=True)
    for action in ("capabilities", "list"):
        actions.add_parser(action).add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    start = actions.add_parser("start")
    target = start.add_mutually_exclusive_group(required=True)
    target.add_argument("--codespace")
    target.add_argument("--container")
    target.add_argument("--target", help="codespace:NAME or container:NAME")
    for field in ("owner", "cwd", "request-id"):
        start.add_argument(f"--{field}", required=True)
    start.add_argument("--command-file", "--interactive-command-file", dest="command_file", required=True)
    start.add_argument("--remote-command-file")
    start.add_argument("--host-resources-file")
    for direction in ("local", "reverse"):
        start.add_argument(f"--{direction}-forward", action="append", default=[])
    for flag in ("no-plugin-staging", "require-relay"):
        start.add_argument(f"--{flag}", action="store_true", default=True)
    start.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    for action in ("attach", "resume", "status", "stop", "observe"):
        p = actions.add_parser(action)
        p.add_argument("execution_id")
        p.add_argument("--expected-generation", required=action != "status")
        if action in {"attach", "resume"}:
            role = p.add_mutually_exclusive_group()
            role.add_argument("--observer", action="store_true")
            role.add_argument("--takeover", action="store_true")
        else:
            p.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
        if action == "observe":
            p.add_argument("--expected-session-id", required=True)
            p.add_argument("--position")
    resources = actions.add_parser("resource").add_subparsers(dest="resource_action", required=True)
    resources.add_parser("descriptor").add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    ensure = resources.add_parser("ensure")
    ensure.add_argument("resource")
    ensure.add_argument("--request-id")
    ensure.add_argument("--input-file")
    ensure.add_argument("--timeout", type=float, default=120)
    ensure.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    native.set_defaults(func=command)
    host = sub.add_parser("native-host", help="Official remote native execution-host management")
    actions = host.add_subparsers(dest="native_host_action", required=True)
    actions.add_parser("capabilities")
    actions.add_parser("guard").add_argument("--requested-mode", choices=["acp", "native"], required=True)
    actions.add_parser("start").add_argument("--request-stdin", action="store_true", required=True)
    for action in ("activate", "status", "stop", "message", "result", "resource-complete"):
        p = actions.add_parser(action)
        p.add_argument("execution_id")
        p.add_argument("--expected-generation", required=True)
        if action in {"stop", "message", "result", "resource-complete"}:
            p.add_argument("--request-stdin", action="store_true", required=action != "stop")
    host.set_defaults(func=host_command)


def host_command(args) -> None:
    from .native_runtime import command
    raise SystemExit(command(args))


def send_if_native(client, args, target, prompt) -> bool:
    from .__main__ import _caller_id_for
    from .protocol import NATIVE_EXECUTION_PROTOCOL_VERSION
    import uuid

    if getattr(client, "daemon_supports", lambda _: False)(NATIVE_EXECUTION_PROTOCOL_VERSION) is True:
        native = client.native_resolve(target)
        if native is not None:
            if not native.get("ready") or not native.get("sessionId"):
                raise SystemExit("Native execution owns this target but is not represented; refusing ACP fallback.")
            expected = getattr(args, "expected_session_id", None)
            if expected and expected != native["sessionId"]:
                raise SystemExit("Native live-session identity changed; refusing delivery.")
            result = client.native_message(native["executionId"], {
                "generation": native["generation"], "expectedSessionId": native["sessionId"],
                "sender": _caller_id_for(args) or "operator", "body": prompt,
                "messageId": getattr(args, "idempotency_key", None) or uuid.uuid4().hex,
                "kind": getattr(args, "kind", "prompt"), "wait": not getattr(args, "no_wait", False),
                "waitTimeout": getattr(args, "reply_timeout", 120),
            })
            print(json.dumps(result, indent=2))
            return True
    if target.startswith("native:"):
        raise SystemExit("Native execution is unavailable; refusing ACP fallback.")
    return False
