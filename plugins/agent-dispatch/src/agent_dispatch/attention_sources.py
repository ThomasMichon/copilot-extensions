"""Attention sources and the aggregator over them.

Built in: ``dispatch`` (this coordinator's tasks). External **command sources**
are registered on this machine (``agent-dispatch attention source add``) under a
name that is their identity; each prints a source-result envelope, and the
aggregator stamps and validates it at the boundary (see
:mod:`agent_dispatch.attention_contract`). Every source runs under its own
timeout; a timeout or any contract violation is that source's ``failed``,
never a crash and never an empty source.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx

from . import attention_contract as ac
from .client import DispatchError
from .handoff_claim_release import handoff_is_local, handoff_worktree, is_handoff_task

BUILTIN_SOURCES = ("bridge", "dispatch", "pr")
DEFAULT_TIMEOUT = 20.0
MAX_TIMEOUT = 120.0
#: A command source's stdout and stderr together (characters); more fails that source.
MAX_OUTPUT = 1024 * 1024
#: How long past its own timeout a command source's reader is waited for: its
#: runner's whole tree cleanup (a 5 s SIGTERM grace, a 2 s post-SIGKILL reap,
#: a start-identity probe of up to 5 s) plus scheduling margin.
COMMAND_CLEANUP_SECONDS = 15.0
#: Lifecycle states that can carry an attention condition.
_OPEN_STATES = "proposed,queued,claimed,started,suspended,submitted"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def iso(value: Any, fallback: str) -> str:
    """A coordinator timestamp (epoch seconds, or ISO-8601) as canonical UTC."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="seconds")
    try:
        return ac.canonical_time(value)
    except ac.ContractError:
        return fallback


# -- the dispatch source ---------------------------------------------------------


def _valid_form(form: Any) -> bool:
    """A card's form becomes ``input`` only in the exact steering field-list
    shape; any other stays reachable through the item's ``card show`` action
    rather than failing the whole dispatch read."""
    try:
        ac.check_input(form)
    except ac.ContractError:
        return False
    return True


def _task_item(task: dict[str, Any], read_at: str,
               cli: tuple[str, ...] | None = ("agent-dispatch",),
               now: float | None = None, machine: str | None = None) -> dict[str, Any] | None:
    """One task's item, coalescing its conditions to the worst: an operator ask
    (``awaiting_steer``), an operator hold (``hold_reason``), or a self-tracked
    completion claim awaiting confirmation (``submitted``). ``submitted`` is
    concluded, so it wins over a stale steering flag (``steer submit`` refuses a
    concluded task); one with an ``evaluator_ref`` waits on its evaluator, not
    the operator, and ``completed`` is never an item. A handoff baton is
    different: its pickup is its completion, so a ``submitted`` one is spent
    (never a review), and one no session has picked up for longer than the
    handoff threshold is ``stalled`` -- its predecessor already stopped, so the
    work waits on a successor. ``cli`` is the invocation
    that reaches the coordinator this read came from (its ``--url``/``--shared``,
    never a token), so the action runs as-is; ``None`` (a read authenticated only
    by a ``--token`` argument) means no action could, so none is offered."""
    task_id, title = str(task.get("id") or ""), ac.one_line(task.get("title"), 120)
    status = task.get("status")
    card = task.get("card") if isinstance(task.get("card"), dict) else {}
    if not task_id:
        return None
    handoff = is_handoff_task(task)
    waited = _unpicked_handoff_age(task, now) if handoff else None
    extra: dict[str, Any] = {}
    if status == "submitted":
        if task.get("evaluator_ref") or handoff:
            return None
        state, reason = "review", f"completion awaiting confirmation: {title}"
    elif task.get("awaiting_steer"):
        state, reason = "awaiting_input", f"awaiting your answer: {title}"
        form = card.get("request_input")
        extra = {"input": form} if _valid_form(form) else {}
    elif task.get("hold_reason"):
        state, reason = "blocked", f"held ({ac.one_line(task['hold_reason'], 60)}): {title}"
    elif waited is not None:
        state, reason = "stalled", f"no session has picked up this handoff for {waited / 60:.0f} min: {title}"
    else:
        return None
    # The steering card for an ask; the task itself (its result, its hold) otherwise.
    view = ["card", "show", task_id] if state == "awaiting_input" else ["show", task_id]
    actions = [{"verb": "show", "argv": [*cli, *view]}] if cli else []
    if state == "stalled":
        # Resume only a worktree on this machine (``machine``): another's id means nothing here.
        here = handoff_worktree(task) if handoff_is_local(task, machine) else None
        actions = _stalled_handoff_actions(task_id, title, here, cli) + actions
    item = {
        "schema": ac.SCHEMA, "entity": "task", "entity_ref": task_id, "lifecycle_state": status,
        "display_state": state, "severity": ac.SEVERITY[state], "reason": ac.one_line(reason),
        "created_at": None, "updated_at": iso(task.get("updated_at"), read_at),
        "confidence": "reported", "actions": actions, "source": "dispatch",
        "id": ac.make_id("dispatch", "task", task_id), "also": [], **extra,
    }
    return item


