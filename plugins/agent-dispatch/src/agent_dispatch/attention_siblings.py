"""The ``bridge`` and ``pr`` attention sources: agent-bridge's sessions and
agent-worktrees' tracked PRs, each read through its owner's own CLI (never its
files). A sibling that isn't installed is ``disabled`` (a-la-carte): it never
degrades the queue.

**bridge.** Candidates are the bridge-managed sessions (``agent-bridge --json
sessions``) and the live registered interactive ones (``agent-bridge --json
live-sessions list``), keyed by their logical delegate reference,
``wt:<machine>/<project>/<worktree-id>``: a session in both registries, or a
successor after a restart, takeover or handoff, is one entity. Each is read by
its worktree handle (``agent-bridge --json attention <worktree-id>...``), so the
bridge answers for the session that heads the worktree now, and the item's
action names that session. A candidate no managed worktree hosts has no
reference yet: it counts toward ``uncertain``. A bridge-managed session's
transcript presence is read too (``agent-bridge --json presence``); a session
whose transcript is on a remote target (an SSH read) only with
``include_remote``. If either listing fails, the source fails.

**pr.** Candidates are every PR agent-worktrees tracks in every adopted
project (``agent-worktrees claims find pr --state all --json``), each read once
by its project, repository and number (``agent-worktrees -p <project> pr bar
<owner/name> <n> --json``). An ``OPEN`` PR whose bar ``failed`` is an item; the
live state decides, so a closed PR is none even when its record says open. A
record whose PR merged is skipped unread: a merge is terminal on every provider,
so it can never need its author again. A bar read that is ``unknown`` or fails,
and a record that doesn't resolve to an authority, ``owner/name`` and number,
count toward ``uncertain``; a project whose PRs can't be enumerated fails the
source.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from typing import Any, Callable

from . import attention_contract as ac

#: Each source's own deadline (seconds), inside which every read it makes runs.
#: A ``pr bar`` read takes several provider calls (~10 s each), so the pr source
#: reads up to ``PR_WORKERS`` PRs at once and gets the longer deadline.
BRIDGE_TIMEOUT = 30.0
PR_TIMEOUT = 90.0
#: A single sibling CLI call's cap, and how many run at once.
CALL_TIMEOUT = 30.0
WORKERS = 6
PR_WORKERS = 10
#: Any one sibling call's output (characters); more fails that call.
MAX_OUTPUT = 4 * 1024 * 1024
#: The reserve each source keeps for its own bookkeeping before its deadline.
_MARGIN = 1.0

Runner = Callable[..., Any]

_ASKS = {"input_required", "permission_required", "policy_required"}
_FAILURES = {"failed", "unreachable"}
_SETTLED = {"turn_complete", "turn_cancelled", "stopped", "ended"}
_TERMINAL_STATUSES = {"stopped", "ended"}
_CONFIDENCE_RANK = {c: i for i, c in enumerate(ac.CONFIDENCES)}


def _default_run() -> Runner:
    from .procutil import run_background_capture

    return run_background_capture


def _call_json(run: Runner, argv: list[str], deadline: float, *,
               env: dict[str, str] | None = None) -> tuple[Any, str | None, int | None]:
    """``(parsed stdout, error, exit code)``; the error is ``None`` when the
    command ran and printed JSON, whatever its exit code."""
    from .procutil import OutputLimitExceeded

    remaining = min(CALL_TIMEOUT, deadline - time.monotonic())
    if remaining <= 0:
        return None, "no time left before the source's deadline", None
    kwargs: dict[str, Any] = {"timeout": remaining, "max_output": MAX_OUTPUT}
    if env is not None:
        kwargs["env"] = env
    try:
        done = run(argv, **kwargs)
    except OutputLimitExceeded:
        return None, f"wrote more than {MAX_OUTPUT} characters", None
    if done is None:
        return None, f"did not finish within {remaining:.0f}s (or could not start)", None
    try:
        return json.loads(done.stdout), None, done.returncode
    except ValueError:
        detail = ac.one_line(done.stderr or done.stdout, 160) or "no output"
        return None, f"exited {done.returncode} without JSON: {detail}", done.returncode


def _parallel(jobs: list[Callable[[], Any]], deadline: float, workers: int = WORKERS) -> list[Any]:
    """Run ``jobs`` on at most ``workers`` daemon threads (never an executor,
    whose exit hook would wait for a hung call); a job unfinished at
    ``deadline`` yields ``None``."""
    results: list[Any] = [None] * len(jobs)
    pending: queue.Queue[int] = queue.Queue()
    for index in range(len(jobs)):
        pending.put(index)

    def work() -> None:
        while time.monotonic() < deadline:
            try:
                index = pending.get_nowait()
            except queue.Empty:
                return
            try:
                results[index] = jobs[index]()
            except Exception:  # noqa: BLE001 -- one call's failure is that entity's uncertainty
                results[index] = None

    threads = [threading.Thread(target=work, daemon=True, name="attention-sibling")
               for _ in range(min(workers, len(jobs)))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))
    return results


def _failed(error: str) -> dict[str, Any]:
    return ac.command_failure(error)


def _coalesce(items: list[dict[str, Any]]) -> dict[str, Any]:
    """One item per entity: the highest severity, then the strongest
    confidence; its reason names the rest."""
    ranked = sorted(items, key=lambda i: (i["severity"], _CONFIDENCE_RANK[i["confidence"]]))
    kept = dict(ranked[0])
    others = [i["reason"] for i in ranked[1:] if i["reason"] != kept["reason"]]
    if others:
        kept["reason"] = ac.one_line(f"{kept['reason']}; also {'; '.join(others)}")
    return kept


# -- bridge ------------------------------------------------------------------------


def _logical_ref(machine: str | None, project: str | None, worktree_id: str | None) -> str | None:
    if machine and project and worktree_id:
        return f"wt:{machine}/{project}/{worktree_id}"
    return None


def _bridge_candidates(sessions: list[Any], live: list[Any], machine: str | None
                       ) -> tuple[dict[str, dict[str, Any]], int]:
    """``({logical ref: candidate}, uncertain)``: every session that could be
    parked, keyed by its logical reference; one without a reference counts."""
    candidates: dict[str, dict[str, Any]] = {}
    uncertain = 0
    for row in sessions:
        if not isinstance(row, dict) or str(row.get("status") or "") in _TERMINAL_STATUSES:
            continue  # a stopped or ended session is parked on nothing
        ref = _logical_ref(machine, row.get("project"), row.get("worktree_id"))
        if ref is None:
            uncertain += 1
            continue
        candidates.setdefault(ref, {"handle": row["worktree_id"], "lifecycle": row.get("status"), "bridge": {}})
        candidates[ref]["bridge"][str(row.get("session_id"))] = row
    for row in live:
        if not isinstance(row, dict) or row.get("status") != "live":
            continue
        ref = _logical_ref(row.get("machine"), row.get("repo"), row.get("worktree_id"))
        if ref is None:
            uncertain += 1
            continue
        candidates.setdefault(ref, {"handle": row["worktree_id"], "lifecycle": "live", "bridge": {}})
    return candidates, uncertain


def _attention_item(ref: str, entry: dict[str, Any], lifecycle: str | None, read_at: str
                    ) -> tuple[dict[str, Any] | None, bool]:
    """``(item or None, uncertain)`` for one session's attention read."""
    if entry.get("status") != "ok" or entry.get("availability") == "unknown_after_restart":
        return None, True
    reason = entry.get("reason")
    if reason is None or reason in _SETTLED:
        return None, False
    if reason in _ASKS:
        state = "awaiting_input"
    elif reason in _FAILURES:
        state = "failed"
    else:  # contract_changed, or a reason this adapter doesn't know (a newer bridge)
        return None, True
    detail = f": {entry['detail']}" if entry.get("detail") else ""
    session_id = str(entry.get("session_id") or "")
    return _session_item(ref, state, "reported", f"{reason.replace('_', ' ')}{detail}", lifecycle,
                         session_id, read_at), False


