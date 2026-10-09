"""``agent-bridge attention <session>...``: each session's current attention
reason, read once and never waited on (``wait --attention`` is the wait).

Serves bridge-managed sessions and registered interactive ones alike. The read
needs a daemon that speaks ``CURRENT_ATTENTION_PROTOCOL_VERSION``; against an
older one every session reports ``unsupported`` and no request is sent.

``--json`` (the global option, so ``agent-bridge --json attention <s>``) prints
``{"schema": 1, "sessions": [{"ref", "status", ...}]}``, one entry per operand in
order. ``status`` is ``ok`` (with the daemon's ``reason`` -- ``null`` when
nothing is pending -- ``availability``, ``registry``, ``session_id``,
``worktree_id``, ``fidelity`` and ``detail``), ``not_found``, ``unsupported`` or
``error`` (with ``error``). It exits 0 once every operand has an entry; an
unreachable daemon exits as every other command does.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

SCHEMA = 1
_ERROR_MAX = 200


def _core():
    from . import __main__ as core

    return core


def _one_line(value: Any) -> str:
    return " ".join(str(value or "").split())[:_ERROR_MAX]


def _current_attention(client: Any, session_ref: str) -> dict[str, Any]:
    """``GET /api/v1/sessions/{ref}/attention/current``: a session's current
    attention reason, never a wait (the caller gates on the protocol)."""
    from urllib.parse import quote

    return client._request("GET", f"/api/v1/sessions/{quote(session_ref, safe='')}/attention/current") or {}


def read_current_attention(client: Any, refs: list[str]) -> list[dict[str, Any]]:
    """One entry per ref, in order (see the module docstring)."""
    from .client import BridgeClientError
    from .protocol import CURRENT_ATTENTION_PROTOCOL_VERSION

    if not client.daemon_supports(CURRENT_ATTENTION_PROTOCOL_VERSION):
        version, _minimum = client.daemon_protocol()
        error = (f"the daemon speaks HTTP protocol v{version}; reading current attention "
                 f"needs v{CURRENT_ATTENTION_PROTOCOL_VERSION}")
        return [{"ref": ref, "status": "unsupported", "error": error} for ref in refs]
    entries = []
    for ref in refs:
        try:
            body = _current_attention(client, ref)
        except BridgeClientError as exc:
            status = "not_found" if exc.status == 404 else "error"
            entries.append({"ref": ref, "status": status, "error": _one_line(exc.detail) or str(exc)})
            continue
        body.pop("requested_ref", None)
        entries.append({"ref": ref, "status": "ok", **body})
    return entries


def _cmd_attention(args: argparse.Namespace) -> None:
    core = _core()
    client = core._get_client(ensure=False)
    entries = read_current_attention(client, list(args.sessions))
    if args.json:
        core._json_out({"schema": SCHEMA, "sessions": entries})
        return
    for entry in entries:
        if entry["status"] != "ok":
            print(f"  {entry['ref']}: {entry['status']} -- {entry.get('error')}", file=sys.stderr)
            continue
        reason = entry.get("reason") or "nothing pending"
        late = " (unknown after restart)" if entry.get("availability") == "unknown_after_restart" else ""
        detail = f" -- {entry['detail']}" if entry.get("detail") else ""
        print(f"  {entry['ref']} [{entry.get('registry')} {entry.get('session_id')}]: {reason}{late}{detail}")


def register_attention_commands(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "attention",
        help="Each session's current attention reason (input/permission/policy required, failed, ...), "
             "read once without waiting, for bridge-managed and registered interactive sessions",
    )
    p.add_argument("sessions", nargs="+", metavar="session",
                   help="Session ids, or worktree handles (repeatable)")
    # SUPPRESS: never overwrite the top-level --json ('agent-bridge --json attention <s>').
    p.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Emit JSON.")
    p.set_defaults(func=_cmd_attention)
