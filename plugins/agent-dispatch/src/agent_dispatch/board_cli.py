"""Fast stdlib-only Tasks-board client for the Picker provider."""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .install_paths import install_dir
from .procutil import no_window_kwargs as _no_window_kwargs
from .procutil import windowless_python, windowless_python_env
from .worktree_status_relay import board_fields_for_task, claimed_identity

#: Operator feedback 2026-09-20: Started is more interesting to inspect at a
#: glance than Queued (a task not yet running), so it sits right after
#: Blocked/Proposed. `__main__.py`'s `_BOARD_GROUPS` is a byte-identical
#: duplicate (used by the delegated `inbox` CLI path) and must stay in sync.
#: Phase 7 follow-up (2026-09-29): a durable, operator-set pause hold
#: (``hold_reason``) is its OWN group -- distinct from system-``Suspended``
#: (a liveness-detected/force-stop outcome the system can recover from on its
#: own schedule) and from ``Blocked`` (the task itself is asking the operator
#: something). Sits right after Blocked: both need the operator's attention,
#: but a paused task is waiting on the operator to *unpause*, not to answer a
#: question. Keep this in sync with `task_query_cli.py`'s byte-identical
#: `_BOARD_GROUPS` tuple.
GROUPS = (
    "Blocked",
    "Paused",
    "Proposed",
    "Started",
    "Queued",
    "Suspended",
    "Submitted",
    "Completed",
    "Abandoned",
)
TERMINAL = frozenset({"Submitted", "Completed", "Abandoned"})
ACTIVITY_TTL_SECONDS = 90.0
_RELAY_ENDPOINT: str | None = None


def _local_machine() -> str | None:
    value = os.environ.get("AGENT_DISPATCH_SUPERVISE_MACHINE")
    root = install_dir()
    if not value:
        try:
            value = (root / "machine").read_text(encoding="utf-8").strip()
        except OSError:
            pass
    if not value:
        try:
            for line in (root / "supervisor.env").read_text(
                encoding="utf-8"
            ).splitlines():
                key, sep, candidate = line.partition("=")
                if sep and key.strip() == "AGENT_DISPATCH_SUPERVISE_MACHINE":
                    value = candidate.strip().strip("\"'")
                    break
        except OSError:
            pass
    return (value or platform.node() or "").strip().casefold() or None


def _endpoint() -> str:
    explicit = os.environ.get("AGENT_DISPATCH_URL")
    if explicit:
        return explicit.rstrip("/")
    root = Path(os.environ.get("AGENT_DISPATCH_ROUTING_DIR") or install_dir())
    try:
        data = json.loads((root / "active.json").read_text(encoding="utf-8"))
        active = data.get("active") or {}
        if active.get("bind") and active.get("port"):
            bind = str(active["bind"]).strip()
            if bind in {"0.0.0.0", "*"}:
                bind = "127.0.0.1"
            elif bind in {"::", "[::]"}:
                bind = "[::1]"
            return f"http://{bind}:{int(active['port'])}"
    except (OSError, ValueError, TypeError):
        pass
    endpoint = os.environ.get("AGENT_DISPATCH_ENDPOINT")
    if not endpoint:
        run_dir = Path(
            os.environ.get("AGENT_DISPATCH_RUN_DIR") or (root / "run")
        )
        try:
            endpoint = json.loads(
                (run_dir / "endpoint.json").read_text(encoding="utf-8")
            ).get("endpoint")
        except (OSError, ValueError, TypeError, AttributeError):
            endpoint = None
    if endpoint:
        value = str(endpoint).rstrip("/")
        return value if "://" in value else f"http://{value}"
    raise RuntimeError("agent-dispatch coordinator endpoint is unavailable")


def _group(task: dict) -> str:
    status = task.get("status")
    if status == "submitted":
        return "Submitted"
    if status == "completed":
        return "Completed"
    if status in {"abandoned", "dead_letter"}:
        return "Abandoned"
    if task.get("hold_reason"):
        return "Paused"
    if task.get("awaiting_steer"):
        return "Blocked"
    if status == "proposed":
        return "Proposed"
    if status == "queued":
        return "Queued"
    if status == "suspended":
        return "Suspended"
    return "Started"


