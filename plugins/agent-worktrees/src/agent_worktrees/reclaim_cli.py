"""Reclaim / remux / restart CLI surfaces extracted from ``__main__``."""

from __future__ import annotations

import argparse
import os
import platform

from . import output, reclaim, sessions, tracking
from . import config as cfg


def _core():
    from . import __main__ as core
    return core


def _core_helper(name: str, local):
    candidate = getattr(_core(), name, None)
    if callable(candidate) and candidate is not local:
        return candidate
    return local


def _infer_worktree_id_from_cwd(*args, **kwargs): return _core()._infer_worktree_id_from_cwd(*args, **kwargs)
def _json_error(*args, **kwargs): return _core()._json_error(*args, **kwargs)
def _json_output(*args, **kwargs): return _core()._json_output(*args, **kwargs)
def _resolve_worktree_id(*args, **kwargs): return _core()._resolve_worktree_id(*args, **kwargs)


def add_parsers(sub) -> None:
    p = sub.add_parser("restart", help="Stop a worktree's interactive Copilot (graceful double Ctrl-C, then mux kill-session) -- keeps the worktree on disk. The shared primitive behind the Picker 'Stop' action and NF 'Take over'; relaunch/ACP-resume is performed by the caller.")
    p.add_argument("worktree_id", help="Worktree id whose Copilot to stop")
    p.add_argument("--no-graceful", action="store_true", help="Skip the graceful double-Ctrl-C quit; hard-kill the mux session immediately")
    p.add_argument("--settle-timeout", type=float, default=6.0, help="Seconds to wait for a graceful quit before hard-killing (default: 6.0)")
    p.add_argument("--json", action="store_true", help="Emit a single JSON result object")
    p = sub.add_parser("reclaim", help="Free the exact Copilot process(es) bound to a session/worktree, resolved from Copilot's own inuse.<pid>.lock claim -- precise, never splashing onto a sibling session or a worktree that merely shares a cwd. The primitive for BARE orphans (a Copilot launched straight in a terminal, invisible to the wt-<id> mux fleet view). Dry-run by default; pass --yes to terminate. Freeing an idle orphan loses nothing -- the session stays resumable.")
    p.add_argument("--session-id", default=None, help="Target one session (exact dir name or unambiguous prefix)")
    p.add_argument("--worktree-id", default=None, help="Target every session bound to this worktree id (default: infer from cwd)")
    p.add_argument("--all", action="store_true", help="Target every bound Copilot on the machine")
    p.add_argument("--bare-only", action="store_true", help="Restrict to bound Copilots Stop cannot reach (homing bare or unclassifiable, OR homed in a mux whose wt-<id> session is unreachable -- a detached psmux server) -- the common intent; leaves only live, Stop-able muxed sessions to restart/reap")
    p.add_argument("--yes", action="store_true", help="Actually terminate the matched processes (without it, the command is a dry run that kills nothing)")
    p.add_argument("--json", action="store_true", help="Emit a single JSON result object")
    p = sub.add_parser("remux", help="Restore a running BARE (un-muxed) Copilot to the mux fleet. Linux/WSL reparents it into tmux via reptyr. Windows cannot reparent a live ConPTY process, so --yes precisely reclaims the Stop-unreachable owner and returns a resume-next result.")
    p.add_argument("--session-id", default=None, help="Target one session (exact dir name or unambiguous prefix)")
    p.add_argument("--worktree-id", default=None, help="Target the bare Copilot bound to this worktree id (default: infer from cwd)")
    p.add_argument("--sudo", dest="force_sudo", action=argparse.BooleanOptionalAction, default=None, help="Force (--sudo) or forbid (--no-sudo) running reptyr under sudo -A. Needed when the yama ptrace_scope forbids attaching a non-descendant; auto-detected by default.")
    p.add_argument("--yes", action="store_true", help="Windows only: reclaim the confirmed unreachable owner (without it, report the recovery plan)")
    p.add_argument("--json", action="store_true", help="Emit a single JSON result object")

