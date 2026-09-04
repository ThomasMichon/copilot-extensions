"""Copilot CLI session-state scanning.

Scans ~/.copilot/session-state/ to detect active Copilot sessions
(by lock file + process check) and extract latest session summaries
for worktree annotation.

Session discovery is **registry-driven** (random access by exact session id).
The only sanctioned full walk of the state root is the explicit
``backfill_sessions()`` repair -- see ``docs/patterns/session-state-access.md``.
"""

from __future__ import annotations

import base64
import json
import os
import platform
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import yaml

try:  # libyaml (C) is dramatically faster; the one sanctioned sweep uses it.
    from yaml import CSafeLoader as _YamlSafeLoader
except ImportError:  # pragma: no cover - pure-Python fallback
    from yaml import SafeLoader as _YamlSafeLoader


@dataclass
class SessionContext:
    """Aggregated session info for a set of worktree paths."""

    active_sessions: dict[str, list[str]] = field(default_factory=dict)
    """normalized_path → list of session_ids with live Copilot processes"""

    stale_locks: dict[str, list[int]] = field(default_factory=dict)
    """normalized_path → pids from ``inuse.<pid>.lock`` files whose process is
    NOT a live Copilot (a crashed/killed session that never cleaned up its
    lock). Distinct from ``active_sessions`` (a LIVE bound process): this is the
    residue the Picker's Reclaim verb must clear "to the point where the pid
    lock file is removed". A worktree with a stale lock but no mux/live-lock is
    still offered Reclaim (file-only cleanup), never stranded ACTIVE."""

    latest_summary: dict[str, str] = field(default_factory=dict)
    """normalized_path → best available session display text (summary or name)"""

    session_count: dict[str, int] = field(default_factory=dict)
    """normalized_path → total number of Copilot sessions found"""

    turn_count: dict[str, int] = field(default_factory=dict)
    """normalized_path → total user-message turns across all sessions"""

    last_activity: dict[str, str] = field(default_factory=dict)
    """normalized_path → ISO updated_at of the most-recent session"""

    context_pct: dict[str, int] = field(default_factory=dict)
    """normalized_path → context-window utilization % of the most-recent session"""

    live_intent: dict[str, str] = field(default_factory=dict)
    """normalized_path → most-recent session's live agent intent (the pulse).

    Passively derived from the ``assistant.intent`` stream by the agent-worktrees
    live-pulse extension (sidecar ``substatus.json``); never the agent-asserted
    disposition.  The picker renders this as a dim, expiring line and NEVER
    treats it as the durable ``follow_up`` flag.
    """

    live_intent_at: dict[str, str] = field(default_factory=dict)
    """normalized_path → ISO timestamp the live intent was last updated."""

    live_intent_idle: dict[str, bool] = field(default_factory=dict)
    """normalized_path → whether the pulse's session had gone idle at flush."""

    live_rest: dict[str, str] = field(default_factory=dict)
    """normalized_path → the graded REST state (copilot-extensions#228): one of
    ``"busy"`` / ``"idle"`` / ``"awaiting-operator"``.

    Sourced two ways: the crisp value comes from the live-pulse extension's
    ``substatus.json`` sidecar (``busy`` = a turn is running; ``idle`` = done-rest,
    the session went quiescent; ``awaiting-operator`` = parked on a human
    input/permission request, "this needs me"); when the sidecar is absent the
    **backbone** fills a COARSE ``busy`` / ``idle`` from a bounded ``events.jsonl``
    tail (turn boundaries). So ``live_rest`` may be present with **no extension
    loaded** — do NOT infer "extension loaded" from its presence. Only
    ``awaiting-operator`` is extension-only (``session.idle`` and the
    ``*.requested`` prompts are ephemeral and never persist to disk). Enrichment
    only, never the durable ``follow_up`` disposition."""

    live_rest_at: dict[str, str] = field(default_factory=dict)
    """normalized_path → ISO timestamp of the last rest-state transition."""

    _latest_ts: dict[str, str] = field(default_factory=dict)
    """Internal: tracks latest updated_at per path for summary selection."""

    _activity_ts: dict[str, str] = field(default_factory=dict)
    """Internal: tracks latest updated_at per path for activity/context selection."""

    last_session_id: dict[str, str] = field(default_factory=dict)
    """normalized_path → session_id of the most-recent session carrying
    conversation data (``session.db``/``events.jsonl``).

    Folded into the single ``scan_sessions``/``_enrich_session_dir`` pass so the
    list command no longer re-scans **all** of ``session-state`` once per
    worktree -- that per-worktree full scan was O(worktrees x sessions)
    ``yaml.safe_load`` calls and could pin a CPU core on a large tree (GH #198).
    This is transcript metadata only, never the current-session authority:
    callers derive resumability from ``WorktreeRecord.resolved_head_session``.
    Detached sessions are skipped, conversation data is required, and newest
    ``updated_at`` wins."""

    _last_sid_ts: dict[str, str] = field(default_factory=dict)
    """Internal: tracks latest updated_at per path for last_session_id selection.
    Independent of ``_activity_ts`` so an older *valid* session still wins over a
    newer stale stub (which ``last_activity`` may reflect but a resume target
    must not)."""


def _normalize_path(p: str) -> str:
    """Normalize a path for comparison -- strip trailing separators."""
    return p.rstrip("/\\")


def _read_context_pct(entry: Path) -> int | None:
    """Read context-window utilization % from a session's ``context.json``.

    The context-handoff extension writes this sidecar after each model
    interaction (the ``session.usage_info`` event carries the exact token
    counts, which are not present in ``events.jsonl``).  Returns the
    rounded percentage, or None when the sidecar is absent/unreadable.
    Never raises.
    """
    f = entry / "context.json"
    try:
        if not f.exists():
            return None
        data = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    pct = data.get("utilizationPct")
    if isinstance(pct, bool):
        return None
    if isinstance(pct, (int, float)):
        return max(0, min(100, int(round(pct))))
    return None


def _read_substatus(entry: Path) -> tuple[str, str, bool, str | None, str] | None:
    """Read the live agent-intent + rest pulse from a session's ``substatus.json``.

    The agent-worktrees live-pulse extension writes this sidecar from native
    session events (root agent only) -- the ``assistant.intent`` stream is
    ephemeral and never lands in ``events.jsonl``, so this file is the sole
    on-disk source. Returns
    ``(intent, updated_at_iso, idle, rest, rest_at_iso)`` or None when
    absent/unreadable, where ``rest`` is the graded rest state
    (``"busy"``/``"idle"``/``"awaiting-operator"``, copilot-extensions#228) or
    None on a legacy sidecar (then derived from ``idle``). Never raises. This is
    the derived pulse register; it is deliberately independent of the
    agent-asserted ``follow_up`` disposition.
    """
    f = entry / "substatus.json"
    try:
        if not f.exists():
            return None
        data = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    intent = data.get("intent")
    if not isinstance(intent, str) or not intent.strip():
        return None
    updated_at = data.get("updatedAt")
    updated_at = updated_at if isinstance(updated_at, str) else ""
    idle = bool(data.get("idle"))
    rest = data.get("rest")
    rest = rest if isinstance(rest, str) and rest.strip() else None
    if rest is None:
        # Legacy sidecar (no graded rest): derive the coarse state from ``idle``.
        rest = "idle" if idle else None
    rest_at = data.get("restAt")
    rest_at = rest_at if isinstance(rest_at, str) else ""
    return intent.strip(), updated_at, idle, rest, rest_at


# Bounded tail window (bytes) for the extension-free rest inference below. Sized
# to comfortably contain a recent turn boundary without ever reading the whole
# (append-only, unbounded) event log.
_REST_TAIL_WINDOW = 65536


def _infer_rest_from_events(entry: Path) -> str | None:
    """Coarse, extension-free at-rest inference from a BOUNDED events.jsonl tail.

    The live-pulse extension is the crisp rest source (``session.idle`` /
    ``awaiting-operator``), but it is **non-load-bearing** (copilot-extensions#228):
    when it is not loaded the backbone must still report a coarse rest. The
    ephemeral rest events (``session.idle`` and the ``*.requested`` prompts) never
    land on disk, but the **turn boundaries do** (``assistant.turn_start`` /
    ``assistant.turn_end``), so the session's own event log yields a coarse
    busy/idle -- and only that (``awaiting-operator`` stays extension-only).

    **Bounded by design** (the no-unbounded-sweep invariant): reads only the last
    :data:`_REST_TAIL_WINDOW` bytes via a random-access seek to the tail, never the
    whole file, and scans raw bytes (no JSON parse). Returns ``"idle"`` when the
    most recent turn boundary in the window is a ``turn_end`` (the turn finished ->
    at rest), ``"busy"`` when it is a ``turn_start`` (a turn is in flight), or None
    when the window carries no turn boundary (unknown -- never guessed, never
    swept). Never raises.
    """
    events = entry / "events.jsonl"
    try:
        size = events.stat().st_size
        if size <= 0:
            return None
        with open(events, "rb") as f:
            f.seek(max(0, size - _REST_TAIL_WINDOW))
            tail = f.read()
    except OSError:
        return None
    last: str | None = None
    for line in tail.split(b"\n"):
        # Trailing hook/usage events are not turn boundaries -- only the turn
        # markers move the coarse state, so scan for the LAST of those two.
        if b'"assistant.turn_end"' in line:
            last = "idle"
        elif b'"assistant.turn_start"' in line:
            last = "busy"
    return last


def _update_activity(
    ctx: SessionContext, norm_path: str, entry: Path, updated_at: str
) -> None:
    """Track the most-recent session's activity timestamp + context %.

    ``last_activity`` and ``context_pct`` always reflect the newest
    session (by ``updated_at``) for a worktree, independent of whether
    that session has a usable title.
    """
    if not updated_at:
        return
    # GH #198: track the newest session that carries conversation data as the
    # worktree's resume target (``last_session_id``), *before* the
    # last_activity gate below. This tracker is independent so an older valid
    # session still wins over a newer stale stub -- mirroring
    # ``find_latest_session_id`` (which skips stubs lacking session.db /
    # events.jsonl). Detached sessions are already skipped by every caller.
    if (entry / "session.db").exists() or (entry / "events.jsonl").exists():
        sid_prev = ctx._last_sid_ts.get(norm_path, "")
        if not sid_prev or updated_at > sid_prev:
            ctx._last_sid_ts[norm_path] = updated_at
            ctx.last_session_id[norm_path] = entry.name
    prev = ctx._activity_ts.get(norm_path, "")
    if prev and updated_at <= prev:
        return
    ctx._activity_ts[norm_path] = updated_at
    ctx.last_activity[norm_path] = updated_at
    pct = _read_context_pct(entry)
    if pct is not None:
        ctx.context_pct[norm_path] = pct
    elif norm_path in ctx.context_pct:
        # Newest session has no context.json -- drop a stale older value
        # rather than misreport an unrelated session's utilization.
        del ctx.context_pct[norm_path]
    # The live pulse follows the same newest-session-wins rule as context %: a
    # newer session without a sidecar clears any stale intent from an older one.
    sub = _read_substatus(entry)
    rest: str | None = None
    rest_at = ""
    if sub is not None:
        intent, sub_at, idle, rest, rest_at = sub
        ctx.live_intent[norm_path] = intent
        ctx.live_intent_at[norm_path] = sub_at
        ctx.live_intent_idle[norm_path] = idle
    else:
        ctx.live_intent.pop(norm_path, None)
        ctx.live_intent_at.pop(norm_path, None)
        ctx.live_intent_idle.pop(norm_path, None)
    # REST (copilot-extensions#228): prefer the crisp sidecar value written by the
    # live-pulse extension; when it is absent (extension not loaded, or a legacy
    # sidecar without a graded rest) fall back to a COARSE busy/idle inferred from
    # a BOUNDED events.jsonl tail -- the non-load-bearing backbone. ``restAt`` is
    # only a real ISO timestamp from the sidecar; the coarse backbone carries none.
    if rest is None:
        rest = _infer_rest_from_events(entry)
        rest_at = ""
    if rest is not None:
        ctx.live_rest[norm_path] = rest
        if rest_at:
            ctx.live_rest_at[norm_path] = rest_at
        else:
            ctx.live_rest_at.pop(norm_path, None)
    else:
        ctx.live_rest.pop(norm_path, None)
        ctx.live_rest_at.pop(norm_path, None)


