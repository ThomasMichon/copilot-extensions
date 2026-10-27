"""Round-local work measurements, not checkpoint or coverage authorities."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from functools import wraps
from typing import Callable, ParamSpec

from agent_index.store.mutation_result import MutationResult

P = ParamSpec("P")


@dataclass
class HttpCounts:
    """Shared-session requests; bytes are decoded response payload bytes."""

    attempts: int = 0
    responses: int = 0
    retries: int = 0
    response_bytes: int = 0
    not_modified: int = 0
    json_results: int = 0


@dataclass
class ModelCounts:
    candidate_chunks: int = 0
    reused_chunks: int = 0
    embed_calls: int = 0
    chunks_submitted: int = 0
    chunks_embedded: int = 0
    vector_rows_upserted: int = 0


@dataclass
class WorkCounts:
    """Completed work unless a field explicitly names attempts or submissions.

    Row upserts count primary API row mutations, not storage-engine I/O or
    provenance/metadata side effects. HTTP covers GoodCitizenSession only; it
    stays unavailable until that transport is observed in this round.
    """

    version: int = 1
    manifests_acquired: int = 0
    identities_scanned: int = 0
    upserts_acquired: int = 0
    tombstones_acquired: int = 0
    manifests_applied: int = 0
    files_chunked: int = 0
    files_reused: int = 0
    chunks_generated: int = 0
    checkpoint_lookup_keys: int = 0
    checkpoint_rows_found: int = 0
    checkpoint_rows_written: int = 0
    content_rows_upserted: int = 0
    file_chunk_ids_deleted: int = 0
    stale_chunk_ids_deleted: int = 0
    fts_ensure_attempts: int = 0
    fts_ensure_completed: int = 0
    cluster_pass_attempts: int = 0
    cluster_pass_completed: int = 0
    cluster_pass_failures: int = 0
    cluster_cache_hits: int = 0
    http: HttpCounts | None = None
    models: dict[str, ModelCounts] = field(default_factory=dict)
    source_mutations: dict[str, list[MutationResult]] = field(default_factory=dict)

    def model(self, model_id: str) -> ModelCounts:
        return self.models.setdefault(model_id, ModelCounts())


_current: ContextVar[WorkCounts | None] = ContextVar("index_work_counts", default=None)


def current_counts() -> WorkCounts | None:
    return _current.get()


def observe_http() -> HttpCounts | None:
    counts = current_counts()
    if counts is None:
        return None
    if counts.http is None:
        counts.http = HttpCounts()
    return counts.http


def measure_reindex(fn: Callable[P, dict[str, object]]) -> Callable[P, dict[str, object]]:
    """Attach a JSON-safe snapshot; reset even after cancellation or failure."""
    @wraps(fn)
    def measured(*args: P.args, **kwargs: P.kwargs) -> dict[str, object]:
        counts = WorkCounts()
        token = _current.set(counts)
        try:
            result = fn(*args, **kwargs)
            result["work_counts"] = asdict(counts)
            return result
        finally:
            _current.reset(token)

    return measured
