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

        # sessions=None means pre-registry -- needs full scan fallback
        sessions = getattr(rec, "sessions", None)
        if sessions is None:
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
