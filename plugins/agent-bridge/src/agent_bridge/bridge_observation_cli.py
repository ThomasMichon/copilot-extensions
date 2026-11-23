"""Opt-in exact, cursor-neutral observations of an existing local bridge."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import sys
from typing import Any


@dataclass(frozen=True)
class _Target:
    session_id: str
    represented: bool
    native_session_id: str


def selected(args: argparse.Namespace) -> bool:
    """Keep the legacy selector until callers explicitly adopt the new contract."""
    enabled = bool(getattr(args, "bridge", False))
    if not enabled and any(
        getattr(args, key, None) for key in ("all_projects", "machine", "namespace")
    ):
        raise SystemExit("--all-projects, --machine and --namespace require --bridge")
    return enabled


def _resolve(client: Any, args: argparse.Namespace) -> _Target:
    from .bridge_listing_cli import _scope
    from .client import BridgeClientError

    row = client.resolve_bridge(args.session_id, local_only=True, **_scope(args))["bridge"]
    native = row.get("session_id")
    if not isinstance(native, str) or not native:
        raise BridgeClientError(409, "bridge_observation_unavailable")
    managed = row.get("managed_session_id")
    represented = row.get("registered_session_id")
    retained = row.get("retained_managed_session_id")
    for session_id, is_represented in ((managed, False), (represented, True), (retained, False)):
        if isinstance(session_id, str) and session_id:
            return _Target(session_id, is_represented, native)
    raise BridgeClientError(409, "bridge_observation_unavailable")


def _snapshot(client: Any, target: _Target, **bounds: Any) -> dict[str, Any]:
    from .client import BridgeClientError

    method = client.get_live_result_snapshot if target.represented else client.get_result_snapshot
    payload: dict[str, Any] = method(
        target.session_id,
        require_native_identity=True,
        **bounds,
    )
    identity = payload.get("identity") or {}
    if (
        identity.get("snapshot_session_id") != target.session_id
        or identity.get("native_session_id") != target.native_session_id
    ):
        raise BridgeClientError(409, "bridge_observation_identity_changed")
    return payload


def read(client: Any, args: argparse.Namespace) -> None:
    """Read the selected conversation without hydration or delivery-cursor changes."""
    from . import __main__ as core
    from .session_streaming_cli import _render_result_snapshot

    if any(getattr(args, key, None) is not None for key in ("range", "event", "since")):
        raise SystemExit(
            "--bridge reads bounded snapshots, not ACP event ranges; omit --bridge for legacy event reads"
        )
    if getattr(args, "message_id", None) is not None:
        raise SystemExit(
            "--bridge does not inspect message receipts; use the existing receipt command"
        )
    target = _resolve(client, args)
    tail = getattr(args, "tail", None)
    payload = _snapshot(
        client,
        target,
        max_items=tail if tail is not None else getattr(args, "max_items", None),
        max_text_chars=getattr(args, "max_text_chars", None),
    )
    if args.json:
        core._json_out(payload)
    else:
        _render_result_snapshot(payload, args.session_id)


def wait(args: argparse.Namespace) -> None:
    """Observe idle or terminal state without sending, resuming or following a successor."""
    from . import __main__ as core
    from .client import BridgeClientError, BridgeConnectionError
    from .client_read_budget import ReadDeadlineExpired, command_deadline, pause, remaining

    if any(getattr(args, key, None) for key in ("attention", "all_attention", "position")):
        raise SystemExit(
            "--bridge supports turn-only observation; omit --bridge for owned-session attention waits"
        )
    timeout = core._phased_timeouts().command
    payload: dict[str, Any] | None = None
    with command_deadline(timeout):
        try:
            client = core._get_client(ensure=False)
            remaining()
            target = _resolve(client, args)
            while True:
                remaining()
                observed = _snapshot(client, target, max_items=1, max_text_chars=256)
                remaining()
                registration = (
                    client.get_live_session(target.session_id) if target.represented else None
                )
                remaining()
                if (
                    registration is not None
                    and registration.get("session_id") != target.session_id
                ):
                    raise BridgeClientError(409, "bridge_observation_identity_changed")
                payload = observed
                identity = payload["identity"]
                state = payload["state"]
                status = (registration or {}).get("status") or state["session_status"]
                if (
                    identity.get("successor_id")
                    or identity.get("current_session_id") != target.session_id
                ):
                    reason = "identity_changed"
                elif status in {"stopped", "ended", "failed", "expired", "taken-over", "wedged"}:
                    reason = str(status)
                elif (
                    state.get("at_rest") is True
                    and state["session_status"] == "idle"
                    and (
                        registration is None
                        or (
                            registration.get("status") == "live"
                            and registration.get("turn_state") != "running"
                        )
                    )
                ):
                    reason = "idle"
                else:
                    pause(core._RECONNECT_BACKOFF)
                    continue
                break
        except ReadDeadlineExpired:
            reason = "timeout"
        except (OSError, BridgeClientError, BridgeConnectionError):
            try:
                remaining()
            except ReadDeadlineExpired:
                reason = "timeout"
            else:
                raise
    result = {"settled": reason != "timeout", "reason": reason, "snapshot": payload}
    if args.json:
        core._json_out(result)
    else:
        print(f"[>] Bridge {args.session_id}: {reason}", file=sys.stdout)


def register(parser: argparse.ArgumentParser) -> None:
    """Add explicit adoption and project-scope selectors without changing legacy forms."""
    parser.add_argument(
        "--bridge",
        action="store_true",
        help="Use exact local bridge identity and cursor-neutral bounded observations (requires bridge_inventory: 2 and result_native_identity: 1)",
    )
    parser.add_argument("--all-projects", action="store_true")
    parser.add_argument("--machine", help="Filter the conversation's machine (with --bridge)")
    parser.add_argument("--namespace", help="Filter the conversation's namespace (with --bridge)")
