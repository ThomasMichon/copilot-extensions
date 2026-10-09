"""Source-root layouts and producer identity, independent of archive placement."""

from __future__ import annotations

import gzip
import json
import tarfile
import zipfile
from pathlib import Path

import pytest

from agent_logger import sessions, source_roots
from agent_logger.source_roots import (
    SOURCE_METADATA_MEMBER,
    SourceIdentity,
    SourceLayoutError,
    iter_archive_sources,
    validate_source_key,
)

pytestmark = pytest.mark.contract("agent_logger.archive_source_discovery")


def _session(root: Path, session_id: str = "session-1") -> Path:
    path = root / "session-state" / session_id
    path.mkdir(parents=True)
    (path / "events.jsonl").write_text('{"type":"session.start"}\n', encoding="utf-8")
    return path


def _metadata(root: Path, identity: SourceIdentity, aliases: tuple[str, ...] = ()) -> None:
    data = {"schema_version": 1, **identity.to_dict(), "legacy_aliases": list(aliases)}
    (root / SOURCE_METADATA_MEMBER).write_text(json.dumps(data), encoding="utf-8")


def test_flat_legacy_and_qualified_sources_share_leaf_readers(tmp_path):
    keys = (
        "desktop",
        "container-worker-1",
        ".codespaces/old-box",
        "host-a.containers/worker-1",
        "host-b.containers/worker-1",
        "repo.codespaces/new-box",
    )
    for key in keys:
        _session(tmp_path / key)
    sources = list(iter_archive_sources(tmp_path))
    assert {source.key for source in sources} == set(keys)
    assert all(source.identity is None for source in sources)
    assert all([ref.id for ref in source.iter_sessions()] == ["session-1"] for source in sources)
    assert {source.key for source in sources if source.layout == "container"} == {
        "host-a.containers/worker-1",
        "host-b.containers/worker-1",
    }
    assert all(source.path.is_absolute() for source in sources)


def test_source_identity_is_independent_of_physical_alias(tmp_path):
    identity = SourceIdentity(
        "container", "agent-containers", host="host-a", venue_name="worker-1"
    )
    aliases = ("container-worker-1",)
    for key in (aliases[0], identity.namespace):
        root = tmp_path / key
        _session(root)
        _metadata(root, identity, aliases)
    sources = list(iter_archive_sources(tmp_path))
    assert len(sources) == 2
    assert {source.identity.source_id for source in sources} == {identity.source_id}
    assert all(source.legacy_aliases == aliases for source in sources)
    assert len({source.key for source in sources}) == 2


def test_same_short_repo_group_retains_full_provider_repository_identity(tmp_path):
    first = SourceIdentity("codespace", "github", repository="owner-a/repo", venue_name="box-a")
    second = SourceIdentity("codespace", "github", repository="owner-b/repo", venue_name="box-b")
    for identity in (first, second):
        root = tmp_path / identity.namespace
        _session(root)
        _metadata(root, identity)
    sources = list(iter_archive_sources(tmp_path))
    assert {source.key for source in sources} == {"repo.codespaces/box-a", "repo.codespaces/box-b"}
    assert {source.identity.repository for source in sources} == {"owner-a/repo", "owner-b/repo"}
    assert first.source_id != second.source_id
    assert (
        first.source_id
        != SourceIdentity(
            "codespace", "other-provider", repository="owner-a/repo", venue_name="box-a"
        ).source_id
    )


@pytest.mark.parametrize("repository", ["owner/.github", "owner/_repo", "owner/-repo"])
def test_portable_repository_prefixes_are_not_lossy_slugged_or_hidden(tmp_path, repository):
    identity = SourceIdentity("codespace", "github", repository=repository, venue_name="box")
    root = tmp_path / identity.namespace
    _session(root)
    _metadata(root, identity)
    source = next(iter_archive_sources(tmp_path))
    assert source.key == identity.namespace
    assert source.identity.repository == repository


