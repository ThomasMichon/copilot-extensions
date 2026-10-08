"""CLI shim for ``worktree-manager daemons ...`` (Phase 1 of #5001)."""

from __future__ import annotations

import json


def _reject_unsupported_args(args: list[str], *, allowed: tuple[str, ...]) -> list[str] | None:
    """Return ``args`` unchanged, or print an error and ``None`` if any
    token isn't in ``allowed``. Without this, a typo (``--jsoon``) was
    silently ignored rather than rejected -- a scripted caller expecting
    JSON on a flag it misspelled would instead get text output and exit 0,
    with no signal anything went wrong."""
    unsupported = [a for a in args if a not in allowed]
    if unsupported:
        print(f"error: unsupported option(s): {' '.join(unsupported)}")
        return None
    return args


def cmd_daemons(rest: list[str]) -> int:
    args = list(rest)
    if not args:
        print("usage: worktree-manager daemons <status|mappings> [--json]")
        return 2
    action = args.pop(0)
    if action == "status":
        from .daemons_status import daemon_statuses

        if _reject_unsupported_args(args, allowed=("--json",)) is None:
            return 2
        json_mode = "--json" in args
        statuses = daemon_statuses()
        if json_mode:
            print(json.dumps(statuses, indent=2))
            return 0
        if not statuses:
            print("  no resident mux-daemons found.")
            return 0
        print("  resident mux-daemons:")
        for entry in statuses:
            marker = "*" if entry.get("active") else " "
            port = entry.get("port")
            port_s = str(port) if port is not None else "?"
            status = entry.get("status", "unknown")
            bits = [f"pid {entry['pid']}", f"port {port_s}", status]
            version = entry.get("version")
            if version:
                bits.append(f"version {version}")
            attached = entry.get("attached_clients")
            if attached is not None:
                bits.append(f"attached {attached}")
            if entry.get("busy"):
                bits.append("busy")
            if entry.get("telemetry") == "unsupported":
                bits.append("telemetry unavailable (pre-upgrade daemon)")
            print(f"    {marker} " + " · ".join(bits))
        print()
        print("  (* = routing table's current active endpoint)")
        return 0
    if action == "mappings":
        from .daemons_status import mapping_statuses

        if _reject_unsupported_args(args, allowed=("--json",)) is None:
            return 2
        json_mode = "--json" in args
        mappings = mapping_statuses()
        if json_mode:
            print(json.dumps(mappings, indent=2))
            return 0
        if not mappings:
            print("  no known mux-session mappings for this root.")
            return 0
        print("  known mux-session mappings (root-wide, not per-daemon; includes tombstoned/removed entries):")
        for entry in mappings:
            marker = "*" if entry.get("live") else " "
            bits = [
                f"{entry['project']}/{entry['worktree_id']}",
                f"session {entry.get('mux_session')}",
                f"attached {entry.get('attached_clients', 0)}",
            ]
            print(f"    {marker} " + " · ".join(bits))
        print()
        print("  (* = registry's own 'live' bit -- NOT confirmed liveness; a dead")
        print("   session can retain live: true until the next real probe. Shared")
        print("   across every resident daemon for this root, not attributable to")
        print("   one specific pid)")
        print("  (attached_clients is refreshed by the daemon's live-mapping cycle")
        print("   (20-second backstop), independently of status-option changes.")
        print("   Failed probes preserve the last count; a new mapping may read 0")
        print("   until its first successful observation)")
        return 0
    print(f"error: unknown daemons action {action!r}")
    return 2
