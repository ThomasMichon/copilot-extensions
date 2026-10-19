"""Fold ``events.jsonl`` records into an ``update-status.json`` document."""

from __future__ import annotations

from collections.abc import Iterable

from . import outcomes as oc

STATUS_SCHEMA = "copilot-extensions.update-status"
STATUS_VERSION = 1
RUN_COMPONENT = "run"
MAX_DETAIL = 300


def _severity(outcome) -> int:
    return oc.SEVERITY.get(outcome, -1)


def _merge_target(entry: dict, event: dict) -> None:
    """Accumulate one ``end`` event; :func:`_finish_target` resolves the entry.

    Required and advisory events are kept apart so an advisory failure never
    becomes the outcome of a component that also has a required result, and
    the detail and error code are both the ones from the event that set each
    pool's worst outcome (never one event's detail with another's code).
    """
    details = event.get("details") if isinstance(event.get("details"), dict) else {}
    pool = entry["_pools"]["required" if details.get("required", True) is not False
                           else "advisory"]
    outcome = event.get("outcome")
    pool["outcomes"].append(outcome)
    detail = details.get("detail") or (event.get("error") or {}).get("message")
    code = (event.get("error") or {}).get("code")
    if _severity(outcome) >= pool["severity"]:
        pool["severity"] = _severity(outcome)
        pool["detail"] = str(detail)[:MAX_DETAIL] if detail else None
        pool["code"] = str(code) if code else None
    if event.get("changed") is not None:
        entry["changed"] = bool(entry.get("changed")) or bool(event["changed"])
    if code:
        entry.setdefault("notes", []).append(code)
    if isinstance(event.get("duration_ms"), int):
        durations = entry.setdefault("durations_ms", {})
        phase = str(event.get("phase"))
        durations[phase] = durations.get(phase, 0) + event["duration_ms"]


def _pool_outcome(pool: dict) -> str:
    known = [o for o in pool["outcomes"] if o in oc.SEVERITY]
    return oc.worst(known) if known else oc.SKIPPED


def _finish_target(entry: dict) -> dict:
    pools = entry.pop("_pools")
    required, advisory = pools["required"], pools["advisory"]
    chosen = required if required["outcomes"] else advisory
    entry["required"] = bool(required["outcomes"])
    entry["outcome"] = _pool_outcome(chosen)
    if chosen.get("detail"):
        entry["detail"] = chosen["detail"]
    if chosen.get("code"):
        entry["error_code"] = chosen["code"]
    if required["outcomes"] and oc.is_failure(_pool_outcome(advisory)):
        entry["advisory_outcome"] = _pool_outcome(advisory)
        entry["advisory_detail"] = advisory.get("detail") or entry["advisory_outcome"]
    return entry


def fold_targets(events: Iterable[dict]) -> dict[str, dict]:
    """Merge every target ``end`` event (component other than ``run``)."""
    targets: dict[str, dict] = {}
    for event in events:
        component = event.get("component")
        if event.get("event") != "end" or not component or component == RUN_COMPONENT:
            continue
        entry = targets.setdefault(str(component), {
            "changed": False,
            "_pools": {name: {"outcomes": [], "severity": -2}
                       for name in ("required", "advisory")},
        })
        _merge_target(entry, event)
    return {name: _finish_target(entry) for name, entry in targets.items()}


def fold_phases(events: Iterable[dict]) -> list[dict]:
    """Run-level phases (component ``run``) paired start->end per process."""
    phases: list[dict] = []
    open_: dict[tuple, dict] = {}
    for event in events:
        if event.get("component") != RUN_COMPONENT or event.get("phase") in (None, "run"):
            continue
        key = (event.get("pid"), event.get("phase"))
        if event.get("event") == "start":
            entry = {
                "phase": event["phase"],
                "started_at": event.get("ts"),
                "duration_ms": None,
                "outcome": oc.IN_PROGRESS,
            }
            phases.append(entry)
            open_[key] = entry
        elif event.get("event") == "end" and key in open_:
            entry = open_.pop(key)
            entry["duration_ms"] = event.get("duration_ms")
            entry["outcome"] = event.get("outcome") or oc.VERIFIED_CURRENT
    return phases


def run_outcome(targets: dict[str, dict], hint: str | None = None) -> str:
    """Maximum severity over required targets (plus the caller's own hint)."""
    required = [t.get("outcome") for t in targets.values() if t.get("required", True)]
    return oc.worst([*required, hint])