def test_live_preference_and_cold_archives_use_existing_codec(tmp_path):
    root = tmp_path / "repo.codespaces/box"
    live = _session(root)
    archived = root / "archived"
    archived.mkdir()
    with tarfile.open(archived / "session-1.tar.gz", "w:gz") as archive:
        archive.add(live / "events.jsonl", arcname="events.jsonl")
    cold = tmp_path / "cold-input"
    cold.mkdir()
    (cold / "events.jsonl").write_text('{"type":"session.start"}\n')
    with tarfile.open(archived / "cold-session.tar.gz", "w:gz") as archive:
        archive.add(cold / "events.jsonl", arcname="events.jsonl")
    source = next(s for s in iter_archive_sources(tmp_path) if s.key == "repo.codespaces/box")
    refs = list(source.iter_sessions())
    assert {(ref.id, ref.kind) for ref in refs} == {
        ("session-1", "live"),
        ("cold-session", "archive"),
    }
    assert sessions.read_member(next(r for r in refs if r.kind == "archive"), "events.jsonl")


def test_process_log_representations_delegate_without_coalescing(tmp_path):
    logs = tmp_path / "host.containers/worker/logs"
    logs.mkdir(parents=True)
    content = b'{"metric":1}\n'
    (logs / "process-1.log").write_bytes(content)
    with gzip.open(logs / "process-1.log.gz", "wb") as stream:
        stream.write(content)
    with zipfile.ZipFile(logs / "old.zip", "w") as archive:
        archive.writestr("process-1.log", content)
    source = next(iter_archive_sources(tmp_path))
    refs = list(source.iter_process_logs())
    assert len(refs) == 3
    assert {ref.logical_name for ref in refs} == {"process-1.log"}
    assert all(list(ref.iter_lines()) == ['{"metric":1}\n'] for ref in refs)


def test_chronicle_keeps_full_provider_source_keys(tmp_path):
    from agent_logger.chronicle.source import ReservationStore, SyncedSessionSource

    corpus = tmp_path / "corpus"
    keys = (
        "host-a.containers/worker",
        "host-b.containers/worker",
        ".codespaces/old-box",
        "repo.codespaces/new-box",
        "desktop",
    )
    for index, key in enumerate(keys):
        directory = _session(corpus / key, f"session-{index}")
        (directory / "workspace.yaml").write_text(
            f"id: session-{index}\nrepository: owner/repo\ncreated_at: 2026-01-01T00:00:00Z\n",
            encoding="utf-8",
        )
    source = SyncedSessionSource(
        corpus,
        ReservationStore(tmp_path / "state.db"),
        settle_seconds=0,
    )
    found = source.scan()
    assert len(found) == len(keys)
    assert {record.machine for record in found} == set(keys)


def test_relative_root_does_not_follow_later_working_directory(tmp_path, monkeypatch):
    _session(tmp_path / "corpus/host")
    monkeypatch.chdir(tmp_path)
    source = next(iter_archive_sources(Path("corpus")))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert [ref.id for ref in source.iter_sessions()] == ["session-1"]


@pytest.mark.parametrize(
    "key",
    [
        "../outside",
        "host/worker",
        "host.containers/a/b",
        "host.containers/",
        "/host",
        "C:/host",
        r"host.containers\worker",
        "host.",
        "CON",
        "repo.codespaces/../worker",
        "host.containers",
        "repo.codespaces",
        ".hidden",
        "host.containers/.",
        "host.containers/..",
        ".codespaces/.",
        ".codespaces/..",
        "repo.codespaces/.",
        "repo.codespaces/..",
        "..containers/worker",
        "...containers/worker",
    ],
)
def test_unsafe_or_ambiguous_namespace_is_rejected(key):
    with pytest.raises(SourceLayoutError):
        validate_source_key(key)


