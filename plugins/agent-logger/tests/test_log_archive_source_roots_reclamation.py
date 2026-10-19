"""Synthetic post-publication orchestration; native deletion is never mocked."""

from __future__ import annotations

import hashlib
import os
import time
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import BinaryIO

import pytest

from agent_logger.sync import log_reclamation as reclamation
from agent_logger.sync.archive_directory import ArchiveDirectory
from agent_logger.sync.log_archive_publication import LogArchivePublicationResult
from agent_logger.sync.log_archive_readback import LogArchiveRevision, ProcessLogReadback
from agent_logger.sync.log_archives import LogArchiveSettings
from agent_logger.sync.log_reclamation_policy import LogReclamationSettings
from agent_logger.sync.log_retirement_file import RetirementFile

pytestmark = [pytest.mark.guard, pytest.mark.contract("agent_logger.process_logs.reclamation")]
native = pytest.mark.skipif(os.name != "nt", reason="native exact-entry retirement sequence")
NAME = "process-1000-123.log"
CONTENT = b"preserved process evidence\n"
REVISION = LogArchiveRevision(NAME, hashlib.sha256(CONTENT).hexdigest(), len(CONTENT))
SETTINGS = LogReclamationSettings(True, 60, ("local", "onedrive"))
ARCHIVES = LogArchiveSettings(True, True, 1)


def _zip(root: Path, content: bytes = CONTENT) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / REVISION.archive_name
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(NAME, content)
    return path


def _prepare(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, tuple[ProcessLogReadback, ...]]:
    source = tmp_path / "source"
    _zip(source)
    (source / NAME).write_bytes(CONTENT)
    os.utime(source / NAME, (1, 1))
    # Producer classification is controlled; native sharing/disposition is real.
    monkeypatch.setattr(reclamation, "producer_exited", lambda pid: True)
    return source, (
        ProcessLogReadback("local", tmp_path / "first"),
        ProcessLogReadback("onedrive", tmp_path / "second"),
    )


def _run(
    source: Path, targets: tuple[ProcessLogReadback, ...], *,
    settings: LogReclamationSettings = SETTINGS,
    archives: LogArchiveSettings = ARCHIVES,
    policy_guard: reclamation.PolicyGuard = nullcontext,
    dry_run: bool = False,
) -> reclamation.LogReclamationResult:
    return reclamation.reclaim_process_logs(
        source, settings=settings, archives=archives, process_logs_enabled=True,
        active_target="local", targets=targets, policy_guard=policy_guard, dry_run=dry_run,
    )


def test_source_roots_reclamation_disabled_has_no_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, targets = _prepare(tmp_path, monkeypatch)
    result = _run(source, targets, settings=LogReclamationSettings())
    assert result == reclamation.LogReclamationResult()
    assert (source / NAME).read_bytes() == CONTENT
    assert all(target.directory is not None and not target.directory.exists() for target in targets)


