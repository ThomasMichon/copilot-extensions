"""Optional bucket generations within the existing logical commit receipt."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from .clustering import source_bucket, validate_bucket

log = logging.getLogger(__name__)
MAX_BUCKETS = 256


def mutation_buckets(sources: Iterable[str] | None) -> set[str] | None:
    if sources is None:
        return None
    result: set[str] = set()
    for source in sources:
        if not isinstance(source, str):
            log.warning("Unqualified mutation source; retaining whole-table invalidation")
            return None
        bucket = source_bucket(source)
        try:
            validate_bucket(bucket)
        except ValueError:
            log.warning("Unqualified mutation bucket; retaining whole-table invalidation")
            return None
        result.add(bucket)
        if len(result) > MAX_BUCKETS:
            log.warning("Mutation bucket budget exceeded; retaining whole-table invalidation")
            return None
    return result


def _tables(projection: dict[str, Any]) -> dict[str, Any]:
    value = projection.get("bucket_generations")
    if value is None:
        return {}
    valid = (
        isinstance(value, dict) and type(value.get("version")) is int
        and value["version"] == 1 and isinstance(value.get("tables"), dict)
        and value["tables"].keys() == projection["tables"].keys()
    )
    count = 0
    if valid:
        for name, state in value["tables"].items():
            if (not isinstance(state, dict) or type(state.get("baseline")) is not int
                    or not 0 <= state["baseline"] <= projection["tables"][name]
                    or not isinstance(state.get("buckets"), dict)):
                valid = False
                break
            for bucket, generation in state["buckets"].items():
                try:
                    validate_bucket(bucket)
                except ValueError:
                    valid = False
                    break
                if (type(generation) is not int
                        or not state["baseline"] < generation <= projection["tables"][name]):
                    valid = False
                    break
            count += len(state["buckets"])
    if not valid or count > MAX_BUCKETS:
        log.warning("Invalid bucket generation metadata; retaining whole-table invalidation")
        return {}
    return value["tables"]


def bucket_generation(projection: dict[str, Any], table: str, bucket: str) -> tuple[str, int, int]:
    """Use only with a current, physically witnessed logical projection."""
    validate_bucket(bucket)
    state = _tables(projection).get(table)
    generation = projection["tables"][table]
    return (
        projection["epoch"],
        state["baseline"] if state is not None else generation,
        state["buckets"].get(bucket, 0) if state is not None else 0,
    )


def commit_buckets(
    previous: dict[str, Any] | None,
    projection: dict[str, Any],
    scoped: dict[str, set[str]],
) -> None:
    old = _tables(previous) if previous is not None else {}
    states: dict[str, Any] = {}
    for name, generation in projection["tables"].items():
        prior = previous["tables"].get(name) if previous is not None else None
        state = old.get(name, {"baseline": prior, "buckets": {}})
        if prior is None or (generation != prior and name not in scoped):
            states[name] = {"baseline": generation, "buckets": {}}
        else:
            buckets = dict(state["buckets"])
            if generation != prior:
                buckets.update(dict.fromkeys(scoped[name], generation))
            states[name] = {"baseline": state["baseline"], "buckets": buckets}
    if sum(len(state["buckets"]) for state in states.values()) > MAX_BUCKETS:
        log.warning("Bucket generation budget exceeded; retaining whole-table invalidation")
        return
    projection["bucket_generations"] = {"version": 1, "tables": states}
