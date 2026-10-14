"""Caller-keyed creation over the existing session-start client."""

from __future__ import annotations

import argparse


def create(args: argparse.Namespace, client, *, caller_id: str | None) -> None:
    from . import __main__ as core
    from .client import BridgeClientError
    from .protocol import WORKSPACE_CREATION_PROTOCOL_VERSION
    from .session_start_cli import _prepare_sender_preference, _read_agent_inventory
    from .session_targeting_cli import (
        _companion_seed_prompt, _hold_protocol_floor, _resolve_prompt,
        _write_session_id_file,
    )

    _hold_protocol_floor(client, WORKSPACE_CREATION_PROTOCOL_VERSION)
    inventory = _read_agent_inventory(client)
    preference = _prepare_sender_preference(client, args.target, agent_inventory=inventory)
    prompt = _companion_seed_prompt(_resolve_prompt(args, required=False))
    result = client.start_session(
        agent=args.target, caller_id=caller_id,
        caller_session_id=core._get_caller_session_id(),
        sender_repo=preference.project, caller_owner_ref=core._worktrees_get("owner-ref"),
        worktree_id=getattr(args, "worktree_id", None),
        target_dir=getattr(args, "target_dir", None),
        charter=getattr(args, "charter", None), model=getattr(args, "model", None),
        effort=getattr(args, "effort", None), force_new=True,
        creation_key=args.creation_key, creation_prompt=prompt,
        request_timeout=core._startup_request_timeout(),
    )
    creation = result.get("creation")
    if not isinstance(creation, dict) or creation.get("status") != "complete":
        raise BridgeClientError(409, "bridge did not prove keyed embodiment; retain the original creation key")
    session_id = result["session_id"]
    if path := getattr(args, "session_id_file", None):
        _write_session_id_file(path, session_id)
    if prompt:
        core._submit_and_stream(
            client, args, session_id, prompt, caller_id=caller_id,
            submitted_result={**result, "turn_index": creation["turn_index"], "queued": False},
        )
    elif args.json:
        session = client.get_session(session_id)
        ident = core._connection_identity(client, session_id)
        ident["native_session_id"] = creation["native_session_id"]
        ident["target_dir"] = creation["worktree"]["path"]
        ready = session.get("status") == "idle" and session.get("turn_count") == 0
        core._json_out({
            **result, "connection": ident, "ready": ready,
            "status": "awaiting_first_prompt" if ready else session.get("status"),
        })
    else:
        print(f"[OK] Session {session_id} -- workspace {creation['worktree']['id']}")
