"""Aggregate transport is exact byte custody, never admission authority."""

import hashlib
import io
from pathlib import Path
import stat
import struct
import sys
import time
import zipfile

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parents[2] / "libs" / "installer-engine")
)
import installer_wheel_snapshot as snapshots


@pytest.fixture(autouse=True)
def pure_file_handles(monkeypatch):
    import installer_dependency_config as configuration

    # Native handles and extended paths are exercised by the constructor packet.
    monkeypatch.setattr(snapshots, "_open", lambda path: path.open("rb"))
    monkeypatch.setattr(configuration, "native_retained_path", str)


def bundle(tmp_path, *, change=None):
    members = {
        "wheels/one-1-py3-none-any.whl": b"first",
        "wheels/two-1-py3-none-any.whl": b"second",
    }
    expected = {
        name.removeprefix("wheels/"): "sha256:" + hashlib.sha256(data).hexdigest()
        for name, data in members.items()
    }
    if change == "missing":
        members.pop(next(iter(members)))
    elif change == "extra":
        members["experiment.json"] = b"{}"
    elif change == "traversal":
        members["wheels/../escape.whl"] = b"escape"
    elif change == "case":
        members["wheels/One-1-py3-none-any.whl"] = b"first"
    elif change == "wheel-digest":
        members[next(iter(members))] = b"other"
    output = io.BytesIO()
    compression = zipfile.ZIP_STORED if change == "crc" else zipfile.ZIP_DEFLATED
    with zipfile.ZipFile(output, "w", compression=compression) as archive:
        for name, data in members.items():
            if change == "symlink" and name == next(iter(members)):
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(info, data)
            else:
                archive.writestr(name, data)
        if change == "duplicate":
            with pytest.warns(UserWarning, match="Duplicate"):
                archive.writestr(next(iter(members)), b"first")
    data = output.getvalue()
    if change == "crc":
        data = data.replace(b"first", b"other", 1)
    elif change == "truncated":
        data = data[:20]
    path = tmp_path / "transport.zip"
    path.write_bytes(data)
    digest = "sha256:" + hashlib.sha256(data).hexdigest()
    return path, expected, digest


def retain(root, source, expected, digest, **overrides):
    return snapshots.open_wheel_bundle_snapshot(
        root,
        source,
        expected,
        bundle_sha256=digest,
        **(
            {
                "max_bundle_bytes": source.stat().st_size,
                "max_wheels": 2,
                "max_archive_bytes": 6,
                "max_total_bytes": 11,
                "max_snapshot_bytes": 22,
                "deadline": time.monotonic() + 10,
            }
            | overrides
        ),
    )


def test_bundle_yields_existing_snapshot_with_independent_custody(tmp_path):
    source, expected, digest = bundle(tmp_path)
    root = tmp_path / "private"
    root.mkdir()
    with retain(root, source, expected, digest) as snapshot:
        assert isinstance(snapshot, snapshots.WheelSnapshot)
        assert snapshot.digests == expected
        assert {name: stream.read() for name, stream in snapshot.archives.items()} == {
            "one-1-py3-none-any.whl": b"first",
            "two-1-py3-none-any.whl": b"second",
        }
        source.unlink()
        for path in (root / "bundle").iterdir():
            path.write_bytes(b"modified intermediate")
        snapshot.verify()
    assert all(stream.closed for stream in snapshot.archives.values())


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "extra",
        "traversal",
        "case",
        "wheel-digest",
        "symlink",
        "duplicate",
        "transport-digest",
        "crc",
        "truncated",
    ],
)
def test_bundle_refuses_inexact_or_unsafe_transport(tmp_path, change):
    source, expected, digest = bundle(tmp_path, change=change)
    root = tmp_path / "private"
    root.mkdir()
    if change == "transport-digest":
        digest = "sha256:" + "0" * 64
    with pytest.raises(snapshots.WheelError):
        with retain(root, source, expected, digest):
            pytest.fail("unselected transport admitted")


@pytest.mark.parametrize(
    "option,value",
    [
        ("max_wheels", 1),
        ("max_archive_bytes", 5),
        ("max_total_bytes", 10),
        ("max_snapshot_bytes", 21),
        ("max_bundle_bytes", 1),
        ("max_wheels", True),
        ("max_bundle_bytes", 0),
        ("max_snapshot_bytes", -1),
        ("deadline", 0),
        ("deadline", float("inf")),
    ],
)
def test_bundle_obeys_inclusive_budgets_and_shared_deadline(tmp_path, option, value):
    source, expected, digest = bundle(tmp_path)
    root = tmp_path / "private"
    root.mkdir()
    with pytest.raises(snapshots.WheelError):
        with retain(root, source, expected, digest, **{option: value}):
            pytest.fail("over-budget transport admitted")


