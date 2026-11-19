"""Required page acquisition cannot fall back or evade policy through recovery."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, NoReturn, cast

import pytest

from agent_index.indexing import engine
from agent_index.indexing.acquisition_requirement import require_execution_v1
from agent_index.indexing.change_hints import ChangeHints
from agent_index.indexing.manifest import CrawlManifest
from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.pending_crawl import PendingCrawls, recovery_scope
from agent_index.indexing.state import IndexState, SourceState
from agent_index.sources.base import FileEntry
from agent_index.sources.delta import AcquisitionPage, SnapshotPage, SourceDelta

FAMILY = "forge:repo"


def forbidden(*args: object, **kwargs: object) -> NoReturn:
    pytest.fail("required acquisition reached a fallback or unqualified publication")


@pytest.fixture
def acquisition(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    calls: list[tuple[Any, ...]] = []
    responses: list[Any] = []
    hints = ChangeHints(
        source="alias", canonical_family=FAMILY, account_id="account-a", item_ids=("one",),
    )

    def page(*args: Any, **kwargs: Any) -> Any:
        calls.append(args)
        response = responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    setattr(page, "acquisition_page_versions", (1,))
    setattr(page, "snapshot_page_versions", (1,))
    setattr(page, "change_hints_versions", (1,))
    connector = SimpleNamespace(
        source_name=FAMILY, recovery_identity=("target", "account-a"),
        hint_scope=(FAMILY, "account-a"), discover_delta_page=page,
        discover_snapshot_page=page, discover_hints_page=page,
        discover_hints=forbidden, discover_delta=forbidden,
        list_paths=forbidden, discover_changed=forbidden, discover=forbidden,
        current_commit=forbidden,
    )
    scope = recovery_scope("config", connector)
    paths_file = tmp_path / "paths.db"
    state_file = tmp_path / "state.json"
    state = IndexState(
        sources={"alias": SourceState(last_commit="base", acquisition_scope=scope)},
        stored_families={"alias": FAMILY},
    )
    state.save(state_file)
    projection: dict[str, str] = {}
    applications: list[CrawlManifest] = []
    fail_apply = False

    def apply(
        source: str, *, manifest: CrawlManifest, state: IndexState,
        path_index: PathIndex, **kwargs: Any,
    ) -> tuple[int, int, int]:
        if fail_apply:
            raise RuntimeError("interrupted application")
        applications.append(manifest)
        for entry in manifest.upserts:
            projection[entry.path] = entry.content
            path_index.mark_indexed_batch([(entry.source, entry.path, None, 1)])
        state.mark_source_indexed(source, manifest.commit, len(projection))
        return len(manifest.upserts), 0, len(manifest.upserts)

    monkeypatch.setattr(engine, "_apply_source_manifest", apply)
    monkeypatch.setattr(engine, "selected_layout", lambda: "source-aware")

    def run(
        mode: str = "incremental", *, required: Any = True, fail: bool = False,
    ) -> tuple[int, int, int]:
        nonlocal fail_apply
        fail_apply = fail
        return engine._index_source(
            "alias", config=cast(Any, SimpleNamespace(state_file=state_file)),
            state=IndexState.load(state_file), path_index=PathIndex(paths_file),
            multi_store=cast(Any, SimpleNamespace()), multi_clients={}, full=mode == "full",
            connector=connector, source_scope="config",
            change_hints=hints if mode == "hinted" else None,
            require_paged_acquisition=required,
        )

    return SimpleNamespace(
        calls=calls, responses=responses, hints=hints, connector=connector,
        run=run, paths_file=paths_file, state_file=state_file, scope=scope,
        projection=projection, applications=applications,
        journal=lambda: PendingCrawls(PathIndex(paths_file)),
    )


def unit(mode: str, *, cursor: str | None = None) -> AcquisitionPage | SnapshotPage:
    entry = FileEntry("one", "body", "text", FAMILY)
    if mode == "full":
        return SnapshotPage("snapshot", [entry], "target", cursor)
    return AcquisitionPage(
        SourceDelta(upserts=[entry], commit="base" if mode == "hinted" else "target"),
        cursor,
    )


@pytest.mark.parametrize("mode", ["incremental", "full", "hinted"])
def test_required_engine_pages_preserve_scope_cursor_and_projection(
    acquisition: SimpleNamespace, mode: str,
) -> None:
    a = acquisition
    a.responses.append(unit(mode))
    assert a.run(mode) == (1, 0, 1)
    assert a.projection == {"one": "body"}
    assert PathIndex(a.paths_file).count(FAMILY) == 1
    saved = IndexState.load(a.state_file)
    assert saved.sources["alias"].last_commit == ("base" if mode == "hinted" else "target")
    assert saved.sources["alias"].acquisition_scope == a.scope
    assert a.journal().read_payload("alias", a.scope) is None


@pytest.mark.parametrize("mode", ["incremental", "full", "hinted"])
def test_required_reopen_resumes_pages_and_ready_work_without_losing_provenance(
    acquisition: SimpleNamespace, mode: str,
) -> None:
    a = acquisition
    a.responses.extend([unit(mode, cursor="next"), RuntimeError("interrupted acquisition")])
    with pytest.raises(RuntimeError, match="interrupted acquisition"):
        a.run(mode)
    partial = a.journal().read_payload("alias", a.scope)
    assert json.loads(partial)["version"] == {"incremental": 2, "full": 4, "hinted": 5}[mode]
    assert not a.projection and not a.applications

    # Continue with a distinct identity to avoid overlapping page identities.
    terminal = (
        SnapshotPage("snapshot", commit="target") if mode == "full"
        else AcquisitionPage(SourceDelta(commit="base" if mode == "hinted" else "target"))
    )
    a.responses.append(terminal)
    with pytest.raises(RuntimeError, match="interrupted application"):
        a.run(mode, fail=True)
    ready = a.journal().read_payload("alias", a.scope)
    assert json.loads(ready)["version"] == (6 if mode == "hinted" else 7)
    assert not a.projection and IndexState.load(a.state_file).sources["alias"].last_commit == "base"
    assert len(a.calls) == 3 and a.calls[-1][-1] == "next"
    if mode != "hinted":
        a.responses.append(RuntimeError("new round after replay"))
        with pytest.raises(RuntimeError, match="new round after replay"):
            a.run(mode)
    else:
        assert a.run(mode) == (1, 0, 1)
    assert a.projection == {"one": "body"} and len(a.applications) == 1
    assert a.journal().read_payload("alias", a.scope) is None
    assert IndexState.load(a.state_file).sources["alias"].last_commit == (
        "base" if mode == "hinted" else "target"
    )


@pytest.mark.parametrize("mode", ["incremental", "full", "hinted"])
@pytest.mark.parametrize("retained", ["legacy-ready", "other-mode"])
def test_required_engine_refuses_unqualified_retained_work_before_application(
    acquisition: SimpleNamespace, mode: str, retained: str,
) -> None:
    a = acquisition
    if retained == "legacy-ready":
        a.journal().save(
            CrawlManifest(source="alias", accounting_family=FAMILY, commit="unqualified"),
            scope=a.scope, full=mode == "full",
        )
    else:
        a.responses.append(unit("incremental" if mode != "incremental" else "full"))
        with pytest.raises(RuntimeError, match="interrupted application"):
            a.run("incremental" if mode != "incremental" else "full", fail=True)
        a.calls.clear()
    before = a.journal().read_payload("alias", a.scope)
    with pytest.raises(ValueError, match="cannot qualify retained acquisition"):
        a.run(mode)
    assert a.journal().read_payload("alias", a.scope) == before
    assert not a.calls and not a.applications and not a.projection
    assert IndexState.load(a.state_file).sources["alias"].last_commit == "base"


@pytest.mark.parametrize("versions", [None, (), (True,), (1.0,), (1, "2"), "1", (2,)])
@pytest.mark.parametrize("mode", ["incremental", "full", "hinted"])
def test_required_connector_declaration_refuses_before_ready_replay(
    acquisition: SimpleNamespace, versions: object, mode: str,
) -> None:
    a = acquisition
    a.journal().save(
        CrawlManifest(source="alias", accounting_family=FAMILY),
        scope=a.scope, full=mode == "full",
    )
    selected = a.connector.discover_delta_page
    attribute = "snapshot_page_versions" if mode == "full" else "acquisition_page_versions"
    setattr(selected, attribute, versions)
    before = a.journal().read_payload("alias", a.scope)
    with pytest.raises(ValueError):
        a.run(mode)
    assert a.journal().read_payload("alias", a.scope) == before
    assert not a.calls and not a.projection and not a.applications


def test_legacy_default_still_lists_and_required_call_does_not(acquisition: SimpleNamespace) -> None:
    a = acquisition
    a.connector.discover_delta_page = None
    a.connector.discover_delta = None
    legacy_calls: list[str] = []
    a.connector.current_commit = lambda: "target"

    def list_paths(**kwargs: object) -> dict[str, set[str]]:
        legacy_calls.append("list")
        return {FAMILY: set()}

    def discover_changed(*args: object, **kwargs: object) -> list[FileEntry]:
        legacy_calls.append("discover")
        return []

    a.connector.list_paths = list_paths
    a.connector.discover_changed = discover_changed
    with pytest.raises(ValueError, match="selected callable"):
        a.run()
    assert not legacy_calls
    assert a.run(required=False) == (0, 0, 0)
    assert legacy_calls == ["list", "discover"]


@pytest.mark.parametrize("value", [None, 1, "false"])
def test_invalid_requirement_refuses_before_connector_or_replay(
    acquisition: SimpleNamespace, value: object,
) -> None:
    a = acquisition
    with pytest.raises(ValueError, match="must be a bool"):
        a.run(required=value)
    assert not a.calls and not a.projection and not a.applications


def test_required_publication_cannot_treat_partial_pages_as_an_empty_completed_crawl(
    acquisition: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    a = acquisition
    a.responses.extend([unit("incremental", cursor="next"), RuntimeError("interrupted")])
    with pytest.raises(RuntimeError, match="interrupted"):
        a.run()
    before = a.journal().read_payload("alias", a.scope)
    monkeypatch.setattr(
        engine, "_crawl_source",
        lambda *args, **kwargs: CrawlManifest(source="alias", commit="base", accounting_family=FAMILY),
    )
    with pytest.raises(RuntimeError, match="did not finish its pending pages"):
        a.run()
    assert a.journal().read_payload("alias", a.scope) == before
    assert not a.projection and not a.applications


@pytest.mark.parametrize("versions", [None, (), (True,), (1.0,), (1, "2"), "1", (2,), (1,)])
def test_new_caller_negotiates_the_actual_callable_before_passing_requirement(versions: object) -> None:
    calls: list[dict[str, object]] = []

    def selected(**kwargs: object) -> None:
        calls.append(kwargs)

    setattr(selected, "paged_acquisition_versions", versions)
    if isinstance(versions, tuple) and versions == (1,) and type(versions[0]) is int:
        require_execution_v1(selected)
        selected(require_paged_acquisition=True)
        assert calls == [{"require_paged_acquisition": True}]
    else:
        with pytest.raises(ValueError, match="Selected indexing callable"):
            require_execution_v1(selected)
        assert not calls
    # Neither a wrapper nor an older engine inherits another callable's declaration.
    with pytest.raises(ValueError):
        require_execution_v1(lambda **kwargs: selected(**kwargs))


def test_ready_provenance_codec_is_strict_and_legacy_modes_still_decode(acquisition: SimpleNamespace) -> None:
    a = acquisition
    a.responses.append(unit("incremental"))
    with pytest.raises(RuntimeError, match="interrupted application"):
        a.run(fail=True)
    payload = a.journal().read_payload("alias", a.scope)
    data = json.loads(payload)
    assert a.journal().decode_pending(payload, "alias")[1] is False
    for changed in (
        {**data, "page_protocol": "full"}, {**data, "full": 0},
        {**data, "unknown": True}, {**data, "version": True},
    ):
        with pytest.raises(ValueError):
            a.journal().decode_pending(json.dumps(changed), "alias")
    legacy = {key: value for key, value in data.items() if key != "page_protocol"}
    legacy["version"] = 1
    assert a.journal().decode_pending(json.dumps(legacy), "alias") == (
        a.journal().decode_pending(payload, "alias")
    )


@pytest.mark.parametrize("items", [1, 1000])
def test_required_ready_codec_reuses_its_one_manifest_parse(
    acquisition: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, items: int,
) -> None:
    a = acquisition
    a.responses.append(unit("incremental"))
    with pytest.raises(RuntimeError, match="interrupted application"):
        a.run(fail=True)
    data = json.loads(a.journal().read_payload("alias", a.scope))
    template = data["manifest"]["upserts"][0]
    data["manifest"]["upserts"] = [
        {**template, "path": str(i), "content": "x" * 256} for i in range(items)
    ]
    payload = json.dumps(data)
    loads = json.loads
    calls: list[Any] = []

    def observe(value: Any, *args: Any, **kwargs: Any) -> Any:
        calls.append(value)
        return loads(value, *args, **kwargs)

    monkeypatch.setattr(json, "loads", observe)
    decoded = PendingCrawls.decode_pending(payload, "alias", required_pages=True)
    assert decoded is not None and len(decoded[0].upserts) == items
    assert calls == [payload]
