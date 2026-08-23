#!/usr/bin/env python3
"""Portable single-instance daemon-guard probe for agent-bridge (concurrent-flip family).

Asserts the guard that prevents the duplicate-daemon failure mode at its ROOT:
when a second ``agent-bridge start`` races the first -- the classic case is a
concurrent ``sessionStart``-hook reinstall spawning a second daemon while one is
live -- the guard must *refuse* the duplicate instead of standing a colliding
daemon up beside the first. It complements the relay-bind probe: that one proves
the relay recovers if a duplicate slips through; this one proves the duplicate is
refused *before* it can bind at all.

It drives the REAL guard (``agent_bridge.singleton.SingleInstance`` over the shared
``single_instance_lease`` OS byte-range lock), with **cross-process** holders (the
probe re-execs itself in ``--hold`` mode as a subprocess), so it is faithful to two
real daemon processes and deterministic. Verifiable off-Docker on any built
agent-bridge venv.

Checks (each prints ``PROBE: <name> PASS|FAIL <detail>``):
  duplicate-refused          a live holder owns the lease (same config dir + port);
                             a second acquire raises ``AlreadyRunningError`` naming
                             the holder pid -- the duplicate daemon is refused.
  dead-holder-reclaimed      a holder acquires then is KILLED; a fresh acquire
                             SUCCEEDS -- the OS frees the lock on death, so a stale
                             lock never wedges startup (the liveness-reclamation a
                             resident single-owner daemon must be able to rely on).
  passive-coexist-by-port    a holder owns the lease on port A; a second instance on
                             the SAME config dir but port B acquires -- an active and
                             a passive daemon coexist during a cutover (keying on the
                             port, not just the dir).

Usage:
    python singleton_guard_probe.py [--checks a,b,c]
    python singleton_guard_probe.py --hold <config_dir> --port <n>   # internal

Exit 0 iff every selected check PASSes. Requires ``agent_bridge`` importable (run
under the built agent-bridge venv python).
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import tempfile
import time

ALL_CHECKS = ["duplicate-refused", "dead-holder-reclaimed", "passive-coexist-by-port"]

_HELD_MARK = "HELD"


def _emit(name: str, ok: bool, detail: str) -> bool:
    print(f"PROBE: {name} {'PASS' if ok else 'FAIL'} {detail}")
    return ok


def _imports():
    from agent_bridge.singleton import AlreadyRunningError, SingleInstance

    return SingleInstance, AlreadyRunningError


def _spawn_holder(cfg_dir: str, port: int) -> subprocess.Popen:
    """Re-exec this file in --hold mode; a live subprocess holds the lease.

    Returns once the child has printed ``HELD <pid>`` on stdout (lease acquired),
    so the caller can rely on the lease being held before it probes.
    """
    proc = subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), "--hold", cfg_dir, "--port", str(port)],
        stdout=subprocess.PIPE,
        stdin=subprocess.PIPE,
        text=True,
    )
    # Wait (bounded) for the HELD marker so the acquire has definitely happened.
    deadline = time.time() + 10.0
    assert proc.stdout is not None
    while time.time() < deadline:
        line = proc.stdout.readline()
        if line.startswith(_HELD_MARK):
            return proc
        if proc.poll() is not None:
            break
    raise RuntimeError("holder subprocess never reported HELD")


def _stop_holder(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def _hold_mode(cfg_dir: str, port: int) -> int:
    """Acquire the real lease, announce it, and block until killed / stdin closes."""
    SingleInstance, _ = _imports()
    lease = SingleInstance(cfg_dir, port=port)
    lease.acquire()  # keep `lease` referenced for the process lifetime
    sys.stdout.write(f"{_HELD_MARK} {os.getpid()}\n")
    sys.stdout.flush()
    try:
        sys.stdin.read()  # blocks; returns when the parent closes our stdin
    except Exception:
        pass
    return 0


def _check_duplicate_refused(cfg_dir: str) -> bool:
    SingleInstance, AlreadyRunningError = _imports()
    port = 51001
    holder = _spawn_holder(cfg_dir, port)
    try:
        contender = SingleInstance(cfg_dir, port=port)
        try:
            contender.acquire()
            contender.release()
            return _emit("duplicate-refused", False, "(second acquire SUCCEEDED -- guard did not refuse)")
        except AlreadyRunningError as exc:
            holder_pid = getattr(exc, "holder_pid", None)
            named = holder_pid is not None
            return _emit(
                "duplicate-refused",
                named,
                f"(second acquire refused: AlreadyRunningError holder_pid={holder_pid}"
                + ("" if named else " -- guard refused but did not name the holder")
                + ")",
            )
    finally:
        _stop_holder(holder)


def _check_dead_holder_reclaimed(cfg_dir: str) -> bool:
    SingleInstance, _ = _imports()
    port = 51002
    holder = _spawn_holder(cfg_dir, port)
    # Hard-kill the holder: the OS must free the byte-range lock on death.
    holder.kill()
    holder.wait(timeout=5)
    time.sleep(0.2)
    fresh = SingleInstance(cfg_dir, port=port)
    try:
        fresh.acquire()
        ok = bool(getattr(fresh, "held", True))
        fresh.release()
        return _emit(
            "dead-holder-reclaimed",
            ok,
            "(fresh acquire succeeded after holder death -- stale lock did not wedge)",
        )
    except Exception as exc:
        return _emit("dead-holder-reclaimed", False, f"(fresh acquire FAILED: {exc!r})")


def _check_passive_coexist_by_port(cfg_dir: str) -> bool:
    SingleInstance, _ = _imports()
    holder = _spawn_holder(cfg_dir, 51003)  # "active" on port A
    try:
        passive = SingleInstance(cfg_dir, port=51004)  # "passive" on port B
        try:
            passive.acquire()
            ok = bool(getattr(passive, "held", True))
            passive.release()
            return _emit(
                "passive-coexist-by-port",
                ok,
                "(second instance on a different port coexists -- active/passive cutover)",
            )
        except Exception as exc:
            return _emit("passive-coexist-by-port", False, f"(coexist acquire FAILED: {exc!r})")
    finally:
        _stop_holder(holder)


def _run(checks: list[str]) -> int:
    dispatch = {
        "duplicate-refused": _check_duplicate_refused,
        "dead-holder-reclaimed": _check_dead_holder_reclaimed,
        "passive-coexist-by-port": _check_passive_coexist_by_port,
    }
    all_ok = True
    with tempfile.TemporaryDirectory(prefix="cr-singleton-") as cfg_dir:
        for name in checks:
            fn = dispatch.get(name)
            if fn is None:
                all_ok = _emit(name, False, "(unknown check)") and all_ok
                continue
            try:
                all_ok = fn(cfg_dir) and all_ok
            except Exception as exc:
                all_ok = _emit(name, False, f"(probe error: {exc!r})") and all_ok
    return 0 if all_ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hold", metavar="CONFIG_DIR", help="internal: hold the lease and block")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--checks", default=",".join(ALL_CHECKS))
    args = ap.parse_args()
    if args.hold:
        return _hold_mode(args.hold, args.port)
    checks = [c.strip() for c in args.checks.split(",") if c.strip()]
    return _run(checks)


if __name__ == "__main__":
    # A killed holder subprocess should die quietly, not dump a traceback.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    sys.exit(main())