def test_bundle_never_extracts_before_digest_verification(tmp_path):
    source, expected, _digest = bundle(tmp_path)
    root = tmp_path / "private"
    root.mkdir()
    with pytest.raises(snapshots.WheelError, match="digest"):
        with retain(root, source, expected, "sha256:" + "0" * 64):
            pytest.fail("incorrect aggregate digest admitted")
    assert list(root.iterdir()) == []


def test_bundle_cannot_renew_deadline_during_member_copy(tmp_path, monkeypatch):
    source, expected, digest = bundle(tmp_path)
    root = tmp_path / "private"
    root.mkdir()
    deadline = time.monotonic() + 10
    original = snapshots._check_deadline

    def check(value):
        assert value == deadline
        if (root / "bundle" / "one-1-py3-none-any.whl").exists():
            raise snapshots.WheelError("expired shared deadline")
        original(value)

    monkeypatch.setattr(snapshots, "_check_deadline", check)
    with pytest.raises(snapshots.WheelError, match="expired shared deadline"):
        with retain(root, source, expected, digest, deadline=deadline):
            pytest.fail("expired member copy admitted")


def test_bundle_refuses_source_replacement_before_open(tmp_path, monkeypatch):
    source, expected, digest = bundle(tmp_path)
    root = tmp_path / "private"
    root.mkdir()
    original = snapshots._open

    def replaced(path):
        if path == source:
            replacement = tmp_path / "replacement.zip"
            replacement.write_bytes(source.read_bytes())
            source.replace(tmp_path / "original.zip")
            replacement.replace(source)
        return original(path)

    monkeypatch.setattr(snapshots, "_open", replaced)
    with pytest.raises(snapshots.WheelError, match="changed"):
        with retain(root, source, expected, digest):
            pytest.fail("replaced bundle source admitted")


@pytest.mark.parametrize("compression", [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED])
def test_bundle_refuses_hidden_output_beyond_forged_member_size(tmp_path, compression):
    source, expected, _digest = bundle(tmp_path)
    with zipfile.ZipFile(source, "w", compression=compression) as archive:
        archive.writestr("wheels/one-1-py3-none-any.whl", b"first" + b"hidden" * 1000)
        archive.writestr("wheels/two-1-py3-none-any.whl", b"second")
    data = bytearray(source.read_bytes())
    central = data.index(b"PK\x01\x02")
    crc = zipfile.crc32(b"first")
    for offset, checksum, size in ((0, 14, 22), (central, 16, 24)):
        struct.pack_into("<L", data, offset + checksum, crc)
        struct.pack_into("<L", data, offset + size, len(b"first"))
    source.write_bytes(data)
    digest = "sha256:" + hashlib.sha256(data).hexdigest()
    root = tmp_path / "private"
    root.mkdir()
    with pytest.raises(snapshots.WheelError):
        with retain(root, source, expected, digest):
            pytest.fail("forged prefix hid unselected decompressed bytes")


def test_bundle_bounds_actual_directory_before_zipfile_indexing(tmp_path, monkeypatch):
    source, expected, _digest = bundle(tmp_path, change="extra")
    data = bytearray(source.read_bytes())
    footer = data.rindex(b"PK\x05\x06")
    struct.pack_into("<2H", data, footer + 8, 2, 2)
    source.write_bytes(data)
    digest = "sha256:" + hashlib.sha256(data).hexdigest()
    root = tmp_path / "private"
    root.mkdir()

    def forbidden(*args, **kwargs):
        pytest.fail("ZipFile allocated its index before actual entry-count preflight")

    monkeypatch.setattr(zipfile, "ZipFile", forbidden)
    with pytest.raises(snapshots.WheelError, match="count"):
        with retain(root, source, expected, digest):
            pytest.fail("forged footer hid an extra central-directory entry")


def test_bundle_maps_malformed_utf8_names_to_wheel_error(tmp_path):
    source, expected, _digest = bundle(tmp_path)
    data = bytearray(source.read_bytes())
    central = data.index(b"PK\x01\x02")
    for offset, flags, name in ((0, 6, 30), (central, 8, 46)):
        struct.pack_into("<H", data, offset + flags, 0x800)
        data[offset + name] = 0xFF
    source.write_bytes(data)
    digest = "sha256:" + hashlib.sha256(data).hexdigest()
    root = tmp_path / "private"
    root.mkdir()
    with pytest.raises(snapshots.WheelError, match="invalid"):
        with retain(root, source, expected, digest):
            pytest.fail("invalid UTF-8 filename escaped the adapter")