def _session_item(ref: str, state: str, confidence: str, reason: str, lifecycle: str | None,
                  session_id: str, read_at: str) -> dict[str, Any]:
    return {
        "schema": ac.SCHEMA, "entity": "session", "entity_ref": ref, "lifecycle_state": lifecycle,
        "display_state": state, "severity": ac.SEVERITY[state], "reason": ac.one_line(reason),
        "created_at": None, "updated_at": read_at, "confidence": confidence,
        "actions": [{"verb": "show", "argv": ["agent-bridge", "result", session_id]}] if session_id else [],
        "source": "bridge", "id": ac.make_id("bridge", "session", ref), "also": [],
    }


def _presence_item(ref: str, body: Any, lifecycle: str | None, session_id: str, read_at: str
                   ) -> tuple[dict[str, Any] | None, bool]:
    presence = body.get("presence") if isinstance(body, dict) else None
    if not isinstance(presence, dict):
        return None, True
    state = presence.get("state")
    if state == "unknown":
        return None, True
    if state != "awaiting_input":
        return None, False
    confidence = presence.get("confidence") if presence.get("confidence") in ("scanned", "heuristic") else "scanned"
    reason = "its transcript is waiting on you" + (f": {presence['reason']}" if presence.get("reason") else "")
    return _session_item(ref, "awaiting_input", confidence, reason, lifecycle, session_id, read_at), False


