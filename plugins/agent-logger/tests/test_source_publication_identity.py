"""Source-publication admission using real producer identities and metadata."""

from __future__ import annotations

import json
import multiprocessing
import os
from pathlib import Path

import pytest

from agent_logger.source_publication import load_source_identity_file
from agent_logger.source_roots import SourceIdentity, iter_archive_sources
from agent_logger.sync.targets import publication_admission as admission
from agent_logger.sync.targets.filesystem import LocalTarget, OneDriveTarget
from agent_logger.sync.targets.ingest import IngestTarget
from agent_logger.sync.targets.ssh import SshTarget


def _identity(provider: str = "copilot") -> SourceIdentity:
    return SourceIdentity("machine", provider, host="m1")


def _source(root: Path) -> Path:
    source = root / "copilot"
    session = source / "session-state" / "abc-123"
    session.mkdir(parents=True)
    (session / "events.jsonl").write_text('{"ts":1}\n', encoding="utf-8")
    return source


def _metadata(identity: SourceIdentity, aliases=()) -> bytes:
    return json.dumps({
        "schema_version": 1, **identity.to_dict(), "legacy_aliases": list(aliases),
    }).encode()


def _claim(dest: Path, identity: SourceIdentity | None = None, **kwargs):
    identity = _identity() if identity is None else identity
    return admission.check_publication_identity(
        dest, identity, publication_key=identity.namespace, **kwargs
    )


def _push_worker(source: str, root: str, provider: str, barrier, queue, full: bool) -> None:
    identity = _identity(provider)
    barrier.wait(timeout=30)
    if full:
        result = LocalTarget({"path": root}).push(
            Path(source), identity.namespace, {"abc-123"}, source_identity=identity
        )
        queue.put((provider, result.ok, result.detail))
    else:
        result = _claim(Path(root) / identity.namespace, identity)
        queue.put((provider, result is None, None if result is None else result.detail))


def _unfiltered_worker(source: str, root: str, paused, release, queue) -> None:
    from agent_logger.sync.targets import filesystem

    original_copy = filesystem._copy_replace
    stopped = False

    def copy_then_pause(src, dst):
        nonlocal stopped
        original_copy(src, dst)
        if paused is not None and "session-state" in dst.parts and not stopped:
            stopped = True
            paused.set()
            if not release.wait(timeout=15):
                raise OSError("test publisher was not released")

    filesystem._copy_replace = copy_then_pause
    result = LocalTarget({"path": root}).push(
        Path(source), "m1", source_identity=_identity()
    )
    queue.put((result.ok, result.detail))


@pytest.mark.parametrize("identity", [
    _identity(),
    SourceIdentity("container", "containers", host="source-host", venue_name="worker"),
    SourceIdentity("codespace", "github", repository="owner/repo", venue_name="box"),
])
def test_publication_identity_round_trip(tmp_path: Path, identity: SourceIdentity) -> None:
    source, root = _source(tmp_path), tmp_path / "archives"
    target = LocalTarget({"path": str(root)})
    first = target.push(source, identity.namespace, source_identity=identity)
    assert first.ok, first.detail
    marker = root / identity.namespace / admission.PUBLICATION_IDENTITY_MARKER
    assert load_source_identity_file(marker) == identity
    recorded = marker.read_bytes()
    assert target.push(source, identity.namespace, source_identity=identity).ok
    assert marker.read_bytes() == recorded
    discovered = list(iter_archive_sources(root))
    assert len(discovered) == 1
    assert discovered[0].key == identity.namespace
    assert discovered[0].identity == identity


@pytest.mark.parametrize("key", ["m1", "other.containers/worker"])
def test_publication_identity_rejects_new_noncanonical_key(tmp_path: Path, key: str) -> None:
    identity = SourceIdentity("container", "containers", host="host", venue_name="worker")
    root = tmp_path / "archives"
    result = LocalTarget({"path": str(root)}).push(
        _source(tmp_path), key, source_identity=identity
    )
    assert not result.ok
    assert "publication key" in result.detail
    assert not (root / key / admission.PUBLICATION_IDENTITY_MARKER).exists()


