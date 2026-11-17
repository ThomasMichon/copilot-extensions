"""Phase 1 observability for resident mux-daemons (copilot-extensions#5001).

Resident per-version mux-daemons accumulate indefinitely today: a cutover is
only attempted opportunistically, and a superseded daemon with even one
still-attached client blocks its own retirement forever (see the issue for
the full background). Before any retirement sweep can be built (later
phases), there needs to be ground truth on what is actually resident right
now -- this module enumerates every resident mux-daemon matched to a given
root and reports its identity (pid/port), whether it is the routing table's
current active endpoint, and -- for a reachable one -- its own reported
version, attached-client count, and busy state.

This is read-only: it never drains, terminates, or otherwise mutates any
daemon. Phases 2+ (retirement) are tracked separately in the same issue.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from zdd import routing
from zdd.diagnostics import process_start_time

from . import mux_daemon_cutover
from .self_install import default_root


def _cmdline_for_pid(pid: int) -> str:
    """Command line for diagnostic root/port matching, not mutation authority."""
    if os.name == "nt":
        from .windows_daemon_observation import process_command_line

        try:
            return process_command_line(pid)
        except OSError:
            return ""
    proc_cmdline = Path(f"/proc/{pid}/cmdline")
    if proc_cmdline.is_file():
        try:
            parts = [part for part in proc_cmdline.read_text("utf-8").split("\0") if part]
            if parts:
                return " ".join(parts)
        except OSError:
            pass
    out = subprocess.run(  # noqa: S603
        ["ps", "-p", str(pid), "-o", "args="],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
        env=mux_daemon_cutover._ps_env_without_width_override(),
    )
    return (out.stdout or "").strip()


def _parse_listen_port(cmdline: str) -> int | None:
    for token in cmdline.split():
        token = token.strip("\"'")
        if token.startswith("--listen-port="):
            try:
                return int(token.split("=", 1)[1])
            except ValueError:
                return None
    return None


def _current_user_sid() -> str | None:
    """The current OS user's Windows SID, read from its native process token.

    Never derived from ``USERDOMAIN``/``USERNAME``: those are ordinary
    environment variables, trivially overridable by whatever spawned this
    process, so comparing against them would let a caller simply set a
    matching value and defeat the ownership check entirely. A SID is the
    OS's own non-spoofable identity primitive.
    """
    from .windows_daemon_observation import current_user_sid

    try:
        return current_user_sid() or None
    except OSError:
        return None


def _pid_owned_by_current_user(pid: int, *, current_sid: str | None = "") -> bool:
    """Whether ``pid`` is owned by the OS account running this process.

    Required before sending the cutover control token to a recovered port:
    without this check, another local account could run a lookalike
    process that reports the same ``--root=`` value in its own command
    line and listens on a port, and a blind probe here would hand it our
    bearer token. Only a verified same-owner process is ever probed; a
    candidate whose ownership can't be confirmed (including a failed or
    timed-out probe itself) is reported without a connection attempt.

    ``current_sid`` (Windows only) lets a caller checking many pids in one
    pass (:func:`daemon_statuses`) resolve the caller's own token SID once
    and reuse it.
    The default sentinel (``""``, never a real SID) means "resolve it fresh
    for this one call" -- so a direct caller never has to know about this
    parameter at all.
    """
    if os.name == "nt":
        from .windows_daemon_observation import process_user_sid

        try:
            owner_sid = process_user_sid(pid)
        except OSError:
            return False
        resolved_sid = current_sid if current_sid != "" else _current_user_sid()
        # Both sides are required to be genuinely non-empty: a failed
        # lookup on either side must never compare equal to another failed
        # lookup (two blank strings would otherwise match each other).
        return (
            bool(owner_sid)
            and bool(resolved_sid)
            and owner_sid.lower() == resolved_sid.lower()
        )
    proc_dir = Path(f"/proc/{pid}")
    if proc_dir.is_dir():
        try:
            return proc_dir.stat().st_uid == os.getuid()
        except OSError:
            return False
    try:
        out = subprocess.run(  # noqa: S603
            ["ps", "-p", str(pid), "-o", "uid="],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    try:
        return int((out.stdout or "").strip()) == os.getuid()
    except ValueError:
        return False


def _process_start_time(pid: int) -> str | None:
    """Native Windows birth, or the shared helper plus a macOS/BSD fallback.

    The shared ``zdd`` helper only implements Windows (``GetProcessTimes``)
    and Linux (``/proc/<pid>/stat``) -- it returns ``None`` on macOS and
    other ``/proc``-less POSIX systems, which would otherwise make every
    reachable daemon on those platforms permanently ``"unverified-owner"``
    (a None/None pair is deliberately never treated as a match; see
    ``daemon_statuses``'s own post-connection re-check). ``ps -o lstart=``
    gives a stable, second-resolution start-time string on any BSD-flavored
    ``ps`` (including macOS) -- not foolproof against a same-second PID
    reuse, but strictly better than treating every macOS daemon as
    unverifiable. Extending the shared ``zdd`` library itself with a real
    macOS implementation is out of scope here (it has several other
    consumers); this stays a local, narrowly-scoped supplement.
    """
    if os.name == "nt":
        from .windows_daemon_observation import process_start_time as native_start_time

        try:
            return native_start_time(pid)
        except OSError:
            return None
    token = process_start_time(pid)
    if token is not None or os.name == "nt" or Path(f"/proc/{pid}").is_dir():
        return token
    try:
        out = subprocess.run(  # noqa: S603
            ["ps", "-p", str(pid), "-o", "lstart="],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    value = (out.stdout or "").strip()
    return value or None


def _iter_status_pids() -> set[int]:
    if os.name == "nt":
        from .windows_daemon_observation import mux_daemon_pids

        return mux_daemon_pids()
    return mux_daemon_cutover._iter_mux_daemon_pids()


def _status_pid_matches_root(pid: int, *, root: Path) -> bool:
    if os.name != "nt":
        return mux_daemon_cutover._pid_matches_root(pid, root=root)
    command = _cmdline_for_pid(pid)
    if not command:
        return False
    recorded = mux_daemon_cutover._cmdline_root(command)
    resolved = root.expanduser().resolve()
    return recorded == resolved or (
        recorded is None and resolved == default_root().expanduser().resolve()
    )


def daemon_statuses(root: Path | None = None) -> list[dict[str, Any]]:
    """Enumerate every resident mux-daemon matched to ``root``.

    Each entry always carries ``pid``, ``port`` (``None`` if it couldn't be
    recovered from the process's own command line), and ``active`` (whether
    the routing table's current endpoint points at this pid). When ``port``
    and a reachable control endpoint are both available, the entry is
    enriched with the daemon's own self-reported ``status``/``version``/
    ``attached_clients``/``busy``; otherwise ``status`` is ``"unreachable"``
    (a live health request failed), ``"unverified-owner"`` (the candidate's
    OS process owner could not be confirmed to be the current user, so the
    control token was never sent to it), or ``"unknown"`` (no port could be
    recovered at all).
    """
    resolved_root = root if root is not None else default_root()
    table = routing.read_table(mux_daemon_cutover.routing_dir(resolved_root))
    active_raw = table.get("active") if isinstance(table, dict) else None
    active_endpoint = (
        routing.Endpoint.from_dict(active_raw) if isinstance(active_raw, dict) else None
    )
    # Current-user identity is stable within this observation pass.
    current_sid = _current_user_sid() if os.name == "nt" else None

    results: list[dict[str, Any]] = []
    for pid in sorted(_iter_status_pids()):
        if not _status_pid_matches_root(pid, root=resolved_root):
            continue
        cmdline = _cmdline_for_pid(pid)
        port = _parse_listen_port(cmdline)
        start_time_before = None
        if (
            port is None
            and active_endpoint is not None
            and active_endpoint.pid == pid
            and active_endpoint.process_start_time is not None
            and active_endpoint.client_host == "127.0.0.1"
            and 0 < active_endpoint.port < 65536
        ):
            start_time_before = _process_start_time(pid)
            if start_time_before == active_endpoint.process_start_time:
                port = active_endpoint.port
        entry: dict[str, Any] = {
            "pid": pid,
            "port": port,
            # Matched on BOTH pid and port: pid alone can misattribute
            # "active" after PID reuse -- the routing table's active row
            # could retain a now-stale endpoint while an unrelated, later
            # daemon happens to reuse that exact pid on a different port.
            # A routed port requires a matching birth above; an unresolved
            # port never matches an unverifiable candidate.
            "active": bool(
                active_endpoint is not None
                and active_endpoint.pid == pid
                and active_endpoint.port == port
            ),
        }
        if port is None:
            entry["status"] = "unknown"
            results.append(entry)
            continue
        if not _pid_owned_by_current_user(pid, current_sid=current_sid):
            entry["status"] = "unverified-owner"
            results.append(entry)
            continue
        # Captured before connecting so the post-connection re-check below
        # can detect a same-pid replacement in the gap.
        if start_time_before is None:
            start_time_before = _process_start_time(pid)
        try:
            client = mux_daemon_cutover.ControlClient(
                f"http://{routing.format_authority('127.0.0.1', port)}",
                root=resolved_root,
                timeout=3.0,
            )
            health = client.health()
        except Exception:
            entry["status"] = "unreachable"
            results.append(entry)
            continue
        # Re-verify identity immediately after the connection narrows (it
        # does not eliminate) the window between the ownership check above
        # and the actual TCP handshake that disclosed the control token:
        # the verified daemon could have exited and a DIFFERENT, unrelated
        # local account's process bound that exact port in between. Confirm
        # the same pid is still alive, still resolves to this root, is
        # still owned by the current user, and never changed its own start
        # time (ruling out a same-pid replacement too). A genuine fix needs
        # the wire protocol itself to authenticate its peer before
        # disclosing the token -- out of scope here since it touches every
        # ControlClient caller (drain/undrain/shutdown/adopt too), not just
        # this read-only surface; tracked as follow-on work under #5001.
        try:
            still_live = pid in _iter_status_pids()
            still_matches_root = still_live and _status_pid_matches_root(
                pid, root=resolved_root
            )
            start_time_after = _process_start_time(pid) if still_matches_root else None
        except Exception:
            still_live = still_matches_root = False
            start_time_after = None
        # A ``None`` start time (identity lookup unavailable on this
        # platform, e.g. macOS's POSIX path) must never compare equal to
        # another ``None`` -- that would silently accept an unprovable
        # identity as "unchanged" and defeat this whole re-check. Both
        # samples must be genuine, non-``None`` tokens that also match.
        start_time_confirmed = (
            start_time_before is not None
            and start_time_after is not None
            and start_time_before == start_time_after
        )
        if (
            not still_matches_root
            or not start_time_confirmed
            or not _pid_owned_by_current_user(pid, current_sid=current_sid)
        ):
            entry["status"] = "unverified-owner"
            results.append(entry)
            continue
        entry["status"] = health.get("status", "unknown")
        # A reachable daemon running code older than this feature (exactly
        # the long-resident stale daemons #5001 is about) answers with the
        # old, smaller health shape -- no "version" key at all. Representing
        # that as null version/attached_clients/busy would misleadingly look
        # like "live, measured, and idle" rather than "can't be measured";
        # mark it explicitly unsupported instead so a caller (and the text
        # renderer) can tell the two apart.
        if "version" in health:
            entry["version"] = health.get("version")
            entry["attached_clients"] = health.get("attached_clients")
            entry["busy"] = health.get("busy")
        else:
            entry["telemetry"] = "unsupported"
        results.append(entry)
    return results


def mapping_statuses(root: Path | None = None) -> list[dict[str, Any]]:
    """Every mux-session mapping this root's Manager has ever registered.

    This is a PRELIMINARY registry-listing command, not the issue's full
    Phase 1 "attribution slice" -- it does not, and cannot, correlate a
    live wire connection to a specific project/worktree_id/mux_session
    identity or that connection's own busy state (no client_id encoding
    caller identity exists anywhere in the current wire protocol; see
    ``daemon_statuses()``'s own scope note for the same limitation applied
    to per-daemon load). What it DOES give, read straight from the
    Manager's own disk-backed registry (``mux_mapping_registry.
    MuxMappingRegistry.snapshot()`` -- shared across every resident daemon
    for this root, not owned by one specific pid): which mappings the
    Manager has ever registered, each one's own ``live`` flag, and its
    registry-tracked ``attached_clients`` count.

    **``attached_clients`` freshness (#4564 -- PARTIAL fix, writer-side
    only):** neither shipped launch path (``bin/launch-session.sh``/``.ps1``)
    ever passes ``--attached-clients`` at register() time, so normalization
    (``_normalize_mapping_entry``) defaults a brand-new mapping to ``0``.
    The resident daemon attempts refreshes (``list-clients``) on an independent
    20-second cadence, even when the status monitor is absent or publications
    fail, and independently of changes to status option values.
    At most two mappings are probed
    per cycle, round-robin, within a shared two-second budget; large fleets
    converge over multiple cycles. This function still
    reports whatever is currently stored exactly as-is (it never invents
    or estimates); a brand-new mapping awaiting its first successful
    observation, a stopped daemon, or a failed probe can leave the stored
    count stale. This implements only the attached-client observation portion
    of #4564's proposed step 1.
    #4564 also proposes a distinct ``reachable`` field (preserving the
    last-known ``live``/``attached_clients`` values across an unreachable
    session rather than this registry's current tombstone-on-death
    behavior) and a batch "restore what I had open" verb -- neither is
    implemented here; both remain open, tracked follow-on work under the
    same issue.

    This is NOT filtered to currently-live mappings either: ``snapshot()``
    also returns tombstoned entries (``live: False``), which the registry
    keeps indefinitely by design (see ``MuxMappingRegistry.remove()``'s own
    docstring for why -- a tombstone fences a monotonic revision guard, not
    something safe to drop). Each entry's own ``live`` field is exactly how
    a caller tells the two apart; this function never filters it away.

    No per-mapping ``busy`` field exists either: the registry tracks only
    ``live``/``attached_clients`` (whether/how many mux-session attachments
    are currently known), never a per-worktree activity/busy concept --
    that notion exists only at the per-DAEMON level (this module's
    ``daemon_statuses()``'s own ``busy``, reflecting that process's active
    control-wire handlers), which cannot be attributed back to one specific
    mapping either (see the same limitation noted on the daemon list).
    """
    from .mux_mapping_registry import MuxMappingRegistry, registry_path

    resolved_root = root if root is not None else default_root()
    registry = MuxMappingRegistry(registry_path(resolved_root))
    results: list[dict[str, Any]] = []
    for (project, worktree_id), entry in sorted(registry.snapshot().items()):
        results.append(
            {
                "project": project,
                "worktree_id": worktree_id,
                "mux_session": entry.get("mux_session"),
                "live": bool(entry.get("live")),
                "attached_clients": entry.get("attached_clients", 0),
                "observed_at": entry.get("observed_at"),
            }
        )
    return results
