"""Caller-owned embedding policy for the shared indexing pipeline."""

from __future__ import annotations

import os
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_index.engine.client import EngineClient
    from agent_index.index_config import ModelProfile
    from agent_index.store.multi_model_store import MultiModelStore


@dataclass(frozen=True)
class EmbeddingRuntime:
    """Inject clients and lifecycle policy, not another indexing engine.

    Each factory result is owned by this run and closed on every exit. Return a
    fresh EngineClient (or compatible subclass), not a borrowed service client.
    Only engines reported as started by ``ensure_engine`` are stopped.
    """

    create_client: Callable[[ModelProfile], EngineClient]
    ensure_engine: Callable[[ModelProfile, EngineClient], bool]
    stop_engine: Callable[[ModelProfile], None]
    manage_engines: bool = True


def default_embedding_runtime() -> EmbeddingRuntime:
    """Resolve defaults at call time, retaining ordinary runtime/test selection."""
    from agent_index.engine.client import EngineClient
    from agent_index.engine.lifecycle import ensure_engine, stop_engine

    def create_client(profile: ModelProfile) -> EngineClient:
        return EngineClient(
            base_url=profile.engine_url, query_prefix=profile.query_prefix,
            model_id=profile.model_id,
        )

    return EmbeddingRuntime(
        create_client, ensure_engine, stop_engine,
        manage_engines=os.environ.get("AGENT_INDEX_INDEX_ENSURE_ENGINES", "1") != "0",
    )


def open_embedding_clients(
    store: MultiModelStore, profiles: dict[str, ModelProfile], runtime: EmbeddingRuntime,
) -> tuple[dict[str, EngineClient], ExitStack]:
    """Close already-created clients if a later model registration/factory fails."""
    clients: dict[str, EngineClient] = {}
    with ExitStack() as cleanup:
        for profile in profiles.values():
            store.register_model(profile)
            client = runtime.create_client(profile)
            cleanup.callback(client.close)
            clients[profile.model_id] = client
        return clients, cleanup.pop_all()
