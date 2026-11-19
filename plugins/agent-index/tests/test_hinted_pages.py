"""Hinted units resume through the collector's existing SQLite pending owner."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent_index.index_config import IndexConfig, ModelProfile
from agent_index.indexing import engine
from agent_index.indexing.change_hints import ChangeHints, MetadataRevision, RefRange
from agent_index.indexing.crawl import collect_source_changes
from agent_index.indexing.embedding_runtime import EmbeddingRuntime
from agent_index.indexing.hinted_crawl import require_hint_connector
from agent_index.indexing.manifest import CrawlManifest
from agent_index.indexing.paged_crawl import discard_acquisition
from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.pending_crawl import (
    PendingCrawlConflict,
    PendingCrawls,
    recover_source,
    recovery_scope,
)
from agent_index.indexing.state import IndexState, SourceState
from agent_index.sources.base import FileEntry
from agent_index.sources.delta import AcquisitionPage, SourceDelta
from agent_index.store.source_layout import LAYOUT

FAMILY = "forge:repo"


def forbidden(*args: object, **kwargs: object) -> Any:
    pytest.fail("hinted pages attempted a polling or whole-source fallback")


@pytest.fixture
def acquisition(tmp_path: Path) -> SimpleNamespace:
    hints = ChangeHints(
        source="alias",
        canonical_family=FAMILY,
        account_id="account-a",
        item_ids=("1", "2"),
        event_ids=("delivery",),
        ref_ranges=(RefRange("a", "b"),),
        metadata_revisions=(MetadataRevision("1", "r1"),),
    )
    calls: list[str | None] = []
    responses: list[AcquisitionPage | Exception | Callable[[], AcquisitionPage]] = []

    def discover(
        value: ChangeHints,
        prior: str | None,
        cursor: str | None,
        *,
        cancel_check: Callable[[], None] | None,
    ) -> AcquisitionPage:
        assert value == hints and prior == "poll"
        calls.append(cursor)
        response = responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        if callable(response):
            return response()
        return response

    setattr(discover, "change_hints_versions", (1,))
    setattr(discover, "acquisition_page_versions", (1,))
    provider = SimpleNamespace(
        source_name=FAMILY,
        hint_scope=(FAMILY, "account-a"),
        recovery_identity=("target", "account-a"),
        discover_hints_page=discover,
        discover_hints=forbidden,
        discover_delta=forbidden,
        list_paths=forbidden,
        discover=forbidden,
        current_commit=forbidden,
    )
    scope = recovery_scope("config", provider)
    state = IndexState(
        sources={"alias": SourceState(last_commit="poll", acquisition_scope=scope)},
        stored_families={"alias": FAMILY},
    )
    paths = PathIndex(tmp_path / "paths.db")

    def collect(**kwargs: Any) -> CrawlManifest:
        return collect_source_changes(
            "alias",
            state=state,
            path_index=PathIndex(tmp_path / "paths.db"),
            full=kwargs.pop("full", False),
            source_aware=True,
            source_scope="config",
            connector=provider,
            change_hints=kwargs.pop("change_hints", hints),
            **kwargs,
        )

    return SimpleNamespace(
        hints=hints,
        calls=calls,
        responses=responses,
        provider=provider,
        scope=scope,
        state=state,
        paths=paths,
        collect=collect,
        journal=PendingCrawls(paths),
    )


def page(number: int, cursor: str | None = None, *, commit: str = "poll") -> AcquisitionPage:
    return AcquisitionPage(
        SourceDelta(
            upserts=[FileEntry(f"{number}.md", f"body {number}", "text", FAMILY)],
            commit=commit,
        ),
        cursor,
    )


def interrupt(acquisition: SimpleNamespace) -> str:
    acquisition.responses.extend([page(1, "unit-2"), RuntimeError("interrupted")])
    with pytest.raises(RuntimeError, match="interrupted"):
        acquisition.collect()
    payload: str | None = acquisition.journal.read_payload("alias", acquisition.scope)
    assert payload is not None
    return payload


def test_restart_reuses_completed_unit_and_retains_refresh_only_at_terminal(
    acquisition: SimpleNamespace,
    tmp_path: Path,
) -> None:
    a = acquisition
    a.provider.hint_poll_refresh = True

    def poll(*args: object, **kwargs: object) -> SourceDelta:
        return SourceDelta()

    setattr(poll, "poll_refresh_versions", (1,))
    a.provider.discover_delta = poll
    before = interrupt(a)
    assert a.journal.load("alias", a.scope) is None
    assert a.journal.refresh_candidates("alias", a.scope, FAMILY) is None
    assert a.state.sources["alias"].last_commit == "poll"
    with pytest.raises(PendingCrawlConflict):
        a.journal.assert_gc_available()
    with pytest.raises(PendingCrawlConflict):
        a.journal.assert_family_available("other", FAMILY + ":issues")
    a.journal.assert_family_available("independent", "forge:elsewhere")
    assert json.loads(before)["hints"] == a.hints.to_dict()
    a.responses.append(page(2))
    result = a.collect()
    assert a.calls == [None, "unit-2", "unit-2"]
    assert [entry.path for entry in result.upserts] == ["1.md", "2.md"]
    assert result.commit == "poll" and result.stats.files_scanned == 2
    assert a.journal.load("alias", a.scope) == (result, False)
    refresh = a.journal.refresh_candidates("alias", a.scope, FAMILY)
    assert refresh is not None and set(refresh.paths) == {"1.md", "2.md"}

    applied: list[CrawlManifest] = []

    def apply(*, manifest: CrawlManifest, full: bool) -> tuple[int, int, int]:
        applied.append(manifest)
        assert a.calls == [None, "unit-2", "unit-2"] and not full
        a.state.mark_source_indexed("alias", manifest.commit, len(manifest.upserts))
        return len(manifest.upserts), 0, len(manifest.upserts)

    # A fresh journal/PathIndex sees the terminal handoff; no provider is consulted.
    assert recover_source(
        "alias",
        scope=a.scope,
        state=a.state,
        state_file=tmp_path / "state.json",
        paths=PathIndex(tmp_path / "paths.db"),
        full=False,
        family=FAMILY,
        apply=apply,
        acquire=lambda: CrawlManifest(source="alias", commit="poll", accounting_family=FAMILY),
    ) == (2, 0, 2)
    assert applied[0] == result
    assert IndexState.load(tmp_path / "state.json").sources["alias"].last_commit == "poll"
    assert a.journal.read_payload("alias", a.scope) is None
    assert a.journal.refresh_candidates("alias", a.scope, FAMILY) == refresh


@pytest.mark.parametrize(
    "changed",
    [
        "items",
        "events",
        "ranges",
        "revisions",
        "account",
        "recovery",
        "base",
        "full",
        "poll",
        "legacy",
        "refresh-policy",
        "bad-envelope",
    ],
)
def test_resume_binding_refuses_before_new_provider_work(
    acquisition: SimpleNamespace,
    changed: str,
) -> None:
    a = acquisition
    before = interrupt(a)
    kwargs: dict[str, Any] = {}
    if changed in {"items", "events", "ranges", "revisions", "account"}:
        field, value = {
            "items": ("item_ids", ("3",)),
            "events": ("event_ids", ("other",)),
            "ranges": ("ref_ranges", (RefRange("c", "d"),)),
            "revisions": ("metadata_revisions", (MetadataRevision("1", "r2"),)),
            "account": ("account_id", "account-b"),
        }[changed]
        kwargs["change_hints"] = replace(a.hints, **{field: value})
    elif changed == "recovery":
        a.provider.recovery_identity = ("target", "account-b")
    elif changed == "base":
        a.state.sources["alias"].last_commit = "other"
    elif changed == "full":
        kwargs["full"] = True
    elif changed == "poll":
        kwargs["change_hints"] = None
    elif changed == "legacy":
        a.provider.discover_hints_page = None
    elif changed == "refresh-policy":
        a.provider.hint_poll_refresh = True

        def poll(*args: object, **kwargs: object) -> SourceDelta:
            return SourceDelta()

        setattr(poll, "poll_refresh_versions", (1,))
        a.provider.discover_delta = poll
    else:
        data = json.loads(before)
        data["hints"]["unknown"] = True
        before = json.dumps(data)
        a.journal.compare_and_save(
            "alias",
            scope=a.scope,
            payload=before,
            expected=a.journal.read_payload("alias", a.scope),
            family=FAMILY,
        )
    with pytest.raises(ValueError):
        a.collect(**kwargs)
    assert a.calls == [None, "unit-2"]
    assert a.journal.read_payload("alias", a.scope) == before


@pytest.mark.parametrize(
    "bad",
    [
        page(2, commit="advanced"),
        page(1),
        page(2, "unit-2"),
        AcquisitionPage(SourceDelta(commit="poll", complete=False)),
        AcquisitionPage(SourceDelta(commit="poll"), "x" * 65537),
        SourceDelta(commit="poll"),
    ],
)
def test_invalid_resumed_page_keeps_prior_progress(
    acquisition: SimpleNamespace,
    bad: AcquisitionPage | SourceDelta,
) -> None:
    a = acquisition
    before = interrupt(a)
    a.responses.append(bad)
    with pytest.raises(ValueError):
        a.collect()
    assert a.journal.read_payload("alias", a.scope) == before


def test_first_hint_page_cannot_advance_poll_cursor(acquisition: SimpleNamespace) -> None:
    a = acquisition
    a.responses.append(page(1, commit="advanced"))
    with pytest.raises(ValueError, match="polling cursor"):
        a.collect()
    assert a.journal.sources() == []


def test_collector_budget_stops_at_128_units_and_resumes(acquisition: SimpleNamespace) -> None:
    a = acquisition
    a.responses.extend(page(i, str(i)) for i in range(1, 129))
    with pytest.raises(RuntimeError, match=r"budget \(128\)"):
        a.collect()
    assert len(a.calls) == 128 and a.calls[-1] == "127"
    a.responses.append(page(129))
    result = a.collect()
    assert len(a.calls) == 129 and a.calls[-1] == "128"
    assert len(result.upserts) == 129 and result.commit == "poll"


def test_cancel_after_second_unit_preserves_first_only(acquisition: SimpleNamespace) -> None:
    a = acquisition
    a.responses.extend([page(1, "unit-2"), page(2)])

    def cancel() -> None:
        if len(a.calls) == 2:
            raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        a.collect(progress_cb=SimpleNamespace(check_cancelled=cancel))
    payload = json.loads(a.journal.read_payload("alias", a.scope))
    assert payload["cursor"] == "unit-2"
    assert [entry["path"] for entry in payload["manifest"]["upserts"]] == ["1.md"]
    a.responses.append(page(2))
    assert len(a.collect().upserts) == 2


def test_competing_progress_and_explicit_discard_use_same_cas_owner(
    acquisition: SimpleNamespace,
) -> None:
    a = acquisition
    before = interrupt(a)

    def competing() -> AcquisitionPage:
        discard_acquisition(
            "alias",
            paths=a.paths,
            connector=a.provider,
            source_scope="config",
            expected_payload=before,
        )
        return page(2)

    a.responses.append(competing)
    with pytest.raises(PendingCrawlConflict):
        a.collect()
    assert a.journal.sources() == []


@pytest.mark.parametrize("versions", [None, (), (True,), (2,), "1"])
def test_unnegotiated_page_callable_never_replaces_legacy_hints(
    acquisition: SimpleNamespace,
    versions: object,
) -> None:
    a = acquisition
    a.provider.discover_hints_page.acquisition_page_versions = versions

    def legacy(*args: object, **kwargs: object) -> SourceDelta:
        return SourceDelta(commit="poll")

    setattr(legacy, "change_hints_versions", (1,))
    a.provider.discover_hints = legacy
    assert require_hint_connector(a.provider, a.hints) is legacy
    assert a.collect().commit == "poll" and a.calls == []


@pytest.mark.parametrize("source_aware", [False, True])
def test_actual_engine_indexes_only_after_terminal_page_and_preserves_poll_cursor(
    acquisition: SimpleNamespace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_aware: bool,
) -> None:
    from test_consumer_runtime import Client

    a = acquisition
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT if source_aware else "")
    config = IndexConfig(
        data_dir=tmp_path / "engine",
        cluster_enabled=False,
        model_profiles={"a": ModelProfile("a", "fixture", dim=2)},
    )
    spec = engine.SourceSpec("alias")
    a.state.sources["alias"].acquisition_scope = recovery_scope(
        engine.source_scope_hash(spec),
        a.provider,
    )
    a.state.save(config.state_file)
    monkeypatch.setattr("agent_index.sources.get_connector", lambda *args, **kwargs: a.provider)
    monkeypatch.setattr(engine, "_connector_kwargs", lambda spec: {})
    monkeypatch.setattr(PathIndex, "get_paths_by_family", forbidden)
    events: list[str] = []
    clients: list[Any] = []

    def create(profile: ModelProfile) -> Any:
        client = Client(profile.model_id, events)
        clients.append(client)
        return client

    def ensure(*args: object) -> bool:
        assert a.calls == [None, "unit-2", "unit-2"]
        return True

    runtime = EmbeddingRuntime(create, ensure, lambda *args: None)
    a.responses.extend([page(1, "unit-2"), RuntimeError("interrupted")])

    def run() -> dict[str, Any]:
        return engine.run_reindex(
            source="alias",
            source_specs=[spec],
            config=config,
            change_hints=a.hints,
            embedding_runtime=runtime,
        )

    with pytest.raises(RuntimeError, match="interrupted"):
        run()
    assert not clients[0].texts
    assert IndexState.load(config.state_file).sources["alias"].last_commit == "poll"
    a.responses.append(page(2))
    result = run()
    assert not result.get("sources_failed")
    assert result["work_counts"]["upserts_acquired"] == 2
    assert result["chunks_total"] > 0 and clients[-1].texts
    assert a.calls == [None, "unit-2", "unit-2"]
    assert IndexState.load(config.state_file).sources["alias"].last_commit == "poll"
    assert PendingCrawls(PathIndex(config.data_dir / "path_index.db")).sources() == []
