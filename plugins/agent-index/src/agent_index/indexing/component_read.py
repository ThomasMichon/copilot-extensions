"""Explicit named-component reads; no legacy result or whole-discussion claims."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Literal

from agent_index.chunking.base import Chunk
from agent_index.indexing.component_application import qualify_applied_component
from agent_index.indexing.component_manifest import ComponentIdentity
from agent_index.indexing.path_index import PathIndex
from agent_index.store.component_ledger import ComponentLedger
from agent_index.store.source_layout import PointScope, SourceLayoutError


@dataclass(frozen=True)
class ComponentRead:
    """One applied named target, including an ordered, qualified deletion."""

    point_scope: PointScope
    identity: ComponentIdentity
    source: str
    path: str
    applied_version: int
    semantics: str
    deleted: bool
    chunks: tuple[Chunk, ...]
    coverage: ClassVar[Literal["named-target-only"]] = "named-target-only"


def read_component(
    ledger: ComponentLedger, paths: PathIndex, path: str, *,
    point_scope: PointScope, application_scope: str, max_rows: int,
) -> ComponentRead:
    """Qualify one exact path without enumeration, inference or index preparation.

    The selected domain binds representation/account/query identity. The existing
    application owner independently checks its scope, policy and current native,
    ordering, path-checkpoint and routed-vector evidence. Unknown or untyped
    points refuse rather than yielding a legacy-shaped or incomplete result.
    """
    if point_scope is None:
        raise SourceLayoutError("Component read requires an explicit point scope")
    domain = ledger._source_domain
    with domain.operation(ledger._db, point_scope=point_scope):
        source = point_scope[1]
        evidence = ledger.get(source, path, point_scope=point_scope)
        if evidence is None or evidence.content.component.identity is None:
            raise SourceLayoutError("Component read requires applied canonical identity")
        snapshot = qualify_applied_component(
            ledger, paths, evidence, scope=application_scope, max_rows=max_rows,
            point_scope=point_scope,
        )
        component = evidence.content.component
        assert component.identity is not None
        return ComponentRead(
            point_scope, component.identity, component.source, component.path,
            component.version, component.semantics, evidence.content.deleted, snapshot.chunks,
        )
