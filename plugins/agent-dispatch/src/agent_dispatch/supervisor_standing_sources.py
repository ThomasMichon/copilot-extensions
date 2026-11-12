"""Reconcile complete standing source sets without creating process registrations."""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from .identity import canonicalize_remote
from .pool_source_snapshot import DeclarationRead
from .registrar_source_revision import source_revision_follows
from .registrar_standing import standing_source_entries

log = logging.getLogger("agent-dispatch.supervisor-daemon")


def reconcile_standing_sources(
    source: DeclarationRead, client: Any, *, deadline: float, before_write: Callable[[], None],
) -> None:
    """Apply only namespace-complete observations with a qualified source revision."""
    observations = defaultdict(list)
    for observation in source.source_observations:
        observations[observation.namespace].append(observation)
    for namespace, group in sorted(observations.items()):
        declarations = [item for item in source if item.owner == namespace]
        first = group[0]
        if (
            not source.complete or not namespace or first.root is None
            or first.revision_state not in {"ordered", "unordered"}
            or (first.revision_state == "ordered" and first.revision is None)
            or (first.revision_state == "unordered" and first.revision is not None)
            or any(
                item.status != "complete" or item.root != first.root
                or (item.revision_state, item.revision) != (first.revision_state, first.revision)
                for item in group
            )
            or {name for item in group for name in item.declaration_names}
            != {item.name for item in declarations}
        ):
            continue
        try:
            entries = defaultdict(list)
            for entry in standing_source_entries(declarations):
                repo = canonicalize_remote(entry.repo)
                if not repo:
                    raise ValueError("standing source requires a repository lane")
                entries[repo].append({
                    "title": entry.title, "declaration": dict(entry.declaration), "enabled": entry.enabled,
                })
            if time.monotonic() >= deadline:
                raise TimeoutError("standing source deadline exhausted")
            retained = client.standing_source_repositories(namespace, deadline=deadline)
            repositories = set(entries)
            for repo in retained:
                canonical = canonicalize_remote(repo)
                if not canonical:
                    raise ValueError("standing source inventory requires a repository lane")
                repositories.add(canonical)
            for repo in sorted(repositories):
                if time.monotonic() >= deadline:
                    raise TimeoutError("standing source deadline exhausted")
                previous = client.standing_source_observation(repo, namespace, deadline=deadline)
                if first.revision_state == "ordered" and not source_revision_follows(
                    first.root, previous, first.revision, deadline=deadline,
                ):
                    log.warning("standing source reconciliation withheld: source-revision-not-forward")
                    continue
                before_write()
                client.reconcile_standing_source_set(
                    repo, namespace, entries.get(repo, []),
                    revision=first.revision, expected_observation=previous, deadline=deadline,
                )
        except Exception as exc:
            log.warning("standing source reconciliation withheld: error_type=%s", type(exc).__name__)
