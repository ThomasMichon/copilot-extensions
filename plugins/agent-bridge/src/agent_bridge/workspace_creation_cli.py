"""Caller-keyed creation over the existing session-start client."""

from __future__ import annotations

import argparse
import sys


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--min-daemon-protocol", type=int, default=None,
                        help="Require this protocol before each keyed creation request")
    parser.add_argument("--creation-key", help="opaque logical creation identity; exact local workspace/session retries retain the original result")
    parser.add_argument("--provider-instance", action="store_true",
                        help="keyed native creation on an exact provider-exec instance, without a worktree")
    parser.add_argument("--replacement-task", help="task whose current control grant authorizes this native replacement")


def validate_arguments(args: argparse.Namespace) -> None:
    if getattr(args, "replacement_task", None) is not None and (
        not getattr(args, "creation_key", None) or not getattr(args, "worktree_id", None)
        or getattr(args, "provider_instance", False)
    ):
        print("[FAIL] --replacement-task requires --creation-key and --worktree-id.", file=sys.stderr)
        sys.exit(2)
    if getattr(args, "provider_instance", False) and getattr(args, "creation_key", None) is None:
        print("[FAIL] --provider-instance requires --creation-key.", file=sys.stderr)
        sys.exit(2)
    if getattr(args, "creation_key", None) is not None and getattr(args, "cli", False):
        print("[FAIL] --creation-key is not supported by CLI-mode venue creation.", file=sys.stderr)
        sys.exit(2)


def create(args: argparse.Namespace, client, *, caller_id: str | None) -> None:
    from . import __main__ as core
    from .client import BridgeClientError
    from .protocol import PROVIDER_CREATION_PROTOCOL_VERSION, WORKSPACE_CREATION_PROTOCOL_VERSION
    from .session_start_cli import _prepare_sender_preference, _read_agent_inventory
    from .session_targeting_cli import (
        _companion_seed_prompt, _hold_protocol_floor, _resolve_prompt,
        _write_session_id_file,
    )

    provider_instance = getattr(args, "provider_instance", False)
    _hold_protocol_floor(
        client, PROVIDER_CREATION_PROTOCOL_VERSION if provider_instance else WORKSPACE_CREATION_PROTOCOL_VERSION,
    )
    if getattr(args, "min_daemon_protocol", None):
        _hold_protocol_floor(client, args.min_daemon_protocol)
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
        provider_instance=provider_instance,
        **({"replacement_task": args.replacement_task}
           if getattr(args, "replacement_task", None) is not None else {}),
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
        if "worktree" in creation:
            ident["target_dir"] = creation["worktree"]["path"]
        else:
            ident["venue"] = creation["venue"]
        ready = session.get("status") == "idle" and session.get("turn_count") == 0
        core._json_out({
            **result, "connection": ident, "ready": ready,
            "status": "awaiting_first_prompt" if ready else session.get("status"),
        })
    else:
        identity = (
            f"workspace {creation['worktree']['id']}" if "worktree" in creation
            else f"provider instance {creation['venue']['instance_id']}"
        )
        print(f"[OK] Session {session_id} -- {identity}")
