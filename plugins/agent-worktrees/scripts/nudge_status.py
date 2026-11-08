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

**Agenda nudge.** When the effective ``agendas.default`` preference is
``expected`` and this worktree has no detected agenda lane, a second, separately
throttled reminder suggests adopting one via the ``driving-effort-agendas``
skill. It shares the same lock and the same call/minute thresholds, but keeps
its own window sidecar (a disposition write does not reset it). The policy is
never resolved here: the owner (the sessionStart session-context path, running
in the selected runtime) publishes a tiny record,
``<runtime>/nudge-state/<worktree>.agenda-policy.json`` =
``{"version": 1, "policy": "off"|"expected", "written_at": <epoch>}``, on every
session start/resume and every ``agent-worktrees status`` write (which the
disposition nudge above prompts), so a running session sees a changed setting
at its next disposition write. This hook (and the Windows prepared PowerShell path)
reads only that record and the local lane evidence on a due tick -- stdlib-only,
no config loader, no git -- so it works under a bare fallback interpreter and
cannot stall the resident. A missing/unreadable record or policy ``off`` is
silent. Adoption is never automatic. Kill switch:
``AGENT_WORKTREES_AGENDA_NUDGE=off``.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
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


def _note_identity(value):
    if not isinstance(value, str):
        return value
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.astimezone(timezone.utc).isoformat(timespec="microseconds")
    except ValueError:
        return value


@contextmanager
def _counter_lock(aw: Path, worktree_id: str, deadline=None):
    """Share one bounded byte-range transaction with the shell read consumer."""
    with _byte_lock(aw / "nudge-state" / f"{worktree_id}.lock", deadline) as acquired:
        yield acquired


@contextmanager
def _byte_lock(path: Path, deadline=None):
    if deadline is not None and time.time() >= deadline:
        yield False
        return
    stream = None
    acquired = False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        stream = path.open("a+b")
        until = time.monotonic() + min(
            0.1, max(0, deadline - time.time()) if deadline is not None else 0.1,
        )
        while True:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.lockf(stream, fcntl.LOCK_EX | fcntl.LOCK_NB, 1)
                acquired = True
                break
            except OSError:
                if time.monotonic() >= until:
                    break
                time.sleep(0.005)
    except OSError:
        pass
    try:
        yield acquired
    finally:
        if stream is not None:
            try:
                if acquired:
                    stream.seek(0)
                    if os.name == "nt":
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.lockf(stream, fcntl.LOCK_UN, 1)
            finally:
                stream.close()


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


_AGENDA_EXPECTED = "expected"
_WORKTREE_PATH_RE = re.compile(r"^worktree_path:\s*(.+?)\s*$", re.MULTILINE)


def _agenda_nudge_text() -> str:
    return (
        "[agent-worktrees] This project expects an agenda for this session "
        "(agendas.default: expected), but no agenda lane was found for this "
        "worktree. Adopt one via the `driving-effort-agendas` skill: "
        "`agenda_outline.py from-effort <effort README.md>` (or "
        "`agenda_backfill.py preview <session-folder>` to rebuild from this "
        "session), then `preview` and `adopt`. You own and edit the agenda; "
        "nothing is adopted automatically."
    )


def agenda_lane_state(yaml_path: Path, worktree_id: str) -> str:
    """Return ``present``/``absent``/``unknown`` from cheap local evidence only.

    Evidence: a managed ``agendas`` placement in the project's worktree-data
    descriptor, or a non-empty worktree-local ``.effort-agenda/`` home.
    """
    try:
        descriptor = (
            Path(yaml_path).parent.parent / "worktree-state" / worktree_id / "descriptor.json"
        )
        if descriptor.is_file():
            if descriptor.stat().st_size > 1_048_576:
                return "unknown"
            value = json.loads(descriptor.read_text("utf-8"))
            placements = value.get("placements") if isinstance(value, dict) else None
            if not isinstance(placements, list):
                return "unknown"
            if any(isinstance(p, dict) and p.get("domain") == "agendas" for p in placements):
                return "present"
        match = _WORKTREE_PATH_RE.search(Path(yaml_path).read_text("utf-8", errors="replace"))
        if match:
            home = Path(match.group(1).strip().strip("'\"")) / ".effort-agenda"
            if home.is_dir() and any(child.is_dir() for child in home.iterdir()):
                return "present"
        return "absent"
    except (OSError, ValueError, TypeError):
        return "unknown"


