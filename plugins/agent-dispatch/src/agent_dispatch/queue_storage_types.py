"""Composed TaskQueue capabilities used by the storage mixin (typing only)."""
from collections.abc import Iterable, Mapping
import sqlite3
from typing import TypedDict

from .assignment_workspace import RelationshipWriter
from .payload import PayloadStore
from .queue_common import Task


class ProducerFence(TypedDict):
    scope: dict[str, str]
    producer_id: str
    generation: int
    request_id: str
    capability: str


class StorageOwner:
    payloads: PayloadStore
    blob_threshold: int
    lease_seconds: int
    result_max_bytes: int
    _relationship_writer: RelationshipWriter | None
    def _connect(self) -> sqlite3.Connection: raise NotImplementedError
    def get(self, task_id: str) -> Task | None: raise NotImplementedError
    def require_worker_admission(self) -> None: raise NotImplementedError
    def _notify_owned_transition(self) -> None: raise NotImplementedError
    def _require_standing_child_parent(self, conn: sqlite3.Connection, parent_id: str, repo: str) -> None: raise NotImplementedError
    @staticmethod
    def _require_agent_backed_repo(canonical_repo: str) -> None: raise NotImplementedError
    @staticmethod
    def _capability_hash(capability: str) -> str: raise NotImplementedError
    @staticmethod
    def _producer_request_hash(fields: Mapping[str, object]) -> str: raise NotImplementedError
    @staticmethod
    def _producer_scope_row(conn: sqlite3.Connection, *, repo: str, source: str | None) -> sqlite3.Row | None: raise NotImplementedError
    @staticmethod
    def _required_label_scope_rows(conn: sqlite3.Connection, *, labels: Iterable[object]) -> list[sqlite3.Row]: raise NotImplementedError
    @classmethod
    def _normalize_producer_fence(
        cls, producer_scope: Mapping[str, object] | None, producer_id: str | None,
        producer_generation: int | None, producer_capability: str | None,
        producer_request_id: str | None, *, repo: str, source: str | None,
    ) -> dict[str, object] | None: raise NotImplementedError