def _listing_problem(value: Any, error: str | None, code: int | None) -> str | None:
    if error:
        return error
    if code:
        return f"exited {code}"
    return None if isinstance(value, list) else "its output isn't a list"


def read_bridge(read_at: str, *, prefix: list[str], machine: str | None, include_remote: bool = False,
                run: Runner | None = None, timeout: float = BRIDGE_TIMEOUT) -> dict[str, Any]:
    run = run or _default_run()
    deadline = time.monotonic() + timeout - _MARGIN
    sessions, error, code = _call_json(run, [*prefix, "--json", "sessions"], deadline)
    if problem := _listing_problem(sessions, error, code):
        return _failed(f"agent-bridge sessions: {problem}")
    live, error, code = _call_json(run, [*prefix, "--json", "live-sessions", "list"], deadline)
    if problem := _listing_problem(live, error, code):
        return _failed(f"agent-bridge live-sessions list: {problem}")
    candidates, uncertain = _bridge_candidates(sessions, live, machine)
    if not candidates:
        return {"items": [], "status": "uncertain" if uncertain else "ok", "uncertain": uncertain,
                "read_at": read_at}
    refs = sorted(candidates)
    body, error, _code = _call_json(run, [*prefix, "--json", "attention", *[candidates[r]["handle"] for r in refs]],
                                    deadline)
    entries = body.get("sessions") if isinstance(body, dict) else None
    if error or not isinstance(entries, list) or len(entries) != len(refs):
        return {"items": [], "status": "uncertain", "uncertain": uncertain + len(refs), "read_at": read_at}
    found: dict[str, list[dict[str, Any]]] = {ref: [] for ref in refs}
    presence_jobs: list[tuple[str, str, Callable[[], Any]]] = []
    for ref, entry in zip(refs, entries):
        lifecycle = candidates[ref]["lifecycle"]
        item, unsure = _attention_item(ref, entry if isinstance(entry, dict) else {}, lifecycle, read_at)
        uncertain += unsure
        if item:
            found[ref].append(item)
        session_id = str((entry or {}).get("session_id") or "") if isinstance(entry, dict) else ""
        row = candidates[ref]["bridge"].get(session_id)
        if row is None or (row.get("target_type") not in (None, "local") and not include_remote):
            continue  # an interactive session, or a remote transcript the caller didn't opt into
        presence_jobs.append((ref, session_id, lambda s=session_id: _call_json(
            run, [*prefix, "--json", "presence", s], deadline)))
    results = _parallel([job for _ref, _sid, job in presence_jobs], deadline)
    for (ref, session_id, _job), result in zip(presence_jobs, results):
        body, error = (result[0], result[1]) if result else (None, "no answer before the deadline")
        item, unsure = _presence_item(ref, None if error else body, candidates[ref]["lifecycle"], session_id,
                                      read_at)
        uncertain += unsure
        if item:
            found[ref].append(item)
    items = [_coalesce(group) for group in found.values() if group]
    return {"items": items, "status": "uncertain" if uncertain else "ok", "uncertain": uncertain,
            "read_at": read_at}


