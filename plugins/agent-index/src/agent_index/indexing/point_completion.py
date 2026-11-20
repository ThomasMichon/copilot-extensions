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
    pending: str | None
    generation: str | None
    scope: str
    policy: str
    domain: str
    components: tuple[ComponentContent, ...]
    higher_covered: bool = False

    def to_stats(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "version": 2 if self.higher_covered else 1, "claim": self.claim, "scope": self.scope,
            "policy": self.policy, "domain": self.domain,
            "components": [
                {"component": content.component.to_dict(), "fingerprint": content.fingerprint,
                 "deleted": content.deleted} for content in self.components
            ],
        }
        if self.higher_covered:
            result["mode"] = "higher-covered"
        else:
            result.update(pending=self.pending, generation=self.generation)
        return {"point_completion": result}

    @classmethod
    def from_stats(cls, stats: object) -> _Completion:
        raw = _keys(stats, {"point_completion"})["point_completion"]
        if type(raw) is not dict:
            raise ValueError("Invalid whole point completion outcome")
        higher = type(raw.get("version")) is int and raw["version"] == 2
        fields = {"version", "claim", "scope", "policy", "domain", "components"}
        raw = _keys(raw, fields | ({"mode"} if higher else {"pending", "generation"}))
        if (
            type(raw["version"]) is not int or raw["version"] != (2 if higher else 1)
            or higher and raw["mode"] != "higher-covered"
            or any(type(raw[key]) is not str or not raw[key] for key in (
                "claim", "scope", "policy", "domain",
            ))
            or not higher and any(type(raw[key]) is not str or not raw[key]
                                  for key in ("pending", "generation"))
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
            raw["claim"], raw.get("pending"), raw.get("generation"), raw["scope"],
            raw["policy"], raw["domain"], tuple(components), higher,
        )

    def require_targets(self, claim: PendingPointClaim) -> None:
        self.require_versions(
            claim, tuple(content.component for content in self.components), higher=self.higher_covered,
        )

    @staticmethod
    def require_versions(
        claim: PendingPointClaim, components: tuple[ComponentVersion, ...], *, higher: bool = False,
    ) -> None:
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
        for component in components:
            current_identity, point = component.identity, (component.source, component.path)
            if (
                current_identity not in expected or current_identity in seen or point in points
                or component.source != claim.hints.canonical_family
                or (component.version <= expected[current_identity] if higher
                    else component.version != expected[current_identity])
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
    result = _complete_point_claim(
        claim, scope=scope, paths=paths, tasks=tasks, ledger=ledger, max_rows=max_rows,
    )
    assert result is not None
    return result


def complete_higher_point_claim(
    claim: PendingPointClaim, *, components: tuple[ComponentVersion, ...], scope: str,
    paths: PathIndex, tasks: TaskStore, ledger: ComponentLedger, max_rows: int,
) -> TaskRecord | None:
    """Complete an entirely superseded claim without acquisition or pending retirement.

    The adapter supplies the exact current representation mapping after fresh
    eligibility checks, under its source writer. Missing, equal or newer targets
    return None for ordinary acquisition. Uncertain native evidence refuses.
    """
    if type(components) is not tuple or any(type(item) is not ComponentVersion for item in components):
        raise ValueError("Higher-covered completion requires exact typed component versions")
    return _complete_point_claim(
        claim, scope=scope, paths=paths, tasks=tasks, ledger=ledger, max_rows=max_rows,
        higher_components=components,
    )


def _complete_point_claim(
    claim: PendingPointClaim, *, scope: str, paths: PathIndex, tasks: TaskStore,
    ledger: ComponentLedger, max_rows: int,
    higher_components: tuple[ComponentVersion, ...] | None = None,
) -> TaskRecord | None:
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
        previous = _Completion.from_stats(task.result_stats) if task.status == TaskStatus.COMPLETE else None
        higher = previous.higher_covered if previous is not None else higher_components is not None
        parent_digest: str | None
        generation: str | None
        if higher_components is not None and previous is not None:
            _Completion.require_versions(claim, higher_components)
            if not previous.higher_covered or {
                item.identity: (item.source, item.path, item.semantics) for item in higher_components
            } != {
                content.component.identity: (
                    content.component.source, content.component.path, content.component.semantics,
                ) for content in previous.components
            }:
                raise PendingCrawlConflict("Completed point outcome has different coverage inputs")
        if journal is not None:
            if higher:
                raise PendingCrawlConflict("Higher-covered completion cannot bypass pending acquisition")
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
        elif previous is not None:
            components, parent_digest, generation = previous.components, previous.pending, previous.generation
        elif higher_components is not None:
            if pending.execute(
                "SELECT 1 FROM pending_crawls WHERE source = ?", (source,),
            ).fetchone() is not None:
                raise PendingCrawlConflict("Higher-covered completion cannot bypass pending acquisition")
            _Completion.require_versions(claim, higher_components)
            components = ()
            parent_digest = generation = None
        else:
            raise PendingCrawlConflict("Point completion pending parent is missing")
        with domain.operation(store.content_store.db, point_scope=claim.hints.scope):
            # Reconstructing checks the current configured native/model owner mapping.
            current = ComponentLedger(config, store)
            if higher_components is not None and previous is None:
                covered = []
                for component in higher_components:
                    evidence = current.get(
                        component.source, component.path, point_scope=claim.hints.scope,
                    )
                    if evidence is None:
                        return None
                    applied = evidence.content.component
                    if (applied.identity, applied.semantics) != (component.identity, component.semantics):
                        raise SourceLayoutError("Higher-covered component identity or semantics changed")
                    if applied.version <= component.version:
                        return None
                    covered.append(evidence.content)
                components = tuple(covered)
            completion = _Completion(
                _digest(_json(claim.to_dict())), parent_digest, generation,
                scope, _policy(config), domain.receipt()["domain"], components, higher,
            )
            completion.require_targets(claim)
            if task.status == TaskStatus.COMPLETE and (
                previous != completion
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
