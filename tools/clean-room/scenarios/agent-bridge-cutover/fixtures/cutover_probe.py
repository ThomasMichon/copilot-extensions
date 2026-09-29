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
  abrupt-kill-recovery a generation is SIGKILLed with no graceful shutdown --
                       never releasing a session-host claim it held (the exit
                       contract's own release path, #4543, never runs) -- and a
                       fresh generation started afterward still comes up clean
                       and does not sit stuck behind the dead generation's stale
                       claim (agent-bridge-unified-zdd-cutover Phase 5)

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

ALL_CHECKS = [
    "routing-flip-retire", "drain-gate", "breadcrumb-recover",
    "abrupt-kill-recovery",
]


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


def check_abrupt_kill_recovery(python):
    """A generation is SIGKILLed before it can run its own exit contract (the
    ``/api/v1/shutdown`` release-every-claim path, #4543) -- the "kill the old
    generation before it releases its claims" drill this effort's Phase 5
    calls for. A stale session-host claim it never released must not wedge a
    later generation forever: ``zdd.claims.is_recoverable`` treats a claim
    whose owner pid is dead as free for the taking, no live handshake
    required (Phase 2's contract) -- this proves that holds for a REAL killed
    daemon process (its actual own pid, actually SIGKILLed) recovered by a
    REAL fresh daemon's own real startup reattach scan, not just the
    pure-function unit tests in ``libs/zdd/tests/test_claims.py``.

    Two synchronization hazards a naive version of this check gets wrong
    (both review-caught in earlier revisions -- kept documented here so they
    are not reintroduced):
      1. The daemon's own ONE-SHOT startup reattach scan (a background task,
         never periodic -- ``app.py``'s ``_reattach_session_hosts_bg``) would
         reap an orphan record (no adoptable DB session) the instant it
         runs, whether the record predates startup or arrives moments after
         -- so a fixed sleep before registering the test record is a race,
         not a real synchronization point (the scan may spend up to its own
         remote-recovery budget). A **sentinel** orphan record is seeded
         *before* the daemon starts and polled until removed -- its removal
         is the daemon's own real scan having actually run to completion --
         before the real test record is registered.
      2. The record's *recovery* must be observed as done BY THE REAL FRESH
         DAEMON's own background task, not by a disconnected direct
         ``HostIndex.claim()`` call standing in for "a new generation" --
         otherwise the check can pass while the real daemon's own recovery
         path silently does nothing. This fixture creates no adoptable DB
         session, so the production path's only valid terminal outcome is a
         full reap (claim, then remove -- "no adoptable session on
         reattach"); this check polls the on-disk index after the fresh
         daemon starts and requires the record to be **gone**, never
         "claimed under the fresh daemon's pid" as an alternate pass --
         that would let the check observe the intermediate claim write and
         pass even if the record's subsequent removal then silently fails
         (removal is itself best-effort/suppressed in production).
    """
    r = Result("abrupt-kill-recovery")
    cfg = tempfile.mkdtemp(prefix="abcv-ak-")
    c = Ctx(python, cfg)
    proc = None
    proc2 = None
    alive_dummy = None
    sentinel_dummy = None
    session_id = "abrupt-kill-sim-session"
    sentinel_id = "abrupt-kill-sim-sentinel"
    index_path = os.path.join(c.root, "hosts", "index.json")

    def _run_snip(snip, timeout=30):
        return subprocess.run([python, "-c", snip], env=c.env,
                               capture_output=True, text=True, timeout=timeout)

    def _index_get(sid):
        """Read one record's (owner_generation, owner_pid) via the real
        HostIndex class, or None if absent/unreadable."""
        snip = (
            "from agent_bridge.session_host.host_index import HostIndex\n"
            f"idx = HostIndex({index_path!r})\n"
            f"rec = idx.get({sid!r})\n"
            "print((rec.owner_generation, rec.owner_pid) if rec else 'GONE')\n"
        )
        out = _run_snip(snip)
        if out.returncode != 0:
            return None
        text = out.stdout.strip()
        if text == "GONE" or not text:
            return None
        try:
            return eval(text, {"__builtins__": {}})  # noqa: S307 -- our own tuple literal
        except Exception:
            return None

    try:
        # A disposable dummy standing in for the sentinel's session-host
        # child. Reaping a dead/orphan record doesn't just drop the index
        # entry -- it force-kills the record's own `host_pid`/`child_pid`
        # too (`session_host_connection.py`), so this process is expected to
        # die as part of the sentinel being reaped and must NEVER be reused
        # for the real test record below (a real bug an earlier revision
        # had: reusing it meant the "real" record's host was already dead
        # before the ownership contract was even exercised).
        sentinel_dummy = subprocess.Popen([python, "-c", "import time; time.sleep(300)"])

        # Seed the sentinel BEFORE the daemon starts -- an orphan (no
        # adoptable DB session) the daemon's own one-shot startup scan is
        # guaranteed to remove.
        seed_sentinel = (
            "from agent_bridge.session_host.host_index import HostIndex, HostRecord\n"
            f"idx = HostIndex({index_path!r})\n"
            "idx.register(HostRecord("
            f"session_id={sentinel_id!r}, port=1, host_pid={sentinel_dummy.pid}, "
            f"child_pid={sentinel_dummy.pid}))\n"
        )
        seed = _run_snip(seed_sentinel)
        r.check(seed.returncode == 0,
                f"seeded a sentinel orphan record before daemon startup "
                f"(rc={seed.returncode}; {seed.stderr.strip()[:160]})")

        proc = c.spawn_serve()
        a = c.active()
        if not r.check(a is not None, "generation-to-be-killed's daemon is up"):
            return r
        old_port = a["port"]
        r.check(_listening(old_port), f"generation to be killed is listening on :{old_port}")

        # Poll until the sentinel is gone -- the real, observable signal
        # that this generation's one-shot startup reattach scan has actually
        # run to completion (never a fixed sleep guess).
        sentinel_gone = False
        deadline = time.monotonic() + 60.0
        while time.monotonic() < deadline:
            if _index_get(sentinel_id) is None:
                sentinel_gone = True
                break
            time.sleep(0.25)
        if not r.check(sentinel_gone, "sentinel orphan reaped -- one-shot startup scan has run"):
            return r

        # A FRESH, still-alive dummy for the real test record -- never the
        # sentinel's own dummy, which the reap above already force-killed.
        alive_dummy = subprocess.Popen([python, "-c", "import time; time.sleep(300)"])

        # Only now register the real test record -- nothing else ever
        # revisits it for the rest of this generation's life (the scan
        # never repeats), so it durably survives until we claim it below.
        register_snip = (
            "from agent_bridge.session_host.host_index import HostIndex, HostRecord\n"
            f"idx = HostIndex({index_path!r})\n"
            "idx.register(HostRecord("
            f"session_id={session_id!r}, port=59999, host_pid={alive_dummy.pid}, "
            f"child_pid={alive_dummy.pid}))\n"
        )
        reg = _run_snip(register_snip)
        r.check(reg.returncode == 0,
                f"registered an unclaimed session-host record "
                f"(rc={reg.returncode}; {reg.stderr.strip()[:160]})")

        # This generation claims the record for itself, using its OWN real,
        # live pid as owner_pid -- exactly what `_claim_host_record` does
        # inside the real daemon (invoked directly here since making the
        # real daemon discover and claim an ad hoc record with no live
        # session-host child of its own needs a full session-host
        # implementation, out of reach for a stdlib-only probe).
        claim1_snip = (
            "from agent_bridge.session_host.host_index import HostIndex\n"
            "from agent_bridge.session_host.osutil import pid_alive\n"
            f"idx = HostIndex({index_path!r})\n"
            f"idx.claim({session_id!r}, generation='real-gen-1', "
            f"owner_pid={proc.pid}, pid_alive=pid_alive)\n"
            f"print(idx.get({session_id!r}).owner_generation)\n"
        )
        claim1 = _run_snip(claim1_snip)
        r.check(
            claim1.returncode == 0 and claim1.stdout.strip() == "real-gen-1",
            f"the live generation (real pid {proc.pid}) claimed the record for "
            f"itself (rc={claim1.returncode}, stdout={claim1.stdout.strip()!r}, "
            f"stderr={claim1.stderr.strip()[:160]})",
        )
        before = _index_get(session_id)
        r.check(
            before is not None and before[1] == proc.pid,
            f"claim durably recorded owner_pid={proc.pid} before the kill "
            f"(read back {before!r})",
        )

        # The abrupt kill itself: SIGKILL, no drain, no /api/v1/shutdown
        # handshake -- the exit contract's own release_all() never runs, so
        # the claim above is durably stranded exactly as recorded.
        proc.kill()
        proc.wait(timeout=10)
        r.check(not _listening(old_port), f"killed generation's :{old_port} is gone (no graceful exit ran)")

        # A real fresh daemon -- its own startup reattach scan (the SAME
        # production code path, not a synthetic stand-in) must recover this
        # record. This fixture creates no adoptable session, so the only
        # valid outcome is a full reap (claim, then remove); see the
        # dedicated check below for why "still claimed under proc2" is
        # deliberately NOT accepted as an alternate pass.
        proc2 = c.spawn_serve()
        a2 = None
        deadline = time.monotonic() + 25.0
        while time.monotonic() < deadline:
            candidate = c.active(tries=1)
            if candidate and candidate.get("pid") == proc2.pid:
                a2 = candidate
                break
            time.sleep(0.25)
        r.check(
            a2 is not None,
            "a real fresh daemon (its own real pid) starts cleanly after the "
            "abrupt kill and publishes its own active endpoint -- no manual "
            "fix needed",
        )

        # This fixture deliberately creates no adoptable DB session, so the
        # production path's ONLY valid terminal outcome is reap (claim, then
        # remove -- `session_host_recovery.py`'s "no adoptable session on
        # reattach" branch); it never legitimately stays claimed under this
        # fresh daemon's own pid. Accepting `owner_pid == proc2.pid` as an
        # alternate pass would let the check observe the intermediate claim
        # write and pass even if the subsequent removal then silently fails
        # (that removal is itself best-effort/suppressed in production) --
        # require absence, not "claimed OR reaped".
        recovered = False
        after = None
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            after = _index_get(session_id)
            if after is None:
                recovered = True
                break
            time.sleep(0.25)
        r.check(
            recovered,
            "the fresh daemon's OWN real startup reattach scan claimed then "
            f"reaped the stale claim (record now {after!r}) -- never left "
            f"wedged under killed pid {proc.pid}, and not silently retained "
            "under the fresh daemon either (this fixture has no adoptable "
            "session, so a lingering claim would itself be a bug)",
        )
        return r
    finally:
        for p in (proc, proc2, alive_dummy, sentinel_dummy):
            try:
                if p is not None and p.poll() is None:
                    p.terminate()
            except Exception:
                pass
        time.sleep(0.5)
        shutil.rmtree(cfg, ignore_errors=True)


CHECKS = {
    "routing-flip-retire": check_routing_flip_retire,
    "drain-gate": check_drain_gate,
    "breadcrumb-recover": check_breadcrumb_recover,
    "abrupt-kill-recovery": check_abrupt_kill_recovery,
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