@pytest.mark.parametrize("key", ["m1", "legacy-host"])
def test_publication_identity_preserves_declared_aliases(tmp_path: Path, key: str) -> None:
    identity = _identity()
    root = tmp_path / "archives"
    dest = root / key
    dest.mkdir(parents=True)
    marker = dest / admission.PUBLICATION_IDENTITY_MARKER
    encoded = _metadata(identity, ["legacy-host"])
    marker.write_bytes(encoded)
    result = LocalTarget({"path": str(root)}).push(
        _source(tmp_path), key, source_identity=identity
    )
    assert result.ok, result.detail
    assert marker.read_bytes() == encoded
    source = list(iter_archive_sources(root))[0]
    assert source.identity == identity
    assert source.legacy_aliases == ("legacy-host",)


def test_publication_identity_rejects_undeclared_existing_alias(tmp_path: Path) -> None:
    dest = tmp_path / "wrong"
    dest.mkdir()
    marker = dest / admission.PUBLICATION_IDENTITY_MARKER
    encoded = _metadata(_identity(), ["legacy-host"])
    marker.write_bytes(encoded)
    result = admission.check_publication_identity(dest, _identity(), publication_key="wrong")
    assert result is not None and not result.ok
    assert "namespace/aliases" in result.detail
    assert marker.read_bytes() == encoded


def test_publication_identity_rejects_alias_in_incompatible_provider_group(tmp_path: Path) -> None:
    key = "host.containers/worker"
    dest = tmp_path / key
    dest.mkdir(parents=True)
    marker = dest / admission.PUBLICATION_IDENTITY_MARKER
    encoded = _metadata(_identity(), [key])
    marker.write_bytes(encoded)
    result = admission.check_publication_identity(dest, _identity(), publication_key=key)
    assert result is not None and not result.ok
    assert "venue kind" in result.detail
    assert marker.read_bytes() == encoded


def test_publication_identity_full_repository_collision(tmp_path: Path) -> None:
    first = SourceIdentity("codespace", "github", repository="owner-a/repo", venue_name="box")
    second = SourceIdentity("codespace", "github", repository="owner-b/repo", venue_name="box")
    assert first.namespace == second.namespace
    target = LocalTarget({"path": str(tmp_path / "archives")})
    source = _source(tmp_path)
    assert target.push(source, first.namespace, source_identity=first).ok
    result = target.push(source, second.namespace, source_identity=second)
    assert not result.ok
    assert "mismatch" in result.detail


def test_publication_identity_legacy_none_unchanged(tmp_path: Path) -> None:
    root = tmp_path / "archives"
    result = LocalTarget({"path": str(root)}).push(_source(tmp_path), "m1")
    assert result.ok
    assert not (root / "m1" / admission.PUBLICATION_IDENTITY_MARKER).exists()


def test_publication_identity_legacy_none_preserves_root_whitelist(tmp_path: Path) -> None:
    from agent_logger.sync.targets.base import is_session_path_included

    source = _source(tmp_path)
    marker = source / admission.PUBLICATION_IDENTITY_MARKER
    encoded = _metadata(_identity())
    marker.write_bytes(encoded)
    root = tmp_path / "archives"
    result = LocalTarget({"path": str(root)}).push(source, "m1")
    assert result.ok, result.detail
    assert not is_session_path_included(Path(admission.PUBLICATION_IDENTITY_MARKER), None)
    assert not (root / "m1" / admission.PUBLICATION_IDENTITY_MARKER).exists()
    assert marker.read_bytes() == encoded


@pytest.mark.parametrize("name", [
    "existing.txt",
    f".{admission.PUBLICATION_IDENTITY_MARKER}.deadbeefdeadbeef.tmp",
    f".{admission.PUBLICATION_IDENTITY_MARKER}.notes.tmp",
])
def test_publication_identity_refuses_unowned_content(tmp_path: Path, name: str) -> None:
    dest = tmp_path / "m1"
    dest.mkdir()
    content = dest / name
    content.write_bytes(b"not owned")
    result = _claim(dest)
    assert result is not None and not result.ok
    assert "unowned" in result.detail
    assert content.read_bytes() == b"not owned"
    assert not (dest / admission.PUBLICATION_IDENTITY_MARKER).exists()


