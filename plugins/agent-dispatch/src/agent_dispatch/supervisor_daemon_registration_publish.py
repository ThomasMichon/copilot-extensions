"""Publish/withdraw declared registrations to the coordinator's store.

Extracted from ``supervisor_daemon.py`` purely to keep that module under its
module-size baseline as the repo grows (the same rationale
``coordinator_registries.py`` documents for its own extraction). This module
owns exactly one responsibility: mirroring the registrar/YAML **declared**
profile set into the coordinator's own ``registrations`` table, so a
coordinator-side policy consumer (e.g. ``TaskQueue.set_card``'s
``steering_disallowed_labels`` gate) can see a field a declaration sets --
the local declared-set merge in ``SupervisorDaemon._declared()`` only ever
fed that daemon's own subprocess-desired-state computation, never the
coordinator's ``GET /registrations`` surface (confirmed missing in PR
review, copilot-extensions#3731).
"""

from __future__ import annotations

import logging

from .registrations import RegistrationKind

log = logging.getLogger("agent-dispatch.supervisor-daemon")


def publish_declared_registrations(
    client, registrations: list[dict], *, published_ids: set[str]
) -> set[str]:
    """Upsert each declared registration into the coordinator's store, and
    withdraw any previously-published id no longer declared. Returns the new
    published-id set for the caller to persist across reconcile ticks.

    Best-effort and idempotent (``register_registration`` upserts by the
    declared id, preserving ``created_at``/``status``): a publish failure,
    or the coordinator being briefly unreachable, is logged and skipped --
    the caller's own local subprocess-desired-state merge has its own
    independent source of truth and must keep working even if the
    coordinator is down. Only ``RegistrationKind.DIRECT`` kinds are
    published; a ``plugin-companion`` declaration has no coordinator-side
    registration counterpart and is skipped (never published, never tracked
    for withdrawal).

    A dropped declaration is actively removed from the coordinator's store,
    not merely left behind: a stale ``declared:...`` row would otherwise
    remain visible to ``list_registrations()`` forever, which would make a
    store-backed desired-set computation keep treating a withdrawn unit as
    desired after its registrar/YAML source no longer lists it (a
    regression this same fix introduced and a PR review caught).
    """
    new_published: set[str] = set()
    for reg in registrations:
        kind = reg.get("kind")
        if kind not in RegistrationKind.DIRECT:
            continue
        rid = reg.get("id")
        new_published.add(rid)
        try:
            client.register_registration(
                kind,
                reg["spec"],
                reg_id=rid,
                machine=reg.get("machine"),
                env=reg.get("env", "default"),
            )
        except Exception:  # pragma: no cover - coordinator reachability varies
            log.exception("failed to publish declared registration %s to the coordinator", rid)
    for stale_id in published_ids - new_published:
        try:
            client.remove_registration(stale_id)
        except Exception:  # pragma: no cover - coordinator reachability varies
            log.exception(
                "failed to withdraw stale declared registration %s from the coordinator",
                stale_id,
            )
    return new_published
