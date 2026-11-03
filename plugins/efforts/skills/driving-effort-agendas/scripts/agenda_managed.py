#!/usr/bin/env python3
"""Explicit fresh agenda initialization through the selected placement owner."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import re
import sys
from typing import Any

from agenda import AgendaError, _read, _text, decode
from agenda_publish import _invoke
from agenda_store import DATABASE, _state, encode, mutate, safe_path, transaction
from agenda_store_v2 import validate_initialization

CAPABILITY = "agenda-managed-placement-v1"
PLACEMENT_SCHEMA = "agent-worktrees.worktree-data"
PROVIDER = r'''
import json,sys
try:
    from agent_worktrees import worktree_data_cli as contract
    if (getattr(contract,"CAPABILITY",None)!="worktree-data-v1"
            or getattr(contract,"VERSION",None)!=1):
        raise ImportError("selected placement provider does not declare worktree-data-v1")
    from agent_worktrees.__main__ import main
except ImportError as exc:
    print(json.dumps({"ok":False,"error":{"code":"unsupported","message":str(exc)}}))
else:
    try:
        main(["--project",sys.argv[1],*sys.argv[2:]])
    except SystemExit as exc:
        print(json.dumps({"ok":False,"error":{
            "code":"unsupported","message":"selected placement parser refused the command"}}))
'''


def _owner(provider_python: str, project: str, command: list[str]) -> dict[str, Any]:
    interpreter = Path(provider_python)
    if not interpreter.is_absolute():
        raise AgendaError("placement interpreter must be explicitly resolved and absolute")
    _text(project, "selected placement project")
    try:
        status = asyncio.run(_invoke(
            [str(interpreter), "-I", "-B", "-c", PROVIDER, project, *command], b"", timeout=7,
        ))
    except (OSError, ValueError, TimeoutError) as exc:
        raise AgendaError(f"placement owner unavailable: {exc}") from exc
    if status["state"] != "delivered":
        raise AgendaError(f"placement owner refused: {status['error']['message']}")
    reply = status["reply"]
    if reply.get("schema") != PLACEMENT_SCHEMA or type(reply.get("version")) is not int or reply["version"] != 1:
        raise AgendaError("placement owner returned an unsupported response")
    return reply


def initialize(
    provider_python: str, project: str, worktree: str, creation_nonce: str, lane: str,
    store_id: str, request: dict[str, Any], *, actor: str | None = None,
    session: str | None = None,
) -> dict[str, Any]:
    """Allocate explicitly, then initialize the same declared store without delivery."""
    if not isinstance(store_id, str) or re.fullmatch(r"[0-9a-f]{32}", store_id) is None:
        raise AgendaError("managed store ID must be 32 lowercase hexadecimal characters")
    request = decode(encode(request).encode(), "managed initialization request")
    validate_initialization(request, actor=actor, session=session, initial_store_id=store_id)
    if request["change"]["worktree"] != worktree:
        raise AgendaError("managed initialization request must select the exact worktree")
    support = _owner(provider_python, project, ["worktree-data-read", "--capabilities", "--json"])
    if support.get("capabilities") != ["worktree-data-v1"]:
        raise AgendaError("actual placement owner does not support worktree-data-v1")
    reply = _owner(provider_python, project, [
        "worktree-data-allocate", "--worktree", worktree, "--creation-nonce", creation_nonce,
        "--domain", "agendas", "--lane", lane, "--store-id", store_id,
        "--contract-version", "1", "--json",
    ])
    if reply.get("project") != project or reply.get("worktree_id") != worktree or reply.get("availability") != "registered":
        raise AgendaError("placement owner changed the selected project or worktree")
    placement = reply.get("data")
    if not isinstance(placement, dict) or any(
        placement.get(key) != value for key, value in {
            "domain": "agendas", "lane": lane, "store_id": store_id,
            "creation_nonce": creation_nonce, "worktree_id": worktree,
        }.items()
    ):
        raise AgendaError("placement owner changed the selected store identity")
    scope = placement.get("scope")
    if (
        not isinstance(scope, dict) or scope.get("project") != project
        or scope.get("repository") != project
        or placement.get("state_home_allocated") is not True
        or placement.get("store_content_validated") is not False
        or placement.get("live_authority") is not False
        or type(placement.get("placement_generation")) is not int
        or placement["placement_generation"] < 1
    ):
        raise AgendaError("placement owner returned an invalid scope or placement qualification")
    home_value = placement.get("state_home")
    if not isinstance(home_value, str) or not Path(home_value).is_absolute():
        raise AgendaError("placement owner omitted an absolute state home")
    home = safe_path(Path(home_value), directory=True)
    if not home.is_dir():
        raise AgendaError("placement owner did not allocate its state home")
    store_files = {DATABASE + suffix for suffix in ("", "-journal", "-wal", "-shm")}
    if any(path.name not in store_files for path in home.iterdir()):
        try:
            with transaction(home, readonly=True, store_version=2) as db:
                existing = _state(db, home)
                if existing["store_id"] != store_id or existing["snapshot"]["worktree"] != worktree:
                    raise AgendaError("existing accepted store does not match the placement")
        except AgendaError as exc:
            raise AgendaError("managed initialization refuses a nonempty uninitialized or unqualified home") from exc
    result, accepted = mutate(
        home, request, actor=actor, session=session, initial_store_id=store_id,
    )
    if result["store_id"] != store_id or result["source"]["worktree"] != worktree:
        raise AgendaError("initialized agenda identity differs from its admitted placement")
    return {"schema": "efforts.agenda-managed-placement", "version": 1,
            "placement": placement, "result": result, "accepted": accepted}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("info", allow_abbrev=False)
    init = commands.add_parser("init", allow_abbrev=False)
    for option in ("provider-python", "project", "worktree", "creation-nonce", "lane", "store-id"):
        init.add_argument(f"--{option}", required=True)
    init.add_argument("--request", type=Path)
    init.add_argument("--actor")
    init.add_argument("--session")
    args = parser.parse_args(argv)
    try:
        if args.command == "info":
            output = {"schema": "efforts.agenda-managed-placement", "version": 1,
                      "capabilities": [CAPABILITY]}
        else:
            request = (_read(safe_path(args.request)) if args.request else
                       decode(sys.stdin.buffer.read(131073), "request"))
            output = initialize(
                args.provider_python, args.project, args.worktree, args.creation_nonce,
                args.lane, args.store_id, request, actor=args.actor, session=args.session,
            )
        print(json.dumps(output, ensure_ascii=True, allow_nan=False, sort_keys=True))
        return 0
    except (AgendaError, OSError, BrokenPipeError) as exc:
        print(f"agenda managed placement: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