def _session_state_dir() -> Path:
    """Return the Copilot session-state directory."""
    if platform.system() == "Windows":
        home = os.environ.get("USERPROFILE", str(Path.home()))
    else:
        home = str(Path.home())
    return Path(home) / ".copilot" / "session-state"


# Marker file Copilot CLI writes into a session-state directory when the
# session is a *detached child of a spawning parent* -- i.e. its
# ``detachedFromSpawningParentSessionId`` is set. Per the CLI's own schema,
# this is "a detached headless rem-agent run launched on the parent's
# interactive shutdown" (the subconscious / memory-consolidation pass).
#
# Such a session inherits the parent session's ``cwd`` -- which, when an
# *old* session is consolidated, is an already-finalized worktree path. The
# CLI is not worktree-aware and reuses that cwd, so without this guard the
# detached run's live ``copilot`` process makes a finalized worktree look
# active again (blocking cleanup) and pollutes its display summary. These
# background continuation runs must never be attributed to a worktree.
_DETACHED_MARKER = ".detached"


def _is_detached_session(entry: Path) -> bool:
    """Whether *entry* is a detached parent-continuation session dir.

    Detected via the ``.detached`` marker file the Copilot CLI writes for
    sessions whose context continues a spawning parent (e.g. a headless
    rem-agent / subconscious consolidation run). Such sessions reuse the
    parent's cwd and must be excluded from worktree liveness/attribution.
    Never raises -- treats any error as "not detached".
    """
    try:
        return (entry / _DETACHED_MARKER).exists()
    except OSError:
        return False


def _is_process_alive(pid: int) -> bool:
    """Check if a process is running."""
    if platform.system() == "Windows":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(  # type: ignore[attr-defined]
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
            return True
        return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


# Cached kernel32 handle for Windows process queries (avoids per-call DLL setup)
_kernel32 = None


def _get_kernel32():
    """Return a configured kernel32 WinDLL handle, cached after first call."""
    global _kernel32
    if _kernel32 is not None:
        return _kernel32
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD,
        wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
    ]
    k32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.restype = wintypes.BOOL
    _kernel32 = k32
    return k32


def _is_copilot_process(pid: int) -> bool:
    """Check if a PID belongs to a Copilot CLI process."""
    if platform.system() == "Windows":
        import ctypes
        from ctypes import wintypes

        kernel32 = _get_kernel32()
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(buf))
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                exe_name = Path(buf.value).name.lower()
                return "copilot" in exe_name
            return False
        finally:
            kernel32.CloseHandle(handle)
    else:
        cmdline_path = Path(f"/proc/{pid}/cmdline")
        try:
            content = cmdline_path.read_bytes()
            return b"copilot" in content
        except OSError:
            return False


# picker-cache-first-paint (dotfiles#948): the per-session turn-count sidecar.
# ``events.jsonl`` is append-only and can reach tens of MB, so re-counting
# ``"user.message"`` lines across every session on each populate/refresh is the
# dominant scan cost (measured: ~2s over 476 MB on cloud1). This memoizes the
# count keyed by file size next to the events file, so a stable session costs a
# stat + tiny JSON read, and a grown session re-reads ONLY the appended bytes.
_TURNS_SIDECAR = ".aw-turns.json"


def _count_user_turns(entry: Path) -> int:
    """User-turn count for a session dir, computed incrementally.

    Counts ``"user.message"`` lines in ``events.jsonl`` but reuses a size-keyed
    sidecar (``.aw-turns.json``) so an unchanged file is O(1) and a grown file
    only re-reads the appended tail (events.jsonl is append-only, line-delimited,
    so the cached size is a line boundary). Falls back to a full recount when the
    file shrank/rotated or the sidecar is unreadable. Best-effort: a read/write
    hiccup never raises (returns the best count it has).
    """
    events = entry / "events.jsonl"
    try:
        size = events.stat().st_size
    except OSError:
        return 0
    prev_size = 0
    prev_turns = 0
    sidecar = entry / _TURNS_SIDECAR
    try:
        d = json.loads(sidecar.read_text(encoding="utf-8"))
        prev_size = int(d.get("size", 0))
        prev_turns = int(d.get("turns", 0))
    except Exception:
        prev_size = prev_turns = 0
    if prev_size == size and size > 0:
        return prev_turns
    # Grown file: resume from the cached boundary; otherwise full recount.
    resume = 0 < prev_size < size
    start = prev_size if resume else 0
    turns = prev_turns if resume else 0
    try:
        with open(events, "rb") as f:
            if start:
                f.seek(start)
            for line in f:
                if b'"user.message"' in line:
                    turns += 1
    except OSError:
        return prev_turns
    try:
        sidecar.write_text(
            json.dumps({"size": size, "turns": turns}), encoding="utf-8")
    except OSError:
        pass
    return turns


def worktree_session_lock_state(rec) -> tuple[bool, list[int]]:
    """Return live-binding presence and stale lock PIDs for one worktree.

    The targeted scan covers only the worktree's registered session directories
    and their ``inuse.<pid>.lock`` files. It does not read events/workspace data
    or enumerate the machine process table. A PID owned by a live Copilot is a
    binding; any other PID is stale lock residue.

    Best-effort: never raises. An unindexed worktree returns ``(False, [])``;
    the cached ``bound_live`` hint and the full populate cover that case.
    """
    sessions_list = getattr(rec, "sessions", None)
    if not sessions_list:
        return False, []
    state_dir = _session_state_dir()
    if not state_dir.exists():
        return False, []
    stale_pids: list[int] = []
    for entry in sessions_list:
        sid = getattr(entry, "session_id", None)
        if not sid:
            continue
        sdir = state_dir / sid
        if not sdir.is_dir():
            continue
        # Detached parent-continuation runs reuse a foreign cwd -- never a live
        # signal for THIS worktree (mirrors _enrich_session_dir).
        if _is_detached_session(sdir):
            continue
        try:
            lock_files = list(sdir.glob("inuse.*.lock"))
        except OSError:
            continue
        for lock_file in lock_files:
            parts = lock_file.stem.split(".")
            if len(parts) < 2:
                continue
            try:
                pid = int(parts[1])
            except ValueError:
                continue
            # _is_copilot_process is alive-AND-copilot in one call (OpenProcess
            # fails for a dead pid), so a stale lock from a crashed session does
            # not count.
            if _is_copilot_process(pid):
                return True, stale_pids
            stale_pids.append(pid)
    return False, stale_pids


def worktree_has_live_session(rec) -> bool:
    """Cheap ACTIVE check for the picker's cache-only first paint."""
    return worktree_session_lock_state(rec)[0]


def _enrich_session_dir(
    session_dir: Path,
    session_id: str,
    worktree_path: str,
    ctx: SessionContext,
) -> None:
    """Read a single session directory and populate ctx fields.

    Shared helper for fast-path scanning -- reads workspace.yaml for
    summary, events.jsonl for turn count, and lock files for liveness.
    """
    entry = session_dir / session_id
    if not entry.is_dir():
        return

    # Skip detached parent-continuation sessions (e.g. headless rem-agent /
    # subconscious runs); they reuse the parent's cwd and must not be
    # attributed to this worktree.
    if _is_detached_session(entry):
        return

    norm_path = _normalize_path(worktree_path)

    # Turn count from events.jsonl (incremental, size-keyed sidecar).
    events_file = entry / "events.jsonl"
    if events_file.exists():
        turns = _count_user_turns(entry)
        if turns > 0:
            ctx.turn_count[norm_path] = (
                ctx.turn_count.get(norm_path, 0) + turns
            )

    # Summary from workspace.yaml
    ws_file = entry / "workspace.yaml"
    if ws_file.exists():
        try:
            with open(ws_file, encoding="utf-8") as f:
                ws_data = yaml.safe_load(f)
        except Exception:
            ws_data = None

        if ws_data and isinstance(ws_data, dict):
            updated_at = str(ws_data.get("updated_at", ""))
            _update_activity(ctx, norm_path, entry, updated_at)

            _placeholder = ("", "|-", "|", ">-", ">", "null", "Untitled")
            display_text = ""
            summary = ws_data.get("summary", "")
            if isinstance(summary, str) and summary.strip() and summary not in _placeholder:
                display_text = summary.strip()
            if not display_text:
                name = ws_data.get("name", "")
                if isinstance(name, str) and name.strip() and name not in _placeholder:
                    display_text = name.strip()

            if display_text:
                prev_ts = ctx._latest_ts.get(norm_path, "")
                if not prev_ts or updated_at > prev_ts:
                    ctx._latest_ts[norm_path] = updated_at
                    if len(display_text) > 60:
                        display_text = display_text[:57] + "..."
                    ctx.latest_summary[norm_path] = display_text

    # Session count
    ctx.session_count[norm_path] = ctx.session_count.get(norm_path, 0) + 1

    # Liveness check via lock files
    for lock_file in entry.glob("inuse.*.lock"):
        parts = lock_file.stem.split(".")
        if len(parts) >= 2:
            try:
                lock_pid = int(parts[1])
            except ValueError:
                continue
            if _is_copilot_process(lock_pid):
                if norm_path not in ctx.active_sessions:
                    ctx.active_sessions[norm_path] = []
                ctx.active_sessions[norm_path].append(session_id)
                break
            else:
                # A lock file whose pid is no longer a live Copilot -- residue
                # from a crashed/killed session. Record it so the Picker can
                # offer Reclaim (file-only cleanup) instead of stranding the row
                # or falsely reading it ACTIVE.
                ctx.stale_locks.setdefault(norm_path, []).append(lock_pid)


def scan_sessions_fast(
    records: list,
) -> SessionContext:
    """Targeted session scan using the per-worktree session registry.

    Instead of walking all of ``~/.copilot/session-state/``, reads
    session IDs from each record's ``sessions`` list and random-accesses only
    those specific directories.

    **Invariant (GH #198):** a routine, looped read path (the ``list`` command a
    poller drives) must never sweep the whole session-state folder -- only
    random-access known session ids. A record whose ``sessions`` registry is
    empty/None (pre-registry, or the register-session hook never fired) is left
    un-enriched here; its registry is repaired only by an **explicit** backfill
    (``agent-worktrees backfill-sessions`` / ``doctor``), never by an
    on-every-list sweep. The prior full-scan fallback made this an
    O(worktrees x sessions) ``yaml.safe_load`` walk that pinned a CPU core.

    Args:
        records: List of WorktreeRecord objects (with sessions field).

    Returns:
        SessionContext with active sessions and latest summaries.
    """
    ctx = SessionContext()
    session_dir = _session_state_dir()

    if not session_dir.exists():
        return ctx

    for rec in records:
        if not rec.worktree_path:
            continue

        # sessions=None means pre-registry; an empty list means the registry is
        # active but no session was recorded for this worktree (e.g. the
        # register-session hook never fired). Either way there is no
        # random-access target -- and, per the invariant (GH #198), a routine
        # read must NOT sweep all of session-state to recover one. The record is
        # left un-enriched here; an explicit backfill
        # (``agent-worktrees backfill-sessions`` / ``doctor``) repairs the
        # registry off the hot path. The prior full-scan fallback made this an
        # O(worktrees x sessions) yaml.safe_load walk that pinned a CPU core.
        sessions = getattr(rec, "sessions", None)
        if not sessions:
            continue

        # Fast path -- random-access only the known session IDs.
        for entry in sessions:
            _enrich_session_dir(
                session_dir, entry.session_id, rec.worktree_path, ctx,
            )

    return ctx


