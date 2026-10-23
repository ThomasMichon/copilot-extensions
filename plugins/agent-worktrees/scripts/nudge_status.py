#!/usr/bin/env python3
"""agent-worktrees disposition nudge -- a Copilot CLI ``postToolUse`` command hook.

Keeps the Worktree Picker's disposition (summary / title / follow-up) honest on a
long run. The static ``sessionStart`` conduct is delivered exactly ONCE, so on a
lengthy session the summary/title silently go stale -- only the agent knows the
focus moved. This hook fires after each successful tool call, maintains a tiny
per-worktree drift counter + timer sidecar, and injects a lean reminder to run
``agent-worktrees status --summary`` (and ``--title`` when the focus changed) once
the agent has drifted far enough from its last disposition write.

Wiring: agent-worktrees' ``hooks.json`` declares a ``postToolUse`` command hook
that runs this script (deployed to ``~/.agent-worktrees/bin/``). The hook payload
arrives as JSON on **stdin**; a due nudge is written as JSON to **stdout**:

    {"additionalContext": "<reminder>"}

Anything else (empty output) is a no-op -- the tool result passes through
unchanged.

**Reactive, never a scheduler.** ``postToolUse`` fires only while the agent is
actively using tools -- never on a clock and never when idle. That is exactly
when a re-nudge is useful, so it is a good fit; it just cannot wake an idle
session (no hook can).

**Fail-open + advisory.** A nudge is never a blocker. This wraps everything and,
on ANY error, emits nothing and exits 0.

Reset semantics (``on_status_write_or_finalize``): the counter/timer reset when
the agent writes ANY disposition -- ``agent-worktrees status
--summary/--title/--follow-up/--resolved`` stamps ``status_note_at``, which this
hook watches -- and when the worktree is finalized (``finalize`` removes the
sidecar).

Thresholds (defaults; env-overridable): nudge when the drift since the last
disposition write reaches **>= AGENT_WORKTREES_NUDGE_CALLS** (25) tool calls OR
**>= AGENT_WORKTREES_NUDGE_MINUTES** (20) minutes. After a nudge the window
resets, so the next one needs another full threshold (no per-call spam).

Kill switch: ``AGENT_WORKTREES_NUDGE=off`` (or ``0``/``false``/``no``).
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

_DEFAULT_CALLS = 25
_DEFAULT_MINUTES = 20

# Worktrees that are done/finalized should not be nudged (their disposition is
# sealed). Mirrors the tracking record ``state``/``status`` vocabulary.
_TERMINAL_STATES = frozenset({"completed", "finalized", "removed", "pruned"})

_OFF_VALUES = frozenset({"0", "off", "false", "no"})
_UNSET = object()


def _truthy_off(val: str | None) -> bool:
    return (val or "").strip().lower() in _OFF_VALUES


def _int_env(env, name: str, default: int) -> int:
    try:
        v = int(str(env.get(name, "")).strip())
        return v if v > 0 else default
    except (TypeError, ValueError):
        return default


def _find_tracking_yaml(cwd: str, home: Path) -> tuple[str, Path] | None:
    """Resolve ``(worktree_id, tracking_yaml)`` for the worktree owning *cwd*.

    Tracking records live per-project at ``~/.<project>/worktrees/<id>.yaml``, so
    we match an ancestor directory name of *cwd* (the worktree root basename IS
    the id) against such a file across every project root. Returns None when
    *cwd* is not inside a tracked worktree (e.g. an anchor / base-repo checkout)."""
    try:
        p = Path(cwd).resolve()
    except Exception:
        return None
    # Only real project roots (a ``~/.<project>/worktrees/`` dir) are candidates
    # -- pre-filter so we never stat-probe unrelated home dotdirs (.cache, .ssh,
    # .config, ...) on every tool call.
    wt_dirs = [d / "worktrees" for d in home.glob(".*")
               if (d / "worktrees").is_dir()]
    if not wt_dirs:
        return None
    for cand in (p, *p.parents):
        name = cand.name
        if not name:
            continue
        for wdir in wt_dirs:
            yml = wdir / f"{name}.yaml"
            if yml.is_file():
                return name, yml
    return None


_NOTE_RE = re.compile(r"^\s*status_note_at:\s*(.+?)\s*$", re.MULTILINE)
_STATE_RE = re.compile(r"^\s*(?:state|status):\s*(.+?)\s*$", re.MULTILINE)


def _read_record_fields(yaml_path: Path) -> tuple[str | None, str | None]:
    """Best-effort ``(status_note_at, state)`` from a worktree tracking yaml,
    without a yaml dependency (two flat scalar keys). Quotes are stripped."""
    try:
        text = yaml_path.read_text("utf-8", errors="replace")
    except OSError:
        return None, None

    def _clean(m):
        if not m:
            return None
        v = m.group(1).strip().strip("'\"")
        return v or None

    note = _clean(_NOTE_RE.search(text))
    # First state/status line wins (the record's top-level field).
    state = _clean(_STATE_RE.search(text))
    return note, (state.lower() if state else None)


def _nudge_text(calls: int, minutes: int) -> str:
    return (
        f"[agent-worktrees] It's been {calls} tool call(s) / ~{minutes} min since "
        "this worktree's Picker disposition was last written, and the focus or "
        "state may have moved. If so, refresh it: `agent-worktrees status "
        "--activity \"<current sub-task>\"` most often (pass this whenever the "
        "immediate focus shifts); add `--summary \"<updated recap>\"` only "
        "occasionally, to fold newly completed work into the existing summary; "
        "add `--title \"<short headline>\"` only rarely, when the worktree's "
        "main theme genuinely changed. Keep `--follow-up`/`--resolved` "
        "accurate. If nothing consequential changed, ignore this."
    )


def _worker_definitely_gone(owner: object) -> bool:
    if (
        not isinstance(owner, dict) or type(owner.get("pid")) is not int
        or not 0 < owner["pid"] <= 0xFFFFFFFF or not isinstance(owner.get("start_time"), str)
    ):
        return False
    try:
        runtime = sys.modules.get("agent_worktrees.locks")
        if runtime is not None and not runtime.lock_is_live(owner):
            return True
        pid = owner["pid"]
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel.WaitForSingleObject.restype = wintypes.DWORD
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle.restype = wintypes.BOOL
            handle = kernel.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE, no mutation rights
            if not handle:
                return ctypes.get_last_error() == 87  # absent PID, not access denial
            try:
                return kernel.WaitForSingleObject(handle, 0) == 0
            finally:
                kernel.CloseHandle(handle)
        os.kill(pid, 0)
        if sys.platform.startswith("linux"):
            fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            return fields[0] == "Z" or fields[19] != owner["start_time"]
    except ProcessLookupError:
        return True
    except (OSError, ValueError, IndexError, AttributeError, OverflowError):
        pass
    return False


def _publication_revision(directory: Path, root: str) -> str:
    try:
        path = directory / "publication.json"
        if not path.is_file() or path.stat().st_size > 4096:
            return ""
        value = json.loads(path.read_text(encoding="utf-8"))
        revision = value.get("revision")
        if (
            value.get("version") == 1 and value.get("root") == root
            and isinstance(revision, str) and re.fullmatch(r"[0-9a-f]{32}", revision)
        ):
            return revision
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return ""


def _late_guidance_notice(payload: dict, home: Path) -> str | None:
    session_id = payload.get("sessionId")
    if not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session_id):
        return None
    watch_path = home / ".copilot" / "session-state" / session_id / "local-guidance-watch.json"
    try:
        if not watch_path.is_file() or watch_path.stat().st_size > 8192:
            return None
        watch = json.loads(watch_path.read_text(encoding="utf-8"))
        if (
            watch.get("version") != 1
            or not Path(payload.get("cwd") or "").resolve().is_relative_to(Path(watch["root"]).resolve())
        ):
            return None
        metadata = Path(watch["metadata"])
        request = metadata / "request.json"
        if not request.is_file() or request.stat().st_size > 256 * 1024:
            return None
        state = json.loads(request.read_text(encoding="utf-8"))
        if state.get("version") != 1 or state.get("root") != watch["root"]:
            return None
        if state.get("phase") in ("spawning", "queued", "running"):
            if not _worker_definitely_gone(state.get("owner")):
                return None
            result = {"status": "failed", "counts_known": False}
        else:
            if state.get("phase") not in ("done", "failed") or state.get("completed_at", 0) < watch["started_at"]:
                return None
            result = state["result"]
        status = result.get("status")
        if status not in ("ready", "partial", "skipped", "failed", "timeout"):
            return None
        watch_path.unlink()
        latest = _publication_revision(metadata, watch["root"])
        revision = latest or result.get("publication_revision")
        changed = result.get("restart_required") is True or (
            isinstance(revision, str) and revision and revision != watch.get("publication_revision", "")
        )
        if status in ("failed", "timeout"):
            if changed:
                return (
                    "[local-guidance] Backup refresh failed after possible file changes. "
                    "Retain reviewed fallback and repair preparation. Restart/new context is required "
                    "before discovering refreshed guidance; file changes are not proof of model admission."
                )
            return "[local-guidance] Backup refresh failed; retain reviewed fallback. Model admission is unknown."
        if changed:
            coverage = "partially refreshed" if status == "partial" else "refreshed"
            description = (
                f"Static guidance files {coverage} after session discovery."
                if result.get("restart_required") is True
                else "Static guidance preparation is incomplete after a possible late file change."
                if status == "partial"
                else "Static guidance was revalidated after a possible late file change."
            )
            if latest and latest != result.get("publication_revision"):
                description = (
                    "Static guidance preparation is incomplete; files may have changed after its last validation."
                    if status == "partial"
                    else "Static guidance may have changed after its last validation; reprepare it first."
                )
            fallback = "Retain reviewed fallback for unresolved sources. " if status == "partial" else ""
            return (
                f"[local-guidance] {description} "
                "Restart/new context is required to discover the refreshed guidance; "
                "this is file publication, not proof of model admission. "
                f"{fallback}"
                "Do not restart automatically; preserve current work first."
            )
        if status == "partial":
            return "[local-guidance] Backup refresh is incomplete; retain reviewed fallback. Model admission is unknown."
        return "[local-guidance] Backup verified unchanged guidance; no new model admission is asserted."
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def decide(
    payload: dict, *, env=None, home=None, now: float | None = None,
    tracking_record=_UNSET, deadline: float | None = None,
) -> str | None:
    """Return the ``additionalContext`` reminder string, or None for a no-op.

    Pure/injectable for tests: reads+writes the per-worktree sidecar under
    ``<home>/.agent-worktrees/nudge-state/`` and reads the worktree tracking yaml
    (per-project) at ``<home>/.<project>/worktrees/<id>.yaml``.
    """
    env = env if env is not None else os.environ
    home = Path(home) if home is not None else Path.home()
    now = time.time() if now is None else now
    if deadline is not None and time.time() >= deadline:
        return None
    notice = _late_guidance_notice(payload, home)
    if notice:
        return notice
    if _truthy_off(env.get("AGENT_WORKTREES_NUDGE")):
        return None

    cwd = str(payload.get("cwd") or "") or os.getcwd()
    aw = home / ".agent-worktrees"
    found = (
        _find_tracking_yaml(cwd, home)
        if tracking_record is _UNSET else tracking_record
    )
    if not found:
        return None
    worktree_id, yaml_path = found

    note_at, state = _read_record_fields(yaml_path)
    if state in _TERMINAL_STATES:
        if deadline is not None and time.time() >= deadline:
            return None
        # Sealed disposition -- stop nudging and drop any sidecar.
        _clear_sidecar(aw, worktree_id)
        return None

    calls_thresh = _int_env(env, "AGENT_WORKTREES_NUDGE_CALLS", _DEFAULT_CALLS)
    mins_thresh = _int_env(env, "AGENT_WORKTREES_NUDGE_MINUTES", _DEFAULT_MINUTES)

    state_dir = aw / "nudge-state"
    sidecar = state_dir / f"{worktree_id}.json"
    data: dict = {}
    try:
        if sidecar.is_file():
            data = json.loads(sidecar.read_text("utf-8")) or {}
    except (OSError, ValueError):
        data = {}

    # Reset the drift window when the agent has written a disposition since we
    # last looked (status_note_at advanced), or on first sight of this worktree.
    if data.get("seen_note_at") != note_at or "window_start" not in data:
        data = {"count": 0, "window_start": now, "seen_note_at": note_at}

    data["count"] = int(data.get("count", 0)) + 1
    elapsed_min = (now - float(data.get("window_start", now))) / 60.0

    due = data["count"] >= calls_thresh or elapsed_min >= mins_thresh
    text: str | None = None
    if due:
        text = _nudge_text(int(data["count"]), max(1, round(elapsed_min)))
        # Reset the window so the next nudge needs another full threshold (no
        # per-call spam); keep seen_note_at so a real disposition write still
        # resets independently.
        data = {"count": 0, "window_start": now, "seen_note_at": note_at}

    if deadline is not None and time.time() >= deadline:
        return None
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass
    return text


def _clear_sidecar(aw: Path, worktree_id: str) -> None:
    try:
        (aw / "nudge-state" / f"{worktree_id}.json").unlink(missing_ok=True)
    except OSError:
        pass


def main() -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        text = decide(payload)
        if text:
            sys.stdout.write(json.dumps({"additionalContext": text}))
    except Exception:
        # Advisory only: never let a nudge error disturb the tool result.
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