#: The most open tasks one read fetches. A read that hits it may have missed
#: older tasks, so it says so (``uncertain``) rather than claiming a full queue.
DISPATCH_READ_LIMIT = 5000
#: Buildup thresholds (seconds; strictly greater counts; ``0`` turns that half off).
QUEUED_AFTER_ENV = "AGENT_DISPATCH_ATTENTION_QUEUED_AFTER_SECS"
HELD_LIVE_AFTER_ENV = "AGENT_DISPATCH_ATTENTION_HELD_LIVE_AFTER_SECS"
DEFAULT_STALLED_AFTER = 1800.0
#: Seconds an unclaimed handoff baton may wait for a successor (``0`` turns it off).
HANDOFF_AFTER_ENV = "AGENT_DISPATCH_ATTENTION_HANDOFF_AFTER_SECS"
DEFAULT_HANDOFF_AFTER = 600.0
#: Seconds, from the start of a dispatch read (its task list included), within
#: which every per-lane backlog read must *finish*, inside the source's own
#: deadline. A lane that can't, or whose read fails, counts toward
#: ``uncertain``: backlog probing never fails the task items already read.
BACKLOG_BUDGET = 19.0
#: The dispatch client's per-request timeout: a lane read starts only when it
#: can still finish inside the budget.
_LANE_REQUEST_TIMEOUT = 10.0


def _threshold(env: str, default: float = DEFAULT_STALLED_AFTER) -> float:
    try:
        value = float(os.environ.get(env, default))
    except ValueError:
        return default
    return value if value >= 0 else default


def _unpicked_handoff_age(task: dict[str, Any], now: float | None) -> float | None:
    """Seconds a handoff baton has waited unclaimed, when past the handoff
    threshold; ``None`` otherwise (claimed, too young, or the age is unknown)."""
    after = _threshold(HANDOFF_AFTER_ENV, DEFAULT_HANDOFF_AFTER)
    created = task.get("created_at")
    if (not after or task.get("status") not in ("proposed", "queued") or task.get("owner")
            or not isinstance(created, (int, float)) or isinstance(created, bool)):
        return None
    age = (time.time() if now is None else now) - created
    return age if age > after else None


#: The reason an attention-queue abandon records on the task.
ABANDON_REASON = "abandoned from the attention queue"


def _stalled_handoff_actions(task_id: str, title: str, worktree: str | None,
                             cli: tuple[str, ...] | None) -> list[dict[str, Any]]:
    """What the operator can do about a baton nobody picked up: start a successor
    in its worktree (the default; only for a worktree on this machine), or
    abandon it. The ``show`` follows."""
    from .handoff_fallback_seed import build_fallback_seed

    actions = []
    if worktree:
        actions.append({"verb": "resume", "argv": ["agent-worktrees", "embody", "--worktree-id", worktree,
                                                   "--seed", build_fallback_seed(task_id, title)]})
    if cli:
        actions.append({"verb": "abandon",
                        "argv": [*cli, "abandon", task_id, "--permit", "--reason", ABANDON_REASON]})
    return actions


#: Seconds one worktree's pending-handoff lookup may take.
HANDOFF_PENDING_TIMEOUT = 8.0
#: Worktree ledgers read at once (each is one agent-worktrees process).
HANDOFF_PENDING_CONCURRENCY = 4