# -- pr ----------------------------------------------------------------------------


def _pr_item(authority: str, repo: str, number: int, project: str, bar: dict[str, Any],
             read_at: str) -> dict[str, Any]:
    ref = f"{authority}/{repo}#{number}"
    failing = [c for c in bar.get("clauses") or [] if isinstance(c, dict) and c.get("status") == "failed"]
    why = "; ".join(f"{c.get('id')}: {c.get('evidence') or c.get('error') or ''}".strip() for c in failing[:2])
    actions = [{"verb": "show", "argv": ["agent-worktrees", "-p", project, "pr", "bar", repo, str(number)]}]
    if authority == "github.com":
        actions.append({"verb": "open", "argv": ["gh", "pr", "view", str(number), "--repo", repo, "--web"]})
    return {
        "schema": ac.SCHEMA, "entity": "pr", "entity_ref": ref, "lifecycle_state": "open",
        "display_state": "failed", "severity": ac.SEVERITY["failed"],
        "reason": ac.one_line(f"{repo}#{number} merge bar failed" + (f": {why}" if why else "")),
        "created_at": None, "updated_at": read_at, "confidence": "reported", "actions": actions,
        "source": "pr", "id": ac.make_id("pr", "pr", ref), "also": [],
    }


def read_pr(read_at: str, *, prefix: list[str], env: dict[str, str] | None = None,
            run: Runner | None = None, timeout: float = PR_TIMEOUT) -> dict[str, Any]:
    run = run or _default_run()
    deadline = time.monotonic() + timeout - _MARGIN
    body, error, code = _call_json(run, [*prefix, "claims", "find", "pr", "--state", "all", "--json"], deadline,
                                   env=env)
    if error or code:
        return _failed(f"agent-worktrees claims find pr: {error or f'exited {code}'}")
    if not isinstance(body, dict) or body.get("schema") != 1 or not isinstance(body.get("projects"), list):
        return _failed("agent-worktrees claims find pr has no schema-1 projects envelope (an older agent-worktrees?)")
    uncertain = 0
    candidates: dict[tuple[str, str, int], tuple[str, str, str, int]] = {}
    for entry in body["projects"]:
        if not isinstance(entry, dict):
            return _failed("agent-worktrees claims find pr: a project entry isn't an object")
        if entry.get("status") != "ok":
            return _failed(f"project {entry.get('project')!r}: its tracked PRs couldn't be enumerated: "
                           f"{entry.get('error') or 'failed'}")
        uncertain += int(entry.get("unreadable") or 0)
        for pr in entry.get("prs") or []:
            if not isinstance(pr, dict) or pr.get("state") == "merged":
                continue
            authority, repo, number = pr.get("authority"), str(pr.get("repo") or ""), pr.get("number")
            if not authority or "/" not in repo or type(number) is not int:
                uncertain += 1
                continue
            candidates.setdefault((str(authority), repo.lower(), number),
                                  (str(entry["project"]), str(authority), repo, number))
    reads = list(candidates.values())
    results = _parallel([lambda p=p, r=r, n=n: _call_json(
        run, [*prefix, "-p", p, "pr", "bar", r, str(n), "--json"], deadline, env=env)
        for p, _a, r, n in reads], deadline, workers=PR_WORKERS)
    items = []
    for (project, authority, repo, number), result in zip(reads, results):
        bar = result[0] if result and not result[1] else None
        if not isinstance(bar, dict) or bar.get("verdict") not in ("met", "merged", "pending", "failed"):
            uncertain += 1  # unknown, unreadable, or out of time
            continue
        if str(bar.get("state") or "").upper() == "OPEN" and bar["verdict"] == "failed":
            items.append(_pr_item(authority, repo, number, project, bar, read_at))
    return {"items": items, "status": "uncertain" if uncertain else "ok", "uncertain": uncertain,
            "read_at": read_at}
