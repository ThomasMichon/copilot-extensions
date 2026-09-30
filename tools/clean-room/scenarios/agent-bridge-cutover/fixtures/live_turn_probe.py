#!/usr/bin/env python3
"""Phase 6 (agent-bridge-unified-zdd-cutover) live-turn-survival drill.

Docker-only, opt-in, credits-consuming.

Closes Phase 5's deferred Plan item 1 of the ``agent-bridge-unified-zdd-cutover``
effort: prove, with a genuinely LIVE Copilot/ACP turn in flight (real model
calls, real credits -- NOT the stdlib-simulated stand-in ``cutover_probe.py``
uses for its other checks), that ``agent-bridge deploy`` does not disrupt it
while the daemon fully changes generation underneath it.

**Why this is a separate fixture, not another ``cutover_probe.py`` check.**
Every check in that sibling module is deliberately stdlib-only and fully
isolates itself into a throwaway ``HOME``/``AGENT_BRIDGE_CONFIG_DIR`` sandbox,
so it never touches a real Copilot auth context and is safe to run anywhere
(even off-Docker). This drill is the opposite on both counts: it needs a REAL,
already-authenticated ``copilot`` CLI (the box's own real ``~/.copilot``, set
up by the harness's normal auth-inject step) and it deliberately runs against
the box's REAL, already-provisioned ``agent-bridge`` install (the one
``scenario.sh`` phases 1/2 just built) rather than a fresh sandbox -- a
throwaway ``HOME`` would have no Copilot credentials in it, which would make
every real model call in this drill fail closed, and there is no other
concurrent daemon to protect in a disposable clean-room container. This is
what makes the drill Docker-only: running it against a real workstation's real
``~/.agent-bridge``/``~/.copilot`` would risk disrupting whatever the operator
is legitimately using agent-bridge for on that machine right now.

**Topology (see the design doc for the full derivation).** A plain
``command``-registered Tier-E provider (``bridge_register.py``) bypasses the
Session-Host path entirely and would prove nothing about reattachment. The
correct shape is a **local** target: register one project in
``~/.agent-worktrees/projects.yaml`` (agent-bridge's ``discover_local_agents``
auto-discovery) so ``agent-bridge create <name> --target-dir <repo>`` resolves
to ``SpawnTarget(type="local", ...)`` -- the only shape that spawns a real
Session-Host child and durably registers it in ``HostIndex``.

**What actually gets asserted (programmatic, not an LLM judge -- see the design
doc's own §"why this doesn't fit the harness's standard Tier-E shape").
Reading `hosts/index.json` records directly (rather than re-deriving the
frontend's own private ``_generation_id`` computation, which is intentionally
not reproducible from outside the process -- see Phase 5's own honest-scope
note) is enough to prove a REAL reattach, not a no-op:
  1. The session's ``acp_session_id`` (hence its ``events.jsonl`` transcript)
     never changes across the boundary -- the SAME session-host child, not a
     respawned one.
  2. The daemon generation genuinely changed: the old daemon's port stops
     listening, the new one's is different, and the HostIndex record's
     ``owner_pid`` for this session moves from the old daemon's real pid to
     the new daemon's real pid (never merely re-derived/assumed).
  3. The events.jsonl snapshot taken the instant ``deploy`` was fired is an
     exact PREFIX of the final snapshot -- no line already written before the
     cutover was lost, truncated, or mutated.
  4. Exactly one ``assistant.turn_end`` for the one prompt we sent (no
     duplicate ``session.start``/turn indicating the child was restarted, not
     reattached).
  5. The bridge's own caller-facing ``wait --attention turn_complete`` (the
     SAME channel a real caller uses) settles cleanly across the boundary --
     this is the actual continuity guarantee callers depend on, not merely an
     internal bookkeeping fact.

Usage:
    python live_turn_probe.py --python <agent-bridge-venv-python> \\
        --repo <local-repo-dir> [--project-name live-turn-target] \\
        [--turn-timeout 600]

Exit 0 iff the drill PASSes. Prints ``PROBE: live-turn-survival PASS|FAIL
<detail>`` in the same shape ``cutover_probe.py`` uses, so ``scenario.sh`` can
parse it identically.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time

DEFAULT_PROMPT = (
    "Write a five-paragraph short story about a cave explorer, one paragraph "
    "at a time. After drafting each paragraph, append it to a file named "
    "progress.txt in the current directory using a shell command, then run "
    "`sleep 6` before starting the next paragraph. Do this for all five "
    "paragraphs in order, then reply with exactly the single word: DONE."
)


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
        summary = "; ".join(
            self.detail[-3:] if self.ok else [d for d in self.detail if d.startswith("FAILED")]
        )
        print(f"PROBE: {self.name} {status} {summary}")
        return self.ok


def _run(python: str, *args, timeout=120):
    return subprocess.run(
        [python, "-m", "agent_bridge", *args],
        capture_output=True, text=True, timeout=timeout,
    )


def _run_snip(python: str, snip: str, timeout=30):
    return subprocess.run([python, "-c", snip], capture_output=True, text=True, timeout=timeout)


def _config_dir(python: str) -> str:
    out = _run_snip(python, "from agent_bridge.config import config_dir; print(config_dir())")
    return out.stdout.strip()


def _listening(port) -> bool:
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", int(port))) == 0


def _active(config_dir: str, tries=100):
    path = os.path.join(config_dir, "active.json")
    for _ in range(tries):
        try:
            a = json.loads(open(path, encoding="utf-8").read()).get("active")
            if a and a.get("port"):
                return a
        except Exception:
            pass
        time.sleep(0.25)
    return None


class _IndexReadError(RuntimeError):
    """A read attempt failed or was unparseable -- never treated as absence."""


def _host_record(python: str, config_dir: str, session_id: str):
    """Read back ``(owner_pid, owner_generation)`` for one session's
    HostIndex record. Note ``HostRecord`` carries transport addressing only
    (session_id/port/host_pid/child_pid/owner_*) -- it has no
    ``acp_session_id`` field; that lives in the frontend's own sessions.db
    and is read separately via ``_get_session``.
    """
    index_path = os.path.join(config_dir, "hosts", "index.json")
    snip = (
        "from agent_bridge.session_host.host_index import HostIndex\n"
        f"idx = HostIndex({index_path!r})\n"
        "idx._load_or_raise()\n"
        f"rec = idx._records.get({session_id!r})\n"
        "print((rec.owner_pid, rec.owner_generation) if rec else 'GONE')\n"
    )
    out = _run_snip(python, snip)
    if out.returncode != 0:
        raise _IndexReadError(f"index read failed (rc={out.returncode}): {out.stderr.strip()[:200]}")
    text = out.stdout.strip()
    if text == "GONE":
        return None
    if not text:
        raise _IndexReadError("index read produced no output")
    try:
        return eval(text, {"__builtins__": {}})  # noqa: S307 -- our own tuple literal
    except Exception as exc:
        raise _IndexReadError(f"unparseable index read output {text!r}") from exc


def _get_session(python: str, session_id: str) -> dict | None:
    out = _run(python, "sessions", "--json")
    if out.returncode != 0:
        return None
    try:
        sessions = json.loads(out.stdout)
    except Exception:
        return None
    for s in sessions:
        if s.get("session_id") == session_id:
            return s
    return None


def _events_path(acp_session_id: str) -> str:
    return os.path.join(
        os.path.expanduser("~"), ".copilot", "session-state", acp_session_id, "events.jsonl",
    )


def _read_events(path: str) -> list[str]:
    try:
        with open(path, encoding="utf-8") as f:
            return f.readlines()
    except FileNotFoundError:
        return []


def _count_type(lines: list[str], type_name: str) -> int:
    n = 0
    for line in lines:
        try:
            if json.loads(line).get("type") == type_name:
                n += 1
        except Exception:
            continue
    return n


def run(python: str, repo: str, project_name: str, turn_timeout: float) -> Result:
    r = Result("live-turn-survival")
    cfg_dir = _config_dir(python)
    r.check(bool(cfg_dir), f"resolved agent-bridge config dir ({cfg_dir!r})")

    projects_yaml = os.path.join(os.path.expanduser("~"), ".agent-worktrees", "projects.yaml")
    os.makedirs(os.path.dirname(projects_yaml), exist_ok=True)
    with open(projects_yaml, "w", encoding="utf-8") as f:
        f.write(f"projects:\n  {project_name}:\n    anchor: {repo!r}\n    expose_agent: true\n")
    r.check(os.path.exists(projects_yaml), f"registered local project {project_name!r} -> {repo}")

    # Reuse an already-running daemon (matches production reality -- a real
    # box will very likely already have one from an earlier session/first
    # use) rather than assuming a clean slate.
    proc1 = None
    a1 = _active(cfg_dir, tries=1)
    if a1 is None:
        proc1 = subprocess.Popen(
            [python, "-m", "agent_bridge", "start", "--port", "0", "--bind", "127.0.0.1"],
            start_new_session=True,
        )
        a1 = _active(cfg_dir)
    if not r.check(a1 is not None, "a daemon (generation 1) is up and has published routing"):
        return r
    old_port = a1["port"]
    old_pid = a1.get("pid")
    r.check(_listening(old_port), f"generation 1 listening on :{old_port} (pid {old_pid})")

    session_id_file = tempfile.mktemp(prefix="abv-live-turn-sid-")
    session_id = ""
    try:
        create = _run(
            python, "create", project_name, "--target-dir", repo,
            "--no-wait", "--session-id-file", session_id_file, DEFAULT_PROMPT,
            timeout=60,
        )
        r.check(create.returncode == 0, f"create --no-wait rc==0 (rc={create.returncode}; {create.stderr.strip()[:200]})")
        if os.path.exists(session_id_file):
            session_id = open(session_id_file, encoding="utf-8").read().strip()
        if not r.check(bool(session_id), "wrote a session id via --session-id-file"):
            return r

        # Wait for the child to genuinely register a Session-Host claim
        # before firing the cutover -- a bounded poll on real state, never a
        # fixed sleep guess.
        rec = None
        deadline = time.monotonic() + 60.0
        while time.monotonic() < deadline:
            try:
                rec = _host_record(python, cfg_dir, session_id)
            except _IndexReadError:
                rec = None
            if rec is not None:
                break
            time.sleep(0.5)
        if not r.check(rec is not None, "session registered a real Session-Host record (local target)"):
            return r
        owner_pid_before, _gen_before = rec
        r.check(owner_pid_before == old_pid,
                f"the record is claimed by generation 1's real pid before any cutover ({owner_pid_before} == {old_pid})")

        session = None
        acp_session_id = ""
        deadline = time.monotonic() + 60.0
        while time.monotonic() < deadline:
            session = _get_session(python, session_id)
            acp_session_id = (session or {}).get("acp_session_id") or ""
            if session and session.get("status") == "running" and acp_session_id:
                break
            time.sleep(1.0)
        r.check(bool(session) and session.get("status") == "running",
                f"session status is 'running' before firing the cutover (status={session and session.get('status')!r})")
        r.check(bool(acp_session_id), f"resolved a real acp_session_id ({acp_session_id!r})")

        events_path = _events_path(acp_session_id)
        deadline = time.monotonic() + 60.0
        mid_turn = False
        while time.monotonic() < deadline:
            lines = _read_events(events_path)
            if _count_type(lines, "assistant.turn_start") > _count_type(lines, "assistant.turn_end"):
                mid_turn = True
                break
            time.sleep(0.5)
        r.check(mid_turn, "confirmed genuinely mid-turn (turn_start with no matching turn_end yet) before firing deploy")

        before_snapshot = _read_events(events_path)

        # Fire the cutover from OUTSIDE the driven session -- the harness
        # racing the turn, exactly as a real operator update would.
        deploy = _run(python, "deploy", "--json", "--health-timeout", "60", "--drain-timeout", "5", timeout=180)
        r.check(deploy.returncode == 0, f"deploy rc==0 (rc={deploy.returncode}; {deploy.stderr.strip()[:200]})")

        a2 = _active(cfg_dir)
        new_port = a2["port"] if a2 else None
        new_pid = a2.get("pid") if a2 else None
        r.check(new_port is not None and new_port != old_port,
                f"a new daemon generation stood up beside the old; routing flipped {old_port} -> {new_port}")
        r.check(bool(new_pid) and new_pid != old_pid,
                f"the new daemon is a genuinely different real process (pid {old_pid} -> {new_pid})")
        time.sleep(1.5)
        r.check(not _listening(old_port), f"generation 1 (:{old_port}) retired")

        # The reattach that matters: the SAME session's HostIndex claim must
        # move to the NEW daemon's own real pid -- not merely "some record
        # exists" and not "the killed process's label", per Phase 5's own
        # honest-scope caveat about not conflating those.
        reattached = False
        rec2 = None
        deadline = time.monotonic() + 60.0
        while time.monotonic() < deadline:
            try:
                rec2 = _host_record(python, cfg_dir, session_id)
            except _IndexReadError:
                rec2 = None
            if rec2 is not None and rec2[0] == new_pid:
                reattached = True
                break
            time.sleep(0.5)
        r.check(reattached, f"Session-Host claim reattached under the NEW generation's real pid (record now {rec2!r})")

        # The caller-facing continuity proof: the SAME channel a real caller
        # uses to wait for a reply must settle cleanly across the boundary.
        waited = _run(python, "wait", session_id, "--attention", "turn_complete", "--json", timeout=turn_timeout)
        r.check(waited.returncode == 0,
                f"'wait --attention turn_complete' settled cleanly across the cutover (rc={waited.returncode}; {waited.stderr.strip()[:200]})")

        after_snapshot = _read_events(events_path)
        r.check(len(after_snapshot) >= len(before_snapshot), "events.jsonl only grew across the boundary (never shrank)")
        prefix_intact = after_snapshot[: len(before_snapshot)] == before_snapshot
        r.check(prefix_intact, "every event already written before the cutover is byte-identical afterward (no truncation/mutation)")
        r.check(_count_type(after_snapshot, "assistant.turn_end") == 1,
                "exactly one assistant.turn_end for the one prompt we sent (no duplicate/second turn)")
        r.check(_count_type(after_snapshot, "session.start") <= 1,
                "no duplicate session.start (the child was reattached, never respawned)")
        r.check(_count_type(after_snapshot, "session.shutdown") == 0,
                "no session.shutdown mid-stream (the child was never killed)")

        return r
    finally:
        try:
            if session_id:
                _run(python, "end", session_id, timeout=30)
        except Exception:
            pass
        try:
            os.path.exists(session_id_file) and os.remove(session_id_file)
        except Exception:
            pass
        try:
            if proc1 is not None and proc1.poll() is None:
                proc1.terminate()
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--python", required=True, help="the installed agent-bridge venv python")
    ap.add_argument("--repo", required=True, help="a real local git repo/worktree to run the session in")
    ap.add_argument("--project-name", default="live-turn-target")
    ap.add_argument("--turn-timeout", type=float, default=600.0,
                    help="max seconds to wait for the driven turn to complete (default 600)")
    args = ap.parse_args()
    try:
        res = run(args.python, args.repo, args.project_name, args.turn_timeout)
        ok = res.emit()
    except Exception as e:  # a probe crash is a FAIL, not a wedge
        print(f"PROBE: live-turn-survival FAIL probe-exception {type(e).__name__}: {e}")
        ok = False
    print(f"PROBE-SUMMARY: {1 if ok else 0}/1 passed")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
