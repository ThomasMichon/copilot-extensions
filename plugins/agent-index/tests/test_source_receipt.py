"""Windows receipt handles: deterministic contention, not probabilistic reruns."""

from __future__ import annotations

import os
import stat
import threading
from contextlib import contextmanager

import numpy as np
import pytest
from test_source_aware_store import chunk, cold as cold

from agent_index.store import source_layout as layout

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows file-sharing contract")


@pytest.mark.timeout(60)
@pytest.mark.parametrize("phase", ["writer-entry", "fts-child"])
def test_receipt_reader_allows_actual_fts_publication(cold, monkeypatch, phase):
    _config, store = cold
    store.upsert("small", [chunk()], np.array([[1, 0]], dtype=np.float32))
    domain = store._source_domain
    original_open = layout._open_receipt
    ready, release = threading.Event(), threading.Event()
    snapshots, failures = [], []

    def held_open(path):
        stream = original_open(path)
        if threading.current_thread() is reader:
            ready.set()
            if not release.wait(40):
                stream.close()
                raise TimeoutError("fixture receipt reader not released")
        return stream

    def read():
        try:
            snapshots.append(domain.receipt())
        except Exception as exc:  # Re-raised by the owning test, not hidden in a thread.
            failures.append(exc)

    reader = threading.Thread(target=read, name="owned-receipt-reader")
    monkeypatch.setattr(layout, "_open_receipt", held_open)

    @contextmanager
    def holding():
        reader.start()
        try:
            assert ready.wait(10)
            yield
        finally:
            release.set()
            reader.join(10)
            assert not reader.is_alive()
            if failures:
                raise failures[0]

    if phase == "writer-entry":
        with holding():
            assert store.ensure_fts_index()
    else:
        original_handoff = domain.fts_handoff

        @contextmanager
        def handoff(*, table_name=None):
            with original_handoff(table_name=table_name) as nonce, holding():
                yield nonce

        monkeypatch.setattr(domain, "fts_handoff", handoff)
        assert store.ensure_fts_index()
    assert snapshots
    assert snapshots[0]["state"] == ("clean" if phase == "writer-entry" else "dirty")
    assert bool(snapshots[0].get("fts_pending")) == (phase == "fts-child")
    current = domain.receipt()
    assert current["state"] == "clean" and not current.get("fts_pending")
    assert current["fts_coverage"]["chunks"] == current["versions"]["chunks"]


@pytest.mark.timeout(60)
@pytest.mark.parametrize("denial", ["incompatible-reader", "permanent-readonly"])
@pytest.mark.parametrize("escalate", [False, True])
def test_receipt_denial_preserves_primary_and_uncertainty(cold, monkeypatch, denial, escalate):
    _config, store = cold
    item = chunk()
    store.upsert("small", [item], np.array([[1, 0]], dtype=np.float32))
    assert store.ensure_fts_index()  # Exercise the old-index escalation branch, too.
    store.content_store.mark_fts_dirty()
    domain = store._source_domain
    marker = domain.path / layout.MARKER
    original = store.content_store._run_fts_build
    replace = layout._replace_receipt
    denied, calls = [], []

    def observed(source, target):
        try:
            return replace(source, target)
        except PermissionError as exc:
            denied.append(exc)
            raise

    def blocked(*args, **kwargs):
        calls.append(kwargs["full"])
        if escalate and len(calls) == 1:
            raise RuntimeError("synthetic incremental build requires replacement")
        # Durable uncertainty precedes the blocked handoff publication; no child is launched.
        domain.effect("fts-builder")
        mode = marker.stat().st_mode
        stream = None
        try:
            if denial == "incompatible-reader":
                stream = marker.open("rb")  # Intentional deny-delete fixture.
            else:
                marker.chmod(stat.S_IREAD)
            return original(*args, **kwargs)
        finally:
            if stream is not None:
                stream.close()
            if denial == "permanent-readonly":
                marker.chmod(mode)

    monkeypatch.setattr(layout, "_replace_receipt", observed)
    monkeypatch.setattr(store.content_store, "_run_fts_build", blocked)
    with pytest.raises(PermissionError) as failure:
        store.content_store.ensure_fts_index(max_retries=5)
    assert len(denied) == 1
    assert calls == ([False, True] if escalate else [False])
    assert failure.value is denied[0]
    assert failure.value.winerror == (32 if denial == "incompatible-reader" else 5)
    assert str(marker) == failure.value.filename2
    current = domain.receipt()
    assert current["state"] == "dirty" and current["uncertain_effect"] is True
    with pytest.raises(layout.SourceLayoutError):
        store.stats()
    with pytest.raises(layout.SourceLayoutError, match="uncertain effect"):
        layout.validate_source_store(store)
    assert not list(domain.path.glob(".source-layout-*.tmp"))


def test_legacy_replace_reproduces_access_denied_with_real_receipt_reader(cold):
    _config, store = cold
    domain = store._source_domain
    marker = domain.path / layout.MARKER
    temporary = domain.path / "controlled-receipt.tmp"
    before = domain.receipt()
    temporary.write_bytes(marker.read_bytes())
    try:
        with layout._open_receipt(marker):
            with pytest.raises(PermissionError) as failure:
                os.replace(temporary, marker)
            assert failure.value.winerror == 5
        # Same identity, same permissions, only the open reader has changed.
        layout._replace_receipt(temporary, marker)
        assert domain.receipt() == before
    finally:
        temporary.unlink(missing_ok=True)
