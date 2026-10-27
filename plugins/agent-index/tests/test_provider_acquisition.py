"""Ordinary paged acquisition negotiates and resumes across actual provider processes."""

from __future__ import annotations

import json

import pytest
from test_provider_delta import DECLARATION, DELTA, ENTRY, FAMILY, Receiver

from agent_index.indexing.crawl import collect_source_changes
from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.pending_crawl import PendingCrawls, recovery_scope
from agent_index.indexing.state import IndexState, SourceState
from agent_index.sources.providers import ProviderError

pytestmark = [pytest.mark.portfolio_tier("T3"), pytest.mark.effect("filesystem")]


def acquisition_receiver(tmp_path):
    receiver = Receiver(tmp_path)
    receiver.config["acquisition_cli"] = True
    receiver.config["metadata"]["source_delta"]["acquisition_page_versions"] = [1]
    receiver.config["acquisition_pages"] = {
        "first": {**DELTA, "acquisition_page_version": 1, "cursor": "history"},
        "history": {
            "version": 1, "acquisition_page_version": 1, "complete": True,
            "upserts": [{**ENTRY, "source": FAMILY + ":commits", "path": "history"}],
            "deletions": [], "commit": "delta-tip", "cursor": None,
        },
    }
    return receiver


@pytest.mark.parametrize("versions", [None, [], [2]])
def test_unsupported_pages_keep_negotiated_flat_delta_syntax(tmp_path, versions):
    receiver = acquisition_receiver(tmp_path)
    declaration = receiver.config["metadata"]["source_delta"]
    if versions is None:
        declaration.pop("acquisition_page_versions")
    else:
        declaration["acquisition_page_versions"] = versions
    receiver.config["acquisition_cli"] = False
    connector = receiver.connector()
    assert connector.discover_delta_page is None
    assert connector.delta_available
    assert connector.discover_delta("base").commit == "delta-tip"
    assert receiver.calls() == [
        ["content-current-commit", "--source", "forge"],
        ["content-discover-changed", "--source", "forge", "--last-commit", "base",
         "--delta-version", "1"],
    ]


@pytest.mark.parametrize("versions", [None, True, "1", [True], [0], [1, 1], [{}]])
def test_malformed_page_versions_refuse_before_discovery(tmp_path, versions):
    receiver = acquisition_receiver(tmp_path)
    receiver.config["metadata"]["source_delta"]["acquisition_page_versions"] = versions
    with pytest.raises(ProviderError, match="acquisition_page_versions"):
        receiver.connector().discover_delta_page
    assert receiver.calls() == [["content-current-commit", "--source", "forge"]]


def test_old_callers_and_provider_rollback_keep_baseline_and_strict_parser(tmp_path):
    receiver = acquisition_receiver(tmp_path)
    receiver.save()
    for verb in ("content-discover", "content-discover-changed"):
        result = receiver.invoke(verb, "--source", "forge")
        assert result.returncode == 0
        assert json.loads(result.stdout)["entries"][0]["path"] == "old.md"
    result = receiver.invoke("content-list-paths", "--source", "forge")
    assert result.returncode == 0
    assert json.loads(result.stdout)["paths"] == {FAMILY: ["old.md"]}
    assert receiver.invoke("content-discover-changed", "--source", "forge", "--typo").returncode == 2
    connector = receiver.connector()
    assert connector.discover_delta("base").commit == "delta-tip"
    receiver.config["acquisition_cli"] = False
    receiver.config["new"] = False
    receiver.config["metadata"].pop("source_delta")
    rolled_back = receiver.connector()
    assert rolled_back.discover_delta_page is None
    assert not rolled_back.delta_available
    assert rolled_back.discover_changed("base")[0].source == "forge"
    assert receiver.calls()[-1] == [
        "content-discover-changed", "--source", "forge", "--last-commit", "base",
    ]


def test_page_capability_requires_delta_scope_contract(tmp_path):
    receiver = acquisition_receiver(tmp_path)
    receiver.config["metadata"]["source_delta"]["versions"] = [2]
    connector = receiver.connector()
    assert connector.discover_delta_page is None
    assert connector.source_name == "forge"