@pytest.mark.parametrize("dot", [".", ".."])
@pytest.mark.parametrize(
    "field", ["provider", "host", "venue_name", "repository_owner", "repository_name"]
)
def test_dot_components_are_rejected_in_source_identities(dot: str, field: str) -> None:
    with pytest.raises(SourceLayoutError):
        if field == "host":
            SourceIdentity("container", "p", host=dot, venue_name="worker")
        else:
            SourceIdentity(
                "codespace",
                dot if field == "provider" else "p",
                repository=(
                    f"{dot}/repo"
                    if field == "repository_owner"
                    else f"owner/{dot}"
                    if field == "repository_name"
                    else "owner/repo"
                ),
                venue_name=dot if field == "venue_name" else "box",
            )


@pytest.mark.parametrize(
    "data",
    [
        [],
        {"schema_version": True},
        {"schema_version": 2},
        {"schema_version": 1, "venue_kind": "container", "provider": "p"},
        {
            "schema_version": 1,
            "venue_kind": "codespace",
            "provider": "p",
            "repository": "repo",
            "venue_name": "box",
        },
    ],
)
def test_invalid_recorded_identity_is_not_an_unknown_success(tmp_path, data):
    root = tmp_path / "host.containers/worker"
    root.mkdir(parents=True)
    (root / SOURCE_METADATA_MEMBER).write_text(json.dumps(data))
    with pytest.raises(SourceLayoutError):
        list(iter_archive_sources(tmp_path))


def test_group_identity_mismatch_and_bad_aliases_fail_explicitly(tmp_path):
    root = tmp_path / "host-a.containers/worker"
    root.mkdir(parents=True)
    _metadata(root, SourceIdentity("container", "p", host="host-b", venue_name="worker"))
    with pytest.raises(SourceLayoutError, match="disagrees"):
        list(iter_archive_sources(tmp_path))
    _metadata(
        root, SourceIdentity("container", "p", host="host-a", venue_name="worker"), ("../outside",)
    )
    with pytest.raises(SourceLayoutError):
        list(iter_archive_sources(tmp_path))


def test_legacy_source_requires_explicit_physical_alias_and_rechecks_metadata(tmp_path):
    root = tmp_path / ".codespaces/old-box"
    root.mkdir(parents=True)
    identity = SourceIdentity("codespace", "github", repository="owner/repo", venue_name="box")
    _metadata(root, identity)
    with pytest.raises(SourceLayoutError, match="namespace"):
        list(iter_archive_sources(tmp_path))
    _metadata(root, identity, (".codespaces/old-box",))
    source = next(iter_archive_sources(tmp_path))
    _metadata(
        root,
        SourceIdentity("codespace", "github", repository="other-owner/repo", venue_name="box"),
        (".codespaces/old-box",),
    )
    with pytest.raises(SourceLayoutError, match="metadata changed"):
        list(source.iter_sessions())


def test_directory_change_and_optional_non_directory_are_not_silently_empty(tmp_path):
    root = tmp_path / "host.containers/worker"
    root.mkdir(parents=True)
    source = next(iter_archive_sources(tmp_path))
    root.rename(root.with_name("old"))
    root.mkdir()
    with pytest.raises(SourceLayoutError, match="changed"):
        list(source.iter_sessions())
    source = next(s for s in iter_archive_sources(tmp_path) if s.path == root)
    (root / "logs").write_text("not a directory")
    with pytest.raises(SourceLayoutError):
        list(source.iter_process_logs())


def test_symlinked_source_or_metadata_is_rejected(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    try:
        (corpus / "host").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"native symlink creation unavailable: {exc}")
    with pytest.raises(SourceLayoutError):
        list(iter_archive_sources(corpus))
    (corpus / "host").unlink()
    root = corpus / "host"
    root.mkdir()
    target = outside / "metadata.json"
    target.write_text("{}")
    (root / SOURCE_METADATA_MEMBER).symlink_to(target)
    with pytest.raises(SourceLayoutError, match="not regular"):
        list(iter_archive_sources(corpus))


