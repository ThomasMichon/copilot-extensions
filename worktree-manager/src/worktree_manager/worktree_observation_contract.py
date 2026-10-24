"""Bounded v2 observation admission without an engine-runtime dependency."""

from __future__ import annotations

import math

CAPABILITY = "worktrees-observations-v2"
FACTS = frozenset({"record", "git", "mux", "session"})


def validate_observation(item: dict, revision: int) -> None:
    if (
        not isinstance(item, dict) or set(item) not in (
            {"id", "facts"}, {"id", "facts", "derivations"},
        )
        or not isinstance(item["id"], str) or not item["id"]
        or len(item["id"].encode("utf-8")) > 256
        or item["id"] in {".", ".."} or any(c in item["id"] for c in ("/", "\\", "\x00"))
        or not isinstance(item["facts"], dict) or set(item["facts"]) - FACTS
    ):
        raise ValueError("invalid current-roster observation")
    for fact in item["facts"].values():
        if not isinstance(fact, dict) or set(fact) != {
            "quality", "observed_at", "observation_revision",
        }:
            raise ValueError("invalid observation fact")
        at, version = fact["observed_at"], fact["observation_revision"]
        if (
            type(version) is not int or not 1 <= version <= revision
            or fact["quality"] not in {"current", "unavailable"}
            or (at is not None and (
                type(at) not in (int, float) or not math.isfinite(at) or at < 0
            ))
            or (fact["quality"] == "current" and at is None)
            or (fact["quality"] == "unavailable" and at is not None)
        ):
            raise ValueError("invalid observation provenance")
    if "derivations" in item:
        derived = item["derivations"]
        if not isinstance(derived, dict) or set(derived) != {"closure"}:
            raise ValueError("invalid observation derivations")
        closure = derived["closure"]
        if (
            not isinstance(closure, dict) or set(closure) != {"computed_at", "observation_revision"}
            or not isinstance(closure["computed_at"], str)
            or len(closure["computed_at"].encode("utf-8")) > 4096
            or type(closure["observation_revision"]) is not int
            or not 1 <= closure["observation_revision"] <= revision
        ):
            raise ValueError("invalid closure observation")


def validate_rows(rows: list[dict], items, revision: int, *, complete: bool) -> dict:
    by_id = {}
    if items is not None:
        if not isinstance(items, list) or len(items) != len(rows):
            raise ValueError("incomplete observation roster")
        for item in items:
            validate_observation(item, revision)
            if item["id"] in by_id:
                raise ValueError("duplicate observation identity")
            by_id[item["id"]] = item
    elif complete:
        raise ValueError("missing observations")
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("invalid semantic row")
        version = row.get("semantic_revision")
        if type(version) is not int or not 1 <= version <= revision:
            raise ValueError("invalid semantic revision")
        provenance = row.get("fact_provenance") or {}
        if not isinstance(provenance, dict) or set(provenance) - FACTS or any(
            not isinstance(value, dict) or set(value) != {"quality"}
            or value["quality"] not in {"current", "unavailable"}
            for value in provenance.values()
        ):
            raise ValueError("invalid semantic fact provenance")
        if items is not None:
            item = by_id.get(row.get("id"))
            facts = None if item is None else item["facts"]
            if (
                facts is None or set(facts) != set(provenance)
                or any(provenance[name]["quality"] != fact["quality"]
                       for name, fact in facts.items())
            ):
                raise ValueError("observation does not match semantic row")
            if "derivations" in item and (
                not isinstance(row.get("closure"), dict) or "computed_at" in row["closure"]
            ):
                raise ValueError("closure observation does not match semantic row")
    return by_id


def check_advance(previous: dict | None, current: dict, *, field: str) -> None:
    if type(current.get(field)) is not int or current[field] < 1:
        raise ValueError("invalid observation or semantic revision")
    if previous is None:
        return
    previous = {k: v for k, v in previous.items() if not k.startswith("_")}
    current = {k: v for k, v in current.items() if not k.startswith("_")}
    if (
        current.get(field, 0) < previous[field]
        or (current.get(field) == previous[field] and current != previous)
    ):
        raise ValueError("observation or semantic revision regressed or conflicted")


def check_observation_advance(previous: dict | None, current: dict) -> None:
    if previous is None:
        return
    for group in ("facts", "derivations"):
        for name, value in current.get(group, {}).items():
            check_advance(previous.get(group, {}).get(name), value, field="observation_revision")