def validate_session_id(session_id: str | None) -> str | None:
    """Return *session_id* iff its state dir exists and carries conversation
    data (``session.db`` or ``events.jsonl``), else ``None``.

    Used by the resume path to validate a ``parent_session`` fallback (#1029)
    before handing it to ``copilot --resume`` -- a stale/pruned pointer must not
    produce an "unknown session" launch.
    """
    if not session_id:
        return None
    sdir = _session_state_dir() / session_id
    if not sdir.is_dir():
        return None
    if not (sdir / "session.db").exists() and not (sdir / "events.jsonl").exists():
        return None
    return session_id


def find_latest_session_id_fast(
    worktree_path: str,
    sessions: list | None,
) -> str | None:
    """Find the most recent Copilot session ID using the registry.

    If *sessions* is None (pre-registry) or empty (registry active but
    no sessions recorded -- e.g. hook failed to fire), returns ``None``:
    per the invariant (GH #198) a launch/auto-resume lookup must not sweep all
    of session-state to recover a target. An explicit backfill
    (``agent-worktrees backfill-sessions`` / ``doctor``) repairs the registry.

    Validates each candidate: session dir must exist and contain
    ``session.db`` or ``events.jsonl`` (not a stale stub).
    """
    if not sessions:
        # Invariant (GH #198): random-access only. With no registry there is no
        # target to random-access, and a routine launch/auto-resume is not
        # severe enough to justify a full session-state sweep -- return None.
        # Callers degrade gracefully (no auto-resume); an explicit backfill
        # repairs the registry off the hot path.
        return None

    session_dir = _session_state_dir()
    if not session_dir.exists():
        return None

    best_id: str | None = None
    best_ts: str = ""

    for entry in sessions:
        sid = entry.session_id
        sdir = session_dir / sid
        if not sdir.is_dir():
            continue
        # Must have conversation data
        if not (sdir / "session.db").exists() and not (sdir / "events.jsonl").exists():
            continue
        # Use workspace.yaml updated_at for ordering
        ws_file = sdir / "workspace.yaml"
        if ws_file.exists():
            try:
                with open(ws_file, encoding="utf-8") as f:
                    ws_data = yaml.safe_load(f)
                updated_at = str(ws_data.get("updated_at", "")) if ws_data else ""
            except Exception:
                updated_at = ""
        else:
            updated_at = entry.started_at or ""

        if updated_at > best_ts:
            best_ts = updated_at
            best_id = sid

    return best_id


def session_has_conversation_data(session_id: str | None) -> bool:
    """True when *session_id*'s on-disk dir exists AND holds real conversation
    data (``session.db`` or ``events.jsonl``).

    This is the same validity bar ``find_latest_session_id*`` apply: a session
    directory with only a ``workspace.yaml`` is a stale stub that Copilot CLI
    rejects with "No session matched", so ``--resume``-ing it would silently
    cold-start. Callers use this to reject a stub *before* handing an id to
    ``--resume`` / a ``/resume`` hint.
    """
    if not session_id:
        return False
    sdir = _session_state_dir() / session_id
    if not sdir.is_dir():
        return False
    return (sdir / "session.db").exists() or (sdir / "events.jsonl").exists()


def resolve_resume_target(record) -> str | None:
    """The concrete session id a resume should reattach to -- freshest first.

    The single execution-time answer to "which session does this worktree
    resume?", used by the launch executor for Open / Resume / Bare resume so
    all three agree on one target:

      1. the record's **asserted lifecycle head** (``resolved_head_session``)
         when it still has on-disk conversation data -- the authoritative
         "current session" a handoff/cutover may have advanced; then
      2. the **filesystem-latest** valid session
         (``find_latest_session_id_fast``) for un-annotated records; else
      3. ``None`` -- nothing resumable exists (a genuine cold start).

    Preferring the head over pure mtime is what lets Bare resume always surface
    a ``/resume`` id, and stops an "Open"/"Resume" of a worktree whose newest
    on-disk dir is a stub from blank-starting when a valid head still exists.
    Never raises: any lookup hiccup degrades to the fast-latest path.
    """
    try:
        head = getattr(record, "resolved_head_session", None)
    except Exception:
        head = None
    if head and session_has_conversation_data(head):
        return head
    return find_latest_session_id_fast(
        getattr(record, "worktree_path", ""), getattr(record, "sessions", None),
    )


def backfill_sessions(records: list) -> dict[str, list[str]]:
    """Populate empty session registries from existing session-state data.

    Scans ``~/.copilot/session-state/`` once, matches sessions to
    worktree paths, and returns a mapping of worktree_id to session IDs
    that were discovered.  The caller is responsible for writing the
    entries into the tracking YAMLs.

    Only processes records whose ``sessions`` field is empty (``None``
    or ``[]``).  Records with populated session lists are skipped.

    **This is the single sanctioned session-state sweep** (invariant:
    ``docs/patterns/session-state-access.md``). Every other path resolves a
    session by exact id; this repair is the *only* code that may iterate the
    state root, and it is invoked explicitly (the ``backfill-sessions`` verb /
    ``doctor``), never implicitly on a routine read. Parsed with the C YAML
    loader since this is the lone remaining O(sessions) path.
    """
    session_dir = _session_state_dir()
    if not session_dir.exists():
        return {}

    # Collect worktrees that need backfilling
    path_to_wt: dict[str, str] = {}  # normalized_path → worktree_id
    for rec in records:
        sessions = getattr(rec, "sessions", None)
        if sessions:
            continue  # already has entries
        if not rec.worktree_path:
            continue
        path_to_wt[_normalize_path(rec.worktree_path)] = rec.worktree_id

    if not path_to_wt:
        return {}

    # Single pass over all session directories
    # worktree_id → list of (session_id, updated_at)
    discovered: dict[str, list[tuple[str, str]]] = {}

    for entry in session_dir.iterdir():
        if not entry.is_dir():
            continue

        # Skip detached parent-continuation sessions (subconscious /
        # rem-agent runs); they reuse the parent's cwd and must not be
        # backfilled into a worktree's session registry.
        if _is_detached_session(entry):
            continue

        ws_file = entry / "workspace.yaml"
        if not ws_file.exists():
            continue

        # Must have conversation data (not a stale stub)
        if not (entry / "session.db").exists() and not (entry / "events.jsonl").exists():
            continue

        try:
            with open(ws_file, encoding="utf-8") as f:
                ws_data = yaml.load(f, Loader=_YamlSafeLoader)
        except Exception:
            continue

        if not ws_data or not isinstance(ws_data, dict):
            continue

        cwd = ws_data.get("cwd", "")
        if not cwd:
            continue

        norm_cwd = _normalize_path(cwd)

        # Match against worktree paths
        for wt_path, wt_id in path_to_wt.items():
            if norm_cwd == wt_path or norm_cwd.startswith(wt_path + os.sep):
                updated_at = str(ws_data.get("updated_at", ""))
                discovered.setdefault(wt_id, []).append(
                    (entry.name, updated_at)
                )
                break

    # Return just the session IDs, sorted by updated_at (newest last)
    result: dict[str, list[str]] = {}
    for wt_id, entries in discovered.items():
        entries.sort(key=lambda e: e[1])
        result[wt_id] = [sid for sid, _ in entries]

    return result


# Copilot CLI event types that render meaningfully in a transcript view.
# Mirrors the renderable subset a conversation browser needs (messages,
# tool calls + results, lifecycle markers) while dropping low-level noise.
_RENDERABLE_EVENT_TYPES = frozenset({
    "user.message",
    "assistant.message",
    "tool.execution_start",
    "tool.execution_complete",
    "session.start",
    "session.model_change",
    "session.task_complete",
    "subagent.started",
    "subagent.completed",
    "session.info",
    "session.warning",
})


def _has_live_session(entry: Path) -> bool:
    """Whether a session dir has a live Copilot process (via lock files)."""
    for lock_file in entry.glob("inuse.*.lock"):
        parts = lock_file.stem.split(".")
        if len(parts) >= 2:
            try:
                lock_pid = int(parts[1])
            except ValueError:
                continue
            if _is_copilot_process(lock_pid):
                return True
    return False


def _session_meta(session_dir: Path, session_id: str) -> dict | None:
    """Read one session's display metadata from its session-state directory.

    Returns a dict with id, name (summary/title), cwd, branch, created_at,
    updated_at, event_count, turn_count, and a live flag -- or None if the
    directory is missing or is a stale stub (no conversation data).
    Detached parent-continuation sessions are excluded (return None).
    """
    entry = session_dir / session_id
    if not entry.is_dir():
        return None
    if _is_detached_session(entry):
        return None
    events_file = entry / "events.jsonl"
    if not (entry / "session.db").exists() and not events_file.exists():
        return None

    ws_data: dict = {}
    ws_file = entry / "workspace.yaml"
    if ws_file.exists():
        try:
            with open(ws_file, encoding="utf-8") as f:
                loaded = yaml.safe_load(f)
            if isinstance(loaded, dict):
                ws_data = loaded
        except Exception:
            ws_data = {}

    event_count = 0
    turn_count = 0
    if events_file.exists():
        try:
            with open(events_file, encoding="utf-8", errors="replace") as ef:
                for line in ef:
                    event_count += 1
                    if '"user.message"' in line:
                        turn_count += 1
        except OSError:
            pass

    _placeholder = ("", "|-", "|", ">-", ">", "null", "Untitled")
    title = ""
    summary = ws_data.get("summary", "")
    if isinstance(summary, str) and summary.strip() and summary not in _placeholder:
        title = summary.strip()
    if not title:
        name = ws_data.get("name", "")
        if isinstance(name, str) and name.strip() and name not in _placeholder:
            title = name.strip()

    return {
        "id": session_id,
        "name": title,
        "cwd": str(ws_data.get("cwd", "")),
        "branch": str(ws_data.get("branch", "")),
        "created_at": str(ws_data.get("created_at", "")),
        "updated_at": str(ws_data.get("updated_at", "")),
        "event_count": event_count,
        "turn_count": turn_count,
        "live": _has_live_session(entry),
    }


def session_cwd(session_id: str) -> Path | None:
    """Return the recorded CWD for one valid local Copilot session."""
    meta = _session_meta(_session_state_dir(), session_id)
    if not meta:
        return None
    cwd = str(meta.get("cwd", "")).strip()
    if not cwd:
        return None
    path = Path(cwd).expanduser()
    return path if path.is_dir() else None