def _activity(task: dict, now: float) -> str | None:
    value = task.get("activity")
    if value not in {"ACTIVE", "STALLED"}:
        return None
    try:
        observed = float(task.get("activity_updated_at"))
    except (TypeError, ValueError):
        return None
    return value if now - observed <= ACTIVITY_TTL_SECONDS else None


def _wt_live(activity: str | None, task: dict, now: float) -> str | None:
    """The Tasks pane's ``LIVE`` column (Phase 3): a compact, at-a-glance
    liveness string reusing ``activity``/``activity_updated_at`` -- fields
    already computed above, self-reported by a **headless** worker via
    ``agent-dispatch activity`` -- rather than a fresh subprocess/bridge probe
    (this board client is stdlib-only and re-runs on every Picker refresh, so
    a per-row liveness probe was ruled out; see the effort's Runbook).

    Returns ``"active"`` / ``"stalled Nm"`` (elapsed minutes since the last
    beat) for a headless body with a fresh signal, else ``None`` (blank) --
    including for a CLI-embodied task (item 3), which never calls
    ``set_activity`` and so has no cheap liveness signal available here. A
    blank cell is therefore "no headless liveness signal", not a confirmed
    "not live" -- a real interactive session may still be running.
    """
    if activity == "ACTIVE":
        return "active"
    if activity == "STALLED":
        try:
            observed = float(task.get("activity_updated_at"))
        except (TypeError, ValueError):
            return "stalled"
        minutes = max(0, int((now - observed) // 60))
        return f"stalled {minutes}m"
    return None


def _repo_name(value: object) -> str | None:
    text = str(value or "").rstrip("/")
    return text.rsplit("/", 1)[-1].removesuffix(".git") if text else None


#: Operator feedback 2026-09-29: standardize the Tasks pane row on the same
#: two-line shape Worktrees/CodeSpaces/Containers already use -- line 1 is
#: purely columnized (id, phase/status, stats, trailing claims), line 2 is
#: the title + a short activity phrase (`board_cli._subtitle_for_task`),
#: with an optional bracketed interface tag (mirroring Worktrees'
#: `[system]`/`[delegate]`/`[acp]` title-prefix convention in `derive.py`).
_MAX_HOLD_REASON_CHARS = 40


def _embodiment_tag(task: dict, wt_live: str | None) -> str | None:
    """The optional bracketed interface tag for the subtitle line, mirroring
    Worktrees' own title-prefix convention (`derive.norm`'s `_tag`): only the
    NON-default interface gets a visible mark. A headless body (pool/dedicated
    agent) is the default embodiment for a Task, so it stays untagged; a
    CLI-embodied (interactive, non-railroaded) session gets ``"cli"``.

    This is a best-effort HEURISTIC, not an authoritative `embodiment_kind`
    field (that would need a new board-facing field sourced from the
    coordinator's `local-body:`/`fleet-body:` spawn-reservation handle --
    still just a Runbook-tracked follow-on, not implemented here): a
    confirmed headless liveness signal (``wt_live`` non-``None``) means
    "definitely headless" -> no tag; an owned, live-status task with NO
    headless signal is assumed CLI-embodied (the only other embodiment Phase
    1/2 support) -> ``"cli"``. A task not yet embodied at all has no
    interface to tag.
    """
    status = task.get("status")
    if status not in ("claimed", "started"):
        return None
    if not task.get("owner_session_id"):
        return None
    return None if wt_live is not None else "cli"


def _activity_phrase(task: dict, wt_live: str | None) -> str:
    """The short, human-readable phrase after the subtitle's `` - `` --
    whatever is most useful to know about this task's activity right now.
    Prefers a real headless liveness signal (``wt_live``) when there is one;
    otherwise falls back to a phrase derived from the task's own lifecycle
    fields, in priority order: an operator hold, then awaiting-steer, then
    the raw status."""
    if wt_live is not None:
        return wt_live
    hold_reason = task.get("hold_reason")
    if hold_reason:
        reason = str(hold_reason).strip()
        if len(reason) > _MAX_HOLD_REASON_CHARS:
            reason = reason[: _MAX_HOLD_REASON_CHARS - 1].rstrip() + "…"
        return f"paused — {reason}" if reason else "paused"
    if task.get("awaiting_steer"):
        return "awaiting your steer"
    status = task.get("status")
    if status == "suspended":
        return "suspended — no live session"
    if status == "queued":
        return "queued for a worker" if task.get("pool") else "queued"
    if status == "proposed":
        return "awaiting approval"
    if status == "claimed":
        return "claimed, starting…"
    if status == "started":
        return "in progress"
    if status == "completed":
        return "completed"
    if status == "confirmed":
        return "confirmed"
    if status in ("abandoned", "dead_letter"):
        return "abandoned"
    return ""


def _subtitle_for_task(task: dict, *, wt_live: str | None) -> str:
    """Compose the Tasks pane's standardized second-line subtitle:
    ``[tag] <repo> <title> - <activity phrase>`` (tag and repo are each
    optional; the phrase is omitted only when genuinely empty). Mirrors the
    same shape Worktrees/CodeSpaces/Containers already use, so a Task row
    reads with the same at-a-glance rhythm as any other pivot's row."""
    parts: list[str] = []
    tag = _embodiment_tag(task, wt_live)
    if tag:
        parts.append(f"[{tag}]")
    repo_name = task.get("repo_name") or _repo_name(task.get("repo"))
    if repo_name:
        parts.append(str(repo_name))
    parts.append(str(task.get("title") or task.get("id") or "(untitled)"))
    prefix = " ".join(parts)
    phrase = _activity_phrase(task, wt_live)
    return f"{prefix} - {phrase}" if phrase else prefix


def _cli_openable(task: dict) -> bool:
    """Whether the task is eligible for Phase 7's interactive embodiment.

    Keep this aligned with ``interactive_embody.py``'s own contract:
    ``proposed`` is implicitly approved first, then only a plain unpooled
    ``queued`` task and ``suspended`` remain eligible. Anything already
    embodied (``claimed`` / ``started``), Blocked/awaiting-steer, terminal, or a
    queued task already assigned to a pool stays false.
    """
    if task.get("hold_reason"):
        return False
    status = task.get("status")
    if status == "proposed":
        return True
    if status == "suspended":
        return False if task.get("awaiting_steer") else True
    if status == "queued":
        return not task.get("awaiting_steer") and not task.get("pool")
    return False


def _charter_for_task(task: dict) -> dict:
    """Compose the read-only ``charter`` card: a short description plus
    structured metadata, and the raw prompt verbatim -- so the operator can
    see "what this task is" the same way they can already read a steering
    card's raw prose (Phase 7's own charter note). Never mutates the task.
    """
    repo_name = task.get("repo_name") or _repo_name(task.get("repo"))
    labels = task.get("labels") or []
    if isinstance(labels, str):
        labels = [labels]
    meta = "\n".join(
        [
            f"- Repo: `{repo_name or 'unknown'}`",
            f"- Source: `{task.get('source') or 'unknown'}`",
            f"- Registrar/origin: `{task.get('origin_ref') or 'none'}`",
            f"- Target machine: `{task.get('target_machine') or 'any'}`",
            "- Labels: `"
            + (", ".join(str(label) for label in labels) if labels else "none")
            + "`",
        ]
    )
    goal = task.get("goal")
    goal_text = goal.strip() if isinstance(goal, str) and goal.strip() else None
    done_criteria = task.get("done_criteria")
    done_text = (
        done_criteria.strip()
        if isinstance(done_criteria, str) and done_criteria.strip()
        else None
    )
    prompt = task.get("prompt")
    prompt_text = prompt.strip() if isinstance(prompt, str) else ""
    body = "\n\n".join(
        [
            meta,
            "## Goal\n"
            + (goal_text or "_no durable goal recorded — see the raw prompt below_"),
            "## Done criteria\n" + (done_text or "_not specified_"),
            "## Raw prompt\n```\n" + prompt_text + "\n```",
        ]
    )
    return {
        "title": task.get("title") or task.get("id"),
        "status": task.get("status"),
        "link": None,
        "body": body,
    }


def _sort_timestamp(task: dict) -> float:
    try:
        return float(task.get("updated_at") or task.get("created_at") or 0)
    except (TypeError, ValueError):
        return 0.0


def _relay_fetch(repo: str, worktree_id: str) -> dict | None:
    endpoint = _RELAY_ENDPOINT or _endpoint()
    request = urllib.request.Request(
        f"{endpoint}/worktree-status-relay?"
        f"{urllib.parse.urlencode({'repo': repo, 'worktree_id': worktree_id})}"
    )
    token = os.environ.get("AGENT_DISPATCH_TOKEN")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    return payload if isinstance(payload, dict) else None


def _relay_fetch_many(
    refs: list[tuple[str, str]], *, endpoint: str | None = None
) -> dict[tuple[str, str], dict | None]:
    """``endpoint`` pins the request to a caller-resolved coordinator base
    URL (e.g. a live relay connection's own :class:`DispatchClient`) instead
    of re-resolving via ``_RELAY_ENDPOINT``/``_endpoint()`` -- required so a
    cutover landing between a relay's SSE connection and this call can never
    point the two at different coordinator generations."""
    if not refs:
        return {}
    endpoint = endpoint or _RELAY_ENDPOINT or _endpoint()
    payload = json.dumps(
        [{"repo": repo, "worktree_id": worktree_id} for repo, worktree_id in refs]
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{endpoint}/worktree-status-relays",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    token = os.environ.get("AGENT_DISPATCH_TOKEN")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=5) as response:
        rows = json.loads(response.read().decode("utf-8"))
    return {
        (row["repo"], row["worktree_id"]): row.get("entry")
        for row in rows
        if isinstance(row, dict)
    }


def _build(
    tasks: list[dict],
    *,
    machine: str,
    recent_mins: int,
    relay_fetch=None,
    relay_fetch_many=None,
) -> list[dict]:
    now = time.time()
    cutoff = now - max(0, recent_mins) * 60
    rows: list[dict] = []
    relay_cache: dict[tuple[str, str], dict | None] = {}
    relay_keys: list[tuple[str, str]] = []

    for task in tasks:
        target = task.get("target_machine")
        if target and str(target).casefold() != machine.casefold():
            continue
        group = _group(task)
        if group in TERMINAL:
            try:
                terminal_at = float(
                    task.get("completed_at") or task.get("updated_at") or 0
                )
            except (TypeError, ValueError):
                terminal_at = 0
            if terminal_at < cutoff:
                continue
        row = dict(task)
        row["group"] = group
        row["activity"] = _activity(task, now)
        row["wt_live"] = _wt_live(row["activity"], task, now)
        _claimed_machine, claimed_worktree = claimed_identity(task)
        if claimed_worktree and not row.get("target_worktree"):
            row["target_worktree"] = claimed_worktree
        # PR #2913 review: the manifest's mutating/card actions gate on these
        # booleans -- an unpopulated field degrades to falsy in the picker's
        # `when` matcher, so leaving one out doesn't crash anything, but it
        # DOES silently hide (or, worse, wrongly show) the action it gates.
        # Populate every field the manifest actually depends on today from
        # data already on the task; only `cli_openable` and `has_charter`
        # stay hard-`False` pending their own follow-on phases (see the two
        # comments below for exactly why).
        row["has_worktree"] = bool(claimed_worktree)
        # `embodied` gates Pause/Force-stop: true only for a task with a
        # genuinely LIVE session (`started`). A "Blocked" task's real status
        # is `suspended` (see `set_card`'s own docstring: posting a card with
        # a form atomically suspends the task so its worker process CAN be
        # stopped) -- so `embodied` must NOT include Blocked, or force-stop
        # would be offered on a task with no live session to stop.
        row["embodied"] = task.get("status") == "started"
        row["held"] = bool(task.get("hold_reason"))
        # Phase 7 open-cli wiring: the Picker's dedicated `embody-cli`
        # internal verb now shells out to `agent-dispatch embody
        # --interactive`, so this row-level gate can finally mirror the
        # transaction's own status contract instead of staying hard-false.
        row["cli_openable"] = _cli_openable(task)
        # `has_charter`/`charter`: every task carries at least a title +
        # prompt, so the charter card is always populated (2026-09-29,
        # closes the Phase 7 gap this comment used to document as open).
        row["has_charter"] = bool(task.get("title"))
        row["charter"] = _charter_for_task(task)
        row.setdefault("repo_name", _repo_name(task.get("repo")))
        # Operator feedback 2026-09-29: standardize on the Worktrees/
        # CodeSpaces/Containers two-line row shape -- `columns` (line 1)
        # stays pure stats, `subtitle` (line 2) carries the title + an
        # activity phrase (and an optional `[cli]` interface tag).
        row["subtitle"] = _subtitle_for_task(row, wt_live=row["wt_live"])
        repo = str(row.get("repo") or "")
        if repo and claimed_worktree:
            relay_keys.append((repo, claimed_worktree))
        progress = row.get("latest_progress")
        if isinstance(progress, str) and progress:
            try:
                row["latest_progress"] = json.loads(progress)
            except (ValueError, TypeError):
                pass
        rows.append(row)
    try:
        if relay_keys:
            fetch_many = relay_fetch_many or _relay_fetch_many
            relay_cache = fetch_many(list(dict.fromkeys(relay_keys)))
    except Exception:
        relay_cache = {}
    if not relay_cache and relay_fetch is not None:
        for repo, worktree_id in dict.fromkeys(relay_keys):
            try:
                relay_cache[(repo, worktree_id)] = relay_fetch(repo, worktree_id)
            except Exception:
                relay_cache[(repo, worktree_id)] = None
    for row in rows:
        _claimed_machine, claimed_worktree = claimed_identity(row)
        repo = str(row.get("repo") or "")
        row.update(
            board_fields_for_task(
                row,
                relay_cache.get((repo, claimed_worktree))
                if repo and claimed_worktree
                else None,
                now=now,
            )
        )
    rows.sort(
        key=lambda task: (
            GROUPS.index(task["group"]),
            -_sort_timestamp(task),
        )
    )
    return rows


def _fetch_rows_delegated(args: argparse.Namespace) -> list[dict]:
    """Cross-machine path (this host supervises ``args.machine`` as a peer):
    run ``agent_dispatch inbox --board`` as a child process, capturing and
    parsing its JSON-array stdout instead of inheriting the pipe -- so a
    ``--stream``/``--subscribe`` caller can frame the result as NDJSON (and,
    with ``--subscribe``, re-run it on a timer) rather than just forwarding
    the child's one-shot output verbatim. Raises on a non-zero exit or
    malformed output; the caller decides how to surface that."""
    python = sys.executable
    command = [
        windowless_python(python),
        "-m",
        "agent_dispatch",
        "inbox",
        "--machine",
        args.machine,
        "--board",
        "--recent-mins",
        str(args.recent_mins),
        "--limit",
        str(args.limit),
    ]
    if args.label:
        command.extend(["--label", args.label])
    env = dict(os.environ)
    env.update(windowless_python_env(python))
    result = subprocess.run(
        command, check=False, env=env, capture_output=True, text=True,
        **_no_window_kwargs(),
    )
    if result.returncode != 0:
        raise RuntimeError(
            (result.stderr or "").strip()
            or f"delegated inbox query exited {result.returncode}"
        )
    return json.loads(result.stdout or "[]")


def _fetch_raw_tasks_direct(
    args: argparse.Namespace, *, endpoint: str | None = None
) -> list[dict]:
    """Network-only half of :func:`_fetch_rows_direct`: fetch this machine's
    own coordinator ``/tasks`` endpoint and return the raw task dicts. The
    resolved ``endpoint`` (``endpoint`` itself when given, otherwise a fresh
    ``_endpoint()`` resolution) is recorded in ``_RELAY_ENDPOINT`` as a side
    effect, so a subsequent :func:`_relay_fetch_many` call with no explicit
    ``endpoint`` of its own reuses this same resolution instead of
    re-resolving ``active.json`` independently. Split out so Phase 3a's
    relay (``board_relay.py``) can drive its own zero-network local
    recompute tick by re-running :func:`_build` against the last-fetched
    raw tasks instead of re-fetching -- never imported by the plain
    one-shot/poll path, which keeps calling the combined
    :func:`_fetch_rows_direct` below unchanged.

    ``endpoint``, when given, pins the request to a caller-resolved
    coordinator base URL (a live relay connection's own
    :class:`DispatchClient`) instead of re-resolving ``active.json`` here --
    required so a cutover landing between the relay's SSE connection and
    this fetch can never read a different coordinator generation's tasks
    than the one the connection's event bus will publish mutations for."""
    query = {
        "status": (
            "proposed,queued,claimed,started,suspended,"
            "submitted,completed,abandoned,dead_letter"
        ),
        "limit": str(args.limit),
    }
    if args.label:
        query["label"] = args.label
    endpoint = endpoint or _endpoint()
    url = f"{endpoint}/tasks?{urllib.parse.urlencode(query)}"
    request = urllib.request.Request(url)
    token = os.environ.get("AGENT_DISPATCH_TOKEN")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=3) as response:
        tasks = json.loads(response.read().decode("utf-8"))
    global _RELAY_ENDPOINT
    _RELAY_ENDPOINT = endpoint
    return tasks


def _fetch_rows_direct(args: argparse.Namespace) -> list[dict]:
    """Direct path (this host IS ``args.machine``, the common case): query this
    machine's own coordinator ``/tasks`` endpoint and run the board through
    :func:`_build`. A standalone fetch (rather than reusing ``main``'s inline
    one-shot query) because ``--subscribe`` needs to call it repeatedly from
    inside one long-lived process, never re-exec'ing the CLI per re-scan.
    Raises on any fetch/parse failure -- same contract as
    :func:`_fetch_rows_delegated`."""
    tasks = _fetch_raw_tasks_direct(args)
    return _build(
        tasks,
        machine=args.machine,
        recent_mins=args.recent_mins,
        relay_fetch_many=_relay_fetch_many,
    )


def _fetch_rows(args: argparse.Namespace) -> list[dict]:
    """Resolve the board rows the right way for this invocation: delegate to
    the supervisor's cross-machine inbox when ``--machine`` names a peer this
    host supervises, otherwise query this machine's own coordinator
    directly. Used only by the ``--stream``/``--subscribe`` path (D2); the
    plain one-shot path below has its own long-standing inline fetch."""
    local = _local_machine()
    if local and local != args.machine.casefold():
        return _fetch_rows_delegated(args)
    return _fetch_rows_direct(args)


def _emit_frame(obj: dict, out) -> bool:
    """Write one NDJSON frame, flushing immediately so the Picker paints
    progressively. Returns False (never raises) once the reader has closed
    the pipe, so the caller can stop cleanly instead of crashing on a broken
    pipe."""
    try:
        out.write(json.dumps(obj, default=str) + "\n")
        out.flush()
        return True
    except (BrokenPipeError, OSError):
        return False


def _diff_rows(
    prev: list[dict], curr: list[dict], *, id_key: str = "id"
) -> tuple[list[dict], list[str]]:
    """Diff two board snapshots by ``id_key`` for a ``--subscribe`` re-scan.

    Returns ``(deltas, removed_ids)`` -- whole-row ``delta`` entries for ids
    that are new or whose content changed, and ids present before but gone
    now. Whole-row granularity matches the Picker's own ``delta``/``removed``
    envelope contract (``tasks.py``): the consumer replaces/removes by id."""
    prev_by = {str(r.get(id_key)): r for r in prev if r.get(id_key) is not None}
    curr_by = {str(r.get(id_key)): r for r in curr if r.get(id_key) is not None}
    deltas = [
        r for r in curr
        if r.get(id_key) is not None and prev_by.get(str(r.get(id_key))) != r
    ]
    removed = [rid for rid in prev_by if rid not in curr_by]
    return deltas, removed


#: Default seconds between ``--subscribe`` re-scans. Tighter than
#: agent-codespaces' pool (5s): task state (steer requests, progress,
#: completion) changes on a human-interaction cadence, and each re-scan here
#: is a single lightweight coordinator HTTP call (or, cross-machine, one
#: child-process inbox query) -- not a `gh` roster call.
DEFAULT_SUBSCRIBE_INTERVAL = 2.0


def poll_loop(
    args: argparse.Namespace,
    out,
    prev: list[dict],
    interval: float,
    *,
    sleep=time.sleep,
) -> int:
    """Phase 1's original poll-and-diff ``--subscribe`` loop: every
    ``interval`` seconds, re-fetch via :func:`_fetch_rows` and emit the diff
    vs. ``prev`` as ``delta``/``removed`` frames. This is the universal
    degraded path: used directly whenever Phase 3a's relay doesn't apply
    (a delegated/cross-machine board, or a daemon that never advertises
    ready-frame support -- see :func:`_run_stream`), and reused by
    ``board_relay.py`` itself for a transient SSE failure's immediate
    fallback and for the permanent fallback once its own bounded reconnect
    retries are exhausted -- one implementation of "poll and diff," not a
    second copy that could drift from this one. Returns 0 on a clean
    ``KeyboardInterrupt`` or once the reader closes the pipe (matching
    ``_run_stream``'s own contract); never returns otherwise."""
    try:
        while True:
            sleep(interval)
            try:
                curr = _fetch_rows(args)
            except Exception:
                # A transient re-fetch failure (coordinator hiccup, delegated
                # subprocess blip) must not kill the live channel -- skip
                # this tick and try again next time.
                continue
            deltas, removed = _diff_rows(prev, curr)
            for entry in deltas:
                if not _emit_frame({"type": "delta", "entry": entry}, out):
                    return 0
            for rid in removed:
                if not _emit_frame({"type": "removed", "id": rid}, out):
                    return 0
            prev = curr
    except KeyboardInterrupt:
        return 0


def _run_stream(args: argparse.Namespace) -> int:
    """Emit the Tasks board as the registered-pivot NDJSON envelope (D2):
    ``begin`` -> a ``row`` per task -> ``done``. With ``--subscribe`` the
    channel is then held open and live-updated one of two ways: Phase 3a's
    event-woken relay (``board_relay.py``) for the direct, non-delegated
    path when the coordinator advertises support, or the original Phase 1
    poll-and-diff loop (:func:`poll_loop`) otherwise -- a delegated
    ``--machine`` board, a daemon that doesn't advertise ready-frame support
    (no observable subscription barrier to reconcile against -- see
    ``board_relay.py``'s own docstring), or ``httpx`` genuinely unavailable.
    Only the initial fetch failing is fatal (emits ``error``, matching the
    one-shot path's exit-1 contract)."""
    out = sys.__stdout__
    try:
        rows = _fetch_rows(args)
    except Exception as exc:
        _emit_frame({"type": "error", "message": str(exc)[:200]}, out)
        return 1
    if not _emit_frame({"type": "begin", "count": len(rows)}, out):
        return 0
    for row in rows:
        if not _emit_frame({"type": "row", "entry": row}, out):
            return 0
    if not _emit_frame({"type": "done", "count": len(rows)}, out):
        return 0

    if not getattr(args, "subscribe", False):
        return 0

    interval = max(
        0.5,
        float(
            getattr(args, "interval", DEFAULT_SUBSCRIBE_INTERVAL)
            or DEFAULT_SUBSCRIBE_INTERVAL
        ),
    )

    # Phase 3a scope: the direct (local) path only -- this machine's own
    # coordinator `/events` stream describes only *this* machine's tasks, so
    # relaying it for a delegated `--machine` board would silently mix in
    # the wrong machine's events (or none at all). A delegated board keeps
    # the unmodified poll loop, full stop, never attempted below.
    local = _local_machine()
    is_direct = bool(local and local == args.machine.casefold())
    if is_direct:
        try:
            from . import board_relay
        except ImportError:
            board_relay = None  # httpx (or the module itself) unavailable
        if board_relay is not None:
            try:
                return board_relay.run_relay(
                    args, out, initial_rows=rows, interval=interval
                )
            except board_relay.RelayUnavailable:
                # The daemon doesn't advertise ready-frame support at all --
                # no observable subscription barrier exists to reconcile
                # against (`stream_events()` is a lazy generator), so don't
                # attempt a half-optimized relay with an unclosed startup
                # race. Fall back to the unmodified poll loop for this
                # connection's whole remaining lifetime.
                pass

    return poll_loop(args, out, rows, interval)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent-dispatch-board")
    parser.add_argument("--machine", required=True)
    parser.add_argument("--recent-mins", type=int, default=120)
    parser.add_argument("--label")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument(
        "--stream", dest="stream", action="store_true",
        help="Emit the registered-pivot NDJSON envelope (begin -> row per "
             "task -> done) so the Picker's Tasks pivot paints progressively "
             "(D2).",
    )
    parser.add_argument(
        "--subscribe", dest="subscribe", action="store_true",
        help="With --stream, hold the channel open and emit live "
             "delta/removed frames from a periodic re-scan so an open pivot "
             "updates in place (D2).",
    )
    parser.add_argument(
        "--interval", dest="interval", type=float,
        default=DEFAULT_SUBSCRIBE_INTERVAL,
        help="Seconds between --subscribe re-scans "
             f"(default: {DEFAULT_SUBSCRIBE_INTERVAL}).",
    )
    args = parser.parse_args(argv)

    if args.stream:
        return _run_stream(args)

    local = _local_machine()
    if local and local != args.machine.casefold():
        python = sys.executable
        command = [
            windowless_python(python),
            "-m",
            "agent_dispatch",
            "inbox",
            "--machine",
            args.machine,
            "--board",
            "--recent-mins",
            str(args.recent_mins),
            "--limit",
            str(args.limit),
        ]
        if args.label:
            command.extend(["--label", args.label])
        env = dict(os.environ)
        env.update(windowless_python_env(python))
        return subprocess.run(
            command, check=False, env=env, **_no_window_kwargs()
        ).returncode

    query = {
        "status": (
            "proposed,queued,claimed,started,suspended,"
            "submitted,completed,abandoned,dead_letter"
        ),
        "limit": str(args.limit),
    }
    if args.label:
        query["label"] = args.label
    try:
        endpoint = _endpoint()
        url = f"{endpoint}/tasks?{urllib.parse.urlencode(query)}"
        request = urllib.request.Request(url)
        token = os.environ.get("AGENT_DISPATCH_TOKEN")
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(request, timeout=3) as response:
            tasks = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        print(f"agent-dispatch-board: {exc}", file=sys.stderr)
        return 1
    global _RELAY_ENDPOINT
    _RELAY_ENDPOINT = endpoint
    json.dump(
        _build(
            tasks,
            machine=args.machine,
            recent_mins=args.recent_mins,
            relay_fetch_many=_relay_fetch_many,
        ),
        sys.stdout,
        indent=2,
        sort_keys=True,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