@pytest.mark.parametrize("representation", ["session", "events", "archive"])
def test_leaf_reader_rejects_linked_session_observations(tmp_path, representation):
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "events.jsonl"
    target.write_text('{"type":"session.start"}\n')
    corpus = tmp_path / "corpus"
    root = corpus / "host"
    state = root / "session-state"
    state.mkdir(parents=True)
    try:
        if representation == "session":
            (state / "session-1").symlink_to(outside, target_is_directory=True)
        elif representation == "events":
            session = state / "session-1"
            session.mkdir()
            (session / "events.jsonl").symlink_to(target)
        else:
            archived = root / "archived"
            archived.mkdir()
            (archived / "session-1.tar.gz").symlink_to(target)
    except OSError as exc:
        pytest.skip(f"native symlink creation unavailable: {exc}")
    source = next(iter_archive_sources(corpus))
    with pytest.raises(SourceLayoutError):
        list(source.iter_sessions())


def test_corrupt_session_archive_surfaces_when_consumed(tmp_path):
    archived = tmp_path / "host/archived"
    archived.mkdir(parents=True)
    (archived / "session-1.tar.gz").write_bytes(b"invalid archive")
    source = next(iter_archive_sources(tmp_path))
    ref = next(source.iter_sessions())
    with pytest.raises(tarfile.ReadError):
        sessions.read_member(ref, "events.jsonl")


@pytest.mark.parametrize("kind", ["live", "archive"])
@pytest.mark.parametrize("member", sessions.SIDECAR_MEMBERS)
@pytest.mark.parametrize("representation", ["symlink", "broken-symlink", "directory"])
def test_leaf_reader_rejects_unsafe_session_sidecars(
    tmp_path: Path, kind: str, member: str, representation: str
) -> None:
    corpus = tmp_path / "corpus"
    root = corpus / "host"
    if kind == "live":
        directory = _session(root)
        sidecar = directory / member
    else:
        directory = root / "archived"
        directory.mkdir(parents=True)
        with tarfile.open(directory / "session-1.tar.gz", "w:gz"):
            pass
        sidecar = directory / f"session-1.{member}"
    target = tmp_path / "outside"
    if representation == "directory":
        sidecar.mkdir()
    else:
        if representation == "symlink":
            target.write_bytes(b"outside-corpus")
        try:
            sidecar.symlink_to(target)
        except OSError as exc:
            pytest.skip(f"native symlink creation unavailable: {exc}")
    source = next(iter_archive_sources(corpus))
    with pytest.raises(SourceLayoutError, match="not regular"):
        list(source.iter_sessions())


@pytest.mark.parametrize("kind", ["live", "archive"])
def test_leaf_reader_preserves_regular_and_absent_sidecars(tmp_path: Path, kind: str) -> None:
    root = tmp_path / "host"
    if kind == "live":
        directory = _session(root)
        sidecars = [directory / member for member in sessions.SIDECAR_MEMBERS]
    else:
        directory = root / "archived"
        directory.mkdir(parents=True)
        with tarfile.open(directory / "session-1.tar.gz", "w:gz"):
            pass
        sidecars = [directory / f"session-1.{member}" for member in sessions.SIDECAR_MEMBERS]
    source = next(iter_archive_sources(tmp_path))
    assert len(list(source.iter_sessions())) == 1
    for member, path in zip(sessions.SIDECAR_MEMBERS, sidecars, strict=True):
        path.write_bytes(member.encode())
    ref = next(source.iter_sessions())
    for member in sessions.SIDECAR_MEMBERS:
        assert sessions.read_member(ref, member) == member.encode()


