"""Orthogonal activity metadata in the existing repository-cache envelope."""

from __future__ import annotations

import copy
import re
from collections.abc import Callable
from typing import Any


def extend_facts(value: dict[str, Any]) -> None:
    """Preserve prior facts without inventing their missing activity coverage."""
    value.update(reviews_complete=None, updated_at=None)
    for review in value["reviews"]:
        review["submitted_at"] = None


def adopt(
    store: Any, *, lifecycle_checkpoint: Callable[[], None], expires_at: int,
    checkpoint: Callable[[], None] | None = None,
) -> dict[str, str]:
    """Upgrade a valid continuing envelope while its existing owner excludes old readers."""
    if not store.repository_activity or not callable(lifecycle_checkpoint):
        raise ValueError("selected activity owner and reader lifecycle required")
    store.check_current(expires_at, checkpoint)
    with store._lock() as lock:
        store.check_current(expires_at, checkpoint)
        lifecycle_checkpoint()
        document = store._read_document()
        if "repository_activity_version" in document:
            store._validate_owner(document)
            return {"status": "already_adopted"}
        prior = copy.copy(store)
        prior.repository_activity = False
        prior._validate_owner(document)
        selected = copy.deepcopy(document)
        state = selected["repository_cache"]
        for repository in state["repositories"]:
            for pull in repository["pulls"]:
                pull["activity"] = None
                if pull["facts"] is not None:
                    extend_facts(pull["facts"])
        for event in state["events"]:
            if event["facts"] is not None and event["kind"] != "coverage":
                extend_facts(event["facts"])
        selected["repository_activity_version"] = 1
        store._validate_owner(selected)
        lock.assert_owned()
        lifecycle_checkpoint()
        store.check_current(expires_at, checkpoint)
        from .watch_storage import _atomic_write_json
        _atomic_write_json(store.path, selected)
        store.check_current(expires_at, checkpoint)
        return {"status": "adopted"}


def validate(value: object, observed_at: int | None) -> None:
    """Validate stable source-observation causes without requiring retained event bodies."""
    if value is None:
        return
    if not isinstance(value, dict) or set(value) != {"candidate", "conflict"}:
        raise ValueError("invalid repository activity")
    for name in ("candidate", "conflict"):
        cause = value[name]
        if cause is None and name == "conflict":
            continue
        if not isinstance(cause, dict) or set(cause) != {"event_id", "since"} or (
            not isinstance(cause["event_id"], str)
            or re.fullmatch(r"[a-f0-9]{32}", cause["event_id"]) is None
            or type(cause["since"]) is not int or observed_at is None
            or not 0 <= cause["since"] <= observed_at
        ):
            raise ValueError("invalid repository activity cause")


def advance(
    previous: dict[str, Any] | None, facts: dict[str, Any], activity: dict[str, Any] | None,
    event_id: str | None, observed_at: int,
) -> dict[str, Any]:
    """Unchanged refreshes retain original candidate/conflict cause and age."""
    candidate_changed = previous is None or any(
        previous[name] != facts[name] for name in ("head_sha", "base_sha")
    )
    if activity is None or candidate_changed:
        if event_id is None:
            raise ValueError("candidate cause requires its atomic publication event")
        activity = {"candidate": {"event_id": event_id, "since": observed_at}, "conflict": None}
    if facts["mergeable"] is False and activity["conflict"] is None:
        if event_id is None:
            raise ValueError("conflict cause requires its atomic publication event")
        activity["conflict"] = {"event_id": event_id, "since": observed_at}
    elif facts["mergeable"] is True:
        activity["conflict"] = None
    return activity
