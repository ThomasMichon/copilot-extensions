"""Synthetic bounded transcript reads across live, TAR, and ZIP sessions."""

from __future__ import annotations

import gzip
import io
import json
import os
import stat
import tarfile
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_logger import session_codecs as codecs
from agent_logger import sessions, usage_reader as reader
from agent_logger.sessions import SessionRef
from agent_logger.sync.publication_directory import PublicationDirectory
from agent_logger.usage_ledger import UsageEvidenceError

EVENTS = [{"type": "user.message", "data": {"content": "synthetic \u2603"}}, {"type": "done"}]
PAYLOAD = b"".join(json.dumps(event).encode("utf-8") + b"\n" for event in EVENTS)


def _ref(tmp_path: Path, kind: str, data: bytes = PAYLOAD) -> SessionRef:
    if kind == "live":
        path = tmp_path / "session"
        path.mkdir()
        (path / "events.jsonl").write_bytes(data)
        return SessionRef("session", "live", path)
    path = tmp_path / ("session.tar.gz" if kind == "tar" else "session.zip")
    if kind == "tar":
        _tar(path, [("events.jsonl", data)])
    else:
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("events.jsonl", data)
    return SessionRef("session", "archive", path, tmp_path)


def _tar(path: Path, members: list[tuple[str, bytes]]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))


@pytest.mark.parametrize("kind", ["live", "tar", "zip"])
def test_usage_reader_equivalent_events_without_extraction(tmp_path, monkeypatch, kind):
    ref = _ref(tmp_path, kind)
    before = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))

    def forbidden(*args, **kwargs):
        pytest.fail("bounded reader must not extract or use unbounded codec reads")

    monkeypatch.setattr(codecs.TarGzCodec, "read_member", forbidden)
    monkeypatch.setattr(codecs.ZipCodec, "read_member", forbidden)
    monkeypatch.setattr(tarfile.TarFile, "extractall", forbidden)
    monkeypatch.setattr(zipfile.ZipFile, "extractall", forbidden)
    assert reader.read_session_events(ref) == EVENTS
    assert reader.read_session_member(ref, "events.jsonl", max_bytes=len(PAYLOAD)) == PAYLOAD
    assert sessions.read_member(ref, "events.jsonl", max_bytes=len(PAYLOAD)) == PAYLOAD
    assert reader.read_session_member(ref, "absent.json", max_bytes=0) is None
    assert before == sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))


@pytest.mark.parametrize("kind", ["live", "tar", "zip"])
@pytest.mark.parametrize("data", [
    b"{}\n{", b"{}", b"\n", b"[]\n", b"null\n", b"{bad}\n",
    b'{"value":"\xff"}\n', b'{"value":NaN}\n', b'\xff\xfe{\x00}\x00\n\x00',
])
def test_usage_reader_rejects_invalid_jsonl(tmp_path, kind, data):
    with pytest.raises(UsageEvidenceError):
        reader.read_session_events(_ref(tmp_path, kind, data))


@pytest.mark.parametrize("kind", ["live", "tar", "zip"])
def test_usage_reader_empty_is_valid(tmp_path, kind):
    assert reader.read_session_events(_ref(tmp_path, kind, b"")) == []


@pytest.mark.parametrize("kind", ["live", "tar", "zip"])
@pytest.mark.parametrize("limit,value", [
    ("MAX_TRANSCRIPT_BYTES", len(PAYLOAD) - 1),
    ("MAX_LINE_BYTES", 4),
    ("MAX_EVENTS", 1),
])
def test_usage_reader_limits(tmp_path, monkeypatch, kind, limit, value):
    monkeypatch.setattr(reader, limit, value)
    with pytest.raises(UsageEvidenceError, match="limit"):
        reader.read_session_events(_ref(tmp_path, kind))


