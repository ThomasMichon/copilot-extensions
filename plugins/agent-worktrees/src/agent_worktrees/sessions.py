"""Copilot CLI session-state scanning.

Scans ~/.copilot/session-state/ to detect active Copilot sessions
(by lock file + process check) and extract latest session summaries
for worktree annotation.

Provides two scanning modes:
- ``scan_sessions()`` -- full walk of all session directories (legacy)
- ``scan_sessions_fast()`` -- targeted lookup using the per-worktree
  session registry, falling back to full scan for unindexed records
"""

from __future__ import annotations

import json
import os
import platform
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class SessionContext:
    """Aggregated session info for a set of worktree paths."""

    active_sessions: dict[str, list[str]] = field(default_factory=dict)
    """normalized_path → list of session_ids with live Copilot processes"""

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

    _latest_ts: dict[str, str] = field(default_factory=dict)
    """Internal: tracks latest updated_at per path for summary selection."""

    _activity_ts: dict[str, str] = field(default_factory=dict)
    """Internal: tracks latest updated_at per path for activity/context selection."""


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


def _read_substatus(entry: Path) -> tuple[str, str, bool] | None:
    """Read the live agent-intent pulse from a session's ``substatus.json``.

    The agent-worktrees live-pulse extension writes this sidecar from the
    ``assistant.intent`` event stream (root agent only), which is ephemeral and
    never lands in ``events.jsonl`` -- so this file is the sole on-disk source.
    Returns ``(intent, updated_at_iso, idle)`` or None when absent/unreadable.
    Never raises.  This is the derived pulse register; it is deliberately
    independent of the agent-asserted ``follow_up`` disposition.
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
    return intent.strip(), updated_at, idle


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
    if sub is not None:
        intent, sub_at, idle = sub
        ctx.live_intent[norm_path] = intent
        ctx.live_intent_at[norm_path] = sub_at
        ctx.live_intent_idle[norm_path] = idle
    else:
        ctx.live_intent.pop(norm_path, None)
        ctx.live_intent_at.pop(norm_path, None)
        ctx.live_intent_idle.pop(norm_path, None)


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


def scan_sessions(worktree_paths: list[str]) -> SessionContext:
    """Scan Copilot session-state for active sessions and summaries.

    Args:
        worktree_paths: List of worktree filesystem paths to match against.

    Returns:
        SessionContext with active sessions and latest summaries.
    """
    ctx = SessionContext()
    session_dir = _session_state_dir()

    if not session_dir.exists() or not worktree_paths:
        return ctx

    # Build normalized lookup set
    path_set: set[str] = {_normalize_path(p) for p in worktree_paths}

    # Track latest summary per path by updated_at
    latest_ts: dict[str, str] = {}

    for entry in session_dir.iterdir():
        if not entry.is_dir():
            continue

        # Skip detached parent-continuation sessions (e.g. headless
        # rem-agent / subconscious runs). They reuse the parent's cwd and
        # must not be attributed to a worktree.
        if _is_detached_session(entry):
            continue

        ws_file = entry / "workspace.yaml"
        if not ws_file.exists():
            continue

        try:
            with open(ws_file, encoding="utf-8") as f:
                ws_data = yaml.safe_load(f)
        except Exception:
            continue

        if not ws_data or not isinstance(ws_data, dict):
            continue

        cwd = ws_data.get("cwd", "")
        if not cwd:
            continue

        norm_cwd = _normalize_path(cwd)

        # Match against worktree roots -- session cwd may be a subdirectory
        matched_path: str | None = None
        _casefold = platform.system() == "Windows"
        for wt_path in path_set:
            a, b = (norm_cwd.lower(), wt_path.lower()) if _casefold else (norm_cwd, wt_path)
            if a == b or a.startswith(b + "/") or a.startswith(b + "\\"):
                matched_path = wt_path
                break

        if matched_path is None:
            continue

        # Count sessions per worktree
        ctx.session_count[matched_path] = ctx.session_count.get(matched_path, 0) + 1

        # Count user turns from events.jsonl (cheap string match, no JSON parse)
        events_file = entry / "events.jsonl"
        if events_file.exists():
            try:
                with open(events_file, encoding="utf-8", errors="replace") as ef:
                    turns = sum(1 for line in ef if '"user.message"' in line)
                if turns > 0:
                    ctx.turn_count[matched_path] = (
                        ctx.turn_count.get(matched_path, 0) + turns
                    )
            except OSError:
                pass

        # Track best available display text per path by updated_at.
        # Prefer summary (richer) over name (short title), but pick
        # the newest session's best text overall.
        updated_at = str(ws_data.get("updated_at", ""))
        _update_activity(ctx, matched_path, entry, updated_at)

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
            if not latest_ts.get(matched_path) or updated_at > latest_ts[matched_path]:
                latest_ts[matched_path] = updated_at
                if len(display_text) > 60:
                    display_text = display_text[:57] + "..."
                ctx.latest_summary[matched_path] = display_text

        # Check for live lock files
        live_found = False
        for lock_file in entry.glob("inuse.*.lock"):
            parts = lock_file.stem.split(".")
            if len(parts) >= 2:
                try:
                    lock_pid = int(parts[1])
                except ValueError:
                    continue
                if _is_copilot_process(lock_pid):
                    live_found = True
                    break

        if live_found:
            if matched_path not in ctx.active_sessions:
                ctx.active_sessions[matched_path] = []
            ctx.active_sessions[matched_path].append(entry.name)

    return ctx


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

    # Turn count from events.jsonl
    events_file = entry / "events.jsonl"
    if events_file.exists():
        try:
            with open(events_file, encoding="utf-8", errors="replace") as ef:
                turns = sum(1 for line in ef if '"user.message"' in line)
            if turns > 0:
                ctx.turn_count[norm_path] = (
                    ctx.turn_count.get(norm_path, 0) + turns
                )
        except OSError:
            pass

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


def scan_sessions_fast(
    records: list,
) -> SessionContext:
    """Targeted session scan using the per-worktree session registry.

    Instead of walking all of ``~/.copilot/session-state/``, reads
    session IDs from each record's ``sessions`` list and checks only
    those specific directories.

    Records whose ``sessions`` field is None (pre-registry, not yet
    indexed) are collected and their paths passed to the legacy
    ``scan_sessions()`` for a full-scan fallback.  This ensures
    correct behavior during the migration window.

    Args:
        records: List of WorktreeRecord objects (with sessions field).

    Returns:
        SessionContext with active sessions and latest summaries.
    """
    ctx = SessionContext()
    session_dir = _session_state_dir()

    if not session_dir.exists():
        return ctx

    # Separate indexed vs unindexed records
    fallback_paths: list[str] = []

    for rec in records:
        if not rec.worktree_path:
            continue

        # sessions=None means pre-registry; an empty list means the registry
        # is active but no session was recorded for this worktree (e.g. the
        # register-session hook never fired).  Both need the full-scan
        # fallback -- otherwise the fast path scans nothing and the worktree
        # silently loses its session summary + turn count (so the status bar
        # shows a bare UNUSED state with no title).  Mirrors the same
        # empty-or-None fallback in ``find_latest_session_id_fast``.
        sessions = getattr(rec, "sessions", None)
        if not sessions:
            fallback_paths.append(rec.worktree_path)
            continue

        # Fast path -- only check known session IDs
        for entry in sessions:
            _enrich_session_dir(
                session_dir, entry.session_id, rec.worktree_path, ctx,
            )

    # Fallback for unindexed records
    if fallback_paths:
        fallback_ctx = scan_sessions(fallback_paths)
        # Merge fallback results
        for k, v in fallback_ctx.active_sessions.items():
            ctx.active_sessions.setdefault(k, []).extend(v)
        for k, v in fallback_ctx.latest_summary.items():
            if k not in ctx.latest_summary:
                ctx.latest_summary[k] = v
        for k, v in fallback_ctx.session_count.items():
            ctx.session_count[k] = ctx.session_count.get(k, 0) + v
        for k, v in fallback_ctx.turn_count.items():
            ctx.turn_count[k] = ctx.turn_count.get(k, 0) + v
        # Fallback paths are disjoint from fast-path records, so a direct
        # copy is safe (no key collisions to reconcile).
        for k, v in fallback_ctx.last_activity.items():
            ctx.last_activity.setdefault(k, v)
        for k, v in fallback_ctx.context_pct.items():
            ctx.context_pct.setdefault(k, v)

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
    no sessions recorded -- e.g. hook failed to fire), falls back to the
    full-scan ``find_latest_session_id()``.

    Validates each candidate: session dir must exist and contain
    ``session.db`` or ``events.jsonl`` (not a stale stub).
    """
    if not sessions:
        return find_latest_session_id(worktree_path)

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


