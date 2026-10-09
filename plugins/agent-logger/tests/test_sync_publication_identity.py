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


def _make_source(root: Path) -> Path:
    src = root / "copilot"
    sess = src / "session-state" / "abc-123"
    sess.mkdir(parents=True)
    (sess / "events.jsonl").write_text('{"ts": 1}\n', encoding="utf-8")
    return src


def test_filesystem_push_claims_empty_destination_and_writes_marker(
    tmp_path: Path,
) -> None:
    src = _make_source(tmp_path)
    dest_root = tmp_path / "dest"
    identity = _Identity(
        provider="github", host="lambda-core", repository="example", venue="codespace"
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
        provider="github", host="lambda-core", repository="example", venue="codespace"
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
        provider="github", host="lambda-core", repository="example", venue="codespace"
    )
    result = LocalTarget({"path": str(dest_root)}).push(
        src, "m1", source_identity=identity
    )
    assert result.ok
    marker = dest_root / "m1" / ".archive-source.json"
    recorded = json.loads(marker.read_text(encoding="utf-8"))
    assert recorded["host"] == "lambda-core"
    assert recorded["provider"] == "github"


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
        provider="github", host="lambda-core", repository="example", venue="codespace"
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
        provider="github", host="lambda-core", repository="example", venue="codespace"
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
    assert json.loads(outside_target.read_text(encoding="utf-8"))["host"] == "lambda-core"


def test_check_publication_identity_handles_lock_setup_errors(tmp_path: Path) -> None:
    """A lock-acquisition failure (e.g. an unsafe/unopenable lock path) must
    surface as a failing ``PushResult``, never an uncaught exception --
    ``FilesystemTarget.push`` promises ``PushResult`` on every path."""
    dest = tmp_path / "dest" / "m1"
    dest.mkdir(parents=True)
    lock_path = dest.parent / f".{dest.name}.publication-admission.lock"
    lock_path.mkdir()  # forces sync_lock's open to fail: not a regular file
    identity = _Identity(
        provider="github", host="lambda-core", repository="example", venue="codespace"
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
        provider="github", host="lambda-core", repository="example", venue="codespace"
    )
    result = check_publication_identity(
        dest, identity, supports_identity_admission=False
    )
    assert result is not None and not result.ok
    assert not dest.exists()  # never even attempted a claim


def test_check_publication_identity_clears_a_stale_temp_marker(
    tmp_path: Path,
) -> None:
    """A temp-claim artifact left behind by a crashed/killed prior writer
    must not permanently block a later, legitimate admission attempt."""
    dest = tmp_path / "dest" / "m1"
    dest.mkdir(parents=True)
    stale = dest / f".{PUBLICATION_IDENTITY_MARKER}.deadbeefdeadbeef.tmp"
    stale.write_text('{"provider": "orphaned"}', encoding="utf-8")

    identity = _Identity(
        provider="github", host="lambda-core", repository="example", venue="codespace"
    )
    result = check_publication_identity(dest, identity)
    assert result is None
    assert not stale.exists()
    marker = dest / PUBLICATION_IDENTITY_MARKER
    assert marker.is_file()


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
        provider="github", host="lambda-core", repository="example", venue="codespace"
    )
    result = check_publication_identity(dest, identity)
    assert result is not None and not result.ok
    assert "exceeds" in result.detail


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
        provider="github", host="lambda-core", repository="example", venue="codespace"
    )
    result = LocalTarget({"path": str(dest_root)}).push(
        src, machine_dir, source_identity=identity
    )
    assert result.ok, result.detail
    assert len(str(dest_root / machine_dir)) >= 260
    from agent_logger.sync import provenance

    marker = dest_root / machine_dir / PUBLICATION_IDENTITY_MARKER
    with open(provenance._windows_extended_path(marker), encoding="utf-8") as f:
        assert json.loads(f.read())["host"] == "lambda-core"


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
