"""Render helpers for ``GET /api/v1/worktrees/{id}/sessions``'s response.

Split out of ``routes/worktrees.py`` to keep that already-large module from
growing further (see ``tools/module-size-baseline.json``) -- same pattern as
:mod:`agent_bridge.cold_store_views` for the cold-store-provider fallback.
Not to be confused with :mod:`agent_bridge.worktree_lineage`, which *writes*
session-lifecycle facts into the agent-worktrees ground layer on the ACP
handoff path; this module only *renders* what the ground layer already
returned from ``list-sessions --json``.
"""

from __future__ import annotations

from typing import Any


def lineage_fields(envelope: dict[str, Any]) -> dict[str, Any]:
    """Forward the ground-layer's head-succession/fork-lineage data.

    ``<project> list-sessions --worktree <id> --json`` already carries this
    (see ``session_tracking_cli.cmd_list_sessions``): ``head_revision`` (the
    ledger's monotonic head-transition counter), ``handoffs`` (every pending
    handoff -- ``predecessor``/``candidate`` pairs; more than one entry means
    a fork, e.g. a second handoff opened before the first's candidate ever
    consumed it), ``controller_revision``/``controllers`` (the live
    parent-session binding, when any), and ``controller_findings`` (each
    with its own ``lineage`` list -- the full predecessor chain the ledger
    replayed to reach the current head). None of this was previously
    forwarded through ``GET /api/v1/worktrees/{id}/sessions``, so a caller
    had no way to see a forked/stuck handoff chain short of shelling out to
    ``head-session`` itself. Derived straight from the ground-layer
    envelope -- the bridge keeps no lineage state of its own
    (derive-dont-duplicate), and every field defaults to an empty/zero
    value so a legacy or malformed envelope never breaks the response.
    """
    head_revision = envelope.get("head_revision")
    handoffs = envelope.get("handoffs")
    controller_revision = envelope.get("controller_revision")
    controllers = envelope.get("controllers")
    controller_findings = envelope.get("controller_findings")
    return {
        "head_revision": head_revision if isinstance(head_revision, int) else 0,
        "handoffs": handoffs if isinstance(handoffs, list) else [],
        "controller_revision": (
            controller_revision if isinstance(controller_revision, int) else 0
        ),
        "controllers": controllers if isinstance(controllers, list) else [],
        "controller_findings": (
            controller_findings if isinstance(controller_findings, list) else []
        ),
    }
