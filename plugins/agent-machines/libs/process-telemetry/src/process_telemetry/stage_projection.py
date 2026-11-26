"""Closed stage, PR-activity and hook projections for finite tool owners.

Each schema admits only fixed enums and bounded integers.  Names, paths,
repositories, URLs, command lines and error text have no admitted field.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from typing import Any

_MAX = 2**53 - 1
_OUTCOMES = frozenset({"success", "failure", "timeout", "retry"})
_COMMON_REASONS = frozenset({"none", "interrupted", "other"})
_CLAIM_REASONS = frozenset({"claim_busy", "lease_conflict", "force_claimed"})
_ACCOUNT_REASONS = frozenset({"snapshot_timeout", "account_unresolved"})
_VENUE_REASONS = (
    _COMMON_REASONS | _CLAIM_REASONS | _ACCOUNT_REASONS | {
        "seed_too_long", "seed_invalid",
        "marketplace_add_failed", "plugin_install_failed", "clone_auth_failed",
        "relay_not_listening", "credential_fill_failed",
        "payload_install_failed",
        "installer_unavailable", "bootstrap_python_unavailable",
        "listing_failed", "not_found", "connect_failed",
        "transport_retry", "transport_failed",
        "session_unregistered", "session_unrepresented",
    }
)
_VENUE_STAGES = frozenset({
    "launch", "seed_validation", "plugin_preparation", "credential_relay",
    "bridge_payload", "remote_embody", "claim", "account_resolution",
    "bridge_registration",
})

STAGES: dict[str, frozenset[str]] = {
    "worktrees.lifecycle.v1": frozenset({
        "account_resolution", "claim", "lease", "worktree_create", "session_bind",
    }),
    "codespaces.lifecycle.v1": _VENUE_STAGES | {
        "codespace_lookup", "codespace_connect", "ssh",
    },
    "containers.lifecycle.v1": _VENUE_STAGES | {
        "container_lookup", "container_start", "container_connect", "exec",
    },
}
REASONS: dict[str, frozenset[str]] = {
    "worktrees.lifecycle.v1": _COMMON_REASONS | _CLAIM_REASONS | _ACCOUNT_REASONS,
    "codespaces.lifecycle.v1": _VENUE_REASONS,
    "containers.lifecycle.v1": _VENUE_REASONS | {
        "engine_unavailable", "image_unavailable", "not_running", "start_failed",
    },
}

PR_ACTIONS = frozenset({"create", "push", "label", "merge", "abandon"})
PR_REASONS = _COMMON_REASONS | {
    "auth_failed", "provider_rejected", "conflict", "checks_pending",
    "review_required", "gate_refused", "nothing_to_publish", "unavailable",
}
LABEL_RESULTS = frozenset({
    "applied", "already_present", "repaired", "not_requested", "unsupported", "failed",
})

HOOK_PLUGINS = frozenset({
    "agent-worktrees", "budget-guidance", "agent-vault", "context-handoff",
    "agent-pull-requests",
})
HOOK_ENTRIES = frozenset({"sessionStart"})
HOOK_OUTCOMES = frozenset({"success", "failure", "timeout", "unfinished"})
HOOK_FAILURES = frozenset({
    "none", "nonzero_exit", "timeout", "exception", "unavailable_runtime",
    "unfinished_prior_run", "other",
})
_TIMESTAMP = re.compile(r"\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z\Z")

_STAGE_FIELDS = frozenset({
    "kind", "stage", "outcome", "reason", "duration_ms", "attempt", "limit_ms",
})
_PR_FIELDS = frozenset({
    "kind", "action", "outcome", "reason", "duration_ms", "label_result",
})
_HOOK_FIELDS = frozenset({
    "kind", "plugin", "entry", "outcome", "failure_category", "exit_code",
    "duration_ms", "run_sequence", "started_at",
})


def _field_set(schema: str, kind: Any) -> frozenset[str]:
    if schema == "hook.lifecycle.v1":
        return _HOOK_FIELDS
    if type(kind) is str and kind == "pr" and schema == "worktrees.lifecycle.v1":
        return _PR_FIELDS
    return _STAGE_FIELDS


def _offered(schema: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def offered_fields(event: dict[str, Any]) -> dict[str, Any]:
        """Omit unknown fields before the bounded queue snapshots values."""
        if type(event) is not dict or len(event) > 64:
            raise ValueError("unbounded source")
        fields = _field_set(schema, event.get("kind"))
        result: dict[str, Any] = {}
        for key, value in event.items():
            if type(key) is not str:
                raise ValueError("nonprimitive key")
            if key in fields and value is not None:
                result[key] = value
        return result

    return offered_fields


def _enum(event: dict[str, Any], key: str, allowed: frozenset[str], *, required: bool) -> str:
    value = event.get(key)
    if value is None and not required:
        return ""
    if type(value) is not str or value not in allowed:
        raise ValueError("unadmitted value")
    return value


def _integer(event: dict[str, Any], key: str, low: int, high: int) -> int | None:
    value = event.get(key)
    if value is None:
        return None
    if type(value) is not int or not low <= value <= high:
        raise ValueError("unadmitted integer")
    return value


def _copy(result: dict[str, Any], event: dict[str, Any], bounds: dict[str, tuple[int, int]]) -> None:
    for key, (low, high) in bounds.items():
        value = _integer(event, key, low, high)
        if value is not None:
            result[key] = value


def _project_stage(schema: str, event: dict[str, Any]) -> dict[str, Any]:
    outcome = _enum(event, "outcome", _OUTCOMES, required=True)
    reason = _enum(event, "reason", REASONS[schema], required=True)
    stage = _enum(event, "stage", STAGES[schema], required=True)
    if reason == "force_claimed":
        if stage != "claim" or outcome != "success":
            raise ValueError("force_claimed is only a successful claim")
    elif (outcome == "success") != (reason == "none"):
        raise ValueError("inconsistent outcome")
    result: dict[str, Any] = {
        "kind": "stage", "stage": stage, "outcome": outcome, "reason": reason,
    }
    _copy(result, event, {
        "duration_ms": (0, _MAX), "attempt": (1, 2**16), "limit_ms": (0, _MAX),
    })
    return result


def _project_pr(event: dict[str, Any]) -> dict[str, Any]:
    outcome = _enum(event, "outcome", _OUTCOMES - {"retry"}, required=True)
    reason = _enum(event, "reason", PR_REASONS, required=True)
    if (outcome == "success") != (reason == "none"):
        raise ValueError("inconsistent outcome")
    action = _enum(event, "action", PR_ACTIONS, required=True)
    result: dict[str, Any] = {
        "kind": "pr", "action": action, "outcome": outcome, "reason": reason,
    }
    label = _enum(event, "label_result", LABEL_RESULTS, required=False)
    if label:
        result["label_result"] = label
    _copy(result, event, {"duration_ms": (0, _MAX)})
    return result


def _project_hook(event: dict[str, Any]) -> dict[str, Any]:
    if event.get("kind") != "hook" or type(event.get("kind")) is not str:
        raise ValueError("unadmitted record")
    outcome = _enum(event, "outcome", HOOK_OUTCOMES, required=True)
    failure = _enum(event, "failure_category", HOOK_FAILURES, required=True)
    if (outcome == "success") != (failure == "none") or (
        (outcome == "unfinished") != (failure == "unfinished_prior_run")
    ):
        raise ValueError("inconsistent outcome")
    result: dict[str, Any] = {
        "kind": "hook",
        "plugin": _enum(event, "plugin", HOOK_PLUGINS, required=True),
        "entry": _enum(event, "entry", HOOK_ENTRIES, required=True),
        "outcome": outcome, "failure_category": failure,
    }
    started = event.get("started_at")
    if started is not None:
        if type(started) is not str or not _TIMESTAMP.match(started):
            raise ValueError("unadmitted timestamp")
        # The shape check admits impossible values such as month 99.
        datetime.strptime(started, "%Y-%m-%dT%H:%M:%S.%fZ")
        result["started_at"] = started
    _copy(result, event, {
        "exit_code": (-(2**31), 2**31 - 1), "duration_ms": (0, _MAX),
        "run_sequence": (1, _MAX),
    })
    return result


def _projector(schema: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def project(event: dict[str, Any]) -> dict[str, Any]:
        """Admit closed enums and bounded integers only; anything else drops."""
        if schema == "hook.lifecycle.v1":
            return _project_hook(event)
        kind = event.get("kind")
        if type(kind) is not str:
            raise ValueError("unadmitted record")
        if kind == "stage":
            return _project_stage(schema, event)
        if kind == "pr" and schema == "worktrees.lifecycle.v1":
            return _project_pr(event)
        raise ValueError("unadmitted record")

    return project


SCHEMAS = frozenset(STAGES) | {"hook.lifecycle.v1"}


def for_schema(schema: str) -> tuple[
    Callable[[dict[str, Any]], dict[str, Any]], Callable[[dict[str, Any]], dict[str, Any]],
]:
    """Return the `(offered_fields, project)` pair for one registered schema."""
    if schema not in SCHEMAS:
        raise ValueError("unregistered source schema")
    return _offered(schema), _projector(schema)