def test_publication_identity_does_not_clean_unknown_claim_artifacts(tmp_path: Path) -> None:
    dest = tmp_path / "m1"
    assert _claim(dest) is None
    content = dest / f".{admission.PUBLICATION_IDENTITY_MARKER}.deadbeefdeadbeef.tmp"
    content.write_bytes(b"preserved")
    assert _claim(dest) is None
    assert content.read_bytes() == b"preserved"


@pytest.mark.parametrize("name", [".archive-source.json", ".ARCHIVE-SOURCE.JSON"])
def test_publication_identity_root_marker_excluded_but_nested_file_copied(
    tmp_path: Path, name: str,
) -> None:
    source, root = _source(tmp_path), tmp_path / "archives"
    (source / name).write_bytes(b"forged root marker")
    nested = source / "session-state" / "abc-123" / name
    nested.write_bytes(b"legitimate nested content")
    result = LocalTarget({"path": str(root)}).push(
        source, "m1", source_identity=_identity()
    )
    assert result.ok, result.detail
    assert load_source_identity_file(root / "m1" / admission.PUBLICATION_IDENTITY_MARKER) == _identity()
    assert (root / "m1" / "session-state" / "abc-123" / name).read_bytes() == nested.read_bytes()


@pytest.mark.parametrize("payload", [
    b"{}",
    b"null",
    b'{"schema_version":true}',
    b'{"schema_version":2}',
    b"[" * 10_000 + b"]" * 10_000,
    b"x" * (admission.MAX_MARKER_BYTES + 1),
    _metadata(_identity(), ["../escape"]),
])
def test_publication_identity_refuses_invalid_marker(tmp_path: Path, payload: bytes) -> None:
    dest = tmp_path / "m1"
    dest.mkdir()
    marker = dest / admission.PUBLICATION_IDENTITY_MARKER
    marker.write_bytes(payload)
    result = _claim(dest)
    assert result is not None and not result.ok
    assert marker.read_bytes() == payload


def test_publication_identity_refuses_marker_symlink(tmp_path: Path) -> None:
    dest = tmp_path / "m1"
    dest.mkdir()
    outside = tmp_path / "outside"
    outside.write_bytes(_metadata(_identity()))
    marker = dest / admission.PUBLICATION_IDENTITY_MARKER
    try:
        marker.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    result = _claim(dest)
    assert result is not None and not result.ok
    assert marker.is_symlink()
    assert outside.read_bytes() == _metadata(_identity())


@pytest.mark.parametrize("winner", ["matching", "different", "symlink"])
def test_publication_identity_never_replaces_raced_marker(tmp_path: Path, monkeypatch, winner) -> None:
    dest = tmp_path / "m1"
    marker = dest / admission.PUBLICATION_IDENTITY_MARKER
    identity = _identity("other" if winner == "different" else "copilot")
    payload = _metadata(identity)
    outside = tmp_path / "outside"
    original_publish = admission._publish_marker_no_replace

    def race(temp, final):
        if winner == "symlink":
            outside.write_bytes(payload)
            try:
                final.symlink_to(outside)
            except OSError:
                pytest.skip("symlinks unavailable")
        else:
            final.write_bytes(payload)
        original_publish(temp, final)

    monkeypatch.setattr(admission, "_publish_marker_no_replace", race)
    result = _claim(dest)
    assert (result is None) == (winner == "matching")
    assert marker.read_bytes() == payload
    assert marker.is_symlink() == (winner == "symlink")
    assert sorted(path.name for path in dest.iterdir()) == [admission.PUBLICATION_IDENTITY_MARKER]


def test_publication_identity_failed_write_cleans_created_temp(tmp_path: Path, monkeypatch) -> None:
    dest = tmp_path / "m1"

    def fail(temp, final):
        raise OSError("injected publication failure")

    monkeypatch.setattr(admission, "_publish_marker_no_replace", fail)
    result = _claim(dest)
    assert result is not None and not result.ok
    assert "injected publication failure" in result.detail
    assert not any(dest.iterdir())


