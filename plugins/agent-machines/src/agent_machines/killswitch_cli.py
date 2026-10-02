"""``agent-machines bootstrap-killswitch`` CLI verbs, split out of
``__main__.py`` (module-size cap) -- registers the ``bootstrap-killswitch
{on,off,status}`` subcommands.

This is the primary, discoverable operator/agent surface for the
cross-plugin bootstrap-killswitch switch (see
``libs/bootstrap-killswitch/README.md``): one shared state file read by
every adopting plugin's vendored ``bootstrap-killswitch-guard.*``, so a
single on/off here pauses every plugin's sessionStart reconcile at once.

Deliberately self-contained, native Python (no shelling out to the vendored
bash/PowerShell guard) so this subcommand works identically on every OS
agent-machines itself supports, and so it's unit-testable without a shell.
The state file format and location are kept in lock-step with
``libs/bootstrap-killswitch/bootstrap-killswitch-guard.sh`` / ``.ps1`` --
change the three together.
"""

from __future__ import annotations

import argparse
import getpass
import glob
import json
import os
import socket
from datetime import datetime, timezone
from pathlib import Path


def _state_file() -> Path:
    override = os.environ.get("BOOTSTRAP_KILLSWITCH_STATE_FILE")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".copilot-extensions" / "bootstrap-killswitch.json"


