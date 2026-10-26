"""Native finite-budget backfill, explicit repair and retained owner guards."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from test_embedding_pair_reuse import Client, chunk, profile

from agent_index.index_config import IndexConfig
from agent_index.indexing.embedding_backfill import BackfillRepairRequired, backfill_embedding_slice
from agent_index.indexing.engine import SourceSpec, source_scope_hash
from agent_index.indexing.manifest import CrawlManifest
from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.pending_crawl import PendingCrawls, PendingCrawlConflict, recovery_scope, recover_source
from agent_index.indexing.state import IndexState, SourceState
from agent_index.store.chunk_id_index import prepare_chunk_id_index
from agent_index.store.source_layout import LAYOUT, SourceLayoutError, initialize_source_store


@pytest.fixture
def context(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    config = IndexConfig(data_dir=tmp_path, model_profiles={"a": profile()})
    store = initialize_source_store(config)
    paths = PathIndex(tmp_path / "path_index.db")
    spec = SourceSpec(name="alias", type="git", repo="example/project")
    connector = SimpleNamespace(source_name="git:project", recovery_identity=("principal", "target"))
    items = [chunk(f"row {i}", path=f"{i}.md", source=connector.source_name) for i in range(5)]
    store.upsert_content(items)
    prepare_chunk_id_index(store.content_store, max_rows=5)
    scope = recovery_scope(source_scope_hash(spec), connector)
    state = IndexState(
        sources={spec.name: SourceState(last_commit="accepted", chunk_count=5, acquisition_scope=scope)},
        total_chunks=5, stored_families={spec.name: connector.source_name},
    )
    state.save(config.state_file)
    client = Client()
    ready = []

    def after(table, _selected):
        # An explicit physical-tail budget, not inferred from the selected count.
        prepare_chunk_id_index(table, max_rows=20)

    result = SimpleNamespace(
        config=config, store=store, paths=paths, spec=spec, connector=connector,
        items=items, client=client, ready=ready, after=after,
    )

    def run(**kwargs):
        return backfill_embedding_slice(
            result.spec, result.connector, config=config, store=store, paths=result.paths,
            clients={"a": client}, row_budget=kwargs.pop("row_budget", 2),
            after_store=kwargs.pop("after_store", result.after),
            before_embed=kwargs.pop("before_embed", lambda: ready.append(True)), **kwargs,
        )

    result.run = run
    return result


def saved(context):
    conn = context.paths._connect()
    try:
        return dict(conn.execute(
            "SELECT * FROM embedding_backfills WHERE source = ?", (context.spec.name,),
        ).fetchone())
    finally:
        conn.close()


def test_finite_restart_resume_completion_and_no_canonical_or_checkpoint_writes(context) -> None:
    original = context.store.content_store.capture_snapshot()
    state_bytes = context.config.state_file.read_bytes()
    first = context.run()
    assert (first.offset, first.rows_read, first.scoped_chunks, first.vector_rows_upserted) == (2, 2, 2, 2)
    assert not first.complete
    with pytest.raises(PendingCrawlConflict, match="backfill"):
        PendingCrawls(context.paths).assert_gc_available()
    # Reopen progress/coverage and retain the accepted source cursor and totals.
    context.paths = PathIndex(context.config.data_dir / "path_index.db")
    second, third = context.run(), context.run()
    assert second.offset == 4
    assert third.offset == 5 and third.complete and third.rows_read == 1
    before_calls = len(context.client.calls)
    complete = context.run()
    assert complete.complete and complete.rows_read == complete.vector_rows_upserted == 0
    assert len(context.client.calls) == before_calls == 3
    assert context.store.content_store.capture_snapshot() == original
    assert context.config.state_file.read_bytes() == state_bytes
    PendingCrawls(context.paths).assert_gc_available()
    replay = context.run(restart=True)
    assert replay.offset == 2 and replay.vector_rows_upserted == 0
    assert len(context.client.calls) == before_calls


def test_canonical_change_keeps_indexing_active_and_requires_explicit_restart(context) -> None:
    context.run()
    previous = saved(context)
    call_count = len(context.client.calls)
    context.store.upsert_content([chunk("new item", source=context.connector.source_name)])
    prepare_chunk_id_index(context.store.content_store, max_rows=20)
    with pytest.raises(BackfillRepairRequired, match="changed"):
        context.run()
    assert saved(context) == previous
    assert len(context.client.calls) == call_count
    assert context.store._source_domain.receipt()["state"] == "clean"
    restarted = context.run(restart=True)
    assert restarted.offset == 2 and restarted.total_rows == 6


def test_ordinary_acquisition_is_not_fenced_by_pending_backfill(context) -> None:
    context.run()
    journal = PendingCrawls(context.paths)
    assert journal.sources() == []
    state = IndexState.load(context.config.state_file)

    def apply(*, manifest, full):
        state.mark_source_indexed(context.spec.name, "ordinary-poll", 5)
        return 0, 0, 0

    recover_source(
        context.spec.name, scope=state.sources[context.spec.name].acquisition_scope,
        state=state, state_file=context.config.state_file, paths=context.paths,
        full=False, family=context.connector.source_name,
        acquire=lambda: CrawlManifest(source=context.spec.name, commit="ordinary-poll"),
        apply=apply,
    )
    assert IndexState.load(context.config.state_file).sources[context.spec.name].last_commit == "ordinary-poll"
    assert context.run().offset == 4


@pytest.mark.parametrize("change", ["account", "config", "profile", "missing-scope", "family"])
def test_binding_change_refuses_before_engine_or_progress(context, change) -> None:
    context.run()
    before = saved(context)
    calls = len(context.client.calls)
    if change == "account":
        context.connector.recovery_identity = ("other-principal", "target")
    elif change == "config":
        context.spec = replace(context.spec, repo="different/project")
    elif change == "profile":
        context.config.model_profiles["a"] = replace(profile(), revision="new-weights")
    else:
        state = IndexState.load(context.config.state_file)
        if change == "missing-scope":
            state.sources[context.spec.name].acquisition_scope = None
        else:
            state.stored_families[context.spec.name] = "other:family"
        state.save(context.config.state_file)
    with pytest.raises(BackfillRepairRequired):
        context.run()
    assert saved(context) == before
    assert len(context.client.calls) == calls
    assert context.store._source_domain.receipt()["state"] == "clean"


def test_completed_vector_coverage_change_requires_restart_not_cached_success(context) -> None:
    assert context.run(row_budget=10).complete
    before = saved(context)
    import numpy as np
    context.store.upsert_vectors("a", [context.items[0]], np.ones((1, 2), dtype=np.float32))
    prepare_chunk_id_index(context.store.get_vector_table("a"), max_rows=20)
    with pytest.raises(BackfillRepairRequired, match="vector coverage"):
        context.run()
    assert saved(context) == before


def test_progress_failure_retries_covered_pairs_without_reembedding(context, monkeypatch) -> None:
    from agent_index.indexing import embedding_backfill as module

    require = module._require_current
    calls = []

    def fail_after_store(*args):
        calls.append(True)
        if not context.store._source_domain.writing and context.client.calls:
            raise RuntimeError("progress publication interrupted")
        return require(*args)

    with monkeypatch.context() as patch:
        patch.setattr(module, "_require_current", fail_after_store)
        with pytest.raises(RuntimeError, match="publication"):
            context.run()
    before = saved(context)
    assert context.store._source_domain.receipt()["state"] == "clean"
    # Store-first intent retained the proven vectors but never acknowledged the
    # page offset, so retry verifies/reuses this page without restarting discovery.
    replay = context.run()
    assert replay.offset == 2 and replay.vector_rows_upserted == 0
    assert saved(context) != before
    assert len(context.client.calls) == 1


def test_post_effect_failure_preserves_existing_outer_writer_uncertainty(context) -> None:
    def fail(table, selected):
        context.after(table, selected)
        raise RuntimeError("coverage interrupted")

    with pytest.raises(RuntimeError, match="coverage interrupted"):
        context.run(after_store=fail)
    assert saved(context)["complete"] == 0
    assert context.store._source_domain.receipt()["state"] == "dirty"
    with pytest.raises(SourceLayoutError, match="incomplete"):
        context.run()


def test_pending_acquisition_blocks_slice_but_backfill_does_not_replace_manifest(context) -> None:
    manifest = CrawlManifest(
        source=context.spec.name, commit="target", stored_family=context.connector.source_name,
        accounting_family=context.connector.source_name,
    )
    scope = recovery_scope(source_scope_hash(context.spec), context.connector)
    journal = PendingCrawls(context.paths)
    journal.save(manifest, scope=scope, full=False)
    with pytest.raises(PendingCrawlConflict, match="acquired"):
        context.run()
    assert context.client.calls == []
    assert journal.load(context.spec.name, scope) == (manifest, False)


@pytest.mark.parametrize("budget", [0, -1, True, 2.5])
def test_invalid_budget_precedes_any_intent(context, budget) -> None:
    with pytest.raises(ValueError, match="budget"):
        context.run(row_budget=budget)
    assert context.client.calls == []


def test_unrelated_family_rows_consume_only_explicit_budget_without_vectors(context) -> None:
    unrelated = [chunk(f"unrelated {i}", source="git:project-extra") for i in range(3)]
    context.store.upsert_content(unrelated)
    prepare_chunk_id_index(context.store.content_store, max_rows=20)
    before = context.store.content_store.capture_snapshot()
    result = context.run(row_budget=10)
    assert result.complete and result.rows_read == 8 and result.scoped_chunks == 5
    assert result.vector_rows_upserted == 5
    vectors = context.store.get_vector_table("a")
    assert vectors.get_vectors([item.chunk_id for item in unrelated], bounded=True) == {}
    assert context.store.content_store.capture_snapshot() == before


@pytest.mark.parametrize("size", [100, 10_000, 100_000])
def test_native_offset_and_embedding_work_is_bounded_at_scale(context, monkeypatch, size) -> None:
    from lancedb.table import LanceTable

    items = [chunk(f"scale {i}", path=f"scale-{i}.md", source=context.connector.source_name)
             for i in range(size - 5)]
    context.store.upsert_content(items)
    prepare_chunk_id_index(context.store.content_store, max_rows=size)
    snapshot = context.store.content_store.capture_snapshot()
    assert snapshot.rows == size
    offsets = []
    take = LanceTable.take_offsets

    def measured(table, selected):
        offsets.append(list(selected))
        return take(table, selected)

    monkeypatch.setattr(LanceTable, "take_offsets", measured)
    first = context.run(row_budget=3)
    second = context.run(row_budget=3)
    assert offsets == [[0, 1, 2], [3, 4, 5]]
    assert first.rows_read == second.rows_read == 3
    assert first.vector_rows_upserted == second.vector_rows_upserted == 3
    assert len(context.client.calls) == 2
    assert all(len(call) == 3 for call in context.client.calls)
    assert context.store.content_store.capture_snapshot() == snapshot


@pytest.mark.parametrize("corruption", [
    '{"version":2}', '{"version":true}', '{}', 'not-json',
])
def test_corrupt_progress_never_grants_resume(context, corruption) -> None:
    context.run()
    before_calls = len(context.client.calls)
    conn = context.paths._connect()
    try:
        conn.execute("UPDATE embedding_backfills SET payload = ?", (corruption,))
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(BackfillRepairRequired, match="Malformed"):
        context.run()
    assert len(context.client.calls) == before_calls


def test_new_model_backfills_only_its_missing_pairs_without_source_rewrites(context) -> None:
    assert context.run(row_budget=10).complete
    snapshot = context.store.content_store.capture_snapshot()
    original_calls = len(context.client.calls)
    new_profile = profile("b")
    context.config.model_profiles["b"] = new_profile
    with context.store._source_domain.write(context.store.content_store.db):
        context.store.register_model(new_profile)._get_or_create_table()
    second = Client()

    def run(*, restart=False):
        return backfill_embedding_slice(
            context.spec, context.connector, config=context.config,
            store=context.store, paths=context.paths,
            clients={"a": context.client, "b": second}, row_budget=10,
            after_store=context.after, restart=restart,
        )

    with pytest.raises(BackfillRepairRequired, match="binding"):
        run()
    result = run(restart=True)
    assert result.complete and result.vector_rows_upserted == 5
    assert len(second.calls) == 1 and len(second.calls[0]) == 5
    assert len(context.client.calls) == original_calls
    assert context.store.content_store.capture_snapshot() == snapshot
    assert run().vector_rows_upserted == 0


def test_cursor_compare_and_save_never_overwrites_a_concurrent_restart(context, monkeypatch) -> None:
    import json
    from agent_index.indexing import embedding_backfill as module

    save = module._save_progress
    calls = []

    def race(paths, source, raw, payload):
        calls.append(True)
        if len(calls) == 2:
            conn = paths._connect()
            try:
                changed = json.loads(raw)
                changed["generation"] = "concurrent-restart"
                conn.execute("UPDATE embedding_backfills SET payload = ?", (json.dumps(changed),))
                conn.commit()
            finally:
                conn.close()
        return save(paths, source, raw, payload)

    monkeypatch.setattr(module, "_save_progress", race)
    with pytest.raises(BackfillRepairRequired, match="concurrently"):
        context.run()
    assert json.loads(saved(context)["payload"])["generation"] == "concurrent-restart"
    assert json.loads(saved(context)["payload"])["offset"] == 0
    assert context.store._source_domain.receipt()["state"] == "clean"


def test_explicit_discard_preserves_data_and_refuses_a_newer_job(context) -> None:
    from agent_index.indexing.embedding_backfill import discard_embedding_backfill

    context.run()
    original = saved(context)["payload"]
    context.run(restart=True)
    before = context.store.projection_snapshot()
    state = context.config.state_file.read_bytes()
    with pytest.raises(BackfillRepairRequired, match="changed"):
        discard_embedding_backfill(
            context.spec.name, store=context.store, paths=context.paths, expected_payload=original,
        )
    current = saved(context)["payload"]
    discard_embedding_backfill(
        context.spec.name, store=context.store, paths=context.paths, expected_payload=current,
    )
    PendingCrawls(context.paths).assert_gc_available()
    assert context.store.projection_snapshot() == before
    assert context.config.state_file.read_bytes() == state


def test_intervening_ordinary_write_refuses_after_clean_writer_exit(context, monkeypatch) -> None:
    content = context.store.content_store
    read = content.read_snapshot

    def intervening(*args, **kwargs):
        records = read(*args, **kwargs)
        context.store.upsert_content([chunk("ordinary change", source=context.connector.source_name)])
        prepare_chunk_id_index(content, max_rows=20)
        return records

    with monkeypatch.context() as patch:
        patch.setattr(content, "read_snapshot", intervening)
        with pytest.raises(BackfillRepairRequired, match="changed"):
            context.run()
    assert context.client.calls == []
    assert context.store._source_domain.receipt()["state"] == "clean"
    assert content.capture_snapshot().rows == 6
    context.store.upsert_content([chunk("another ordinary change", source=context.connector.source_name)])
    prepare_chunk_id_index(content, max_rows=20)
    assert context.run(restart=True).offset == 2


def test_intervening_covered_restart_refuses_cleanly_before_pair_work(context, monkeypatch) -> None:
    assert context.run(row_budget=10).complete
    content = context.store.content_store
    read = content.read_snapshot
    restarted = []

    def intervening(*args, **kwargs):
        records = read(*args, **kwargs)
        if not restarted:
            restarted.append(True)
            assert context.run(restart=True).vector_rows_upserted == 0
        return records

    with monkeypatch.context() as patch:
        patch.setattr(content, "read_snapshot", intervening)
        with pytest.raises(BackfillRepairRequired, match="intent changed"):
            context.run(restart=True)
    assert len(context.client.calls) == 1
    assert context.store._source_domain.receipt()["state"] == "clean"
    assert context.run().offset == 4


def test_unprepared_canonical_index_refuses_cleanly_then_can_be_prepared(context) -> None:
    from agent_index.store.chunk_id_index import INDEX_NAME, ChunkIdIndexPreparationRequired

    content = context.store.content_store
    with context.store._source_domain.write(content.db):
        context.store._source_domain.effect("fixture-index-drop")
        content._get_or_create_table().drop_index(INDEX_NAME)
    with pytest.raises(ChunkIdIndexPreparationRequired, match="missing"):
        context.run()
    assert context.client.calls == []
    assert context.store._source_domain.receipt()["state"] == "clean"
    prepare_chunk_id_index(content, max_rows=20)
    assert context.run(restart=True).offset == 2


@pytest.mark.parametrize("restart", [False, True])
def test_legacy_recovery_fallback_never_grants_backfill_account_authority(context, restart) -> None:
    context.connector = SimpleNamespace(source_name=context.connector.source_name)
    state = IndexState.load(context.config.state_file)
    state.sources[context.spec.name].acquisition_scope = recovery_scope(
        source_scope_hash(context.spec), context.connector,
    )
    state.save(context.config.state_file)
    before = context.config.state_file.read_bytes()
    with pytest.raises(BackfillRepairRequired, match="explicit connector"):
        context.run(restart=restart)
    assert context.client.calls == []
    assert context.config.state_file.read_bytes() == before
    assert context.store._source_domain.receipt()["state"] == "clean"


@pytest.mark.parametrize("kind", ["configured", "stale"])
def test_gc_guard_and_retirement_exclude_concurrent_backfill_admission(
    context, monkeypatch, kind,
) -> None:
    import threading
    from agent_index.indexing.gc import (
        SourceRetirementPolicy, gc_stale_sources, gc_unconfigured_sources,
    )

    guard = PendingCrawls.assert_gc_available
    failures = []

    def try_backfill():
        try:
            context.run()
        except SourceLayoutError as exc:
            failures.append(str(exc))

    def interleaved(journal):
        guard(journal)
        worker = threading.Thread(target=try_backfill)
        worker.start()
        worker.join(timeout=5)
        assert not worker.is_alive()
        assert failures and "busy" in failures[-1]

    monkeypatch.setattr(PendingCrawls, "assert_gc_available", interleaved)
    state = IndexState.load(context.config.state_file)
    if kind == "configured":
        result = gc_unconfigured_sources(
            context.store, context.paths, state, configured_names=frozenset(),
        )
    else:
        result = gc_stale_sources(
            context.store, context.paths, state,
            policy=SourceRetirementPolicy(is_live_source=lambda _source: False),
        )
    assert result["chunks_deleted"] == 5
    assert context.store.content_store.capture_snapshot().rows == 0
    assert context.store._source_domain.receipt()["state"] == "clean"
    assert PendingCrawls(context.paths).backfill_sources() == []


def test_explicit_retirement_refusal_keeps_domain_clean_and_backfill_reusable(context) -> None:
    from agent_index.indexing.gc import gc_unconfigured_sources

    context.run()
    before = context.store.content_store.capture_snapshot()
    with pytest.raises(PendingCrawlConflict, match="backfill"):
        gc_unconfigured_sources(
            context.store, context.paths, IndexState.load(context.config.state_file),
            configured_names=frozenset(),
        )
    assert context.store._source_domain.receipt()["state"] == "clean"
    assert context.store.content_store.capture_snapshot() == before
    assert context.run().offset == 4
