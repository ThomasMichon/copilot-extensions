"""Producer identity loading and canonical write keys reuse the reader contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_logger.source_publication import (
    load_source_identity_file,
    validate_publication_key,
)
from agent_logger.source_roots import (
    SOURCE_METADATA_MEMBER,
    SourceIdentity,
    SourceLayoutError,
    iter_archive_sources,
)

pytestmark = pytest.mark.contract("agent_logger.archive_source_publication")


def _identity(host: str = "host-a") -> SourceIdentity:
    return SourceIdentity("container", "agent-containers", host=host, venue_name="worker")


def test_identity_file_and_discovery_use_the_same_contract(tmp_path: Path) -> None:
    identity = _identity()
    path = tmp_path / identity.namespace
    path.mkdir(parents=True)
    marker = path / SOURCE_METADATA_MEMBER
    marker.write_text(
        json.dumps({"schema_version": 1, **identity.to_dict(), "legacy_aliases": []}),
        encoding="utf-8",
    )
    before = marker.read_bytes()
    source = next(iter_archive_sources(tmp_path))
    assert source.identity == identity
    assert source.legacy_aliases == ()
    assert load_source_identity_file(marker) == identity
    assert validate_publication_key(identity.namespace, identity) == identity.namespace
    assert marker.read_bytes() == before


def test_same_container_name_on_two_hosts_has_distinct_keys() -> None:
    first, second = _identity("host-a"), _identity("host-b")
    assert validate_publication_key(first.namespace, first) != validate_publication_key(
        second.namespace, second
    )
    assert first.source_id != second.source_id


def test_same_short_repository_retains_full_identity_in_loaded_files(tmp_path: Path) -> None:
    first = SourceIdentity("codespace", "github", repository="owner-a/repo", venue_name="box")
    second = SourceIdentity("codespace", "github", repository="owner-b/repo", venue_name="box")
    loaded = []
    for index, identity in enumerate((first, second)):
        path = tmp_path / f"{index}.json"
        path.write_text(json.dumps({"schema_version": 1, **identity.to_dict()}), encoding="utf-8")
        loaded.append(load_source_identity_file(path))
    assert loaded == [first, second]
    assert first.namespace == second.namespace
    assert first.source_id != second.source_id


@pytest.mark.parametrize(
    "key",
    [
        "legacy-worker",
        ".codespaces/worker",
        "host-b.containers/worker",
        "Host-A.containers/worker",
        "host-a.containers/../worker",
    ],
)
def test_new_publication_requires_canonical_identity_key(key: str) -> None:
    with pytest.raises(SourceLayoutError):
        validate_publication_key(key, _identity())


def test_declared_read_alias_is_not_a_new_write_key(tmp_path: Path) -> None:
    identity = _identity()
    marker = tmp_path / "identity.json"
    data = {"schema_version": 1, **identity.to_dict(), "legacy_aliases": ["legacy-worker"]}
    marker.write_text(json.dumps(data), encoding="utf-8")
    before = marker.read_bytes()
    loaded = load_source_identity_file(marker)
    with pytest.raises(SourceLayoutError):
        validate_publication_key("legacy-worker", loaded)
    assert marker.read_bytes() == before


@pytest.mark.parametrize("member", ["parent", "marker"])
def test_linked_identity_file_is_rejected(tmp_path: Path, member: str) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "identity.json"
    marker.write_text(json.dumps({"schema_version": 1, **_identity().to_dict()}), encoding="utf-8")
    try:
        if member == "parent":
            (corpus / "parent").symlink_to(outside, target_is_directory=True)
            linked = corpus / "parent" / marker.name
        else:
            linked = corpus / marker.name
            linked.symlink_to(marker)
    except OSError as exc:
        pytest.skip(f"native symlink creation unavailable: {exc}")
    with pytest.raises(SourceLayoutError):
        load_source_identity_file(linked)


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"schema_version": True},
        {"schema_version": 9},
        {"schema_version": 1, **_identity().to_dict(), "legacy_aliases": ["../bad"]},
    ],
)
def test_identity_file_uses_shared_metadata_validation(tmp_path: Path, data: dict) -> None:
    marker = tmp_path / "identity.json"
    marker.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(SourceLayoutError):
        load_source_identity_file(marker)


def test_missing_identity_file_is_not_unknown_success(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_source_identity_file(tmp_path / "missing.json")