@pytest.mark.parametrize("kind", ["live", "tar", "zip"])
def test_usage_reader_exact_limits(tmp_path, monkeypatch, kind):
    data = b"{}\n{}\n"
    monkeypatch.setattr(reader, "MAX_TRANSCRIPT_BYTES", len(data))
    monkeypatch.setattr(reader, "MAX_LINE_BYTES", 3)
    monkeypatch.setattr(reader, "MAX_EVENTS", 2)
    assert reader.read_session_events(_ref(tmp_path, kind, data)) == [{}, {}]


@pytest.mark.parametrize("kind", ["live", "tar", "zip"])
def test_usage_reader_preallocation_size_check(tmp_path, monkeypatch, kind):
    ref = _ref(tmp_path, kind)
    if kind == "live":
        original = PublicationDirectory.open

        def guarded_open(self, name, **kwargs):
            return _GuardedStream(original(self, name, **kwargs), maximum=0)

        monkeypatch.setattr(PublicationDirectory, "open", guarded_open)
    else:
        def forbidden(*args, **kwargs):
            pytest.fail("oversize member opened before its size was checked")

        if kind == "tar":
            monkeypatch.setattr(tarfile.TarFile, "extractfile", forbidden)
        else:
            monkeypatch.setattr(zipfile.ZipFile, "open", forbidden)
    with pytest.raises(UsageEvidenceError, match="limit"):
        reader.read_session_member(ref, "events.jsonl", max_bytes=1)


class _GuardedStream:
    def __init__(self, stream, maximum, mutate=None):
        self.stream = stream
        self.maximum = maximum
        self.mutate = mutate

    def __getattr__(self, name):
        return getattr(self.stream, name)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.stream.close()

    def _after_read(self, data):
        if self.mutate is not None:
            callback, self.mutate = self.mutate, None
            callback()
        return data

    def read(self, size=-1):
        assert 0 <= size <= self.maximum
        return self._after_read(self.stream.read(size))

    def readline(self, size=-1):
        assert 0 <= size <= self.maximum
        return self._after_read(self.stream.readline(size))


def test_usage_reader_live_line_bound_before_json_allocation(tmp_path, monkeypatch):
    ref = _ref(tmp_path, "live", b" " * 100 + b"\n")
    monkeypatch.setattr(reader, "MAX_LINE_BYTES", 8)
    original = PublicationDirectory.open
    monkeypatch.setattr(
        PublicationDirectory, "open",
        lambda self, name: _GuardedStream(original(self, name), maximum=9),
    )
    monkeypatch.setattr(
        reader.json, "loads", lambda *a, **k: pytest.fail("oversize line parsed"),
    )
    with pytest.raises(UsageEvidenceError, match="limit"):
        reader.read_session_events(ref)


@pytest.mark.parametrize("kind", ["live", "tar", "zip"])
def test_usage_reader_successful_member_reads_are_always_sized(tmp_path, monkeypatch, kind):
    ref = _ref(tmp_path, kind)
    if kind == "live":
        original = PublicationDirectory.open
        monkeypatch.setattr(
            PublicationDirectory, "open",
            lambda self, name: _GuardedStream(original(self, name), len(PAYLOAD) + 1),
        )
    elif kind == "tar":
        original = tarfile.TarFile.extractfile
        monkeypatch.setattr(
            tarfile.TarFile, "extractfile",
            lambda self, info: _GuardedStream(original(self, info), info.size + 1),
        )
    else:
        original = zipfile.ZipFile.open
        monkeypatch.setattr(
            zipfile.ZipFile, "open",
            lambda self, info: _GuardedStream(original(self, info), info.file_size + 1),
        )
    assert reader.read_session_member(ref, "events.jsonl", max_bytes=len(PAYLOAD)) == PAYLOAD