AGENDA_POLICY_SUFFIX = ".agenda-policy.json"
# Session-level switches the hook client forwards to the resident, whose own
# process environment belongs to whichever session spawned it.
NUDGE_ENV_KEYS = (
    "AGENT_WORKTREES_NUDGE",
    "AGENT_WORKTREES_AGENDA_NUDGE",
    "AGENT_WORKTREES_NUDGE_CALLS",
    "AGENT_WORKTREES_NUDGE_MINUTES",
)


def forwarded_env(payload: dict, base=None) -> dict | None:
    """Return the session's forwarded nudge switches over ``base``, else None."""
    metadata = payload.get("_agentWorktrees")
    forwarded = metadata.get("environment") if isinstance(metadata, dict) else None
    if not isinstance(forwarded, dict):
        return None
    base = os.environ if base is None else base
    env = {key: value for key, value in base.items() if key not in NUDGE_ENV_KEYS}
    env.update({
        key: value for key, value in forwarded.items()
        if key in NUDGE_ENV_KEYS and isinstance(value, str)
    })
    return env
_AGENDA_POLICY_MAX_BYTES = 4096


def read_agenda_policy(aw: Path, worktree_id: str) -> str | None:
    """Return the owner-published ``agendas.default`` record value, else None."""
    path = Path(aw) / "nudge-state" / f"{worktree_id}{AGENDA_POLICY_SUFFIX}"
    try:
        with path.open("rb") as stream:
            raw = stream.read(_AGENDA_POLICY_MAX_BYTES + 1)
        if len(raw) > _AGENDA_POLICY_MAX_BYTES:
            return None
        value = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict) or value.get("version") != 1:
        return None
    policy = value.get("policy")
    return policy if isinstance(policy, str) else None


def _agenda_nudge(aw, yaml_path, worktree_id) -> str | None:
    if _read_record_fields(yaml_path)[1] in _TERMINAL_STATES:
        return None
    if read_agenda_policy(aw, worktree_id) != _AGENDA_EXPECTED:
        return None
    if agenda_lane_state(yaml_path, worktree_id) != "absent":
        return None
    return _agenda_nudge_text()


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


def _consume_late_watch(path: Path, snapshot: bytes) -> bool:
    if os.name != "nt":
        path.unlink()
        return True
    with _byte_lock(path.with_name(path.name + ".lock")) as acquired:
        if not acquired:
            return False
        claimed = path.with_name(path.name + ".consuming-" + uuid.uuid4().hex)
        try:
            path.rename(claimed)
            try:
                with claimed.open("rb") as stream:
                    if stream.read(8193) != snapshot:
                        return False
                claimed.unlink()
                return True
            finally:
                if claimed.exists():
                    try:
                        claimed.rename(path)
                    except FileExistsError:
                        # A newer watch supersedes the claimed snapshot; never overwrite it.
                        claimed.unlink()
        except OSError:
            return False