def test_publication_identity_never_uses_pathname_cleanup(tmp_path: Path, monkeypatch) -> None:
    def forbidden(path, *args, **kwargs):
        raise AssertionError("claim cleanup must not unlink a pathname")

    monkeypatch.setattr(os, "unlink", forbidden)
    assert _claim(tmp_path / "m1") is None


@pytest.mark.parametrize("replacement", ["matching", "different", "symlink"])
def test_publication_identity_replaced_temp_cannot_admit_payload(
    tmp_path: Path, monkeypatch, replacement,
) -> None:
    outside = tmp_path / "replacement"
    provider = "other" if replacement == "different" else "copilot"
    payload = _metadata(_identity(provider))
    outside.write_bytes(payload)
    def race(claim, final):
        if replacement == "symlink":
            try:
                final.symlink_to(outside)
            except OSError:
                pytest.skip("symlinks unavailable")
        else:
            os.replace(outside, final)

    monkeypatch.setattr(admission, "_publish_marker_no_replace", race)
    root = tmp_path / "archives"
    result = LocalTarget({"path": str(root)}).push(
        _source(tmp_path), "m1", source_identity=_identity()
    )
    assert not result.ok
    assert not (root / "m1" / "session-state").exists()
    marker = root / "m1" / admission.PUBLICATION_IDENTITY_MARKER
    assert marker.read_bytes() == payload


def test_publication_identity_lock_setup_failure(tmp_path: Path) -> None:
    dest = tmp_path / "m1"
    admission._publication_lock_path(dest).mkdir()
    result = _claim(dest)
    assert result is not None and not result.ok
    assert "lock failed" in result.detail


def test_publication_identity_unsupported_helper_no_write(tmp_path: Path) -> None:
    dest = tmp_path / "m1"
    result = _claim(dest, supports_identity_admission=False)
    assert result is not None and not result.ok
    assert not dest.exists()


@pytest.mark.parametrize("target", [
    OneDriveTarget({"root": "/unused"}),
    SshTarget({"host": "example.com", "remote_path": "/unused"}),
    IngestTarget({"url": "rsync://example.com/module"}),
])
def test_publication_identity_unsupported_transport(tmp_path: Path, target, monkeypatch) -> None:
    if isinstance(target, OneDriveTarget):
        monkeypatch.setattr(target, "_root", lambda: tmp_path / "onedrive")
    result = target.push(_source(tmp_path), "m1", source_identity=_identity())
    assert not result.ok


def test_publication_identity_empty_scan_is_bounded(tmp_path: Path, monkeypatch) -> None:
    class Entries:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def __iter__(self):
            return self

        def __next__(self):
            if getattr(self, "visited", False):
                raise AssertionError("enumerated more than one child")
            self.visited = True
            return object()

    monkeypatch.setattr(admission.os, "scandir", lambda path: Entries())
    assert admission._destination_has_content(tmp_path)


def test_publication_identity_maximum_component(tmp_path: Path) -> None:
    from agent_logger.sync.provenance import windows_extended_path

    identity = SourceIdentity("machine", "copilot", host="m" * 255)
    root = tmp_path / "archives"
    result = LocalTarget({"path": str(root)}).push(
        _source(tmp_path), identity.namespace, source_identity=identity
    )
    assert result.ok, result.detail
    assert len(admission._publication_lock_path(root / identity.namespace).name.encode()) < 255
    marker = Path(windows_extended_path(root / identity.namespace / admission.PUBLICATION_IDENTITY_MARKER))
    assert load_source_identity_file(marker) == identity


def test_publication_identity_new_parents_flushed(tmp_path: Path, monkeypatch) -> None:
    from agent_logger.sync import provenance

    flushed = []
    original = provenance.fsync_directory

    def record(path):
        flushed.append(path)
        original(path)

    monkeypatch.setattr(provenance, "fsync_directory", record)
    identity = SourceIdentity("container", "containers", host="source-host", venue_name="worker")
    root = tmp_path / "archives"
    assert _claim(root / identity.namespace, identity) is None
    assert tmp_path in flushed
    assert root in flushed
    assert root / "source-host.containers" in flushed