def _live_reconciling_plugins() -> list[str]:
    """Best-effort: plugins with a reconcile already in flight right now.

    The switch only ever prevents a FUTURE session start from launching a
    new background reconcile -- it has no way to retroactively stop one
    already running when `on` is called (that process is detached and
    outlives the session-start hook that spawned it). Most adopters guard
    their own background reconcile with a `~/.<plugin>/reconcile.lock`
    single-flight file naming the live PID (see e.g.
    plugins/agent-dispatch/scripts/bootstrap-check.sh's own stale-reap
    comment); this checks that convention across every such lock this host
    knows about and reports any that are genuinely still running, so `on`
    never silently overclaims "reconciliation is paused" while one is
    actually still mutating a venv. Not exhaustive: a handful of adopters
    (agent-bridge, agent-machines, agent-worktrees) don't use this exact
    lock convention and aren't detected here -- see README.md's Known
    limitations.
    """
    live: list[str] = []
    scan_root = os.environ.get("BOOTSTRAP_KILLSWITCH_RECONCILE_SCAN_ROOT")
    root = Path(scan_root).expanduser() if scan_root else Path.home()
    for lock_path in sorted(glob.glob(str(root / ".*" / "reconcile.lock"))):
        lock = Path(lock_path)
        try:
            pid = int(lock.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            continue
        try:
            os.kill(pid, 0)
        except OSError:
            continue  # not alive (or we can't signal it) -- not in flight
        except Exception:
            continue  # platform doesn't support this probe -- skip, don't guess
        live.append(lock.parent.name.lstrip("."))
    return live



def _read_state(path: Path) -> tuple[dict, str | None]:
    """Returns (state, error). `state` always has a safe `active` key to act
    on (fails OPEN to `{"active": False}` on any problem -- a corrupt state
    file must never be reported as blocking every plugin's reconcile).
    `error` is None when the file is simply absent (the normal "never
    activated" case) or parsed cleanly; it names the problem when the file
    exists but could not be read/parsed, so `status` can surface a
    discoverable corruption instead of silently normalizing it away.
    """
    if not path.exists():
        return {"active": False}, None
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return {"active": False}, f"state file does not contain a JSON object: {path}"
        return data, None
    except Exception as exc:
        return {"active": False}, f"state file exists but could not be parsed ({exc}): {path}"


def cmd_status(args: argparse.Namespace) -> int:
    path = _state_file()
    state, error = _read_state(path)
    if getattr(args, "json", False):
        payload = {"path": str(path), **state}
        if error is not None:
            payload["error"] = error
        print(json.dumps(payload, indent=2))
        return 0
    if state.get("active") is True:
        print(f"ACTIVE -- {state.get('reason') or '(no reason given)'}")
        print(f"  set by: {state.get('set_by', '?')}")
        print(f"  set at: {state.get('set_at', '?')}")
        print(f"  state file: {path}")
        print("Reset with: agent-machines bootstrap-killswitch off")
    elif error is not None:
        print(f"inactive (fail-open) -- {error}")
        print("Every plugin's sessionStart reconcile runs normally, but this state")
        print("file is corrupt and should be inspected or cleared.")
    else:
        print("inactive -- every plugin's sessionStart reconcile runs normally.")
    return 0


def cmd_on(args: argparse.Namespace) -> int:
    path = _state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    reason = getattr(args, "reason", None) or ""
    who = f"{getpass.getuser()}@{socket.gethostname()}"
    state = {
        "active": True,
        "reason": reason,
        "set_by": who,
        "set_at": datetime.now(timezone.utc).isoformat(),
    }
    # Write to a same-directory temp file, then atomically replace the real
    # state file (os.replace is an atomic rename on a given filesystem). A
    # concurrent bootstrap-check guard's `check` reads this exact file on
    # every session start; writing in place would leave a window where it
    # could observe a truncated/partial file, fail OPEN, and let a reconcile
    # through even though the switch was (or was about to be) active.
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    live = _live_reconciling_plugins()
    if getattr(args, "json", False):
        payload = {"ok": True, "path": str(path), **state}
        if live:
            payload["in_flight_reconciles"] = live
        print(json.dumps(payload, indent=2))
        return 0
    print(f"ACTIVE{f' -- {reason}' if reason else ''}.")
    print("Every plugin's sessionStart reconcile is now paused on this machine.")
    if live:
        print(
            "WARNING: the switch only prevents a NEW reconcile from starting -- it "
            "cannot stop one already running. These plugin(s) have a reconcile "
            "in flight right now (detached; outlives this command):"
        )
        for name in live:
            print(f"  - {name}")
        print("Wait for it/them to finish before treating their venv as settled.")
    print("Reset with: agent-machines bootstrap-killswitch off")
    return 0


def cmd_off(args: argparse.Namespace) -> int:
    path = _state_file()
    existed = path.exists()
    if existed:
        path.unlink()
    if getattr(args, "json", False):
        print(json.dumps({"ok": True, "cleared": existed, "path": str(path)}, indent=2))
        return 0
    if existed:
        print("cleared. Plugins resume normal sessionStart reconcile.")
    else:
        print("already inactive; nothing to clear.")
    return 0


def register_parser(sub: argparse._SubParsersAction) -> None:
    """Wire the ``bootstrap-killswitch`` subcommand tree onto agent-machines'
    parser."""
    killswitch = sub.add_parser(
        "bootstrap-killswitch",
        help=(
            "Pause (or resume) every plugin's sessionStart bootstrap-check "
            "reconcile at once -- for hand-diagnosing a venv/install without "
            "a background reconcile racing the diagnosis."
        ),
    )
    killswitch_sub = killswitch.add_subparsers(dest="bootstrap_killswitch_command")

    on = killswitch_sub.add_parser("on", help="Activate the killswitch.")
    on.add_argument(
        "reason",
        nargs="?",
        default="",
        help="why the killswitch is being activated (shown by `status`)",
    )
    on.add_argument("--json", action="store_true", help="emit JSON")
    on.set_defaults(func=cmd_on)

    off = killswitch_sub.add_parser("off", help="Clear the killswitch.")
    off.add_argument("--json", action="store_true", help="emit JSON")
    off.set_defaults(func=cmd_off)

    status = killswitch_sub.add_parser("status", help="Show the current killswitch state.")
    status.add_argument("--json", action="store_true", help="emit JSON")
    status.set_defaults(func=cmd_status)

    # `agent-machines bootstrap-killswitch` with no further verb -> status.
    killswitch.set_defaults(func=cmd_status, json=False)
