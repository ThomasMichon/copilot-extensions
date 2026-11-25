"""Public FTS admission must not dirty a real source domain before any effect."""

from __future__ import annotations

from pathlib import Path
import subprocess
from types import SimpleNamespace
from typing import NoReturn

import numpy as np
import pytest
from agent_procutil.owned_process import OwnedCleanupError

from agent_index.chunking.base import Chunk
from agent_index.index_config import IndexConfig, ModelProfile
from agent_index.store import content_fts_worker as worker
from agent_index.store.multi_model_store import MultiModelStore
from agent_index.store.source_layout import (
    LAYOUT, MARKER, SourceLayoutError, initialize_source_store,
)


def forbidden(*args: object, **kwargs: object) -> NoReturn:
    pytest.fail("No-effect admission refusal must not hand off, build, or retry cleanup")


@pytest.fixture
def source_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MultiModelStore:
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    config = IndexConfig(
        data_dir=tmp_path / "source", model_profiles={
            "small": ModelProfile("small", "fixture", dim=2),
        },
    )
    store = initialize_source_store(config)
    store.upsert(
        "small", [Chunk("needle original", "one.md", "text", "markdown", 1, 1, "test:repo")],
        np.array([[1, 0]], dtype=np.float32),
    )
    return store


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("failure", ["timeout", "missing", "cleanup"])
def test_public_admission_refusal_preserves_clean_receipt_and_recovery(
    source_store: MultiModelStore, monkeypatch: pytest.MonkeyPatch,
    existing: bool, failure: str,
) -> None:
    store = source_store
    content = store.content_store
    if existing:
        assert store.ensure_fts_index()
        store.upsert(
            "small", [Chunk("needle new", "two.md", "text", "markdown", 1, 1, "test:repo")],
            np.array([[0, 1]], dtype=np.float32),
        )
    domain = content._source_domain
    assert domain is not None
    marker = domain.path / MARKER
    before = marker.read_bytes()
    original = {
        "timeout": subprocess.TimeoutExpired(["uv"], 10),
        "missing": FileNotFoundError("missing UV"),
        "cleanup": OwnedCleanupError(
            SimpleNamespace(pid=123, returncode=None), object(), cleanup_retry=forbidden,
        ),
    }[failure]
    lookups = []

    def refuse(*args: object, **kwargs: object) -> NoReturn:
        lookups.append("lookup")
        raise original

    with monkeypatch.context() as patch:
        patch.setattr(worker, "resolve_runtime_python", refuse)
        patch.setattr(domain, "fts_handoff", forbidden)
        patch.setattr(subprocess, "run", forbidden)
        expected = OwnedCleanupError if failure == "cleanup" else worker.FtsRuntimeAdmissionError
        with pytest.raises(expected) as caught:
            content.ensure_fts_index(max_retries=5)
        if failure == "cleanup":
            assert caught.value is original
        else:
            assert caught.value.__cause__ is original
        assert lookups == ["lookup"] and marker.read_bytes() == before
        assert store.stats()
        if failure == "cleanup":
            with pytest.raises(OwnedCleanupError) as retained:
                content.ensure_fts_index(max_retries=5)
            assert retained.value is original and lookups == ["lookup"]
            assert marker.read_bytes() == before
    if failure != "cleanup":
        assert content.ensure_fts_index()
        assert domain.receipt()["state"] == "clean"
        assert content.fts_search("needle")


def test_admission_refusal_does_not_clear_an_earlier_outer_effect(
    source_store: MultiModelStore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = source_store.content_store
    domain = content._source_domain
    assert domain is not None

    def refuse(*args: object, **kwargs: object) -> NoReturn:
        raise subprocess.TimeoutExpired(["uv"], 10)

    monkeypatch.setattr(worker, "resolve_runtime_python", refuse)
    monkeypatch.setattr(domain, "fts_handoff", forbidden)
    with pytest.raises(worker.FtsRuntimeAdmissionError):
        with domain.write(content.db):
            domain.effect("synthetic earlier effect")
            content.ensure_fts_index()
    receipt = domain.receipt()
    assert receipt["state"] == "dirty" and receipt["uncertain_effect"] is True
    assert "fts_pending" not in receipt
    with pytest.raises(SourceLayoutError):
        source_store.stats()
