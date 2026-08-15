#!/usr/bin/env python3
"""Portable, stdlib-only cutover-resilience probe for agent-bridge.

Drives the OS-agnostic zdd graceful-cutover mechanism against a REAL installed
agent-bridge daemon, in an isolated config dir, and asserts the binding invariant
of the correct-install-flows effort (dotfiles#1393): *a version cutover must never
kill in-flight, non-resumable work*. agent-bridge stands a new daemon up beside the
old (``start --passive`` on a fresh port), health-gates it, flips the routing table
(``active.json``), drains the old at the TURN boundary, and retires it -- so a live
interactive session is not hard-killed and clients follow the routing flip.

It is the reusable core of the clean-room ``agent-bridge-cutover`` scenario: the
thin scenario.sh installs + provisions the plugin on a fresh box and runs this
probe, so the thorny orchestration is verifiable independently of Docker (it runs
on any OS with a built agent-bridge venv).

FIDELITY NOTE (honest scope). A *fully live* "session turn survives the flip"
assertion needs a real model/ACP child (agent-bridge cancels an in-flight turn
COOPERATIVELY with resume-on-reattach, then the session host re-adopts) -- that is
a Tier-E, model-in-the-loop concern, not stdlib-simulatable. This Tier-P probe
therefore proves the cutover MECHANISM the turn-survival guarantee is built on:
the routing active/passive flip + old-daemon retirement, the drain GATE (the turn
boundary at which in-flight work is waited on, not hard-killed), and cooperative
recovery of an aborted cutover. The live-turn survival itself is asserted by the
Tier-E agent-bridge eval, not here.

Checks (each prints ``PROBE: <name> PASS|FAIL <detail>``):
  routing-flip-retire  `deploy` stands a new daemon up beside the old, flips the
                       routing table, and retires the old -- clients resolve the
                       new daemon (beside-not-in-place; nothing hard-killed)
  drain-gate           `drain` opens the gate (/health -> draining: the turn
                       boundary at which new work is refused and in-flight work is
                       waited on); `undrain` releases it
  breadcrumb-recover   an aborted cutover strands a DRAINED survivor; recovery
                       (`deploy --recover`) undrains it (not stuck closed)

Usage:
    python cutover_probe.py --python <agent_bridge-venv-python> [--checks a,b]

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

ALL_CHECKS = ["routing-flip-retire", "drain-gate", "breadcrumb-recover"]


class Ctx:
    def __init__(self, python: str, cfgdir: str):
        self.python = python
        # Full isolation: agent-bridge's sessions.db defaults to
        # ``~/.agent-bridge/sessions.db`` (home-based, NOT AGENT_BRIDGE_CONFIG_DIR)
        # and agent discovery reads ``~/.agent-worktrees/projects.yaml`` -- so we
        # relocate HOME/USERPROFILE into the sandbox and point the config dir +
        # projects.yaml there too, to never touch a live daemon's state.
        self.env = dict(os.environ)
        cfg = os.path.join(cfgdir, ".agent-bridge")
        os.makedirs(cfg, exist_ok=True)
        self.env.update(
            HOME=cfgdir,
            USERPROFILE=cfgdir,
            AGENT_BRIDGE_CONFIG_DIR=cfg,
            AGENT_WORKTREES_PROJECTS_YAML=os.path.join(cfgdir, "no-projects.yaml"),
            PYTHONUTF8="1",
        )
        for k in ("AGENT_BRIDGE_BASE_URL", "AGENT_BRIDGE_NO_ROUTING_TABLE",
                  "AGENT_BRIDGE_DYNAMIC_PORT"):
            # NB: do NOT set AGENT_BRIDGE_DYNAMIC_PORT=1 -- it forces a dynamic port
            # *even when one is pinned*, which makes the deploy orchestrator's
            # passive daemon ignore the specific --port it was assigned, so the
            # orchestrator health-checks the wrong port and the cutover rolls back.
            # We get an ephemeral active port the safe way instead: `start --port 0`.
            self.env.pop(k, None)
        self.cfgdir = cfg
        self.root = cfg
        self._token = None

    def cli(self, *args, timeout=90):
        return subprocess.run(
            [self.python, "-m", "agent_bridge", *args],
            env=self.env, capture_output=True, text=True, timeout=timeout,
        )

    def deploy(self, *extra, timeout=180):
        """Run `deploy` with output to FILES, not pipes.

        `deploy` spawns a long-lived passive daemon that inherits the parent's
        stdout/stderr. With capture_output=True (pipes) the pipe never reaches
        EOF while that daemon lives, so subprocess.run hangs (a POSIX-only
        footgun -- Windows detaches the child from the pipes). Redirecting to
        temp files makes subprocess.run wait only for the deploy PROCESS to exit.
        """
        out = tempfile.TemporaryFile(mode="w+")
        err = tempfile.TemporaryFile(mode="w+")
        try:
            p = subprocess.run(
                [self.python, "-m", "agent_bridge", "deploy", *extra],
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
            [self.python, "-m", "agent_bridge", "start", "--port", "0", "--bind", "127.0.0.1"],
            env=self.env, **kw
        )

    def token(self):
        if self._token is None:
            try:
                self._token = (self.cli("token").stdout or "").strip()
            except Exception:
                self._token = ""
        return self._token

    def active(self, tries=100):
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

    def health(self, port):
        headers = {}
        tok = self.token()
        if tok:
            headers["Authorization"] = "Bearer " + tok
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{port}/health", headers=headers)
            with urllib.request.urlopen(req, timeout=3) as r:  # noqa: S310 loopback
                return json.loads(r.read().decode())
        except Exception as e:
            return {"_err": str(e)}


def _listening(port):
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", int(port))) == 0


def _read_active_via_lib(python, env):
    """Resolve the live endpoint the way a client does (zdd routing table)."""
    snip = (
        "from zdd.routing import read_active_endpoint;"
        "from agent_bridge.config import config_dir;"
        "e=read_active_endpoint(config_dir());"
        "print(e.port if e else '')"
    )
    r = subprocess.run([python, "-c", snip], env=env, capture_output=True, text=True, timeout=30)
    return (r.stdout or "").strip()


def _breadcrumb_path(python, env):
    snip = (
        "from zdd.breadcrumb import breadcrumb_path;"
        "from agent_bridge.config import config_dir;"
        "print(breadcrumb_path(config_dir()))"
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


def check_routing_flip_retire(python):
    r = Result("routing-flip-retire")
    cfg = tempfile.mkdtemp(prefix="abcv-rf-")
    c = Ctx(python, cfg)
    proc = None
    try:
        proc = c.spawn_serve()
        a = c.active()
        if not r.check(a is not None, "initial daemon published routing"):
            return r
        old = a["port"]
        r.check(_listening(old), f"active daemon listening on :{old}")
        cut = c.deploy("--json", "--health-timeout", "30", "--drain-timeout", "10")
        r.check(cut.returncode == 0, f"deploy (zdd cutover) rc==0 (rc={cut.returncode}; {cut.stderr.strip()[:160]})")
        a2 = c.active()
        new = a2["port"] if a2 else None
        r.check(new is not None and new != old, f"new daemon stood up beside the old; routing flipped {old} -> {new}")
        time.sleep(1.5)
        r.check(not _listening(old), f"old daemon :{old} retired (not listening)")
        r.check(bool(new) and _listening(new), f"new daemon :{new} healthy")
        resolved = _read_active_via_lib(python, c.env)
        r.check(str(resolved) == str(new), f"client resolves the new daemon via routing table (:{resolved})")
        return r
    finally:
        try:
            a3 = c.active(tries=1)
            if a3 and a3.get("port"):
                c.cli("undrain")
        except Exception:
            pass
        try:
            if proc:
                proc.terminate()
        except Exception:
            pass
        time.sleep(0.5)
        shutil.rmtree(cfg, ignore_errors=True)


def check_drain_gate(python):
    r = Result("drain-gate")
    cfg = tempfile.mkdtemp(prefix="abcv-dg-")
    c = Ctx(python, cfg)
    proc = None
    try:
        proc = c.spawn_serve()
        a = c.active()
        if not r.check(a is not None, "daemon up"):
            return r
        port = a["port"]
        d = c.cli("drain", "--timeout", "2")
        r.check(d.returncode == 0, f"drain command rc==0 (rc={d.returncode}; {d.stderr.strip()[:160]})")
        r.check(c.health(port).get("draining") is True, "/health reports draining after `drain` (turn-boundary gate open)")
        u = c.cli("undrain")
        r.check(u.returncode == 0, f"undrain command rc==0 (rc={u.returncode})")
        time.sleep(0.4)
        r.check(c.health(port).get("draining") is False, "gate released after `undrain` (accepting new work again)")
        return r
    finally:
        try:
            if proc:
                proc.terminate()
        except Exception:
            pass
        time.sleep(0.5)
        shutil.rmtree(cfg, ignore_errors=True)


def check_breadcrumb_recover(python):
    r = Result("breadcrumb-recover")
    cfg = tempfile.mkdtemp(prefix="abcv-bc-")
    c = Ctx(python, cfg)
    proc = None
    try:
        proc = c.spawn_serve()
        a = c.active()
        if not r.check(a is not None, "daemon up"):
            return r
        port = a["port"]
        # Simulate an ABORTED cutover: open the drain gate on the live daemon (now
        # drained-but-alive = a "stranded survivor") and drop the breadcrumb naming
        # it, exactly as an orchestrator killed mid-cutover would leave behind.
        d = c.cli("drain", "--timeout", "2")
        r.check(d.returncode == 0, "survivor drained (aborted-cutover state)")
        r.check(c.health(port).get("draining") is True, "survivor is drained (closed to new work)")
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
        r.check(c.health(port).get("draining") is False,
                "stranded survivor UNDRAINED by recovery (open to new work again)")
        return r
    finally:
        try:
            c.cli("undrain")
        except Exception:
            pass
        try:
            if proc:
                proc.terminate()
        except Exception:
            pass
        time.sleep(0.5)
        shutil.rmtree(cfg, ignore_errors=True)


CHECKS = {
    "routing-flip-retire": check_routing_flip_retire,
    "drain-gate": check_drain_gate,
    "breadcrumb-recover": check_breadcrumb_recover,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--python", required=True, help="the installed agent-bridge venv python")
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