def find_latest_session_id(worktree_path: str) -> str | None:
    """Find the most recent Copilot session ID for a worktree path.

    Scans ``~/.copilot/session-state/`` for sessions whose ``cwd``
    matches *worktree_path* and returns the session directory name
    (which is the session ID) of the most recently updated match.

    Returns None if no matching session is found.
    """
    session_dir = _session_state_dir()
    if not session_dir.exists():
        return None

    norm_wt = _normalize_path(worktree_path)
    best_id: str | None = None
    best_ts: str = ""

    for entry in session_dir.iterdir():
        if not entry.is_dir():
            continue

        # Skip detached parent-continuation sessions (subconscious /
        # rem-agent runs) -- they reuse the parent's cwd and are not a real
        # resume target for this worktree.
        if _is_detached_session(entry):
            continue

        ws_file = entry / "workspace.yaml"
        if not ws_file.exists():
            continue

        try:
            with open(ws_file, encoding="utf-8") as f:
                ws_data = yaml.safe_load(f)
        except Exception:
            continue

        if not ws_data or not isinstance(ws_data, dict):
            continue

        cwd = ws_data.get("cwd", "")
        if not cwd:
            continue

        norm_cwd = _normalize_path(cwd)
        if norm_cwd != norm_wt and not norm_cwd.startswith(norm_wt + os.sep):
            continue

        # A session directory with only workspace.yaml but no conversation
        # data (session.db or events.jsonl) is a stale stub that Copilot
        # CLI will reject with "No session matched".  Skip it.
        if not (entry / "session.db").exists() and not (entry / "events.jsonl").exists():
            continue

        updated_at = str(ws_data.get("updated_at", ""))
        if updated_at > best_ts:
            best_ts = updated_at
            best_id = entry.name

    return best_id


