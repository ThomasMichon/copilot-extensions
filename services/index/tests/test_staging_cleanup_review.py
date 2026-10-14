"""Output/receipt review regressions separated from the full staging module."""

from __future__ import annotations

import os
import hashlib
import stat
from types import SimpleNamespace

import pytest
from test_staging import case as staging_case  # noqa: F401 - shared pytest fixture
from test_staging import original_run, stage, tree

from agent_index_service import staging
from agent_index_service.staging import CandidateError


@pytest.mark.parametrize("interrupt", [False, True])
@pytest.mark.parametrize("unlink_fails", [False, True])
def test_teardown_failure_always_attempts_secret_output_removal(
    staging_case, monkeypatch, interrupt, unlink_fails,  # noqa: F811 - imported fixture
):
    import agent_procutil

    error = KeyboardInterrupt() if interrupt else TypeError("unexpected Job failure")
    process = SimpleNamespace(pid=123, returncode=0, poll=lambda: 0)
    job = SimpleNamespace(
        terminate_and_wait=lambda seconds: (_ for _ in ()).throw(error),
    )
    output_paths = []

    def spawn(argv, **kwargs):
        kwargs["stdout"].write(b"credential-output-sentinel")
        kwargs["stdout"].flush()
        output_paths.append(kwargs["stdout"].name)
        return process, job

    monkeypatch.setattr(agent_procutil, "spawn_sync_in_kill_on_close_job", spawn)
    monkeypatch.setattr(staging, "_run", original_run)
    actual_unlink = staging.Path.unlink
    attempts = []

    def unlink(path, *args, **kwargs):
        if path.name.startswith(".build-output-"):
            attempts.append(path)
            if unlink_fails:
                raise OSError("fixture unlink failure")
        return actual_unlink(path, *args, **kwargs)

    monkeypatch.setattr(staging.Path, "unlink", unlink)
    with pytest.raises(staging._CleanupUnconfirmed) as raised:
        stage(staging_case)
    slot = staging_case.root / "versions" / "0.1.1.dev1"
    assert slot.exists()
    assert not (slot / ".install-complete.json").exists()
    assert attempts
    assert bool(list(slot.glob(".build-output-*"))) is unlink_fails
    assert "credential-output-sentinel" not in str(raised.value)
    if unlink_fails:
        assert "output cleanup unconfirmed" in str(raised.value)
        assert raised.value.__cause__.__context__ is not None
    else:
        assert raised.value.__cause__ is error


def test_receipt_creation_is_exclusive_private_at_open(tmp_path, monkeypatch):
    actual_open = staging.os.open
    calls = []

    def capture(path, flags, mode):
        calls.append((path, flags, mode))
        return actual_open(path, flags, mode)

    monkeypatch.setattr(staging.os, "open", capture)
    destination = tmp_path / "receipt.json"
    staging._write(destination, {"safe": True})
    assert len(calls) == 1
    assert calls[0][1] & os.O_EXCL
    assert calls[0][1] & os.O_CREAT
    assert calls[0][2] == 0o600  # Never manipulate process-wide umask.
    if os.name != "nt":
        assert stat.S_IMODE(destination.stat().st_mode) == 0o600


def test_copy_final_path_revalidation_retains_source_change_diagnostic(tmp_path, monkeypatch):
    source, target = tmp_path / "source.whl", tmp_path / "snapshot.whl"
    source.write_bytes(b"fixture")
    actual_path = staging._path
    checks = []

    def changed(path, **kwargs):
        checks.append(path)
        if len(checks) == 2:
            raise CandidateError("source path became a symlink")
        return actual_path(path, **kwargs)

    monkeypatch.setattr(staging, "_path", changed)
    with pytest.raises(CandidateError, match="release source changed while snapshotting"):
        staging._copy(source, target, hashlib.sha256(b"fixture").hexdigest())
    assert checks == [source, source]


@pytest.mark.parametrize("kind,label", [
    ("third_party_lock", "third-party lock must be UTF-8"),
    ("uv_config", "uv configuration must be UTF-8"),
])
def test_invalid_policy_encoding_is_specific_without_mutation(
    staging_case, kind, label,  # noqa: F811 - imported fixture
):
    getattr(staging_case.build, kind).write_bytes(b"\xff\xfeencoding-sentinel")
    with pytest.raises(CandidateError, match=label):
        stage(staging_case)
    assert not staging_case.root.exists()
    assert not staging_case.calls


@pytest.mark.parametrize("kind,label", [
    ("missing", "missing candidate receipt"),
    ("malformed", "invalid candidate receipt"),
    ("identity", "candidate identity conflict"),
])
def test_existing_completed_receipt_failure_is_contextual_and_preserved(
    staging_case, kind, label,  # noqa: F811 - imported fixture
):
    first = stage(staging_case)
    slot = staging.Path(first["slot"])
    if kind == "missing":
        (slot / "candidate.json").unlink()
    elif kind == "malformed":
        (slot / "candidate.json").write_text("{invalid")
    else:
        staging_case.build.uv_config.write_text('index-url = "https://different.invalid"')
    before = tree(slot)
    with pytest.raises(CandidateError, match=label) as raised:
        stage(staging_case)
    assert "0.1.1.dev1" in str(raised.value)
    assert str(slot) in str(raised.value)
    assert "different.invalid" not in str(raised.value)
    assert tree(slot) == before
    assert len(staging_case.calls) == 4
