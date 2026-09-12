"""Append-only worktree activity log -- high-level lifecycle events.

Records the high-level lifecycle of worktrees and their Copilot/mux
sessions to a machine-global JSONL file at
``~/.agent-worktrees/logs/activity.jsonl``.  Unlike the per-PID launcher
setup logs under ``$TMPDIR/worktree-setup-logs`` (capped at the 10 newest
and wiped on reboot), this log persists across reboots and accumulates a
rolling window of history (default 7 days), so session-lifecycle anomalies
-- e.g. a finalized worktree whose tmux/Copilot session is never reaped --
can be reconstructed after the fact.

Both the Python lifecycle code and the bash launcher append here (the
launcher via ``agent-worktrees activity-log``), so a single file captures
the full picture across processes.

Events are intentionally high-level:

  worktree_created          a new worktree + branch was created
  worktree_resumed          an existing worktree was resumed via the picker
  launcher_started          the session launcher began a launch flow (carries
                            launch_id + the setup-log path)
  session_started           a Copilot session registered against a worktree
  session_ended             a Copilot session deregistered
  ahp_session_bound         an AHP session was created or verified for a worktree
  ahp_session_disposed      the bound AHP session was explicitly disposed
  copilot_exited            the Copilot process exited (launcher)
  pane_exited               the wrapped pane command exited (pane wrapper) --
                            the only mark carrying the mux pane's real exit_code
  mux_attached              a tmux/psmux session was attached/joined (launcher);
                            fresh creation carries its attempt count
  mux_session_assigned      the programmatic cutover path's mux pane creation
                            succeeded (mux_new_session/mux_new_window) --
                            stage 2's other emitter, alongside mux_attached
  copilot_invoked           the Copilot binary was actually exec'd (the true
                            final resolution point in the setup launcher)
  copilot_invocation_attempted
                            launch-command's wrapper handed off to a
                            config-driven launch template or legacy
                            tools/setup/setup.{sh,ps1} -- a coarser mark for
                            paths that never reach default-setup's own
                            precise copilot_invoked emitter
  mux_failed                the requested tmux/psmux launch failed closed;
                            creation exhaustion carries attempt count and
                            recoverable=true when the worktree was preserved
  mux_detached              the attach returned -- user detached or session ended
  status_reported           the first status-report (disposition) write in a
                            session -- marks "Copilot did something here"
  changes_pushed            worktree content was pushed to the default branch
  worktree_finalized        finalize completed (content on upstream)
  finalize_skipped_removal  finalize left the worktree/branch/session in place
                            (running inside it, or a live session was detected)
  worktree_reaped           cleanup removed a worktree's dir/branch/session
  handoff_cutover_claim     monitor atomically claimed a pending handoff token
  handoff_successor_spawn_started
                            successor pane spawn is starting -- emitted before
                            success/failure is known, so a killed spawn still
                            leaves a trace
  handoff_cutover_spawn     live handoff spawned a successor pane (terminal
                            success for the spawn started above)
  handoff_successor_spawn_failed
                            the successor pane spawn (started above) failed
  handoff_predecessor_retire
                            a consumed handoff retired the predecessor pane
  handoff_retire_guard      a retire request was left in place by a safety guard

Every record carries ``worktree_id`` and (where known) ``session_id`` and
``launch_id``. ``launch_id`` is a short correlation token minted once at
launcher entry and threaded through the whole flow (launcher -> mux env ->
session hooks -> post-exit), so ``agent-worktrees activity --launch-id <id>``
returns one launch flow deterministically rather than by timestamp guesswork.

Events recognized in ``HANDOFF_STAGE_MAP`` (see below) additionally carry
``stage`` (1-13, the ordinal position in the handoff cutover lifecycle) and
``stage_name`` (its canonical name), stamped automatically by ``log_event`` --
see efforts/active/handoff-cutover-lifecycle-journal/README.md Phase 1 for the
full 13-stage model this lays the foundation for. This is purely additive:
no existing event name is renamed, so existing readers (health.py's
``find_orphaned_handoffs``, the status monitor's pending-handoff scan) are
unaffected.

Logging must never break the worktree lifecycle: every public function
swallows its own exceptions.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config as cfg

# Rolling retention window. Lines older than this are dropped on prune.
RETENTION_DAYS = 7

# Prune is only attempted once the file grows past this size, keeping the
# common append path cheap. Events are small and infrequent, so this
# triggers rarely (hundreds of sessions).
_PRUNE_SIZE_BYTES = 512 * 1024

_HOSTNAME = socket.gethostname()

log = logging.getLogger("agent-worktrees")

# Process-local count of log_event() calls that failed to write a record.
# log_event() must never raise into its caller, so a dropped event is
# otherwise invisible; this counter (plus the paired log.debug() call) makes
# a swallowed failure detectable rather than merely theorized. Reset each
# process start -- it is a liveness signal for the current run, not a
# persisted metric.
_log_event_failures = 0


def log_event_failure_count() -> int:
    """Number of ``log_event()`` calls in this process that failed to write.

    ``log_event`` always swallows its own exceptions (a diagnostic log must
    never break the operation it observes), so this counter -- incremented
    alongside a ``log.debug`` call on every such failure -- is how a caller or
    test can detect "an event was silently dropped" instead of only being
    able to theorize it from a gap in the trace.
    """
    return _log_event_failures


# Handoff cutover lifecycle stage vocabulary (the 13-stage model from
# efforts/active/handoff-cutover-lifecycle-journal/README.md Phase 1). Maps
# each existing wire event name that corresponds to a stage onto that stage's
# (ordinal, canonical_name). Layered on top of existing event names -- never
# renames or replaces them, so existing consumers (health.py, the status
# monitor's pending-handoff scan) keep working unmodified. New stages with no
# pre-existing event (5, 7-as-a-distinct-signal, etc.) are added here as their
# dedicated emitters land in later phases; until then they simply have no
# entry and are omitted from a rendered trace rather than guessed at.
HANDOFF_STAGE_MAP: dict[str, tuple[int, str]] = {
    "worktree_created": (1, "worktree_created"),
    "mux_attached": (2, "mux_session_assigned"),
    "mux_session_assigned": (2, "mux_session_assigned"),
    "copilot_invoked": (3, "copilot_invoked"),
    "copilot_invocation_attempted": (3, "copilot_invoked"),
    "session_started": (4, "session_start_bound"),
    "status_reported": (5, "status_reported"),
    "handoff_requested": (6, "handoff_triggered"),
    "handoff_cutover_claim": (7, "handoff_host_acknowledged"),
    "handoff_host_acknowledged": (7, "handoff_host_acknowledged"),
    "handoff_cutover_spawn": (8, "handoff_successor_spawn_started"),
    "handoff_successor_spawn_started": (8, "handoff_successor_spawn_started"),
    "handoff_successor_spawn_failed": (8, "handoff_successor_spawn_started"),
    "handoff_successor_session_start_bound": (
        9,
        "handoff_successor_session_start_bound",
    ),
    "handoff_successor_claimed": (10, "handoff_successor_claimed"),
    "handoff_predecessor_retire": (
        11,
        "handoff_pickup_confirmed_predecessor_closing",
    ),
    "handoff_pickup_confirmed_predecessor_closing": (
        11,
        "handoff_pickup_confirmed_predecessor_closing",
    ),
    "session_ended": (12, "session_end_bound"),
    "session_end_bound": (12, "session_end_bound"),
    "handoff_complete": (13, "handoff_complete"),
}


# Events whose stage-map entry is only valid for a specific field value --
# e.g. handoff_cutover_claim fires for outcome="acquired" (the real
# acknowledgement), but also for "already-claimed" and "error" (a duplicate or
# failed claim attempt), which must NOT be stamped as a Stage 7 success or the
# audit trace would show a false acknowledgement for every collision.
# handoff_predecessor_retire similarly fires for outcome="identity-mismatch"
# (a safety guard skipped the pane) and outcome="left-running" (the pane/
# process survived retirement), neither of which is a confirmed Stage 11
# pickup/closure -- only outcome="gone" (retire_pane confirmed gone AND
# process reaping clean) is.
_HANDOFF_STAGE_GATE: dict[str, tuple[str, object]] = {
    "handoff_cutover_claim": ("outcome", "acquired"),
    "handoff_predecessor_retire": ("outcome", "gone"),
}


def log_path() -> Path:
    """Path to the machine-global activity log."""
    return cfg.install_dir() / "logs" / "activity.jsonl"


def log_event(
    event: str,
    *,
    worktree_id: str | None = None,
    session_id: str | None = None,
    launch_id: str | None = None,
    source: str = "python",
    **fields: object,
) -> None:
    """Append a single high-level lifecycle event. Never raises.

    Delivery is best-effort: a write failure (disk full, permissions, ...) is
    swallowed and never propagates to the caller, but it is not swallowed
    *invisibly* -- see ``log_event_failure_count()`` and the paired
    ``log.debug`` call in the except clause below.

    Args:
        event: One of the documented event names (see module docstring).
        worktree_id: The worktree this event concerns.
        session_id: The Copilot session id, if known.
        launch_id: The launch-flow correlation id, if known. Minted once at
            launcher entry and threaded through the flow so every record of one
            launch shares it (``agent-worktrees activity --launch-id``).
        source: Originating component ("python" or "launcher").
        **fields: Extra context (branch, reason, exit_code, ...). ``None``
            values are dropped. ``stage``/``stage_name`` are reserved: a
            caller-supplied value is dropped in favor of the canonical
            ``HANDOFF_STAGE_MAP`` stamp so the schema can't be corrupted by an
            arbitrary ``--field stage=...`` from the CLI.
    """
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        record: dict[str, object] = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "event": event,
            "worktree_id": worktree_id,
            "session_id": session_id,
            "launch_id": launch_id,
            "pid": os.getpid(),
            "host": _HOSTNAME,
            "source": source,
        }
        for key, value in fields.items():
            if value is not None:
                record[key] = value
        stage_info = HANDOFF_STAGE_MAP.get(event)
        if stage_info is not None:
            gate = _HANDOFF_STAGE_GATE.get(event)
            gated_out = gate is not None and record.get(gate[0]) != gate[1]
            if gated_out:
                record.pop("stage", None)
                record.pop("stage_name", None)
            else:
                # Stamped last so no caller-supplied field (including a
                # same-named one from **fields) can override the canonical
                # value.
                record["stage"], record["stage_name"] = stage_info
        # An unmapped/custom event's own stage/stage_name fields (if any)
        # are left exactly as the caller supplied them -- only a *mapped*
        # event's stamp is reserved/gated.
        line = json.dumps(record, ensure_ascii=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        _maybe_prune(path)
    except Exception as exc:
        # A diagnostic log must never interfere with the operation it
        # observes -- delivery stays best-effort and this never raises into
        # the caller. But "fail silently" must not mean "fail invisibly": bump
        # a process-local counter and emit a debug-level log line so a missing
        # event is itself detectable (via log_event_failure_count() or a
        # DEBUG-level log stream) rather than only inferable from a gap in
        # the trace.
        global _log_event_failures
        _log_event_failures += 1
        log.debug("activity log_event(%r) failed to write: %s", event, exc)


def _maybe_prune(path: Path) -> None:
    """Prune lines older than the retention window if the file is large."""
    try:
        if path.stat().st_size < _PRUNE_SIZE_BYTES:
            return
    except OSError:
        return
    _prune(path, RETENTION_DAYS)


def _prune(path: Path, retention_days: int) -> int:
    """Rewrite the log keeping only lines within the retention window.

    Returns the number of lines kept. Best-effort: a concurrent append
    during the rewrite could be lost, which is acceptable for a
    diagnostic log. Unparseable lines are kept.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    kept: list[str] = []
    try:
        with open(path, encoding="utf-8") as handle:
            for raw in handle:
                line = raw.rstrip("\n")
                if not line:
                    continue
                ts = _parse_ts(line)
                if ts is None or ts >= cutoff:
                    kept.append(line)
    except OSError:
        return 0

    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(
            ("\n".join(kept) + "\n") if kept else "", encoding="utf-8"
        )
        tmp.replace(path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
    return len(kept)


def _parse_ts(line: str) -> datetime | None:
    """Extract the UTC timestamp from a log line, or None if unparseable."""
    try:
        ts = datetime.fromisoformat(json.loads(line)["ts"])
    except Exception:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


# ── Reader / viewer ────────────────────────────────────────────────────

_DURATION_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def parse_since(value: str) -> datetime | None:
    """Parse a --since value into a UTC cutoff datetime.

    Accepts relative durations like ``2d``, ``12h``, ``30m``, ``1w`` or an
    ISO date/datetime (``2026-06-09`` or ``2026-06-09T11:00``). Returns
    None if the value cannot be parsed.
    """
    value = value.strip()
    if not value:
        return None
    if value[-1].lower() in _DURATION_UNITS and value[:-1].isdigit():
        seconds = int(value[:-1]) * _DURATION_UNITS[value[-1].lower()]
        return datetime.now(timezone.utc) - timedelta(seconds=seconds)
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def read_events(
    *,
    since: datetime | None = None,
    worktree_id: str | None = None,
    launch_id: str | None = None,
    event: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    """Return matching events, oldest first."""
    path = log_path()
    out: list[dict] = []
    if not path.exists():
        return out
    try:
        with open(path, encoding="utf-8") as handle:
            for raw in handle:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except Exception:
                    continue
                if worktree_id and rec.get("worktree_id") != worktree_id:
                    continue
                if launch_id and rec.get("launch_id") != launch_id:
                    continue
                if event and rec.get("event") != event:
                    continue
                if since is not None:
                    ts = _parse_ts(raw)
                    if ts is not None and ts < since:
                        continue
                out.append(rec)
    except OSError:
        return out
    if limit is not None and limit > 0:
        out = out[-limit:]
    return out


def _fmt_local(ts_iso: str) -> str:
    """Render a UTC ISO timestamp in local time for display."""
    try:
        dt = datetime.fromisoformat(ts_iso)
    except ValueError:
        return ts_iso
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")


# Context fields worth surfacing in the human-readable table, in order.
_EXTRA_KEYS = (
    "reason", "branch", "exit_code", "resume_count", "state", "mux", "launch_id",
    "old_pane", "new_pane", "successor_verified", "method", "outcome",
)


def render_events(events: list[dict]) -> str:
    """Format events as an aligned, human-readable table (oldest first)."""
    if not events:
        return "No activity recorded."
    rows: list[tuple[str, str, str, str, str]] = []
    for rec in events:
        when = _fmt_local(str(rec.get("ts", "")))
        event = str(rec.get("event", ""))
        wt = rec.get("worktree_id") or "-"
        sess = rec.get("session_id")
        sess = sess[:8] if isinstance(sess, str) else "-"
        extras = [
            f"{k}={rec[k]}" for k in _EXTRA_KEYS if rec.get(k) is not None
        ]
        rows.append((when, event, str(wt), sess, " ".join(extras)))

    w_event = max(len(r[1]) for r in rows)
    w_wt = max(len(r[2]) for r in rows)
    lines = []
    for when, event, wt, sess, extra in rows:
        lines.append(
            f"{when}  {event:<{w_event}}  {wt:<{w_wt}}  {sess:<8}  {extra}".rstrip()
        )
    return "\n".join(lines)


def cmd_activity(args) -> int:
    """``agent-worktrees activity`` -- view the lifecycle log."""
    since = None
    raw_since = getattr(args, "since", None)
    if raw_since:
        since = parse_since(raw_since)
        if since is None:
            print(f"Invalid --since value: {raw_since!r}", file=sys.stderr)
            return 1
    events = read_events(
        since=since,
        worktree_id=getattr(args, "worktree_id", None),
        launch_id=getattr(args, "launch_id", None),
        event=getattr(args, "event", None),
        limit=getattr(args, "lines", None),
    )
    if getattr(args, "json", False):
        for rec in events:
            print(json.dumps(rec, ensure_ascii=True))
        return 0
    print(render_events(events))
    return 0


def cmd_activity_log(args) -> int:
    """``agent-worktrees activity-log`` -- append one event (launcher hook).

    Extra context is passed as repeatable ``--field key=value`` args.
    """
    event = getattr(args, "event", None)
    if not event:
        print("Usage: activity-log EVENT [--worktree-id ID] ...", file=sys.stderr)
        return 1
    fields: dict[str, object] = {}
    for item in getattr(args, "field", None) or []:
        if "=" in item:
            key, _, value = item.partition("=")
            key = key.strip()
            if key:
                fields[key] = value
    log_event(
        event,
        worktree_id=getattr(args, "worktree_id", None),
        session_id=getattr(args, "session_id", None),
        launch_id=getattr(args, "launch_id", None),
        source=getattr(args, "source", None) or "launcher",
        **fields,
    )
    return 0
