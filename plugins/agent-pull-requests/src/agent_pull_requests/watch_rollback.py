"""Attributable predecessor restoration through the public native routing CAS."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from zdd import publication_history, routing

if TYPE_CHECKING:
    from .watch_installation import WatchInstallation
    from .watch_journal import WatchJournal


def _body(table: dict) -> dict:
    return {key: value for key, value in table.items() if key != "epoch"}


def restoration(document: dict) -> tuple[dict, dict]:
    from .watch_journal import _json_object

    raw = document["rollbackRoute"]
    if not isinstance(raw, str) or document["prior"]["route"] is None:
        raise PermissionError("watch route restoration has no captured predecessor route")
    before = _json_object(bytes.fromhex(raw))
    prior = _json_object(bytes.fromhex(document["prior"]["route"]))
    target = (
        _json_object(bytes.fromhex(document["targetRoute"]))
        if document["targetRoute"] is not None else {}
    )
    active = routing.Endpoint.from_dict(before.get("active", {}))
    old = routing.Endpoint.from_dict(prior.get("active", {}))
    if (
        active is None or old is None
        or before["active"] not in (prior.get("active"), target.get("active"))
        or publication_history.read(before) is None
        or old.pid != document["predecessor"]["pid"]
        or old.process_start_time != document["predecessor"]["startTime"]
        or old.version != document["predecessor"]["runtimeVersion"]
    ):
        raise PermissionError("watch route restoration lacks exact native publication custody")
    generations = []
    for role in ("active", "previous"):
        if before.get(role) is not None:
            endpoint = routing.Endpoint.from_dict(before[role])
            if endpoint is None or before[role] != endpoint.to_dict() or (
                type(before[role].get("generation")) is not int
                or not 0 <= endpoint.generation < 2**63
            ):
                raise PermissionError("watch route restoration has an unverifiable native row")
            generations.append(endpoint.generation)
    generation = max(generations) + 1
    if generation >= 2**63:
        raise PermissionError("watch route restoration generation is exhausted")
    following = {"active": {**old.to_dict(), "generation": generation}}
    if active.port != old.port:
        following["previous"] = active.to_dict()
    following[publication_history.KEY] = publication_history.updated(before, following)
    return before, following


def validate_restoration(document: dict) -> None:
    if document["rollbackRoute"] is None:
        return
    if (
        document["phase"] not in {"rolling-back", "rolled-back"}
        or document["predecessor"] is None
        or document["launchInstanceId"] is not None and document["successor"] is None
        or document["successor"] is not None and "successor" not in document["exited"]
    ):
        raise PermissionError("watch route restoration precedes exact successor exit")
    restoration(document)


def require_rollback_selection(installation: WatchInstallation, document: dict) -> None:
    from .watch_journal import _json_object, _selection

    selected = _selection(installation)
    if any(selected[key] != document["prior"][key] for key in (
        "current", "lastKnownGood", "manifest",
    )):
        raise PermissionError("watch rollback selection has not been restored")
    if document["rollbackRoute"] is None:
        matches = selected["route"] == document["prior"]["route"]
    else:
        validate_restoration(document)
        _, expected = restoration(document)
        matches = selected["route"] is not None and (
            _body(_json_object(bytes.fromhex(selected["route"]))) == expected
        )
    if not matches:
        raise PermissionError("watch rollback selection has no attributable restored route")


def restore_route(
    journal: WatchJournal, *, expected_active: routing.Endpoint, deadline: float,
) -> routing.Endpoint:
    """Persist intent inside native CAS, retaining its generation and sticky history."""
    from .watch_journal import _regular_bytes

    if time.monotonic() >= deadline:
        raise TimeoutError("watch route restoration deadline expired")
    document = journal._current()
    if document["phase"] != "rolling-back" or document["predecessor"] is None:
        raise PermissionError("watch route restoration is not authorized in this phase")
    if document["rollbackRoute"] is None:
        raw = _regular_bytes(journal.installation.run_root / "active.json")
        if raw is None:
            raise PermissionError("watch native route cannot be treated as absent")
        document["rollbackRoute"] = raw.hex()
    validate_restoration(document)
    before, expected = restoration(document)
    actual = routing.read_table(journal.installation.run_root, strict=True)
    if time.monotonic() >= deadline:
        raise TimeoutError("watch route restoration deadline expired")
    if _body(actual or {}) == expected:
        if journal._document["rollbackRoute"] is None:
            raise PermissionError("watch restored route has no prior durable intent")
        return routing.Endpoint.from_dict(expected["active"])
    active = routing.Endpoint.from_dict(before["active"])
    if active != expected_active:
        raise PermissionError("watch native route differs from expected rollback owner")
    protected = {**before, publication_history.KEY: expected[publication_history.KEY]}

    def record_intent(_active: dict | None) -> str | None:
        journal._current()
        current = routing.read_table(journal.installation.run_root, strict=True)
        if current != before and (
            journal._document["rollbackRoute"] is None or current != protected
        ):
            raise PermissionError("watch native route changed before restoration")
        if journal._document["rollbackRoute"] is None:
            journal._write(document)
        if time.monotonic() >= deadline:
            raise TimeoutError("watch route restoration deadline expired")
        return None

    old = routing.Endpoint.from_dict(expected["active"])
    restored, _ = routing.publish_active_with_previous_guarded(
        journal.installation.run_root, bind=old.bind, port=old.port, pid=old.pid,
        version=old.version, generation=old.generation,
        process_start_time=old.process_start_time, demote_existing=True,
        expected_active=expected_active, refuse_current=record_intent,
        lock_deadline=deadline,
    )
    journal._current()
    current = routing.read_table(journal.installation.run_root, strict=True)
    if _body(current or {}) != expected:
        raise PermissionError("watch native restoration requires explicit reconciliation")
    if time.monotonic() >= deadline:
        raise TimeoutError("watch route restoration deadline expired")
    return restored
