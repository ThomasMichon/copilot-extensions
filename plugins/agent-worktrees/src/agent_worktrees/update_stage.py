"""Background update *staging* for the launch path (issue #1430).

The launcher used to run the whole update/reconcile chain **serially before**
showing the Picker: ``copilot plugin update agent-worktrees`` (a ~1.3-1.9s
marketplace network call, even when already at latest) + ``pre-launch`` +
``reconcile-plugins`` x2. That fixed cost is paid on every boot, before the
Picker can paint.

This module implements the **stage** half of the Copilot-style
*stage-then-join* model: run the slow marketplace download **in the background
while the Picker is open**, so the operator's think-time hides it. The launcher
then **joins** (waits for) this stage and **applies** any pending update *after*
the Picker closes and *before* the psmux/Copilot handoff.

Critical safety constraint (why stage != apply): the Picker (``resolve``) runs
from the **installed runtime venv** ``~/.agent-worktrees/.venv``. This stage
only touches the **marketplace payload dir**
(``~/.copilot/installed-plugins/copilot-extensions/agent-worktrees``) via
``copilot plugin update`` -- it never rewrites the running venv -- so it is safe
to run concurrently with the Picker. The *apply* (installer: payload->runtime +
pip) must run from the shell after the Picker exits; it lives in the launch
wrappers, not here.

Single-flight: a lockfile (PID + start epoch, with stale reclaim) ensures two
near-simultaneous launches never both hit the marketplace / race the payload
write. A second launch whose stage finds the lock held simply records
``skipped: locked`` and exits -- the in-flight stage's result is authoritative.

The heavier, order-sensitive steps (the ``pre-launch`` self-update installers
and the two-pass ``reconcile-plugins``) stay in the shell apply step; this
stage only pre-computes the cheap ``pre-launch`` *plan* so the join can skip a
redundant spawn when nothing is stale.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import config as cfg

# ── Well-known paths (under the runtime dir ~/.agent-worktrees) ────────────
_STATUS_NAME = "updater-status.json"
_LOCK_NAME = "updater.lock"

# The plugin's marketplace payload id and the files whose hashes decide whether
# a downloaded update actually changed anything worth applying.
_PLUGIN_ID = "agent-worktrees@copilot-extensions"
_FINGERPRINT_FILES = (
    "pyproject.toml",
    "plugin.json",
    "bin/launch-session.ps1",
    "bin/launch-session.sh",
    "scripts/install.ps1",
    "scripts/install.sh",
)

# A stage older than this (seconds) is considered abandoned and its lock is
# reclaimed -- covers a crashed stage that never released.
_LOCK_TTL_SECS = 120
# Upper bound on the marketplace download itself.
_COPILOT_UPDATE_TIMEOUT = 90


def status_path() -> Path:
    return cfg.install_dir() / _STATUS_NAME


def lock_path() -> Path:
    return cfg.install_dir() / _LOCK_NAME


# ── PID liveness (portable; never uses os.kill on Windows) ─────────────────
def _pid_alive(pid: int) -> bool:
    """Best-effort: is a process with this PID currently running?"""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid
            )
            if not handle:
                return False
            try:
                code = wintypes.DWORD()
                if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return code.value == STILL_ACTIVE
                return True
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            # If we can't tell, assume alive so we don't stomp a live lock.
            return True
    # POSIX: signal 0 probes existence without delivering a signal.
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return True
    return True


def acquire_lock(lock: Path | None = None, *, pid: int | None = None) -> bool:
    """Acquire the single-flight update lock.

    Returns True if acquired. Reclaims a lock whose owner is dead or whose age
    exceeds the TTL. Best-effort and race-tolerant: two racers may both think
    they won in a tight window, which is acceptable here (the loser's stage is a
    redundant, idempotent no-op guarded by the marketplace itself).
    """
    lock = lock or lock_path()
    pid = os.getpid() if pid is None else pid
    now = time.time()
    if lock.exists():
        try:
            data = json.loads(lock.read_text(encoding="utf-8"))
            owner = int(data.get("pid", -1))
            started = float(data.get("started", 0.0))
        except Exception:
            owner, started = -1, 0.0
        fresh = (now - started) < _LOCK_TTL_SECS
        if fresh and _pid_alive(owner):
            return False  # a live stage owns it
        # else: stale -- fall through and reclaim
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(
            json.dumps({"pid": pid, "started": now}), encoding="utf-8"
        )
        return True
    except Exception:
        return False


def release_lock(lock: Path | None = None, *, pid: int | None = None) -> None:
    """Release the lock if we (this pid) still own it."""
    lock = lock or lock_path()
    pid = os.getpid() if pid is None else pid
    try:
        if lock.exists():
            data = json.loads(lock.read_text(encoding="utf-8"))
            if int(data.get("pid", -1)) == pid:
                lock.unlink()
    except Exception:
        # Best-effort; a stale lock is reclaimed by age/PID next round.
        pass


# ── Plugin payload discovery + fingerprint ─────────────────────────────────
def discover_plugin_dir(home: Path | None = None) -> tuple[Path | None, str]:
    """Locate the active agent-worktrees plugin payload dir and its layout.

    Mirrors the launcher's discovery: prefer the marketplace layout, fall back
    to a ``_direct`` install. Only the marketplace layout is updatable via
    ``copilot plugin update``.
    """
    home = home or Path.home()
    marketplace = (
        home / ".copilot" / "installed-plugins" / "copilot-extensions"
        / "agent-worktrees"
    )
    if marketplace.exists():
        return marketplace, "marketplace"
    direct_root = home / ".copilot" / "installed-plugins" / "_direct"
    if direct_root.exists():
        for child in sorted(direct_root.iterdir()):
            manifest = child / "plugin.json"
            if manifest.exists():
                try:
                    pj = json.loads(manifest.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if pj.get("name") == "agent-worktrees":
                    return child, "direct"
    return None, ""


def fingerprint(plugin_dir: Path) -> str:
    """Hash the version/launcher/installer files to detect a real change."""
    import hashlib

    h = hashlib.sha256()
    for rel in _FINGERPRINT_FILES:
        fp = plugin_dir / rel
        if fp.exists():
            try:
                h.update(fp.read_bytes())
            except Exception:
                h.update(b"<unreadable>")
        h.update(b"\x00")
    return h.hexdigest()


def _run_copilot_update() -> tuple[bool, str]:
    """Run the marketplace download. Returns (ran, combined_output)."""
    from shutil import which

    if which("copilot") is None:
        return False, "copilot not on PATH"
    try:
        proc = subprocess.run(
            ["copilot", "plugin", "update", _PLUGIN_ID],
            capture_output=True,
            text=True,
            timeout=_COPILOT_UPDATE_TIMEOUT,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return True, out.strip()
    except subprocess.TimeoutExpired:
        return False, f"copilot plugin update timed out ({_COPILOT_UPDATE_TIMEOUT}s)"
    except Exception as e:  # never break the launch
        return False, f"copilot plugin update error: {e}"


def stage(
    *,
    status: Path | None = None,
    lock: Path | None = None,
    home: Path | None = None,
) -> dict:
    """Perform one background staging pass and write the status file.

    Steps (all safe w.r.t. the running Picker's venv):
      1. Single-flight: acquire the lock, else record ``skipped: locked``.
      2. Discover the marketplace payload dir (else ``skipped``).
      3. Fingerprint -> ``copilot plugin update`` -> fingerprint; the diff is
         ``plugin_changed`` (the shell apply runs the installer iff changed).
      4. Pre-compute the cheap ``pre-launch`` staleness plan so the join can
         skip a redundant spawn when nothing is stale.

    Returns the status dict (also written to ``status``).
    """
    status = status or status_path()
    lock = lock or lock_path()
    result: dict = {"stage_done": False, "ts": time.time()}

    if not acquire_lock(lock):
        result.update(stage_done=True, skipped="locked", plugin_changed=False)
        _write_status(status, result)
        return result

    try:
        plugin_dir, layout = discover_plugin_dir(home)
        if plugin_dir is None:
            result.update(
                stage_done=True, skipped="no-plugin-dir", plugin_changed=False
            )
            _write_status(status, result)
            return result

        result["plugin_dir"] = str(plugin_dir)
        result["layout"] = layout

        plugin_changed = False
        copilot_output = "skipped (non-marketplace layout)"
        if layout == "marketplace":
            before = fingerprint(plugin_dir)
            ran, copilot_output = _run_copilot_update()
            after = fingerprint(plugin_dir) if ran else before
            plugin_changed = ran and (before != after)

        result["plugin_changed"] = plugin_changed
        result["copilot_output"] = copilot_output

        # Cheap staleness plan for the join (best-effort; never fatal).
        try:
            from .__main__ import plan_pre_launch

            result["prelaunch"] = plan_pre_launch()
        except Exception as e:
            result["prelaunch"] = {"action": "continue", "reason": f"error: {e}"}

        result["stage_done"] = True
        _write_status(status, result)
        return result
    finally:
        release_lock(lock)


def _write_status(status: Path, data: dict) -> None:
    try:
        status.parent.mkdir(parents=True, exist_ok=True)
        status.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


def read_status(status: Path | None = None) -> dict:
    """Read the last stage status (empty dict if missing/unreadable)."""
    status = status or status_path()
    try:
        return json.loads(status.read_text(encoding="utf-8"))
    except Exception:
        return {}


def indicator_state(
    *,
    status: Path | None = None,
    lock: Path | None = None,
) -> str:
    """Picker-facing update state for the version indicator (#1430).

    Returns one of:
      "checking"  -- a background stage is in flight (live, fresh lock, or the
                     last stage recorded ``skipped: locked`` because a peer
                     stage owns the lock);
      "available" -- the stage finished and the marketplace payload changed
                     (an update is staged, ready to apply on launch/refresh);
      "current"   -- the stage finished and nothing changed (up to date);
      "idle"      -- no stage has run / it was skipped (no plugin dir, etc.).

    Read-only and cheap (two small files); safe to poll on the render tick.
    """
    lk = lock or lock_path()
    try:
        if lk.exists():
            data = json.loads(lk.read_text(encoding="utf-8"))
            started = float(data.get("started", 0.0))
            owner = int(data.get("pid", -1))
            if (time.time() - started) < _LOCK_TTL_SECS and _pid_alive(owner):
                return "checking"
    except Exception:
        pass
    st = read_status(status)
    if not st or not st.get("stage_done"):
        return "idle"
    if st.get("skipped") == "locked":
        return "checking"
    if st.get("skipped"):
        return "idle"
    return "available" if st.get("plugin_changed") else "current"


def cmd_stage_update(args) -> int:
    """CLI: run one background staging pass (launcher backgrounds this)."""
    st = getattr(args, "status", None)
    result = stage(status=Path(st) if st else None)
    if getattr(args, "json", False):
        print(json.dumps(result))
    return 0