def test_usage_reader_event_limit_before_next_line_allocation(tmp_path, monkeypatch):
    ref = _ref(tmp_path, "live", b"{}\n" + b" " * 100 + b"\n")
    monkeypatch.setattr(reader, "MAX_EVENTS", 1)
    original = PublicationDirectory.open
    monkeypatch.setattr(
        PublicationDirectory, "open",
        lambda self, name: _GuardedStream(original(self, name), reader.MAX_LINE_BYTES + 1),
    )
    original_readline = _GuardedStream.readline
    calls = 0

    def guarded_readline(self, size=-1):
        nonlocal calls
        calls += 1
        if calls > 1:
            assert size == 1
        return original_readline(self, size)

    monkeypatch.setattr(_GuardedStream, "readline", guarded_readline)
    with pytest.raises(UsageEvidenceError, match="limit"):
        reader.read_session_events(ref)


@pytest.mark.parametrize("kind", ["live", "tar", "zip"])
def test_usage_reader_missing_events_is_not_empty(tmp_path, kind):
    ref = _ref(tmp_path, kind)
    if kind == "live":
        (ref.path / "events.jsonl").rename(ref.path / "other")
    elif kind == "tar":
        _tar(ref.path, [("other", b"")])
    else:
        with zipfile.ZipFile(ref.path, "w") as archive:
            archive.writestr("other", b"")
    with pytest.raises(UsageEvidenceError, match="missing"):
        reader.read_session_events(ref)


@pytest.mark.parametrize("method", ["events", "member"])
def test_usage_reader_live_mutation(tmp_path, monkeypatch, method):
    ref = _ref(tmp_path, "live")
    path = ref.path / "events.jsonl"
    original = PublicationDirectory.open

    def mutate():
        info = path.stat()
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 2_000_000_000))

    monkeypatch.setattr(
        PublicationDirectory, "open",
        lambda self, name: _GuardedStream(
            original(self, name), maximum=reader.MAX_TRANSCRIPT_BYTES + 1, mutate=mutate,
        ),
    )
    with pytest.raises(UsageEvidenceError, match="changed"):
        if method == "events":
            reader.read_session_events(ref)
        else:
            reader.read_session_member(ref, "events.jsonl", max_bytes=len(PAYLOAD))


@pytest.mark.parametrize("member", ["../events.jsonl", "/events.jsonl", "C:\\events.jsonl",
                                   "sub/events.jsonl", ".\\events.jsonl", "", "CON"])
@pytest.mark.parametrize("kind", ["live", "tar", "zip"])
def test_usage_reader_unsafe_member_names(tmp_path, kind, member):
    with pytest.raises(UsageEvidenceError):
        reader.read_session_member(_ref(tmp_path, kind), member, max_bytes=100)


@pytest.mark.parametrize("value", [-1, True, 1.5, None])
def test_usage_reader_invalid_limits(tmp_path, value):
    with pytest.raises(UsageEvidenceError, match="nonnegative integer"):
        reader.read_session_member(_ref(tmp_path, "live"), "events.jsonl", max_bytes=value)


def test_usage_reader_unknown_kind_and_codec(tmp_path):
    with pytest.raises(UsageEvidenceError, match="kind"):
        reader.read_session_events(SessionRef("s", "unexpected", tmp_path))
    with pytest.raises(UsageEvidenceError, match="no codec"):
        reader.read_session_events(SessionRef("s", "archive", tmp_path / "s.unknown"))


def test_usage_reader_custom_codec_explicitly_unsupported(tmp_path, monkeypatch):
    class CustomCodec(codecs.Codec):
        name, suffix = "custom", ".custom"

        def read_member(self, archive, member):
            pytest.fail("must not fall back to an unbounded third-party codec")

        def extract_all(self, archive, dest_dir):
            pytest.fail("must not materialize a session")

        def list_members(self, archive):
            return ["events.jsonl"]

    monkeypatch.setitem(sessions.CODECS, "custom", CustomCodec())
    with pytest.raises(UsageEvidenceError, match="does not support bounded"):
        reader.read_session_events(SessionRef("s", "archive", tmp_path / "s.custom"))


