"""Supervisor **registration** model -- the durable unit a caller hands the
host's singleton supervisor.

A registration is *what to run*, not *the running of it*: a lane to spawn for, a
schedule, an emitter, or an evaluator, captured as a ``kind`` plus a ``spec``
dict (the config the unit's runtime consumes) and scoped to a
``machine``-and-``env``. Registering **adds the registration and returns its
handle**; the singleton supervisor (the daemon, a later increment) is what turns
each registration into a live subprocess. This module is deliberately
transport- and store-free: it defines the record, the kind/status vocabularies,
validation, and stable-id derivation, so both the queue store and the CLI share
one definition.

See ``visions/plugins/agent-dispatch`` -- Concept *the supervisor*, Feature
*registered-supervision*, Behavior *supervise-registers-and-returns*.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass


class RegistrationKind:
    """The kinds of work a caller can register with the singleton supervisor.

    Each names a runtime the supervisor drives in its own subprocess: a
    ``supervised-lane`` spawns claimable tasks in a lane; a ``schedule`` fires a
    recurring producer; an ``emitter`` runs an event source (e.g. a webhook
    listener); an ``evaluator`` advances a domain loop across terminal tasks.
    """

    SUPERVISED_LANE = "supervised-lane"
    SCHEDULE = "schedule"
    EMITTER = "emitter"
    EVALUATOR = "evaluator"

    ALL = frozenset({SUPERVISED_LANE, SCHEDULE, EMITTER, EVALUATOR})


class RegistrationStatus:
    """Lifecycle status of a registration (the caller-owned token's state).

    ``active`` registrations are the ones the singleton reconciles into running
    subprocesses; ``paused`` retains the definition but is skipped by the
    reconcile (the analogue of a paused schedule). Removal deletes the row
    entirely (and, once the daemon lands, winds down its running work).
    """

    ACTIVE = "active"
    PAUSED = "paused"

    ALL = frozenset({ACTIVE, PAUSED})


class RegistrationError(ValueError):
    """Raised when a registration's kind or spec is malformed."""


def _schedule_entry(spec: dict, *, strict: bool) -> dict:
    """The single schedule entry a schedule registration carries.

    A schedule spec is either a bare entry, or ``{"schedules": [entry]}``. When a
    ``schedules`` field is present it must be a non-empty list of objects; a
    malformed one raises :class:`RegistrationError` under ``strict`` (validation),
    and falls back to the spec itself otherwise (best-effort id derivation).
    """
    scheds = spec.get("schedules")
    if scheds is None:
        return spec
    if isinstance(scheds, list) and scheds and isinstance(scheds[0], dict):
        return scheds[0]
    if strict:
        raise RegistrationError(
            "schedule 'schedules' must be a non-empty list of objects"
        )
    return spec


@dataclass
class RegistrationRecord:
    """A read-only snapshot of a registered supervision unit.

    ``spec`` is the JSON-round-tripped config the unit's runtime consumes
    verbatim (its shape depends on ``kind``). ``machine``/``env`` scope the
    registration to exactly one host's singleton supervisor -- the *one
    supervisor per machine-and-environment* that will run it.
    """

    id: str
    kind: str
    spec: dict
    machine: str | None = None
    env: str = "default"
    status: str = RegistrationStatus.ACTIVE
    created_at: float = 0.0
    updated_at: float = 0.0


def validate_registration(kind: str, spec: dict) -> None:
    """Validate a registration's ``kind`` and ``spec`` eagerly.

    Rejects an unknown kind or a non-dict spec, and applies a light per-kind
    shape check so a malformed registration is refused at register time rather
    than failing every reconcile. Intentionally permissive about the *contents*
    of a valid-shaped spec -- the unit's own runtime is the authority on the
    fine-grained schema.
    """
    if kind not in RegistrationKind.ALL:
        raise RegistrationError(
            f"unknown registration kind {kind!r}; expected one of "
            f"{', '.join(sorted(RegistrationKind.ALL))}"
        )
    if not isinstance(spec, dict):
        raise RegistrationError("registration 'spec' must be a JSON object")

    if kind == RegistrationKind.SUPERVISED_LANE:
        # A lane registration must name a scope: a specific repo, or an explicit
        # all-repos opt-in. This is what the supervisor spawns for.
        if not spec.get("repo") and not spec.get("all_repos"):
            raise RegistrationError(
                "supervised-lane registration needs a 'repo' (the lane) or "
                "'all_repos': true"
            )
    elif kind == RegistrationKind.SCHEDULE:
        # A schedule is a self-run emitter: it needs an id (its dedup namespace,
        # 'sched:<id>:<epoch>') and a lane to emit into.
        if not spec:
            raise RegistrationError("schedule registration needs a non-empty spec")
        entry = _schedule_entry(spec, strict=True)
        if not entry.get("id"):
            raise RegistrationError("schedule registration needs an 'id'")
        if not entry.get("repo"):
            raise RegistrationError("schedule registration needs a 'repo' (the lane)")
    elif kind == RegistrationKind.EMITTER:
        if not spec:
            raise RegistrationError("emitter registration needs a non-empty spec")
        if "command" in spec:
            from .producers.emitter import EmitterError, validate_spec

            try:
                validate_spec(spec)
            except EmitterError as exc:
                raise RegistrationError(str(exc)) from exc
    elif kind == RegistrationKind.EVALUATOR:
        if not spec:
            raise RegistrationError("evaluator registration needs a non-empty spec")
        eval_spec = spec.get("evaluator_spec")
        if eval_spec is None and not spec.get("evaluator"):
            raise RegistrationError(
                "evaluator registration needs 'evaluator_spec' (inline) or "
                "'evaluator' (a path)"
            )
        if eval_spec is not None and not isinstance(eval_spec, dict):
            raise RegistrationError(
                "evaluator 'evaluator_spec' must be a JSON object"
            )
        if not spec.get("repo") and not spec.get("all_repos"):
            raise RegistrationError(
                "evaluator registration needs a 'repo' (the lane) or "
                "'all_repos': true"
            )


def _scope_key(kind: str, spec: dict) -> str:
    """A short, human-readable natural key for a registration's scope.

    Used only as the readable prefix of a derived id; the trailing digest is what
    actually guarantees idempotency, so this need only be stable, not unique.
    """
    if kind == RegistrationKind.SUPERVISED_LANE:
        lane = "all-repos" if spec.get("all_repos") else str(spec.get("repo") or "lane")
        labels = spec.get("labels") or spec.get("label") or []
        if isinstance(labels, str):
            labels = [labels]
        base = lane if not labels else f"{lane}-{'-'.join(sorted(str(x) for x in labels))}"
    elif kind == RegistrationKind.EVALUATOR:
        base = "all-repos" if spec.get("all_repos") else str(spec.get("repo") or "eval")
    elif kind == RegistrationKind.SCHEDULE:
        entry = _schedule_entry(spec, strict=False)
        base = str(entry.get("id") or kind)
    else:
        base = str(spec.get("id") or spec.get("name") or kind)
    slug = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")
    return slug or kind


def derive_registration_id(
    kind: str, spec: dict, machine: str | None, env: str
) -> str:
    """Derive a stable registration id from its identity.

    The id is deterministic in ``(kind, machine, env, spec)`` so **re-registering
    the same unit upserts** (the idempotency the vision requires) rather than
    creating a duplicate. It is a readable ``<kind>-<scope>-<digest>`` slug; the
    8-char digest disambiguates units that share a scope slug. A caller may
    always supply an explicit id instead.
    """
    payload = json.dumps(
        {"kind": kind, "machine": machine, "env": env, "spec": spec},
        sort_keys=True,
        default=str,
    )
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:8]
    return f"{kind}-{_scope_key(kind, spec)}-{digest}"