def test_full_and_ordinary_capabilities_select_independently(tmp_path):
    from test_provider_snapshot import snapshot_receiver

    receiver = acquisition_receiver(tmp_path)
    snapshot = snapshot_receiver(tmp_path)
    receiver.config["snapshot_cli"] = True
    receiver.config["snapshot_pages"] = snapshot.config["snapshot_pages"]
    receiver.config["metadata"]["source_delta"]["snapshot_versions"] = [1]
    connector = receiver.connector()
    discover = connector.discover_delta_page
    assert 1 in discover.acquisition_page_versions
    receiver.config["metadata"]["commit"] = "later"
    receiver.save()
    assert connector.current_commit() == "metadata-tip"
    manifest = collect_source_changes(
        "alias", state=IndexState(), path_index=PathIndex(tmp_path / "paths.db"),
        full=True, source_aware=True, connector=connector,
    )
    assert manifest.commit == "snapshot-tip"
    assert all("--acquisition-page-version" not in call for call in receiver.calls())
    assert len([call for call in receiver.calls() if call[0] == "content-current-commit"]) == 1


@pytest.mark.parametrize("count", [0, 256])
def test_empty_and_maximum_terminal_units_are_valid_without_a_base(tmp_path, count):
    receiver = acquisition_receiver(tmp_path)
    receiver.config["acquisition_pages"]["first"] = {
        **DELTA, "upserts": [{**ENTRY, "path": str(i)} for i in range(count)],
        "deletions": [], "acquisition_page_version": 1, "cursor": None,
    }
    discover = receiver.connector().discover_delta_page
    page = discover(None, None)
    assert page.cursor is None
    assert len(page.delta.upserts) == count
    assert not any(arg.startswith("--last-commit") for arg in receiver.calls()[-1])


@pytest.mark.parametrize("base,cursor", [("base", "history"), ("--base", "-next"), ("", "next")])
def test_restart_resumes_acknowledged_unit_with_original_base_and_no_index_effects(
    tmp_path, base, cursor,
):
    receiver = acquisition_receiver(tmp_path)
    pages = receiver.config["acquisition_pages"]
    pages["first"]["cursor"] = cursor
    pages[cursor] = pages.pop("history")
    receiver.config["fail_acquisition"] = True
    connector = receiver.connector()
    paths = PathIndex(tmp_path / "paths.db")
    paths.mark_indexed(FAMILY, "gone.md", chunk_count=1)
    paths.mark_indexed(FAMILY, "keep.md", chunk_count=1)
    paths.mark_indexed("other:repo", "untouched.md", chunk_count=1)
    state = IndexState(sources={"alias": SourceState(last_commit=base)})
    scope = recovery_scope("alias", connector)
    state.sources["alias"].acquisition_scope = scope
    state.stored_families["alias"] = FAMILY

    def collect(connector):
        return collect_source_changes(
            "alias", state=state, path_index=paths, full=False,
            source_aware=True, connector=connector,
        )

    with pytest.raises(ProviderError, match="temporarily unavailable"):
        collect(connector)
    pending = PendingCrawls(paths).read_payload("alias", scope)
    assert json.loads(pending)["cursor"] == cursor
    assert json.loads(pending)["base_commit"] == base
    assert PendingCrawls(paths).load("alias", scope) is None
    assert state.sources["alias"].last_commit == base
    assert paths.count() == 3
    receiver.config["fail_acquisition"] = False
    receiver.config["metadata"]["commit"] = "later-metadata-tip"
    paths = PathIndex(tmp_path / "paths.db")
    manifest = collect(receiver.connector())
    assert manifest.commit == "delta-tip"
    assert [(entry.source, entry.path) for entry in manifest.upserts] == [
        (FAMILY, "new.md"), (FAMILY + ":commits", "history"),
    ]
    assert [(entry.source, entry.path) for entry in manifest.deletions] == [(FAMILY, "gone.md")]
    assert state.sources["alias"].last_commit == base
    assert paths.count() == 3
    assert PendingCrawls(paths).load("alias", scope) == (manifest, False)
    args = [
        "content-discover-changed", "--source", "forge", "--delta-version", "1",
        "--acquisition-page-version", "1", f"--last-commit={base}",
    ]
    assert receiver.calls() == [
        ["content-current-commit", "--source", "forge"],
        args, [*args, f"--cursor={cursor}"],
        ["content-current-commit", "--source", "forge"],
        [*args, f"--cursor={cursor}"],
    ]


