"""Ordinary send addressing, without session creation or lifecycle effects."""

from __future__ import annotations

from dataclasses import dataclass

from .send_outcome import SendRefused


def normalize_arguments(args) -> None:
    if getattr(args, "force", False):
        raise SendRefused(
            "refused_invalid", reason="lifecycle_option", retryable=False,
            target=getattr(args, "target", None) or getattr(args, "agent", None),
            error="Raw send cannot replace a receiver; use an explicit lifecycle command first.",
        )
    if getattr(args, "queue", False):
        args.delivery = "queue"
    target = getattr(args, "target", None)
    agent = getattr(args, "agent", None)
    message = getattr(args, "message", None)
    if agent is not None:
        if target is not None:
            raise SendRefused(
                "refused_invalid", reason="conflicting_target", retryable=False,
                target=target, error="Pass either TARGET or --agent, not both.",
            )
        args.target = agent
    if not getattr(args, "target", None):
        raise SendRefused(
            "refused_invalid", reason="missing_target", retryable=False,
            error="Provide TARGET or --agent.",
        )
    if message is not None:
        if getattr(args, "prompt", None) is not None or getattr(args, "prompt_file", None):
            raise SendRefused(
                "refused_invalid", reason="conflicting_message", retryable=False,
                target=args.target,
                error="Pass --message, a positional prompt, or --prompt-file, not more than one.",
            )
        args.prompt = message


@dataclass(frozen=True)
class RemoteLiveTarget:
    machine: str
    target: str

    def __str__(self) -> str:
        return self.target


def remote_target(args) -> RemoteLiveTarget | None:
    machine = getattr(args, "machine", None)
    if machine is None:
        return None
    if not machine.strip():
        raise SendRefused(
            "refused_invalid", reason="invalid_machine", retryable=False,
            target=args.target, error="--machine requires a nonempty machine name.",
        )
    if any(getattr(args, field, None) for field in (
        "expected_native_session_id", "native_invocation_file", "receipt_only",
        "live_exact_notify", "live_receipt_only", "live_exact", "plugin_context_stdin",
        "startup_communication_timeout", "startup_process_timeout",
    )):
        raise SendRefused(
            "refused_invalid", reason="remote_options", retryable=False,
            target=args.target, error="--machine requires an ordinary live-session send.",
        )
    from .live_peer_address import LivePeerAddress

    try:
        qualified = LivePeerAddress.parse(args.target)
    except ValueError as exc:
        raise SendRefused(
            "refused_invalid", reason="invalid_address", retryable=False,
            target=args.target, error=str(exc),
        ) from exc
    if qualified is not None and qualified.machine.casefold() != machine.casefold():
        raise SendRefused(
            "refused_invalid", reason="conflicting_machine", retryable=False,
            target=args.target, error="--machine conflicts with the qualified target's machine.",
        )
    return RemoteLiveTarget(machine, args.target)


def resolve_existing_session(client, target: str) -> str:
    from .client import BridgeClientError

    session = None
    try:
        session = client.get_session(target)
    except BridgeClientError as exc:
        if exc.status != 404:
            raise
    if session:
        matches = [session]
    else:
        matches = [
            row for row in getattr(client, "list_sessions", lambda: [])()
            if target == row.get("worktree_id")
            or target.casefold() == str(row.get("agent_name") or "").casefold()
        ]
    live = [row for row in matches if row.get("status") in ("idle", "running")]
    if len(live) > 1:
        raise SendRefused(
            "refused_unavailable", reason="ambiguous_target", retryable=False,
            target=target, error="Several live sessions match; use an exact session or worktree id.",
        )
    if not live:
        raise SendRefused(
            "refused_unavailable", reason="not_found", retryable=False,
            target=target,
            error=(f"No live receiver for {target!r}; explicitly resume a dormant target before sending, "
                   "or check receiver registration if it is already running."),
        )
    return live[0]["session_id"]