@pytest.mark.parametrize("kind", ["tar", "zip"])
def test_usage_reader_archive_stability(tmp_path, monkeypatch, kind):
    ref = _ref(tmp_path, kind)
    original = PublicationDirectory.stat
    calls = 0

    def changed(self, name):
        nonlocal calls
        calls += 1
        if calls == 2:
            info = ref.path.stat()
            os.utime(ref.path, ns=(info.st_atime_ns, info.st_mtime_ns + 2_000_000_000))
        return original(self, name)

    monkeypatch.setattr(PublicationDirectory, "stat", changed)
    with pytest.raises(UsageEvidenceError, match="changed"):
        reader.read_session_events(ref)


@pytest.mark.parametrize("kind", ["live", "tar", "zip"])
def test_usage_reader_ctime_is_compared_within_each_stat_api(tmp_path, monkeypatch, kind):
    ref = _ref(tmp_path, kind)
    original = PublicationDirectory.stat

    def distinct_path_ctime(self, name):
        info = original(self, name)
        return SimpleNamespace(
            st_dev=info.st_dev, st_ino=info.st_ino, st_size=info.st_size,
            st_mtime_ns=info.st_mtime_ns, st_ctime_ns=info.st_ctime_ns + 123_456,
        )

    monkeypatch.setattr(PublicationDirectory, "stat", distinct_path_ctime)
    assert reader.read_session_events(ref) == EVENTS


@pytest.mark.parametrize("kind", ["live", "tar", "zip"])
@pytest.mark.parametrize("link_parent", [False, True])
def test_usage_reader_filesystem_links(tmp_path, kind, link_parent):
    source = tmp_path / "source"
    source.mkdir()
    ref = _ref(source, kind)
    link = tmp_path / ("redirect" + ("" if kind == "live" else "".join(ref.path.suffixes)))
    target = ref.path if kind != "live" else ref.path / "events.jsonl"
    try:
        link.symlink_to(target.parent if link_parent else target, target_is_directory=link_parent)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    if link_parent:
        redirected = link / target.name
    else:
        redirected = link
    if kind == "live":
        if not link_parent:
            destination = tmp_path / "destination"
            destination.mkdir()
            (destination / "events.jsonl").symlink_to(link)
            ref = SessionRef("session", "live", destination)
        else:
            ref = SessionRef("session", "live", link)
    else:
        ref = SessionRef("session", "archive", redirected)
    with pytest.raises(UsageEvidenceError):
        reader.read_session_events(ref)


@pytest.mark.parametrize("kind", ["tar", "zip"])
def test_usage_reader_container_links_rejected(tmp_path, kind):
    ref = _ref(tmp_path, kind)
    if kind == "tar":
        with tarfile.open(ref.path, "w:gz") as archive:
            info = tarfile.TarInfo("events.jsonl")
            info.type, info.linkname = tarfile.SYMTYPE, "outside"
            archive.addfile(info)
    else:
        with zipfile.ZipFile(ref.path, "w") as archive:
            info = zipfile.ZipInfo("events.jsonl")
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, "outside")
    with pytest.raises(UsageEvidenceError, match="non-regular"):
        reader.read_session_events(ref)


@pytest.mark.parametrize("kind", ["tar", "zip"])
def test_usage_reader_container_selected_directory_rejected(tmp_path, kind):
    ref = _ref(tmp_path, kind)
    if kind == "tar":
        with tarfile.open(ref.path, "w:gz") as archive:
            info = tarfile.TarInfo("events.jsonl")
            info.type = tarfile.DIRTYPE
            archive.addfile(info)
    else:
        with zipfile.ZipFile(ref.path, "w") as archive:
            archive.writestr("events.jsonl/", b"")
    with pytest.raises(UsageEvidenceError):
        reader.read_session_events(ref)


@pytest.mark.parametrize("kind", ["tar", "zip"])
def test_usage_reader_truncated_container_rejected(tmp_path, kind):
    ref = _ref(tmp_path, kind)
    ref.path.write_bytes(ref.path.read_bytes()[:-8])
    with pytest.raises(UsageEvidenceError):
        reader.read_session_events(ref)