def list_worktree_sessions(record) -> list[dict]:
    """Enumerate the Copilot sessions associated with a worktree.

    Uses the worktree's session registry (``record.sessions``) and
    random-accesses only those session dirs. Per the invariant (GH #198) a
    pre-registry record (``sessions is None``) is **not** auto-backfilled here
    -- that would sweep all of session-state on a routine read. Until an
    explicit backfill (``agent-worktrees backfill-sessions`` / ``doctor``) runs,
    such a worktree lists no sessions. Each entry carries display metadata (see
    :func:`_session_meta`).  Sorted newest-first by ``updated_at``.
    """
    session_dir = _session_state_dir()
    if not session_dir.exists() or not record.worktree_path:
        return []

    out: list[dict] = []
    seen: set[str] = set()

    def _add(sid: str) -> None:
        if sid in seen:
            return
        meta = _session_meta(session_dir, sid)
        if meta is not None:
            seen.add(sid)
            out.append(meta)

    sessions = getattr(record, "sessions", None)
    if sessions is not None:
        for entry in sessions:
            _add(entry.session_id)
    else:
        # Invariant (GH #198): pre-registry records are NOT auto-backfilled
        # here -- an implicit backfill sweeps all of session-state, which a
        # routine read must never do. Backfill is an explicit maintenance
        # action (``agent-worktrees backfill-sessions`` / ``doctor``); until it
        # runs, a pre-registry worktree simply lists no sessions.
        pass

    out.sort(key=lambda s: s.get("updated_at", ""), reverse=True)

    # session-lifecycle: stamp the ASSERTED lifecycle onto each entry so a
    # consumer (agent-bridge -> Neuron Forge) can resolve the head-first current
    # session and badge the rest "no longer current" (agent-fabric
    # single-current-session-per-worktree, Phase 4). ``state`` is the per-session
    # SessionEntry.state (default ``active`` for legacy/backfill entries with no
    # stamp); ``is_head`` marks the one session ``resolved_head_session``
    # derives as current. Derived, not a rival store -- consumers read this, they
    # do not recompute a head of their own.
    head = record.resolved_head_session
    for meta in out:
        sid = meta.get("id")
        entry = record.session_entry(sid) if sid else None
        meta["state"] = entry.state if entry is not None else "active"
        meta["is_head"] = sid is not None and sid == head
        if entry is not None:
            meta["started_at_marker"] = entry.started_at
            meta["ended_at_marker"] = entry.ended_at
            meta["activations"] = [
                {
                    "ordinal": activation.ordinal,
                    "started_at": activation.started_at,
                    "start_recorded_at": activation.start_recorded_at,
                    "start_source": activation.start_source,
                    "ended_at": activation.ended_at,
                    "end_recorded_at": activation.end_recorded_at,
                    "end_source": activation.end_source,
                }
                for activation in entry.activations
            ]
    return out


def read_session_transcript(session_id: str) -> list[dict]:
    """Return the renderable events for a single Copilot session.

    Reads ``~/.copilot/session-state/<session_id>/events.jsonl`` and
    returns the subset of events that render meaningfully in a transcript
    view (see ``_RENDERABLE_EVENT_TYPES``).  Returns an empty list if the
    session or its event log is absent.
    """
    session_dir = _session_state_dir()
    events_file = session_dir / session_id / "events.jsonl"
    if not events_file.is_file():
        return []

    events: list[dict] = []
    try:
        with open(events_file, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(ev, dict) and ev.get("type", "") in _RENDERABLE_EVENT_TYPES:
                    events.append(ev)
    except OSError:
        return []
    return events


# The event types that carry an actual conversational turn (as opposed to the
# tool/lifecycle chatter in ``_RENDERABLE_EVENT_TYPES``). The recent-messages
# viewer shows only these -- the human-readable back-and-forth.
_CONVERSATION_EVENT_TYPES = {"user.message": "user",
                             "assistant.message": "assistant"}


def _event_text(ev: dict) -> str:
    """Extract the displayable text from a user/assistant message event.

    Both carry the turn text under ``data.content``; an assistant turn that is
    *only* tool calls has an empty ``content`` (its work is the tool requests,
    not prose). Returns the stripped text, or "" when there is nothing to show.
    """
    data = ev.get("data")
    if not isinstance(data, dict):
        return ""
    content = data.get("content", "")
    return content.strip() if isinstance(content, str) else ""


def recent_worktree_messages(record, *, limit: int = 3) -> dict:
    """The last *limit* conversational messages of a worktree's latest session.

    The read-side companion to the disposition ``summary`` overlay (see
    ``tracking.set_disposition``): when the agent-asserted summary is missing or
    stale, this derives *what the worktree was actually doing* straight from the
    latest session's ``events.jsonl`` -- the last human/assistant turns, newest
    last. Owned by the same session/summary layer that stores the disposition so
    the Picker has a single place to ask "what is this worktree?".

    Picks the worktree's newest session (``list_worktree_sessions`` is sorted
    newest-first), then returns its final *limit* ``user.message`` /
    ``assistant.message`` turns that carry text (tool-only assistant turns are
    skipped). Never raises: a worktree with no session / no transcript yields an
    empty ``messages`` list and a ``None`` ``session_id``.

    Returns a JSON-ready dict::

        {"session_id": "<id>|None",
         "messages": [{"role": "user|assistant",
                       "text": "...",
                       "timestamp": "<iso>"}, ...],
         "count": <int>}          # messages returned (<= limit)
    """
    lim = max(1, int(limit))
    sess_list = list_worktree_sessions(record)
    if not sess_list:
        return {"session_id": None, "messages": [], "count": 0}
    session_id = sess_list[0]["id"]

    messages: list[dict] = []
    for ev in read_session_transcript(session_id):
        role = _CONVERSATION_EVENT_TYPES.get(ev.get("type", ""))
        if role is None:
            continue
        text = _event_text(ev)
        if not text:
            continue
        messages.append({"role": role, "text": text,
                         "timestamp": str(ev.get("timestamp", ""))})

    tail = messages[-lim:]
    return {"session_id": session_id, "messages": tail, "count": len(tail)}


@dataclass
class MuxInfo:
    """Multiplexer session status for a worktree."""

    exists: bool = False
    """Whether a tmux/psmux session exists for this worktree."""

    clients: int | None = None
    """Number of attached terminal clients, or None if unknown."""

    @property
    def attached(self) -> bool | None:
        """Whether a human terminal is attached.

        Returns None if client count is unknown (e.g. psmux fallback).
        """
        if self.clients is None:
            return None
        return self.clients > 0


@dataclass
class LiveVerdict:
    """Authoritative single-worktree liveness, for the menu-open / Enter moments.

    The picker-populate path derives liveness cheaply and in bulk (batched
    ``list-sessions`` + the per-worktree session registry). This verdict is the
    opposite trade: the *truth* for the ONE worktree the operator is about to act
    on, paid for only at (a) Actions-menu open and (b) Enter->Open/Resume. It
    combines mux presence with the authoritative, **cwd-independent**
    ``inuse.<pid>.lock`` binding (via :mod:`reclaim`), so it also catches a
    **bare** (un-muxed) bound Copilot that the mux fleet view cannot see -- the
    session-registration reality that the picker's cwd-keyed populate scan misses
    (test-chamber #662/#1416).
    """

    active: bool = False
    """A live mux session OR a live bound Copilot owns the worktree right now."""

    mux_live: bool = False
    """A ``wt-<id>`` mux session exists."""

    mux_clients: int = 0
    """Attached terminal clients on the mux session (0 when unknown/none)."""

    live_session_ids: list[str] = field(default_factory=list)
    """Session ids with a live ``inuse.<pid>.lock`` (a bound Copilot process)."""

    bare: bool = False
    """A bound Copilot exists with NO mux ancestor (an un-muxed/orphaned one)."""

    source: str = "none"
    """Where liveness came from: ``mux`` | ``lock`` | ``both`` | ``none``."""


def verify_worktree_active(record) -> "LiveVerdict":
    """Authoritatively verify whether ONE worktree has a live session right now.

    Unlike the batched populate scan, this pays for a precise single-worktree
    truth-check: the mux ``has-session`` presence AND the authoritative
    ``inuse.<pid>.lock`` binding resolved by :func:`reclaim.resolve_bound_copilots`
    (which binds pid<->session<->worktree from the lock file, not a cwd guess, so
    a bare Copilot -- launched straight in a terminal and ``/resume``-d -- is
    still found as long as its session is registered on the record). Intended for
    the exact moments the operator acts on a worktree (Actions-menu open,
    Enter->Open/Resume), never the fleet-wide populate.

    Returns a :class:`LiveVerdict`. Best-effort and never raises: a mux or
    reclaim hiccup degrades to whichever signal succeeded.
    """
    from . import reclaim

    wt_id = record.worktree_id
    try:
        info = mux_status_many([wt_id]).get(wt_id) or MuxInfo()
    except Exception:
        info = MuxInfo()
    mux_live = bool(info.exists)

    live_ids: list[str] = []
    bare = False
    try:
        bound = reclaim.resolve_bound_copilots(worktree_id=wt_id)
        live_ids = sorted({b["session_id"] for b in bound if b.get("session_id")})
        bare = any(b.get("homing") == "bare" for b in bound)
    except Exception:
        pass
    lock_live = bool(live_ids)

    if mux_live and lock_live:
        source = "both"
    elif mux_live:
        source = "mux"
    elif lock_live:
        source = "lock"
    else:
        source = "none"

    return LiveVerdict(
        active=(mux_live or lock_live),
        mux_live=mux_live,
        mux_clients=(info.clients or 0),
        live_session_ids=live_ids,
        bare=bare,
        source=source,
    )


def mux_session_name(worktree_id: str) -> str:
    """Multiplexer session name for a worktree, safe to use as a target spec.

    Both tmux and psmux parse ``.`` in a target as the ``window.pane``
    separator, so a worktree id containing a dot produces a session that can be
    *created* but never addressed again::

        $ tmux new-session -d -s wt-host.local-linux-20260828-113232-c3c7   # ok
        $ tmux has-session -t '=wt-host.local-linux-20260828-113232-c3c7'
        can't find window: wt-host

    Worktree ids embed the machine name, and a machine keyed by its raw
    hostname is routinely dotted (every default macOS box is ``<name>.local``),
    so the launcher created a session and then failed every subsequent
    ``has-session`` / ``attach-session`` / ``set-option`` against it. The ``=``
    exact-match prefix does not help: the target is split on ``.`` before the
    session name is matched.

    Map ``.`` to ``_``. This is a no-op for the dot-free ids that most machines
    already produce, so it does not orphan their running sessions.
    """
    return "wt-" + (worktree_id or "base").replace(".", "_")


def mux_session_index(known_ids: Iterable[str]) -> dict[str, str]:
    """Map mux session name -> worktree id, for O(1) repeated resolution.

    Build once and pass as ``index=`` when resolving many sessions; otherwise a
    per-session scan over every known id is O(sessions x records).
    """
    return {mux_session_name(wt_id): wt_id for wt_id in known_ids}


def worktree_id_from_mux_session(
    session_name: str,
    known_ids: Iterable[str] = (),
    *,
    index: dict[str, str] | None = None,
) -> str:
    """Inverse of :func:`mux_session_name` -- session name -> worktree id.

    The ``.`` -> ``_`` mapping is **lossy**, so stripping the ``wt-`` prefix does
    not recover a dotted id. Resolve against *known_ids* (ids we already hold,
    e.g. from the tracking records) and only fall back to the stripped name,
    which stays correct for the dot-free ids most machines produce.

    Callers that use the result as a record key MUST pass *known_ids* (or a
    prebuilt *index*): a lookup miss can be read as "untracked" and a tracked
    session reaped.
    """
    if not session_name.startswith("wt-"):
        return ""
    if index is None:
        index = mux_session_index(known_ids)
    return index.get(session_name) or session_name[len("wt-"):]


def has_mux_session(worktree_id: str) -> bool:
    """Check if a multiplexer session exists for a worktree (without killing it).

    Uses tmux on Linux/WSL and psmux on Windows.

    Returns True if the mux session is alive, False otherwise.
    """
    import subprocess

    sess_name = mux_session_name(worktree_id)
    if platform.system() == "Windows":
        cmd = ["psmux", "has-session", "-t", sess_name]
    else:
        cmd = ["tmux", "has-session", "-t", f"={sess_name}"]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=5)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        # OSError covers FileNotFoundError (mux not installed) as well as
        # spawn failures such as WinError 4551 (Application Control policy
        # blocked the executable). Degrade gracefully instead of crashing.
        return False


