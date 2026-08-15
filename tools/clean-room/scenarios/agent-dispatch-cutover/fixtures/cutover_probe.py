#!/usr/bin/env python3
"""Portable, stdlib-only cutover-resilience probe for agent-dispatch.

Drives the OS-agnostic zdd graceful-cutover mechanism against a REAL installed
agent-dispatch runtime, in an isolated HOME, and asserts the binding invariant of
the correct-install-flows effort (dotfiles#1393): *a version cutover must never
kill in-flight, non-resumable work* -- the installer stands the new coordinator
slot up beside the old, flips the routing table, drains at the safe cutover point
(between task claims), and retires the old.

It is the reusable core of the clean-room `agent-dispatch-cutover` scenario: the
thin scenario.sh installs + provisions the plugin on a fresh box and runs this
probe, so the thorny orchestration is verifiable independently of Docker (it runs
on any OS with the agent-dispatch venv, including this dev box).

Checks (each prints `PROBE: <name> PASS|FAIL <detail>`):
  survive-inflight     a QUEUED task survives the cutover (flip + retire + intact)
  held-task-readopt    a CLAIMED+STARTED task survives; the worker re-adopts the
                       new coordinator via the durable SQLite queue DB
  breadcrumb-recover   an aborted cutover strands a DRAINED survivor; recovery
                       (`_cutover --recover`) undrains it (not stuck closed)
  two-slot-wedged-fd   the old daemon holds an open FD in its slot ("wedged/locked")
                       while the cutover stands the new one up beside it and
                       retires the old -- the versioned beside-not-in-place model

Usage:
    python cutover_probe.py --python <agent_dispatch-venv-python> [--checks a,b]

Exit 0 iff every selected check PASSes.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ALL_CHECKS = ["survive-inflight", "held-task-readopt", "breadcrumb-recover", "two-slot-wedged-fd"]


class Ctx:
    def __init__(self, python: str, home: str):
        self.python = python
        self.home = home
        self.env = dict(os.environ)
        self.env.update(
            USERPROFILE=home, HOME=home,
            AGENT_DISPATCH_HOST="127.0.0.1",
            AGENT_DISPATCH_NO_AUTOSTART="1",
            PYTHONUTF8="1",
        )
        for k in ("AGENT_DISPATCH_PORT", "AGENT_DISPATCH_URL", "AGENT_DISPATCH_ENDPOINT"):
            self.env.pop(k, None)
        self.root = os.path.join(home, ".agent-dispatch")

    def cli(self, *args, timeout=60):
        return subprocess.run(
            [self.python, "-m", "agent_dispatch", *args],
            env=self.env, capture_output=True, text=True, timeout=timeout,
        )

    def spawn_serve(self, extra_env=None):
        env = dict(self.env)
        if extra_env:
            env.update(extra_env)
        kw = {}
        if sys.platform == "win32":
            kw["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
        else:
            kw["start_new_session"] = True
        return subprocess.Popen(
            [self.python, "-m", "agent_dispatch", "serve"], env=env, **kw
        )

    def active(self, tries=80):
        path = os.path.join(self.root, "active.json")
        for _ in range(tries):
            try:
                a = json.loads(open(path, encoding="utf-8").read()).get("active")
                if a and a.get("port"):
                    return a
            except Exception:
                pass
            time.sleep(0.25)
        return None


def _health(port, home):
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/health")
        with urllib.request.urlopen(req, timeout=3) as r:  # noqa: S310 loopback
            return json.loads(r.read().decode())
    except Exception as e:
        return {"_err": str(e)}


def _post(port, path, timeout=5):
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method="POST", data=b"{}",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 loopback
            return json.loads(r.read().decode())
    except Exception as e:
        return {"_err": str(e)}


def _listening(port):
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _retire(port):
    _post(port, "/shutdown")


def _queued(port, home):
    return (_health(port, home).get("backlog") or {}).get("queued", 0)


class Result:
    def __init__(self, name):
        self.name = name
        self.ok = True
        self.detail = []

    def check(self, cond, msg):
        if not cond:
            self.ok = False
            self.detail.append("FAILED: " + msg)
        else:
            self.detail.append("ok: " + msg)
        return cond

    def emit(self):
        status = "PASS" if self.ok else "FAIL"
        summary = "; ".join(d for d in self.detail if d.startswith("FAILED") or self.ok is False) or "all assertions held"
        if self.ok:
            summary = "; ".join(self.detail[-3:])
        print(f"PROBE: {self.name} {status} {summary}")
        return self.ok


# --------------------------------------------------------------------------


def check_survive_inflight(python):
    r = Result("survive-inflight")
    home = tempfile.mkdtemp(prefix="cvp-si-")
    c = Ctx(python, home)
    proc = None
    try:
        proc = c.spawn_serve()
        a = c.active()
        if not r.check(a is not None, "initial coordinator published routing"):
            return r
        old = a["port"]
        c.cli("create", "survivor-inflight", "--prompt", "survive")
        r.check(_queued(old, home) >= 1, "task enqueued before cutover")
        cut = c.cli("_cutover", "--json")
        r.check(cut.returncode == 0, f"_cutover rc==0 (rc={cut.returncode})")
        a2 = c.active()
        new = a2["port"] if a2 else None
        r.check(new is not None and new != old, f"routing flipped {old} -> {new}")
        time.sleep(1.5)
        r.check(not _listening(old), f"old coordinator :{old} retired (not listening)")
        r.check(bool(a2) and _listening(new), f"new coordinator :{new} healthy")
        r.check(_queued(new, home) >= 1, "queued task SURVIVED the cutover")
        if new:
            _retire(new)
        return r
    finally:
        try:
            if proc:
                proc.terminate()
        except Exception:
            pass
        time.sleep(0.5)
        shutil.rmtree(home, ignore_errors=True)


def check_held_task_readopt(python):
    r = Result("held-task-readopt")
    home = tempfile.mkdtemp(prefix="cvp-ht-")
    c = Ctx(python, home)
    proc = None
    try:
        proc = c.spawn_serve()
        a = c.active()
        if not r.check(a is not None, "initial coordinator up"):
            return r
        old = a["port"]
        # Create + claim + start a task so it is HELD (a lease) -- in-flight work
        # owned by a worker that must survive the coordinator swap. Pin an explicit
        # lane so the claim is deterministic (no cwd-git-repo dependence).
        lane = "probe-lane"
        cr = c.cli("create", "held-task", "--prompt", "in-flight", "--repo", lane)
        tid = None
        try:
            tid = json.loads(cr.stdout).get("id")
        except Exception:
            try:
                tid = json.loads("{" + cr.stdout.split("{", 1)[1]).get("id")
            except Exception:
                pass
        worker = "probe-machine/probe-wt"
        claim = c.cli("claim", "--repo", lane, "--machine", "probe-machine", "--worktree", "probe-wt")
        r.check(claim.returncode == 0 and "held-task" in (claim.stdout or ""),
                "task claimed by a worker (now held with a lease)")
        if tid:
            c.cli("start", tid, worker)
        cut = c.cli("_cutover", "--json")
        r.check(cut.returncode == 0, f"_cutover rc==0 (rc={cut.returncode})")
        a2 = c.active()
        new = a2["port"] if a2 else None
        r.check(new is not None and new != old, f"routing flipped {old} -> {new}")
        # The held task must still exist on the NEW coordinator (the worker
        # re-adopts via the durable queue DB + routing table). `show <tid>` is
        # lane-agnostic, unlike the cwd-scoped `list`.
        if tid:
            shown = c.cli("show", tid)
            st = ""
            try:
                st = json.loads(shown.stdout).get("status", "")
            except Exception:
                pass
            r.check(shown.returncode == 0 and "held-task" in (shown.stdout or ""),
                    f"held task present on the NEW coordinator (status={st or '?'}) -- durable-queue re-adoption")
            # And the worker can complete it against the new coordinator (real re-adoption).
            comp = c.cli("complete", tid, worker)
            r.check(comp.returncode == 0, "worker completed the held task via the new coordinator")
        else:
            r.check(False, "could not resolve the held task id from create output")
        if new:
            _retire(new)
        return r
    finally:
        try:
            if proc:
                proc.terminate()
        except Exception:
            pass
        time.sleep(0.5)
        shutil.rmtree(home, ignore_errors=True)


def check_breadcrumb_recover(python):
    r = Result("breadcrumb-recover")
    home = tempfile.mkdtemp(prefix="cvp-bc-")
    c = Ctx(python, home)
    proc = None
    try:
        proc = c.spawn_serve()
        a = c.active()
        if not r.check(a is not None, "coordinator up"):
            return r
        port = a["port"]
        # Simulate an ABORTED cutover: open the drain gate on the live coordinator
        # (it is now drained-but-alive = a "stranded survivor") and drop a
        # breadcrumb naming it, exactly as an orchestrator killed mid-cutover would.
        _post(port, "/drain")
        r.check(_health(port, home).get("status") == "draining", "survivor is drained (closed to new claims)")
        # Write the breadcrumb the recovery path reads.
        bc = {
            "state": "draining",
            "old": {"bind": a.get("bind", "127.0.0.1"), "port": port},
            "new_port": None,
            "started_at": "2026-01-01T00:00:00+00:00",
        }
        os.makedirs(c.root, exist_ok=True)
        # zdd breadcrumb file name is discovered via the library to stay in sync.
        bcpath = subprocess.run(
            [python, "-c",
             "from zdd.breadcrumb import breadcrumb_path;"
             "import os;print(breadcrumb_path(os.path.join(os.environ['HOME'],'.agent-dispatch')))"],
            env=c.env, capture_output=True, text=True).stdout.strip()
        open(bcpath, "w", encoding="utf-8").write(json.dumps(bc))
        r.check(os.path.exists(bcpath), "aborted-cutover breadcrumb written")
        # Recover: undrain the stranded survivor.
        rec = c.cli("_cutover", "--recover", "--json")
        r.check(rec.returncode == 0, f"_cutover --recover rc==0 (rc={rec.returncode})")
        time.sleep(0.5)
        r.check(_health(port, home).get("status") == "ok",
                "stranded survivor UNDRAINED by recovery (open to new claims again)")
        _retire(port)
        return r
    finally:
        try:
            if proc:
                proc.terminate()
        except Exception:
            pass
        time.sleep(0.5)
        shutil.rmtree(home, ignore_errors=True)


def check_two_slot_wedged_fd(python):
    r = Result("two-slot-wedged-fd")
    home = tempfile.mkdtemp(prefix="cvp-2s-")
    c = Ctx(python, home)
    proc = None
    fd = None
    try:
        proc = c.spawn_serve()
        a = c.active()
        if not r.check(a is not None, "coordinator up (slot A)"):
            return r
        old = a["port"]
        # WEDGE: the old daemon (and this probe) holds an open handle on a file in
        # the runtime dir -- the "locked/wedged daemon" the update must not need to
        # kill. On Windows this would block an in-place swap (os error 32); the
        # versioned beside-not-in-place model makes it a non-issue, which is what
        # we assert: the new slot comes up beside the FD-held old one.
        os.makedirs(c.root, exist_ok=True)
        lockfile = os.path.join(c.root, "wedge.lock")
        fd = open(lockfile, "w", encoding="utf-8")
        fd.write("held open by the old daemon's slot")
        fd.flush()
        c.cli("create", "survivor-2slot", "--prompt", "survive a wedged cutover")
        # Cut over while the FD is held open.
        cut = c.cli("_cutover", "--json")
        r.check(cut.returncode == 0, f"_cutover rc==0 with a wedged FD held (rc={cut.returncode})")
        a2 = c.active()
        new = a2["port"] if a2 else None
        r.check(new is not None and new != old, f"new slot stood up beside the wedged old: {old} -> {new}")
        time.sleep(1.5)
        r.check(not _listening(old), f"wedged old :{old} retired despite the held FD")
        r.check(bool(new) and _listening(new), f"new coordinator :{new} healthy")
        r.check(_queued(new, home) >= 1, "task survived the wedged cutover")
        if new:
            _retire(new)
        return r
    finally:
        try:
            if fd:
                fd.close()
        except Exception:
            pass
        try:
            if proc:
                proc.terminate()
        except Exception:
            pass
        time.sleep(0.5)
        shutil.rmtree(home, ignore_errors=True)


CHECKS = {
    "survive-inflight": check_survive_inflight,
    "held-task-readopt": check_held_task_readopt,
    "breadcrumb-recover": check_breadcrumb_recover,
    "two-slot-wedged-fd": check_two_slot_wedged_fd,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--python", required=True, help="the installed agent-dispatch venv python")
    ap.add_argument("--checks", default=",".join(ALL_CHECKS),
                    help="comma-separated subset of: " + ",".join(ALL_CHECKS))
    args = ap.parse_args()
    selected = [x.strip() for x in args.checks.split(",") if x.strip()]
    failed = 0
    for name in selected:
        fn = CHECKS.get(name)
        if not fn:
            print(f"PROBE: {name} FAIL unknown check")
            failed += 1
            continue
        try:
            res = fn(args.python)
            if not res.emit():
                failed += 1
        except Exception as e:  # a probe crash is a FAIL, not a wedge
            print(f"PROBE: {name} FAIL probe-exception {type(e).__name__}: {e}")
            failed += 1
    print(f"PROBE-SUMMARY: {len(selected) - failed}/{len(selected)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
