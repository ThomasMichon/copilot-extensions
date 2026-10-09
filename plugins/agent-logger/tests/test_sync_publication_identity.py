"""Tests for ``Target.push``'s optional ``source_identity`` admission gate.

See the ``session-intelligence-and-accounting`` effort's identity-admission
interface: a ``source_identity`` argument requests destination
identity-admission; the default ``None`` preserves every existing caller's
behavior unchanged (covered by ``test_sync.py``'s existing push tests, none
of which pass this argument).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from agent_logger.sync.targets.filesystem import LocalTarget
from agent_logger.sync.targets.ingest import IngestTarget
from agent_logger.sync.targets.publication_admission import (
    MAX_MARKER_BYTES,
    PUBLICATION_IDENTITY_MARKER,
    check_publication_identity,
)
from agent_logger.sync.targets.ssh import SshTarget


@dataclass
class _Identity:
    """Minimal stand-in satisfying ``SourceIdentityLike`` structurally --
    no import of the (not-yet-landed) real ``source_publication.SourceIdentity``
    dataclass is needed or taken here."""

    provider: str
    host: str
    repository: str
    venue: str


def _claim_worker(dest_str: str, host: str, barrier, queue) -> None:
    """Module-level (picklable) worker for a real cross-process claim race.

    Runs in a genuinely separate OS process -- fcntl/msvcrt advisory locks
    (``sync_lock``) are per-process, so a same-process thread-only test
    would not exercise the actual contention this gate serializes.
    """
    from agent_logger.sync.targets.publication_admission import (
        check_publication_identity,
    )

    identity = _Identity(
        provider="github", host=host, repository="example", venue="codespace"
    )
    barrier.wait(timeout=30)
    result = check_publication_identity(Path(dest_str), identity)
    queue.put((host, result is None, None if result is None else result.detail))


def _make_source(root: Path) -> Path:
    src = root / "copilot"
    sess = src / "session-state" / "abc-123"
    sess.mkdir(parents=True)
    (sess / "events.jsonl").write_text('{"ts": 1}\n', encoding="utf-8")
    return src


def _push_worker(source: str, root: str, host: str, barrier, queue) -> None:
    barrier.wait(timeout=30)
    result = LocalTarget({"path": root}).push(
        Path(source), "m1", {"abc-123"},
        source_identity=_Identity("github", host, "example", "codespace"),
    )
    queue.put((host, result.ok, result.detail))


def test_push_tolerates_destination_created_after_missing_leaf_check(
    tmp_path: Path, monkeypatch,
) -> None:
    from agent_logger.sync.targets import filesystem

    source = _make_source(tmp_path)
    root = tmp_path / "dest"
    root.mkdir()
    leaf = root / "m1"
    original_lstat = filesystem._lstat
    injected = False

    def lstat_with_concurrent_creator(path):
        nonlocal injected
        try:
            return original_lstat(path)
        except FileNotFoundError:
            if path == leaf and not injected:
                leaf.mkdir()
                injected = True
            raise

    monkeypatch.setattr(filesystem, "_lstat", lstat_with_concurrent_creator)
    result = LocalTarget({"path": str(root)}).push(
        source, "m1", source_identity=_Identity("github", "host-a", "example", "codespace")
    )
    assert injected
    assert result.ok, result.detail
    assert (leaf / "session-state" / "abc-123" / "events.jsonl").is_file()


@pytest.mark.parametrize("same_identity", [True, False])
def test_concurrent_end_to_end_pushes_admit_only_matching_identities(
    tmp_path: Path, same_identity: bool,
) -> None:
    import multiprocessing

    source = _make_source(tmp_path)
    root = tmp_path / "dest"
    ctx = multiprocessing.get_context("spawn")
    barrier, queue = ctx.Barrier(2), ctx.Queue()
    hosts = ("host-a", "host-a" if same_identity else "host-b")
    processes = [
        ctx.Process(target=_push_worker, args=(str(source), str(root), host, barrier, queue))
        for host in hosts
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=60)
        assert process.exitcode == 0
    outcomes = [queue.get(timeout=5) for _ in processes]
    winners = [host for host, ok, _ in outcomes if ok]
    assert len(winners) == (2 if same_identity else 1), outcomes
    if not same_identity:
        assert "mismatch" in next(detail for _, ok, detail in outcomes if not ok)
    marker = json.loads((root / "m1" / PUBLICATION_IDENTITY_MARKER).read_text())
    assert marker["host"] in winners
    assert (root / "m1" / "session-state" / "abc-123" / "events.jsonl").is_file()


def test_filesystem_push_claims_empty_destination_and_writes_marker(
    tmp_path: Path,
) -> None:
    src = _make_source(tmp_path)
    dest_root = tmp_path / "dest"
    identity = _Identity(
        provider="github", host="source-host", repository="example", venue="codespace"
    )
    result = LocalTarget({"path": str(dest_root)}).push(
        src, "m1", source_identity=identity
    )
    assert result.ok
    marker = dest_root / "m1" / ".archive-source.json"
    assert marker.is_file()


def test_filesystem_push_is_idempotent_for_matching_identity(tmp_path: Path) -> None:
    src = _make_source(tmp_path)
    dest_root = tmp_path / "dest"
    identity = _Identity(
        provider="github", host="source-host", repository="example", venue="codespace"
    )
    target = LocalTarget({"path": str(dest_root)})
    first = target.push(src, "m1", source_identity=identity)
    second = target.push(src, "m1", source_identity=identity)
    assert first.ok
    assert second.ok


def test_filesystem_push_rejects_mismatched_identity(tmp_path: Path) -> None:
    src = _make_source(tmp_path)
    dest_root = tmp_path / "dest"
    target = LocalTarget({"path": str(dest_root)})
    first = target.push(
        src,
        "m1",
        source_identity=_Identity(
            provider="github", host="a", repository="r1", venue="codespace"
        ),
    )
    assert first.ok
    second = target.push(
        src,
        "m1",
        source_identity=_Identity(
            provider="github", host="b", repository="r1", venue="codespace"
        ),
    )
    assert not second.ok
    assert "mismatch" in second.detail


def test_filesystem_push_rejects_unowned_nonempty_leaf(tmp_path: Path) -> None:
    src = _make_source(tmp_path)
    dest_root = tmp_path / "dest"
    # First push with no identity -- ordinary legacy publication, no marker.
    target = LocalTarget({"path": str(dest_root)})
    legacy = target.push(src, "m1")
    assert legacy.ok
    # A later identity-admitted push to the same, now-nonempty destination
    # must refuse rather than silently adopt it.
    result = target.push(
        src,
        "m1",
        source_identity=_Identity(
            provider="github", host="a", repository="r1", venue="codespace"
        ),
    )
    assert not result.ok
    assert "unowned" in result.detail


def test_filesystem_push_with_no_identity_is_unaffected(tmp_path: Path) -> None:
    """``source_identity=None`` (the default) never touches the new marker."""
    src = _make_source(tmp_path)
    dest_root = tmp_path / "dest"
    result = LocalTarget({"path": str(dest_root)}).push(src, "m1")
    assert result.ok
    assert not (dest_root / "m1" / ".archive-source.json").exists()


def test_filesystem_push_never_copies_source_marker_over_ownership_claim(
    tmp_path: Path,
) -> None:
    """A source tree that itself contains a file named like the ownership
    marker must never overwrite the destination's real claim once admitted
    -- the marker name is reserved and excluded from the copy loop."""
    src = _make_source(tmp_path)
    # Forged/incidental marker-named file sitting in the source root.
    (src / ".archive-source.json").write_text(
        '{"provider": "forged", "host": "x", "repository": "y", "venue": "z"}',
        encoding="utf-8",
    )
    dest_root = tmp_path / "dest"
    identity = _Identity(
        provider="github", host="source-host", repository="example", venue="codespace"
    )
    result = LocalTarget({"path": str(dest_root)}).push(
        src, "m1", source_identity=identity
    )
    assert result.ok
    marker = dest_root / "m1" / ".archive-source.json"
    recorded = json.loads(marker.read_text(encoding="utf-8"))
    assert recorded["host"] == "source-host"
    assert recorded["provider"] == "github"


def test_filesystem_push_excludes_a_differently_cased_root_marker(
    tmp_path: Path,
) -> None:
    """A case-insensitive destination filesystem resolves any differently
    cased spelling of the marker name to the same real path -- the
    root-marker exclusion must match case-insensitively, not just the
    exact-case spelling."""
    src = _make_source(tmp_path)
    (src / ".ARCHIVE-SOURCE.JSON").write_text(
        '{"provider": "forged", "host": "x", "repository": "y", "venue": "z"}',
        encoding="utf-8",
    )
    dest_root = tmp_path / "dest"
    identity = _Identity(
        provider="github", host="source-host", repository="example", venue="codespace"
    )
    result = LocalTarget({"path": str(dest_root)}).push(
        src, "m1", source_identity=identity
    )
    assert result.ok
    marker = dest_root / "m1" / ".archive-source.json"
    recorded = json.loads(marker.read_text(encoding="utf-8"))
    assert recorded["host"] == "source-host"
    assert recorded["provider"] == "github"
    assert not (dest_root / "m1" / ".ARCHIVE-SOURCE.JSON").is_file()


def test_filesystem_push_preserves_a_nested_legitimately_named_file(
    tmp_path: Path,
) -> None:
    """The marker name is reserved only at the publication root -- a
    legitimately nested file sharing that basename (e.g. captured session
    content) must still be copied normally."""
    src = _make_source(tmp_path)
    nested = src / "session-state" / "abc-123" / ".archive-source.json"
    nested.write_text('{"captured": true}', encoding="utf-8")
    dest_root = tmp_path / "dest"
    identity = _Identity(
        provider="github", host="source-host", repository="example", venue="codespace"
    )
    result = LocalTarget({"path": str(dest_root)}).push(
        src, "m1", source_identity=identity
    )
    assert result.ok
    copied_nested = dest_root / "m1" / "session-state" / "abc-123" / ".archive-source.json"
    assert copied_nested.is_file()
    assert copied_nested.read_text(encoding="utf-8") == '{"captured": true}'


def test_publication_marker_refuses_a_symlink_at_the_marker_path(
    tmp_path: Path,
) -> None:
    """A symlink sitting at the marker path is never legitimate ownership
    state -- admission must refuse outright (never follow it to read/write
    through to wherever it points, even when the linked file holds valid,
    matching identity JSON), so a race that plants a symlink there can't
    redirect the claim write to an arbitrary file."""
    dest = tmp_path / "dest" / "m1"
    dest.mkdir(parents=True)
    identity = _Identity(
        provider="github", host="source-host", repository="example", venue="codespace"
    )
    outside_target = tmp_path / "outside-secret.json"
    outside_target.write_text(
        json.dumps(
            {
                "provider": identity.provider,
                "host": identity.host,
                "repository": identity.repository,
                "venue": identity.venue,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    try:
        (dest / PUBLICATION_IDENTITY_MARKER).symlink_to(outside_target)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    result = check_publication_identity(dest, identity)
    assert result is not None and not result.ok

    marker = dest / PUBLICATION_IDENTITY_MARKER
    assert marker.is_symlink()  # untouched -- never followed or replaced
    assert json.loads(outside_target.read_text(encoding="utf-8"))["host"] == "source-host"


def test_check_publication_identity_handles_lock_setup_errors(tmp_path: Path) -> None:
    """A lock-acquisition failure (e.g. an unsafe/unopenable lock path) must
    surface as a failing ``PushResult``, never an uncaught exception --
    ``FilesystemTarget.push`` promises ``PushResult`` on every path."""
    dest = tmp_path / "dest" / "m1"
    dest.mkdir(parents=True)
    lock_path = dest.parent / f".{dest.name}.publication-admission.lock"
    lock_path.mkdir()  # forces sync_lock's open to fail: not a regular file
    identity = _Identity(
        provider="github", host="source-host", repository="example", venue="codespace"
    )
    result = check_publication_identity(dest, identity)
    assert result is not None and not result.ok


def test_check_publication_identity_fails_closed_when_unsupported(
    tmp_path: Path,
) -> None:
    """A target with no cross-writer admission control (e.g. a cloud-synced
    OneDrive replica) must refuse a ``source_identity`` push outright rather
    than enforcing a lock that only ever coordinates writers on this host."""
    dest = tmp_path / "dest" / "m1"
    identity = _Identity(
        provider="github", host="source-host", repository="example", venue="codespace"
    )
    result = check_publication_identity(
        dest, identity, supports_identity_admission=False
    )
    assert result is not None and not result.ok
    assert not dest.exists()  # never even attempted a claim


def test_check_publication_identity_refuses_a_stale_looking_unowned_file(
    tmp_path: Path,
) -> None:
    """A filename pattern alone never establishes ownership."""
    dest = tmp_path / "dest" / "m1"
    dest.mkdir(parents=True)
    stale = dest / f".{PUBLICATION_IDENTITY_MARKER}.deadbeefdeadbeef.tmp"
    stale.write_text('{"provider": "orphaned"}', encoding="utf-8")

    identity = _Identity(
        provider="github", host="source-host", repository="example", venue="codespace"
    )
    result = check_publication_identity(dest, identity)
    assert result is not None and not result.ok
    assert "unowned" in result.detail
    assert stale.read_text(encoding="utf-8") == '{"provider": "orphaned"}'
    marker = dest / PUBLICATION_IDENTITY_MARKER
    assert not marker.exists()


def test_check_publication_identity_preserves_a_lookalike_unowned_file(
    tmp_path: Path,
) -> None:
    """Lookalike files must be preserved, and the leaf refused as unowned."""
    dest = tmp_path / "dest" / "m1"
    dest.mkdir(parents=True)
    lookalike = dest / f".{PUBLICATION_IDENTITY_MARKER}.notes.tmp"
    lookalike.write_text("someone else's file", encoding="utf-8")

    identity = _Identity(
        provider="github", host="source-host", repository="example", venue="codespace"
    )
    result = check_publication_identity(dest, identity)
    assert result is not None and not result.ok
    assert "unowned" in result.detail
    assert lookalike.is_file()
    assert lookalike.read_text(encoding="utf-8") == "someone else's file"


def test_matching_claim_does_not_authorize_deleting_stale_looking_files(
    tmp_path: Path,
) -> None:
    dest = tmp_path / "dest"
    identity = _Identity("github", "host-a", "example", "codespace")
    assert check_publication_identity(dest, identity) is None
    artifact = dest / f".{PUBLICATION_IDENTITY_MARKER}.deadbeefdeadbeef.tmp"
    artifact.write_bytes(b"unrelated preserved content")

    assert check_publication_identity(dest, identity) is None
    assert artifact.read_bytes() == b"unrelated preserved content"


@pytest.mark.parametrize("winner", ["different", "matching", "symlink"])
def test_marker_created_during_publication_is_never_replaced(
    tmp_path: Path, monkeypatch, winner: str,
) -> None:
    from agent_logger.sync.targets import publication_admission as admission

    dest = tmp_path / "dest"
    identity = _Identity("github", "host-a", "example", "codespace")
    marker = dest / PUBLICATION_IDENTITY_MARKER
    outside = tmp_path / "outside.json"
    recorded = {
        "provider": "github",
        "host": "host-a" if winner != "different" else "host-b",
        "repository": "example",
        "venue": "codespace",
    }
    payload = json.dumps(recorded).encode()
    original_publish = admission._publish_marker_no_replace

    def publish_after_racing_writer(temp_path: Path, marker_path: Path) -> None:
        if winner == "symlink":
            outside.write_bytes(payload)
            try:
                marker_path.symlink_to(outside)
            except OSError:
                pytest.skip("symlink creation is unavailable")
        else:
            marker_path.write_bytes(payload)
        original_publish(temp_path, marker_path)

    monkeypatch.setattr(admission, "_publish_marker_no_replace", publish_after_racing_writer)
    result = check_publication_identity(dest, identity)
    if winner == "matching":
        assert result is None
    else:
        assert result is not None and not result.ok
    assert marker.read_bytes() == payload
    assert marker.is_symlink() == (winner == "symlink")
    assert sorted(path.name for path in dest.iterdir()) == [PUBLICATION_IDENTITY_MARKER]


def test_claim_failure_cleans_only_its_own_temp_file(tmp_path: Path, monkeypatch) -> None:
    from agent_logger.sync.targets import publication_admission as admission

    dest = tmp_path / "dest"

    def fail_publish(temp_path: Path, marker_path: Path) -> None:
        assert temp_path.is_file()
        raise OSError("injected publication failure")

    monkeypatch.setattr(admission, "_publish_marker_no_replace", fail_publish)
    result = check_publication_identity(
        dest, _Identity("github", "host-a", "example", "codespace")
    )
    assert result is not None and not result.ok
    assert "injected publication failure" in result.detail
    assert not any(dest.iterdir())


def test_exclusive_temp_collision_never_deletes_preexisting_file(
    tmp_path: Path, monkeypatch,
) -> None:
    from agent_logger.sync.targets import publication_admission as admission

    dest = tmp_path / "dest"
    original_open = admission.os.open
    collided: list[Path] = []

    def open_after_collision(path, flags, mode=0o777, *, dir_fd=None):
        if flags & os.O_EXCL:
            entry = Path(path)
            entry.write_bytes(b"another writer's content")
            collided.append(entry)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(admission.os, "open", open_after_collision)
    result = check_publication_identity(
        dest, _Identity("github", "host-a", "example", "codespace")
    )
    assert result is not None and not result.ok
    assert len(collided) == 1
    assert collided[0].read_bytes() == b"another writer's content"


def test_windows_marker_move_is_write_through_without_replacement(
    tmp_path: Path, monkeypatch,
) -> None:
    import ctypes
    from types import SimpleNamespace

    from agent_logger.sync.targets import publication_admission as admission

    calls = []

    def move_file(source, destination, flags):
        calls.append((source, destination, flags))
        return True

    monkeypatch.setattr(
        ctypes, "WinDLL",
        lambda name, **kwargs: SimpleNamespace(MoveFileExW=move_file),
        raising=False,
    )
    monkeypatch.setattr(admission, "windows_extended_path", lambda path: f"extended:{path}")
    source, destination = tmp_path / "temp", tmp_path / "marker"
    admission._move_marker_no_replace_windows(source, destination)
    assert calls == [(f"extended:{source}", f"extended:{destination}", 0x00000008)]


@pytest.mark.skipif(os.name != "nt", reason="Native Windows lock opener")
def test_windows_lock_opener_uses_extended_path(tmp_path: Path, monkeypatch) -> None:
    import ctypes

    from agent_logger.sync import lock
    from agent_logger.sync.provenance import windows_extended_path

    original_dll = ctypes.WinDLL
    paths = []

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
        ctypes, "WinDLL",
        lambda name, **kwargs: KernelProxy(original_dll(name, **kwargs)),
    )
    target = tmp_path / "lock"
    with lock._open_lock_file(target):
        pass
    assert paths == [windows_extended_path(target)]


def test_onedrive_target_fails_closed_for_identity_admission(tmp_path: Path) -> None:
    """``OneDriveTarget`` inherits the filesystem admission gate but cannot
    serialize cross-writer publication (cloud sync, not this process) --
    ``Target.push`` must fail closed end-to-end, not just the helper."""
    from agent_logger.sync.targets.filesystem import OneDriveTarget

    src = _make_source(tmp_path)
    onedrive_root = tmp_path / "OneDrive"
    onedrive_root.mkdir()
    target = OneDriveTarget({"root": str(onedrive_root)})
    result = target.push(
        src,
        "m1",
        source_identity=_Identity(
            provider="github", host="a", repository="r1", venue="codespace"
        ),
    )
    assert not result.ok
    dest = onedrive_root / "Apps" / "agent-logger" / "sessions" / "m1"
    assert not (dest / ".archive-source.json").exists()
    if dest.exists():
        assert not any(dest.iterdir())  # empty: never copied, never claimed


def test_check_publication_identity_rejects_an_oversized_existing_marker(
    tmp_path: Path,
) -> None:
    """A pre-existing, destination-controlled marker larger than the bound
    must fail closed, never be read to EOF (the admission target is by
    definition a destination the caller does not yet own)."""
    dest = tmp_path / "dest" / "m1"
    dest.mkdir(parents=True)
    oversized = "x" * (MAX_MARKER_BYTES + 1)
    (dest / PUBLICATION_IDENTITY_MARKER).write_text(oversized, encoding="utf-8")

    identity = _Identity(
        provider="github", host="source-host", repository="example", venue="codespace"
    )
    result = check_publication_identity(dest, identity)
    assert result is not None and not result.ok
    assert "exceeds" in result.detail


def test_check_publication_identity_fails_closed_on_deeply_nested_marker(
    tmp_path: Path,
) -> None:
    """A destination-controlled marker can be deeply nested JSON within the
    size bound -- ``json.loads`` can raise ``RecursionError`` for that, which
    must still fail closed with a ``PushResult``, never escape as an
    uncaught exception."""
    dest = tmp_path / "dest" / "m1"
    dest.mkdir(parents=True)
    depth = 10_000
    nested = "[" * depth + "]" * depth
    assert len(nested) <= MAX_MARKER_BYTES
    (dest / PUBLICATION_IDENTITY_MARKER).write_text(nested, encoding="utf-8")

    identity = _Identity(
        provider="github", host="source-host", repository="example", venue="codespace"
    )
    result = check_publication_identity(dest, identity)
    assert result is not None and not result.ok


@pytest.mark.skipif(os.name != "nt", reason="Windows MAX_PATH regression")
def test_filesystem_push_claims_a_long_destination_path_with_identity(
    tmp_path: Path,
) -> None:
    """Admission's own filesystem operations (mkdir/scan/unlink/replace)
    must go through the same extended-path handling as the rest of the
    filesystem target, so a claim past Windows' MAX_PATH still lands."""
    src = _make_source(tmp_path)
    dest_root = tmp_path / ("d" * 48)
    machine_dir = "m" + ("x" * 220)
    identity = _Identity(
        provider="github", host="source-host", repository="example", venue="codespace"
    )
    result = LocalTarget({"path": str(dest_root)}).push(
        src, machine_dir, source_identity=identity
    )
    assert result.ok, result.detail
    assert len(str(dest_root / machine_dir)) >= 260
    from agent_logger.sync import provenance

    marker = dest_root / machine_dir / PUBLICATION_IDENTITY_MARKER
    with open(provenance._windows_extended_path(marker), encoding="utf-8") as f:
        assert json.loads(f.read())["host"] == "source-host"


