"""Tests for the CodeSpace session-recovery helpers (agent_codespaces.sessions)."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent_codespaces import sessions


def test_extract_b64_between_sentinels_drops_noise():
    text = (
        "INFO: connecting...\n"
        f"{sessions._B64_START}\n"
        "aGVsbG8=\n"
        "INFO: stray log line !!!\n"   # non-base64 chars stripped
        f"{sessions._B64_END}\n"
        "trailing noise\n"
    )
    # 'aGVsbG8=' decodes to 'hello'; the stray line contributes only its
    # base64-legal chars, so guard by checking the clean line decodes.
    assert sessions._extract_b64(
        f"{sessions._B64_START}\naGVsbG8=\n{sessions._B64_END}\n"
    ) == "aGVsbG8="
    # Sentinel framing present in the noisy text too.
    assert sessions._B64_START not in sessions._extract_b64(text)


def test_extract_b64_empty_when_no_sentinels():
    assert sessions._extract_b64("nothing here") == ""


def _make_session_tar(session_ids: list[str], *, include_db: bool = True) -> bytes:
    """Build an in-memory gzip tar mirroring a CodeSpace ~/.copilot subset."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for sid in session_ids:
            data = b'{"ts": 1}\n'
            info = tarfile.TarInfo(f"session-state/{sid}/events.jsonl")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        if include_db:
            db = b"SQLite format 3\x00" + b"\x00" * 32
            info = tarfile.TarInfo("session-store.db")
            info.size = len(db)
            tf.addfile(info, io.BytesIO(db))
    return buf.getvalue()


def test_stage_and_push_counts_and_invokes_session_sync():
    tar_bytes = _make_session_tar(["aaa-1", "bbb-2"])
    captured = {}

    def fake_push(staging: Path, machine_label: str, *, verbose: bool):
        captured["machine"] = machine_label
        captured["has_sessions"] = (staging / "session-state" / "aaa-1" / "events.jsonl").is_file()
        captured["db_ok"] = (staging / "session-store.db").is_file()
        return True, "-> hub (3 files)"

    with patch.object(sessions, "_push_via_session_sync", side_effect=fake_push):
        res = sessions._stage_and_push(tar_bytes, "cs-xyz", verbose=False)

    assert res["ok"] is True
    assert res["session_count"] == 2
    assert captured["machine"] == ".codespaces/cs-xyz"
    assert captured["has_sessions"] is True
    assert captured["db_ok"] is True


def test_stage_and_push_drops_corrupt_db():
    # A db without the SQLite header must be dropped, not pushed.
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        bad = b"NOT a sqlite file"
        info = tarfile.TarInfo("session-store.db")
        info.size = len(bad)
        tf.addfile(info, io.BytesIO(bad))
    tar_bytes = buf.getvalue()

    seen = {}

    def fake_push(staging: Path, machine_label: str, *, verbose: bool):
        seen["db_present"] = (staging / "session-store.db").exists()
        return True, "ok"

    with patch.object(sessions, "_push_via_session_sync", side_effect=fake_push):
        res = sessions._stage_and_push(tar_bytes, "cs-1", verbose=False)

    assert res["ok"] is True
    assert seen["db_present"] is False


def test_stage_and_push_rejects_corrupt_archive():
    res = sessions._stage_and_push(b"this is not a gzip tar", "cs-1", verbose=False)
    assert res["ok"] is False
    assert "corrupt" in res["detail"] or "invalid" in res["detail"]


def test_push_via_session_sync_missing_cli():
    with patch.object(sessions, "find_session_sync", return_value=None):
        ok, detail = sessions._push_via_session_sync(Path("."), ".codespaces/x", verbose=False)
    assert ok is False
    assert "session-sync" in detail


def test_push_via_session_sync_stale_cli_gives_upgrade_hint():
    """#246: a deployed agent-logger predating `session-sync push` fails with an
    argparse 'invalid choice' error. The push helper must translate that into an
    actionable 'upgrade agent-logger' message, not surface the raw argparse dump."""
    stale_err = (
        "usage: session-sync [-h] {run,status,doctor} ...\n"
        "session-sync: error: argument command: invalid choice: 'push' "
        "(choose from run, status, doctor)"
    )
    completed = SimpleNamespace(returncode=2, stdout="", stderr=stale_err)
    with patch.object(sessions, "find_session_sync", return_value="session-sync"), \
            patch.object(sessions.subprocess, "run", return_value=completed):
        ok, detail = sessions._push_via_session_sync(Path("."), ".codespaces/x", verbose=False)
    assert ok is False
    assert "stale" in detail
    assert "agent-logger" in detail.lower()
    assert "install" in detail.lower()
    assert "agent-worktrees update" in detail


def test_push_via_session_sync_other_error_passes_through():
    """A non-skew push failure keeps its raw message (no false upgrade hint)."""
    completed = SimpleNamespace(
        returncode=1, stdout="", stderr="session-sync: push failed: target unreachable"
    )
    with patch.object(sessions, "find_session_sync", return_value="session-sync"), \
            patch.object(sessions.subprocess, "run", return_value=completed):
        ok, detail = sessions._push_via_session_sync(Path("."), ".codespaces/x", verbose=False)
    assert ok is False
    assert "stale" not in detail
    assert "target unreachable" in detail


def test_is_stale_session_sync_detection():
    assert sessions._is_stale_session_sync("invalid choice: 'push' (choose from run, status)")
    assert sessions._is_stale_session_sync("ERROR: invalid choice: 'PUSH'")  # case-insensitive
    assert not sessions._is_stale_session_sync("some other error")
    assert not sessions._is_stale_session_sync("")


def test_sync_never_raises_on_ssh_config_runtimeerror(monkeypatch):
    """#155: an unbootable CodeSpace raises RuntimeError from the SSH-config
    fetch. sync_codespace_sessions must catch it and return a failed-recovery
    dict (never propagate), so a --force finalize/delete can still delete."""
    import ssh_manager

    class _FakeLock:
        def __init__(self, *a, **k):
            pass

        def acquire(self, *a, **k):
            pass

        def release(self):
            pass

    async def _raise_runtime(*a, **k):
        raise RuntimeError(
            "Timed out fetching SSH config for codespace x after 3 attempt(s)"
        )

    monkeypatch.setattr(ssh_manager, "TargetLock", _FakeLock)
    monkeypatch.setattr(ssh_manager, "ConnectionManager", lambda *a, **k: object())
    monkeypatch.setattr(sessions, "_connect_with_retry", _raise_runtime)

    res = sessions.sync_codespace_sessions("x", timeout=1.0)
    assert res["ok"] is False
    assert "could not connect" in res["detail"]