def pending_handoff_tokens(worktree: str, timeout: float = HANDOFF_PENDING_TIMEOUT) -> set[str] | None:
    """The handoff tokens ``worktree``'s own ledger (agent-worktrees, the
    authority for which handoff a worktree still waits on) lists as pending.
    ``None`` when that can't be read for certain (no agent-worktrees, an
    untracked worktree, a failed reply, or any malformed entry): the batons
    are then taken at their word."""
    from .procutil import run_agent_worktrees_capture

    done = run_agent_worktrees_capture("head-session", "--worktree", worktree, "--json", timeout=timeout)
    if done is None or done.returncode != 0:
        return None
    try:
        data = json.loads(done.stdout)
    except ValueError:
        return None
    pending = data.get("pending_handoffs") if isinstance(data, dict) and data.get("tracked") is True else None
    if not isinstance(pending, list):
        return None
    tokens = [p.get("token") if isinstance(p, dict) else None for p in pending]
    if not all(isinstance(t, str) and t for t in tokens):
        return None
    return set(tokens)


class _PendingHandoffs:
    """A stalled baton needs the operator only while its worktree still waits on
    it. One its ledger no longer lists was picked up, replaced by a later
    handoff, or cancelled -- or was only saved, never handed over -- so it is no
    item. Each worktree's ledger is read once, a few at a time, alongside the
    read's lane reads; a baton whose ledger hasn't answered by the read's
    deadline keeps its item."""

    def __init__(self, items: list[dict[str, Any]], tasks: dict[str, dict[str, Any]],
                 lookup: Callable[[str], set[str] | None], machine: str | None = None) -> None:
        self.lookup = lookup
        self.ledgers: dict[str, set[str] | None] = {}
        self.closed = False
        stalled = {i["entity_ref"] for i in items if i["entity"] == "task" and i["display_state"] == "stalled"}
        # Only this machine's ledgers can be read: a baton pinned elsewhere keeps its item.
        self.batons = {t: handoff_worktree(tasks[t]) for t in stalled
                       if t in tasks and handoff_is_local(tasks[t], machine)}
        self.todo = sorted({wt for wt in self.batons.values() if wt})
        self.lock = threading.Lock()
        self.threads = [threading.Thread(target=self._work, daemon=True, name=f"attention-handoff-{n}")
                        for n in range(min(HANDOFF_PENDING_CONCURRENCY, len(self.todo)))]
        for thread in self.threads:
            thread.start()

    def _work(self) -> None:
        while True:
            with self.lock:
                if self.closed or not self.todo:  # past the read's deadline: start no more lookups
                    return
                worktree = self.todo.pop()
            try:
                tokens = self.lookup(worktree)
            except Exception:  # noqa: BLE001 -- an unreadable ledger keeps its batons
                tokens = None
            with self.lock:
                self.ledgers[worktree] = tokens

    def keep(self, items: list[dict[str, Any]], deadline: float) -> list[dict[str, Any]]:
        for thread in self.threads:
            thread.join(max(0.0, deadline - time.monotonic()))
        with self.lock:
            self.closed = True
            ledgers = dict(self.ledgers)

        def gone(task_id: str) -> bool:
            tokens = ledgers.get(self.batons.get(task_id) or "")
            return tokens is not None and task_id not in tokens

        return [i for i in items if not (i["entity"] == "task" and i["entity_ref"] in self.batons
                                         and gone(i["entity_ref"]))]


def _queue_item(repo: str, backlog: dict[str, Any], read_at: str,
                cli: tuple[str, ...] | None) -> dict[str, Any] | None:
    """An undraining lane (the vision's *buildup-is-a-health-signal*): the oldest
    queued task waited too long, or a **live** owner stopped progressing. Held
    tasks whose owner is ``unknown`` or ``gone`` never count."""
    queued_after, held_after = _threshold(QUEUED_AFTER_ENV), _threshold(HELD_LIVE_AFTER_ENV)
    queued_age, held_age = backlog.get("oldest_queued_age"), backlog.get("oldest_held_live_age")
    queued_stalled = bool(queued_after) and isinstance(queued_age, (int, float)) and queued_age > queued_after
    held_stalled = bool(held_after) and isinstance(held_age, (int, float)) and held_age > held_after
    if not (queued_stalled or held_stalled):
        return None
    reason = (f"{repo} isn't draining: {backlog.get('queued', 0)} queued (oldest {queued_age or 0:.0f}s), "
              f"{backlog.get('held_live', 0)} held live (oldest without progress {held_age or 0:.0f}s)")
    return {
        "schema": ac.SCHEMA, "entity": "queue", "entity_ref": repo, "lifecycle_state": None,
        "display_state": "stalled", "severity": ac.SEVERITY["stalled"], "reason": ac.one_line(reason),
        "created_at": None, "updated_at": read_at, "confidence": "reported",
        "actions": [{"verb": "show", "argv": [*cli, "list", "--repo", repo, "--status", "queued,claimed,started"]}]
        if cli else [],
        "source": "dispatch", "id": ac.make_id("dispatch", "queue", repo), "also": [],
    }


