"""End-to-end proof that both documented container-testing workarounds for a
repo with no ``indexer:`` designation of its own (see
``_indexer_assignment_fixtures.py`` and ``plugins/agent-index/README.md``
"Testing without an assigned indexer machine") produce a genuinely working
local host -- not just a routing decision. Each test resolves
``transport.plan_route()`` to ``host`` via one workaround, then drives a real
index -> embed -> store -> search round trip (using ``FakeEmbeddingEngineClient``
in place of the heavy real model) to prove search actually works once that
resolution succeeds.
"""

from __future__ import annotations

from pathlib import Path

from agent_index import transport
from agent_index.chunking.base import Chunk
from agent_index.index_config import ModelProfile
from agent_index.indexing.engine import _embed_and_store_batch
from agent_index.search.engine import SearchEngine
from agent_index.store.multi_model_store import MultiModelStore

from _fake_engine import FakeEmbeddingEngineClient

_DIM = 64


def _index_and_search(tmp_path: Path) -> None:
    """Shared round trip: seed one known chunk through the real production
    embed/store code path, then confirm the real search path finds it."""
    profile = ModelProfile(model_id="code", model_name="fake", dim=_DIM, table_name="vectors_code")
    store = MultiModelStore(tmp_path / "lance")
    store.register_model(profile)
    client = FakeEmbeddingEngineClient(dim=_DIM)

    chunk = Chunk(
        content="Procedure: how to open the share dialog in Teams Channel Files",
        file_path="docs/example.md",
        chunk_type="heading",
        language="markdown",
        line_start=1,
        line_end=3,
        source="git:example",
    )
    stored = _embed_and_store_batch([chunk], store, {"code": client}, {"code": profile})
    assert stored == 1

    engine = SearchEngine({"code": client}, store)
    hits = engine.search("share dialog Teams Channel Files", limit=5)
    assert any(hit.chunk_id == chunk.chunk_id for hit in hits), (
        f"expected chunk {chunk.chunk_id} in hits, got {[h.chunk_id for h in hits]}"
    )


def test_paired_knowledge_repo_indexer_resolves_host_and_can_search(
    tmp_path: Path, paired_knowledge_repo_indexer: Path
) -> None:
    role, indexer = transport.plan_route()
    assert role == "host"
    assert indexer is not None and indexer["machine"] == "test-machine"
    _index_and_search(tmp_path)


def test_machine_local_overlay_resolves_host_and_can_search(
    tmp_path: Path, machine_local_indexer_overlay: Path
) -> None:
    role, indexer = transport.plan_route()
    assert role == "host"
    assert indexer is not None and indexer["machine"] == "test-machine"
    _index_and_search(tmp_path)