def _list_mux_sessions() -> dict[str, int] | None:
    """Query all mux sessions with their attached client counts.

    Returns a dict of session_name -> attached_client_count, or None if
    the list-sessions command is unavailable or fails.
    """
    import subprocess

    if platform.system() == "Windows":
        cmd = ["psmux", "list-sessions", "-F", "#{session_name}:#{session_attached}"]
    else:
        cmd = ["tmux", "list-sessions", "-F", "#{session_name}:#{session_attached}"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            return None
        sessions_map: dict[str, int] = {}
        for line in result.stdout.strip().splitlines():
            if ":" not in line:
                continue
            name, _, count_str = line.rpartition(":")
            try:
                sessions_map[name] = int(count_str)
            except ValueError:
                sessions_map[name] = 0
        return sessions_map
    except (OSError, subprocess.TimeoutExpired):
        # OSError covers FileNotFoundError (mux not installed) as well as
        # spawn failures such as WinError 4551 (Application Control policy
        # blocked the executable). Degrade gracefully instead of crashing.
        return None


def _mux_session_activity() -> dict[str, int]:
    """Query each mux session's last-activity time (epoch seconds).

    ``#{session_activity}`` reflects real pane output/input, so a session whose
    Copilot is mid-turn or running a background task reads *recent*, while one
    parked idle at a prompt goes stale -- exactly the signal the reaper needs to
    never kill a **busy** session (#713). Returns ``session_name -> epoch``;
    ``{}`` when the mux or the field is unavailable (both tmux and psmux support
    it, but degrade safely to an empty map rather than crash).
    """
    import subprocess

    if platform.system() == "Windows":
        cmd = ["psmux", "list-sessions", "-F",
               "#{session_name}:#{session_activity}"]
    else:
        cmd = ["tmux", "list-sessions", "-F",
               "#{session_name}:#{session_activity}"]
    out: dict[str, int] = {}
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            return {}
        for line in result.stdout.strip().splitlines():
            if ":" not in line:
                continue
            name, _, ts = line.rpartition(":")
            try:
                out[name] = int(ts)
            except ValueError:
                continue
    except (OSError, subprocess.TimeoutExpired):
        return {}
    return out


def mux_status_many(worktree_ids: list[str]) -> dict[str, MuxInfo]:
    """Get mux session status for multiple worktrees efficiently.

    Uses a single ``list-sessions`` call when available. Falls back to
    per-worktree ``has-session`` checks if list-sessions is unsupported
    (clients will be None in that case).
    """
    result: dict[str, MuxInfo] = {}

    all_sessions = _list_mux_sessions()
    if all_sessions is not None:
        for wt_id in worktree_ids:
            sess_name = mux_session_name(wt_id)
            if sess_name in all_sessions:
                result[wt_id] = MuxInfo(exists=True, clients=all_sessions[sess_name])
            else:
                result[wt_id] = MuxInfo(exists=False, clients=0)
    else:
        # Fallback: per-worktree has-session (no client count available)
        for wt_id in worktree_ids:
            exists = has_mux_session(wt_id)
            result[wt_id] = MuxInfo(exists=exists, clients=None)

    return result


def kill_tmux_session(worktree_id: str) -> bool:
    """Kill the multiplexer session associated with a worktree, if one exists.

    Uses tmux on Linux/WSL and psmux on Windows.

    Returns True if a session was killed, False if none existed or the
    multiplexer is not available.
    """
    import subprocess

    sess_name = mux_session_name(worktree_id)
    if platform.system() == "Windows":
        cmd = ["psmux", "kill-session", "-t", sess_name]
    else:
        cmd = ["tmux", "kill-session", "-t", f"={sess_name}"]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=5)
        killed = result.returncode == 0
        try:
            from . import reap_audit
            reap_audit.record("mux-session", sess_name,
                              reason="kill_tmux_session", killed=killed,
                              worktree_id=worktree_id)
        except Exception:
            pass
        return killed
    except (OSError, subprocess.TimeoutExpired):
        # OSError covers FileNotFoundError (mux not installed) as well as
        # spawn failures such as WinError 4551 (Application Control policy
        # blocked the executable). Degrade gracefully instead of crashing.
        return False


def _mux_send_keys(worktree_id: str, keys: str) -> bool:
    """Send a key sequence to a worktree's mux pane (tmux/psmux ``send-keys``).

    ``keys`` uses tmux key syntax (e.g. ``"C-c"`` for Ctrl-C). Returns True if
    the command succeeded, False if the session/mux is gone or unavailable.
    """
    import subprocess

    sess_name = mux_session_name(worktree_id)
    if platform.system() == "Windows":
        cmd = ["psmux", "send-keys", "-t", sess_name, keys]
    else:
        # ``send-keys`` needs a *pane* target: the bare ``=wt-<id>`` exact-match
        # form (valid for has-session/kill-session) is rejected as "can't find
        # pane", so append ``:`` to address the session's active pane while
        # keeping the ``=`` exact-session match (avoids hitting a ``wt-<id>-x``
        # sibling).
        cmd = ["tmux", "send-keys", "-t", f"={sess_name}:", keys]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=5)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def graceful_quit_mux_session(
    worktree_id: str,
    *,
    settle_timeout: float = 6.0,
    poll_interval: float = 0.3,
    ctrl_c_gap: float = 0.5,
    escalate_after: float = 1.5,
) -> bool:
    """Ask the interactive Copilot in a worktree's mux session to quit cleanly.

    Copilot CLI exits on a **double Ctrl-C** -- two interrupts ~300-800 ms
    apart. We deliver them via the multiplexer's ``send-keys`` (tmux on
    Linux/WSL, psmux on Windows), which is Copilot's *native* clean-quit path:
    it lets Copilot tear down its own session rather than being signalled out
    from under (a plain ``SIGTERM`` to the pane only ``SIGHUP``s Copilot when
    its shell dies, which is no cleaner than the hard kill below). When Copilot
    exits, the pane's only command ends, dropping the single-window
    ``wt-<id>`` session.

    **Escalation ladder (up to three Ctrl-C).** Two interrupts is the common
    case, but some Copilot states swallow the second (a prompt mid-render, a
    modal, a busy turn flushing state). So after the double-interrupt we wait a
    *brief* ``escalate_after`` window; if the session is still alive we deliver
    a **conditional third** Ctrl-C within the same burst before falling back to
    the hard kill. The third still routes through Copilot's own interrupt
    handling, so session state is persisted (letting a later ACP resume pick it
    back up) rather than being severed by a signal.

    Returns True if the session ended within ``settle_timeout`` (graceful quit
    succeeded), False otherwise (the caller should fall back to a hard
    ``kill_tmux_session``). A worktree with no live mux session counts as
    already quit (True).
    """
    import time

    if not has_mux_session(worktree_id):
        return True

    def _dropped_within(window: float) -> bool:
        """Poll until the session drops or ``window`` seconds elapse."""
        deadline = time.monotonic() + window
        while time.monotonic() < deadline:
            if not has_mux_session(worktree_id):
                return True
            time.sleep(poll_interval)
        return not has_mux_session(worktree_id)

    # First Ctrl-C. If the first send fails the mux is already gone.
    if not _mux_send_keys(worktree_id, "C-c"):
        return not has_mux_session(worktree_id)
    time.sleep(ctrl_c_gap)
    # Second Ctrl-C, ``ctrl_c_gap`` after the first (default 0.5 s, within
    # Copilot's 300-800 ms double-interrupt window) -- the native clean quit.
    _mux_send_keys(worktree_id, "C-c")

    # Give the double-interrupt a *brief* window to land: Copilot flushes and
    # persists its session state, then exits, dropping the pane's only command.
    escalate_at = min(max(escalate_after, 0.0), settle_timeout)
    if _dropped_within(escalate_at):
        return True

    # Still alive after two -- deliver the conditional THIRD Ctrl-C, then wait
    # out the remaining budget before the caller resorts to a hard kill.
    _mux_send_keys(worktree_id, "C-c")
    return _dropped_within(settle_timeout - escalate_at)


def restart_worktree_copilot(
    worktree_id: str,
    *,
    graceful: bool = True,
    settle_timeout: float = 6.0,
) -> dict:
    """Terminate the interactive Copilot holding a worktree, keeping the worktree.

    The shared primitive behind the Picker **"Stop"** row action and
    Neuron-Forge **"Take over"**: it stops the running interactive Copilot (its
    ``wt-<id>`` tmux/psmux session) **without** removing the git worktree, so the
    caller can relaunch interactively (Picker) or ACP-resume (NF) afterwards.

    Ladder: with ``graceful`` (default), first ask Copilot to quit cleanly via a
    double Ctrl-C (:func:`graceful_quit_mux_session`); if it does not exit within
    ``settle_timeout``, hard-kill the mux session. With ``graceful=False`` it
    hard-kills immediately.

    Returns a JSON-able dict ``{worktree_id, had_session, method, ok}`` where
    ``method`` is ``none`` (nothing was running), ``graceful``, ``hard``, or
    ``failed``.
    """
    if not has_mux_session(worktree_id):
        _stamp_mux_live_quiet(worktree_id, False)
        return {
            "worktree_id": worktree_id, "had_session": False,
            "method": "none", "ok": True,
        }
    if graceful and graceful_quit_mux_session(
        worktree_id, settle_timeout=settle_timeout,
    ):
        _stamp_mux_live_quiet(worktree_id, False)
        return {
            "worktree_id": worktree_id, "had_session": True,
            "method": "graceful", "ok": True,
        }
    killed = kill_tmux_session(worktree_id)
    if killed:
        _stamp_mux_live_quiet(worktree_id, False)
    return {
        "worktree_id": worktree_id, "had_session": True,
        "method": "hard" if killed else "failed", "ok": killed,
    }


def _stamp_mux_live_quiet(worktree_id: str, live: bool) -> None:
    """Best-effort #4057 cached-liveness stamp; never disturbs the caller."""
    try:
        from . import tracking
        # sync: this is a lifecycle transition (post-kill), not the render path,
        # and durability before the next populate is preferred over off-loading.
        tracking.stamp_mux_live(worktree_id, live, sync=True)
    except Exception:
        pass


# ── Live-cutover handoff mux primitives (issue #2250) ─────────────────────
# A live handoff spawns a *seeded successor* Copilot in a NEW window of the
# same ``wt-<id>`` session (preserving session identity + status bar), cuts the
# operator over to it, and later retires the OLD pane. These helpers are the
# platform-aware mux verbs behind ``agent-worktrees handoff-cutover``.

