"""Canonical namespace admission does not silently adopt unknown archive bytes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_logger import source_publication
from agent_logger.source_publication import (
    admit_publication_source,
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


def test_admission_creates_discoverable_idempotent_source(tmp_path: Path) -> None:
    identity = _identity()
    path = admit_publication_source(tmp_path, identity.namespace, identity)
    marker = path / SOURCE_METADATA_MEMBER
    before = marker.read_bytes()
    (path / "session-state").mkdir()
    assert admit_publication_source(tmp_path, identity.namespace, identity) == path
    assert marker.read_bytes() == before
    source = next(iter_archive_sources(tmp_path))
    assert source.identity == identity
    assert source.legacy_aliases == ()
    assert load_source_identity_file(marker) == identity


def test_same_container_name_on_two_hosts_has_distinct_admission(tmp_path: Path) -> None:
    first, second = _identity("host-a"), _identity("host-b")
    first_path = admit_publication_source(tmp_path, first.namespace, first)
    second_path = admit_publication_source(tmp_path, second.namespace, second)
    assert first_path != second_path
    assert {source.identity.source_id for source in iter_archive_sources(tmp_path)} == {
        first.source_id,
        second.source_id,
    }


def test_same_short_repository_and_venue_cannot_replace_owner(tmp_path: Path) -> None:
    first = SourceIdentity("codespace", "github", repository="owner-a/repo", venue_name="box")
    second = SourceIdentity("codespace", "github", repository="owner-b/repo", venue_name="box")
    path = admit_publication_source(tmp_path, first.namespace, first)
    before = (path / SOURCE_METADATA_MEMBER).read_bytes()
    with pytest.raises(SourceLayoutError, match="identity collision"):
        admit_publication_source(tmp_path, second.namespace, second)
    assert (path / SOURCE_METADATA_MEMBER).read_bytes() == before


@pytest.mark.parametrize(
    "key", ["legacy-worker", ".codespaces/worker", "host-b.containers/worker"]
)
def test_new_publication_requires_canonical_identity_key(key: str) -> None:
    with pytest.raises(SourceLayoutError, match="canonical"):
        validate_publication_key(key, _identity())


def test_unknown_nonempty_destination_is_not_adopted(tmp_path: Path) -> None:
    identity = _identity()
    path = tmp_path / identity.namespace
    path.mkdir(parents=True)
    (path / "session-state").mkdir()
    with pytest.raises(SourceLayoutError, match="nonempty"):
        admit_publication_source(tmp_path, identity.namespace, identity)
    assert not (path / SOURCE_METADATA_MEMBER).exists()


def test_existing_aliases_are_preserved_not_rewritten(tmp_path: Path) -> None:
    identity = _identity()
    path = admit_publication_source(tmp_path, identity.namespace, identity)
    marker = path / SOURCE_METADATA_MEMBER
    data = json.loads(marker.read_bytes())
    data["legacy_aliases"] = ["legacy-worker"]
    marker.write_text(json.dumps(data), encoding="utf-8")
    before = marker.read_bytes()
    admit_publication_source(tmp_path, identity.namespace, identity)
    assert marker.read_bytes() == before
    assert next(iter_archive_sources(tmp_path)).legacy_aliases == ("legacy-worker",)


@pytest.mark.parametrize("member", ["group", "leaf", "marker"])
def test_linked_destination_is_rejected(tmp_path: Path, member: str) -> None:
    identity = _identity()
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    group = corpus / "host-a.containers"
    try:
        if member == "group":
            group.symlink_to(outside, target_is_directory=True)
        else:
            group.mkdir()
            leaf = group / "worker"
            if member == "leaf":
                leaf.symlink_to(outside, target_is_directory=True)
            else:
                leaf.mkdir()
                target = outside / "marker"
                target.write_text("{}")
                (leaf / SOURCE_METADATA_MEMBER).symlink_to(target)
    except OSError as exc:
        pytest.skip(f"native symlink creation unavailable: {exc}")
    with pytest.raises((SourceLayoutError, OSError)):
        admit_publication_source(corpus, identity.namespace, identity)
    assert not (outside / SOURCE_METADATA_MEMBER).exists()


def test_case_conflicting_component_is_explicit(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "Host-A.containers").mkdir()
    monkeypatch.setattr(source_publication, "_CASE_INSENSITIVE", True)
    with pytest.raises(SourceLayoutError, match="case-conflicting"):
        admit_publication_source(tmp_path, _identity().namespace, _identity())


@pytest.mark.parametrize("data", [{}, {"schema_version": True}, {"schema_version": 9}])
def test_identity_file_uses_shared_metadata_validation(tmp_path: Path, data: dict) -> None:
    marker = tmp_path / "identity.json"
    marker.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(SourceLayoutError):
        load_source_identity_file(marker)


def test_missing_identity_file_is_not_unknown_success(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_source_identity_file(tmp_path / "missing.json")
