#!/usr/bin/env python3
"""Standalone managed agenda CLI; only explicit trusted publisher argv executes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from agenda import AgendaError, _read, decode
from agenda_publish import publish
from agenda_store import history, mutate, read, safe_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("read", "next", "check", "history", "init", "update", "publish"))
    parser.add_argument("--state-home", type=Path, required=True)
    parser.add_argument("--request", type=Path, help="strict mutation JSON; otherwise read bounded stdin")
    parser.add_argument("--import-home", type=Path, help="explicit read-only legacy home, init only")
    parser.add_argument("--expected-revision", type=int, help="optional read guard; mutations require it in JSON")
    parser.add_argument("--worktree", help="optional read identity guard")
    parser.add_argument("--actor", help="caller-asserted metadata, not authenticated identity")
    parser.add_argument("--session", help="caller-asserted metadata, never permission")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--before-revision", type=int)
    parser.add_argument("--publish-arg", action="append", default=[],
                        help="one trusted argv token; use --publish-arg=--flag for flags")
    args = parser.parse_args(argv)
    try:
        if args.command != "history" and (args.limit is not None or args.before_revision is not None):
            raise AgendaError("limit/before-revision options require history")
        if args.command not in ("read", "next", "check") and (
                args.expected_revision is not None or args.worktree is not None):
            raise AgendaError("expected-revision/worktree flags are read guards; mutation revisions belong in JSON")
        if args.command in ("init", "update"):
            request = (_read(safe_path(args.request)) if args.request else
                       decode(sys.stdin.buffer.read(131073), "request"))
            change = request.get("change")
            if not isinstance(change, dict) or (change.get("operation") == "init") != (args.command == "init"):
                raise AgendaError("CLI command and semantic operation disagree")
            result, accepted = mutate(args.state_home, request, actor=args.actor, session=args.session,
                import_home=args.import_home, publisher_configured=bool(args.publish_arg))
            output = {"result": result, "delivery": result["delivery"]}
            if accepted and args.publish_arg:
                try:
                    output["delivery"] = publish(args.state_home, args.publish_arg)
                except AgendaError as exc:
                    output["delivery"] = {"state": "pending", "error": {
                        "code": "delivery-record-unavailable", "message": str(exc)}}
        else:
            if args.request or args.import_home or args.actor or args.session:
                raise AgendaError("request/import/actor/session options require init or update")
            if args.command == "publish":
                output = {"persisted": True, "delivery": publish(args.state_home, args.publish_arg)}
            elif args.publish_arg:
                raise AgendaError("publisher argv is only accepted by init, update or publish")
            elif args.command == "history":
                output = {"history": history(args.state_home,
                    100 if args.limit is None else args.limit, args.before_revision)}
            else:
                output = read(args.state_home, args.expected_revision, args.worktree)
                if args.command in ("next", "check"):
                    output = output["snapshot"]
        print(json.dumps(output, ensure_ascii=True, allow_nan=False, sort_keys=True))
        return 0
    except (AgendaError, BrokenPipeError) as exc:
        print(f"agenda: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
