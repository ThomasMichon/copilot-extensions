"""Installed standalone client; explicit configuration never adopts a native session."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from . import __version__
from .client import Client, ClientError, emit
from .store import ChatError, MAX_BODY_BYTES, strict_json
from .transport import GatewayTransport

SCAN_CAPABILITY = {
    "schema": "agent-chat.scan-capability", "version": 1, "operation": "scan",
    "request_file": "-",
    "max_request_bytes": 65536, "max_response_bytes": 1048576,
    "max_channels": 16, "max_events": 100, "max_timeout": 300, "max_batch_ms": 5000,
}


def configured(path: str | None) -> Client:
    if not path:
        raise ClientError("invalid_configuration", "Provide an explicit nonsecret client configuration file.")
    with Path(path).open("rb") as stream:
        config = strict_json(stream.read(MAX_BODY_BYTES + 1))
    if (not isinstance(config, dict)
            or not {"schema", "version", "actor", "state_dir"} <= set(config)
            or set(config) - {
                "schema", "version", "endpoint", "endpoint_dir", "actor", "state_dir",
                "token_command", "request_command",
            }
            or config["schema"] != "agent-chat.client" or type(config["version"]) is not int
            or config["version"] != 1 or not isinstance(config["state_dir"], str)):
        raise ClientError("invalid_configuration", "Invalid client configuration schema or fields.")
    return Client(GatewayTransport(config), config["actor"], config["state_dir"])


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="strict", newline="\n")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--config", help="Explicit nonsecret client JSON; no ambient home fallback")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("catalog", help="Inspect this installed client's exact offline invocation")
    sub.add_parser("status", help="Inspect local pending metadata without network or credentials")
    for name in ("join", "leave", "post", "read", "wait", "members"):
        child = sub.add_parser(name)
        child.add_argument("channel")
        if name == "post":
            child.add_argument("text", nargs="?", default="-")
            child.add_argument("--request-id", required=True)
        elif name == "read":
            child.add_argument("--limit", type=int, default=10)
        elif name == "wait":
            child.add_argument("--timeout", type=float, default=300)
            child.add_argument("--batch-ms", type=int, default=250)
        elif name == "members":
            child.add_argument("--limit", type=int)
            child.add_argument("--after")
    sub.add_parser("resolve").add_argument("codename")
    scan = sub.add_parser("scan")
    controls = scan.add_mutually_exclusive_group(required=True)
    controls.add_argument("--describe", action="store_true")
    controls.add_argument("--request-file", choices=["-"])
    args = parser.parse_args(argv)
    try:
        if args.command == "catalog":
            from .catalog import installed_catalog
            emit(installed_catalog(), sys.stdout)
            return 0
        if args.command == "scan" and args.describe:
            emit(SCAN_CAPABILITY, sys.stdout)
            return 0
        client = configured(args.config)
        if args.command == "status":
            result = client.status()
        elif args.command == "join":
            result = client.join(args.channel)
        elif args.command == "leave":
            result = client.leave(args.channel)
        elif args.command == "post":
            text = args.text
            if text == "-":
                text = sys.stdin.buffer.read(8193).decode("utf-8")
            result = client.post(args.channel, text, args.request_id)
        elif args.command == "read":
            client.read(args.channel, sys.stdout, limit=args.limit)
            return 0
        elif args.command == "scan":
            fields = strict_json(sys.stdin.buffer.read(MAX_BODY_BYTES + 1))
            if not isinstance(fields, dict) or set(fields) - {
                "epoch", "after", "limit", "timeout", "batch_ms",
            }:
                raise ClientError("invalid_request", "Invalid metadata scan controls.")
            result = client.scan(**fields)
        elif args.command == "resolve":
            result = client.resolve(args.codename)
        elif args.command == "members":
            result = client.members(args.channel, limit=args.limit, after=args.after)
        else:
            result = client.wait(args.channel, timeout=args.timeout, batch_ms=args.batch_ms)
        emit(result, sys.stdout)
        return 0
    except (ClientError, ChatError) as error:
        emit(error.as_dict(), sys.stderr)
        return 1
    except (OSError, ValueError, UnicodeError) as error:
        emit({"error": "client_io", "message": f"Client operation failed: {type(error).__name__}."}, sys.stderr)
        return 1
    except KeyboardInterrupt:
        emit({"error": "interrupted", "message": "Operation interrupted; retain any unconfirmed original."}, sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
