#!/usr/bin/env python3
"""Portable, stdlib-only cutover-resilience probe for agent-index.

Drives the OS-agnostic zdd graceful-cutover mechanism against a REAL installed
agent-index *service* daemon, in an isolated HOME, and asserts the binding
invariant of the correct-install-flows effort (dotfiles#1393): *a version cutover
must never kill in-flight, non-resumable work*. agent-index's durable unit is the
SQLite indexing queue (``$AGENT_INDEX_HOME/data/tasks.db``); the installer stands
a new service slot up beside the old, health-gates it, flips the routing table
(``active.json``), drains the old, and retires it -- so an in-flight/queued index
batch survives and the new service re-adopts it via the shared durable DB.

It is the reusable core of the clean-room ``agent-index-cutover`` scenario: the
thin scenario.sh installs + provisions the plugin on a fresh box and runs this
probe, so the thorny orchestration is verifiable independently of Docker (it runs
on any OS with a built agent-index service venv).

Design notes that keep this dependency-light (no torch engine, no lancedb):
  * "in-flight work" is created by enqueuing a task straight into the durable
    TaskStore (pure SQLite) -- the same row a POST /reindex would create -- so the
    probe never needs the heavy embedding/index dependencies to prove the QUEUE
    survives the version swap. (The engine daemon on fixed port 8421 is a separate
    unit and is intentionally NOT exercised here; a service cutover leaves it
    untouched.)
  * cutover is driven through the real ``agent_index deploy`` seam (there is no
    ``_cutover`` subcommand -- ``deploy`` IS the zdd active/passive orchestrator).

Checks (each prints ``PROBE: <name> PASS|FAIL <detail>``):
  survive-queued-batch  a QUEUED index task survives the cutover (routing flips,
                        old service retired, new service healthy, the task row is
                        still present in the shared durable tasks.db)
  drain-gate            /drain closes the service to new work (/health -> draining,
                        /reindex refused); the cutover then yields a FRESH, non-
                        draining service that accepts work again
  breadcrumb-recover    an aborted cutover strands a DRAINED survivor; recovery
                        (``deploy --recover``) undrains it (not stuck closed)

Usage:
    python cutover_probe.py --python <agent_index-service-venv-python> [--checks a,b]

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
import types
import urllib.request

ALL_CHECKS = ["survive-queued-batch", "drain-gate", "breadcrumb-recover"]


class Ctx:
    def __init__(self, python: str, home: str):
        self.python = python
        self.home = home
        self.env = dict(os.environ)
        self.env.update(
            AGENT_INDEX_HOME=home,
            AGENT_INDEX_HOST="127.0.0.1",
            AGENT_INDEX_ROLE="host",
            PYTHONUTF8="1",
        )
        for k in ("AGENT_INDEX_PORT", "AGENT_INDEX_ENDPOINT", "AGENT_INDEX_RUN_DIR"):
            self.env.pop(k, None)
        # $AGENT_INDEX_HOME is the routing/install root: active.json lives here and
        # the durable queue at $AGENT_INDEX_HOME/data/tasks.db (config.py).
        self.root = home
        self.db = os.path.join(home, "data", "tasks.db")

    def cli(self, *args, timeout=90):
        return subprocess.run(
            [self.python, "-m", "agent_index", *args],
            env=self.env, capture_output=True, text=True, timeout=timeout,
        )

    def deploy(self, *extra, timeout=180):
        """Run `deploy` with output to FILES, not pipes.

        `deploy` spawns a long-lived passive service that inherits the parent's
        stdout/stderr. With capture_output=True (pipes) the pipe never reaches
        EOF while that daemon lives, so subprocess.run hangs (a POSIX-only
        footgun -- Windows detaches the child from the pipes). Redirecting to
        temp files makes subprocess.run wait only for the deploy PROCESS to exit.
        """
        out = tempfile.TemporaryFile(mode="w+")
        err = tempfile.TemporaryFile(mode="w+")
        try:
            p = subprocess.run(
                [self.python, "-m", "agent_index", "deploy", *extra],
                env=self.env, stdout=out, stderr=err, timeout=timeout,
            )
            out.seek(0)
            err.seek(0)
            return types.SimpleNamespace(returncode=p.returncode, stdout=out.read(), stderr=err.read())
        finally:
            out.close()
            err.close()

    def spawn_serve(self):
        kw = {}
        if sys.platform == "win32":
            kw["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
        else:
            kw["start_new_session"] = True
        return subprocess.Popen(
            [self.python, "-m", "agent_index", "start"], env=self.env, **kw
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

    def py(self, snippet, timeout=60):
        """Run a short snippet in the plugin's venv (for TaskStore access)."""
        return subprocess.run(
            [self.python, "-c", snippet], env=self.env,
            capture_output=True, text=True, timeout=timeout,
        )

    def enqueue(self):
        """Create a durable QUEUED task straight in the TaskStore (no lancedb).

        Prints the new task id on stdout. Mirrors exactly what POST /reindex
        persists, minus the optional-dependency gate.
        """
        snip = (
            "from agent_index.indexing.task_store import TaskStore;"
            "from agent_index.config import data_dir;"
            "t=TaskStore(data_dir()/'tasks.db').enqueue(source='cvp-probe', full=False, trigger_source='clean-room:cutover-probe');"
            "print(t.id)"
        )
        r = self.py(snip)
        return (r.stdout or "").strip(), r

    def task_present(self, tid):
        """True iff the task id still exists anywhere in the durable queue DB."""
        snip = (
            "import json;"
            "from agent_index.indexing.task_store import TaskStore;"
            "from agent_index.config import data_dir;"
            "s=TaskStore(data_dir()/'tasks.db').get_all_tasks();"
            "ids=[t['id'] for t in s['queued']]+([s['active']['id']] if s['active'] else [])+[t['id'] for t in s['history']];"
            "print(json.dumps(ids))"
        )
        r = self.py(snip)
        try:
            return tid in json.loads((r.stdout or "").strip())
        except Exception:
            return False