def cmd_reclaim(args: argparse.Namespace) -> int:
    """``reclaim`` -- free the exact Copilot process(es) bound to a session.

    Resolves the authoritative pid<->session<->worktree binding from Copilot's
    own ``inuse.<pid>.lock`` files (see :mod:`agent_worktrees.reclaim`) and
    terminates *only* the matched process(es) and their Copilot child tree --
    never a sibling session, never an unrelated process that merely shares a
    working directory. The reclaim primitive for **bare** orphans (a Copilot
    launched straight in a terminal, invisible to the ``wt-<id>`` mux fleet
    view) whose terminal was closed or wedged; freeing one loses nothing, since
    the session stays resumable from its on-disk state.

    Target selection (at least one, else cwd is inferred):
      * ``--session-id <id>``  -- one session (exact or unambiguous prefix);
      * ``--worktree-id <id>`` -- every session bound to that worktree;
      * ``--all``              -- every bound Copilot on the machine.
    ``--bare-only`` restricts to un-muxed orphans (the common intent).

    Safety: the process subtree containing *this* command is never reaped, and
    without ``--yes`` the command is a dry run (prints the plan, kills nothing)
    -- confirm-before-destroy. JSON out with ``--json``.
    """
    session_id = getattr(args, "session_id", None)
    raw_wt = getattr(args, "worktree_id", None)
    want_all = getattr(args, "all", False)
    as_json = getattr(args, "json", False)

    wt_id: str | None = None
    wt_path: str | None = None

    def _wt_path(wid: str) -> str | None:
        yaml_path = cfg.tracking_dir() / f"{wid}.yaml"
        if yaml_path.exists():
            try:
                return tracking.load_record(yaml_path).worktree_path
            except Exception:
                return None
        return None

    if raw_wt:
        wt_id = _resolve_worktree_id(raw_wt)
        if as_json and getattr(args, "yes", False) and not session_id and not want_all:
            payload = _core_helper("reclaim_one", reclaim_one)(
                wt_id,
                bare_only=getattr(args, "bare_only", False),
            )
            _json_output(payload)
            return 0 if payload.get("ok") else 1
        wt_path = _wt_path(wt_id)
    elif not session_id and not want_all:
        # No explicit target -- infer the worktree from the current directory.
        wt_id = _infer_worktree_id_from_cwd()
        if not wt_id:
            return _json_error(
                "no --session-id/--worktree-id/--all and cwd is not a worktree",
                exit_code=2,
            )
        wt_path = _wt_path(wt_id)

    table = reclaim.build_process_table()
    found = reclaim.resolve_bound_copilots(
        session_id=session_id,
        worktree_id=wt_id,
        worktree_path=wt_path,
        table=table,
    )
    if wt_id and not session_id:
        seen_pids = {item["pid"] for item in found}
        for bound in reclaim.resolve_bridge_bound(wt_id, table=table):
            if bound["pid"] not in seen_pids:
                found.append(bound)
                seen_pids.add(bound["pid"])
    if getattr(args, "bare_only", False):
        # "orphans Stop cannot reach": a walkable non-mux ancestry ("bare"), one
        # whose homing could not be positively classified ("unknown", e.g. the
        # pid missing from a racing process-table snapshot), OR one homed in a
        # mux whose wt-<id> session is unreachable by the mux control socket (a
        # DETACHED psmux server -- dotfiles #1447). Only a live, Stop-able muxed
        # session is left to restart/Stop, so a bound Copilot Stop can't reach
        # stays reclaimable instead of stranding its worktree ACTIVE.
        found = reclaim.filter_stop_unreachable(found, table=table)

    # Safety guard: never reap the process subtree that contains this very
    # command (it runs as a child of the orchestrating Copilot).
    me = os.getpid()
    targets: list[dict] = []
    self_skipped: list[dict] = []
    for f in found:
        subtree = {f["pid"]} | reclaim.descendants_of(f["pid"], table)
        (self_skipped if me in subtree else targets).append(f)

    do_kill = getattr(args, "yes", False)
    reaped: list[dict] = []
    if do_kill and targets:
        reaped = reclaim.reap_bound_copilots(targets, table=table)

    # Clear residual inuse.<pid>.lock files (parity with reclaim_one, the local
    # picker path): force-remove the pids we just terminated plus any stale-pid
    # residue for this worktree, so a killed/crashed session leaves ZERO lock
    # behind. Only on a real kill (never a dry run) and only when scoped to a
    # worktree (a --session-id/--all sweep leaves lock GC to its own worktree
    # pass). A live muxed sibling's lock is preserved by clear_lock_residue.
    cleared: list[dict] = []
    if do_kill and (wt_id or wt_path):
        cleared = reclaim.clear_lock_residue(
            worktree_id=wt_id,
            worktree_path=wt_path,
            force_pids={r["pid"] for r in reaped if r.get("killed")},
            table=table,
        )

    # Tear down any DETACHED (Stop-unreachable) psmux server left hosting a
    # reaped mux-homed target, so a reclaimed still-ACTIVE worktree leaks no
    # orphaned server/pane (dotfiles #1447). Parity with reclaim_one. Only for
    # targets we ACTUALLY killed -- a Copilot that failed to terminate is still
    # live in its server, so its server must not be torn down.
    mux_torn_down: list[int] = []
    if do_kill and reaped:
        _killed = {r["pid"] for r in reaped if r.get("killed")}
        _killed_targets = [t for t in targets if t["pid"] in _killed]
        if _killed_targets:
            mux_torn_down = reclaim.teardown_detached_mux(_killed_targets, table=table)

    ok = all(result.get("killed") for result in reaped) if reaped else True
    payload = {
        "ok": ok,
        "action": "reclaim" if do_kill else "dry-run",
        "filters": {
            "session_id": session_id,
            "worktree_id": wt_id,
            "all": want_all,
            "bare_only": getattr(args, "bare_only", False),
        },
        "targets": targets,
        "self_skipped": self_skipped,
        "reaped": reaped,
        "locks_cleared": cleared,
        "mux_servers_torn_down": mux_torn_down,
    }

    if as_json:
        _json_output(payload)
        return 0 if ok else 1

    if not targets:
        print("No live Copilot process matched (nothing to reclaim).")
        for s in self_skipped:
            print(f"  (skipped self: {s['session_id'][:8]} pid {s['pid']})")
        return 0 if ok else 1

    verb = "Reclaimed" if do_kill else "Would reclaim"
    print(f"{verb} {len(targets)} bound Copilot process(es):")
    for t in targets:
        wt = t["worktree_id"] or "?"
        line = f"  {t['session_id'][:8]}  pid {t['pid']:<6} [{t['homing']}]  {wt}"
        if do_kill:
            r = next((x for x in reaped if x["pid"] == t["pid"]), None)
            if r:
                mark = "killed" if r["killed"] else "FAILED"
                line += f"  -> {mark} (+{r['children_killed']} children)"
        print(line)
    for s in self_skipped:
        print(f"  (skipped self: {s['session_id'][:8]} pid {s['pid']})")
    if not do_kill:
        print("\nDry run -- pass --yes to actually terminate these processes.")
    return 0