@pytest.mark.parametrize("problem", ["duplicate", "sparse", "trailing", "unsafe"])
def test_usage_reader_tar_validates_after_selected_member(tmp_path, problem):
    ref = _ref(tmp_path, "tar")
    if problem == "duplicate":
        _tar(ref.path, [("events.jsonl", PAYLOAD), ("events.jsonl", PAYLOAD)])
    elif problem == "unsafe":
        _tar(ref.path, [("events.jsonl", PAYLOAD), ("../unsafe", b"x")])
    elif problem == "sparse":
        with tarfile.open(ref.path, "w:gz") as archive:
            info = tarfile.TarInfo("events.jsonl")
            info.size = len(PAYLOAD)
            archive.addfile(info, io.BytesIO(PAYLOAD))
            sparse = tarfile.TarInfo("sparse")
            sparse.type = tarfile.GNUTYPE_SPARSE
            archive.addfile(sparse)
    else:
        expanded = gzip.decompress(ref.path.read_bytes())
        ref.path.write_bytes(gzip.compress(expanded + b"unexpected trailing data"))
    with pytest.raises(UsageEvidenceError):
        reader.read_session_events(ref)


@pytest.mark.parametrize("budget,value", [
    ("MAX_ARCHIVE_MEMBERS", 1), ("MAX_ARCHIVE_BYTES", len(PAYLOAD)),
    ("MAX_ARCHIVE_INPUT_BYTES", 1), ("MAX_TAR_CONTAINER_BYTES", 512),
    ("MAX_TAR_METADATA_BYTES", 1),
])
def test_usage_reader_tar_container_budgets(tmp_path, monkeypatch, budget, value):
    ref = _ref(tmp_path, "tar")
    with tarfile.open(ref.path, "w:gz", format=tarfile.PAX_FORMAT) as archive:
        for name, data in [("events.jsonl", PAYLOAD), ("extra", b"x")]:
            info = tarfile.TarInfo(name)
            info.pax_headers = {"comment": "synthetic metadata"}
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    monkeypatch.setattr(codecs, budget, value)
    with pytest.raises(UsageEvidenceError, match="budget"):
        reader.read_session_events(ref)


def test_usage_reader_tar_trailing_zero_expansion_is_bounded(tmp_path, monkeypatch):
    ref = _ref(tmp_path, "tar", b"")
    expanded = gzip.decompress(ref.path.read_bytes())
    ref.path.write_bytes(gzip.compress(expanded + b"\0" * 1024))
    monkeypatch.setattr(codecs, "MAX_TAR_CONTAINER_BYTES", len(expanded))
    with pytest.raises(UsageEvidenceError, match="decoded-container"):
        reader.read_session_events(ref)


def test_usage_reader_tar_metadata_preallocation_check(tmp_path, monkeypatch):
    ref = _ref(tmp_path, "tar")
    with tarfile.open(ref.path, "w:gz") as archive:
        info = tarfile.TarInfo("././@LongLink")
        info.type = tarfile.GNUTYPE_LONGNAME
        info.size = 2048
        archive.addfile(info, io.BytesIO(b"x" * 2047 + b"\0"))
    monkeypatch.setattr(codecs, "MAX_TAR_METADATA_BYTES", 16)
    original_read = tarfile._Stream.read

    def guarded_read(self, size):
        assert size <= 512
        return original_read(self, size)

    monkeypatch.setattr(tarfile._Stream, "read", guarded_read)
    with pytest.raises(UsageEvidenceError, match="metadata byte budget"):
        reader.read_session_events(ref)


