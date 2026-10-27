"""Full-snapshot capability selection and replay cross the real provider CLI."""

from __future__ import annotations

import json

import pytest
from test_provider_delta import DECLARATION, ENTRY, FAMILY, Receiver

from agent_index.indexing.crawl import collect_source_changes
from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.pending_crawl import PendingCrawls, recovery_scope
from agent_index.indexing.state import IndexState, SourceState
from agent_index.sources.providers import ProviderError


def snapshot_receiver(tmp_path):
    receiver = Receiver(tmp_path)
    receiver.config["snapshot_cli"] = True
    receiver.config["metadata"]["source_delta"]["snapshot_versions"] = [1]
    receiver.config["snapshot_pages"] = {
        "first": {
            "version": 1, "snapshot": "immutable-generation", "upserts": [ENTRY],
            "commit": "snapshot-tip", "cursor": "history",
        },
        "history": {
            "version": 1, "snapshot": "immutable-generation",
            "upserts": [{**ENTRY, "source": FAMILY + ":commits", "path": "history"}],
            "commit": "snapshot-tip", "cursor": None,
        },
    }
    return receiver


@pytest.mark.parametrize("versions", [None, [], [2]])
def test_unsupported_snapshot_capability_preserves_legacy_cli(tmp_path, versions):
    receiver = snapshot_receiver(tmp_path)
    declaration = receiver.config["metadata"]["source_delta"]
    if versions is None:
        declaration.pop("snapshot_versions")
    else:
        declaration["snapshot_versions"] = versions
    connector = receiver.connector()
    assert connector.discover_snapshot_page is None
    assert connector.discover()[0].path == "old.md"
    assert receiver.calls() == [
        ["content-current-commit", "--source", "forge"],
        ["content-discover", "--source", "forge"],
    ]


@pytest.mark.parametrize("versions", [None, True, "1", [True], [0], [1, 1], [{}]])
def test_malformed_snapshot_declaration_refuses_before_discovery(tmp_path, versions):
    receiver = snapshot_receiver(tmp_path)
    receiver.config["metadata"]["source_delta"]["snapshot_versions"] = versions
    with pytest.raises(ProviderError, match="snapshot_versions"):
        receiver.connector().source_name
    assert len(receiver.calls()) == 1


def test_old_callers_and_rolled_back_receivers_retain_strict_baseline(tmp_path):
    receiver = snapshot_receiver(tmp_path)
    receiver.save()
    for verb in ("content-discover", "content-discover-changed"):
        result = receiver.invoke(verb, "--source", "forge")
        assert result.returncode == 0
        assert json.loads(result.stdout)["entries"][0]["path"] == "old.md"
    assert receiver.invoke("content-discover", "--source", "forge", "--typo").returncode == 2
    receiver.config["new"] = False
    receiver.config["snapshot_cli"] = False
    receiver.config["metadata"].pop("source_delta")
    connector = receiver.connector()
    assert connector.discover_snapshot_page is None
    assert connector.discover()[0].source == "forge"
    assert "--snapshot-version" not in receiver.calls()[-1]


def test_snapshot_capability_cannot_replace_the_existing_scope_contract(tmp_path):
    receiver = snapshot_receiver(tmp_path)
    receiver.config["metadata"]["source_delta"]["versions"] = [2]
    connector = receiver.connector()
    assert connector.discover_snapshot_page is None
    assert connector.source_name == "forge"
    assert connector.discover()[0].source == FAMILY
    assert receiver.calls()[-1] == ["content-discover", "--source", "forge"]


def test_ordinary_delta_does_not_invoke_advertised_full_snapshot(tmp_path):
    receiver = snapshot_receiver(tmp_path)
    connector = receiver.connector()
    manifest = collect_source_changes(
        "alias", state=IndexState(), path_index=PathIndex(tmp_path / "paths.db"),
        full=False, source_aware=True, connector=connector,
    )
    assert manifest.commit == "delta-tip"
    assert receiver.calls() == [
        ["content-current-commit", "--source", "forge"],
        ["content-discover-changed", "--source", "forge", "--delta-version", "1"],
    ]