@pytest.mark.parametrize("field,value", [
    ("acquisition_page_version", True), ("acquisition_page_version", 2),
    ("version", True), ("version", 2), ("complete", False), ("complete", 1),
    ("cursor", ""), ("cursor", 4), ("cursor", "bad\0cursor"), ("cursor", "x" * 65537),
    ("commit", 4), ("commit", "bad\0commit"), ("upserts", None), ("deletions", None),
    ("upserts", [ENTRY] * 256), ("upserts", [ENTRY, ENTRY]),
    ("upserts", [{**ENTRY, "source": "other:repo"}]),
    ("deletions", [{"source": FAMILY, "path": "new.md"}]),
])
def test_invalid_pages_never_reach_journal(tmp_path, field, value):
    receiver = acquisition_receiver(tmp_path)
    receiver.config["acquisition_pages"]["first"][field] = value
    paths = PathIndex(tmp_path / "paths.db")
    with pytest.raises(ProviderError):
        collect_source_changes(
            "alias", state=IndexState(), path_index=paths, full=False,
            source_aware=True, connector=receiver.connector(),
        )
    assert PendingCrawls(paths).sources() == []
    assert paths.count() == 0


@pytest.mark.parametrize("field", [
    "acquisition_page_version", "cursor", "version", "upserts", "deletions", "commit", "complete",
])
def test_page_fields_are_required_even_when_null_is_valid(tmp_path, field):
    receiver = acquisition_receiver(tmp_path)
    receiver.config["acquisition_pages"]["first"].pop(field)
    discover = receiver.connector().discover_delta_page
    with pytest.raises(ProviderError, match="requires|must contain"):
        discover(None, None)


@pytest.mark.parametrize("echo", [
    None, "missing", DECLARATION, {**DECLARATION, "acquisition_page_versions": [2]},
    {**DECLARATION, "acquisition_page_versions": [1], "account": "other"},
    {**DECLARATION, "acquisition_page_versions": [1], "target": "other"},
    {**DECLARATION, "acquisition_page_versions": [1], "family": FAMILY + ":other"},
])
def test_scope_and_page_capability_echo_are_enforced_before_entries(tmp_path, echo):
    receiver = acquisition_receiver(tmp_path)
    if echo == "missing":
        receiver.config["echo_scope"] = False
    else:
        receiver.config["delta_scope"] = echo
    connector = receiver.connector()
    discover = connector.discover_delta_page
    connector._entries = lambda *a: pytest.fail("unvalidated scope reached entries")
    with pytest.raises(ProviderError, match="scope echo|source_delta"):
        discover(None, None)


@pytest.mark.parametrize("base,cursor", [("bad\0base", None), ("base", "bad\0cursor")])
def test_invalid_request_tokens_refuse_before_discovery(tmp_path, base, cursor):
    receiver = acquisition_receiver(tmp_path)
    discover = receiver.connector().discover_delta_page
    with pytest.raises(ValueError, match="NUL"):
        discover(base, cursor)
    assert receiver.calls() == [["content-current-commit", "--source", "forge"]]


@pytest.mark.parametrize("change", ["commit", "cursor", "capability", "account", "base", "truncated"])
def test_continuation_drift_failure_and_rollback_preserve_acknowledged_progress(tmp_path, change):
    receiver = acquisition_receiver(tmp_path)
    receiver.config["fail_acquisition"] = True
    connector = receiver.connector()
    paths = PathIndex(tmp_path / "paths.db")
    state = IndexState(sources={"alias": SourceState(last_commit="base")})
    scope = recovery_scope("alias", connector)
    state.sources["alias"].acquisition_scope = scope
    state.stored_families["alias"] = FAMILY

    def collect(connector):
        return collect_source_changes(
            "alias", state=state, path_index=paths, full=False,
            source_aware=True, connector=connector,
        )

    with pytest.raises(ProviderError):
        collect(connector)
    before = PendingCrawls(paths).read_payload("alias", scope)
    count = len(receiver.calls())
    receiver.config["fail_acquisition"] = False
    if change == "capability":
        receiver.config["metadata"]["source_delta"].pop("acquisition_page_versions")
        receiver.config["acquisition_cli"] = False
    elif change == "account":
        receiver.config["metadata"]["source_delta"]["account"] = "other"
    elif change == "base":
        state.sources["alias"].last_commit = "other"
    elif change == "truncated":
        receiver.config["truncate_acquisition"] = True
    else:
        receiver.config["acquisition_pages"]["history"][change] = (
            "history" if change == "cursor" else "different"
        )
    with pytest.raises((ValueError, ProviderError)):
        collect(receiver.connector())
    if change in {"capability", "account", "base"}:
        assert receiver.calls()[count:] == [["content-current-commit", "--source", "forge"]]
    assert PendingCrawls(paths).read_payload("alias", scope) == before
    assert paths.count() == 0
