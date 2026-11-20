"""Explicit whole-claim completion through the existing queue and pending owners."""

from __future__ import annotations

import hashlib
import json
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_index.indexing.change_hints import _keys
from agent_index.indexing.component_application import (
    _component_contents, _policy, qualify_applied_component,
)
from agent_index.indexing.component_manifest import ComponentContent, ComponentIdentity, ComponentVersion
from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.pending_crawl import PendingCrawlConflict, PendingCrawls
from agent_index.indexing.point_claim import PendingPointClaim
from agent_index.indexing.point_hints import CommentVersionTarget
from agent_index.indexing.prepared_journal import PreparedJournal
from agent_index.indexing.prepared_points import PreparedPoints, _json
from agent_index.indexing.task_store import TaskRecord, TaskStatus, TaskStore
from agent_index.store.component_ledger import ComponentLedger
from agent_index.store.source_layout import SourceLayoutError


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class _Completion:
    claim: str
    pending: str
    generation: str
    scope: str
    policy: str
    domain: str
    components: tuple[ComponentContent, ...]

    def to_stats(self) -> dict[str, Any]:
        return {"point_completion": {
            "version": 1, "claim": self.claim, "pending": self.pending,
            "generation": self.generation, "scope": self.scope,
            "policy": self.policy, "domain": self.domain,
            "components": [
                {"component": content.component.to_dict(), "fingerprint": content.fingerprint,
                 "deleted": content.deleted} for content in self.components
            ],
        }}

    @classmethod
    def from_stats(cls, stats: object) -> _Completion:
        raw = _keys(_keys(stats, {"point_completion"})["point_completion"], {
            "version", "claim", "pending", "generation", "scope", "policy", "domain", "components",
        })
        if (
            type(raw["version"]) is not int or raw["version"] != 1
            or any(type(raw[key]) is not str or not raw[key] for key in (
                "claim", "pending", "generation", "scope", "policy", "domain",
            ))
            or type(raw["components"]) is not list
        ):
            raise ValueError("Invalid whole point completion outcome")
        components = []
        for item in raw["components"]:
            value = _keys(item, {"component", "fingerprint", "deleted"})
            if (type(value["fingerprint"]) is not str or not value["fingerprint"]
                    or type(value["deleted"]) is not bool):
                raise ValueError("Invalid completed point content")
            components.append(ComponentContent(
                ComponentVersion.from_dict(value["component"]), value["fingerprint"], value["deleted"],
            ))
        return cls(
            raw["claim"], raw["pending"], raw["generation"], raw["scope"],
            raw["policy"], raw["domain"], tuple(components),
        )

    def require_targets(self, claim: PendingPointClaim) -> None:
        expected: dict[ComponentIdentity, int] = {}
        for target in claim.hints.targets:
            identity = ComponentIdentity(
                target.kind, target.item_id,
                target.comment_id if isinstance(target, CommentVersionTarget) else None,
            )
            version = target.version if isinstance(target, CommentVersionTarget) else target.revision
            expected[identity] = max(expected.get(identity, 0), version)
        seen: set[ComponentIdentity] = set()
        points: set[tuple[str, str]] = set()
        for content in self.components:
            component = content.component
            current_identity, point = component.identity, (component.source, component.path)
            if (
                current_identity not in expected or current_identity in seen or point in points
                or component.source != claim.hints.canonical_family
                or component.version != expected[current_identity]
            ):
                raise ValueError("Completion does not cover the entire typed point claim")
            assert current_identity is not None
            seen.add(current_identity)
            points.add(point)
        if seen != expected.keys():
            raise ValueError("Completion does not cover the entire typed point claim")