def _perform_remux(
    *,
    worktree_id: str | None,
    session_id: str | None,
    worktree_path: str | None,
    force_sudo: bool | None,
    apply_windows: bool,
) -> dict:
    """Run the platform remux primitive and return its structured result."""
    from . import remux as _remux

    if platform.system() != "Windows":
        return _remux.remux_bare_copilot(
            worktree_id=worktree_id,
            session_id=session_id,
            worktree_path=worktree_path,
            force_sudo=force_sudo,
        )

    def _fail(reason: str, **extra) -> dict:
        return {
            "ok": False, "reason": reason, "worktree_id": worktree_id, "session_id": session_id,
            "action": "failed", "requires_resume": False, **extra,
        }

    if not worktree_id:
        return _fail("Windows remux requires a resolved worktree id")
    if sessions.has_mux_session(worktree_id):
        return _fail(
            "the worktree already has a live mux session; attach with Open instead of restoring it"
        )

    table = reclaim.build_process_table()
    found = reclaim.resolve_bound_copilots(
        session_id=session_id,
        worktree_id=worktree_id,
        worktree_path=worktree_path,
        table=table,
    )
    seen_pids = {item["pid"] for item in found}
    for bound in reclaim.resolve_bridge_bound(worktree_id, table=table):
        if bound["pid"] not in seen_pids:
            found.append(bound)
            seen_pids.add(bound["pid"])
    found = reclaim.filter_stop_unreachable(found, table=table)

    me = os.getpid()
    targets = [
        item
        for item in found
        if me not in ({item["pid"]} | reclaim.descendants_of(item["pid"], table))
    ]
    if not targets:
        return _fail("no Stop-unreachable bound Copilot found for the target")
    if len(targets) > 1:
        pids = ", ".join(str(item["pid"]) for item in targets)
        return _fail(
            f"multiple unreachable Copilots match ({pids}); narrow with --session-id",
            targets=targets,
        )

    target = targets[0]
    preview = {
        "ok": True,
        "reason": (
            "Windows cannot reparent the live ConPTY process; reclaim this "
            "confirmed unreachable owner, then resume the persisted session "
            "through the normal mux launcher"
        ),
        "worktree_id": worktree_id,
        "session_id": target.get("session_id"),
        "pid": target.get("pid"),
        "action": "preview",
        "requires_resume": True,
        "targets": targets,
    }
    if not apply_windows:
        return preview

    reclaimed = _core_helper("reclaim_one", reclaim_one)(
        worktree_id,
        bare_only=True,
        target_pids={int(target["pid"])},
    )
    return {
        **preview,
        "ok": bool(reclaimed.get("ok")) and bool(reclaimed.get("targets")),
        "reason": (
            "unreachable owner reclaimed; resume the persisted session through "
            "the normal mux launcher"
            if reclaimed.get("ok") and reclaimed.get("targets")
            else "the unreachable owner could not be reclaimed"
        ),
        "action": "reclaimed",
        "reclaim": reclaimed,
    }


