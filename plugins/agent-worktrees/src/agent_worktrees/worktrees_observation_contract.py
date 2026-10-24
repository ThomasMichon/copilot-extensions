"""Negotiated observation metadata, separate from semantic row versions."""

from __future__ import annotations

import json
import math

from .worktrees_projection_index import ProjectionError

CAPABILITY = "worktrees-observations-v2"
VERSION = 2
FACTS = frozenset({"record", "git", "mux", "session"})

# Additive tables deliberately leave the v1 schema and writer election intact.
SCHEMA = """
CREATE TABLE IF NOT EXISTS worktrees_projection_observation_head (
    project TEXT PRIMARY KEY,
    generation TEXT NOT NULL,
    revision INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS worktrees_projection_observation (
    project TEXT NOT NULL,
    worktree_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (project, worktree_id)
);
"""


def negotiate(request: dict, capabilities) -> dict:
    """Use previously discovered capabilities; never probe ownership on a read."""
    return {**request, "contract_version": VERSION if CAPABILITY in capabilities else 1}


def semantic_row(row: dict) -> dict:
    result = dict(row)
    provenance = row.get("fact_provenance")
    if isinstance(provenance, dict):
        result["fact_provenance"] = {
            name: {k: v for k, v in fact.items() if k != "observed_at"}
            if name in FACTS and isinstance(fact, dict) else fact
            for name, fact in provenance.items()
        }
    if isinstance(row.get("closure"), dict):
        result["closure"] = {k: v for k, v in row["closure"].items() if k != "computed_at"}
    return result


def observation(row: dict, revision: int, previous: dict | None = None) -> dict:
    facts = {}
    old = {} if previous is None else previous["facts"]
    for name, fact in (row.get("fact_provenance") or {}).items():
        if (
            name not in FACTS or not isinstance(fact, dict)
            or set(fact) != {"quality", "observed_at"}
        ):
            raise ProjectionError("invalid observation fact group")
        value = {"quality": fact.get("quality"), "observed_at": fact.get("observed_at")}
        prior = old.get(name)
        value["observation_revision"] = (
            prior["observation_revision"]
            if prior is not None and all(prior.get(k) == v for k, v in value.items())
            else revision
        )
        facts[name] = value
    result = {"id": row["id"], "facts": facts}
    closure = row.get("closure")
    if isinstance(closure, dict) and "computed_at" in closure:
        prior = (previous or {}).get("derivations", {}).get("closure", {})
        at = closure["computed_at"]
        result["derivations"] = {"closure": {
            "computed_at": at,
            "observation_revision": prior.get("observation_revision", revision)
            if prior.get("computed_at") == at else revision,
        }}
    validate_observation(result, revision)
    return result


def validate_observation(value: dict, revision: int) -> None:
    from .worktrees_projection import _identity

    if not isinstance(value, dict) or set(value) not in (
        {"id", "facts"}, {"id", "facts", "derivations"},
    ):
        raise ProjectionError("invalid observation fields")
    _identity(value["id"], name="observation id")
    facts = value["facts"]
    if not isinstance(facts, dict) or set(facts) - FACTS:
        raise ProjectionError("invalid observation fact groups")
    for fact in facts.values():
        if not isinstance(fact, dict) or set(fact) != {
            "quality", "observed_at", "observation_revision",
        }:
            raise ProjectionError("invalid observation fact")
        version, at = fact["observation_revision"], fact["observed_at"]
        if (
            type(version) is not int or not 1 <= version <= revision
            or fact["quality"] not in {"current", "unavailable"}
            or (at is not None and (
                type(at) not in (int, float) or not math.isfinite(at) or at < 0
            ))
            or (fact["quality"] == "current" and at is None)
            or (fact["quality"] == "unavailable" and at is not None)
        ):
            raise ProjectionError("invalid observation provenance")
    if "derivations" in value:
        derived = value["derivations"]
        if not isinstance(derived, dict) or set(derived) != {"closure"}:
            raise ProjectionError("invalid observation derivations")
        closure = derived["closure"]
        if (
            not isinstance(closure, dict) or set(closure) != {"computed_at", "observation_revision"}
            or not isinstance(closure["computed_at"], str)
            or len(closure["computed_at"].encode("utf-8")) > 4096
            or type(closure["observation_revision"]) is not int
            or not 1 <= closure["observation_revision"] <= revision
        ):
            raise ProjectionError("invalid closure observation")


def validate_wire_row(row: dict) -> None:
    """Bound the complete schema-admitted v2 row, including its semantic revision."""
    from .worktrees_projection import MAX_ROW_BYTES

    payload = json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if len(payload.encode("utf-8")) > MAX_ROW_BYTES:
        raise ProjectionError("current v2 wire row exceeds byte bound")


def validate_rows(rows: list[dict], observations: list[dict], revision: int) -> None:
    from .worktrees_projection import _encode_row

    if not isinstance(observations, list) or len(observations) != len(rows):
        raise ProjectionError("incomplete observation roster")
    by_id = {}
    for value in observations:
        validate_observation(value, revision)
        if value["id"] in by_id:
            raise ProjectionError("duplicate observation identity")
        by_id[value["id"]] = value
    for row in rows:
        if not isinstance(row, dict):
            raise ProjectionError("invalid semantic row")
        version = row.get("semantic_revision")
        if type(version) is not int or not 1 <= version <= revision:
            raise ProjectionError("invalid semantic revision")
        semantic = {k: v for k, v in row.items() if k != "semantic_revision"}
        _encode_row(semantic)
        validate_wire_row(row)
        value = by_id.pop(row["id"], None)
        provenance = semantic.get("fact_provenance") or {}
        if (
            value is None or set(value["facts"]) != set(provenance)
            or any(provenance[name] != {"quality": fact["quality"]}
                   for name, fact in value["facts"].items())
        ):
            raise ProjectionError("observation does not match semantic row")
        if "derivations" in value and (
            not isinstance(row.get("closure"), dict) or "computed_at" in row["closure"]
        ):
            raise ProjectionError("closure observation does not match semantic row")
    if by_id:
        raise ProjectionError("unknown observation identity")


def with_observation(row: dict, value: dict) -> dict:
    """Reconstruct truthful v1 facts without changing the durable semantic row."""
    result = semantic_row(row)
    if value["facts"]:
        result["fact_provenance"] = {
            name: {"quality": fact["quality"], "observed_at": fact["observed_at"]}
            for name, fact in value["facts"].items()
        }
    if "derivations" in value:
        result["closure"] = {
            **row["closure"], "computed_at": value["derivations"]["closure"]["computed_at"],
        }
    return result


def check_advance(previous: dict | None, current: dict, *, field: str) -> None:
    if type(current.get(field)) is not int or current[field] < 1:
        raise ProjectionError("invalid observation or semantic revision")
    if previous is not None and (
        current[field] < previous[field]
        or (current.get(field) == previous[field] and current != previous)
    ):
        raise ProjectionError("observation or semantic revision regressed or conflicted")


def check_observation_advance(previous: dict | None, current: dict) -> None:
    if previous is None:
        return
    for group in ("facts", "derivations"):
        for name, value in current.get(group, {}).items():
            check_advance(previous.get(group, {}).get(name), value, field="observation_revision")
