"""Native exact-set retention controls, independent of admission authority."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libs" / "installer-engine"))
import installer_wheel_snapshot as snapshots
import installer_dependency_policy as policy


def inputs(tmp_path):
    paths = {}
    for name, data in (("one-1-py3-none-any.whl", b"first"), ("two-1-py3-none-any.whl", b"second")):
        path = tmp_path / name
        path.write_bytes(data)
        paths[name] = path
    hashes = {name: "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest() for name, path in paths.items()}
    return paths, hashes


def retain(paths, hashes, **options):
    return snapshots.retain_wheel_inputs(
        paths, hashes, **({
            "max_wheels": 2, "max_archive_bytes": 6, "max_total_bytes": 11,
            "max_snapshot_bytes": 11, "deadline": time.monotonic() + 10,
        } | options),
    )


@pytest.mark.parametrize("origin", ["fresh", "cache"])
def test_snapshot_is_exact_readonly_and_independent(tmp_path, origin):
    directory = tmp_path / origin
    directory.mkdir()
    paths, hashes = inputs(directory)
    with retain(paths, hashes) as snapshot:
        retained = tuple(snapshot.paths.values())
        assert set(snapshot.archives) == set(snapshot.paths) == set(hashes)
        assert sum(path.stat().st_size for path in retained) == 11
        for name, stream in snapshot.archives.items():
            assert not stream.writable()
            assert snapshot.paths[name].name == name
            assert snapshot.paths[name] != paths[name]
            assert stream.read() == paths[name].read_bytes()
            paths[name].write_bytes(b"caller changed")
        with pytest.raises(TypeError):
            snapshot.paths["other"] = retained[0]
        snapshot.verify()
    assert all(not path.exists() for path in retained)
    assert all(path.read_bytes() == b"caller changed" for path in paths.values())
    assert all(stream.closed for stream in snapshot.archives.values())


def test_snapshot_subset_borrows_original_handles_and_owner_lifetime(tmp_path):
    paths, hashes = inputs(tmp_path)
    selected = next(iter(paths))
    with retain(paths, hashes) as snapshot:
        subset = snapshot.subset((selected,))
        assert set(subset.paths) == {selected}
        assert subset.archives[selected] is snapshot.archives[selected]
        assert subset.paths[selected] == snapshot.paths[selected]
        assert subset.digests[selected] == hashes[selected]
        subset.verify()
        for invalid in ((), (selected, selected), ("missing.whl",), [selected], (1,)):
            with pytest.raises(ValueError, match="subset"):
                snapshot.subset(invalid)
    assert subset.archives[selected].closed
    assert not subset.paths[selected].exists()


@pytest.mark.parametrize("option,value,message", [
    ("max_wheels", 1, "count"), ("max_archive_bytes", 5, "archive"),
    ("max_total_bytes", 10, "total"), ("max_snapshot_bytes", 10, "storage"),
    ("max_wheels", True, "positive"), ("max_archive_bytes", 0, "positive"),
    ("max_total_bytes", -1, "positive"), ("max_snapshot_bytes", 1.5, "positive"),
    ("deadline", float("inf"), "deadline"), ("deadline", 0, "deadline"),
])
def test_snapshot_limits(tmp_path, option, value, message):
    paths, hashes = inputs(tmp_path)
    with pytest.raises(ValueError, match=message):
        with retain(paths, hashes, **{option: value}):
            pytest.fail("over-budget snapshot admitted")
    assert sorted(path.name for path in tmp_path.iterdir()) == sorted(paths)


@pytest.mark.parametrize("change", ["missing", "extra", "digest", "traversal", "case", "device"])
def test_snapshot_refuses_inexact_claims(tmp_path, change):
    paths, hashes = inputs(tmp_path)
    name = next(iter(paths))
    if change == "missing":
        hashes.pop(name)
    elif change == "extra":
        hashes["extra-1-py3-none-any.whl"] = hashes[name]
    elif change == "digest":
        hashes[name] = "sha256:" + "0" * 64
    else:
        alias = {"traversal": "../escape.whl", "case": name.capitalize(), "device": "NUL.whl"}[change]
        paths[alias] = paths[name]
        hashes[alias] = hashes[name]
    with pytest.raises(ValueError):
        with retain(paths, hashes, max_wheels=3, max_total_bytes=30, max_snapshot_bytes=30):
            pytest.fail("invalid snapshot admitted")


def test_snapshot_cancel_cleans_only_owned_paths(tmp_path):
    paths, hashes = inputs(tmp_path)
    retained = []
    with pytest.raises(KeyboardInterrupt):
        with retain(paths, hashes) as snapshot:
            retained.extend(snapshot.paths.values())
            raise KeyboardInterrupt
    assert all(not path.exists() for path in retained)
    assert all(path.exists() for path in paths.values())


def test_snapshot_detects_retained_mutation(tmp_path):
    paths, hashes = inputs(tmp_path)
    with pytest.raises((ValueError, PermissionError), match="changed|digest|denied"):
        with retain(paths, hashes) as snapshot:
            path = next(iter(snapshot.paths.values()))
            path.chmod(0o600)
            path.write_bytes(b"other")
    assert not path.exists()
    assert all(path.exists() for path in paths.values())


def test_snapshot_refuses_hardlinks(tmp_path):
    paths, hashes = inputs(tmp_path)
    os.link(next(iter(paths.values())), tmp_path / "alias")
    with pytest.raises(ValueError, match="unlinked"):
        with retain(paths, hashes):
            pytest.fail("hardlinked source admitted")


def test_snapshot_refuses_source_replacement_before_open(tmp_path, monkeypatch):
    paths, hashes = inputs(tmp_path)
    original = snapshots._open
    source = next(iter(paths.values()))

    def replaced(path):
        if path == source:
            replacement = tmp_path / "replacement"
            replacement.write_bytes(path.read_bytes())
            os.replace(replacement, path)
        return original(path)

    monkeypatch.setattr(snapshots, "_open", replaced)
    with pytest.raises(ValueError, match="changed"):
        with retain(paths, hashes):
            pytest.fail("replacement admitted")


def test_snapshot_refuses_mutation_of_earlier_source(tmp_path, monkeypatch):
    paths, hashes = inputs(tmp_path)
    first, second = paths.values()
    original = snapshots._open

    def changed(path):
        if path == second:
            first.write_bytes(b"other")
        return original(path)

    monkeypatch.setattr(snapshots, "_open", changed)
    with pytest.raises((ValueError, PermissionError), match="changed|denied"):
        with retain(paths, hashes):
            pytest.fail("earlier source changed before exact set was retained")


def test_snapshot_compares_change_time_only_between_handle_stats(tmp_path, monkeypatch):
    paths, hashes = inputs(tmp_path)
    original = os.fstat

    class HandleStat:
        def __init__(self, info):
            self.info = info
            self.st_ctime_ns = info.st_ctime_ns + 1

        def __getattr__(self, name):
            return getattr(self.info, name)

    # Windows 3.12 separates handle change time from path creation time.
    monkeypatch.setattr(
        snapshots, "_identity",
        lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns),
    )
    monkeypatch.setattr(snapshots.os, "fstat", lambda fd: HandleStat(original(fd)))
    with retain(paths, hashes) as snapshot:
        snapshot.verify()


@pytest.mark.skipif(os.name != "nt", reason="Windows read-sharing custody")
def test_windows_snapshot_denies_writes_and_replacement_but_allows_readers(tmp_path):
    paths, hashes = inputs(tmp_path)
    with retain(paths, hashes) as snapshot:
        path = next(iter(snapshot.paths.values()))
        subprocess.run(
            [sys.executable, "-I", "-S", "-c",
             "import pathlib,sys; assert pathlib.Path(sys.argv[1]).read_bytes()", str(path)],
            check=True, capture_output=True, timeout=10, creationflags=policy._HEADLESS,
        )
        snapshot.verify()
        with pytest.raises(PermissionError):
            path.chmod(0o600)
            path.write_bytes(b"other")
        replacement = tmp_path / "replacement"
        replacement.write_bytes(b"other")
        with pytest.raises(PermissionError):
            os.replace(replacement, path)
        snapshot.verify()


@pytest.mark.skipif(os.name != "nt", reason="Windows access-time metadata")
def test_windows_read_guard_does_not_treat_access_metadata_as_byte_mutation(tmp_path, monkeypatch):
    paths, hashes = inputs(tmp_path)
    original = os.fstat

    class AccessUpdate:
        def __init__(self, info):
            self.info = info
            self.st_birthtime_ns = getattr(info, "st_birthtime_ns", info.st_ctime_ns)
            self.st_ctime_ns = info.st_ctime_ns + 1

        def __getattr__(self, name):
            return getattr(self.info, name)

    with retain(paths, hashes) as snapshot:
        with monkeypatch.context() as patch:
            patch.setattr(snapshots.os, "fstat", lambda fd: AccessUpdate(original(fd)))
            snapshot.verify()


@pytest.mark.skipif(os.name != "nt", reason="Windows extended-length file opens")
def test_windows_read_guard_preserves_long_input_paths(tmp_path):
    directory = tmp_path / ("long-" + "x" * 180)
    directory.mkdir()
    paths, hashes = inputs(directory)
    assert all(len(str(path)) > 260 for path in paths.values())
    with retain(paths, hashes) as snapshot:
        snapshot.verify()


class Reader:
    def __init__(self, stream, read):
        self.stream, self.read = stream, read

    def __getattr__(self, name):
        return getattr(self.stream, name)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.stream.close()


@pytest.mark.parametrize("failure", ["grow", "copy", "eof-deadline"])
def test_snapshot_partial_failure_cleanup(tmp_path, monkeypatch, failure):
    paths, hashes = inputs(tmp_path)
    original = snapshots._open
    source = next(iter(paths.values()))
    private = policy._private_retained_root
    roots = []
    expired = False
    check = snapshots._check_deadline

    def root(parent):
        path = private(parent)
        roots.append(path)
        return path

    def deadline(value):
        if expired:
            raise ValueError("wheel inspection deadline exhausted")
        check(value)

    def opened(path):
        stream = original(path)
        if path != source:
            return stream

        def read(size):
            nonlocal expired
            if failure == "grow":
                with source.open("ab") as writer:
                    writer.write(b"overflow")
            if failure == "copy":
                raise OSError("synthetic read failure")
            data = stream.read(size)
            if not data:
                expired = True
            return data

        return Reader(stream, read)

    monkeypatch.setattr(policy, "_private_retained_root", root)
    monkeypatch.setattr(snapshots, "_open", opened)
    monkeypatch.setattr(snapshots, "_check_deadline", deadline)
    with pytest.raises((ValueError, OSError), match="budget|failure|deadline|denied"):
        with retain(paths, hashes):
            pytest.fail("partial copy admitted")
    assert roots and all(not path.exists() for path in roots)
    assert all(path.exists() for path in paths.values())


def test_snapshot_write_failure_preserves_inputs(tmp_path, monkeypatch):
    paths, hashes = inputs(tmp_path)
    original = Path.open
    retained = []

    class Output:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def write(self, data):
            self.stream.write(data[:1])
            raise OSError("synthetic storage failure")

    def opened(path, mode="r", *args, **kwargs):
        stream = original(path, mode, *args, **kwargs)
        if mode == "xb" and path.suffix == ".whl":
            retained.append(path)
            return Output(stream)
        return stream

    monkeypatch.setattr(Path, "open", opened)
    with pytest.raises(OSError, match="storage"):
        with retain(paths, hashes):
            pytest.fail("partial write admitted")
    assert retained and all(not path.exists() for path in retained)
    for name, path in paths.items():
        assert "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest() == hashes[name]


def test_snapshot_composes_with_complete_profile_verifier(tmp_path):
    from packaging.markers import default_environment
    from runtime_profile import SCHEMA, validate_profile
    from runtime_profile_wheels import verify_profile_wheels
    from wheel_input_fixture import wheel_bytes

    path = tmp_path / "demo_pkg-1.0-py3-none-any.whl"
    path.write_bytes(wheel_bytes(metadata=b"Metadata-Version: 2.4\nName: demo-pkg\nVersion: 1.0\n\n"))
    hashes = {path.name: "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()}
    profile = validate_profile({
        "schema": SCHEMA, "version": 1,
        "release": {"identity": "fixture-release", "sha256": "sha256:" + "2" * 64},
        "mode": "source", "artifact": None, "component": "index-engine", "backend": "cpu",
        "approval_source": "fixture-authority", "acquisition_policy": "fixture-policy",
        "target": {"device": "cpu", "environment": default_environment(), "tags": ["py3-none-any"]},
        "roots": ["demo-pkg==1.0"],
        "wheels": [{
            "name": "demo-pkg", "version": "1.0", "filename": path.name, "sha256": hashes[path.name],
            "requires_python": "", "provides_extras": [], "selected_extras": [], "requires_dist": [],
        }],
    })
    deadline = time.monotonic() + 10
    with retain(
        {path.name: path}, hashes, max_archive_bytes=10_000, max_total_bytes=10_000,
        max_snapshot_bytes=10_000, deadline=deadline,
    ) as snapshot:
        verify_profile_wheels(
            profile, snapshot.archives, max_archive_bytes=10_000,
            max_unpacked_bytes=10_000, max_metadata_bytes=10_000, deadline=deadline,
        )
        snapshot.verify()


def test_snapshot_bootstrap_has_no_site_dependencies(tmp_path):
    paths, hashes = inputs(tmp_path)
    code = """
import pathlib, sys, time
sys.path.insert(0, sys.argv[1])
from installer_wheel_snapshot import retain_wheel_inputs
paths = {sys.argv[2]: pathlib.Path(sys.argv[3])}
with retain_wheel_inputs(paths, {sys.argv[2]: sys.argv[4]}, max_wheels=1,
        max_archive_bytes=5, max_total_bytes=5, max_snapshot_bytes=5,
        deadline=time.monotonic()+10) as snapshot:
    assert snapshot.archives[sys.argv[2]].read() == b'first'
    owned = snapshot.paths[sys.argv[2]]
assert not owned.exists()
"""
    name = next(iter(paths))
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", code, str(Path(snapshots.__file__).parent),
         name, str(paths[name]), hashes[name]],
        capture_output=True, text=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
