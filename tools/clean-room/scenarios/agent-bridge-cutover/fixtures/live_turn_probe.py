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
  4. Every turn opened is balanced by exactly one matching close (turnId-
     correlated, not aggregate counts -- a replayed duplicate pair would
     still balance by count alone), no orphan ends, no duplicate event
     ids, AND the SPECIFIC turn open at the verified-reattach boundary
     closes strictly AFTER it (not merely "some" turn_end appearing later
     in the file, which could have closed during deploy's own startup,
     before the generation actually changed) -- one prompt can
     legitimately open several turn_start/turn_end pairs (one per model
     completion in an agentic tool-calling loop), so an exact count of 1
     is the wrong assertion; no duplicate ``session.start`` (the child was
     reattached, never respawned); the ``acp_session_id`` itself never
     changes after settling (never silently replaced by reattach).

**Scope note (read before trusting this drill's PASS as proof of
caller-facing continuity).** This drill's own PASS/FAIL verdict
proves session- and transcript-level survival at the daemon/Session-Host
level (points 1-4 above) -- it does **not** prove the caller-facing "a
reply reaches the client" guarantee end to end. `agent-bridge wait
<sid> --attention turn_complete` -- the same channel a real caller uses to
learn a turn is done -- is invoked only as a best-effort, non-blocking
**advisory** secondary check: it can hang indefinitely after a Session-Host
reattach even though the session correctly reaches `idle` (tracked as
[issue #4681](https://github.com/ThomasMichon/copilot-extensions/issues/4681),
not silently worked around). Until that upstream gap is resolved, treat
this drill as proving points 1-4 only.

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
import contextlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime

DEFAULT_PROMPT = (
    "Run the shell command `sleep 60` (wait for it to finish), then reply "
    "with exactly the single word: DONE."
)
# `deploy` itself is bounded by --health-timeout/--drain-timeout below (60s/5s)
# and the reattach-confirmation poll is bounded at 60s, so a slow box's
# absolute worst case could in principle exceed a short sleep. Every real run
# observed deploy+reattach completing in 1-5s; 60s leaves a wide safety
# margin over that OBSERVED behavior without paying for the rarely-hit
# worst case in real credits every run (the design doc's own feasibility
# note calls this exact tradeoff "the hardest part" to tune).


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


def _run(python: str, *args, timeout=120, json_out=False):
    """Invoke ``python -m agent_bridge <args>``.

    ``--json`` is a GLOBAL option (``build_parser()``'s top-level parser).
    Some subparsers (``deploy``, ``wait``) ALSO define their own local
    ``--json`` (``sessions`` does not). CPython's own
    ``argparse._SubParsersAction.__call__`` unconditionally copies the
    chosen subparser's own parsed namespace over the top of the caller's
    namespace -- so when a subcommand defines a local ``--json`` with a
    plain ``False`` default (``wait`` does; ``deploy`` deliberately does
    NOT, via ``default=argparse.SUPPRESS`` -- see its own comment in
    ``venue_cli.py``), that local default SILENTLY RESETS a global
    ``--json`` passed before the subcommand back to ``False``:
    `--json wait <sid> --attention turn_complete` runs in TEXT-rendering
    mode, not JSON, even though the global flag was given. Place `--json`
    BEFORE the subcommand for `sessions` (no local flag at all) and AFTER
    it, as an explicit local flag, for `wait`/`deploy` (whose local default
    would otherwise clobber the global one).
    """
    if json_out:
        if args and args[0] in ("wait", "deploy"):
            args = (*args, "--json")  # local flag: must be explicit, not defaulted
        else:
            args = ("--json", *args)  # e.g. `sessions`, which has no local flag at all
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


def _stop_daemon_identity_safe(python: str) -> tuple[bool, str]:
    """Replace whatever agent-bridge daemon is currently running, verifying
    the IDENTITY of every candidate victim BEFORE ever signaling anything.

    ``agent-bridge service stop`` (``service_process_cli.py``'s
    ``_service_stop``) is NOT identity-safe by itself: it takes the union
    of the pid-file pid, the port's current holder pid, and the singleton
    lock's holder pid, and calls its own internal ``_kill_pid`` on each
    BEFORE checking identity -- ``_pid_is_agent_bridge`` only runs
    afterward, to confirm cleanup succeeded, not to gate the kill. A stale
    pid-file (or a lock/port pid) that has since been reused by an
    unrelated process could be killed by that path, and it would still
    report ``[OK]`` once no *agent-bridge* process is found afterward.

    This wrapper closes that gap without touching agent-bridge's own
    production code: it gathers the SAME three candidate sources
    (``_read_pid_file``, ``_pid_on_port``, ``_pid_from_lock``) via a
    subprocess importing those exact functions, verifies EVERY live
    candidate with the SAME ``_pid_is_agent_bridge`` check FIRST, and
    refuses to call ``service stop`` at all if any candidate is alive but
    not confirmed as an agent-bridge process -- rather than calling it and
    hoping. A residual TOCTOU window remains between this precheck and
    ``service stop``'s own kill (unavoidable without an identity-bound
    process handle from agent-bridge itself, which this drill does not
    have); this closes the much larger window of never checking at all.
    Returns ``(ok, detail)``.
    """
    snip = (
        "from agent_bridge.service_process_cli import _pid_on_port, _pid_from_lock, _pid_is_agent_bridge\n"
        "from agent_bridge.service_process_state import _read_pid_file, _service_port\n"
        "import os\n"
        "port = _service_port()\n"
        "candidates = {_read_pid_file(), _pid_on_port(port), _pid_from_lock(port), _pid_from_lock(0)}\n"
        "candidates.discard(None)\n"
        "def _alive(pid):\n"
        "    try:\n"
        "        os.kill(pid, 0)\n"
        "    except OSError:\n"
        "        return False\n"
        "    return True\n"
        "unverified = sorted(p for p in candidates if _alive(p) and not _pid_is_agent_bridge(p))\n"
        "print(repr(sorted(candidates)) + '|' + repr(unverified))\n"
    )
    out = _run_snip(python, snip, timeout=30)
    if out.returncode != 0:
        return False, f"identity precheck subprocess failed (rc={out.returncode}): {out.stderr.strip()[:200]}"
    try:
        _candidates_repr, unverified_repr = out.stdout.strip().split("|", 1)
        unverified = eval(unverified_repr, {"__builtins__": {}})  # noqa: S307 -- our own list literal
    except Exception as exc:
        return False, f"unparseable identity precheck output {out.stdout.strip()!r}: {exc}"
    if unverified:
        return False, (
            f"refusing to stop -- candidate pid(s) {unverified} are alive but do NOT identify as an "
            f"agent-bridge process (stale pid-file/port/lock entry reused by something else)"
        )

    out = _run(python, "service", "stop", timeout=30)
    text = (out.stdout or "") + (out.stderr or "")
    if out.returncode == 0 and ("[OK]" in text or "[SKIP]" in text):
        return True, text.strip()
    return False, f"rc={out.returncode}; {text.strip()[:300]}"


def _get_session(python: str, session_id: str) -> dict | None:
    out = _run(python, "sessions", json_out=True)
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


def _malformed_line_count(lines: list[str]) -> int:
    """Count lines that are not valid JSON **objects**.

    A malformed or partially-written line (e.g. a torn last line from a
    concurrent write) would otherwise be silently skipped by
    ``_count_type``/``_turn_balance_and_boundary_crossing``, letting a
    damaged transcript still report a balanced result if the surviving
    lines happen to balance. Blank trailing lines are not malformed.
    ``json.loads`` also accepts bare numbers/strings/arrays/null as valid
    JSON -- a damaged-but-syntactically-valid line like that is not a
    dict, so it counts as malformed here too (the event walkers below all
    assume a dict and would otherwise raise on `.get()`).
    """
    bad = 0
    for line in lines:
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except Exception:
            bad += 1
            continue
        if not isinstance(parsed, dict):
            bad += 1
    return bad


def _parse_event_ts(raw: object) -> float | None:
    """Parse an event's own ``timestamp`` field to a comparable epoch float.
    Accepts either a numeric epoch or an ISO-8601 string; returns None for
    anything unparseable rather than guessing.
    """
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _turn_balance_and_boundary_crossing(lines: list[str], boundary_ts: float, boundary_turn_id: str | None = None) -> dict:
    """Walk ``events.jsonl`` lines IN ORDER, correlating each
    ``assistant.turn_end`` with its OWN ``assistant.turn_start`` via the
    transcript's own ``data.turnId`` field (present on both event types)
    rather than aggregate counts or an open/close depth guess -- neither
    proves non-duplication or which SPECIFIC turn closed when:

    - Aggregate ``turn_start``/``turn_end`` totals being equal would still
      PASS a replayed ``start(A), end(A), start(A), end(A)`` duplicate.
      Every event also carries its own ``id``
      (``peek_snapshot.py``'s documented schema); reject any repeated
      ``id`` as a real duplicate delivery. A turn_id that starts MORE THAN
      ONCE (even with distinct event ids, and even if a prior instance of
      it already closed) is tracked as its own imbalance -- a depth-only
      open/close counter would miss this, since `start(A), start(A),
      end(A)` still nets to a single open/close pair.
    - "Some turn_end appears later in the file" doesn't prove the turn
      that was open AT THE VERIFIED BOUNDARY MOMENT (a confirmed reattach,
      not merely "deploy was launched") is the one that closed afterward
      -- it could have been a different turn entirely, or the original
      one could have closed during deploy's own startup, before the
      generation actually changed. When ``boundary_turn_id`` is given,
      ``crossed`` requires THAT turn's own recorded close to have a
      timestamp after ``boundary_ts``; without it, this falls back to
      "any turn_end after the boundary" (still ordered/duplicate-safe, but
      not turn-specific).

    Returns a dict with ``balanced`` (every distinct turn_id started
    exactly once and was closed by exactly one turn_end, no orphans, no
    duplicate ids, no repeated starts), ``crossed``, and diagnostic
    counts.
    """
    open_turns: dict[str, float | None] = {}
    started_ids: set[str] = set()
    boundary_close_ts: float | None = None
    orphan_ends = 0
    duplicate_ids = 0
    repeated_starts = 0
    seen_ids: set[str] = set()
    for line in lines:
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if not isinstance(ev, dict):
            continue  # syntactically-valid JSON that isn't an event object; _malformed_line_count already flags it
        eid = ev.get("id")
        if eid is not None:
            if eid in seen_ids:
                duplicate_ids += 1
                continue  # never let a replayed event double-count below
            seen_ids.add(eid)
        t = ev.get("type")
        turn_id = str((ev.get("data") or {}).get("turnId") or "")
        if t == "assistant.turn_start":
            if turn_id in started_ids:
                repeated_starts += 1
            else:
                started_ids.add(turn_id)
            open_turns[turn_id] = None
        elif t == "assistant.turn_end":
            if turn_id not in open_turns:
                orphan_ends += 1
                continue
            ts = _parse_event_ts(ev.get("timestamp"))
            del open_turns[turn_id]
            if boundary_turn_id is not None and turn_id == boundary_turn_id:
                boundary_close_ts = ts
    if boundary_turn_id is not None:
        crossed = boundary_close_ts is not None and boundary_close_ts > boundary_ts
    else:
        crossed = boundary_close_ts is not None  # unreachable fallback path kept for callers with no turn id
    return {
        "balanced": not open_turns and orphan_ends == 0 and duplicate_ids == 0 and repeated_starts == 0,
        "crossed": crossed,
        "orphan_ends": orphan_ends,
        "duplicate_ids": duplicate_ids,
        "repeated_starts": repeated_starts,
        "still_open": len(open_turns),
        "boundary_close_ts": boundary_close_ts,
    }


def run(python: str, repo: str, project_name: str, turn_timeout: float) -> Result:
    r = Result("live-turn-survival")
    cfg_dir = _config_dir(python)
    r.check(bool(cfg_dir), f"resolved agent-bridge config dir ({cfg_dir!r})")

    projects_yaml = os.path.join(os.path.expanduser("~"), ".agent-worktrees", "projects.yaml")
    os.makedirs(os.path.dirname(projects_yaml), exist_ok=True)
    with open(projects_yaml, "w", encoding="utf-8") as f:
        f.write(f"projects:\n  {project_name}:\n    anchor: {repo!r}\n    expose_agent: true\n")
    r.check(os.path.exists(projects_yaml), f"registered local project {project_name!r} -> {repo}")

    # NB: a daemon's static local-agent registry (discover_local_agents())
    # is resolved ONCE at startup (`daemon_resolver(cfg)`, no periodic
    # reload -- unlike `refresh_provider_resolvers`, which only covers
    # namespace/CodeSpace/container providers). A daemon started BEFORE
    # this drill writes projects.yaml (e.g. by phase 2's own
    # `copilot -p ...` sessionStart hook) reports "(no agents registered)"
    # and `create` fails closed with "not a known agent name", even though
    # the file on disk is already correct. Any daemon this drill uses for
    # `create` MUST have started AFTER the write above, so unconditionally
    # replace whatever is running via the daemon's own production stop
    # path (see `_stop_daemon_identity_safe`), rather than assuming reuse
    # is safe.
    stopped_ok, stop_detail = _stop_daemon_identity_safe(python)
    if not r.check(stopped_ok, f"replaced any pre-existing daemon via 'agent-bridge service stop' ({stop_detail})"):
        return r

    proc1 = subprocess.Popen(
        [python, "-m", "agent_bridge", "start", "--port", "0", "--bind", "127.0.0.1"],
        start_new_session=True,
    )
    # A bare `_active()` read can still return a stale routing entry (not
    # necessarily ours) for a brief window before `proc1`'s own publish
    # overwrites it. Poll specifically for OUR pid, not merely "some
    # port", before trusting the result as generation 1.
    a1 = None
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        candidate = _active(cfg_dir, tries=1)
        if candidate and candidate.get("pid") == proc1.pid:
            a1 = candidate
            break
        time.sleep(0.25)
    if a1 is None:
        # Never leave an unpublished daemon subprocess running in this
        # PERSISTENT clean-room container -- it could confuse a later
        # retry. Escalate to a hard kill if it doesn't exit gracefully;
        # never suppress a failed cleanup.
        with contextlib.suppress(Exception):
            proc1.terminate()
        try:
            proc1.wait(timeout=10)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(Exception):
                proc1.kill()
                proc1.wait(timeout=10)
        r.check(False, f"a daemon (generation 1, our own real pid {proc1.pid}) published routing within 30s")
        return r
    old_port = a1["port"]
    old_pid = a1.get("pid")
    r.check(_listening(old_port), f"generation 1 listening on :{old_port} (pid {old_pid})")

    session_id_file = tempfile.mktemp(prefix="abv-live-turn-sid-")
    session_id = ""
    try:
        create = _run(
            # NB: the positional prompt must come immediately after the
            # agent name, before --target-dir/other flags -- argparse's
            # nargs="?" positional interleaved with a value-taking optional
            # (--target-dir PATH) placed BEFORE it fails closed with
            # "unrecognized arguments".
            python, "create", project_name, DEFAULT_PROMPT, "--target-dir", repo,
            "--no-wait", "--session-id-file", session_id_file,
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
        boundary_turn_id = None
        while time.monotonic() < deadline:
            lines = _read_events(events_path)
            open_ids = []
            for line in lines:
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                if not isinstance(ev, dict):
                    continue
                tid = str((ev.get("data") or {}).get("turnId") or "")
                if ev.get("type") == "assistant.turn_start":
                    open_ids.append(tid)
                elif ev.get("type") == "assistant.turn_end" and tid in open_ids:
                    open_ids.remove(tid)
            if open_ids:
                mid_turn = True
                boundary_turn_id = open_ids[-1]  # the most-recently-opened still-open turn
                break
            time.sleep(0.5)
        r.check(mid_turn, "confirmed genuinely mid-turn (turn_start with no matching turn_end yet) before firing deploy")
        r.check(bool(boundary_turn_id), f"identified the SPECIFIC turn (turnId={boundary_turn_id!r}) open at the moment we fire deploy")

        before_snapshot = _read_events(events_path)

        # Fire the cutover from OUTSIDE the driven session -- the harness
        # racing the turn, exactly as a real operator update would.
        deploy = _run(python, "deploy", "--health-timeout", "60", "--drain-timeout", "5", timeout=180, json_out=True)
        r.check(deploy.returncode == 0, f"deploy rc==0 (rc={deploy.returncode}; {deploy.stderr.strip()[:200]})")

        a2 = _active(cfg_dir)
        new_port = a2["port"] if a2 else None
        new_pid = a2.get("pid") if a2 else None
        r.check(new_port is not None and new_port != old_port,
                f"a new daemon generation stood up beside the old; routing flipped {old_port} -> {new_port}")
        r.check(bool(new_pid) and new_pid != old_pid,
                f"the new daemon is a genuinely different real process (pid {old_pid} -> {new_pid})")

        # The reattach that matters: the SAME session's HostIndex claim must
        # move to the NEW daemon's own real pid -- not merely "some record
        # exists" and not "the killed process's label", per Phase 5's own
        # honest-scope caveat about not conflating those. Poll for this
        # FIRST (before the unrelated old-port-retirement check below) and
        # capture the boundary timestamp the INSTANT it is confirmed --
        # every extra step between the real reattach and this observation
        # widens the window in which a genuinely-surviving turn could
        # close before we notice, understating the drill's own margin.
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
        boundary_ts = time.time()
        r.check(reattached, f"Session-Host claim reattached under the NEW generation's real pid (record now {rec2!r})")

        time.sleep(1.5)
        r.check(not _listening(old_port), f"generation 1 (:{old_port}) retired")

        # Completion detection: the session status (`sessions --json`,
        # already proven reliable above) is the authoritative signal that
        # the turn settled -- poll it directly rather than relying solely
        # on `wait --attention turn_complete`, which can hang after a
        # reattach even when the session correctly reaches "idle" (a real,
        # separate finding about the attention-wait channel's own
        # interaction with reattach, tracked upstream -- see the effort
        # README's Journal), not something this drill should block a PASS
        # on when the authoritative status already proves the turn safely
        # survived the cutover.
        #
        # The session-status flip to "idle" is NOT by itself sufficient:
        # a real run showed the frontend's own status can report "idle"
        # while the transcript still shows the boundary turn's own tool
        # call approved but never executed/closed (the session-status
        # bookkeeping and the actual Session-Host child's progress are not
        # perfectly synchronized immediately after a reattach). Require
        # BOTH: status == "idle" AND the transcript's own boundary turn
        # has actually closed, continuing to poll otherwise -- the
        # transcript, not the coarse status flag, is the authority on
        # whether real work finished.
        settled_via_status = False
        identity_drift = None
        deadline = time.monotonic() + turn_timeout
        while time.monotonic() < deadline:
            session_after = _get_session(python, session_id)
            if session_after:
                current_acp = session_after.get("acp_session_id") or ""
                if current_acp and current_acp != acp_session_id:
                    identity_drift = current_acp
                    break
                if session_after.get("status") == "idle":
                    probe_lines = _read_events(events_path)
                    probe_analysis = _turn_balance_and_boundary_crossing(probe_lines, boundary_ts, boundary_turn_id)
                    if probe_analysis["balanced"] and _malformed_line_count(probe_lines) == 0:
                        settled_via_status = True
                        break
            time.sleep(1.0)
        r.check(identity_drift is None,
                f"acp_session_id never changed while polling for completion ({acp_session_id!r} -> {identity_drift!r} "
                "would mean reattach silently replaced the child, not merely resumed it)")
        r.check(settled_via_status,
                f"session status reached 'idle' AND the transcript's own boundary turn actually closed within "
                f"{turn_timeout:.0f}s of the cutover")

        # Session-identity check: "idle" alone doesn't prove the reattach
        # preserved the SAME child -- if reattach ever replaced the child
        # while keeping the bridge session id, this would read a stale/
        # wrong transcript path and could still PASS. Confirm the
        # acp_session_id (hence the events.jsonl path itself) never
        # changed after settling (in addition to the per-iteration check
        # above, which covers a drift during the polling window itself).
        session_final = _get_session(python, session_id)
        acp_session_id_after = (session_final or {}).get("acp_session_id") or ""
        r.check(acp_session_id_after == acp_session_id,
                f"acp_session_id unchanged after settling ({acp_session_id!r} -> {acp_session_id_after!r} -- "
                "the reattach preserved the SAME child, never replaced it)")

        # Best-effort secondary signal on the SAME caller-facing channel a
        # real caller uses. Bounded to a short window (the session is
        # already known idle above, so a working wait should return near-
        # instantly) -- advisory only, never blocks the drill's verdict.
        # This means the drill does NOT prove the caller-facing "a reply
        # reaches the client" guarantee end to end -- only session/
        # transcript survival at the daemon/Session-Host level. The `wait
        # --attention turn_complete` channel itself is tracked separately
        # as a real, reproducible gap (issue #4681) and deliberately
        # excluded from this drill's own PASS/FAIL verdict rather than
        # silently assumed proven.
        wait_settled = False
        wait_reason = None
        wait_note = ""
        try:
            waited = _run(python, "wait", session_id, "--attention", "turn_complete", timeout=20, json_out=True)
            if waited.returncode == 0:
                try:
                    wait_payload = json.loads(waited.stdout)
                    wait_settled = bool(wait_payload.get("settled"))
                    wait_reason = wait_payload.get("reason")
                except Exception:
                    wait_note = "unparseable JSON"
            else:
                wait_note = f"rc={waited.returncode}"
        except subprocess.TimeoutExpired:
            wait_note = "did not settle within the 20s advisory window (tracked upstream finding #4681, not a drill failure)"
        r.detail.append(
            ("ok" if wait_settled and wait_reason == "turn_complete" else "advisory")
            + ": 'wait --attention turn_complete' secondary check (NOT part of this drill's verdict -- see issue #4681) "
            + f"-- settled={wait_settled!r}, reason={wait_reason!r}"
            + (f" ({wait_note})" if wait_note else "")
        )

        after_snapshot = _read_events(events_path)
        malformed = _malformed_line_count(after_snapshot)
        r.check(malformed == 0, f"final events.jsonl has zero malformed/unparseable lines (found {malformed})")
        r.check(len(after_snapshot) >= len(before_snapshot), "events.jsonl only grew across the boundary (never shrank)")
        prefix_intact = after_snapshot[: len(before_snapshot)] == before_snapshot
        r.check(prefix_intact, "every event already written before the cutover is byte-identical afterward (no truncation/mutation)")
        # NB: one user prompt can legitimately produce SEVERAL
        # assistant.turn_start/turn_end pairs -- Copilot's ACP loop opens a
        # new turn per model completion, so a multi-tool-call prompt (the
        # deliberately long-running one this drill sends) can produce
        # several turn_end events, not 1. Correlate by the transcript's own
        # `data.turnId` instead of counting: every distinct turn_id must
        # start exactly once and close by exactly one turn_end (no orphan
        # ends, no repeated starts, no duplicate event ids), and the
        # SPECIFIC turn open at the verified-reattach boundary must close
        # strictly after it -- proving that exact turn genuinely continued
        # across the cutover, not merely that some unrelated turn_end
        # appears later in the file (which could have closed during
        # deploy's own startup, before the generation actually changed).
        analysis = _turn_balance_and_boundary_crossing(after_snapshot, boundary_ts, boundary_turn_id)
        r.check(analysis["balanced"],
                f"every distinct turn_id started exactly once and closed by exactly one turn_end "
                f"(turnId-correlated), no orphan ends, no duplicate event ids, no repeated starts "
                f"(orphan_ends={analysis['orphan_ends']}, duplicate_ids={analysis['duplicate_ids']}, "
                f"repeated_starts={analysis['repeated_starts']}, still_open={analysis['still_open']})")
        r.check(analysis["crossed"],
                f"the SPECIFIC turn (turnId={boundary_turn_id!r}) open at the verified-reattach boundary closed "
                f"strictly AFTER it (its own recorded close ts={analysis['boundary_close_ts']!r} vs boundary "
                f"ts={boundary_ts!r}) -- not merely some unrelated turn_end appearing later in the file")
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
