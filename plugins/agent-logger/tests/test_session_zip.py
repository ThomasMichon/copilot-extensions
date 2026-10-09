"""ZIP session compatibility, representation identity, and bounded failure."""

from __future__ import annotations

import io
import json
import os
import stat
import struct
import tarfile
import zipfile
import zlib
from contextlib import contextmanager
from pathlib import Path

import pytest

from agent_logger import session_codecs, sessions
from agent_logger.source_roots import iter_archive_sources

pytestmark = pytest.mark.contract("agent_logger.session_zip_compatibility")


def _session(root: Path, session_id: str = "session-1") -> Path:
    path = root / session_id
    (path / "checkpoints").mkdir(parents=True)
    (path / "events.jsonl").write_bytes(b'{"type":"session.start"}\n')
    (path / "workspace.yaml").write_text(f"id: {session_id}\ncwd: /example\n", encoding="utf-8")
    (path / "origin.json").write_text(json.dumps({"source": "test"}), encoding="utf-8")
    (path / "checkpoints" / "index.md").write_bytes(b"# checkpoint\n")
    return path


def _zip(path: Path, members: list[tuple[str | zipfile.ZipInfo, bytes]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, data in members:
            archive.writestr(name, data)


@pytest.mark.parametrize("checkpoint", [b"# checkpoint\n", b"# checkpoint\r\n"])
def test_zip_roundtrip_uses_registered_reader_without_changing_default(
    tmp_path: Path, checkpoint: bytes
) -> None:
    source = _session(tmp_path / "live")
    (source / "checkpoints" / "index.md").write_bytes(checkpoint)
    store = tmp_path / "archived"
    ref = sessions.archive_session(source, store, codec="zip")
    assert ref.path.name == "session-1.zip"
    assert sessions.is_archived(ref.id, store, codec="zip")
    assert sessions.verify_archive(ref)
    assert sessions.get_codec("targz").suffix == ".tar.gz"
    assert sessions.read_member(ref, "events.jsonl") == (source / "events.jsonl").read_bytes()
    assert sessions.read_member(ref, "missing") is None
    assert sessions.member_exists(ref, "checkpoints/index.md")
    with sessions.materialize(ref) as materialized:
        assert (materialized / "checkpoints" / "index.md").read_bytes() == checkpoint
    assert not materialized.exists()
    assert (source / "events.jsonl").is_file()
    restored = sessions.restore_session(ref, tmp_path / "restored")
    assert (restored / "events.jsonl").read_bytes() == (source / "events.jsonl").read_bytes()
    assert (restored / "checkpoints" / "index.md").read_bytes() == checkpoint


def test_zip_selection_metadata_stays_uncompressed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ref = sessions.archive_session(_session(tmp_path / "live"), tmp_path / "store", codec="zip")

    def forbidden_open(*args: object, **kwargs: object) -> None:
        raise AssertionError("metadata reads must not open ZIP contents")

    monkeypatch.setattr(zipfile, "ZipFile", forbidden_open)
    assert sessions.read_workspace(ref)["cwd"] == "/example"
    assert sessions.read_origin(ref) == {"source": "test"}


@pytest.mark.parametrize("key", ["desktop", "host.containers/worker", "repo.codespaces/box"])
def test_archive_only_zip_discovery_preserves_source_and_session_id(
    tmp_path: Path, key: str
) -> None:
    store = tmp_path / key / "archived"
    _zip(store / "session-1.zip", [("events.jsonl", b"{}\n")])
    source = next(iter_archive_sources(tmp_path))
    refs = list(source.iter_sessions())
    assert source.key == key
    assert [(ref.id, ref.kind) for ref in refs] == [("session-1", "archive")]
    assert sessions.read_member(refs[0], "events.jsonl") == b"{}\n"


def test_zip_accepts_benign_root_directory_entries(tmp_path: Path) -> None:
    archive = tmp_path / "rooted.zip"
    _zip(archive, [("./", b""), ("./events.jsonl", b"{}\n")])
    ref = sessions.SessionRef("rooted", "archive", archive, tmp_path)
    assert sessions.verify_archive(ref)
    assert sessions.read_member(ref, "events.jsonl") == b"{}\n"
    with sessions.materialize(ref) as directory:
        assert (directory / "events.jsonl").read_bytes() == b"{}\n"


@pytest.mark.parametrize(
    "session_id",
    [
        "",
        ".",
        "..",
        "../other",
        "a/b",
        r"a\b",
        r"C:\other",
        "a?.txt",
        "a*.txt",
        "a<.txt",
        "a>.txt",
        'a".txt',
        "a|.txt",
        "a\x00name",
        "a\x1fname",
        "a\x7fname",
        "NUL.txt",
        "CON .txt",
        "COM9.log",
    ],
)
def test_session_ids_cannot_escape_lookup_sidecars_or_materialization(
    tmp_path: Path, session_id: str
) -> None:
    with pytest.raises(ValueError, match="session id"):
        sessions.SessionRef(session_id, "archive", tmp_path / "valid.zip", tmp_path)
    with pytest.raises(ValueError, match="session id"):
        sessions.resolve_ref(session_id, tmp_path / "state", tmp_path / "store")
    with pytest.raises(ValueError, match="session id"):
        sessions.is_archived(session_id, tmp_path / "store", codec="zip")


@pytest.mark.parametrize("filename", [".zip", "...zip", ".tar.gz", "...tar.gz"])
def test_unsafe_archive_stems_fail_before_observation(tmp_path: Path, filename: str) -> None:
    store = tmp_path / "store"
    store.mkdir()
    (store / filename).write_bytes(b"must not be admitted")
    with pytest.raises(ValueError, match="session id"):
        next(sessions.iter_session_refs(None, store))


def test_identical_tar_and_zip_are_one_content_proven_session(tmp_path: Path) -> None:
    source = _session(tmp_path / "live")
    store = tmp_path / "store"
    tar_ref = sessions.archive_session(source, store)
    zip_ref = sessions.archive_session(source, store, codec="zip")
    assert sessions.CODECS["targz"].member_digests(tar_ref.path) == (
        sessions.CODECS["zip"].member_digests(zip_ref.path)
    )
    refs = list(sessions.iter_session_refs(None, store))
    assert refs == [tar_ref]
    assert sessions.resolve_ref(source.name, tmp_path / "absent", store) == tar_ref
    assert tar_ref.path.is_file() and zip_ref.path.is_file()


@pytest.mark.parametrize("member", ["events.jsonl", "checkpoints/index.md", "workspace.yaml"])
def test_divergent_archive_representations_fail_before_first_observation(
    tmp_path: Path, member: str
) -> None:
    source = _session(tmp_path / "live")
    store = tmp_path / "store"
    tar_ref = sessions.archive_session(source, store)
    (source / member).write_bytes(b"different evidence\n")
    zip_ref = sessions.archive_session(source, store, codec="zip")
    with pytest.raises(ValueError, match="divergent"):
        next(sessions.iter_session_refs(None, store))
    with pytest.raises(ValueError, match="divergent"):
        sessions.resolve_ref(source.name, tmp_path / "absent", store)
    assert tar_ref.path.is_file() and zip_ref.path.is_file()


def test_valid_live_session_still_shadows_older_archive_variants(tmp_path: Path) -> None:
    state = tmp_path / "session-state"
    source = _session(state)
    store = tmp_path / "store"
    sessions.archive_session(source, store)
    (source / "events.jsonl").write_bytes(b"continued live evidence\n")
    sessions.archive_session(source, store, codec="zip")
    refs = list(sessions.iter_session_refs(state, store))
    assert [(ref.id, ref.kind) for ref in refs] == [(source.name, "live")]
    assert sessions.resolve_ref(source.name, state, store) == refs[0]


def test_empty_eventless_zip_is_not_verified(tmp_path: Path) -> None:
    archive = tmp_path / "eventless.zip"
    _zip(archive, [("workspace.yaml", b"id: eventless\n")])
    ref = sessions.SessionRef("eventless", "archive", archive, tmp_path)
    assert not sessions.verify_archive(ref)
    assert sessions.read_member(ref, "events.jsonl") is None


def test_tar_comparison_streams_instead_of_materializing_headers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ref = sessions.archive_session(_session(tmp_path / "live"), tmp_path / "store")

    def forbidden_members(*args: object, **kwargs: object) -> None:
        raise AssertionError("comparison must not materialize all tar headers")

    monkeypatch.setattr(tarfile.TarFile, "getmembers", forbidden_members)
    assert "events.jsonl" in sessions.CODECS["targz"].member_digests(ref.path)


def test_tar_comparison_counts_directory_headers_toward_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "directories.tar.gz"
    monkeypatch.setattr(session_codecs, "MAX_ARCHIVE_MEMBERS", 2)
    with tarfile.open(archive, "w:gz") as output:
        for name in ("one", "two"):
            info = tarfile.TarInfo(name)
            info.type = tarfile.DIRTYPE
            output.addfile(info)
    assert sessions.CODECS["targz"].member_digests(archive) == {}
    with tarfile.open(archive, "w:gz") as output:
        for name in ("one", "two", "three"):
            info = tarfile.TarInfo(name)
            info.type = tarfile.DIRTYPE
            output.addfile(info)
    with pytest.raises(ValueError, match="member budget"):
        sessions.CODECS["targz"].member_digests(archive)


def test_tar_comparison_bounds_extended_metadata_before_decoding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "metadata.tar.gz"
    monkeypatch.setattr(session_codecs, "MAX_TAR_METADATA_BYTES", 8)
    with tarfile.open(archive, "w:gz") as output:
        info = tarfile.TarInfo("././@LongLink")
        info.type = tarfile.GNUTYPE_LONGNAME
        info.size = 9
        output.addfile(info, io.BytesIO(b"longname\0"))
    with pytest.raises(ValueError, match="metadata byte budget"):
        sessions.CODECS["targz"].member_digests(archive)


def _corrupt_payload(archive: Path, *, deflated: bool) -> None:
    with zipfile.ZipFile(archive) as opened:
        info = opened.getinfo("events.jsonl")
        offset = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
    data = bytearray(archive.read_bytes())
    if deflated:
        data[offset] = 7  # Reserved DEFLATE block type.
    else:
        data[offset] ^= 1
    archive.write_bytes(data)


def test_deflate_corruption_returns_false_from_verification(tmp_path: Path) -> None:
    archive = tmp_path / "deflate.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        output.writestr("events.jsonl", b"{}\n")
    _corrupt_payload(archive, deflated=True)
    ref = sessions.SessionRef("deflate", "archive", archive, tmp_path)
    with pytest.raises(zlib.error):
        sessions.read_member(ref, "events.jsonl")
    assert not sessions.verify_archive(ref)


def test_truncated_zip_decode_returns_false_from_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "truncated.zip"
    _zip(archive, [("events.jsonl", b"{}\n")])

    def truncated(*args: object, **kwargs: object) -> None:
        raise EOFError("truncated decoded member")

    monkeypatch.setattr(session_codecs, "_copy_and_digest", truncated)
    assert not sessions.verify_archive(sessions.SessionRef("truncated", "archive", archive))


def test_failed_zip_decode_never_unlinks_a_caller_created_member(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "replaced.zip"
    _zip(archive, [("events.jsonl", b"{}\n")])
    destination = tmp_path / "restore"
    out = destination / "events.jsonl"

    def fail_copy(*args: object, **kwargs: object) -> None:
        out.write_bytes(b"unrelated replacement")
        raise ValueError("interrupted decode")

    monkeypatch.setattr(session_codecs, "_copy_and_digest", fail_copy)
    with pytest.raises(ValueError, match="interrupted decode"):
        sessions.CODECS["zip"].extract_all(archive, destination)
    assert out.read_bytes() == b"unrelated replacement"
    assert list(destination.iterdir()) == [out]


def test_zip_publication_preserves_a_destination_created_after_decode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "racing.zip"
    _zip(archive, [("events.jsonl", b"{}\n")])
    destination = tmp_path / "restore"
    out = destination / "events.jsonl"
    original_link = os.link

    def racing_link(source: Path, target: Path) -> None:
        assert source.read_bytes() == b"{}\n"
        assert target == out
        target.write_bytes(b"caller-owned")
        original_link(source, target)

    monkeypatch.setattr(os, "link", racing_link)
    with pytest.raises(FileExistsError):
        sessions.CODECS["zip"].extract_all(archive, destination)
    assert out.read_bytes() == b"caller-owned"
    assert list(destination.iterdir()) == [out]


def test_zip_write_failure_never_publishes_partial_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "write-failure.zip"
    _zip(archive, [("events.jsonl", b"{}\n")])
    destination = tmp_path / "restore"

    def fail_write(source, target, maximum_bytes):
        target.write(b"partial")
        raise OSError("interrupted write")

    monkeypatch.setattr(session_codecs, "_copy_and_digest", fail_write)
    with pytest.raises(OSError, match="interrupted write"):
        sessions.CODECS["zip"].extract_all(archive, destination)
    assert list(destination.iterdir()) == []


def test_zip_publication_requires_atomic_nonoverwriting_filesystem_support(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "unsupported.zip"
    _zip(archive, [("events.jsonl", b"{}\n")])
    destination = tmp_path / "restore"

    def unsupported_link(*args: object, **kwargs: object) -> None:
        raise OSError("hard links unsupported")

    monkeypatch.setattr(os, "link", unsupported_link)
    with pytest.raises(OSError, match="hard links unsupported"):
        sessions.CODECS["zip"].extract_all(archive, destination)
    assert list(destination.iterdir()) == []


@pytest.mark.parametrize("deflated", [False, True])
def test_failed_zip_restore_leaves_no_partial_member_and_can_retry(
    tmp_path: Path, deflated: bool
) -> None:
    archive = tmp_path / "retry.zip"
    payload = b"x" * (2 * 1024 * 1024)
    compression = zipfile.ZIP_DEFLATED if deflated else zipfile.ZIP_STORED
    with zipfile.ZipFile(archive, "w", compression=compression) as output:
        output.writestr("events.jsonl", payload)
    valid = archive.read_bytes()
    _corrupt_payload(archive, deflated=deflated)
    destination = tmp_path / "restored" / "retry"
    destination.mkdir(parents=True)
    (destination / "keep.txt").write_bytes(b"caller-owned")
    ref = sessions.SessionRef("retry", "archive", archive, tmp_path)
    with pytest.raises((zipfile.BadZipFile, zlib.error)):
        sessions.restore_session(ref, destination.parent)
    assert list(destination.iterdir()) == [destination / "keep.txt"]
    assert (destination / "keep.txt").read_bytes() == b"caller-owned"
    archive.write_bytes(valid)
    assert sessions.restore_session(ref, destination.parent) == destination
    assert (destination / "events.jsonl").read_bytes() == payload


def test_zip_verification_uses_one_descriptor_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "snapshot.zip"
    replacement = tmp_path / "replacement.zip"
    _zip(archive, [("events.jsonl", b"{}\n")])
    _zip(replacement, [("workspace.yaml", b"id: eventless\n")])
    original_open = session_codecs._open_zip
    opens = 0

    @contextmanager
    def replacing_open(path: Path):
        nonlocal opens
        opens += 1
        with original_open(path) as opened:
            yield opened
        if opens == 1:
            replacement.replace(path)

    monkeypatch.setattr(session_codecs, "_open_zip", replacing_open)
    ref = sessions.SessionRef("snapshot", "archive", archive, tmp_path)
    assert sessions.verify_archive(ref)
    assert opens == 1
    assert not sessions.verify_archive(ref)


@pytest.mark.parametrize("corruption", ["header", "crc"])
def test_corrupt_zip_is_explicit_failure_not_missing_evidence(
    tmp_path: Path, corruption: str
) -> None:
    archive = tmp_path / "corrupt.zip"
    _zip(archive, [("events.jsonl", b"{}\n")])
    if corruption == "header":
        archive.write_bytes(b"not a ZIP archive")
    else:
        with zipfile.ZipFile(archive) as opened:
            info = opened.getinfo("events.jsonl")
            offset = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
        data = bytearray(archive.read_bytes())
        data[offset] ^= 1
        archive.write_bytes(data)
    ref = sessions.SessionRef("corrupt", "archive", archive, tmp_path)
    assert not sessions.verify_archive(ref)
    with pytest.raises(zipfile.BadZipFile):
        sessions.read_member(ref, "events.jsonl")


@pytest.mark.parametrize("name", ["../escape", "/absolute", r"C:\escape", ".. /escape", "CON.txt"])
def test_unsafe_zip_members_are_rejected_before_extraction(tmp_path: Path, name: str) -> None:
    archive = tmp_path / "unsafe.zip"
    _zip(archive, [("events.jsonl", b"{}\n"), (name, b"escape")])
    ref = sessions.SessionRef("unsafe", "archive", archive, tmp_path)
    assert not sessions.verify_archive(ref)
    destination = tmp_path / "output"
    with pytest.raises(ValueError):
        sessions.CODECS["zip"].extract_all(archive, destination)
    assert not destination.exists()


@pytest.mark.parametrize("codec", ["targz", "zip"])
@pytest.mark.parametrize("name", ["a?.txt", "a\x1fname", "NUL.txt", "CON .txt"])
def test_shared_archive_path_policy_rejects_nonportable_components(
    tmp_path: Path, codec: str, name: str
) -> None:
    archive = tmp_path / f"unsafe{sessions.CODECS[codec].suffix}"
    if codec == "zip":
        _zip(archive, [("events.jsonl", b"{}\n"), (name, b"unsafe")])
    else:
        with tarfile.open(archive, "w:gz") as output:
            for member in ("events.jsonl", name):
                info = tarfile.TarInfo(member)
                info.size = 3
                output.addfile(info, io.BytesIO(b"{}\n"))
    ref = sessions.SessionRef("unsafe", "archive", archive, tmp_path)
    assert not sessions.verify_archive(ref)
    with pytest.raises(ValueError, match="unsafe archive member path"):
        sessions.CODECS[codec].member_digests(archive)
    with pytest.raises(ValueError, match="unsafe archive member path"), sessions.materialize(ref):
        pass


def test_zip_raw_nul_name_is_rejected_before_zipfile_truncation(tmp_path: Path) -> None:
    archive = tmp_path / "nul-member.zip"
    _zip(archive, [("events.jsonl", b"{}\n"), ("badXname", b"unsafe")])
    archive.write_bytes(archive.read_bytes().replace(b"badXname", b"bad\x00name"))
    assert not sessions.verify_archive(sessions.SessionRef("nul-member", "archive", archive))
    with pytest.raises(ValueError, match="unsafe archive member path"):
        sessions.CODECS["zip"].list_members(archive)


@pytest.mark.parametrize("kind", [stat.S_IFLNK, stat.S_IFIFO, stat.S_IFCHR])
def test_zip_rejects_non_regular_members(tmp_path: Path, kind: int) -> None:
    info = zipfile.ZipInfo("events.jsonl")
    info.create_system = 3
    info.external_attr = (kind | 0o600) << 16
    archive = tmp_path / "special.zip"
    _zip(archive, [(info, b"{}\n")])
    with pytest.raises(ValueError, match="non-regular"):
        sessions.CODECS["zip"].list_members(archive)


@pytest.mark.parametrize(
    "names",
    [
        ["events.jsonl", "./events.jsonl"],
        ["checkpoints/index.md", r"checkpoints\index.md"],
        ["events.jsonl", "events.jsonl/child"],
    ],
)
def test_zip_rejects_normalized_duplicate_and_file_directory_conflicts(
    tmp_path: Path, names: list[str]
) -> None:
    archive = tmp_path / "ambiguous.zip"
    _zip(archive, [(name, b"{}\n") for name in names])
    with pytest.raises(ValueError):
        sessions.CODECS["zip"].list_members(archive)


def test_windows_case_conflicting_zip_members_are_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "case.zip"
    _zip(archive, [("events.jsonl", b"{}\n"), ("EVENTS.JSONL", b"different")])
    monkeypatch.setattr(session_codecs, "_CASE_INSENSITIVE", True)
    with pytest.raises(ValueError, match="duplicate"):
        sessions.CODECS["zip"].list_members(archive)


@pytest.mark.parametrize("budget", ["member", "total", "count"])
def test_zip_budgets_accept_exact_boundary_and_reject_overflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, budget: str
) -> None:
    archive = tmp_path / "bounded.zip"
    if budget == "member":
        monkeypatch.setattr(session_codecs, "MAX_ARCHIVE_MEMBER_BYTES", 3)
        accepted = [("events.jsonl", b"{}\n")]
        rejected = [("events.jsonl", b"{}\n ")]
    elif budget == "total":
        monkeypatch.setattr(session_codecs, "MAX_ARCHIVE_BYTES", 4)
        accepted = [("events.jsonl", b"{}\n"), ("extra", b"x")]
        rejected = [("events.jsonl", b"{}\n"), ("extra", b"xx")]
    else:
        monkeypatch.setattr(session_codecs, "MAX_ARCHIVE_MEMBERS", 1)
        accepted = [("events.jsonl", b"{}\n")]
        rejected = [("events.jsonl", b"{}\n"), ("extra", b"x")]
    _zip(archive, accepted)
    assert sessions.verify_archive(sessions.SessionRef("bounded", "archive", archive, tmp_path))
    _zip(archive, rejected)
    with pytest.raises(ValueError, match="budget"):
        sessions.CODECS["zip"].read_member(archive, "events.jsonl")


def test_failed_zip_creation_preserves_existing_archive_and_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _session(tmp_path / "live")
    dest = tmp_path / "archive.zip"
    dest.write_bytes(b"prior archive")
    monkeypatch.setattr(session_codecs, "MAX_ARCHIVE_MEMBER_BYTES", 1)
    with pytest.raises(ValueError, match="budget"):
        sessions.CODECS["zip"].archive_dir(source, dest)
    assert dest.read_bytes() == b"prior archive"
    assert (source / "events.jsonl").is_file()
    assert not list(tmp_path.glob(".archive.zip.*.tmp"))


@pytest.mark.parametrize("codec", ["targz", "zip"])
def test_archive_writers_apply_shared_path_policy_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codec: str
) -> None:
    source = _session(tmp_path / "live")
    implementation = sessions.CODECS[codec]
    destination = tmp_path / f"prior{implementation.suffix}"
    destination.write_bytes(b"prior evidence")
    original_validate = session_codecs._validate_member_name

    def reject_member(name: str) -> str:
        if name == "checkpoints/index.md":
            raise ValueError("unsafe archive member path")
        return original_validate(name)

    monkeypatch.setattr(session_codecs, "_validate_member_name", reject_member)
    with pytest.raises(ValueError, match="unsafe archive member path"):
        implementation.archive_dir(source, destination)
    assert destination.read_bytes() == b"prior evidence"
    assert (source / "checkpoints" / "index.md").read_bytes() == b"# checkpoint\n"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["live", destination.name]


@pytest.mark.skipif(os.name == "nt", reason="Windows cannot create these POSIX source names")
@pytest.mark.parametrize("name", ["NUL.txt", "a?.txt", "a\x1fname"])
def test_tar_writer_rejects_nonportable_posix_source_names(tmp_path: Path, name: str) -> None:
    source = _session(tmp_path / "live")
    (source / name).write_bytes(b"retained source")
    destination = tmp_path / "prior.tar.gz"
    destination.write_bytes(b"prior evidence")
    with pytest.raises(ValueError, match="unsafe archive member path"):
        sessions.CODECS["targz"].archive_dir(source, destination)
    assert destination.read_bytes() == b"prior evidence"
    assert (source / name).read_bytes() == b"retained source"
    assert not destination.with_name(destination.name + ".tmp").exists()


def test_zip_central_directory_budget_precedes_zipfile_allocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "index.zip"
    _zip(archive, [("events.jsonl", b"{}\n")])
    directory_size = struct.unpack("<4s4H2LH", archive.read_bytes()[-22:])[5]
    monkeypatch.setattr(session_codecs, "MAX_ZIP_DIRECTORY_BYTES", directory_size)
    assert sessions.CODECS["zip"].list_members(archive) == ["events.jsonl"]
    monkeypatch.setattr(session_codecs, "MAX_ZIP_DIRECTORY_BYTES", directory_size - 1)

    def forbidden_parse(*args: object, **kwargs: object) -> None:
        raise AssertionError("oversized directory must not reach ZIP index allocation")

    monkeypatch.setattr(zipfile, "ZipFile", forbidden_parse)
    with pytest.raises(ValueError, match="central-directory"):
        sessions.CODECS["zip"].list_members(archive)


@pytest.mark.parametrize("sentinels", [False, True])
def test_zip64_directory_is_preflighted_even_without_legacy_sentinels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sentinels: bool
) -> None:
    archive = tmp_path / "zip64.zip"
    _zip(archive, [("events.jsonl", b"{}\n")])
    data = archive.read_bytes()
    end = list(struct.unpack("<4s4H2LH", data[-22:]))
    record_offset = len(data) - 22
    record = struct.pack(
        "<4sQ2H2L4Q", b"PK\x06\x06", 44, 45, 45, 0, 0, end[3], end[4], end[5], end[6]
    )
    locator = struct.pack("<4sLQL", b"PK\x06\x07", 0, record_offset, 1)
    if sentinels:
        end[3] = end[4] = 0xFFFF
        end[5] = end[6] = 0xFFFFFFFF
    archive.write_bytes(data[:-22] + record + locator + struct.pack("<4s4H2LH", *end))
    ref = sessions.SessionRef("zip64", "archive", archive, tmp_path)
    assert sessions.verify_archive(ref)
    monkeypatch.setattr(session_codecs, "MAX_ZIP_DIRECTORY_BYTES", 1)
    with pytest.raises(ValueError, match="central-directory"):
        sessions.CODECS["zip"].list_members(archive)


def test_zip_materialization_never_overwrites_existing_destination(tmp_path: Path) -> None:
    archive = tmp_path / "session.zip"
    _zip(archive, [("events.jsonl", b"{}\n")])
    destination = tmp_path / "destination"
    destination.mkdir()
    event_file = destination / "events.jsonl"
    event_file.write_bytes(b"existing evidence")
    with pytest.raises(FileExistsError):
        sessions.CODECS["zip"].extract_all(archive, destination)
    assert event_file.read_bytes() == b"existing evidence"


@pytest.mark.parametrize("location", ["archive", "parent"])
def test_zip_reader_rejects_linked_archive_paths(tmp_path: Path, location: str) -> None:
    outside = tmp_path / "outside"
    archive = outside / "session.zip"
    _zip(archive, [("events.jsonl", b"{}\n")])
    linked = tmp_path / "linked"
    try:
        if location == "parent":
            linked.symlink_to(outside, target_is_directory=True)
            candidate = linked / archive.name
        else:
            linked.symlink_to(archive)
            candidate = linked
    except OSError as exc:
        pytest.skip(f"native symlink creation unavailable: {exc}")
    with pytest.raises((OSError, ValueError)):
        sessions.CODECS["zip"].read_member(candidate, "events.jsonl")


def test_zip_writer_does_not_descend_into_name_surrogate_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_logger.sync import provenance

    source = _session(tmp_path / "live")
    nested = source / "redirected"
    nested.mkdir()
    (nested / "outside-evidence").write_bytes(b"must not be captured")
    original = provenance.is_link_or_reparse

    def surrogate(path: Path, mode: int) -> bool:
        return path == nested or original(path, mode)

    monkeypatch.setattr(provenance, "is_link_or_reparse", surrogate)
    archive = tmp_path / "session.zip"
    sessions.CODECS["zip"].archive_dir(source, archive)
    assert "redirected/outside-evidence" not in sessions.CODECS["zip"].list_members(archive)
