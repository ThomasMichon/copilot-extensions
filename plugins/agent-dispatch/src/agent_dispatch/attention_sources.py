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

BUILTIN_SOURCES = ("dispatch",)
DEFAULT_TIMEOUT = 20.0
MAX_TIMEOUT = 120.0
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


def _task_item(task: dict[str, Any], read_at: str,
               cli: tuple[str, ...] = ("agent-dispatch",)) -> dict[str, Any] | None:
    """One task's item, coalescing its conditions to the worst: an operator ask
    (``awaiting_steer``), an operator hold (``hold_reason``), or a completion
    claim awaiting confirmation (``submitted``). ``completed`` is never an item.
    ``cli`` is the invocation that reaches the coordinator this read came from
    (its ``--url``/``--shared``, never a token), so the action runs as-is."""
    task_id, title = str(task.get("id") or ""), ac.one_line(task.get("title"), 120)
    status = task.get("status")
    card = task.get("card") if isinstance(task.get("card"), dict) else {}
    show = {"verb": "show", "argv": [*cli, "card", "show", task_id]}
    if not task_id:
        return None
    if task.get("awaiting_steer"):
        state, reason = "awaiting_input", f"awaiting your answer: {title}"
        form = card.get("request_input")
        extra = {"input": form} if isinstance(form, (dict, list)) and form else {}
    elif task.get("hold_reason"):
        state, reason, extra = "blocked", f"held ({ac.one_line(task['hold_reason'], 60)}): {title}", {}
    elif status == "submitted":
        state, reason, extra = "review", f"completion awaiting confirmation: {title}", {}
    else:
        return None
    item = {
        "schema": ac.SCHEMA, "entity": "task", "entity_ref": task_id, "lifecycle_state": status,
        "display_state": state, "severity": ac.SEVERITY[state], "reason": ac.one_line(reason),
        "created_at": None, "updated_at": iso(task.get("updated_at"), read_at),
        "confidence": "reported", "actions": [show], "source": "dispatch",
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
#: Seconds, from the start of a dispatch read (its task list included), within
#: which every per-lane backlog read must *finish*, inside the source's own
#: deadline. A lane that can't, or whose read fails, counts toward
#: ``uncertain``: backlog probing never fails the task items already read.
BACKLOG_BUDGET = 19.0
#: The dispatch client's per-request timeout: a lane read starts only when it
#: can still finish inside the budget.
_LANE_REQUEST_TIMEOUT = 10.0


def _threshold(env: str) -> float:
    try:
        value = float(os.environ.get(env, DEFAULT_STALLED_AFTER))
    except ValueError:
        return DEFAULT_STALLED_AFTER
    return value if value >= 0 else DEFAULT_STALLED_AFTER


def _queue_item(repo: str, backlog: dict[str, Any], read_at: str, cli: tuple[str, ...]) -> dict[str, Any] | None:
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
        "actions": [{"verb": "show", "argv": [*cli, "list", "--repo", repo, "--status", "queued,claimed,started"]}],
        "source": "dispatch", "id": ac.make_id("dispatch", "queue", repo), "also": [],
    }


def read_dispatch(client_factory: Callable[[], Any], read_at: str,
                  limit: int = DISPATCH_READ_LIMIT,
                  cli: tuple[str, ...] = ("agent-dispatch",),
                  backlog_budget: float = BACKLOG_BUDGET) -> dict[str, Any]:
    started = time.monotonic()
    with client_factory() as client:
        tasks = list(client.list(repo=None, status=_OPEN_STATES, limit=limit) or [])
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
    items = [i for i in (_task_item(t, read_at, cli) for t in tasks) if i]
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
    if name in BUILTIN_SOURCES or name in ("bridge", "pr"):
        return f"{name!r} is a built-in source's name"
    if not isinstance(spec, dict):
        return "a registration must be an object"
    argv = spec.get("argv")
    if not (isinstance(argv, list) and argv and all(isinstance(a, str) and a for a in argv)):
        return "argv must be a non-empty list of strings"
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
    """Run one command source and translate its envelope (never guessing)."""
    if run is None:
        from .procutil import run_background_capture as run
    done = run(spec["argv"], timeout=spec["timeout"])
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
        return result
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


def collect(readers: dict[str, Callable[[str], dict[str, Any]]], *, timeouts: dict[str, float],
            selected: list[str] | None, config_errors: list[dict[str, str]],
            store: Any, read_at: str | None = None) -> dict[str, Any]:
    """Read every (selected) source concurrently and build the aggregate envelope.
    A selected name that is only a rejected registration is reported through its
    config error, not as a source."""
    read_at = read_at or now_iso()
    names = sorted(n for n in (selected if selected is not None else readers) if n in readers)
    results: dict[str, dict[str, Any]] = {}
    outcomes: dict[str, Any] = {}

    def run(name: str) -> None:
        try:
            result = readers[name](read_at)
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
        raw.setdefault("read_at", read_at)
        results[name] = _finish(name, raw)
    store.apply({n: r for n, r in results.items() if r["status"] in ("ok", "uncertain")}, read_at)
    if selected is not None:
        config_errors = [e for e in config_errors if e["name"] in selected]
    sources = [{"name": n, "status": r["status"], "uncertain": r.get("uncertain", 0),
                "items": len(r["items"]), "read_at": r.get("read_at", read_at),
                **({"error": r["error"]} if r.get("error") else {})}
               for n, r in sorted(results.items())]
    items = ac.dedupe(i for r in results.values() for i in r["items"])
    config_errors = sorted(config_errors, key=lambda e: (e["name"], e["error"]))
    return {"schema": ac.SCHEMA, "status": ac.aggregate_status(sources, config_errors, items),
            "read_at": read_at, "selected": selected, "sources": sources,
            "config_errors": config_errors, "items": items}