def test_ssh_target_rejects_identity_admission() -> None:
    result = SshTarget({"host": "example.com", "remote_path": "/x"}).push(
        Path("/tmp/nonexistent-src"),
        "m1",
        source_identity=_Identity(
            provider="github", host="a", repository="r1", venue="codespace"
        ),
    )
    assert not result.ok
    assert "unsupported" in result.detail


def test_ingest_target_rejects_identity_admission() -> None:
    result = IngestTarget({"url": "rsync://example.com/mod"}).push(
        Path("/tmp/nonexistent-src"),
        "m1",
        source_identity=_Identity(
            provider="github", host="a", repository="r1", venue="codespace"
        ),
    )
    assert not result.ok
    assert "unsupported" in result.detail


def test_check_publication_identity_serializes_across_processes(
    tmp_path: Path,
) -> None:
    """Two real OS processes racing different identities against the same
    empty leaf must produce exactly one winner, with the marker matching
    that winner -- proving the OS-level lock contract, not just sequential
    in-process calls."""
    import multiprocessing

    dest = tmp_path / "dest" / "m1"
    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(2)
    queue: multiprocessing.Queue = ctx.Queue()
    procs = [
        ctx.Process(target=_claim_worker, args=(str(dest), host, barrier, queue))
        for host in ("host-a", "host-b")
    ]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(timeout=60)
        assert proc.exitcode == 0

    outcomes = [queue.get(timeout=5) for _ in range(2)]
    winners = [host for host, ok, _ in outcomes if ok]
    losers = [(host, detail) for host, ok, detail in outcomes if not ok]
    assert len(winners) == 1, outcomes
    assert len(losers) == 1, outcomes
    assert "mismatch" in losers[0][1]

    marker = dest / PUBLICATION_IDENTITY_MARKER
    assert marker.is_file()
    assert json.loads(marker.read_text(encoding="utf-8"))["host"] == winners[0]
