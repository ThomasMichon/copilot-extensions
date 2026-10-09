"""Tests for ``Target.push``'s optional ``source_identity`` admission gate.

See the ``session-intelligence-and-accounting`` effort's identity-admission
interface: a ``source_identity`` argument requests destination
identity-admission; the default ``None`` preserves every existing caller's
behavior unchanged (covered by ``test_sync.py``'s existing push tests, none
of which pass this argument).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent_logger.sync.targets.filesystem import LocalTarget
from agent_logger.sync.targets.ingest import IngestTarget
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
