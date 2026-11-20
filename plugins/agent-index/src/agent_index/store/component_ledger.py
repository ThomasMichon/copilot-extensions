"""Opt-in native ordering evidence, subordinate to the existing source writer."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent_index.indexing.component_manifest import ComponentContent, ComponentVersion

from .file_projection import RawFileProjection, RawModelProjection, capture_file_projection
from .scalar_index import (
    MAX_SEGMENTS,
    ScalarIndexPreparationRequired,
    ScalarIndexState,
    consolidate_index,
    consolidation_rows,
    inspect_index,
    prepare_index,
    require_budget,
)
from .source_layout import PointScope, SourceLayoutError

if TYPE_CHECKING:
    from agent_index.index_config import IndexConfig

    from .multi_model_store import MultiModelStore
    from .source_layout import SourceDomain

TABLE = "component_ordering_evidence"
BINDING = "component_ordering"
IDENTITY = b"agent-index.component-ordering"


def _schema(identity: str) -> Any:
    pa = import_module("pyarrow")

    return pa.schema(
        [pa.field("point_key", pa.string()), pa.field("record", pa.binary())],
        metadata={IDENTITY: identity.encode("ascii")},
    )


def qualified_component_table(domain: SourceDomain, db: Any) -> Any:
    """Open existing ordering evidence without adopting, repairing or changing it."""
    binding = domain.receipt().get(BINDING)
    if (not isinstance(binding, dict)
            or set(binding) != {"protocol", "table", "identity", "version"}
            or type(binding["protocol"]) is not int or binding["protocol"] != 1
            or binding["table"] != TABLE or type(binding["version"]) is not int
            or binding["version"] < 1 or not isinstance(binding["identity"], str)
            or len(binding["identity"]) != 32
            or any(char not in "0123456789abcdef" for char in binding["identity"])
            or TABLE not in db.list_tables().tables):
        raise SourceLayoutError("Component ledger binding or native table is missing or incompatible")
    table = db.open_table(TABLE)
    if (table.version != binding["version"]
            or not table.schema.equals(_schema(binding["identity"]), check_metadata=True)):
        raise SourceLayoutError("Component ledger identity or version changed")
    state, _ = inspect_index(table, column="point_key")
    if not state.prepared:
        raise ScalarIndexPreparationRequired("Component ledger needs a prepared point index")
    require_budget(state, 0)
    return table


def _key(source: str, path: str) -> str:
    if any(not isinstance(value, str) or not value.strip() for value in (source, path)):
        raise ValueError("Component evidence requires an exact source and path")
    return hashlib.sha256(json.dumps([source, path], ensure_ascii=True).encode("ascii")).hexdigest()


@dataclass(frozen=True)
class ComponentEvidence:
    """Retained ordering plus raw native observations, not an application receipt."""

    content: ComponentContent
    scope: str
    policy: str
    projection: RawFileProjection

    def __post_init__(self) -> None:
        if (not isinstance(self.content, ComponentContent)
                or not isinstance(self.content.component, ComponentVersion)
                or not isinstance(self.content.fingerprint, str) or not self.content.fingerprint
                or type(self.content.deleted) is not bool
                or any(not isinstance(value, str) or not value.strip()
                       for value in (self.scope, self.policy))
                or not isinstance(self.projection, RawFileProjection)):
            raise ValueError("Invalid component evidence")
        component = self.content.component
        if (component.source, component.path) != (self.projection.source, self.projection.file_path):
            raise ValueError("Component evidence point does not match its raw projection")


def _encode(evidence: ComponentEvidence) -> bytes:
    projection = asdict(evidence.projection)
    projection["content_rows"] = evidence.projection.content_rows.decode("utf-8")
    projection["models"] = [
        {**asdict(model), "rows": model.rows.decode("utf-8")} for model in evidence.projection.models
    ]
    return json.dumps(
        {**asdict(evidence), "projection": projection, "content": {
            **asdict(evidence.content), "component": evidence.content.component.to_dict(),
        }},
        sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def _decode(payload: bytes) -> ComponentEvidence:
    try:
        raw = json.loads(payload)
        if set(raw) != {"content", "scope", "policy", "projection"}:
            raise ValueError("Invalid evidence fields")
        content = raw["content"]
        if set(content) != {"component", "fingerprint", "deleted"}:
            raise ValueError("Invalid content fields")
        projection = raw["projection"]
        projection["content_rows"] = projection["content_rows"].encode("utf-8")
        projection["vector_chunk_ids"] = tuple(projection["vector_chunk_ids"])
        models = []
        for model in projection["models"]:
            models.append(RawModelProjection(
                **{**model, "profile_key": tuple(model["profile_key"]),
                   "content_types": tuple(model["content_types"]),
                   "rows": model["rows"].encode("utf-8")},
            ))
        projection["models"] = tuple(models)
        result = ComponentEvidence(
            ComponentContent(
                ComponentVersion.from_dict(content["component"]),
                content["fingerprint"], content["deleted"],
            ),
            raw["scope"], raw["policy"], RawFileProjection(**projection),
        )
        if _encode(result) != payload:
            raise ValueError("Noncanonical component evidence")
        return result
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise SourceLayoutError("Invalid native component evidence") from exc


class ComponentLedger:
    """Bounded point evidence in the same native directory as its data and backup."""

    def __init__(self, config: IndexConfig, store: MultiModelStore):
        domain = store._source_domain
        if (domain is None or Path(store._db_path).resolve() != config.lance_dir.resolve()
                or store.content_store._table_name != config.content_table
                or not config.model_profiles
                or set(store.model_ids) != set(config.model_profiles)):
            raise SourceLayoutError("Component ledger requires the configured participating store")
        names = {config.content_table}
        for name, profile in config.model_profiles.items():
            owner = store.get_vector_table(name)
            if (name != profile.model_id or owner.table_name != profile.table_name
                    or owner.dim != profile.dim or owner._source_domain is not domain
                    or owner.table_name in names):
                raise SourceLayoutError("Component ledger model mapping is incompatible")
            names.add(owner.table_name)
        if TABLE in names:
            raise SourceLayoutError("Component ledger table collides with configured data")
        self._config = config
        self._store = store
        self._source_domain = domain
        self._db = store.content_store.db
        self._table_name = TABLE
        self._data_tables = names

    def _get_or_create_table(self) -> Any:
        # This scalar-index owner only opens; creation belongs to explicit bootstrap.
        return self._db.open_table(TABLE)

    def _bound_table(self) -> Any:
        return qualified_component_table(self._source_domain, self._db)

    def _bind(self, table: Any, identity: str) -> None:
        value = self._source_domain.receipt()
        value[BINDING] = {
            "protocol": 1, "table": TABLE, "identity": identity, "version": table.version,
        }
        self._source_domain.save(value)

    def get(
        self, source: str, path: str, *, point_scope: PointScope | None = None,
    ) -> ComponentEvidence | None:
        """Read at most two indexed candidates; missing history never bootstraps itself."""
        key = _key(source, path)
        with self._source_domain.operation(self._db, point_scope=point_scope):
            if point_scope is not None and source != point_scope[1]:
                raise SourceLayoutError("Component read is outside its selected family")
            table = self._bound_table()
            rows = table.search().where(f"point_key = '{key}'").limit(2).to_list()
            if not rows:
                return None
            if len(rows) != 1 or rows[0]["point_key"] != key:
                raise SourceLayoutError("Duplicate or unexpected component evidence")
            result = _decode(rows[0]["record"])
            projection = result.projection
            if ((projection.source, projection.file_path) != (source, path)
                    or projection.domain != self._source_domain.receipt().get("domain")):
                raise SourceLayoutError("Component evidence belongs to a different point or domain")
            return result

    def put(
        self, evidence: ComponentEvidence, *, expected: ComponentEvidence | None, max_rows: int,
        point_scope: PointScope | None = None,
    ) -> bool:
        """Retain a witnessed point under an already-owned writer; never execute application."""
        if not self._source_domain.writing:
            raise SourceLayoutError("Component evidence requires the owning source writer")
        component = evidence.content.component
        previous = self.get(component.source, component.path, point_scope=point_scope)
        if previous != expected:
            raise SourceLayoutError("Component evidence prior record changed")
        if previous is not None and (previous.scope, previous.policy) != (evidence.scope, evidence.policy):
            raise SourceLayoutError("Component evidence scope or policy changed")
        covered = previous is not None and evidence.content.covered_by(previous.content)
        selected = previous if covered else evidence
        assert selected is not None
        retained = tuple(sorted(
            set(selected.projection.vector_chunk_ids)
            | (set(previous.projection.vector_chunk_ids) if previous is not None else set()),
        ))
        actual = capture_file_projection(
            self._config, self._store, component.source, component.path,
            max_rows=max_rows, retained_chunk_ids=retained, point_scope=point_scope,
        )
        if actual != selected.projection:
            raise SourceLayoutError("Component evidence does not match the current native point")
        if covered:
            return False
        if evidence.content.deleted and (
            actual.content_rows != b"[]" or any(model.rows != b"[]" for model in actual.models)
        ):
            raise SourceLayoutError("Component deletion still has native content or vectors")
        table = self._bound_table()
        state, _ = inspect_index(table, column="point_key")
        if state.segments >= MAX_SEGMENTS:
            raise ScalarIndexPreparationRequired("Component ledger needs explicit index consolidation")
        payload = _encode(evidence)
        identity = table.schema.metadata[IDENTITY].decode("ascii")
        self._source_domain.effect("component-evidence:" + TABLE)
        table.merge_insert("point_key").when_matched_update_all().when_not_matched_insert_all().execute([
            {"point_key": _key(component.source, component.path), "record": payload},
        ])
        prepare_index(self, column="point_key", max_rows=1, point_scope=point_scope)
        self._bind(self._get_or_create_table(), identity)
        return True

    def consolidate(self, *, max_rows: int) -> ScalarIndexState:
        """Explicitly consolidate the ledger index and rebind its witnessed version."""
        with self._source_domain.operation(self._db):
            table = self._bound_table()
            state, dataset = inspect_index(table, column="point_key")
            consolidation_rows(state, dataset, max_rows=max_rows)
            if state.segments <= 1 and state.physical_unindexed_rows == 0:
                return state
            identity = table.schema.metadata[IDENTITY].decode("ascii")
            with self._source_domain.write(self._db):
                result = consolidate_index(self, column="point_key", max_rows=max_rows)
                self._bind(self._get_or_create_table(), identity)
                return result


def initialize_component_ledger(
    config: IndexConfig, store: MultiModelStore, *, point_scope: PointScope | None = None,
) -> ComponentLedger:
    """Explicit cold bootstrap only; never recreate a missing ledger or adopt existing rows."""
    ledger = ComponentLedger(config, store)
    domain = ledger._source_domain
    with domain.operation(ledger._db, point_scope=point_scope):
        has_binding = BINDING in domain.receipt()
        exists = TABLE in ledger._db.list_tables().tables
        if has_binding or exists:
            ledger._bound_table()
            return ledger
        if any(ledger._db.open_table(name).count_rows() for name in ledger._data_tables):
            raise SourceLayoutError("Component ledger bootstrap requires empty configured data tables")
        with domain._write(ledger._db, point_scope=point_scope):
            identity = uuid.uuid4().hex
            domain.effect("component-evidence-bootstrap:" + TABLE)
            ledger._db.create_table(TABLE, schema=_schema(identity))
            prepare_index(ledger, column="point_key", max_rows=0, point_scope=point_scope)
            ledger._bind(ledger._get_or_create_table(), identity)
    return ledger
