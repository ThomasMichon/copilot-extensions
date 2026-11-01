"""Strict required metadata fields with additive response compatibility."""

from __future__ import annotations

import math

from .pool_instances import PoolDefinitionError, PoolInstanceDefinition, ordered_pool_instances
from .pool_publication_custody import PoolPublicationError
from .queue_pool_catalog import PoolPublicationScope


def read_pool_scope(value: object) -> PoolPublicationScope:
    if not isinstance(value, dict) or not {
        "machine", "env", "publisher_id", "generation", "revision", "state",
        "input_revision", "registration_revision", "valid_until", "definitions",
        "definitions_generation", "definitions_revision", "reason",
    } <= value.keys():
        raise PoolPublicationError("pool metadata response lacks required scope fields")
    if any(
        not isinstance(value[key], str) or not value[key].strip()
        or value[key] != value[key].strip()
        for key in ("machine", "env", "publisher_id")
    ) or any(
        type(value[key]) is not int or value[key] < minimum
        for key, minimum in (("generation", 1), ("revision", 0))
    ):
        raise PoolPublicationError("pool metadata response has invalid scope identity")
    if value["state"] not in ("pending", "complete", "unavailable"):
        raise PoolPublicationError("pool metadata response has unsupported scope state")
    if any(
        value[key] is not None and (
            not isinstance(value[key], str) or not value[key].strip()
            or value[key] != value[key].strip()
        ) for key in ("input_revision", "reason")
    ) or any(
        value[key] is not None and (type(value[key]) is not int or value[key] < minimum)
        for key, minimum in (
            ("registration_revision", 0), ("definitions_generation", 1),
            ("definitions_revision", 1),
        )
    ):
        raise PoolPublicationError("pool metadata response has invalid retained attribution")
    valid_until = value["valid_until"]
    if valid_until is not None:
        try:
            finite = type(valid_until) in (int, float) and math.isfinite(valid_until)
        except OverflowError as exc:
            raise PoolPublicationError("pool metadata response has invalid freshness") from exc
        if not finite:
            raise PoolPublicationError("pool metadata response has invalid freshness")
    if value["state"] != "pending" and (
        valid_until is None or value["input_revision"] is None
        or value["registration_revision"] is None
        or (value["state"] == "complete" and (
            value["reason"] is not None or value["definitions_generation"] is None
            or value["definitions_revision"] is None
        ))
        or (value["state"] == "unavailable" and value["reason"] is None)
    ):
        raise PoolPublicationError("pool metadata response has inconsistent publication state")
    generation = value["definitions_generation"]
    revision = value["definitions_revision"]
    if (generation is None) != (revision is None) or (
        generation is not None and (
            generation > value["generation"]
            or (value["state"] == "pending" and generation >= value["generation"])
            or (value["state"] == "unavailable" and generation == value["generation"]
                and revision >= value["revision"])
        )
    ) or (value["state"] == "complete" and (
        generation != value["generation"] or revision != value["revision"]
    )):
        raise PoolPublicationError("pool metadata response has inconsistent definition attribution")
    if not isinstance(value["definitions"], list):
        raise PoolPublicationError("pool metadata response lacks the full definition array")
    try:
        definitions = ordered_pool_instances(
            PoolInstanceDefinition.from_dict(item) for item in value["definitions"]
        )
    except PoolDefinitionError as exc:
        raise PoolPublicationError("pool metadata response has invalid definitions") from exc
    if any((item.machine, item.env) != (value["machine"], value["env"]) for item in definitions):
        raise PoolPublicationError("pool metadata response has foreign definitions")
    if definitions and generation is None:
        raise PoolPublicationError("pool metadata response has unattributed definitions")
    cohort_revisions = []
    for key in ("enrollment_cohort_revision", "definitions_cohort_revision"):
        revision = value.get(key)
        if revision is not None and (type(revision) is not int or revision < 1):
            raise PoolPublicationError("pool metadata response has invalid configured cohort attribution")
        cohort_revisions.append(revision)
    return PoolPublicationScope(
        value["machine"], value["env"], value["publisher_id"], value["generation"],
        value["revision"], value["state"], value["input_revision"],
        value["registration_revision"], valid_until, definitions,
        value["definitions_generation"], value["definitions_revision"], value["reason"],
        *cohort_revisions,
    )


def read_pool_metadata_result(value: object) -> PoolPublicationScope:
    if not isinstance(value, dict) or (
        value.get("protocol") != "pool_catalog_transport/v1"
        or type(value.get("version")) is not int or value["version"] != 1
    ):
        raise PoolPublicationError("pool metadata response is malformed or unsupported")
    return read_pool_scope(value.get("scope"))