def backfill_sessions(records: list) -> dict[str, list[str]]:
    """Populate empty session registries from existing session-state data.

    Scans ``~/.copilot/session-state/`` once, matches sessions to
    worktree paths, and returns a mapping of worktree_id to session IDs
    that were discovered.  The caller is responsible for writing the
    entries into the tracking YAMLs.

    Only processes records whose ``sessions`` field is empty (``None``
    or ``[]``).  Records with populated session lists are skipped.
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
                ws_data = yaml.safe_load(f)
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


def list_worktree_sessions(record) -> list[dict]:
    """Enumerate the Copilot sessions associated with a worktree.

    Uses the worktree's session registry (``record.sessions``) when
    available; for pre-registry records (``sessions is None``) falls back
    to a cwd-based scan of session-state.  Each entry carries display
    metadata (see :func:`_session_meta`).  Sorted newest-first by
    ``updated_at``.
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
        # Pre-registry fallback: match sessions by cwd under the worktree.
        backfilled = backfill_sessions([record])
        for sid in backfilled.get(record.worktree_id, []):
            _add(sid)

    out.sort(key=lambda s: s.get("updated_at", ""), reverse=True)
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


def has_mux_session(worktree_id: str) -> bool:
    """Check if a multiplexer session exists for a worktree (without killing it).

    Uses tmux on Linux/WSL and psmux on Windows.

    Returns True if the mux session is alive, False otherwise.
    """
    import subprocess

    sess_name = f"wt-{worktree_id}"
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
            sess_name = f"wt-{wt_id}"
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

    sess_name = f"wt-{worktree_id}"
    if platform.system() == "Windows":
        cmd = ["psmux", "kill-session", "-t", sess_name]
    else:
        cmd = ["tmux", "kill-session", "-t", f"={sess_name}"]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=5)
        return result.returncode == 0
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

    sess_name = f"wt-{worktree_id}"
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
        return {
            "worktree_id": worktree_id, "had_session": False,
            "method": "none", "ok": True,
        }
    if graceful and graceful_quit_mux_session(
        worktree_id, settle_timeout=settle_timeout,
    ):
        return {
            "worktree_id": worktree_id, "had_session": True,
            "method": "graceful", "ok": True,
        }
    killed = kill_tmux_session(worktree_id)
    return {
        "worktree_id": worktree_id, "had_session": True,
        "method": "hard" if killed else "failed", "ok": killed,
    }