def fold(
    events: list[dict],
    *,
    run: dict,
    previous: dict | None = None,
    final: bool = True,
    hint: str | None = None,
    error_code: str | None = None,
    updated_at: str | None = None,
) -> dict:
    """Build the status document for ``run`` from its events.

    ``run`` carries the run metadata (``id``, ``owner``, ``trigger``,
    ``started_at``, ``ended_at``, ``duration_ms``, ``host``, ``platform``,
    ``events_path``, ``event_parse_errors``). ``previous`` is the prior status
    document, whose ``last_success``/``last_failure`` carry forward.
    """
    targets = fold_targets(events)
    outcome = run_outcome(targets, hint) if final else oc.IN_PROGRESS
    run_doc = dict(run)
    run_doc["outcome"] = outcome
    run_doc["exit_code"] = oc.exit_code_for(outcome, error_code) if final else None
    run_doc["phases"] = fold_phases(events)
    previous = previous if isinstance(previous, dict) else {}
    last_success = previous.get("last_success")
    last_failure = previous.get("last_failure")
    failing = sorted(
        (name for name, t in targets.items()
         if t.get("required", True) and oc.is_failure(t.get("outcome"))),
        key=lambda name: -oc.SEVERITY[targets[name]["outcome"]],
    )
    if final and run_doc["exit_code"] == 0:
        last_success = {
            "run_id": run.get("id"),
            "ended_at": run.get("ended_at"),
            "duration_ms": run.get("duration_ms"),
        }
    elif final:
        # Summary and remediation come from one source: the worst required
        # target when it set the run's outcome, otherwise the run's own error.
        first = targets[failing[0]] if failing else {}
        if first.get("outcome") == outcome:
            summary = f"{failing[0]}: {outcome}" + (
                f" ({first['detail']})" if first.get("detail") else "")
            fix_code = first.get("error_code")
        else:
            summary = f"run: {outcome}" + (f" ({error_code})" if error_code else "")
            fix_code = error_code
        last_failure = {
            "run_id": run.get("id"),
            "ended_at": run.get("ended_at"),
            "outcome": outcome,
            "summary": summary[:MAX_DETAIL],
            "fix": oc.fix_for(outcome, run.get("events_path"), fix_code),
        }
    restart = sorted(
        name for name, t in targets.items()
        if t.get("required", True) and t.get("outcome") == oc.REQUIRES_RESTART
    )
    advisories = [
        {
            "component": name,
            "outcome": t.get("outcome"),
            "summary": t.get("detail") or t.get("outcome"),
        }
        for name, t in sorted(targets.items())
        if not t.get("required", True) and oc.is_failure(t.get("outcome"))
    ] + [
        {"component": name, "outcome": t["advisory_outcome"], "summary": t["advisory_detail"]}
        for name, t in sorted(targets.items())
        if t.get("advisory_outcome")
    ]
    auth = [event["details"] for event in events
            if event.get("phase") == "auth_report" and event.get("event") == "info"
            and isinstance(event.get("details"), dict)
            and isinstance(event["details"].get("auth"), dict)]
    auth_uses = [event["details"]["auth"]["id"] for event in events
                 if event.get("phase") == "auth_fallback" and event.get("event") == "info"
                 and isinstance(event.get("details"), dict)
                 and isinstance(event["details"].get("auth"), dict)
                 and isinstance(event["details"]["auth"].get("id"), str)]
    return {
        "schema": STATUS_SCHEMA,
        "version": STATUS_VERSION,
        "updated_at": updated_at or run.get("ended_at") or run.get("started_at"),
        "run": run_doc,
        "last_success": last_success,
        "last_failure": last_failure,
        "restart_required": bool(restart),
        "restart_reasons": [f"{name}: {targets[name].get('detail') or 'restart required'}"
                            for name in restart],
        "advisories": advisories,
        **({"auth": {
            "report": [item["auth"] for item in auth],
            "fallback_used": sorted({item["auth"]["id"] for item in auth
                                     if item.get("fallback_applied")} | set(auth_uses)),
            "cleanup_offer": None,
        }} if auth or auth_uses else {}),
        "plugins": {name: t for name, t in sorted(targets.items()) if "@" in name},
    }