def cmd_remux(args: argparse.Namespace) -> int:
    """``remux`` -- restore a bare Copilot to the worktree mux fleet.

    Linux/WSL adopts the live process into tmux with ``reptyr``. Windows cannot
    move an arbitrary live console process into a new ConPTY, so it previews a
    precise reclaim-before-resume plan and applies it only with ``--yes``.
    """
    session_id = getattr(args, "session_id", None)
    raw_wt = getattr(args, "worktree_id", None)
    as_json = getattr(args, "json", False)

    def _wt_path(wid: str) -> str | None:
        yaml_path = cfg.tracking_dir() / f"{wid}.yaml"
        if yaml_path.exists():
            try:
                return tracking.load_record(yaml_path).worktree_path
            except Exception:
                return None
        return None

    wt_id: str | None = None
    wt_path: str | None = None
    if raw_wt:
        wt_id = _resolve_worktree_id(raw_wt)
        wt_path = _wt_path(wt_id)
    elif not session_id:
        wt_id = _infer_worktree_id_from_cwd()
        if not wt_id:
            return _json_error(
                "no --session-id/--worktree-id and cwd is not a worktree", exit_code=2
            )
        wt_path = _wt_path(wt_id)

    result = _core_helper("_perform_remux", _perform_remux)(
        worktree_id=wt_id,
        session_id=session_id,
        worktree_path=wt_path,
        force_sudo=getattr(args, "force_sudo", None),
        apply_windows=getattr(args, "yes", False),
    )

    if as_json:
        _json_output(result)
        return 0 if result.get("ok") else 1

    if not result.get("ok"):
        output.err(result.get("reason", "re-mux failed"))
        return 1
    if result.get("requires_resume"):
        verb = "Reclaimed" if result.get("action") == "reclaimed" else "Would reclaim"
        output.ok(
            f"{verb} pid {result.get('pid')} for "
            f"{result.get('worktree_id')}; resume next to create and attach "
            "the mux session."
        )
        if result.get("action") == "preview":
            output.info("Dry run -- pass --yes to reclaim the unreachable owner.")
        return 0
    sess, pid = result.get("session"), result.get("pid")
    if result.get("verified"):
        output.ok(
            f"Re-muxed pid {pid} into {sess} (pane {result.get('pane')}). "
            f"Attach:  tmux attach -t {sess}"
        )
    else:
        output.info(
            f"Opened a reptyr pane in {sess} for pid {pid} -- "
            f"{result.get('reason')}. Attach to check:  "
            f"tmux attach -t {sess}"
        )
    return 0