def _late_guidance_notice(payload: dict, home: Path) -> str | None:
    session_id = payload.get("sessionId")
    if not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session_id):
        return None
    watch_path = home / ".copilot" / "session-state" / session_id / "local-guidance-watch.json"
    try:
        if not watch_path.is_file() or watch_path.stat().st_size > 8192:
            return None
        with watch_path.open("rb") as stream:
            snapshot = stream.read(8193)
        if len(snapshot) > 8192:
            return None
        watch = json.loads(snapshot.decode("utf-8"))
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
        if not _consume_late_watch(watch_path, snapshot):
            return None
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
    tracking_record=_UNSET, deadline: float | None = None, tracking_lookup=None,
) -> str | None:
    """Return the ``additionalContext`` reminder string, or None for a no-op.

    Pure/injectable for tests: reads+writes the per-worktree sidecar under
    ``<home>/.agent-worktrees/nudge-state/`` and reads the worktree tracking yaml
    (per-project) at ``<home>/.<project>/worktrees/<id>.yaml``, where ``<home>``
    is ``AGENT_HOME`` when set. The agenda
    policy comes only from the owner-published ``<id>.agenda-policy.json``.
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
    # The runtime tree (policy records, counters, tracking) lives under
    # AGENT_HOME when set, matching the publisher; session-state stays on home.
    agent_home = str(env.get("AGENT_HOME") or "").strip()
    state_home = Path(agent_home) if agent_home else home
    aw = state_home / ".agent-worktrees"
    found = (
        (tracking_lookup or _find_tracking_yaml)(cwd, state_home)
        if tracking_record is _UNSET else tracking_record
    )
    if not found:
        return None
    worktree_id, yaml_path = found

    with _counter_lock(aw, worktree_id, deadline) as acquired:
        if not acquired:
            return None
        text = _update_counter(aw, worktree_id, yaml_path, env, now, deadline)
        agenda_due = _agenda_tick(aw, worktree_id, env, now, deadline)
    if agenda_due and not (deadline is not None and time.time() >= deadline):
        agenda_text = _agenda_nudge(aw, yaml_path, worktree_id)
        if agenda_text:
            text = f"{text}\n\n{agenda_text}" if text else agenda_text
    return text


def _agenda_tick(aw, worktree_id, env, now, deadline) -> bool:
    """Advance the agenda window; True when a policy/lane check is due."""
    if _truthy_off(env.get("AGENT_WORKTREES_AGENDA_NUDGE")):
        return False
    sidecar = aw / "nudge-state" / f"{worktree_id}.agenda.json"
    try:
        data = json.loads(sidecar.read_text("utf-8")) if sidecar.is_file() else {}
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    if "window_start" not in data:
        data = {"count": 0, "window_start": now}
    try:
        count = int(data.get("count", 0)) + 1
        elapsed_min = (now - float(data["window_start"])) / 60.0
    except (TypeError, ValueError):
        count, elapsed_min, data = 1, 0.0, {"window_start": now}
    due = (
        count >= _int_env(env, "AGENT_WORKTREES_NUDGE_CALLS", _DEFAULT_CALLS)
        or elapsed_min >= _int_env(env, "AGENT_WORKTREES_NUDGE_MINUTES", _DEFAULT_MINUTES)
    )
    data = {"count": 0, "window_start": now} if due else {**data, "count": count}
    if deadline is not None and time.time() >= deadline:
        return False
    try:
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        return False
    return due


def _update_counter(aw, worktree_id, yaml_path, env, now, deadline):
    note_at, state = _read_record_fields(yaml_path)
    note_at = _note_identity(note_at)
    if state in _TERMINAL_STATES:
        if deadline is not None and time.time() >= deadline:
            return None
        # Sealed disposition -- stop nudging and drop any sidecar.
        try:
            (aw / "nudge-state" / f"{worktree_id}.json").unlink(missing_ok=True)
        except OSError:
            pass
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
    if _note_identity(data.get("seen_note_at")) != note_at or "window_start" not in data:
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
    with _counter_lock(aw, worktree_id) as acquired:
        if acquired:
            try:
                (aw / "nudge-state" / f"{worktree_id}.json").unlink(missing_ok=True)
                (aw / "nudge-state" / f"{worktree_id}.agenda.json").unlink(missing_ok=True)
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