# ── Live-cutover handoff mux primitives (issue #2250) ─────────────────────
# A live handoff spawns a *seeded successor* Copilot in a NEW window of the
# same ``wt-<id>`` session (preserving session identity + status bar), cuts the
# operator over to it, and later retires the OLD pane. These helpers are the
# platform-aware mux verbs behind ``agent-worktrees handoff-cutover``.

# Identity env vars stripped from a child Copilot so the session carries no
# ambient project/worktree identity (in-session tools resolve from CWD). Mirror
# of the ``env -u`` prefix in launch-session.sh.
_IDENTITY_ENV_VARS = ("WORKTREE_PROJECT", "WORKTREE_ID", "APERTURE_WORKTREE_ID")


def _mux_bin(mux: str | None = None) -> str:
    """Resolve the multiplexer binary name (psmux on Windows, tmux elsewhere)."""
    if mux:
        return mux
    return "psmux" if platform.system() == "Windows" else "tmux"


def _mux_session_target(worktree_id: str, mux_bin: str) -> str:
    """Session target string. tmux uses the ``=`` exact-match prefix; psmux
    does not support it (rejected as an unknown session)."""
    sess = f"wt-{worktree_id}"
    return sess if mux_bin == "psmux" else f"={sess}"


def _mux_pane_cmd(
    cmd: list[str], *, is_tmux: bool, pane_wrapper: str | None = None
) -> list[str]:
    """Build the in-pane command vector shared by new-window and new-session.

    On Linux/WSL (tmux) the command is prefixed with ``env -u <identity vars>``
    and wrapped by the pane-wrapper (when present); on Windows (psmux) the server
    env is already identity-clean so the command runs verbatim (and psmux cannot
    carry a spaces-containing pane arg -- see :func:`build_mux_new_window_argv`).
    """
    if not is_tmux:
        # psmux: run verbatim; keep every element single-token.
        return list(cmd)
    clean: list[str] = ["env"]
    for var in _IDENTITY_ENV_VARS:
        clean += ["-u", var]
    wrapper = pane_wrapper
    if wrapper is None:
        wrapper = os.path.expanduser("~/.agent-worktrees/bin/pane-wrapper.sh")
    if wrapper and os.path.isfile(wrapper) and os.access(wrapper, os.R_OK):
        return clean + ["bash", wrapper] + list(cmd)
    return clean + list(cmd)