def _health(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as r:  # noqa: S310 loopback
            return json.loads(r.read().decode())
    except Exception as e:
        return {"_err": str(e)}


def _post(port, path, body=b"{}", timeout=6):
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method="POST", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 loopback
            return json.loads(r.read().decode())
    except Exception as e:
        return {"_err": str(e)}


def _reindex(port):
    return _post(port, "/reindex", body=b'{"source":"cvp-gate"}')


def _listening(port):
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", int(port))) == 0


def _read_active_via_lib(python, env):
    """Resolve the live endpoint the way a client does (zdd routing table)."""
    snip = (
        "from zdd.routing import read_active_endpoint;"
        "from agent_index.config import routing_dir;"
        "e=read_active_endpoint(routing_dir());"
        "print(e.port if e else '')"
    )
    r = subprocess.run([python, "-c", snip], env=env, capture_output=True, text=True, timeout=30)
    return (r.stdout or "").strip()


def _breadcrumb_path(python, env):
    snip = (
        "from zdd.breadcrumb import breadcrumb_path;"
        "from agent_index.config import routing_dir;"
        "print(breadcrumb_path(routing_dir()))"
    )
    r = subprocess.run([python, "-c", snip], env=env, capture_output=True, text=True, timeout=30)
    return (r.stdout or "").strip()


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
        if self.ok:
            summary = "; ".join(self.detail[-3:])
        else:
            summary = "; ".join(d for d in self.detail if d.startswith("FAILED"))
        print(f"PROBE: {self.name} {status} {summary}")
        return self.ok


# --------------------------------------------------------------------------


def check_survive_queued_batch(python):
    r = Result("survive-queued-batch")
    home = tempfile.mkdtemp(prefix="aicv-sq-")
    c = Ctx(python, home)
    proc = None
    try:
        proc = c.spawn_serve()
        a = c.active()
        if not r.check(a is not None, "initial service published routing"):
            return r
        old = a["port"]
        tid, er = c.enqueue()
        if not r.check(bool(tid), f"enqueued a durable index task (id={tid or '?'}; err={er.stderr.strip()[:120] if er else ''})"):
            return r
        r.check(c.task_present(tid), "task present in durable queue before cutover")
        cut = c.deploy("--json", "--health-timeout", "30", "--drain-timeout", "10")
        r.check(cut.returncode == 0, f"deploy (zdd cutover) rc==0 (rc={cut.returncode}; {cut.stderr.strip()[:160]})")
        a2 = c.active()
        new = a2["port"] if a2 else None
        r.check(new is not None and new != old, f"routing flipped {old} -> {new}")
        time.sleep(1.5)
        r.check(not _listening(old), f"old service :{old} retired (not listening)")
        r.check(bool(new) and _listening(new), f"new service :{new} healthy")
        r.check(c.task_present(tid), "queued index task SURVIVED the cutover (durable tasks.db re-adopted)")
        resolved = _read_active_via_lib(python, c.env)
        r.check(str(resolved) == str(new), f"client resolves the new service via routing table (:{resolved})")
        if new:
            _post(new, "/shutdown")
        return r
    finally:
        try:
            if proc:
                proc.terminate()
        except Exception:
            pass
        time.sleep(0.5)
        shutil.rmtree(home, ignore_errors=True)


def check_drain_gate(python):
    r = Result("drain-gate")
    home = tempfile.mkdtemp(prefix="aicv-dg-")
    c = Ctx(python, home)
    proc = None
    try:
        proc = c.spawn_serve()
        a = c.active()
        if not r.check(a is not None, "service up"):
            return r
        old = a["port"]
        _post(old, "/drain", body=b'{"timeout":1,"poll":0.05}')
        r.check(_health(old).get("status") == "draining", "service is draining after /drain (closed to new work)")
        r.check(_reindex(old).get("accepted") is False, "/reindex REFUSED while draining")
        cut = c.deploy("--json", "--health-timeout", "30", "--drain-timeout", "10")
        r.check(cut.returncode == 0, f"deploy rc==0 from a drained service (rc={cut.returncode})")
        a2 = c.active()
        new = a2["port"] if a2 else None
        r.check(new is not None and new != old, f"routing flipped {old} -> {new}")
        time.sleep(1.0)
        r.check(bool(new) and _health(new).get("status") == "ok", "cutover yielded a FRESH, non-draining service")
        r.check(_reindex(new).get("accepted") is True, "new service ACCEPTS work again (gate reset)")
        if new:
            _post(new, "/shutdown")
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
    home = tempfile.mkdtemp(prefix="aicv-bc-")
    c = Ctx(python, home)
    proc = None
    try:
        proc = c.spawn_serve()
        a = c.active()
        if not r.check(a is not None, "service up"):
            return r
        port = a["port"]
        # Simulate an ABORTED cutover: drain the live service (drained-but-alive =
        # a "stranded survivor") and drop the breadcrumb naming it, exactly as an
        # orchestrator killed mid-cutover would leave behind.
        _post(port, "/drain", body=b'{"timeout":1,"poll":0.05}')
        r.check(_health(port).get("status") == "draining", "survivor is drained (closed to new work)")
        bcpath = _breadcrumb_path(python, c.env)
        bc = {
            "state": "draining",
            "old": {"bind": a.get("bind", "127.0.0.1"), "port": port},
            "new_port": None,
            "started_at": "2026-01-01T00:00:00+00:00",
        }
        r.check(bool(bcpath), "resolved the zdd breadcrumb path")
        open(bcpath, "w", encoding="utf-8").write(json.dumps(bc))
        r.check(os.path.exists(bcpath), "aborted-cutover breadcrumb written")
        rec = c.deploy("--recover", "--json")
        r.check(rec.returncode == 0, f"deploy --recover rc==0 (rc={rec.returncode}; {rec.stderr.strip()[:160]})")
        time.sleep(0.5)
        r.check(_health(port).get("status") == "ok",
                "stranded survivor UNDRAINED by recovery (open to new work again)")
        _post(port, "/shutdown")
        return r
    finally:
        try:
            if proc:
                proc.terminate()
        except Exception:
            pass
        time.sleep(0.5)
        shutil.rmtree(home, ignore_errors=True)


CHECKS = {
    "survive-queued-batch": check_survive_queued_batch,
    "drain-gate": check_drain_gate,
    "breadcrumb-recover": check_breadcrumb_recover,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--python", required=True, help="the installed agent-index service venv python")
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
