#!/usr/bin/env python3
"""Adversarial mock harness for the transient-hook-client contract.

Implements `plugin-process-hygiene`'s Phase 4b(ii) design
(`efforts/active/plugin-process-hygiene/adversarial-convergence-mock.md`):
validates, against a synthetic mock daemon (NOT a real plugin runtime), the
shared shape every `agent-*` plugin's hooks/extension callbacks should follow
per `visions/plugin-services`'s `hooks-and-callbacks-are-transient` and
`process-count-scales-with-services-not-sessions` Behaviors (PR #2300):

    resolve identity -> discover the live daemon -> post one bounded packet
    -> optionally read back guidance -> EXIT. Never spawn a daemon to be
    sure one exists; degrade to inline/no-op when none is reachable; never
    block past a wall-clock budget.

This harness is deliberately plugin-agnostic: it exercises the *contract*,
not any real plugin's code, so it is fast, dependency-free (stdlib only), and
runs the same on every OS. Real per-plugin conformance is issue #2301's own
audit, cited against this harness's PASS/FAIL evidence rather than
re-validated here.

Roles (this file plays all three, selected by `--role`):

  daemon   Acquire a single-instance lease, publish a rendezvous file, serve
           one HTTP endpoint (`POST /hook`). A daemon that cannot acquire the
           lease exits immediately with a distinct stand-down code -- it
           never becomes a second listener.
  client   The 5-step transient-hook-client contract above, against the
           daemon's rendezvous file. Always exits; never spawns a daemon.
  (none)   The adversary driver -- this default mode. Spawns floods of
           daemon/client subprocesses as REAL OS processes (never
           threads/asyncio tasks -- the whole point is *process* count) under
           each adversarial scenario, and asserts on the system: total
           process count before/after, every client's actual exit, and no
           leftover process of either kind once each scenario settles.

Usage:
    python plugin_process_hygiene_convergence.py [--scenarios a,b] [--flood-n N]

Exit 0 iff every selected scenario PASSes.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

ALL_SCENARIOS = [
    "flood-against-live-daemon",
    "flood-against-absent-daemon",
    "daemon-appears-mid-flood",
    "daemon-dies-mid-packet",
    "concurrent-daemon-race",
    "process-count-invariant-under-repeated-floods",
]

# Every process this harness spawns carries this unique tag in argv (via
# --tag) so the driver can enumerate "processes belonging to this run" from
# the OS process table -- the same technique the agent-dispatch-supervisor-
# self-update clean-room probe uses, generalized here.
_THIS_FILE = os.path.abspath(__file__)

# Client wall-clock budget. Generous enough for CI jitter, tight enough that
# a hang is unambiguous.
_CLIENT_BUDGET_S = 0.3
# How long the driver waits for a spawned daemon to publish its rendezvous
# file before concluding it failed to start.
_DAEMON_READY_TIMEOUT_S = 5.0
# How long the driver waits, after a flood/scenario, for every process it
# spawned to have actually exited before asserting "no leftovers".
_SETTLE_TIMEOUT_S = 5.0


# ---------------------------------------------------------------------------
# Shared: rendezvous file + single-instance lease (minimal, stdlib-only
# reimplementations of the shape agent-dispatch's own libs already provide --
# this harness intentionally does not depend on agent_dispatch so it stays a
# plugin-agnostic contract test, not a re-test of one plugin's library).
# ---------------------------------------------------------------------------


def _rendezvous_path(home: str) -> str:
    return os.path.join(home, "rendezvous.json")


def _lock_path(home: str) -> str:
    return os.path.join(home, "daemon.lock")


def write_rendezvous(home: str, host: str, port: int) -> None:
    path = _rendezvous_path(home)
    tmp = path + f".tmp-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"host": host, "port": port}, f)
    os.replace(tmp, path)  # atomic on both POSIX and Windows


def read_rendezvous(home: str):
    try:
        with open(_rendezvous_path(home), encoding="utf-8") as f:
            data = json.load(f)
        return data["host"], int(data["port"])
    except Exception:
        return None


def remove_rendezvous(home: str) -> None:
    try:
        os.remove(_rendezvous_path(home))
    except OSError:
        pass


class _Lease:
    """A minimal, OS-released-on-crash single-instance lease.

    Mirrors agent-dispatch's ``single_instance.SingleInstance`` shape closely
    enough for this contract test: an exclusive OS-level lock the kernel
    releases automatically if the holder dies, so a live daemon is never
    displaced and a dead one never wedges the scope.
    """

    def __init__(self, path: str):
        self.path = path
        self._fd = None

    def acquire(self) -> bool:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            if sys.platform == "win32":
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self._fd = fd
        try:
            os.ftruncate(fd, 0)
            os.write(fd, str(os.getpid()).encode("ascii"))
        except OSError:
            pass
        return True

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt

                os.lseek(self._fd, 0, os.SEEK_SET)
                msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fd, fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None


# ---------------------------------------------------------------------------
# Daemon role
# ---------------------------------------------------------------------------


def _make_handler(behavior: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # silence default stderr logging
            pass

        def do_POST(self):
            if self.path != "/hook":
                self.send_response(404)
                self.end_headers()
                return
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            if behavior == "die-on-request":
                # Simulate the daemon dying mid-packet: close the connection
                # without responding, then terminate the whole process. The
                # client must see this as a clean (bounded) failure, not a
                # hang.
                try:
                    self.connection.shutdown(1)  # SHUT_WR, no response sent
                except OSError:
                    pass
                os._exit(1)
            body = json.dumps({"guidance": "ok"}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def run_daemon(home: str, behavior: str) -> int:
    lease = _Lease(_lock_path(home))
    if not lease.acquire():
        print("STANDDOWN", flush=True)
        return 3
    try:
        server = HTTPServer(("127.0.0.1", 0), _make_handler(behavior))
    except OSError:
        lease.release()
        print("BIND-FAILED", flush=True)
        return 4
    host, port = server.server_address
    write_rendezvous(home, host, port)
    print(f"DAEMON-READY pid={os.getpid()} port={port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        remove_rendezvous(home)
        lease.release()
    return 0


# ---------------------------------------------------------------------------
# Client role -- the transient-hook-client contract itself
# ---------------------------------------------------------------------------


def run_client(home: str, budget_s: float, result_path: str) -> int:
    deadline = time.monotonic() + budget_s
    # Step 1: resolve identity (trivial in this mock -- a real hook resolves
    # session/worktree from already-available env/CLI state, never by
    # re-running a full CLI subcommand tree).
    identity = {"session": "mock-session", "pid": os.getpid()}
    # Step 2: discover the live daemon. A miss is a normal outcome -- NEVER a
    # trigger to spawn one.
    rendezvous = read_rendezvous(home)
    if rendezvous is None:
        _write_result(result_path, "degraded-no-daemon")
        return 0
    host, port = rendezvous
    remaining = max(0.01, deadline - time.monotonic())
    conn = None
    try:
        # Step 3: post one bounded packet.
        conn = http.client.HTTPConnection(host, port, timeout=remaining)
        conn.request("POST", "/hook", body=json.dumps(identity))
        resp = conn.getresponse()
        data = resp.read()
        # Step 4: optionally read back guidance (relayed verbatim; this mock
        # just records that it arrived).
        json.loads(data.decode("utf-8"))
        _write_result(result_path, "served")
    except Exception as exc:
        _write_result(result_path, f"degraded-error:{type(exc).__name__}")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    # Step 5: exit. (Falling off the end of main() below does this.)
    return 0


def _write_result(path: str, value: str) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(value)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Driver helpers
# ---------------------------------------------------------------------------


def _spawn(role: str, home: str, tag: str, *, behavior: str | None = None,
           budget: float | None = None, result_path: str | None = None):
    argv = [sys.executable, _THIS_FILE, "--role", role, "--home", home, "--tag", tag]
    if behavior is not None:
        argv += ["--behavior", behavior]
    if budget is not None:
        argv += ["--budget", str(budget)]
    if result_path is not None:
        argv += ["--result-path", result_path]
    kw: dict = {}
    if sys.platform == "win32":
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.Popen(
        argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, **kw,
    )


def _count_tagged_processes(tag: str) -> int:
    """Count live OS processes carrying --tag <tag> in their command line.

    Uses the OS process table directly (not this script's own bookkeeping)
    so a leaked/orphaned process is caught even if this script's own handles
    lost track of it.

    The tag is passed via an **environment variable**, never embedded
    literally in the query command's own `-Command`/argv string -- otherwise
    the querying helper process's own command line would contain the same
    substring it searches for and self-match, inflating every count by
    (at least) one.
    """
    if sys.platform == "win32":
        ps = shutil.which("powershell") or shutil.which("pwsh")
        if not ps:
            return -1
        cmd = (
            "$t = $env:CR_PPC_TAG; @(Get-CimInstance Win32_Process | "
            "Where-Object { $_.CommandLine -like ('*' + $t + '*') }).Count"
        )
        env = dict(os.environ)
        env["CR_PPC_TAG"] = tag
        r = subprocess.run(
            [ps, "-NoProfile", "-NonInteractive", "-Command", cmd],
            capture_output=True, text=True, timeout=20, env=env,
        )
        out = (r.stdout or "").strip()
        return int(out) if out.isdigit() else 0
    r = subprocess.run(
        ["pgrep", "-fc", tag], capture_output=True, text=True, timeout=20,
    )
    out = (r.stdout or "").strip()
    return int(out) if out.isdigit() else 0


def _wait_for_rendezvous(home: str, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if read_rendezvous(home) is not None:
            return True
        time.sleep(0.05)
    return False


def _wait_settled(tag: str, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _count_tagged_processes(tag) == 0:
            return True
        time.sleep(0.1)
    return False


class Result:
    def __init__(self, name: str):
        self.name = name
        self.ok = True
        self.detail: list[str] = []

    def check(self, cond: bool, msg: str) -> bool:
        if not cond:
            self.ok = False
            self.detail.append("FAILED: " + msg)
        else:
            self.detail.append("ok: " + msg)
        return cond

    def emit(self) -> bool:
        status = "PASS" if self.ok else "FAIL"
        summary = "; ".join(d for d in self.detail if d.startswith("FAILED")) or "; ".join(self.detail[-3:])
        print(f"PROBE: {self.name} {status} {summary}")
        return self.ok


def _flood_clients(home: str, tag: str, n: int, *, budget: float = _CLIENT_BUDGET_S):
    """Spawn n concurrent client processes; return (procs, result_paths)."""
    procs = []
    result_paths = []
    for i in range(n):
        rp = os.path.join(home, f"client-result-{i}-{uuid.uuid4().hex[:6]}.txt")
        result_paths.append(rp)
        procs.append(_spawn("client", home, tag, budget=budget, result_path=rp))
    return procs, result_paths


def _collect_results(procs, result_paths, timeout: float) -> list[str]:
    deadline = time.monotonic() + timeout
    for p in procs:
        remaining = max(0.1, deadline - time.monotonic())
        try:
            p.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            pass
    out = []
    for rp in result_paths:
        try:
            with open(rp, encoding="utf-8") as f:
                out.append(f.read().strip())
        except OSError:
            out.append("<missing>")
    return out


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


def scenario_flood_against_live_daemon(flood_n: int) -> Result:
    r = Result("flood-against-live-daemon")
    home = tempfile.mkdtemp(prefix="cr-ppc-flive-")
    tag = f"ppc-flive-{uuid.uuid4().hex[:8]}"
    daemon = None
    try:
        before = _count_tagged_processes(tag)
        daemon = _spawn("daemon", home, tag, behavior="normal")
        r.check(_wait_for_rendezvous(home, _DAEMON_READY_TIMEOUT_S), "daemon published its rendezvous file")
        procs, result_paths = _flood_clients(home, tag, flood_n)
        results = _collect_results(procs, result_paths, timeout=_CLIENT_BUDGET_S * 4 + 2)
        served = sum(1 for x in results if x == "served")
        r.check(served == flood_n, f"all {flood_n} clients were served (got {served})")
        r.check(_count_tagged_processes(tag) == before + 1, "exactly 1 daemon process live, no client residue")
        return r
    finally:
        if daemon and daemon.poll() is None:
            daemon.terminate()
            try:
                daemon.wait(timeout=3)
            except subprocess.TimeoutExpired:
                daemon.kill()
        shutil.rmtree(home, ignore_errors=True)


def scenario_flood_against_absent_daemon(flood_n: int) -> Result:
    r = Result("flood-against-absent-daemon")
    home = tempfile.mkdtemp(prefix="cr-ppc-fabs-")
    tag = f"ppc-fabs-{uuid.uuid4().hex[:8]}"
    try:
        before = _count_tagged_processes(tag)
        procs, result_paths = _flood_clients(home, tag, flood_n)
        results = _collect_results(procs, result_paths, timeout=_CLIENT_BUDGET_S * 4 + 2)
        degraded = sum(1 for x in results if x.startswith("degraded"))
        r.check(degraded == flood_n, f"all {flood_n} clients degraded to inline/no-op (got {degraded})")
        r.check(
            not os.path.exists(_rendezvous_path(home)),
            "no client ever published a rendezvous file -- none self-promoted into a daemon",
        )
        r.check(_count_tagged_processes(tag) == before, "process count unchanged (zero new daemons)")
        return r
    finally:
        shutil.rmtree(home, ignore_errors=True)


def scenario_daemon_appears_mid_flood(flood_n: int) -> Result:
    r = Result("daemon-appears-mid-flood")
    home = tempfile.mkdtemp(prefix="cr-ppc-mid-")
    tag = f"ppc-mid-{uuid.uuid4().hex[:8]}"
    daemon = None
    try:
        # Start half the flood against an absent daemon, then publish, then
        # send the rest -- proving both sides of the transition behave.
        early_procs, early_paths = _flood_clients(home, tag, flood_n // 2)
        daemon = _spawn("daemon", home, tag, behavior="normal")
        r.check(_wait_for_rendezvous(home, _DAEMON_READY_TIMEOUT_S), "daemon published rendezvous mid-flood")
        late_procs, late_paths = _flood_clients(home, tag, flood_n - flood_n // 2)
        early_results = _collect_results(early_procs, early_paths, timeout=_CLIENT_BUDGET_S * 4 + 2)
        late_results = _collect_results(late_procs, late_paths, timeout=_CLIENT_BUDGET_S * 4 + 2)
        # Early clients may have raced ahead of publication (degrade) or
        # landed after it (served) -- both are correct; neither hangs.
        r.check(
            all(x.startswith(("served", "degraded")) for x in early_results),
            "every early client reached a definite (served or degraded) outcome, none hung",
        )
        late_served = sum(1 for x in late_results if x == "served")
        r.check(late_served == len(late_results), f"every late client (after publication) was served ({late_served}/{len(late_results)})")
        return r
    finally:
        if daemon and daemon.poll() is None:
            daemon.terminate()
            try:
                daemon.wait(timeout=3)
            except subprocess.TimeoutExpired:
                daemon.kill()
        shutil.rmtree(home, ignore_errors=True)


def scenario_daemon_dies_mid_packet(flood_n: int) -> Result:
    r = Result("daemon-dies-mid-packet")
    home = tempfile.mkdtemp(prefix="cr-ppc-die-")
    tag = f"ppc-die-{uuid.uuid4().hex[:8]}"
    daemon = None
    try:
        daemon = _spawn("daemon", home, tag, behavior="die-on-request")
        r.check(_wait_for_rendezvous(home, _DAEMON_READY_TIMEOUT_S), "daemon published its rendezvous file")
        start = time.monotonic()
        procs, result_paths = _flood_clients(home, tag, flood_n)
        results = _collect_results(procs, result_paths, timeout=_CLIENT_BUDGET_S * 4 + 2)
        elapsed = time.monotonic() - start
        degraded = sum(1 for x in results if x.startswith("degraded"))
        r.check(degraded == flood_n, f"all {flood_n} clients degraded cleanly on daemon death (got {degraded})")
        r.check(
            elapsed <= _CLIENT_BUDGET_S * 2 + 3,
            f"clients returned within a bounded time after daemon death ({elapsed:.2f}s)",
        )
        return r
    finally:
        if daemon and daemon.poll() is None:
            daemon.terminate()
        shutil.rmtree(home, ignore_errors=True)


def scenario_concurrent_daemon_race(daemon_m: int) -> Result:
    r = Result("concurrent-daemon-race")
    home = tempfile.mkdtemp(prefix="cr-ppc-race-")
    tag = f"ppc-race-{uuid.uuid4().hex[:8]}"
    daemons = []
    try:
        daemons = [_spawn("daemon", home, tag, behavior="normal") for _ in range(daemon_m)]
        ready = _wait_for_rendezvous(home, _DAEMON_READY_TIMEOUT_S)
        r.check(ready, "exactly one contender published a rendezvous file")
        time.sleep(1.0)  # let the losers observe the lease and stand down
        outs = []
        for p in daemons:
            try:
                out = (p.stdout.readline() or "").strip() if p.poll() is not None else ""
            except Exception:
                out = ""
            outs.append(out)
        alive = [p for p in daemons if p.poll() is None]
        r.check(len(alive) == 1, f"exactly 1 daemon is alive/listening (got {len(alive)} of {daemon_m})")
        procs, result_paths = _flood_clients(home, tag, 10)
        results = _collect_results(procs, result_paths, timeout=_CLIENT_BUDGET_S * 4 + 2)
        served = sum(1 for x in results if x == "served")
        r.check(served == 10, f"a client flood reaches the single winner (served {served}/10)")
        return r
    finally:
        for p in daemons:
            if p.poll() is None:
                p.terminate()
        shutil.rmtree(home, ignore_errors=True)


def scenario_process_count_invariant(flood_n: int, rounds: int) -> Result:
    r = Result("process-count-invariant-under-repeated-floods")
    tag_base = f"ppc-inv-{uuid.uuid4().hex[:8]}"
    baseline = _count_tagged_processes(tag_base)
    r.check(baseline == 0, "baseline tag is unused before the suite starts")
    for i in range(rounds):
        home = tempfile.mkdtemp(prefix=f"cr-ppc-inv-{i}-")
        tag = f"{tag_base}-{i}"
        daemon = None
        try:
            daemon = _spawn("daemon", home, tag, behavior="normal")
            _wait_for_rendezvous(home, _DAEMON_READY_TIMEOUT_S)
            procs, result_paths = _flood_clients(home, tag, flood_n)
            _collect_results(procs, result_paths, timeout=_CLIENT_BUDGET_S * 4 + 2)
        finally:
            if daemon and daemon.poll() is None:
                daemon.terminate()
                try:
                    daemon.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    daemon.kill()
            settled = _wait_settled(tag, _SETTLE_TIMEOUT_S)
            r.check(settled, f"round {i}: every process tagged '{tag}' exited (no leftovers)")
            shutil.rmtree(home, ignore_errors=True)
    return r


SCENARIOS = {
    "flood-against-live-daemon": lambda flood_n, **_: scenario_flood_against_live_daemon(flood_n),
    "flood-against-absent-daemon": lambda flood_n, **_: scenario_flood_against_absent_daemon(flood_n),
    "daemon-appears-mid-flood": lambda flood_n, **_: scenario_daemon_appears_mid_flood(flood_n),
    "daemon-dies-mid-packet": lambda flood_n, **_: scenario_daemon_dies_mid_packet(flood_n),
    "concurrent-daemon-race": lambda flood_n, daemon_m=10, **_: scenario_concurrent_daemon_race(daemon_m),
    "process-count-invariant-under-repeated-floods": lambda flood_n, rounds=5, **_: scenario_process_count_invariant(flood_n, rounds),
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--role", choices=["daemon", "client"], default=None,
                    help="internal: run as a spawned daemon/client instead of the driver")
    ap.add_argument("--home", help="isolated home dir (role mode)")
    ap.add_argument("--tag", help="unique process tag embedded for OS-level enumeration")
    ap.add_argument("--behavior", default="normal", help="daemon role: normal | die-on-request")
    ap.add_argument("--budget", type=float, default=_CLIENT_BUDGET_S, help="client role: wall-clock budget seconds")
    ap.add_argument("--result-path", help="client role: where to write the outcome")
    ap.add_argument("--scenarios", default=",".join(ALL_SCENARIOS),
                    help="driver: comma-separated subset of: " + ",".join(ALL_SCENARIOS))
    ap.add_argument("--flood-n", type=int, default=50, help="driver: concurrent clients per flood")
    ap.add_argument("--rounds", type=int, default=5, help="driver: rounds for the repeated-flood invariant")
    ap.add_argument("--daemon-m", type=int, default=10, help="driver: contenders for the daemon-election scenario")
    args = ap.parse_args()

    if args.role == "daemon":
        sys.exit(run_daemon(args.home, args.behavior))
    if args.role == "client":
        sys.exit(run_client(args.home, args.budget, args.result_path))

    # Driver mode.
    selected = [x.strip() for x in args.scenarios.split(",") if x.strip()]
    failed = 0
    for name in selected:
        fn = SCENARIOS.get(name)
        if not fn:
            print(f"PROBE: {name} FAIL unknown scenario")
            failed += 1
            continue
        try:
            res = fn(args.flood_n, rounds=args.rounds, daemon_m=args.daemon_m)
            if not res.emit():
                failed += 1
        except Exception as e:
            print(f"PROBE: {name} FAIL probe-exception {type(e).__name__}: {e}")
            failed += 1
    print(f"PROBE-SUMMARY: {len(selected) - failed}/{len(selected)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