def build_mux_new_window_argv(
    worktree_id: str,
    work_dir: str,
    cmd: list[str],
    env: dict[str, str] | None = None,
    *,
    mux: str | None = None,
    pane_wrapper: str | None = None,
) -> list[str]:
    """Build the argv to open a new window in ``wt-<id>`` running ``cmd``.

    Mirrors the launcher's pane construction: on Linux/WSL the command is
    prefixed with ``env -u <identity vars>`` and wrapped by the pane-wrapper
    (when present); on Windows the psmux server env is already identity-clean so
    the command runs directly. Profile env is re-propagated with ``-e`` for
    parity regardless of session-env inheritance. ``-P -F '#{pane_id}'`` makes
    the mux print the new pane id.
    """
    mux_bin = _mux_bin(mux)
    is_tmux = mux_bin != "psmux"
    target = _mux_session_target(worktree_id, mux_bin)

    argv = [mux_bin, "new-window", "-P", "-F", "#{pane_id}", "-t", target]
    if work_dir:
        argv += ["-c", work_dir]
    for key, val in (env or {}).items():
        argv += ["-e", f"{key}={val}"]

    pane_cmd = _mux_pane_cmd(cmd, is_tmux=is_tmux, pane_wrapper=pane_wrapper)

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
    sess = f"wt-{worktree_id}"

    argv = [mux_bin, "new-session", "-d", "-s", sess, "-P", "-F", "#{pane_id}"]
    if work_dir:
        argv += ["-c", work_dir]
    for key, val in (env or {}).items():
        argv += ["-e", f"{key}={val}"]

    argv += _mux_pane_cmd(cmd, is_tmux=is_tmux, pane_wrapper=pane_wrapper)
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

    sess = f"wt-{worktree_id}"
    argv = build_mux_new_session_argv(worktree_id, work_dir, cmd, env, mux=mux)
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as e:
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

    Waits (up to ``ready_timeout``) for Copilot's input prompt to appear before
    typing, so keystrokes are not eaten by TUI startup, then sends the text, a
    brief ``settle`` pause, and Enter. Returns ``{ok, pane, ready, sent}``.
    """
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

    # Poll for readiness: Copilot renders a ``❯`` prompt caret + a rule line.
    ready = False
    deadline = time.monotonic() + ready_timeout
    while time.monotonic() < deadline:
        cap = _cap()
        if "❯" in cap or "esc interrupt" in cap or "─────" in cap:
            ready = True
            break
        time.sleep(poll_interval)

    def _send(*a: str) -> bool:
        try:
            r = subprocess.run(
                [mux_bin, "send-keys", "-t", pane_id, *a],
                capture_output=True, timeout=5,
            )
            return r.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    # ``-l`` sends the seed literally (no key-name interpretation), so the whole
    # multi-word prompt lands as one input line.
    sent = _send("-l", seed)
    time.sleep(settle)
    _send("Enter")
    return {"ok": bool(sent), "pane": pane_id, "ready": ready, "sent": bool(sent)}


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


def mux_new_window(
    worktree_id: str,
    work_dir: str,
    cmd: list[str],
    env: dict[str, str] | None = None,
    *,
    mux: str | None = None,
) -> dict:
    """Open + select a new window in ``wt-<id>`` running ``cmd``.

    ``new-window`` selects the new window by default (no ``-d``), so the
    operator is cut over to the successor immediately. Returns
    ``{ok, new_pane, error}``.
    """
    import subprocess

    argv = build_mux_new_window_argv(worktree_id, work_dir, cmd, env, mux=mux)
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "new_pane": None, "error": str(e)}
    if r.returncode != 0:
        return {
            "ok": False, "new_pane": None,
            "error": r.stderr.strip() or f"exit {r.returncode}",
        }
    return {"ok": True, "new_pane": r.stdout.strip() or None, "error": None}


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


def mux_retire_pane(
    pane_id: str,
    *,
    mux: str | None = None,
    settle_timeout: float = 6.0,
    poll_interval: float = 0.3,
    ctrl_c_gap: float = 0.6,
) -> dict:
    """Retire a specific pane by asking its Copilot to quit cleanly.

    Copilot CLI exits on a **double Ctrl-C** ~600 ms apart (a single one does
    little) -- its native clean-quit path (cf. :func:`graceful_quit_mux_session`).
    Unlike that session-scoped helper, this targets one ``pane_id`` so it retires
    the OLD Copilot after a cutover without touching the successor (the session's
    new active pane). Falls back to ``kill-pane`` if it does not exit in time.

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

    if not _mux_pane_alive(pane_id, mux_bin):
        return {"ok": True, "pane": pane_id, "gone": True, "method": "already-gone"}

    _send("C-c")
    time.sleep(ctrl_c_gap)
    _send("C-c")

    deadline = time.monotonic() + settle_timeout
    while time.monotonic() < deadline:
        if not _mux_pane_alive(pane_id, mux_bin):
            return {"ok": True, "pane": pane_id, "gone": True, "method": "graceful"}
        time.sleep(poll_interval)

    # Graceful quit did not land -- hard-kill the pane.
    try:
        subprocess.run(
            [mux_bin, "kill-pane", "-t", pane_id],
            capture_output=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass
    gone = not _mux_pane_alive(pane_id, mux_bin)
    return {
        "ok": gone, "pane": pane_id, "gone": gone,
        "method": "hard" if gone else "failed",
    }
