"""Repository reviewer-loop declarations + lifecycle helpers."""

from __future__ import annotations

import json
import logging
import math
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .registrar import (
    Filters,
    ProfileDeclaration,
    RegistrarError,
    _load_filters,
    load_declaration,
)
from .producers.evaluator import Abandon, Confirm, Decision, EvaluatorError, NoOp
from .registrations import RegistrationKind
from .review_target_refs import parse_review_target_ref

log = logging.getLogger("agent-dispatch.reviewer-loops")

_KNOWN_KEYS = frozenset(
    {
        "name",
        "kind",
        "repo",
        "task_label",
        "filters",
        "emitter",
        "evaluator",
        "pool",
        "owner",
        "description",
        "stale_after_days",
    }
)
def _mapping(data: Mapping, key: str) -> dict:
    value = data.get(key)
    if not isinstance(value, Mapping):
        raise RegistrarError(
            f"reviewer-loop {key}: expected a mapping, got {type(value).__name__}"
        )
    return dict(value)


def _string(data: Mapping, key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise RegistrarError(
            f"reviewer-loop {key}: expected a non-empty string, got {value!r}"
        )
    return value


def _optional_positive_number(data: Mapping, key: str) -> float | None:
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RegistrarError(
            f"reviewer-loop {key}: expected a number > 0, got {value!r}"
        )
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise RegistrarError(f"reviewer-loop {key}: expected a number > 0")
    return result


def _strings(data: dict, key: str) -> tuple[str, ...]:
    value = data.pop(key, ())
    if not isinstance(value, (list, tuple)) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise RegistrarError(
            f"reviewer-loop {key}: expected a list of non-empty strings"
        )
    return tuple(dict.fromkeys(value))


def _reject_reserved(section: str, data: Mapping, reserved: set[str]) -> None:
    present = sorted(reserved & set(data))
    if present:
        raise RegistrarError(
            f"reviewer-loop {section}: {present} are derived from the loop declaration"
        )


def _filters_payload(filters: Filters) -> dict[str, dict[str, list[str]]]:
    payload = {}
    for side in ("permit", "reject"):
        values = getattr(filters, side)
        if values:
            payload[side] = {
                dimension: sorted(accepted)
                for dimension, accepted in sorted(values.items())
            }
    return payload


def _compose_filters(common: Filters, specific: Filters) -> Filters:
    permit = dict(common.permit)
    for dimension, accepted in specific.permit.items():
        permit[dimension] = (
            permit[dimension] & accepted
            if dimension in permit
            else accepted
        )
    reject = dict(common.reject)
    for dimension, rejected in specific.reject.items():
        reject[dimension] = reject.get(dimension, frozenset()) | rejected
    for dimension, accepted in permit.items():
        if not accepted:
            raise RegistrarError(
                f"reviewer-loop filters: combined filters permit no "
                f"{dimension!r} value"
            )
        if accepted <= reject.get(dimension, frozenset()):
            raise RegistrarError(
                f"reviewer-loop filters: every permitted {dimension!r} value "
                "is rejected"
            )
    return Filters(permit=permit, reject=reject)


@dataclass(frozen=True)
class ReviewerLoopLifecycleConfig:
    stale_after_days: float | None = None


@dataclass(frozen=True)
class ReviewerLoopCommitSnapshot:
    last_commit_at: float
    observed_at: float | None = None
    repo: str | None = None
    source: str = "inline"


class ReviewerLoopEvaluator:
    """Whole-goal reviewer lifecycle wrapper.

    Keeps the declaration's ordinary evaluator behavior intact, but layers the
    reviewer-loop's stale-exit policy over it when configured.
    """

    def __init__(
        self,
        evaluator: Any,
        config: ReviewerLoopLifecycleConfig,
        *,
        clock=None,
        observation_store_path: str | Path | None = None,
    ) -> None:
        self._evaluator = evaluator
        self._config = config
        self._clock = time.time if clock is None else clock
        self._observation_store_path = observation_store_path

    def evaluate(self, event: dict[str, Any]) -> list[Decision]:
        try:
            decisions = list(self._evaluator.evaluate(event))
        except Exception:
            stale = reviewer_loop_stale_decision(
                event,
                stale_after_days=self._config.stale_after_days,
                now=self._clock(),
                observation_store_path=self._observation_store_path,
            )
            if stale is not None:
                return [stale]
            raise
        if any(isinstance(decision, (Confirm, Abandon)) for decision in decisions):
            return decisions
        stale = reviewer_loop_stale_decision(
            event,
            stale_after_days=self._config.stale_after_days,
            now=self._clock(),
            observation_store_path=self._observation_store_path,
        )
        if stale is not None and all(isinstance(decision, NoOp) for decision in decisions):
            return [stale]
        return decisions

    def next_verification_not_before(self, event: Mapping[str, Any]) -> float | None:
        return reviewer_loop_next_check_not_before(
            event,
            stale_after_days=self._config.stale_after_days,
            now=self._clock(),
            observation_store_path=self._observation_store_path,
        )


def _reviewer_loop_payload(task: Mapping[str, Any]) -> Mapping[str, Any] | None:
    raw = task.get("payload_inline")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        decoded = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(decoded, Mapping):
        return None
    nested = decoded.get("reviewer_loop")
    if isinstance(nested, Mapping):
        return nested
    return decoded


def _reviewer_loop_payload_ref(task: Mapping[str, Any]):
    payload_ref = task.get("payload_ref")
    if not isinstance(payload_ref, str) or not payload_ref:
        return None
    return parse_review_target_ref(payload_ref)


def _timestamp(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        result = float(value)
        return result if math.isfinite(result) else None
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _provider_snapshot(
    task: Mapping[str, Any],
    *,
    observation_store_path: str | Path | None = None,
) -> ReviewerLoopCommitSnapshot | None:
    from .pr_observation_store import PRObservationStore, observation_store_path as _store_path

    target = _reviewer_loop_payload_ref(task)
    if target is None:
        return None
    repo, number = target.observation_repo, target.number
    db_path = _store_path(observation_store_path)
    if not db_path.exists():
        return None
    store = PRObservationStore(db_path)
    observation = store.get(repo, number)
    if observation is None or observation.last_commit_at is None:
        return None
    return ReviewerLoopCommitSnapshot(
        last_commit_at=observation.last_commit_at,
        observed_at=store.last_observed_at(repo, number),
        repo=repo,
        source="provider",
    )


def reviewer_loop_commit_snapshot(
    task: Mapping[str, Any],
    *,
    observation_store_path: str | Path | None = None,
) -> ReviewerLoopCommitSnapshot | None:
    payload = _reviewer_loop_payload(task)
    if payload is not None:
        last_commit_at = _timestamp(payload.get("last_commit_at"))
        if last_commit_at is not None:
            return ReviewerLoopCommitSnapshot(
                last_commit_at=last_commit_at,
                observed_at=_timestamp(payload.get("observed_at")),
                source="inline",
            )
    return _provider_snapshot(task, observation_store_path=observation_store_path)


def reviewer_loop_deadline(
    event: Mapping[str, Any],
    *,
    stale_after_days: float | None,
    observation_store_path: str | Path | None = None,
) -> float | None:
    if stale_after_days is None:
        return None
    task = event.get("task")
    if not isinstance(task, Mapping):
        return None
    snapshot = reviewer_loop_commit_snapshot(
        task, observation_store_path=observation_store_path
    )
    if snapshot is None:
        return None
    return snapshot.last_commit_at + (stale_after_days * 86400.0)


def reviewer_loop_next_check_not_before(
    event: Mapping[str, Any],
    *,
    stale_after_days: float | None,
    now: float | None = None,
    observation_store_path: str | Path | None = None,
) -> float | None:
    task = event.get("task")
    if not isinstance(task, Mapping):
        return None
    snapshot = reviewer_loop_commit_snapshot(
        task, observation_store_path=observation_store_path
    )
    deadline = reviewer_loop_deadline(
        event,
        stale_after_days=stale_after_days,
        observation_store_path=observation_store_path,
    )
    if snapshot is None or deadline is None:
        return None
    current_time = time.time() if now is None else now
    if current_time < deadline:
        return deadline
    if snapshot.source == "provider" and snapshot.observed_at is not None and snapshot.repo:
        from .pr_polling_policy import polling_interval_seconds

        return max(
            current_time + 60.0,
            snapshot.observed_at + polling_interval_seconds(snapshot.repo),
        )
    return None


def reviewer_loop_stale_decision(
    event: Mapping[str, Any],
    *,
    stale_after_days: float | None,
    now: float | None = None,
    observation_store_path: str | Path | None = None,
) -> Abandon | None:
    """Return the reviewer loop's stale-exit decision, if any."""
    task = event.get("task")
    if not isinstance(task, Mapping):
        return None
    snapshot = reviewer_loop_commit_snapshot(
        task, observation_store_path=observation_store_path
    )
    deadline = reviewer_loop_deadline(
        event,
        stale_after_days=stale_after_days,
        observation_store_path=observation_store_path,
    )
    if deadline is None:
        return None
    current_time = time.time() if now is None else now
    if current_time < deadline:
        return None
    if snapshot is None or snapshot.observed_at is None or snapshot.observed_at < deadline:
        return None
    return Abandon(
        reason=(
            "review target stale: last commit is older than "
            f"{stale_after_days:g} day(s)"
        )
    )


def reviewer_loop_lifecycle_for_task(
    registrations: list[tuple[dict[str, Any], ReviewerLoopLifecycleConfig]],
    *,
    repo: str | None,
    evaluator_ref: str,
) -> ReviewerLoopLifecycleConfig | None:
    exact: list[ReviewerLoopLifecycleConfig] = []
    global_matches: list[ReviewerLoopLifecycleConfig] = []
    for spec, config in registrations:
        if spec.get("evaluator_ref") != evaluator_ref:
            continue
        if spec.get("all_repos"):
            global_matches.append(config)
            continue
        if spec.get("repo") == repo:
            exact.append(config)
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        log.warning(
            "skipping reviewer stale reconciliation for evaluator_ref %s in repo %s: multiple repo-scoped registrations",
            evaluator_ref,
            repo,
        )
        return None
    if len(global_matches) == 1:
        return global_matches[0]
    if len(global_matches) > 1:
        log.warning(
            "skipping reviewer stale reconciliation for evaluator_ref %s: multiple all-repos registrations",
            evaluator_ref,
        )
    return None


def active_reviewer_loop_lifecycle_configs(
    queue,
    *,
    current_machine: str | None,
    current_env: str,
) -> list[tuple[dict[str, Any], ReviewerLoopLifecycleConfig]]:
    registry: list[tuple[dict[str, Any], ReviewerLoopLifecycleConfig]] = []
    for record in queue.list_registrations(
        kind=RegistrationKind.EVALUATOR,
        include_paused=False,
    ):
        if record.machine not in (None, current_machine):
            continue
        if str(record.env or "default") != current_env:
            continue
        spec = record.spec or {}
        evaluator_ref = spec.get("evaluator_ref")
        if not isinstance(evaluator_ref, str) or not evaluator_ref:
            continue
        try:
            config = reviewer_loop_lifecycle_config(spec)
        except EvaluatorError as exc:
            log.warning(
                "skipping reviewer stale reconciliation for registration %s (%s): %s",
                record.id,
                evaluator_ref,
                exc,
            )
            continue
        if config is None or config.stale_after_days is None:
            continue
        registry.append((spec, config))
    return registry


def reviewer_loop_runtime_scope() -> tuple[str | None, str]:
    from . import remote_dispatch

    return (
        remote_dispatch.local_machine(),
        os.environ.get("AGENT_DISPATCH_ENV") or "default",
    )


def reviewer_loop_lifecycle_config(
    spec: Mapping[str, Any],
) -> ReviewerLoopLifecycleConfig | None:
    raw = spec.get("reviewer_loop")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise EvaluatorError(
            "reviewer-loop evaluator config must be a mapping"
        )
    stale_after_days = raw.get("stale_after_days")
    if stale_after_days is not None and (
        isinstance(stale_after_days, bool)
        or not isinstance(stale_after_days, (int, float))
        or not math.isfinite(float(stale_after_days))
        or float(stale_after_days) <= 0
    ):
        raise EvaluatorError(
            "reviewer-loop evaluator stale_after_days must be a number > 0"
        )
    return ReviewerLoopLifecycleConfig(
        stale_after_days=(
            None if stale_after_days is None else float(stale_after_days)
        )
    )


def wrap_reviewer_loop_evaluator(
    evaluator: Any,
    spec: Mapping[str, Any],
    *,
    observation_store_path: str | Path | None = None,
) -> Any:
    config = reviewer_loop_lifecycle_config(spec)
    if config is None:
        return evaluator
    return ReviewerLoopEvaluator(
        evaluator, config, observation_store_path=observation_store_path
    )


def _placement_filters(data: object) -> Filters:
    try:
        filters = _load_filters(data)
    except RegistrarError as exc:
        raise RegistrarError(f"reviewer-loop filters: {exc}") from exc
    dimensions = set(filters.permit) | set(filters.reject)
    unsupported = sorted(dimensions - {"machine"})
    if unsupported:
        raise RegistrarError(
            "reviewer-loop filters: top-level placement supports only the "
            f"'machine' dimension; put worker eligibility in pool.filters: {unsupported}"
        )
    return filters


def expand_reviewer_loop(data: Mapping) -> tuple[ProfileDeclaration, ...]:
    """Expand one high-level reviewer loop into emitter, evaluator, and pool units.

    The child names and evaluator association depend only on the loop name. Mutable
    commands, guidance, and pool settings therefore update the same supervised units
    instead of forking a second producer or worker pool.
    """
    if not isinstance(data, Mapping):
        raise RegistrarError(
            f"reviewer-loop: expected a mapping, got {type(data).__name__}"
        )
    extra = sorted(set(data) - _KNOWN_KEYS)
    if extra:
        raise RegistrarError(
            f"reviewer-loop: unknown key(s) {extra}; known: {sorted(_KNOWN_KEYS)}"
        )
    if data.get("kind") != "reviewer-loop":
        raise RegistrarError("reviewer-loop kind must be 'reviewer-loop'")

    name = _string(data, "name")
    repo = _string(data, "repo")
    task_label = _string(data, "task_label")
    stale_after_days = _optional_positive_number(data, "stale_after_days")
    owner = data.get("owner")
    description = data.get("description")
    for key, value in (("owner", owner), ("description", description)):
        if value is not None and not isinstance(value, str):
            raise RegistrarError(
                f"reviewer-loop {key}: expected a string, got {value!r}"
            )

    emitter = _mapping(data, "emitter")
    evaluator = _mapping(data, "evaluator")
    pool = _mapping(data, "pool")
    placement_filters = _placement_filters(data.get("filters"))
    try:
        pool_filters = _load_filters(pool.pop("filters", None))
    except RegistrarError as exc:
        raise RegistrarError(f"reviewer-loop pool.filters: {exc}") from exc
    additional_labels = _strings(pool, "additional_labels")
    _reject_reserved("emitter", emitter, {"id", "evaluator_ref"})
    _reject_reserved("evaluator", evaluator, {"repo", "evaluator_ref", "reviewer_loop"})
    _reject_reserved(
        "pool",
        pool,
        {"name", "labels", "repos", "kind", "spec", "owner", "description"},
    )

    evaluator_ref = f"{name}-lifecycle"
    common = {"owner": owner, "description": description}
    common_filters = _filters_payload(placement_filters)
    worker_filters = _filters_payload(
        _compose_filters(placement_filters, pool_filters)
    )
    declarations = (
        {
            "name": f"{name}-source",
            "kind": "emitter",
            "spec": {
                **emitter,
                "id": f"{name}-source",
                "evaluator_ref": evaluator_ref,
            },
            "filters": common_filters,
            **common,
        },
        {
            "name": f"{name}-evaluator",
            "kind": "evaluator",
            "spec": {
                **evaluator,
                "repo": repo,
                "evaluator_ref": evaluator_ref,
                **(
                    {"reviewer_loop": {"stale_after_days": stale_after_days}}
                    if stale_after_days is not None
                    else {}
                ),
            },
            "filters": common_filters,
            **common,
        },
        {
            "name": f"{name}-workers",
            "labels": list(dict.fromkeys((task_label, *additional_labels))),
            "repos": repo,
            **pool,
            "filters": worker_filters,
            **common,
        },
    )
    return tuple(load_declaration(declaration) for declaration in declarations)
