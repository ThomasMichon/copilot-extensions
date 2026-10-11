"""Settled-log ZIP snapshots preserve originals and reject unstable evidence."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import time
import zipfile
import zlib
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs

from agent_logger.config import Config
from agent_logger.process_logs import iter_process_log_refs
from agent_logger.sync import engine, log_archive_cli, log_archives
from agent_logger.sync.archive_directory import _posix_file_flags, archive_directory
from agent_logger.sync.targets.claim_file import create_claim_file

NAME = "process-1700000000000-2147483647.log"
PAYLOAD = b'{"type":"assistant_usage","tokens":42}\r\nfinal'


def _log(root: Path, name: str = NAME) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    path.write_bytes(PAYLOAD)
    old = time.time() - 601
    os.utime(path, (old, old))
    return path


def test_snapshots_are_readable_idempotent_and_preserve_revisions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _log(tmp_path)
    monkeypatch.setattr(log_archives, "producer_exited", lambda pid: True)
    result = log_archives.archive_settled_process_logs(tmp_path, min_quiet_seconds=600)
    assert result.archived == 1 and not result.failed
    assert path.read_bytes() == PAYLOAD
    archives = list(tmp_path.glob("*.zip"))
    assert len(archives) == 1
    if os.name != "nt":
        assert stat.S_IMODE(archives[0].stat().st_mode) == 0o600
    with zipfile.ZipFile(archives[0]) as archive:
        assert archive.namelist() == [NAME]
        assert archive.read(NAME) == PAYLOAD
    refs = list(iter_process_log_refs(tmp_path))
    assert len(refs) == 2 and {ref.logical_name for ref in refs} == {NAME}
    assert all("".join(ref.iter_lines()).encode() == PAYLOAD for ref in refs)
    retry = log_archives.archive_settled_process_logs(tmp_path, min_quiet_seconds=600)
    assert retry.unchanged == 1 and retry.archived == 0
    path.write_bytes(PAYLOAD + b"\nlate")
    os.utime(path, (time.time() - 601,) * 2)
    changed = log_archives.archive_settled_process_logs(tmp_path, min_quiet_seconds=600)
    assert changed.archived == 1 and len(list(tmp_path.glob("*.zip"))) == 2
    assert path.read_bytes() == PAYLOAD + b"\nlate"


def test_active_idle_recent_unknown_and_compressed_inputs_are_retained(tmp_path: Path) -> None:
    active = _log(tmp_path, f"process-1700000000000-{os.getpid()}.log")
    recent = _log(tmp_path)
    os.utime(recent, None)
    unknown = _log(tmp_path, "process-unknown.log")
    gzip = tmp_path / "process-old.log.gz"
    gzip.write_bytes(b"existing compressed evidence")
    result = log_archives.archive_settled_process_logs(tmp_path, min_quiet_seconds=600)
    assert (result.skipped_active, result.skipped_recent, result.skipped_unclassified) == (1, 1, 1)
    assert not result.failed and not list(tmp_path.glob("*.zip"))
    assert all(path.exists() for path in (active, recent, unknown, gzip))


def test_real_exited_pid_is_classified_without_signaling_processes() -> None:
    child = subprocess.Popen(
        [sys.executable, "-c", "pass"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        **no_window_kwargs(),
    )
    child.communicate(timeout=10)
    assert child.returncode == 0
    assert log_archives.producer_exited(child.pid)
    assert not log_archives.producer_exited(os.getpid())


@pytest.mark.parametrize("quiet", [True, 0, -1, 0.5, "600"])
def test_invalid_quiet_threshold_is_refused(tmp_path: Path, quiet: object) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        log_archives.archive_settled_process_logs(tmp_path, min_quiet_seconds=quiet)


@pytest.mark.parametrize("raw", [
    [], {"enabled": "true"}, {"enabled": True}, {"local_source": 1},
    {"min_quiet_seconds": 0}, {"min_quiet_seconds": True},
])
def test_invalid_or_nonlocal_policy_is_refused(raw: object) -> None:
    with pytest.raises(ValueError):
        log_archives.resolve_log_archive_settings(raw)


@pytest.mark.parametrize("dry_run", [False, True])
def test_missing_log_root_is_an_empty_pass(
    tmp_path: Path, dry_run: bool,
) -> None:
    root = tmp_path / "missing"
    assert log_archives.archive_settled_process_logs(
        root, min_quiet_seconds=600, dry_run=dry_run,
    ) == log_archives.LogArchiveResult()
    assert not root.exists()


@pytest.mark.parametrize("manual", [False, True])
def test_missing_log_root_does_not_fail_snapshot_commands(
    tmp_path: Path, manual: bool, capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    source = tmp_path / "copilot"
    (source / "session-state").mkdir(parents=True)
    cfg = Config({"sync": {
        "source": str(source), "targets": {"local": {"path": str(tmp_path / "dest")}},
        "process_logs": {"enabled": True, "zip_snapshots": {
            "enabled": True, "local_source": True,
        }},
    }, "machine": {"name": "sample-machine"}}, home)
    if manual:
        assert engine.run_compact_logs(cfg, dry_run=True) == 0
        assert engine.run_compact_logs(cfg, dry_run=False) == 0
    else:
        assert engine.run_sync(cfg) == 0
    assert "process-log ZIP archived 0" in capsys.readouterr().out
    assert not (source / "logs").exists()


@pytest.mark.parametrize("failure", [PermissionError, FileNotFoundError])
def test_root_open_errors_are_not_treated_as_initial_absence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: type[OSError],
) -> None:
    def failed_open(path: Path) -> object:
        raise failure("archive root open failed")

    monkeypatch.setattr(log_archives, "archive_directory", failed_open)
    with pytest.raises(failure, match="archive root open failed"):
        log_archives.archive_settled_process_logs(tmp_path, min_quiet_seconds=600)


def test_non_directory_log_root_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "logs"
    root.write_bytes(b"not a directory")
    with pytest.raises(ValueError, match="real directory"):
        log_archives.archive_settled_process_logs(root, min_quiet_seconds=600)


@pytest.mark.skipif(os.name == "nt", reason="POSIX capability refusal")
@pytest.mark.parametrize("flag", ["O_NOFOLLOW", "O_DIRECTORY", "O_NONBLOCK"])
def test_missing_safe_posix_flags_fail_explicitly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flag: str,
) -> None:
    monkeypatch.delattr(os, flag)
    with pytest.raises(OSError, match=f"safe archive I/O requires {flag}"):
        if flag == "O_NONBLOCK":
            _posix_file_flags()
        else:
            log_archives.archive_settled_process_logs(tmp_path, min_quiet_seconds=600)


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink fixture")
@pytest.mark.parametrize("dangling", [False, True])
def test_symlink_log_root_is_refused(tmp_path: Path, dangling: bool) -> None:
    target = tmp_path / "target"
    if not dangling:
        target.mkdir()
    root = tmp_path / "logs"
    root.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="real directory"):
        log_archives.archive_settled_process_logs(root, min_quiet_seconds=600)


def test_probe_failure_preserves_source_and_reports_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _log(tmp_path)

    def denied(pid: int) -> bool:
        raise PermissionError("producer probe denied")

    monkeypatch.setattr(log_archives, "producer_exited", denied)
    result = log_archives.archive_settled_process_logs(tmp_path, min_quiet_seconds=600)
    assert len(result.failed) == 1 and "probe denied" in result.failed[0]
    assert source.read_bytes() == PAYLOAD and not list(tmp_path.glob("*.zip"))


def test_unsupported_kernel_publication_has_no_named_temp_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _log(tmp_path)
    monkeypatch.setattr(log_archives, "producer_exited", lambda pid: True)

    def unsupported(*args: object, **kwargs: object) -> object:
        raise OSError("anonymous publication unsupported")

    monkeypatch.setattr(log_archives, "create_claim_file", unsupported)
    result = log_archives.archive_settled_process_logs(tmp_path, min_quiet_seconds=600)
    assert len(result.failed) == 1 and "unsupported" in result.failed[0]
    assert list(tmp_path.iterdir()) == [source]


@pytest.mark.parametrize("mutation", ["append", "truncate", "replace"])
def test_changes_during_compression_do_not_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str,
) -> None:
    path = _log(tmp_path)
    monkeypatch.setattr(log_archives, "producer_exited", lambda pid: True)
    verify = log_archives._verify_zip_stream

    def mutate(*args: object) -> None:
        verify(*args)
        if mutation == "append":
            with path.open("ab") as stream:
                stream.write(b"late")
        elif mutation == "truncate":
            path.write_bytes(b"truncated")
        else:
            replacement = tmp_path / "replacement"
            replacement.write_bytes(b"replacement")
            replacement.replace(path)

    monkeypatch.setattr(log_archives, "_verify_zip_stream", mutate)
    result = log_archives.archive_settled_process_logs(tmp_path, min_quiet_seconds=600)
    assert len(result.failed) == 1
    assert not list(tmp_path.glob("*.zip")) and not list(tmp_path.glob("*.tmp"))
    assert path.exists()


def test_corrupt_existing_archive_is_not_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _log(tmp_path)
    monkeypatch.setattr(log_archives, "producer_exited", lambda pid: True)
    log_archives.archive_settled_process_logs(tmp_path, min_quiet_seconds=600)
    archive = next(tmp_path.glob("*.zip"))
    archive.write_bytes(b"corrupt")
    result = log_archives.archive_settled_process_logs(tmp_path, min_quiet_seconds=600)
    assert len(result.failed) == 1 and archive.read_bytes() == b"corrupt"
    assert (tmp_path / NAME).read_bytes() == PAYLOAD


def test_manual_dry_run_has_no_archive_or_source_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    source = tmp_path / "copilot"
    path = _log(source / "logs")
    cfg = Config({"sync": {
        "source": str(source), "process_logs": {"zip_snapshots": {
            "enabled": True, "local_source": True,
        }},
    }}, home)
    monkeypatch.setattr(log_archives, "producer_exited", lambda pid: True)
    assert engine.run_compact_logs(cfg, dry_run=True) == 0
    assert path.read_bytes() == PAYLOAD and not list(path.parent.glob("*.zip"))
    assert engine.run_compact_logs(cfg, dry_run=False) == 0
    assert len(list(path.parent.glob("*.zip"))) == 1


@pytest.mark.parametrize("scoped", [False, True])
def test_scheduled_snapshot_precedes_transfer_and_obeys_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scoped: bool,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    source = tmp_path / "copilot"
    _log(source / "logs")
    (source / "session-state").mkdir()
    cfg = Config({"sync": {
        "source": str(source), "targets": {"local": {"path": str(tmp_path / "dest")}},
        "repo_allowlist": ["sample-repo"] if scoped else [],
        "process_logs": {"enabled": True, "zip_snapshots": {
            "enabled": True, "local_source": True,
        }},
    }, "machine": {"name": "sample-machine"}}, home)
    monkeypatch.setattr(log_archives, "producer_exited", lambda pid: True)
    assert engine.run_sync(cfg) == 0
    archives = list((source / "logs").glob("*.zip"))
    assert len(archives) == (0 if scoped else 1)
    if not scoped:
        delivered = tmp_path / "dest" / "sample-machine" / "logs"
        assert (delivered / archives[0].name).read_bytes() == archives[0].read_bytes()
        assert (delivered / NAME).read_bytes() == PAYLOAD


def test_scheduled_archive_error_still_transfers_raw_and_returns_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    source = tmp_path / "copilot"
    _log(source / "logs")
    (source / "session-state").mkdir()
    cfg = Config({"sync": {
        "source": str(source), "targets": {"local": {"path": str(tmp_path / "dest")}},
        "process_logs": {"enabled": True, "zip_snapshots": {
            "enabled": True, "local_source": True,
        }},
    }, "machine": {"name": "sample-machine"}}, home)

    def failed(*args: object, **kwargs: object) -> object:
        raise OSError("snapshot write denied")

    monkeypatch.setattr(log_archive_cli, "archive_settled_process_logs", failed)
    assert engine.run_sync(cfg) == 1
    assert (tmp_path / "dest" / "sample-machine" / "logs" / NAME).read_bytes() == PAYLOAD


@pytest.mark.parametrize("failure", ["compression", "deflate", "encrypted"])
def test_zip_decoder_failure_does_not_abort_scheduled_raw_transfer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    failure: str,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    source = tmp_path / "copilot"
    logs = source / "logs"
    _log(logs)
    (source / "session-state").mkdir()
    monkeypatch.setattr(log_archives, "producer_exited", lambda pid: True)
    assert log_archives.archive_settled_process_logs(logs, min_quiet_seconds=600).archived == 1
    archive_path = next(logs.glob("*.zip"))
    unsupported = bytearray(archive_path.read_bytes())
    central = unsupported.index(b"PK\x01\x02")
    expected: type[Exception]
    if failure == "compression":
        unsupported[8:10] = (99).to_bytes(2, "little")
        unsupported[central + 10:central + 12] = (99).to_bytes(2, "little")
        expected = NotImplementedError
    elif failure == "deflate":
        name_size = int.from_bytes(unsupported[26:28], "little")
        extra_size = int.from_bytes(unsupported[28:30], "little")
        unsupported[30 + name_size + extra_size] = 0x07
        expected = zlib.error
    else:
        unsupported[6:8] = (1).to_bytes(2, "little")
        unsupported[central + 8:central + 10] = (1).to_bytes(2, "little")
        expected = RuntimeError
    archive_path.write_bytes(unsupported)
    with zipfile.ZipFile(archive_path) as archive:
        with pytest.raises(expected):
            with archive.open(NAME) as member:
                member.read()
    other = _log(logs, "process-1700000000001-2147483647.log")
    cfg = Config({"sync": {
        "source": str(source), "targets": {"local": {"path": str(tmp_path / "dest")}},
        "process_logs": {"enabled": True, "zip_snapshots": {
            "enabled": True, "local_source": True,
        }},
    }, "machine": {"name": "sample-machine"}}, home)
    assert engine.run_sync(cfg) == 1
    assert "process-log ZIP failure" in capsys.readouterr().err
    delivered = tmp_path / "dest" / "sample-machine" / "logs"
    assert (delivered / NAME).read_bytes() == PAYLOAD
    assert (delivered / other.name).read_bytes() == PAYLOAD
    assert archive_path.read_bytes() == unsupported
    assert len(list(logs.glob("*.zip"))) == 2


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink/rename fixture")
def test_pinned_root_cannot_be_redirected_and_allows_relocated_ancestor(tmp_path: Path) -> None:
    actual = tmp_path / "actual"
    root = actual / "logs"
    _log(root)
    alias = tmp_path / "alias"
    alias.symlink_to(actual, target_is_directory=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    with archive_directory(alias / "logs") as pinned:
        actual.rename(tmp_path / "moved")
        actual.symlink_to(outside, target_is_directory=True)
        with create_claim_file(pinned.path, directory_fd=pinned.fd, mode=0o600) as snapshot:
            snapshot.stream.write(b"pinned")
            snapshot.publish(pinned.path / "verified.zip")
        with pinned.open("verified.zip") as stream:
            assert stream.read() == b"pinned"
    assert not list(outside.rglob("*"))
    assert (tmp_path / "moved" / "logs" / "verified.zip").read_bytes() == b"pinned"


@pytest.mark.skipif(os.name != "nt", reason="native Windows directory handle lifetime")
def test_windows_ancestors_remain_pinned_until_close(tmp_path: Path) -> None:
    root = tmp_path / "ancestor" / "logs"
    _log(root)
    with archive_directory(root):
        with pytest.raises(OSError):
            root.parent.rename(tmp_path / "renamed")
    root.parent.rename(tmp_path / "renamed")
    assert (tmp_path / "renamed" / "logs" / NAME).read_bytes() == PAYLOAD


@pytest.mark.skipif(os.name != "nt", reason="native Windows sharing and timestamp semantics")
def test_windows_protected_file_is_stable_until_close(tmp_path: Path) -> None:
    source = _log(tmp_path)
    with archive_directory(tmp_path) as pinned:
        selected = pinned.stat(NAME)
        with pinned.open(NAME, protected=True) as stream:
            assert log_archives._fingerprint(selected) == log_archives._fingerprint(
                os.fstat(stream.fileno())
            )
            with pytest.raises(OSError):
                source.write_bytes(b"changed")
            with pytest.raises(OSError):
                source.rename(tmp_path / "renamed.log")
    source.write_bytes(b"changed")
    assert source.read_bytes() == b"changed"