def test_publication_identity_unsupported_barrier_fails_closed(tmp_path: Path, monkeypatch) -> None:
    import errno

    from agent_logger.sync import provenance

    def unsupported(path):
        raise OSError(errno.EINVAL, "directory fsync unsupported")

    monkeypatch.setattr(provenance, "fsync_directory", unsupported)
    legacy = tmp_path / "legacy"
    assert provenance.ensure_real_directory(legacy) == legacy
    result = _claim(tmp_path / "m1")
    assert result is not None and not result.ok
    assert "unsupported" in result.detail
    assert not (tmp_path / "m1" / admission.PUBLICATION_IDENTITY_MARKER).exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory durability barrier")
def test_publication_identity_retries_failed_post_link_barrier(tmp_path: Path, monkeypatch) -> None:
    import errno
    import stat

    dest = tmp_path / "m1"
    marker = dest / admission.PUBLICATION_IDENTITY_MARKER
    original = os.fsync
    failing = True
    failures = []

    def fail_marker_barrier(fd):
        info = os.fstat(fd)
        if failing and marker.exists() and stat.S_ISDIR(info.st_mode):
            directory = dest.stat()
            if (info.st_dev, info.st_ino) == (directory.st_dev, directory.st_ino):
                failures.append(fd)
                raise OSError(errno.EIO, "injected directory barrier failure")
        return original(fd)

    monkeypatch.setattr(os, "fsync", fail_marker_barrier)
    first = _claim(dest)
    assert first is not None and not first.ok
    assert marker.is_file()
    second = _claim(dest)
    assert second is not None and not second.ok
    assert len(failures) == 2
    failing = False
    assert _claim(dest) is None


def test_publication_identity_creation_interleaving(tmp_path: Path, monkeypatch) -> None:
    from agent_logger.sync.targets import filesystem

    root = tmp_path / "archives"
    root.mkdir()
    leaf = root / "m1"
    original = filesystem._lstat
    injected = []

    def race(path):
        try:
            return original(path)
        except FileNotFoundError:
            if path == leaf and not injected:
                leaf.mkdir()
                injected.append(path)
            raise

    monkeypatch.setattr(filesystem, "_lstat", race)
    result = LocalTarget({"path": str(root)}).push(
        _source(tmp_path), "m1", source_identity=_identity()
    )
    assert injected
    assert result.ok, result.detail


@pytest.mark.parametrize("full,same", [(False, False), (True, False), (True, True)])
def test_publication_identity_cross_process_serialization(tmp_path: Path, full, same) -> None:
    root, source = tmp_path / "archives", _source(tmp_path)
    ctx = multiprocessing.get_context("spawn")
    barrier, queue = ctx.Barrier(2), ctx.Queue()
    providers = ("copilot-a", "copilot-a" if same else "copilot-b")
    processes = [
        ctx.Process(target=_push_worker, args=(str(source), str(root), p, barrier, queue, full))
        for p in providers
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=60)
        assert process.exitcode == 0
    outcomes = [queue.get(timeout=5) for _ in processes]
    winners = [provider for provider, ok, _ in outcomes if ok]
    assert len(winners) == (2 if same else 1), outcomes
    if not same:
        assert "mismatch" in next(detail for _, ok, detail in outcomes if not ok)
    marker = root / "m1" / admission.PUBLICATION_IDENTITY_MARKER
    assert load_source_identity_file(marker).provider in winners


