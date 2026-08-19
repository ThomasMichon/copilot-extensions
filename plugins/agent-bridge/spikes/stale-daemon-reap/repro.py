"""Hermetic repro/validation for the #1612 stale / duplicate-daemon reap.

Models the *duplicate/stale-daemon* half of dotfiles#1612 -- where a crashed or
cutover-superseded agent-bridge daemon left **orphaned / duplicate** daemons
holding :9280, and ``service start`` had *"no clean reap-and-rebind"* so it never
recovered (stale processes held the port; a duplicate daemon lingered). dev297
adopts the shared ``single_instance_lease`` primitive (#759); this spike
exercises its **reconcile-set reaper** -- the outside backstop that retires a
service's strays down to the single routing-table ``active`` -- against **real**
throwaway child processes (no agent-bridge daemon, no port bind, no network).

It proves:

1. **Discovery.** ``superseded_pids_from_table`` reads a zdd-shaped routing table
   and returns exactly the stray pids -- a ``previous`` slot **and a STALE
   ``active``** whose pid differs from the genuinely-live active (the #1612
   "an old daemon is still recorded on :9280 while a new one was promoted"
   snapshot) -- and never the known-live active.

2. **Reap.** ``reconcile_set_reap`` retires every identified stray, **spares** the
   live ``active`` and ``self``, actually terminates the stray OS processes, and
   leaves the active alive.

3. **Fail-soft + identity guards.** A stray whose terminate *raises* is recorded
   in ``failed`` and **never re-raised** (a stray must never fail a cutover); an
   already-dead pid is skipped; and a live pid a ``verify`` check vetoes (pid
   reuse) is skipped, not killed.

**A note on liveness.** The library's ``pid_alive`` is an *OS probe*
(``OpenProcess`` on Windows) whose result depends on no handle being held to the
target -- true for the real, independent daemons the reaper retires. In THIS
harness the stand-ins are our own ``subprocess.Popen`` children, so we hold a
handle that keeps a killed pid queryable (a harness artifact, not a lib bug). We
therefore inject the reaper's documented ``alive=`` seam with a deterministic
``Popen.poll()`` oracle and confirm deaths via ``poll()`` -- validating the
reaper's *policy* (spare active/self, terminate strays, fail-soft) directly.
``superseded_pids_from_table`` is pure and used as-is.

Run from the plugin's dev venv:

    .venv\\Scripts\\python.exe spikes\\stale-daemon-reap\\repro.py

Exit code 0 = all checks passed.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import time

_PROCS: dict[int, subprocess.Popen] = {}


def _spawn_stand_in() -> subprocess.Popen:
    """A real, long-lived throwaway process standing in for a daemon."""
    p = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(600)"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    _PROCS[p.pid] = p
    return p


def _alive(pid: int | None) -> bool:
    """Deterministic liveness oracle for our own stand-in children.

    ``Popen.poll()`` reflects a child's real exit without the OS-probe handle
    artifact. Any pid we did not spawn (e.g. ``os.getpid()``) is treated as
    alive -- the reaper spares ``self``/``active`` before it ever consults this.
    """
    p = _PROCS.get(pid)  # type: ignore[arg-type]
    if p is None:
        return True
    return p.poll() is None


def _terminate(pid: int) -> None:
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    else:
        os.kill(pid, 9)


def _wait_dead(pid: int, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return not _alive(pid)


def main() -> int:
    from single_instance_lease import (
        reconcile_set_reap,
        superseded_pids_from_table,
    )

    results: list[tuple[bool, str]] = []
    try:
        # Real stand-in processes: one live "new active" and two strays -- a
        # cutover `previous` and a STALE `active` duplicate still recorded on
        # :9280 (the #1612 leak shape).
        active = _spawn_stand_in()
        stray_prev = _spawn_stand_in()
        stray_stale_active = _spawn_stand_in()
        time.sleep(0.2)  # let them become schedulable/alive

        # A zdd-shaped routing snapshot taken BEFORE the new active was written:
        # `active` still names the stale pid, `previous` the superseded one. The
        # caller knows `active.pid` is genuinely live out-of-band.
        table = {
            "active": {"pid": stray_stale_active.pid, "port": 9280, "gen": 146},
            "previous": {"pid": stray_prev.pid, "port": 9280, "gen": 147},
        }

        # 1) Discovery: exactly the two strays (incl. the stale `active`), and
        #    never the genuinely-live active.
        cands = superseded_pids_from_table(table, active_pid=active.pid)
        disc_ok = cands == {stray_prev.pid, stray_stale_active.pid}
        results.append((
            disc_ok,
            f"discovery: superseded_pids_from_table -> {sorted(cands)} "
            f"(strays {sorted([stray_prev.pid, stray_stale_active.pid])}; "
            f"live active {active.pid} excluded)",
        ))

        # 2) Reap: retire the strays, spare active + self, actually kill them.
        res = reconcile_set_reap(
            own_pids={
                active.pid, stray_prev.pid, stray_stale_active.pid, os.getpid(),
            },
            active_pid=active.pid,
            self_pid=os.getpid(),
            terminate=_terminate,
            alive=_alive,
        )
        strays_dead = _wait_dead(stray_prev.pid) and _wait_dead(
            stray_stale_active.pid
        )
        reap_ok = (
            set(res.reaped) == {stray_prev.pid, stray_stale_active.pid}
            and active.pid in res.skipped
            and os.getpid() in res.skipped
            and res.failed == []
            and res.ok
            and strays_dead
            and _alive(active.pid)  # the live active survived
        )
        results.append((
            reap_ok,
            f"reap: reaped {sorted(res.reaped)}, spared active+self "
            f"{sorted(res.skipped)}, strays dead={strays_dead}, "
            f"active alive={_alive(active.pid)}",
        ))

        # 3) Fail-soft + identity guards: a raising terminate is recorded, not
        #    re-raised; a dead pid and a verify-vetoed live pid are skipped.
        dead = _spawn_stand_in()
        veto = _spawn_stand_in()   # live, but verify-vetoed (pid-reuse defense)
        fail = _spawn_stand_in()   # live, terminate raises
        time.sleep(0.2)
        _terminate(dead.pid)
        _wait_dead(dead.pid)

        def _terminate_raising(pid: int) -> None:
            if pid == fail.pid:
                raise OSError("simulated terminate failure (fail-soft check)")
            _terminate(pid)

        raised = None
        res2 = None
        try:
            res2 = reconcile_set_reap(
                own_pids={dead.pid, veto.pid, fail.pid},
                active_pid=None,
                self_pid=os.getpid(),
                terminate=_terminate_raising,
                verify=lambda pid: pid != veto.pid,  # veto -> skip (pid reuse)
                alive=_alive,
            )
        except BaseException as exc:  # fail-soft must NOT raise
            raised = exc
        guards_ok = (
            raised is None                              # never propagated
            and res2 is not None
            and fail.pid in res2.failed                 # recorded, fail-soft
            and not res2.ok
            and dead.pid in res2.skipped                # already dead -> skip
            and veto.pid in res2.skipped                # verify vetoed -> skip
            and _alive(veto.pid)                        # vetoed pid NOT killed
        )
        results.append((
            guards_ok,
            (f"fail-soft+guards: failed={sorted(res2.failed)} "
             f"skipped={sorted(res2.skipped)} ok={res2.ok}; vetoed pid "
             f"alive={_alive(veto.pid)}; no exception propagated")
            if res2 is not None else
            f"fail-soft+guards: reconcile_set_reap RAISED "
            f"{type(raised).__name__} (must be fail-soft)",
        ))
    finally:
        for p in _PROCS.values():
            with contextlib.suppress(Exception):
                p.kill()

    print("\n=== stale-daemon-reap repro results ===")
    for ok, msg in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {msg}")
    passed = all(ok for ok, _ in results)
    print(f"\n{'ALL PASS' if passed else 'FAILURES PRESENT'}\n")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