def complete_point_claim(
    claim: PendingPointClaim, *, scope: str, paths: PathIndex, tasks: TaskStore,
    ledger: ComponentLedger, max_rows: int,
) -> TaskRecord:
    """Qualify every target, commit exact queue success, then retire its pending parent.

    The two SQLite WAL databases are not one crash-atomic transaction. Queue
    success retains the compact outcome before pending retirement; retries
    requalify native state, including after a lost return, without application.
    """
    config, store, domain = ledger._config, ledger._store, ledger._source_domain
    if (
        type(claim) is not PendingPointClaim
        or type(scope) is not str or not scope.strip()
        or Path(tasks._db_path).resolve() != (config.data_dir / "tasks.db").resolve()
        or Path(paths._db_path).resolve() != (config.data_dir / "path_index.db").resolve()
        or not all(Path(path).is_file() for path in (tasks._db_path, paths._db_path))
        or type(max_rows) is not int or max_rows < 1
        or domain.writing or getattr(domain._local, "operation", False)
    ):
        raise ValueError("Point completion requires the configured whole claim and independent owners")
    source = claim.hints.source
    with closing(paths._connect()) as connection:
        row = connection.execute(
            "SELECT scope, payload FROM pending_crawls WHERE source = ?", (source,),
        ).fetchone()
    journal = None
    if row is not None and json.loads(row["payload"]).get("version") == 9:
        _manifest, pending_claim = PendingCrawls.decode_point_pending(row["payload"], source)
        if pending_claim == claim:
            journal = PreparedJournal(
                paths, source, scope, row["payload"], point_claim=claim, require_existing=True,
            )
    # Acquire SQLite writers before native exclusion; no SQLite wait holds that fence.
    with closing(tasks._connect()) as queue, queue, closing(paths._connect()) as pending, pending:
        queue.execute("BEGIN IMMEDIATE")
        pending.execute("BEGIN IMMEDIATE")
        task = tasks._require_point_claim(queue, claim)
        if journal is not None:
            current_row = pending.execute(
                "SELECT scope, payload FROM pending_crawls WHERE source = ?", (source,),
            ).fetchone()
            if current_row is None or (
                current_row["scope"], current_row["payload"]
            ) != (scope, journal.payload):
                raise PendingCrawlConflict("Point completion belongs to a different pending parent")
            journal._require_parent(pending)
            manifest = PreparedPoints(journal).read_application()
            contents = _component_contents(manifest, config)
            components = tuple(contents[point] for point in sorted(contents))
            parent_digest, generation = _digest(journal.payload), journal.generation
        elif task.status == TaskStatus.COMPLETE:
            previous = _Completion.from_stats(task.result_stats)
            components, parent_digest, generation = previous.components, previous.pending, previous.generation
        else:
            raise PendingCrawlConflict("Point completion pending parent is missing")
        with domain.operation(store.content_store.db, point_scope=claim.hints.scope):
            # Reconstructing checks the current configured native/model owner mapping.
            current = ComponentLedger(config, store)
            completion = _Completion(
                _digest(_json(claim.to_dict())), parent_digest, generation,
                scope, _policy(config), domain.receipt()["domain"], components,
            )
            completion.require_targets(claim)
            if task.status == TaskStatus.COMPLETE and (
                _Completion.from_stats(task.result_stats) != completion
            ):
                raise PendingCrawlConflict("Completed point outcome belongs to different owners or inputs")
            for content in components:
                component = content.component
                evidence = current.get(
                    component.source, component.path, point_scope=claim.hints.scope,
                )
                if (
                    evidence is None or evidence.content.component.semantics != component.semantics
                    or not content.covered_by(evidence.content)
                ):
                    raise SourceLayoutError("Whole point claim is not durably applied or higher-covered")
                qualify_applied_component(
                    current, paths, evidence, scope=scope, max_rows=max_rows,
                    point_scope=claim.hints.scope,
                )
            if (
                completion.policy != _policy(config)
                or Path(tasks._db_path).resolve() != (config.data_dir / "tasks.db").resolve()
                or Path(paths._db_path).resolve() != (config.data_dir / "path_index.db").resolve()
            ):
                raise PendingCrawlConflict("Point completion policy or configured owners changed")
            if task.status != TaskStatus.COMPLETE:
                task = tasks._complete_point_claim(queue, claim, completion.to_stats())
            queue.commit()
            if journal is not None:
                PendingCrawls._retire_point_claim(
                    pending, source=source, scope=scope, payload=journal.payload,
                )
            pending.commit()
            return task