def test_publication_identity_lock_covers_unfiltered_payload_writes(tmp_path: Path) -> None:
    from agent_logger.sync.lock import sync_lock

    first, second = _source(tmp_path / "first"), _source(tmp_path / "second")
    for source, label, timestamp in ((first, "first", 100), (second, "second", 200)):
        session = source / "session-state" / "abc-123"
        (session / "events.jsonl").write_text(json.dumps({"snapshot": label}) + "\n")
        (session / "workspace.yaml").write_text(label)
        for path in session.iterdir():
            os.utime(path, (timestamp, timestamp))
    root = tmp_path / "archives"
    ctx = multiprocessing.get_context("spawn")
    paused, release, queue = ctx.Event(), ctx.Event(), ctx.Queue()
    publisher = ctx.Process(
        target=_unfiltered_worker, args=(str(first), str(root), paused, release, queue)
    )
    follower = ctx.Process(
        target=_unfiltered_worker, args=(str(second), str(root), None, release, queue)
    )
    publisher.start()
    try:
        assert paused.wait(timeout=15)
        with sync_lock(admission._publication_lock_path(root / "m1"), wait=False) as acquired:
            assert not acquired, "payload writer released its identity lock too early"
        follower.start()
    finally:
        release.set()
        publisher.join(timeout=15)
        if follower.pid is not None:
            follower.join(timeout=15)
    assert publisher.exitcode == follower.exitcode == 0
    outcomes = [queue.get(timeout=5) for _ in range(2)]
    assert all(ok for ok, _ in outcomes), outcomes
    session = root / "m1" / "session-state" / "abc-123"
    assert json.loads((session / "events.jsonl").read_text())["snapshot"] == "second"
    assert (session / "workspace.yaml").read_text() == "second"


@pytest.mark.skipif(os.name == "nt", reason="Linux anonymous-file backend")
def test_publication_identity_anonymous_file_has_no_temporary_name(tmp_path: Path) -> None:
    from agent_logger.sync.targets.claim_file import create_claim_file

    with create_claim_file(tmp_path) as claim:
        claim.stream.write(b"kernel-owned")
        claim.stream.flush()
        assert not any(tmp_path.iterdir())
        marker = tmp_path / "marker"
        expected = claim.file_id
        claim.publish(marker)
        info = marker.stat()
        assert (info.st_dev, info.st_ino) == expected
    assert marker.read_bytes() == b"kernel-owned"
    assert [path.name for path in tmp_path.iterdir()] == ["marker"]


@pytest.mark.skipif(os.name == "nt", reason="Linux anonymous-file backend")
def test_publication_identity_unsupported_anonymous_files_fail_closed(
    tmp_path: Path, monkeypatch,
) -> None:
    from agent_logger.sync.targets import claim_file

    monkeypatch.setattr(claim_file.os, "O_TMPFILE", 0)
    result = _claim(tmp_path / "m1")
    assert result is not None and not result.ok
    assert "unsupported" in result.detail


@pytest.mark.skipif(os.name != "nt", reason="Native Windows lock opener")
def test_publication_identity_windows_lock_path(tmp_path: Path, monkeypatch) -> None:
    import ctypes

    from agent_logger.sync import lock
    from agent_logger.sync.provenance import windows_extended_path

    original_dll, paths = ctypes.WinDLL, []

    class KernelProxy:
        def __init__(self, kernel):
            self.kernel = kernel

            def create_file(path, *args):
                paths.append(path)
                kernel.CreateFileW.argtypes = create_file.argtypes
                kernel.CreateFileW.restype = create_file.restype
                return kernel.CreateFileW(path, *args)

            self.CreateFileW = create_file

        def __getattr__(self, name):
            return getattr(self.kernel, name)

    monkeypatch.setattr(
        ctypes, "WinDLL", lambda name, **kwargs: KernelProxy(original_dll(name, **kwargs))
    )
    path = tmp_path / "lock"
    with lock._open_lock_file(path):
        pass
    assert paths == [windows_extended_path(path)]


@pytest.mark.skipif(os.name != "nt", reason="Native Windows MAX_PATH contract")
def test_publication_identity_windows_long_path(tmp_path: Path) -> None:
    identity = SourceIdentity("machine", "copilot", host="m" * 220)
    root = tmp_path / ("d" * 48)
    result = LocalTarget({"path": str(root)}).push(
        _source(tmp_path), identity.namespace, source_identity=identity
    )
    assert len(str(root / identity.namespace)) > 260
    assert result.ok, result.detail