def read_dispatch(client_factory: Callable[[], Any], read_at: str,
                  limit: int = DISPATCH_READ_LIMIT,
                  cli: tuple[str, ...] | None = ("agent-dispatch",),
                  backlog_budget: float = BACKLOG_BUDGET,
                  pending_lookup: Callable[[str], set[str] | None] | None = None,
                  machine: str | None = None) -> dict[str, Any]:
    """``machine`` is this machine's name (resolved when a stalled baton names
    a ``target_machine``): batons pinned elsewhere are neither checked against
    this machine's ledgers nor offered a local ``resume``."""
    started = time.monotonic()
    with client_factory() as client:
        tasks = list(client.list(repo=None, status=_OPEN_STATES, limit=limit) or [])
        if machine is None and any(is_handoff_task(t) and t.get("target_machine") for t in tasks):
            from .remote_dispatch import local_machine

            machine = local_machine()
        items = [i for i in (_task_item(t, read_at, cli, now=time.time(), machine=machine) for t in tasks) if i]
        pending = _PendingHandoffs(items, {str(t.get("id")): t for t in tasks},
                                   pending_lookup or pending_handoff_tokens, machine)
        lanes = sorted({t["repo"] for t in tasks
                        if t.get("repo") and t.get("status") in ("queued", "claimed", "started")})
        backlogs, unread = {}, 0
        for repo in lanes:
            if time.monotonic() + _LANE_REQUEST_TIMEOUT > started + backlog_budget:
                unread += 1
                continue
            try:
                backlogs[repo] = (client.health(repo=repo) or {}).get("backlog") or {}
            except (DispatchError, httpx.HTTPError, OSError, ValueError):
                unread += 1
    items = pending.keep(items, started + backlog_budget)
    items += [i for i in (_queue_item(r, b, read_at, cli) for r, b in backlogs.items()) if i]
    uncertain = unread + (1 if len(tasks) >= limit else 0)
    return {"items": items, "status": "uncertain" if uncertain else "ok", "uncertain": uncertain,
            "read_at": read_at}


# -- command sources ---------------------------------------------------------------


def registry_path() -> Path:
    from .config import install_dir

    return install_dir() / "attention-sources.json"


def load_registrations(path: Path | None = None) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]]]:
    """``({name: {argv, timeout}}, config_errors)``. A registration that isn't a
    valid source (a bad or built-in name, no argv, a bad timeout) is rejected into
    ``config_errors`` -- never listed as a source under that name."""
    path = path or registry_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, []
    except (OSError, ValueError) as exc:
        return {}, [{"name": "*", "error": ac.one_line(f"{path} is unreadable: {exc}")}]
    sources = raw.get("sources") if isinstance(raw, dict) else None
    if not isinstance(sources, dict):
        return {}, [{"name": "*", "error": ac.one_line(f"{path} has no sources object")}]
    valid, errors = {}, []
    for name, spec in sources.items():
        error = registration_error(name, spec)
        if error:
            errors.append({"name": str(name), "error": error})
        else:
            valid[name] = {"argv": list(spec["argv"]), "timeout": float(spec.get("timeout", DEFAULT_TIMEOUT))}
    return valid, errors


def registration_error(name: Any, spec: Any) -> str | None:
    if not isinstance(name, str) or not ac.SOURCE_NAME.match(name):
        return "a source name must match [a-z0-9-]+"
    if name in BUILTIN_SOURCES:
        return f"{name!r} is a built-in source's name"
    if not isinstance(spec, dict):
        return "a registration must be an object"
    argv = spec.get("argv")
    if not (isinstance(argv, list) and argv and all(isinstance(a, str) and a for a in argv)):
        return "argv must be a non-empty list of strings"
    if not os.path.isabs(argv[0]):
        # Resolved at registration, never at read time: a bare or relative command
        # would otherwise resolve against whatever checkout the read runs in.
        return "the command must be an absolute path (`attention source add` resolves it)"
    timeout = spec.get("timeout", DEFAULT_TIMEOUT)
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not 0 < timeout <= MAX_TIMEOUT:
        return f"timeout must be in (0, {MAX_TIMEOUT:g}] seconds"
    return None


