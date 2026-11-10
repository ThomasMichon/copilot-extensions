"""Writer admission must cover caller state and distinguish contention from errors."""

from __future__ import annotations

import errno
import importlib
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from agent_index.indexing import source_ownership as ownership
from agent_index.indexing.source_ownership import SourcePipelineBusy, source_writer


def test_reentrant_owner_excludes_other_threads_not_other_directories(tmp_path):
    root = tmp_path / "shared"

    def attempt(path):
        with source_writer(path):
            return "owned"

    with ThreadPoolExecutor(max_workers=1) as pool:
        with source_writer(root):
            with source_writer(root / "."):
                with pytest.raises(SourcePipelineBusy):
                    pool.submit(attempt, root).result(timeout=5)
                assert pool.submit(attempt, tmp_path / "other").result(timeout=5) == "owned"
        assert pool.submit(attempt, root).result(timeout=5) == "owned"


def test_cancelled_wait_releases_thread_admission(tmp_path, monkeypatch):
    class Cancelled(Exception):
        pass

    probes = []

    def check():
        if probes:
            raise Cancelled()

    with monkeypatch.context() as patch:
        patch.setattr(ownership, "_try_lock", lambda fd: probes.append(fd) or False)
        with pytest.raises(Cancelled):
            with source_writer(tmp_path, wait=True, cancel_check=check):
                pytest.fail("cancelled writer entered")
    assert len(probes) == 1
    with source_writer(tmp_path):
        pass


@pytest.mark.parametrize("operation", ["open", "lock"])
def test_wait_does_not_retry_permanent_io_errors(tmp_path, monkeypatch, operation):
    calls = []

    def fail(*args):
        calls.append(args)
        raise OSError(errno.EIO, "fixture permanent error")

    monkeypatch.setattr(ownership.os if operation == "open" else ownership,
                        "open" if operation == "open" else "_try_lock", fail)
    with pytest.raises(OSError, match="fixture permanent error"):
        with source_writer(tmp_path, wait=True):
            pytest.fail("failed writer entered")
    assert len(calls) == 1


@pytest.mark.parametrize("error_number", [errno.EAGAIN, errno.EIO])
def test_kernel_probe_distinguishes_contention_from_io_failure(tmp_path, monkeypatch, error_number):
    module = importlib.import_module("msvcrt" if ownership.os.name == "nt" else "fcntl")

    def fail(*args):
        raise OSError(error_number, "fixture kernel error")

    monkeypatch.setattr(module, "locking" if ownership.os.name == "nt" else "flock", fail)
    with (tmp_path / "lock").open("w+b") as handle:
        if error_number == errno.EAGAIN:
            assert ownership._try_lock(handle.fileno()) is False
        else:
            with pytest.raises(OSError, match="fixture kernel error"):
                ownership._try_lock(handle.fileno())


@pytest.mark.parametrize("compact", [False, True])
def test_gc_owns_state_load_save_and_requested_maintenance(tmp_path, monkeypatch, compact):
    from agent_index.indexing import gc
    from agent_index.indexing.state import IndexState, SourceState

    config = SimpleNamespace(data_dir=tmp_path, state_file=tmp_path / "state.json")
    IndexState(sources={"monorepo": SourceState(chunk_count=1)}).save(config.state_file)
    stages = []
    compaction = {"content": {"fragments_before": 2, "fragments_after": 1}}
    with ThreadPoolExecutor(max_workers=1) as pool:
        def verify(stage):
            def contend():
                with source_writer(tmp_path):
                    pytest.fail("GC ownership was released early")
            with pytest.raises(SourcePipelineBusy):
                pool.submit(contend).result(timeout=5)
            stages.append(stage)

        load, save = IndexState.load, IndexState.save

        def loaded(path):
            verify("load")
            return load(path)

        def saved(state, path):
            verify("save")
            save(state, path)

        monkeypatch.setattr(IndexState, "load", loaded)
        monkeypatch.setattr(IndexState, "save", saved)
        store = SimpleNamespace(
            source_counts=lambda: verify("count") or {"monorepo": 1},
            delete_by_source_exact=lambda source: verify("delete") or 1,
            ensure_fts_index=lambda: verify("fts"),
            compact=lambda: verify("compact") or compaction,
        )
        monkeypatch.delenv("AGENT_INDEX_STORE_LAYOUT", raising=False)
        result = gc.run_source_gc(config, store, compact=compact)
    assert stages == ["load", "count", "delete", "save", *(["compact"] if compact else []), "fts"]
    assert result["chunks_deleted"] == 1 and load(config.state_file).sources == {}
    if compact:
        assert result["compaction"] == compaction
    else:
        assert "compaction" not in result


@pytest.mark.parametrize("dry_run", [True, False], ids=["dry-run", "no-rows-deleted"])
def test_gc_without_mutation_does_not_publish_state_or_maintain_tables(
    tmp_path, monkeypatch, dry_run,
):
    from agent_index.indexing.gc import run_source_gc
    from agent_index.indexing.state import IndexState

    state_file = tmp_path / "state.json"
    monkeypatch.setattr(IndexState, "save", lambda *a: pytest.fail("dry-run state save"))
    store = SimpleNamespace(
        source_counts=lambda: {"monorepo": 2}, delete_by_source_exact=lambda source: 0,
    )
    result = run_source_gc(SimpleNamespace(data_dir=tmp_path, state_file=state_file), store,
                           dry_run=dry_run, compact=True)
    assert result["chunks_deleted"] == (2 if dry_run else 0)
    assert result["dry_run"] is dry_run
    assert "compaction" not in result
    assert not state_file.exists()
    if dry_run:
        assert not (tmp_path / "path_index.db").exists()