@pytest.mark.parametrize("member", sessions.SIDECAR_MEMBERS)
@pytest.mark.parametrize("representation", ["symlink", "broken-symlink", "directory", "reparse"])
def test_chronicle_rejects_unsafe_archived_sidecars_before_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, member: str, representation: str
) -> None:
    from agent_logger.chronicle.source import ReservationStore, SyncedSessionSource

    corpus = tmp_path / "corpus"
    store = corpus / "repo.codespaces/box/archived"
    store.mkdir(parents=True)
    with tarfile.open(store / "session-1.tar.gz", "w:gz"):
        pass
    sidecar = store / f"session-1.{member}"
    outside = tmp_path / "outside"
    if representation == "directory":
        sidecar.mkdir()
    elif representation == "reparse":
        sidecar.write_bytes(b"outside-corpus")
        real_is_link = source_roots.is_link_or_reparse
        monkeypatch.setattr(
            source_roots,
            "is_link_or_reparse",
            lambda path, mode: path == sidecar or real_is_link(path, mode),
        )
    else:
        if representation == "symlink":
            outside.write_bytes(b"outside-corpus")
        try:
            sidecar.symlink_to(outside)
        except OSError as exc:
            pytest.skip(f"native symlink creation unavailable: {exc}")

    def unexpected_read(*args: object, **kwargs: object) -> None:
        pytest.fail("archive bytes or sidecars read before no-link validation")

    monkeypatch.setattr(sessions, "verify_archive", unexpected_read)
    monkeypatch.setattr(sessions, "read_workspace", unexpected_read)
    monkeypatch.setattr(sessions, "read_origin", unexpected_read)
    source = SyncedSessionSource(corpus, ReservationStore(tmp_path / "state.db"))
    with pytest.raises(SourceLayoutError, match="not regular"):
        source.scan()


@pytest.mark.parametrize("representation", ["session", "events"])
def test_chronicle_rejects_linked_live_ref_before_it_can_shadow_archive(
    tmp_path: Path, representation: str
) -> None:
    from agent_logger.chronicle.source import ReservationStore, SyncedSessionSource

    outside = tmp_path / "outside"
    outside.mkdir()
    events = outside / "events.jsonl"
    events.write_bytes(b'{"type":"session.start"}\n')
    corpus = tmp_path / "corpus"
    root = corpus / "host.containers/worker"
    state = root / "session-state"
    state.mkdir(parents=True)
    archived = root / "archived"
    archived.mkdir()
    with tarfile.open(archived / "session-1.tar.gz", "w:gz") as archive:
        archive.add(events, arcname="events.jsonl")
    try:
        if representation == "session":
            (state / "session-1").symlink_to(outside, target_is_directory=True)
        else:
            directory = state / "session-1"
            directory.mkdir()
            (directory / "events.jsonl").symlink_to(events)
    except OSError as exc:
        pytest.skip(f"native symlink creation unavailable: {exc}")
    source = SyncedSessionSource(corpus, ReservationStore(tmp_path / "state.db"))
    with pytest.raises(SourceLayoutError):
        source.scan()


def test_missing_and_permission_denied_roots_remain_explicit(tmp_path, monkeypatch):
    with pytest.raises(SourceLayoutError, match="missing"):
        list(iter_archive_sources(tmp_path / "absent"))

    def denied(_path):
        raise PermissionError("denied")

    monkeypatch.setattr(source_roots, "existing_real_directory", denied)
    with pytest.raises(PermissionError, match="denied"):
        list(iter_archive_sources(tmp_path))


def test_metadata_size_source_budget_and_casefold_collisions(tmp_path, monkeypatch):
    root = tmp_path / "host"
    root.mkdir()
    (root / SOURCE_METADATA_MEMBER).write_bytes(b" " * 100)
    monkeypatch.setattr(source_roots, "MAX_SOURCE_METADATA_BYTES", 99)
    with pytest.raises(SourceLayoutError, match="byte budget"):
        list(iter_archive_sources(tmp_path))
    (root / SOURCE_METADATA_MEMBER).unlink()
    monkeypatch.setattr(source_roots, "MAX_SOURCE_ENTRIES", 1)
    (tmp_path / "other").mkdir()
    with pytest.raises(SourceLayoutError, match="entry budget"):
        list(iter_archive_sources(tmp_path))
    monkeypatch.setattr(source_roots, "MAX_SOURCE_ENTRIES", 100)
    monkeypatch.setattr(source_roots, "_CASE_INSENSITIVE", True)
    if (tmp_path / "Host").exists():
        pytest.skip("case-insensitive filesystem cannot create distinct case aliases")
    (tmp_path / "Host").mkdir()
    with pytest.raises(SourceLayoutError, match="collision"):
        list(iter_archive_sources(tmp_path))