def save_registrations(sources: dict[str, dict[str, Any]], path: Path | None = None) -> None:
    path = path or registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"sources": sources}, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def read_command(name: str, spec: dict[str, Any], read_at: str,
                 run: Callable[..., Any] | None = None) -> dict[str, Any]:
    """Run one command source and translate its envelope (never guessing). It
    runs from the registry's own directory, so neither its command nor a relative
    argument can resolve against the checkout the read happens to run in."""
    if run is None:
        from .procutil import run_background_capture as run
    from .procutil import OutputLimitExceeded

    try:
        done = run(spec["argv"], timeout=spec["timeout"], cwd=str(registry_path().parent),
                   max_output=MAX_OUTPUT)
    except OutputLimitExceeded:
        return ac.command_failure(f"wrote more than {MAX_OUTPUT} characters of output; stopped")
    if done is None:
        return ac.command_failure(f"did not finish within {spec['timeout']:g}s (or could not start)")
    if done.returncode != 0:
        return ac.command_failure(f"exited {done.returncode}: {done.stderr or done.stdout}")
    try:
        raw = json.loads(done.stdout)
    except ValueError:
        return ac.command_failure("output is not JSON")
    result = ac.normalize_command_result(raw, name=name)
    if result["status"] == "failed":
        return result
    try:
        result["items"] = [ac.stamp_command_item(i, name=name) for i in result["items"]]
    except ac.ContractError as exc:
        return ac.command_failure(f"invalid item: {exc}")
    result.setdefault("read_at", read_at)
    return result


# -- the aggregator ----------------------------------------------------------------


def _finish(name: str, result: dict[str, Any]) -> dict[str, Any]:
    """Validate a source's items (``created_at`` may still be unstamped) and the
    one-item-per-entity rule; any violation makes the source ``failed``. Runs
    before the first-observed store, so an invalid read never moves its times."""
    if result["status"] in ("failed", "disabled"):
        # A failed or disabled source reports nothing: its items (valid or not)
        # never reach the queue or the deduplication.
        return {**result, "items": []}
    keys = set()
    for item in result["items"]:
        item.setdefault("updated_at", result["read_at"])
        try:
            ac.validate_item({**item, "created_at": item.get("created_at") or result["read_at"]})
        except ac.ContractError as exc:
            return ac.command_failure(f"invalid item: {exc}")
        key = (item["entity"], item["entity_ref"])
        if key in keys:
            return ac.command_failure(f"two items for {item['entity']} {item['entity_ref']}")
        keys.add(key)
    return result


def _result_problem(raw: Any) -> str | None:
    """What makes a reader's return value not a source result, or ``None``: a
    contract violation is that source's ``failed``, never a crash of the read."""
    if not isinstance(raw, dict):
        return f"a {type(raw).__name__}, not an object"
    if raw.get("status") not in ac.SOURCE_STATUSES:
        return f"status {raw.get('status')!r}"
    if not isinstance(raw.get("items"), list):
        return "no items list"
    uncertain = raw.get("uncertain", 0)
    if type(uncertain) is not int or uncertain < 0:
        return f"uncertain {uncertain!r}"
    return None


