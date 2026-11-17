"""Exact-owner route withdrawal and superseded endpoint release."""

from __future__ import annotations

import os
from collections.abc import Callable

from . import routing
from .darwin_identity import valid_start_time
from .routing import Endpoint


def withdraw_active_guarded(
    config_dir: str | os.PathLike[str], *, expected_active: Endpoint,
    refuse_current: Callable[[dict | None], str | None], lock_deadline: float,
) -> dict:
    if not callable(refuse_current):
        raise TypeError("exact withdrawal requires an authority validator")
    if (
        not isinstance(expected_active, Endpoint) or type(expected_active.pid) is not int
        or expected_active.pid <= 0 or not valid_start_time(expected_active.process_start_time)
        or Endpoint.from_dict(expected_active.to_dict()) != expected_active
    ):
        raise routing.ActivePublicationRefused("exact withdrawal requires native process identity")
    with routing._routing_lock(config_dir, deadline=lock_deadline):
        current = routing._read_mutation_table(config_dir)
        raw = current.get("active")
        refusal = routing._refuse_or_changed(
            raw if isinstance(raw, dict) else None, expected_active=expected_active,
            refuse_current=refuse_current, require_expected_active=True,
        )
        if refusal:
            raise routing.ActivePublicationRefused(refusal)
        following = {key: value for key, value in current.items() if key != "active"}
        following["previous"] = expected_active.to_dict()
        routing._atomic_write(routing.routing_table_path(config_dir), following)
        return routing._read_mutation_table(config_dir)


def release_previous_guarded(
    config_dir: str | os.PathLike[str], *,
    expected_active: Endpoint, expected_previous: Endpoint, lock_deadline: float,
) -> bool:
    with routing._routing_lock(config_dir, deadline=lock_deadline):
        current = routing._read_mutation_table(config_dir)
        if (
            current.get("active") != expected_active.to_dict()
            or current.get("previous") != expected_previous.to_dict()
        ):
            return False
        following = {key: value for key, value in current.items() if key != "previous"}
        routing._atomic_write(routing.routing_table_path(config_dir), following)
        return True