# Identity env vars stripped from a child Copilot so the session carries no
# ambient project/worktree identity (in-session tools resolve from CWD). Mirror
# of the ``env -u`` prefix in launch-session.sh.
_IDENTITY_ENV_VARS = ("WORKTREE_PROJECT", "WORKTREE_ID")

# Space-free transport flags for a native Copilot
# ``--interactive <prompt>`` launch. psmux flattens the pane command argv before
# CreateProcess, so the multi-word prompt travels as base64 to the pane wrapper.
# A receipt token lets the parent verify that the wrapper decoded and appended
# the real argument before declaring the successor seeded.
_INITIAL_PROMPT_B64_FLAG = "--aw-prompt-b64"
_INITIAL_PROMPT_RECEIPT_B64_FLAG = "--aw-prompt-receipt-b64"


def _initial_prompt_receipt_path(token: str) -> Path:
    """Return the wrapper receipt path for one generated prompt token."""
    return Path.home() / ".agent-worktrees" / "handoff-prompt-receipts" / token


def _mux_bin(mux: str | None = None) -> str:
    """Resolve the multiplexer binary name (psmux on Windows, tmux elsewhere)."""
    if mux:
        return mux
    return "psmux" if platform.system() == "Windows" else "tmux"


def _mux_session_target(worktree_id: str, mux_bin: str) -> str:
    """Session target string. tmux uses the ``=`` exact-match prefix; psmux
    does not support it (rejected as an unknown session)."""
    return _mux_named_session_target(mux_session_name(worktree_id), mux_bin)


def _mux_named_session_target(session_name: str, mux_bin: str) -> str:
    """Return an exact mux target for an already-known session name."""
    sess = str(session_name)
    return sess if mux_bin == "psmux" else f"={sess}"


