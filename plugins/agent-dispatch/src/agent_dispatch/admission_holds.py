"""Installation-local negative selectors for new supervised worker admission."""

from __future__ import annotations

import json
import logging
from collections.abc import Collection, Iterable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from .action_policy import MAX_STATE_BYTES, _read_object
from .install_paths import install_dir
from .overrides import mutate_overrides
from .registrar_json import unique_json_fields

if TYPE_CHECKING:
    from .queue_common import Task
    from .supervisor import Supervisor

STATE_NAME = "worker-admission-holds.json"
DIMENSIONS = ("pool", "instance", "label", "kind", "agent")
DRIVER_BODIES = {"cli": "cli", "ordinary-bridge": "acp", "restricted-bridge": "restricted-bridge"}
log = logging.getLogger("agent-dispatch.admission")


def _text(value: object, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit or not value.isprintable():
        raise ValueError(f"expected nonempty printable text of at most {limit} characters")
    return value


def selector(dimension: str, values: Iterable[str]) -> str:
    parts = tuple(values)
    if dimension not in DIMENSIONS or len(parts) != (3 if dimension == "instance" else 1):
        raise ValueError("invalid worker admission selector")
    return json.dumps([dimension, *(_text(v, 256) for v in parts)], separators=(",", ":"))


def read_holds(path: Path) -> dict[str, dict[str, str]]:
    data = _read_object(path, object_pairs_hook=unique_json_fields)
    holds: dict[str, dict[str, str]] = {}
    for key, record in data.items():
        parts = json.loads(key)
        if (not isinstance(parts, list) or not parts or not all(isinstance(s, str) for s in parts)
                or selector(parts[0], parts[1:]) != key
                or not isinstance(record, dict) or set(record) != {"reason"}):
            raise ValueError("invalid worker admission hold")
        holds[key] = {"reason": _text(record["reason"], 240)}
    return holds


def update_hold(path: Path, key: str, reason: str | None) -> None:
    def update(records: dict[str, dict[str, str]]) -> None:
        if reason is None:
            records.pop(key, None)
        else:
            records[key] = {"reason": _text(reason, 240)}
        if len(json.dumps(records, indent=2, sort_keys=True).encode("utf-8")) > MAX_STATE_BYTES:
            raise ValueError("worker admission holds would exceed 64 KiB")

    mutate_overrides(path, update, loader=read_holds)


def waiter_hold_snapshot(enabled: bool) -> frozenset[str]:
    """Malformed policy cannot discount demand; the separate admission gate refuses effects."""
    if enabled:
        try:
            return frozenset(read_holds(install_dir() / STATE_NAME))
        except (OSError, ValueError, RecursionError, TypeError):
            log.warning("invalid worker admission holds; retaining waiter demand")
    return frozenset()


def inhibited(*, pool: str | None, instance: tuple[str, str, str] | None,
              labels: Iterable[str], kind: str | None, agents: Iterable[str],
              reservation: Mapping[str, object] | None = None, holds: Collection[str] | None = None) -> bool:
    try:
        if holds is None:
            holds = read_holds(install_dir() / STATE_NAME)
        if not holds:
            return False
        candidates: list[tuple[str, tuple[str, ...]]] = []
        if pool:
            candidates.append(("pool", (pool,)))
        if instance:
            candidates.append(("pool", (instance[0],)))
            candidates.append(("instance", instance))
        if kind:
            candidates.append(("kind", (kind,)))
        candidates.extend(("label", (label,)) for label in labels)
        candidates.extend(("agent", (agent,)) for agent in agents)
        if reservation:
            original = reservation.get("reserved_by")
            if isinstance(original, str):
                candidates.append(("pool", (original,)))
            assignment = reservation.get("reservation_pool_assignment")
            if isinstance(assignment, dict):
                rid, machine, env = (assignment.get(k) for k in ("registration_id", "machine", "env"))
                if isinstance(rid, str) and isinstance(machine, str) and isinstance(env, str):
                    candidates.extend([("pool", (rid,)), ("instance", (rid, machine, env))])
            driver = reservation.get("driver")
            if isinstance(driver, str) and driver in DRIVER_BODIES:
                candidates.append(("agent", (DRIVER_BODIES[driver],)))
            info = reservation.get("agent_info")
            invocation = info.get("startup_invocation") if isinstance(info, dict) else None
            selection = invocation.get("selection") if isinstance(invocation, dict) else None
            if isinstance(selection, dict) and isinstance(selection.get("agent"), str):
                candidates.append(("agent", (selection["agent"],)))
        for dimension, values in candidates:
            # Candidate strings outside selector limits cannot equal a legal key.
            if json.dumps([dimension, *values], separators=(",", ":")) in holds:
                return True
    except (OSError, ValueError, RecursionError, TypeError):
        log.warning("invalid worker admission holds; refusing new admission")
        return True
    return False


def supervisor_inhibited(owner: Supervisor, task: Mapping[str, object],
                         reservation: Mapping[str, object] | None = None) -> bool:
    from .fleet import FleetSpawner

    body = owner.spawn_fn
    if task.get("source") == "ci-repair":
        agents = ["ci-repair"]
    elif isinstance(body, FleetSpawner):
        agents = [body.agent, "fleet-headless"] if body.headless else ["fleet-cli"]
    else:
        row = dict(task)
        interface = owner._spawn_attribute(row, "allocation_interface", "")
        agents = [interface] if interface else []
        name = owner._spawn_attribute(row, "admission_agent", "")
        if name:
            agents.append(name)
        if getattr(body, "restricted_bridge_for", lambda _: False)(row):
            agents.append("restricted-bridge")
    labels = task.get("labels", [])
    kind = task.get("kind", "finite")
    return inhibited(pool=owner.supervisor_id, instance=owner.pool_instance,
                     labels=labels if isinstance(labels, list) else (),
                     kind=kind if isinstance(kind, str) else None, agents=agents, reservation=reservation)


def supervisor_resume_inhibited(owner: Supervisor, task: Mapping[str, object], reservation: Mapping[str, object]) -> bool:
    from .action_controls import action_inhibited
    return action_inhibited("worker-admission", logger=log) or supervisor_inhibited(owner, task, reservation)


def require_spawn_admission(task: Task, reserved_by: str | None,
                            instance: tuple[str, str, str] | None, driver: str | None,
                            reservation: Mapping[str, object] | None = None) -> None:
    """The queue knows its actual placement and CI body, not a remote lane's named agent."""
    from .queue_records import WorkerAdmissionInhibited

    pool = reserved_by
    agents = [DRIVER_BODIES[driver]] if driver is not None and driver in DRIVER_BODIES else []
    if driver == "handoff-fallback":
        from .bridge import DEFAULT_WORKER_AGENT
        agents.extend(("handoff-fallback", DEFAULT_WORKER_AGENT))
    if task.source == "ci-repair":
        pool = task.origin_ref
        agents.append("ci-repair")
    if inhibited(pool=pool, instance=instance, labels=task.labels, kind=task.kind,
                 agents=agents, reservation=reservation):
        raise WorkerAdmissionInhibited()