@pytest.mark.parametrize("snapshot,cursor", [
    ("immutable-generation", "history"), ("-snapshot", "--cursor-value"),
])
def test_complete_snapshot_restarts_at_acknowledged_page_without_store_effects(
    tmp_path, snapshot, cursor,
):
    receiver = snapshot_receiver(tmp_path)
    pages = receiver.config["snapshot_pages"]
    pages["first"]["snapshot"] = snapshot
    pages["first"]["cursor"] = cursor
    pages[cursor] = {**pages.pop("history"), "snapshot": snapshot}
    receiver.config["fail_snapshot"] = True
    connector = receiver.connector()
    paths = PathIndex(tmp_path / "paths.db")
    paths.mark_indexed(FAMILY, "gone.md", chunk_count=1)
    paths.mark_indexed(FAMILY + ":commits", "old-history", chunk_count=1)
    paths.mark_indexed("other:repo", "untouched.md", chunk_count=1)
    state = IndexState(sources={"alias": SourceState(last_commit="base")})
    state.sources["alias"].acquisition_scope = recovery_scope("alias", connector)
    state.stored_families["alias"] = FAMILY

    def collect(connector):
        return collect_source_changes(
            "alias", state=state, path_index=paths, full=True, source_aware=True,
            connector=connector,
        )

    with pytest.raises(ProviderError, match="temporarily unavailable"):
        collect(connector)
    scope = recovery_scope("alias", connector)
    pending = PendingCrawls(paths).read_payload("alias", scope)
    assert json.loads(pending)["cursor"] == cursor
    assert state.sources["alias"].last_commit == "base"
    assert paths.count() == 3
    receiver.config["fail_snapshot"] = False
    receiver.config["metadata"]["commit"] = "later-metadata-tip"
    paths = PathIndex(tmp_path / "paths.db")
    manifest = collect(receiver.connector())
    assert manifest.commit == "snapshot-tip"
    assert [(entry.source, entry.path) for entry in manifest.upserts] == [
        (FAMILY, "new.md"), (FAMILY + ":commits", "history"),
    ]
    assert {(entry.source, entry.path) for entry in manifest.deletions} == {
        (FAMILY, "gone.md"), (FAMILY + ":commits", "old-history"),
    }
    assert paths.count() == 3
    assert state.sources["alias"].last_commit == "base"
    assert PendingCrawls(paths).load("alias", scope) == (manifest, True)
    assert receiver.calls() == [
        ["content-current-commit", "--source", "forge"],
        ["content-discover", "--source", "forge", "--snapshot-version", "1"],
        ["content-discover", "--source", "forge", "--snapshot-version", "1",
         f"--snapshot={snapshot}", f"--cursor={cursor}"],
        ["content-current-commit", "--source", "forge"],
        ["content-discover", "--source", "forge", "--snapshot-version", "1",
         f"--snapshot={snapshot}", f"--cursor={cursor}"],
    ]


@pytest.mark.parametrize("field,value", [
    ("version", True), ("version", 2), ("snapshot", ""), ("snapshot", None),
    ("snapshot", "bad\0snapshot"), ("cursor", "bad\0cursor"), ("commit", "bad\0commit"),
    ("cursor", ""), ("cursor", 4), ("commit", 4), ("upserts", None),
    ("upserts", [ENTRY] * 257), ("upserts", [ENTRY, ENTRY]),
    ("upserts", [{**ENTRY, "source": "other:repo"}]),
])
def test_invalid_snapshot_pages_never_reach_journal(tmp_path, field, value):
    receiver = snapshot_receiver(tmp_path)
    receiver.config["snapshot_pages"]["first"][field] = value
    connector = receiver.connector()
    paths = PathIndex(tmp_path / "paths.db")
    with pytest.raises(ProviderError):
        collect_source_changes(
            "alias", state=IndexState(), path_index=paths, full=True,
            source_aware=True, connector=connector,
        )
    assert PendingCrawls(paths).sources() == []
    assert paths.count() == 0


@pytest.mark.parametrize("snapshot,cursor", [("bad\0snapshot", "next"), ("pinned", "bad\0cursor")])
def test_unrepresentable_request_tokens_refuse_before_provider_call(tmp_path, snapshot, cursor):
    receiver = snapshot_receiver(tmp_path)
    discover = receiver.connector().discover_snapshot_page
    with pytest.raises(ValueError, match="NUL"):
        discover(snapshot, cursor)
    assert receiver.calls() == [["content-current-commit", "--source", "forge"]]


@pytest.mark.parametrize("field", ["version", "snapshot", "upserts", "commit", "cursor"])
def test_snapshot_fields_are_required_even_when_null_is_valid(tmp_path, field):
    receiver = snapshot_receiver(tmp_path)
    receiver.config["snapshot_pages"]["first"].pop(field)
    connector = receiver.connector()
    discover = connector.discover_snapshot_page
    with pytest.raises(ProviderError, match="requires"):
        discover(None, None)


@pytest.mark.parametrize("echo", [
    None, "missing", DECLARATION, {**DECLARATION, "snapshot_versions": [2]},
    {**DECLARATION, "snapshot_versions": [1], "account": "other"},
    {**DECLARATION, "snapshot_versions": [1], "target": "other"},
    {**DECLARATION, "snapshot_versions": [1], "family": FAMILY + ":other"},
])
def test_snapshot_scope_and_capability_echo_are_enforced(tmp_path, echo):
    receiver = snapshot_receiver(tmp_path)
    if echo == "missing":
        receiver.config["echo_snapshot_scope"] = False
    else:
        receiver.config["snapshot_scope"] = echo
    discover = receiver.connector().discover_snapshot_page
    with pytest.raises(ProviderError, match="scope echo|source_delta"):
        discover(None, None)


@pytest.mark.parametrize("change", ["snapshot", "commit", "cursor", "capability"])
def test_continuation_drift_preserves_last_acknowledged_page(tmp_path, change):
    receiver = snapshot_receiver(tmp_path)
    receiver.config["fail_snapshot"] = True
    connector = receiver.connector()
    paths = PathIndex(tmp_path / "paths.db")
    scope = recovery_scope("alias", connector)

    def collect(connector):
        return collect_source_changes(
            "alias", state=IndexState(), path_index=paths, full=True,
            source_aware=True, connector=connector,
        )

    with pytest.raises(ProviderError):
        collect(connector)
    before = PendingCrawls(paths).read_payload("alias", scope)
    receiver.config["fail_snapshot"] = False
    if change == "capability":
        receiver.config["metadata"]["source_delta"].pop("snapshot_versions")
    else:
        receiver.config["snapshot_pages"]["history"][change] = (
            "history" if change == "cursor" else "different"
        )
    with pytest.raises(ValueError, match="snapshot|progress|mode"):
        collect(receiver.connector())
    assert PendingCrawls(paths).read_payload("alias", scope) == before
    assert paths.count() == 0