def current_mux_session(
    pane_id: str | None = None,
    *,
    mux: str | None = None,
) -> str | None:
    """Return the mux session containing ``pane_id`` (or this process).

    Unlike worktree launch sessions, an adopted anchor may be running inside a
    caller-owned mux whose name is not ``wt-<id>``.  Handoff cutover uses this
    exact lookup rather than guessing a synthetic session name.
    """
    import subprocess

    mux_bin = _mux_bin(mux)
    argv = [mux_bin, "display-message", "-p"]
    if pane_id:
        argv += ["-t", pane_id]
    argv.append("#{session_name}")
    try:
        result = subprocess.run(
            argv, capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return None
        name = result.stdout.strip()
        return name or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def has_mux_session_named(
    session_name: str,
    *,
    mux: str | None = None,
) -> bool:
    """Check whether an exact, caller-supplied mux session exists."""
    import subprocess

    mux_bin = _mux_bin(mux)
    target = _mux_named_session_target(session_name, mux_bin)
    try:
        result = subprocess.run(
            [mux_bin, "has-session", "-t", target],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _mux_pane_cmd(
    worktree_id: str,
    cmd: list[str],
    *,
    is_tmux: bool,
    pane_wrapper: str | None = None,
    initial_prompt: str | None = None,
    prompt_receipt: str | None = None,
) -> list[str]:
    """Build the in-pane command vector shared by new-window and new-session.

    On Linux/WSL (tmux) the command is prefixed with ``env -u <identity vars>``
    and wrapped by ``pane-wrapper.sh`` (when present). On Windows (psmux) the
    server env is already identity-clean; ``pane-wrapper.ps1`` preserves the
    verbatim ``pwsh -File <script> … --allow-all`` child argv while decoding a
    space-free base64 control argument after psmux's lossy argv reconstruction.
    Every pane-command element remains a single token (psmux cannot carry an
    element containing spaces -- see :func:`build_mux_new_window_argv`).

    Native interactive handoff seeding requires the wrapper: without it there is
    no safe place after psmux to reconstruct the multi-word prompt, so failing
    the spawn is safer than opening an unseeded successor.
    """
    controls: list[str] = []
    if initial_prompt is not None:
        if not prompt_receipt:
            raise RuntimeError(
                "initial prompt transport requires a receipt token"
            )
        encoded = base64.b64encode(initial_prompt.encode("utf-8")).decode("ascii")
        receipt_encoded = base64.b64encode(
            prompt_receipt.encode("utf-8")
        ).decode("ascii")
        controls = [
            _INITIAL_PROMPT_B64_FLAG, encoded,
            _INITIAL_PROMPT_RECEIPT_B64_FLAG, receipt_encoded,
        ]
    wrapper = pane_wrapper
    if wrapper is None:
        name = "pane-wrapper.sh" if is_tmux else "pane-wrapper.ps1"
        wrapper = os.path.expanduser(f"~/.agent-worktrees/bin/{name}")
    if wrapper and os.path.isfile(wrapper) and os.access(wrapper, os.R_OK):
        if is_tmux:
            clean: list[str] = ["env"]
            for var in _IDENTITY_ENV_VARS:
                clean += ["-u", var]
            return clean + [
                "bash", wrapper, "--aw-wt", worktree_id, *controls, *cmd,
            ]
        # psmux space-joins pane argv, so even the wrapper path itself cannot be
        # passed literally when a Windows profile contains spaces. Carry the
        # wrapper path + its complete argv inside PowerShell's space-free
        # UTF-16LE EncodedCommand payload instead.
        wrapper_args = ["-AwWt", worktree_id, *controls, *cmd]
        wrapper_b64 = base64.b64encode(
            wrapper.encode("utf-8")
        ).decode("ascii")
        args_b64 = base64.b64encode(
            json.dumps(wrapper_args).encode("utf-8")
        ).decode("ascii")
        script = (
            "$w=[Text.Encoding]::UTF8.GetString("
            f"[Convert]::FromBase64String('{wrapper_b64}'));"
            "$j=[Text.Encoding]::UTF8.GetString("
            f"[Convert]::FromBase64String('{args_b64}'));"
            "$a=@(ConvertFrom-Json -InputObject $j);"
            "& $w @a;"
            "exit $LASTEXITCODE"
        )
        encoded_command = base64.b64encode(
            script.encode("utf-16-le")
        ).decode("ascii")
        return [
            "pwsh.exe", "-NoProfile", "-NoLogo",
            "-EncodedCommand", encoded_command,
        ]
    if initial_prompt is not None:
        raise RuntimeError(
            "pane wrapper is required for native interactive prompt transport"
        )
    if not is_tmux:
        # psmux fallback: run verbatim; keep every element single-token.
        return list(cmd)
    clean = ["env"]
    for var in _IDENTITY_ENV_VARS:
        clean += ["-u", var]
    return clean + list(cmd)


def build_mux_new_window_argv(
    worktree_id: str,
    work_dir: str,
    cmd: list[str],
    env: dict[str, str] | None = None,
    *,
    mux: str | None = None,
    pane_wrapper: str | None = None,
    initial_prompt: str | None = None,
    prompt_receipt: str | None = None,
    session_name: str | None = None,
) -> list[str]:
    """Build the argv to open a new window in a mux session running ``cmd``.

    Mirrors the launcher's pane construction: the command is wrapped by the
    platform pane-wrapper when present; Linux/WSL additionally strips ambient
    identity vars. Profile env is re-propagated with ``-e`` for parity regardless
    of session-env inheritance. ``session_name`` defaults to ``wt-<id>``;
    adopted-anchor cutover passes the current caller-owned mux session instead.
    ``-P -F '#{pane_id}'`` prints the new pane id.
    """
    mux_bin = _mux_bin(mux)
    is_tmux = mux_bin != "psmux"
    target = (
        _mux_named_session_target(session_name, mux_bin)
        if session_name
        else _mux_session_target(worktree_id, mux_bin)
    )

    argv = [mux_bin, "new-window", "-P", "-F", "#{pane_id}", "-t", target]
    if work_dir:
        argv += ["-c", work_dir]
    for key, val in (env or {}).items():
        argv += ["-e", f"{key}={val}"]

    pane_cmd = _mux_pane_cmd(
        worktree_id,
        cmd,
        is_tmux=is_tmux,
        pane_wrapper=pane_wrapper,
        initial_prompt=initial_prompt,
        prompt_receipt=prompt_receipt,
    )

    # No ``--`` separator: mux option parsing stops at the first non-option
    # token (``env`` / the launcher binary), so the rest is taken as the
    # command verbatim -- matching launch-session.{sh,ps1}'s new-session call.
    argv += pane_cmd
    return argv


def build_mux_new_session_argv(
    worktree_id: str,
    work_dir: str,
    cmd: list[str],
    env: dict[str, str] | None = None,
    *,
    mux: str | None = None,
    pane_wrapper: str | None = None,
) -> list[str]:
    """Build the argv to create a **detached** ``wt-<id>`` session running ``cmd``.

    The new-session analogue of :func:`build_mux_new_window_argv`, used to
    *embody* a Copilot CLI in a worktree that has no mux session yet (D5). ``-d``
    keeps it detached -- the caller does not attach; the operator (or Neuron
    Forge) attaches later. ``-P -F '#{pane_id}'`` prints the new pane id. Same
    identity-clean + pane-wrapper construction as a new window, so an embodied
    session is indistinguishable from a picker-launched one.
    """
    mux_bin = _mux_bin(mux)
    is_tmux = mux_bin != "psmux"
    sess = mux_session_name(worktree_id)

    argv = [mux_bin, "new-session", "-d", "-s", sess, "-P", "-F", "#{pane_id}"]
    if work_dir:
        argv += ["-c", work_dir]
    for key, val in (env or {}).items():
        argv += ["-e", f"{key}={val}"]

    argv += _mux_pane_cmd(
        worktree_id,
        cmd,
        is_tmux=is_tmux,
        pane_wrapper=pane_wrapper,
    )
    return argv


def mux_new_session(
    worktree_id: str,
    work_dir: str,
    cmd: list[str],
    env: dict[str, str] | None = None,
    *,
    mux: str | None = None,
) -> dict:
    """Create a detached ``wt-<id>`` session running ``cmd``; return its pane.

    Returns ``{ok, session, new_pane, error}``. Detached, so the caller does not
    take over a terminal -- the embodied Copilot registers itself with the local
    bridge (Phase 1), which is how the spawn is later verified and viewed.
    """
    import subprocess

    sess = mux_session_name(worktree_id)
    try:
        argv = build_mux_new_session_argv(
            worktree_id, work_dir, cmd, env, mux=mux,
        )
        r = subprocess.run(argv, capture_output=True, text=True, timeout=15)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "session": sess, "new_pane": None, "error": str(e)}
    if r.returncode != 0:
        return {
            "ok": False, "session": sess, "new_pane": None,
            "error": r.stderr.strip() or f"exit {r.returncode}",
        }
    return {
        "ok": True, "session": sess,
        "new_pane": r.stdout.strip() or None, "error": None,
    }


def mux_seed_pane(
    pane_id: str,
    seed: str,
    *,
    mux: str | None = None,
    ready_timeout: float = 20.0,
    poll_interval: float = 0.5,
    settle: float = 0.6,
) -> dict:
    """Type ``seed`` as the first interactive prompt into a freshly spawned pane.

    A cutover spawns a *plain* interactive Copilot (no ``--interactive`` launch
    arg -- see :func:`build_mux_new_window_argv`: psmux cannot carry a
    spaces-containing pane arg on Windows), then this injects the seed as literal
    keystrokes once Copilot is ready. ``send-keys -l`` delivers the whole prompt
    (spaces and all) as one line -- the same mux mechanism the retire path uses --
    sidestepping every command-line quoting hazard.

    Hardened against seeding into the wrong pane state (a half-loaded TUI, or a
    pane that fell back to a bare shell whose ``❯`` looks like Copilot's caret):

    * **Confirmed-ready, not first-frame.** Readiness requires a Copilot cue (the
      ``❯`` caret or the ``esc … interrupt`` footer -- the generic rule line is no
      longer trusted) seen on **two consecutive** polls, so a transient
      banner/spinner frame can't trip it.
    * **Never blind-submit.** If readiness is not confirmed within
      ``ready_timeout`` the seed is **not** typed and Enter is **never** pressed --
      the successor just lands at a fresh prompt (safe) instead of executing a
      mistyped line. The caller sees ``sent``/``submitted`` false.
    * **Echo-verify before Enter.** After typing, the pane is captured and Enter
      is pressed **only** once a distinctive head of the seed is echoed there, so
      a partially-eaten seed is never submitted as a bogus turn.

    Returns ``{ok, pane, ready, sent, submitted, reason}`` -- ``ok`` is true only
    when the seed was actually delivered as a turn (``submitted``).
    """
    import re
    import subprocess
    import time

    mux_bin = _mux_bin(mux)

    def _cap() -> str:
        try:
            r = subprocess.run(
                [mux_bin, "capture-pane", "-p", "-t", pane_id],
                capture_output=True, text=True, timeout=5,
            )
            return r.stdout if r.returncode == 0 else ""
        except (OSError, subprocess.TimeoutExpired):
            return ""

    def _is_copilot_ready(cap: str) -> bool:
        low = cap.lower()
        # Copilot-specific cues only: the input caret, or the interrupt footer.
        # A bare rule line (``─────``) is NOT trusted -- banners/spinners draw it.
        return ("❯" in cap) or ("esc" in low and "interrupt" in low)

    # Readiness must be STABLE (two consecutive sightings) so a single transient
    # frame (a startup banner, a spinner) is not mistaken for the input prompt.
    ready = False
    stable = 0
    deadline = time.monotonic() + ready_timeout
    while time.monotonic() < deadline:
        if _is_copilot_ready(_cap()):
            stable += 1
            if stable >= 2:
                ready = True
                break
        else:
            stable = 0
        time.sleep(poll_interval)

    # Safety gate: without a confirmed-ready Copilot we do NOT type or submit --
    # blind keystrokes into a half-loaded TUI or a fallback shell could execute a
    # mistyped command. Degrade to "landed unseeded" (the operator can paste).
    if not ready:
        return {
            "ok": False, "pane": pane_id, "ready": False,
            "sent": False, "submitted": False, "reason": "not-ready-timeout",
        }

    def _send(*a: str) -> bool:
        try:
            r = subprocess.run(
                [mux_bin, "send-keys", "-t", pane_id, *a],
                capture_output=True, timeout=5,
            )
            return r.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    # A distinctive head of the seed, whitespace-squashed so terminal soft-wrap
    # (a newline inserted mid-line in the captured buffer) can't defeat the echo
    # check below.
    def _squash(s: str) -> str:
        return re.sub(r"\s+", "", s)

    head = _squash(seed)[:16]

    # ``-l`` sends the seed literally (no key-name interpretation), so the whole
    # multi-word prompt lands as one input line.
    sent = _send("-l", seed)
    time.sleep(settle)

    # Echo-verify: press Enter only once the seed's head is visible in the pane,
    # so a partially-eaten or lost seed is never submitted as a bogus turn.
    echoed = False
    if sent and head:
        for _ in range(4):
            if head in _squash(_cap()):
                echoed = True
                break
            time.sleep(poll_interval)
    elif sent:
        echoed = True  # empty seed: nothing to verify

    submitted = False
    reason = None
    if echoed:
        submitted = _send("Enter")
        if not submitted:
            reason = "enter-failed"
    else:
        reason = "seed-not-echoed" if sent else "send-failed"

    return {
        "ok": bool(submitted), "pane": pane_id, "ready": ready,
        "sent": bool(sent), "submitted": bool(submitted), "reason": reason,
    }


def mux_copilot_pane(
    worktree_id: str,
    session_id: str | None = None,
    *,
    mux: str | None = None,
) -> str | None:
    """Return the registry-bound Copilot pane for a worktree/session.

    The session registry is the portable authority for pane identity (tmux and
    psmux both expose the pane id in the Copilot pane's environment, while
    custom pane options are not portable).  A recorded pane is returned only
    while it still exists; otherwise this degrades to the historical active-pane
    heuristic.  Best-effort by design: callers must never fail because the mux
    or registry is unavailable.
    """
    try:
        from . import tracking

        record = tracking.load_record_by_id(worktree_id)
        if record is not None:
            target_session = session_id or getattr(record, "resolved_head_session", None)
            entry = record.session_entry(target_session) if target_session else None
            pane = getattr(entry, "pane_id", None) if entry is not None else None
            if isinstance(pane, str):
                pane = pane.strip()
            if pane:
                mux_bin = _mux_bin(mux)
                if _mux_pane_alive(pane, mux_bin):
                    return pane
    except Exception:
        pass

    try:
        if mux is None:
            return mux_active_pane(worktree_id)
        return mux_active_pane(worktree_id, mux=mux)
    except Exception:
        return None


def mux_active_pane(worktree_id: str, *, mux: str | None = None) -> str | None:
    """Return the active pane id (e.g. ``%3``) of ``wt-<id>``'s current window.

    This is the pane the operator is looking at -- the OLD Copilot, captured
    before a cutover so it can be retired afterward. Returns None if the session
    or mux is unavailable.
    """
    import subprocess

    mux_bin = _mux_bin(mux)
    target = _mux_session_target(worktree_id, mux_bin)
    try:
        r = subprocess.run(
            [mux_bin, "display-message", "-p", "-t", target, "#{pane_id}"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode != 0:
            return None
        pane = r.stdout.strip()
        return pane or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def mux_active_pane_named(
    session_name: str,
    *,
    mux: str | None = None,
) -> str | None:
    """Return the active pane of an exact, caller-supplied mux session."""
    import subprocess

    mux_bin = _mux_bin(mux)
    target = _mux_named_session_target(session_name, mux_bin)
    try:
        result = subprocess.run(
            [mux_bin, "display-message", "-p", "-t", target, "#{pane_id}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None
        pane = result.stdout.strip()
        return pane or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def mux_session_for_pane(
    pane_id: str,
    *,
    mux: str | None = None,
) -> str | None:
    """Return the exact mux session currently containing ``pane_id``."""
    return current_mux_session(pane_id, mux=mux)


def mux_binding_for_session(
    session_id: str,
    *,
    mux: str | None = None,
    expected_session_name: str | None = None,
) -> dict | None:
    """Recover a session's mux/worktree identity from its live lock process.

    Random-access the exact session directory from the sessionStart payload,
    read Copilot's authoritative ``inuse.<pid>.lock``, then match that live
    process's ancestry against mux pane roots. The owning ``wt-<id>`` session
    yields both the worktree and exact pane without trusting an incidental cwd
    or sweeping historical session state.
    """
    import subprocess

    if (
        not session_id
        or session_id in (".", "..")
        or "/" in session_id
        or "\\" in session_id
    ):
        return None
    entry = _session_state_dir() / session_id
    if not entry.is_dir():
        return None

    from . import locks, reclaim

    live_pid_start_times: dict[int, str] = {}
    try:
        for lock_file in entry.glob("inuse.*.lock"):
            parts = lock_file.stem.split(".")
            if len(parts) < 2:
                continue
            try:
                pid = int(parts[1])
            except ValueError:
                continue
            start_time = locks.process_start_time(pid)
            if (
                start_time
                and _is_copilot_process(pid)
                and locks.process_start_time(pid) == start_time
            ):
                live_pid_start_times[pid] = start_time
    except OSError:
        return None
    if not live_pid_start_times:
        return None

    table = reclaim.build_process_table()
    if not table:
        return None
    mux_bin = _mux_bin(mux)

    # Prefer the lock PID that is actually an ancestor of this sessionStart hook
    # process. This rejects a stale lock whose PID was reused by another live
    # Copilot before falling back to the lock's ordinary process validation.
    own_ancestry: set[int] = set()
    seen: set[int] = set()
    cur = os.getpid()
    while cur in table and cur not in seen and len(seen) < 64:
        seen.add(cur)
        own_ancestry.add(cur)
        cur = table[cur]["ppid"]
    live_pids = list(live_pid_start_times)
    owned_pids = [pid for pid in live_pids if pid in own_ancestry]
    if owned_pids:
        live_pids = owned_pids

    ancestry_by_pid: dict[int, set[int]] = {}
    for pid in live_pids:
        ancestry: set[int] = set()
        seen: set[int] = set()
        cur = pid
        while cur in table and cur not in seen and len(seen) < 64:
            seen.add(cur)
            ancestry.add(cur)
            parent = table[cur]["ppid"]
            if mux_bin == "psmux" and platform.system() == "Windows":
                child_started = _windows_process_start_time(cur)
                parent_started = _windows_process_start_time(parent)
                # Windows retains a creator PID after that process exits. If the
                # PID was reused, the apparent parent can be newer than its
                # child; reject that dangling edge instead of mis-binding.
                if (
                    child_started is None
                    or parent_started is None
                    or parent_started > child_started
                ):
                    break
            cur = parent
        ancestry_by_pid[pid] = ancestry

    try:
        result = subprocess.run(
            [
                mux_bin, "list-panes", "-a", "-F",
                "#{session_name}|#{pane_id}|#{pane_pid}",
            ],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None

    matches: dict[tuple[str, str, int, int], dict] = {}
    for line in result.stdout.splitlines():
        fields = line.strip().split("|")
        if len(fields) != 3:
            continue
        session_name, pane_id, pane_pid_raw = fields
        session_is_allowed = (
            session_name == expected_session_name
            if expected_session_name
            else session_name.startswith("wt-")
        )
        if not session_is_allowed or not pane_id:
            continue
        try:
            pane_pid = int(pane_pid_raw)
        except ValueError:
            continue
        for copilot_pid, ancestry in ancestry_by_pid.items():
            if pane_pid not in ancestry:
                continue
            copilot_start_time = live_pid_start_times[copilot_pid]
            if locks.process_start_time(copilot_pid) != copilot_start_time:
                continue
            key = (session_name, pane_id, pane_pid, copilot_pid)
            matches[key] = {
                "worktree_id": worktree_id_from_mux_session(session_name),
                "session_name": session_name,
                "pane_id": pane_id,
                "pane_pid": pane_pid,
                "pane_start_time": locks.process_start_time(pane_pid),
                "copilot_pid": copilot_pid,
                "copilot_start_time": copilot_start_time,
            }

    return next(iter(matches.values())) if len(matches) == 1 else None


def _windows_process_start_time(pid: int) -> int | None:
    """Return a Windows process creation FILETIME, or ``None`` on failure."""
    if pid <= 0:
        return None
    import ctypes
    from ctypes import wintypes

    kernel32 = _get_kernel32()
    process_query_limited_information = 0x1000
    handle = kernel32.OpenProcess(
        process_query_limited_information, False, pid
    )
    if not handle:
        return None
    try:
        created = wintypes.FILETIME()
        exited = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        kernel32.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
        ]
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            return None
        return (int(created.dwHighDateTime) << 32) | int(created.dwLowDateTime)
    finally:
        kernel32.CloseHandle(handle)


def mux_new_window(
    worktree_id: str,
    work_dir: str,
    cmd: list[str],
    env: dict[str, str] | None = None,
    *,
    mux: str | None = None,
    initial_prompt: str | None = None,
    prompt_receipt_timeout: float = 8.0,
    prompt_startup_grace: float = 3.5,
    session_name: str | None = None,
) -> dict:
    """Open + select a new window in a mux session running ``cmd``.

    ``new-window`` selects the new window by default (no ``-d``), so the
    operator is cut over to the successor immediately. Returns
    ``{ok, new_pane, error}``.
    """
    import secrets
    import subprocess
    import time

    receipt_token = secrets.token_hex(16) if initial_prompt is not None else None
    receipt_path = (
        _initial_prompt_receipt_path(receipt_token) if receipt_token else None
    )
    if receipt_path:
        receipt_path.unlink(missing_ok=True)
    try:
        argv = build_mux_new_window_argv(
            worktree_id,
            work_dir,
            cmd,
            env,
            mux=mux,
            initial_prompt=initial_prompt,
            prompt_receipt=str(receipt_path) if receipt_path else None,
            session_name=session_name,
        )
        r = subprocess.run(argv, capture_output=True, text=True, timeout=15)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "new_pane": None, "error": str(e)}
    if r.returncode != 0:
        return {
            "ok": False, "new_pane": None,
            "error": r.stderr.strip() or f"exit {r.returncode}",
        }
    new_pane = r.stdout.strip() or None
    prompt_received = initial_prompt is None
    prompt_status = None
    if receipt_path:
        deadline = time.monotonic() + prompt_receipt_timeout
        while time.monotonic() < deadline:
            if receipt_path.exists():
                try:
                    candidate = receipt_path.read_text("utf-8").strip()
                except OSError:
                    candidate = ""
                if candidate == "launching" or candidate.startswith("failed:"):
                    prompt_status = candidate
                    break
            time.sleep(0.05)
        if prompt_status == "launching":
            startup_deadline = time.monotonic() + prompt_startup_grace
            mux_bin = _mux_bin(mux)
            while time.monotonic() < startup_deadline:
                try:
                    prompt_status = receipt_path.read_text("utf-8").strip()
                except OSError:
                    prompt_status = None
                if prompt_status != "launching":
                    break
                if not new_pane or not _mux_pane_alive(new_pane, mux_bin):
                    prompt_status = "failed:pane-exited"
                    break
                time.sleep(0.05)
            prompt_received = bool(
                prompt_status == "launching"
                and new_pane
                and _mux_pane_alive(new_pane, mux_bin)
            )
            if prompt_status == "launching" and not prompt_received:
                prompt_status = "failed:pane-exited"
        receipt_path.unlink(missing_ok=True)
        if not prompt_received:
            # Snapshot at the failure boundary, not immediately after new-window:
            # the wrapper starts Copilot after writing its provisional receipt,
            # so only the late tree is guaranteed to include the real child.
            process_tree = _mux_pane_process_tree(new_pane, mux=mux)
            cleanup = _retire_failed_successor(
                new_pane,
                process_tree,
                mux=mux,
            )
            return {
                "ok": False,
                "new_pane": new_pane,
                "prompt_received": False,
                "prompt_status": prompt_status,
                "cleanup": cleanup,
                "error": (
                    "successor did not confirm a stable native interactive "
                    f"prompt launch (status: {prompt_status or 'no-receipt'})"
                ),
            }
    return {
        "ok": True,
        "new_pane": new_pane,
        "prompt_received": prompt_received,
        "prompt_status": prompt_status,
        "error": None,
    }


def _mux_pane_pid(pane_id: str | None, *, mux: str | None = None) -> int | None:
    """Return the root pid of one mux pane, or ``None`` when unavailable."""
    if not pane_id:
        return None
    import subprocess

    mux_bin = _mux_bin(mux)
    try:
        r = subprocess.run(
            [
                mux_bin, "display-message", "-p", "-t", pane_id,
                "#{pane_pid}",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode != 0:
            return None
        pid = int(r.stdout.strip())
        return pid if pid > 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _mux_pane_process_tree(
    pane_id: str | None, *, mux: str | None = None,
) -> set[int]:
    """Snapshot the exact process tree rooted at ``pane_id`` before teardown."""
    pane_pid = _mux_pane_pid(pane_id, mux=mux)
    if not pane_pid:
        return set()
    try:
        from . import reclaim

        table = reclaim.build_process_table()
        return {pane_pid, *reclaim.descendants_of(pane_pid, table)}
    except OSError:
        return {pane_pid}


def _retire_failed_successor(
    pane_id: str | None,
    process_tree: set[int],
    *,
    mux: str | None = None,
) -> dict:
    """Retire a failed successor pane and terminate its exact surviving tree."""
    import platform
    import signal
    import time

    retire = (
        mux_retire_pane(pane_id, mux=mux)
        if pane_id else {"ok": True, "gone": True, "method": "no-pane"}
    )
    terminated: list[int] = []
    survivors: list[int] = []
    try:
        from . import locks, procs

        # Children first, pane root last. The snapshot is pane-specific, so this
        # cannot splash onto the predecessor or another pane in the same worktree.
        for pid in sorted(process_tree, reverse=True):
            if not locks.pid_alive(pid):
                continue
            if procs.terminate_pid(pid):
                terminated.append(pid)
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            survivors = [
                pid for pid in sorted(process_tree) if locks.pid_alive(pid)
            ]
            if not survivors:
                break
            time.sleep(0.05)
        # procs.terminate_pid is SIGTERM on POSIX. Escalate the exact pane tree
        # after the bounded grace period; Windows already uses TerminateProcess.
        if survivors and platform.system() != "Windows":
            for pid in survivors:
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                survivors = [
                    pid for pid in survivors if locks.pid_alive(pid)
                ]
                if not survivors:
                    break
                time.sleep(0.05)
    except OSError:
        survivors = sorted(process_tree)
    return {
        "retire": retire,
        "process_tree": sorted(process_tree),
        "terminated": terminated,
        "survivors": survivors,
        "ok": bool(retire.get("gone")) and not survivors,
    }


def _mux_pane_alive(pane_id: str, mux_bin: str) -> bool:
    """Whether ``pane_id`` still exists in any session/window."""
    import subprocess

    try:
        r = subprocess.run(
            [mux_bin, "list-panes", "-a", "-F", "#{pane_id}"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode != 0:
            return False
        return pane_id in r.stdout.split()
    except (OSError, subprocess.TimeoutExpired):
        return False


def _mux_pane_session_name(pane_id: str, mux_bin: str) -> str | None:
    """Return the mux session name containing ``pane_id``."""
    import subprocess

    try:
        r = subprocess.run(
            [mux_bin, "display-message", "-p", "-t", pane_id, "#{session_name}"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode != 0:
            return None
        return getattr(r, "stdout", "").strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _mux_session_window_count(session_name: str, mux_bin: str) -> int | None:
    """Return the number of windows in ``session_name`` when the mux reports it."""
    import subprocess

    target = session_name if mux_bin == "psmux" else f"={session_name}"
    try:
        r = subprocess.run(
            [mux_bin, "list-windows", "-t", target, "-F", "#{window_id}"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode != 0:
            return None
        return len([line for line in getattr(r, "stdout", "").splitlines() if line.strip()])
    except (OSError, subprocess.TimeoutExpired):
        return None


def _mux_last_window_guard(pane_id: str, mux_bin: str) -> dict | None:
    """Return guard context when retiring ``pane_id`` would close a wt session."""
    session_name = _mux_pane_session_name(pane_id, mux_bin)
    if not session_name or not session_name.startswith("wt-"):
        return None
    window_count = _mux_session_window_count(session_name, mux_bin)
    if window_count == 1:
        return {"session": session_name, "window_count": window_count}
    return None


def mux_retire_pane(
    pane_id: str,
    *,
    mux: str | None = None,
    settle_timeout: float = 6.0,
    poll_interval: float = 0.3,
    ctrl_c_gap: float = 0.6,
    escalate_after: float = 1.5,
    hard_kill_settle: float = 1.5,
) -> dict:
    """Retire a specific pane by asking its Copilot to quit cleanly.

    Copilot CLI exits on a **double Ctrl-C** ~600 ms apart (a single one does
    little) -- its native clean-quit path (cf. :func:`graceful_quit_mux_session`).
    Unlike that session-scoped helper, this targets one ``pane_id`` so it retires
    the OLD Copilot after a cutover without touching the successor (the session's
    new active pane). Falls back to ``kill-pane`` if it does not exit in time.

    **Escalation ladder (up to three Ctrl-C).** Two interrupts is the common
    case, but some Copilot states swallow the second (mid-render, a modal, a busy
    turn flushing state) -- so after the double-interrupt we wait a brief
    ``escalate_after`` window and, only if the pane is still alive, deliver a
    conditional **third** Ctrl-C before the hard ``kill-pane`` fallback. This
    mirrors :func:`graceful_quit_mux_session` (a76ab47 / #2614) so a stubborn old
    pane is retired cleanly (persisting session state) instead of being severed,
    which is the failure mode behind a lingering un-retired pane (#3946).

    Returns ``{ok, pane, gone, method}`` where ``method`` is ``already-gone``,
    ``graceful``, ``hard``, or ``failed``.
    """
    import subprocess
    import time

    mux_bin = _mux_bin(mux)

    def _send(keys: str) -> bool:
        try:
            r = subprocess.run(
                [mux_bin, "send-keys", "-t", pane_id, keys],
                capture_output=True, timeout=5,
            )
            return r.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def _gone_within(window: float) -> bool:
        deadline = time.monotonic() + window
        while time.monotonic() < deadline:
            if not _mux_pane_alive(pane_id, mux_bin):
                return True
            time.sleep(poll_interval)
        return not _mux_pane_alive(pane_id, mux_bin)

    if not _mux_pane_alive(pane_id, mux_bin):
        return {"ok": True, "pane": pane_id, "gone": True, "method": "already-gone"}

    guard = _mux_last_window_guard(pane_id, mux_bin)
    if guard:
        try:
            from . import activity

            activity.log_event(
                "handoff_retire_guard",
                source="python",
                old_pane=pane_id,
                reason="last-window-skip",
                method="guard",
                outcome="left-running",
                mux_session=guard.get("session"),
                window_count=guard.get("window_count"),
            )
        except Exception:
            pass
        return {
            "ok": True, "pane": pane_id, "gone": False,
            "method": "last-window-skip",
            "session": guard.get("session"),
        }

    _send("C-c")
    time.sleep(ctrl_c_gap)
    _send("C-c")

    # Brief window for the double-interrupt to land before escalating.
    escalate_at = min(max(escalate_after, 0.0), settle_timeout)
    if _gone_within(escalate_at):
        return {"ok": True, "pane": pane_id, "gone": True, "method": "graceful"}

    # Still alive after two -- conditional third, then wait out the budget.
    _send("C-c")
    if _gone_within(settle_timeout - escalate_at):
        return {"ok": True, "pane": pane_id, "gone": True, "method": "graceful"}

    # Graceful quit did not land -- hard-kill the pane.
    try:
        subprocess.run(
            [mux_bin, "kill-pane", "-t", pane_id],
            capture_output=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass
    gone = _gone_within(max(hard_kill_settle, 0.0))
    return {
        "ok": gone, "pane": pane_id, "gone": gone,
        "method": "hard" if gone else "failed",
    }
