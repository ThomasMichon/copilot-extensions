"""Dynamic codec lookup and read-only extension contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_logger import sessions
from agent_logger.segmenter import collate


class ReadOnlyCodec(sessions.Codec):
    name = "readonly-long"
    suffix = ".long.tar.gz"

    def read_member(self, archive: Path, member: str) -> bytes | None:
        if member == sessions.EVENTS_MEMBER:
            return b'{"type":"session.start"}\n'
        return None

    def extract_all(self, archive: Path, dest_dir: Path) -> None:
        (dest_dir / sessions.EVENTS_MEMBER).write_bytes(b'{"type":"session.start"}\n')

    def list_members(self, archive: Path) -> list[str]:
        return [sessions.EVENTS_MEMBER]


def test_dynamic_longest_suffix_discovery_and_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = tmp_path / "archived"
    store.mkdir()
    archive = store / "session.long.tar.gz"
    archive.write_bytes(b"custom container")
    codec = ReadOnlyCodec()
    monkeypatch.setitem(sessions.CODECS, codec.name, codec)

    refs = list(sessions.iter_session_refs(None, store))
    assert refs == [sessions.SessionRef("session", "archive", archive, store)]
    ref = refs[0]
    assert sessions.resolve_ref("session", tmp_path / "missing", store) == ref
    assert sessions.resolve_ref("session.long", tmp_path / "missing", store) is None
    assert sessions.codec_for_archive(archive) is codec
    assert sessions._codec_for_archive(archive) is codec
    assert sessions.archive_stem(archive) == "session"
    assert sessions.read_member(ref, sessions.EVENTS_MEMBER) == (b'{"type":"session.start"}\n')
    assert sessions.member_exists(ref, sessions.EVENTS_MEMBER)
    assert sessions.verify_archive(ref)
    with sessions.materialize(ref) as directory:
        assert (directory / sessions.EVENTS_MEMBER).is_file()


def test_registry_changes_update_legacy_view_but_not_prior_snapshots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy_view = sessions._ARCHIVE_SUFFIXES
    snapshot = sessions.archive_suffixes()
    codec = ReadOnlyCodec()
    monkeypatch.setitem(sessions.CODECS, codec.name, codec)
    assert next(iter(legacy_view)) == codec.suffix
    assert codec.suffix not in snapshot

    replacement = ReadOnlyCodec()
    replacement.suffix = ".custom"
    monkeypatch.setitem(sessions.CODECS, codec.name, replacement)
    assert codec.suffix not in tuple(legacy_view)
    assert ".custom" in sessions.archive_suffixes()
    assert sessions.codec_for_archive(Path("session.custom")) is replacement

    monkeypatch.delitem(sessions.CODECS, codec.name)
    assert ".custom" not in tuple(legacy_view)
    assert sessions.archive_stem(Path("session.custom")) is None
    with pytest.raises(ValueError, match="no codec for archive"):
        sessions.codec_for_archive(Path("session.custom"))


def test_empty_and_duplicate_suffixes_do_not_create_extra_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codec = ReadOnlyCodec()
    monkeypatch.setitem(sessions.CODECS, codec.name, codec)
    monkeypatch.setitem(sessions.CODECS, "alias", codec)
    empty = ReadOnlyCodec()
    empty.suffix = ""
    monkeypatch.setitem(sessions.CODECS, "empty", empty)
    assert sessions.archive_suffixes().count(codec.suffix) == 1
    assert "" not in sessions.archive_suffixes()
    assert sessions.archive_stem(Path("unrecognized.txt")) is None
    store = tmp_path / "archived"
    store.mkdir()
    archive = store / "session.long.tar.gz"
    archive.write_bytes(b"custom container")
    assert len(list(sessions.iter_session_refs(None, store))) == 1


def test_read_only_codec_rejects_archive_writes_without_modifying_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codec = ReadOnlyCodec()
    monkeypatch.setitem(sessions.CODECS, codec.name, codec)
    source = tmp_path / "session"
    source.mkdir()
    events = source / sessions.EVENTS_MEMBER
    events.write_bytes(b"source evidence")
    store = tmp_path / "archived"
    with pytest.raises(ValueError, match="is read-only"):
        sessions.archive_session(source, store, codec=codec.name)
    assert events.read_bytes() == b"source evidence"
    assert list(store.iterdir()) == []


def test_read_only_codec_cannot_prove_overlapping_representations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codec = ReadOnlyCodec()
    monkeypatch.setitem(sessions.CODECS, codec.name, codec)
    source = tmp_path / "live" / "session"
    source.mkdir(parents=True)
    (source / sessions.EVENTS_MEMBER).write_bytes(b'{"type":"session.start"}\n')
    store = tmp_path / "archived"
    sessions.archive_session(source, store)
    (store / f"session{codec.suffix}").write_bytes(b"custom container")
    with pytest.raises(ValueError, match="cannot compare archive representations"):
        list(sessions.iter_session_refs(None, store))
    assert not sessions.verify_archive(
        sessions.SessionRef("session", "archive", store / "session.tar.gz", store)
    )
    assert (source / sessions.EVENTS_MEMBER).is_file()


def test_collate_resolves_registered_archive_after_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codec = ReadOnlyCodec()
    monkeypatch.setitem(sessions.CODECS, codec.name, codec)
    archive = tmp_path / "session.long.tar.gz"
    archive.write_bytes(b"custom container")
    directory = collate.resolve_session_dir(str(archive))
    assert (directory / sessions.EVENTS_MEMBER).read_bytes() == (b'{"type":"session.start"}\n')