@pytest.mark.parametrize("encoding", ["pax-0.0", "pax-0.1", "pax-1.0"])
def test_usage_reader_tar_sparse_metadata_rejected(tmp_path, monkeypatch, encoding):
    ref = _ref(tmp_path, "tar")
    headers = {
        "pax-0.0": {"GNU.sparse.size": "3"},
        "pax-0.1": {"GNU.sparse.map": "0,3"},
        "pax-1.0": {"GNU.sparse.major": "1", "GNU.sparse.minor": "0"},
    }
    with tarfile.open(ref.path, "w:gz", format=tarfile.PAX_FORMAT) as archive:
        info = tarfile.TarInfo("events.jsonl")
        info.size = 3
        info.pax_headers = headers[encoding]
        archive.addfile(info, io.BytesIO(b"{}\n"))

    def forbidden(*args, **kwargs):
        pytest.fail("sparse metadata parser reached")

    for method in ("_proc_gnusparse_00", "_proc_gnusparse_01", "_proc_gnusparse_10"):
        monkeypatch.setattr(tarfile.TarInfo, method, forbidden)
    with pytest.raises(UsageEvidenceError, match="sparse session tar"):
        reader.read_session_events(ref)


def test_usage_reader_zip_directory_bound_before_index(tmp_path, monkeypatch):
    ref = _ref(tmp_path, "zip")
    monkeypatch.setattr(codecs, "MAX_ZIP_DIRECTORY_BYTES", 1)
    monkeypatch.setattr(
        zipfile.ZipFile, "_RealGetContents",
        lambda *a, **k: pytest.fail("central directory allocated before its limit check"),
    )
    with pytest.raises(UsageEvidenceError, match="central-directory"):
        reader.read_session_events(ref)


def test_usage_reader_unbounded_legacy_seam_unchanged(tmp_path):
    ref = _ref(tmp_path, "live")
    assert sessions.read_member(ref, "events.jsonl") == PAYLOAD


@pytest.mark.parametrize("header", [b"x" * 512, b"x" * 257], ids=["invalid-checksum", "truncated"])
def test_usage_reader_review_regression_corrupt_header_after_selected_member(
    tmp_path: Path, header: bytes,
) -> None:
    ref = _ref(tmp_path, "tar")
    expanded = gzip.decompress(ref.path.read_bytes())
    boundary = 512 + ((len(PAYLOAD) + 511) // 512) * 512
    padding = b"\0" * 10240 if len(header) == 512 else b""
    ref.path.write_bytes(gzip.compress(expanded[:boundary] + header + padding))
    with pytest.raises(UsageEvidenceError, match="header"):
        reader.read_session_events(ref)


@pytest.mark.parametrize("kind", ["live", "tar", "zip"])
def test_usage_reader_review_regression_utf8_vs_json_messages_differ(tmp_path, kind):
    """Encoding errors and JSON syntax errors get distinguishable messages."""
    utf8_root = tmp_path / "utf8"
    utf8_root.mkdir()
    json_root = tmp_path / "json"
    json_root.mkdir()
    with pytest.raises(UsageEvidenceError, match=r"^transcript contains invalid UTF-8$"):
        reader.read_session_events(_ref(utf8_root, kind, b'{"value":"\xff"}\n'))
    with pytest.raises(UsageEvidenceError, match=r"^transcript contains invalid JSON$"):
        reader.read_session_events(_ref(json_root, kind, b"{bad}\n"))


def test_usage_reader_review_regression_tar_bounded_read_skips_hashing(tmp_path, monkeypatch):
    """A bounded single-member TAR read must not SHA-256 every other member."""
    ref = _ref(tmp_path, "tar", PAYLOAD)
    _tar(ref.path, [("events.jsonl", PAYLOAD), ("other-1", b"x" * 4096), ("other-2", b"y" * 4096)])
    digested: list[bytes] = []
    original_sha256 = codecs.hashlib.sha256

    class _TrackingHash:
        def __init__(self) -> None:
            self._inner = original_sha256()

        def update(self, data: bytes) -> None:
            digested.append(data)
            self._inner.update(data)

        def hexdigest(self) -> str:
            return self._inner.hexdigest()

    monkeypatch.setattr(codecs.hashlib, "sha256", _TrackingHash)
    assert reader.read_session_member(ref, "events.jsonl", max_bytes=len(PAYLOAD)) == PAYLOAD
    assert sum(len(chunk) for chunk in digested) == len(PAYLOAD)