def reclaim_one(
    worktree_id: str,
    *,
    bare_only: bool = True,
    target_pids: set[int] | None = None,
) -> dict:
    """Reap the bound Copilot process(es) for one worktree (Picker "Reclaim").

    The in-process executor behind the Picker's per-row **Reclaim** action:
    resolves the exact Copilot process(es) bound to *worktree_id*'s session(s)
    via :func:`reclaim.resolve_bound_copilots` and terminates them (and their
    Copilot child tree). ``bare_only`` (default) restricts to bound Copilots
    **Stop cannot reach** -- a bound Copilot with no mux ancestor (homing
    ``bare``), one whose ancestry couldn't be classified (``unknown``), OR one
    homed in a mux whose ``wt-<id>`` session is unreachable by the mux control
    socket (a **detached** psmux server -- dotfiles #1447). A live, Stop-able
    muxed sibling is left to the graceful ``restart``/Stop path. Never reaps the
    process subtree containing this command. Returns a JSON-able
    ``{ok, worktree_id, targets, reaped}``.

    ``target_pids`` narrows mutation to an already-verified process set. The
    Windows remux flow uses it so the apply step cannot re-resolve and kill a
    different owner that appeared after the guard pass.
    """
    table = reclaim.build_process_table()
    found = reclaim.resolve_bound_copilots(worktree_id=worktree_id, table=table)
    # #4272/#1416: a bare-resumed / bridge-owned session (cwd=home) is invisible
    # to the cwd-keyed resolve above, so an ACTIVE-via-bridge worktree would
    # offer Reclaim (see engine._reclaimable) but reap NOTHING -- stranding the
    # row ACTIVE with no way to act. Its bridge.lock carries the worktree id +
    # owner pid, so union those targets in (deduped by pid).
    seen_pids = {f["pid"] for f in found}
    for b in reclaim.resolve_bridge_bound(worktree_id, table=table):
        if b["pid"] not in seen_pids:
            found.append(b)
            seen_pids.add(b["pid"])
    if bare_only:
        # Reachability-aware: keep a mux-homed target when its wt-<id> mux is
        # unreachable by Stop (a detached psmux server -- dotfiles #1447), so
        # Reclaim isn't a no-op on it; preserve only a live, Stop-able mux.
        found = reclaim.filter_stop_unreachable(found, table=table)
    if target_pids is not None:
        found = [item for item in found if item["pid"] in target_pids]
    me = os.getpid()
    targets = [
        f for f in found if me not in ({f["pid"]} | reclaim.descendants_of(f["pid"], table))
    ]
    reaped = reclaim.reap_bound_copilots(targets, table=table) if targets else []
    ok = all(r["killed"] for r in reaped) if reaped else True
    # Clear residual inuse.<pid>.lock files so the worktree ends with ZERO
    # residue -- "to the point where the pid lock file is removed". Force-remove
    # the pids we just terminated (the OS may not have reaped them yet, so a
    # liveness re-check could still read them alive) plus any pre-existing
    # stale-pid residue; a live muxed sibling's lock is preserved.
    killed_pids = {r["pid"] for r in reaped if r.get("killed")}
    cleared = reclaim.clear_lock_residue(
        worktree_id=worktree_id,
        force_pids=killed_pids,
        table=table,
    )
    # A bridge Copilot reaped out from under agent-bridge can't run the bridge's
    # on-exit lock cleanup, so unlink the now-dead bridge.lock too -- otherwise
    # the file-first bridge scan keeps reading the worktree ACTIVE.
    bridge_cleared = reclaim.clear_bridge_locks(
        worktree_id,
        force_pids=killed_pids,
        table=table,
    )
    # A reaped mux-homed target in a DETACHED (Stop-unreachable) psmux server
    # leaves the server + its pane shell running; nothing else GCs it while the
    # worktree is still ACTIVE. Tear those orphaned servers down by pid so the
    # worktree ends with zero residue (dotfiles #1447). Only for targets we
    # ACTUALLY killed -- a Copilot that failed to terminate is still live in its
    # server, so its server must not be torn down.
    killed_targets = [t for t in targets if t["pid"] in killed_pids]
    mux_torn_down = (
        reclaim.teardown_detached_mux(killed_targets, table=table) if killed_targets else []
    )
    # Update the worktree's cached liveness authoritatively so the automatic
    # post-action refresh renders the TRUE post-reclaim state instead of
    # re-reading the pre-reclaim cache (the "Reclaim ran but the row stayed
    # ACTIVE" bug). Reclaim's own reload path only ever *reads* the bound_live/
    # mux_live hints, so unless the executor writes them here they stay stale
    # until the off-hot-path reconcile or the freshness TTL. Race-safe: a pid we
    # just terminated may not be OS-reaped yet, so re-resolve the remaining
    # binding EXCLUDING ``killed_pids`` (mirrors ``clear_lock_residue``). A
    # preserved live, Stop-able mux-homed sibling still counts as bound, so
    # ``bound_live`` only clears when nothing bound remains. Reuses the already-
    # built process ``table`` (resolve does LIVE pid checks for liveness
    # regardless of table age, and the ``killed_pids`` exclusion is what guards
    # the just-killed race -- a fresh table would add cost without changing the
    # outcome). Best-effort.
    try:
        remaining_bound = [
            b
            for b in reclaim.resolve_bound_copilots(worktree_id=worktree_id, table=table)
            if b["pid"] not in killed_pids
        ]
        remaining_bound += [
            b
            for b in reclaim.resolve_bridge_bound(worktree_id, table=table)
            if b["pid"] not in killed_pids
        ]
        tracking.stamp_bound_live(worktree_id, bool(remaining_bound))
        tracking.stamp_mux_live(
            worktree_id,
            sessions.has_mux_session(worktree_id),
            sync=True,
        )
    except Exception:
        pass
    return {
        "ok": ok, "worktree_id": worktree_id, "targets": len(targets), "reaped": reaped,
        "locks_cleared": cleared, "bridge_locks_cleared": bridge_cleared,
        "mux_servers_torn_down": mux_torn_down,
    }


def cmd_restart(args: argparse.Namespace) -> int:
    """``restart <id>`` -- stop a worktree's interactive Copilot, keep the worktree.

    The shared primitive behind the Picker "Stop" action and NF "Take over":
    graceful double-Ctrl-C quit (Copilot's native clean exit), falling back to a
    hard mux kill-session. Relaunch / ACP-resume is the caller's job. (The CLI
    verb stays ``restart``; the picker labels it "Stop".)
    """
    payload = sessions.restart_worktree_copilot(
        args.worktree_id,
        graceful=not getattr(args, "no_graceful", False),
        settle_timeout=getattr(args, "settle_timeout", 6.0),
    )
    if getattr(args, "json", False):
        _json_output(payload)
        return 0 if payload["ok"] else 1
    wt = payload["worktree_id"]
    if not payload["had_session"]:
        print(f"{wt}: no interactive Copilot running (nothing to stop).")
        return 0
    if payload["method"] == "graceful":
        print(f"{wt}: Copilot quit gracefully (double Ctrl-C).")
    elif payload["method"] == "hard":
        print(f"{wt}: Copilot hard-stopped (mux kill-session).")
    else:
        print(f"{wt}: failed to stop the interactive Copilot.")
    return 0 if payload["ok"] else 1