@pytest.mark.parametrize("settings", [
    LogReclamationSettings(True, None, ("local", "onedrive")),
    LogReclamationSettings(True, True, ("local", "onedrive")),
    LogReclamationSettings(True, 0, ("local", "onedrive")),
    LogReclamationSettings(True, 60, ()),
    LogReclamationSettings(True, 60, ("local", "local")),
    LogReclamationSettings(True, 60, ("onedrive",)),
])
def test_source_roots_reclamation_invalid_policy_fails_before_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, settings: LogReclamationSettings,
) -> None:
    source, targets = _prepare(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        _run(source, targets, settings=settings)
    assert (source / NAME).read_bytes() == CONTENT
    assert all(target.directory is not None and not target.directory.exists() for target in targets)


@pytest.mark.parametrize("kind", ["missing", "unsupported", "duplicate", "source"])
def test_source_roots_reclamation_incomplete_target_set_is_not_a_quorum(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    source, targets = _prepare(tmp_path, monkeypatch)
    if kind == "missing":
        targets = targets[:1]
    else:
        directory = {
            "unsupported": None, "duplicate": targets[0].directory, "source": source,
        }[kind]
        targets = (targets[0], ProcessLogReadback("onedrive", directory))
    with pytest.raises(ValueError):
        _run(source, targets, dry_run=True)
    assert (source / NAME).read_bytes() == CONTENT


@pytest.mark.skipif(os.name == "nt", reason="POSIX has no demonstrated retirement primitive")
def test_source_roots_reclamation_posix_refuses_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, targets = _prepare(tmp_path, monkeypatch)
    with pytest.raises(OSError, match="requires native Windows"):
        _run(source, targets)
    assert (source / NAME).read_bytes() == CONTENT
    assert all(target.directory is not None and not target.directory.exists() for target in targets)


@pytest.mark.parametrize("existing", [False, True])
def test_source_roots_reclamation_dry_run_is_side_effect_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing: bool,
) -> None:
    source, targets = _prepare(tmp_path, monkeypatch)
    if existing:
        for target in targets:
            assert target.directory is not None
            _zip(target.directory)
    before = {str(path.relative_to(tmp_path)): path.read_bytes()
              for path in tmp_path.rglob("*") if path.is_file()}

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("dry run attempted publication, retirement, mkdir or durability writes")

    monkeypatch.setattr(os, "mkdir", forbidden)
    monkeypatch.setattr(os, "fsync", forbidden)
    monkeypatch.setattr(reclamation, "publish_required_log_archives", forbidden)
    monkeypatch.setattr(reclamation, "retirement_file", forbidden)
    result = _run(source, targets, dry_run=True)
    assert (result.inspected, result.eligible, result.retained, result.retired) == (1, 1, 1, 0)
    assert result.reclaimed_bytes == result.committed_unconfirmed == 0
    assert bool(result.failed) == (not existing or os.name != "nt")
    assert before == {str(path.relative_to(tmp_path)): path.read_bytes()
                      for path in tmp_path.rglob("*") if path.is_file()}


@pytest.mark.parametrize("kind", ["recent", "future", "quiet", "active", "unclassified", "pid"])
def test_source_roots_reclamation_eligibility_never_inherits_a_retention_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    source, targets = _prepare(tmp_path, monkeypatch)
    raw = source / NAME
    archives = ARCHIVES
    if kind == "recent":
        os.utime(raw, (time.time() - 30, time.time() - 30))
    elif kind == "future":
        os.utime(raw, (time.time() + 3600, time.time() + 3600))
    elif kind == "quiet":
        archives = LogArchiveSettings(True, True, 600)
        os.utime(raw, (time.time() - 120, time.time() - 120))
    elif kind == "active":
        monkeypatch.setattr(reclamation, "producer_exited", lambda pid: False)
    else:
        raw.rename(source / ("process-unclassified.log" if kind == "unclassified" else "process-1-0.log"))
    result = _run(source, targets, archives=archives, dry_run=True)
    assert (result.inspected, result.eligible, result.retained, result.retired) == (1, 0, 1, 0)
    assert not result.failed
    assert all(target.directory is not None and not target.directory.exists() for target in targets)


@native
def test_source_roots_reclamation_two_targets_retire_only_exact_raw_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, targets = _prepare(tmp_path, monkeypatch)
    for name in ["other.log", NAME + ".gz", "other.zip"]:
        (source / name).write_bytes(b"unrelated evidence")
    result = _run(source, targets)
    assert (result.inspected, result.eligible, result.retained, result.retired) == (1, 1, 0, 1)
    assert result.reclaimed_bytes == len(CONTENT) and not result.failed
    assert result.committed_unconfirmed == 0
    assert not (source / NAME).exists()
    assert (source / REVISION.archive_name).exists()
    for target in targets:
        assert target.verify(REVISION).verified
        assert target.directory is not None
        assert sorted(path.name for path in target.directory.iterdir()) == [REVISION.archive_name]
    assert all((source / name).read_bytes() == b"unrelated evidence"
               for name in ["other.log", NAME + ".gz", "other.zip"])
    assert _run(source, targets).inspected == 0


@native
@pytest.mark.parametrize("kind", ["local_missing", "local_corrupt", "target_corrupt", "offline"])
def test_source_roots_reclamation_missing_or_bad_evidence_retains_raw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    source, targets = _prepare(tmp_path, monkeypatch)
    if kind == "local_missing":
        (source / REVISION.archive_name).rename(source / "parked.zip")
    elif kind == "local_corrupt":
        _zip(source, b"x" * len(CONTENT))
    elif kind == "target_corrupt":
        assert targets[1].directory is not None
        _zip(targets[1].directory, b"x" * len(CONTENT))
    else:
        assert targets[1].directory is not None
        targets[1].directory.write_bytes(b"offline")
    result = _run(source, targets)
    assert (result.eligible, result.retained, result.retired) == (1, 1, 0)
    assert len(result.failed) == 1 and result.reclaimed_bytes == 0
    assert (source / NAME).read_bytes() == CONTENT
    assert str(tmp_path) not in repr(result)


@native
@pytest.mark.parametrize("kind", ["policy", "producer"])
def test_source_roots_reclamation_final_guard_can_cancel_after_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    source, targets = _prepare(tmp_path, monkeypatch)
    entered = []

    @contextmanager
    def cancel() -> Iterator[None]:
        entered.append(True)
        assert all(target.verify(REVISION).verified for target in targets)
        if kind == "policy":
            raise ValueError("synthetic policy change")
        monkeypatch.setattr(reclamation, "producer_exited", lambda pid: False)
        yield

    result = _run(source, targets, policy_guard=cancel)
    assert entered == [True] and len(result.failed) == 1
    assert result.retained == 1 and result.retired == result.reclaimed_bytes == 0
    assert (source / NAME).read_bytes() == CONTENT
    assert all(target.verify(REVISION).verified for target in targets)


@native
def test_source_roots_reclamation_holds_every_replacement_through_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, targets = _prepare(tmp_path, monkeypatch)
    checked = []

    @contextmanager
    def probe() -> Iterator[None]:
        for root in [source, *(target.directory for target in targets)]:
            assert root is not None
            path = root / REVISION.archive_name
            with pytest.raises(OSError):
                with path.open("r+b"):
                    pytest.fail("verified replacement admitted a writer")
            with pytest.raises(OSError):
                root.rename(tmp_path / "moved")
        checked.append(True)
        yield

    result = _run(source, targets, policy_guard=probe)
    assert checked == [True] and result.retired == 1 and not result.failed
    for target in targets:
        assert target.directory is not None
        with (target.directory / REVISION.archive_name).open("r+b"):
            pass
    source.rename(tmp_path / "released")


@native
def test_source_roots_reclamation_guard_ack_failure_never_claims_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, targets = _prepare(tmp_path, monkeypatch)

    @contextmanager
    def failed_acknowledgement() -> Iterator[None]:
        yield
        raise OSError("synthetic acknowledgement failure")

    result = _run(source, targets, policy_guard=failed_acknowledgement)
    assert result.retired == result.retained == result.reclaimed_bytes == 0
    assert result.committed_unconfirmed == 1 and len(result.failed) == 1
    assert "retirement committed, acknowledgement failed" in result.failed[0]
    assert not (source / NAME).exists()
    assert (source / REVISION.archive_name).exists()
    assert all(target.verify(REVISION).verified for target in targets)


@native
@pytest.mark.parametrize("change", ["append", "rewrite", "truncate"])
def test_source_roots_reclamation_old_valid_copies_cannot_authorize_new_raw_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    source, targets = _prepare(tmp_path, monkeypatch)
    for target in targets:
        assert target.directory is not None
        _zip(target.directory)
    raw = source / NAME
    before = raw.stat()
    content = {
        "append": CONTENT + b"late", "rewrite": b"x" * len(CONTENT), "truncate": CONTENT[:2],
    }[change]
    raw.write_bytes(content)
    os.utime(raw, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert all(target.verify(REVISION).verified for target in targets)
    result = _run(source, targets)
    assert result.retained == 1 and result.retired == result.reclaimed_bytes == 0
    assert len(result.failed) == 1 and raw.read_bytes() == content
    assert all(target.verify(REVISION).verified for target in targets)


@native
def test_source_roots_reclamation_replacement_before_exclusive_open_is_retained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, targets = _prepare(tmp_path, monkeypatch)
    original = reclamation.retirement_file

    @contextmanager
    def replaced(root: ArchiveDirectory, name: str) -> Iterator[RetirementFile]:
        (root.path / name).rename(root.path / "parked.log")
        (root.path / name).write_bytes(CONTENT)
        os.utime(root.path / name, (1, 1))
        with original(root, name) as held:
            yield held

    monkeypatch.setattr(reclamation, "retirement_file", replaced)
    result = _run(source, targets)
    assert result.retained == 1 and result.retired == 0 and len(result.failed) == 1
    assert (source / NAME).read_bytes() == CONTENT
    assert (source / "parked.log").read_bytes() == CONTENT
    assert all(target.directory is not None and not target.directory.exists() for target in targets)


@native
def test_source_roots_reclamation_hardlinked_raw_is_never_retired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, targets = _prepare(tmp_path, monkeypatch)
    alias = source / "retained.log"
    os.link(source / NAME, alias)
    result = _run(source, targets)
    assert result.retained == 1 and result.retired == 0 and len(result.failed) == 1
    assert alias.read_bytes() == (source / NAME).read_bytes() == CONTENT


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink candidate rejection")
def test_source_roots_reclamation_redirected_raw_is_retained_without_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, targets = _prepare(tmp_path, monkeypatch)
    outside = tmp_path / "outside.log"
    outside.write_bytes(CONTENT)
    (source / NAME).rename(source / "parked.log")
    (source / NAME).symlink_to(outside)
    result = _run(source, targets, dry_run=True)
    assert result.retained == 1 and result.retired == result.eligible == 0
    assert len(result.failed) == 1 and outside.read_bytes() == CONTENT


def test_source_roots_reclamation_dry_run_rejects_replacement_before_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, targets = _prepare(tmp_path, monkeypatch)
    original = ArchiveDirectory.open

    def replaced(
        root: ArchiveDirectory, name: str, *, protected: bool = False,
    ) -> BinaryIO:
        if name == NAME:
            (source / name).rename(source / "parked.log")
            (source / name).write_bytes(CONTENT)
        return original(root, name, protected=protected)

    monkeypatch.setattr(ArchiveDirectory, "open", replaced)
    result = _run(source, targets, dry_run=True)
    assert result.retained == 1 and result.retired == 0 and len(result.failed) == 1
    assert (source / NAME).read_bytes() == (source / "parked.log").read_bytes() == CONTENT
    assert all(target.directory is not None and not target.directory.exists() for target in targets)


@native
def test_source_roots_reclamation_rechecks_target_bytes_after_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, targets = _prepare(tmp_path, monkeypatch)
    original = reclamation.publish_required_log_archives

    def corrupted(
        root: Path, selected: tuple[ProcessLogReadback, ...],
        revision: LogArchiveRevision, *, dry_run: bool = False,
    ) -> tuple[LogArchivePublicationResult, ...]:
        for target in selected:
            assert target.directory is not None
            with pytest.raises(OSError):
                target.directory.rename(tmp_path / "moved")
        with pytest.raises(OSError):
            with (source / revision.archive_name).open("r+b"):
                pytest.fail("local replacement was not held during publication")
        results = original(root, selected, revision, dry_run=dry_run)
        assert all(item.verified for item in results)
        assert selected[1].directory is not None
        _zip(selected[1].directory, b"x" * len(CONTENT))
        return results

    monkeypatch.setattr(reclamation, "publish_required_log_archives", corrupted)
    result = _run(source, targets)
    assert result.retained == 1 and result.retired == result.reclaimed_bytes == 0
    assert len(result.failed) == 1 and (source / NAME).read_bytes() == CONTENT
    assert targets[0].verify(REVISION).verified and not targets[1].verify(REVISION).verified


def test_source_roots_reclamation_binds_source_root_before_target_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, targets = _prepare(tmp_path, monkeypatch)
    original = reclamation.archive_directory

    @contextmanager
    def replaced(path: Path) -> Iterator[ArchiveDirectory]:
        source.rename(tmp_path / "parked")
        _zip(source)
        (source / NAME).write_bytes(CONTENT)
        with original(path) as root:
            yield root

    monkeypatch.setattr(reclamation, "archive_directory", replaced)
    with pytest.raises(ValueError, match="root changed after target resolution"):
        _run(source, targets, dry_run=True)
    assert (source / NAME).read_bytes() == (tmp_path / "parked" / NAME).read_bytes() == CONTENT
    assert all(target.directory is not None and not target.directory.exists() for target in targets)


@pytest.mark.skipif(os.name == "nt", reason="POSIX intentionally relocated ancestor")
def test_source_roots_reclamation_rejects_source_alias_change_before_pin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, targets = _prepare(tmp_path, monkeypatch)
    alias = tmp_path / "alias"
    alias.symlink_to(source.parent, target_is_directory=True)
    requested = alias / source.name
    other = tmp_path / "other"
    _zip(other / source.name)
    (other / source.name / NAME).write_bytes(CONTENT)
    original = reclamation.archive_directory

    @contextmanager
    def changed(path: Path) -> Iterator[ArchiveDirectory]:
        alias.rename(tmp_path / "old-alias")
        alias.symlink_to(other, target_is_directory=True)
        with original(path) as root:
            yield root

    monkeypatch.setattr(reclamation, "archive_directory", changed)
    with pytest.raises(ValueError, match="source root changed"):
        _run(requested, targets, dry_run=True)
    assert (source / NAME).read_bytes() == CONTENT
    assert (other / source.name / NAME).read_bytes() == CONTENT