def collect(readers: dict[str, Callable[[str], dict[str, Any]]], *, timeouts: dict[str, float],
            selected: list[str] | None, config_errors: list[dict[str, str]],
            store: Any, read_at: str | None = None, read_token: int | None = None,
            dismissals: Any = None,
            dismiss_cli: Callable[[], tuple[str, ...] | None] = lambda: ("agent-dispatch",),
            include_remote: bool = False) -> dict[str, Any]:
    """Read every (selected) source concurrently and build the aggregate envelope.
    A selected name that is only a rejected registration is reported through its
    config error, not as a source. ``read_token`` (default: the number the store
    allocates as the read starts) orders this read against concurrent ones,
    which the second-precision ``read_at`` can't. ``dismissals`` (an
    :class:`~agent_dispatch.attention_dismiss.Dismissals`) moves the operator's
    dismissed items out of the queue into ``dismissed``. ``dismiss_cli`` (asked
    after the read) is the invocation that reaches the coordinator the
    ``dispatch`` items came from, for their ``dismiss`` action (which re-reads
    the item); ``None`` offers them none, like their other actions."""
    if read_token is None:  # the store numbers reads as they start (a clock can tie or step back)
        read_token = store.begin_read() if hasattr(store, "begin_read") else time.time_ns()
    read_at = read_at or now_iso()
    names = sorted(n for n in (selected if selected is not None else readers) if n in readers)
    results: dict[str, dict[str, Any]] = {}
    scopes: dict[str, str] = {}
    outcomes: dict[str, Any] = {}

    def run(name: str) -> None:
        try:
            result = readers[name](read_at)
        except SystemExit as exc:  # a usage error raised while choosing the coordinator
            result = ac.command_failure(f"could not reach its coordinator (exit {exc.code})")
        except Exception as exc:  # noqa: BLE001 -- a source failure is that source's, never the aggregate's
            result = ac.command_failure(f"{type(exc).__name__}: {exc}")
        outcomes[name] = (result, time.monotonic())

    # Daemon threads, not an executor: a reader that hangs past its timeout is
    # abandoned, and can't keep the CLI process alive at exit (an executor's
    # exit hook would join it).
    threads = {n: threading.Thread(target=run, args=(n,), daemon=True, name=f"attention-{n}")
               for n in names}
    started = time.monotonic()
    for thread in threads.values():
        thread.start()
    deadlines = {n: started + timeouts.get(n, DEFAULT_TIMEOUT) for n in names}
    for name in sorted(names, key=deadlines.get):
        threads[name].join(max(0.0, deadlines[name] - time.monotonic()))
    for name in names:
        done = outcomes.get(name)
        # Finished by its own deadline, whatever order the joins ran in.
        raw = done[0] if done and done[1] <= deadlines[name] else None
        if raw is None:
            raw = ac.command_failure(f"timed out after {timeouts.get(name, DEFAULT_TIMEOUT):g}s")
        elif problem := _result_problem(raw):
            raw = ac.command_failure(f"malformed source result: {problem}")
        # Only a built-in reader names the coordinator it read; a command can't
        # file its times under another source's evidence.
        scope = raw.pop("scope", None)
        if name in BUILTIN_SOURCES and isinstance(scope, str) and scope:
            scopes[name] = scope
        raw.setdefault("read_at", read_at)
        results[name] = _finish(name, raw)
    store.apply({n: r for n, r in results.items() if r["status"] in ("ok", "uncertain")}, read_at, read_token,
                scopes=scopes)
    if selected is not None:
        config_errors = [e for e in config_errors if e["name"] in selected]
    sources = [{"name": n, "status": r["status"], "uncertain": r.get("uncertain", 0),
                "items": len(r["items"]), "read_at": r.get("read_at", read_at),
                **({"error": r["error"]} if r.get("error") else {})}
               for n, r in sorted(results.items())]
    # Dismissals apply per condition, before deduplication: hiding one source's
    # condition never hides another source's undismissed one for the same entity.
    raw = [i for r in results.values() for i in r["items"]]
    dismissed: list[dict[str, Any]] = []
    if dismissals is not None:
        # Only a read of this machine's own queue can end a dismissal by omission:
        # another coordinator's (--url, --shared, a failover) never had the item.
        ok = {n for n, r in results.items() if r["status"] == "ok" and n not in scopes}
        raw, dismissed, error = dismissals.split(raw, read_at, ok_sources=ok)
        if error:
            config_errors = [*config_errors, {"name": DISMISSALS_NAME, "error": error}]
    items = ac.dedupe(raw)
    dispatch_cli = dismiss_cli()
    for item in items:
        cli = dispatch_cli if item["source"] == "dispatch" else ("agent-dispatch",)
        # A remote bridge session is read only with --include-remote: so is its dismissal's re-read.
        extra = ("--include-remote",) if include_remote and item["source"] == "bridge" else ()
        if cli:
            item["actions"] = [*item["actions"], dismiss_action(item["id"], cli, extra)]
    config_errors = sorted(config_errors, key=lambda e: (e["name"], e["error"]))
    return {"schema": ac.SCHEMA, "status": ac.aggregate_status(sources, config_errors, items),
            "read_at": read_at, "selected": selected, "sources": sources,
            "config_errors": config_errors, "items": items, "dismissed": dismissed}


#: The ``config_errors`` name a malformed dismissal store is reported under.
DISMISSALS_NAME = "*dismissals"


def dismiss_action(item_id: str, cli: tuple[str, ...] = ("agent-dispatch",),
                   extra: tuple[str, ...] = ()) -> dict[str, Any]:
    """Every queued item's last action: hide it until it changes."""
    return {"verb": "dismiss", "argv": [*cli, "attention", "dismiss", item_id, *extra]}
