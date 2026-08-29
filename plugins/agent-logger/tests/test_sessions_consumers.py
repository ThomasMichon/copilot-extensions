"""WS1b: archived-session resolution in the collate/ramp_up consumers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_logger import sessions
from agent_logger.sessions import SessionRef, archive_session, materialize_path


def _make_session(state_root: Path, sid: str) -> Path:
    d = state_root / sid
    d.mkdir(parents=True)
    (d / "events.jsonl").write_text(
        '{"type":"session.start","data":{}}\n', encoding="utf-8"
    )
    (d / "workspace.yaml").write_text(f"id: {sid}\ncwd: C:/repo\n", encoding="utf-8")
    (d / "origin.json").write_text(json.dumps({"machine": "box"}), encoding="utf-8")
    return d


def test_materialize_path_live_is_identity(tmp_path: Path) -> None:
    src = _make_session(tmp_path / "session-state", "s1")
    ref = SessionRef(id="s1", kind="live", path=src)
    assert materialize_path(ref) == src


def test_materialize_path_archive_extracts(tmp_path: Path) -> None:
    src = _make_session(tmp_path / "session-state", "s1")
    ref = archive_session(src, tmp_path / "archived")
    d = materialize_path(ref)
    assert d != src
    assert (d / "events.jsonl").is_file()
    # registered for atexit cleanup
    assert str(d) in sessions._MATERIALIZED_TEMPS


def test_materialize_path_cleans_temp_on_extraction_failure(
    tmp_path: Path, monkeypatch
) -> None:
    # An archive whose filename matches no codec raises after mkdtemp -- the
    # temp dir must not leak, and nothing is registered for cleanup.
    bogus = tmp_path / "s1.bogus"
    bogus.write_bytes(b"x")
    ref = SessionRef(id="s1", kind="archive", path=bogus, store=tmp_path)

    created: list[str] = []
    real_mkdtemp = sessions.tempfile.mkdtemp

    def _spy(*a, **k):
        d = real_mkdtemp(*a, **k)
        created.append(d)
        return d

    monkeypatch.setattr(sessions.tempfile, "mkdtemp", _spy)
    before = list(sessions._MATERIALIZED_TEMPS)
    with pytest.raises(ValueError, match="no codec for archive"):
        materialize_path(ref)
    assert created, "mkdtemp should have been called"
    assert not Path(created[0]).exists()  # cleaned up, no leak
    assert sessions._MATERIALIZED_TEMPS == before


def test_collate_resolve_session_dir_finds_archive(tmp_path: Path, monkeypatch) -> None:
    from agent_logger.segmenter import collate

    copilot = tmp_path / "copilot"
    state = copilot / "session-state"
    src = _make_session(state, "arch-1")
    store = tmp_path / "archived"
    archive_session(src, store)
    # remove the live copy so only the archive remains
    import shutil

    shutil.rmtree(src)

    monkeypatch.setattr(collate, "find_copilot_dir", lambda: copilot)
    monkeypatch.setattr(collate, "session_archive_stores", lambda: [store])

    resolved = collate.resolve_session_dir("arch-1")
    assert (resolved / "events.jsonl").is_file()
    assert collate.read_workspace(resolved)["id"] == "arch-1"


def test_collate_resolve_session_dir_accepts_absolute_archive(tmp_path: Path) -> None:
    from agent_logger.segmenter import collate

    src = _make_session(tmp_path / "session-state", "arch-absolute")
    ref = archive_session(src, tmp_path / "archived")

    resolved = collate.resolve_session_dir(str(ref.path))

    assert (resolved / "events.jsonl").is_file()
    assert collate.read_workspace(resolved)["id"] == "arch-absolute"


def test_collate_resolve_missing_still_raises(tmp_path: Path, monkeypatch) -> None:
    from agent_logger.segmenter import collate

    copilot = tmp_path / "copilot"
    (copilot / "session-state").mkdir(parents=True)
    monkeypatch.setattr(collate, "find_copilot_dir", lambda: copilot)
    monkeypatch.setattr(collate, "session_archive_stores", lambda: [tmp_path / "none"])
    with pytest.raises(FileNotFoundError):
        collate.resolve_session_dir("does-not-exist")


def test_ramp_up_session_dir_by_id_finds_archive(tmp_path: Path, monkeypatch) -> None:
    from agent_logger.segmenter import ramp_up

    copilot = tmp_path / "copilot"
    state = copilot / "session-state"
    src = _make_session(state, "arch-2")
    store = tmp_path / "archived"
    archive_session(src, store)
    import shutil

    shutil.rmtree(src)

    monkeypatch.setattr(ramp_up, "_session_state_root", lambda: state)
    monkeypatch.setattr(ramp_up, "session_archive_stores", lambda: [store])

    d = ramp_up._session_dir_by_id("arch-2")
    assert d is not None
    assert (d / "events.jsonl").is_file()


def test_ramp_up_session_dir_by_id_prefers_live(tmp_path: Path, monkeypatch) -> None:
    from agent_logger.segmenter import ramp_up

    state = tmp_path / "copilot" / "session-state"
    src = _make_session(state, "live-1")
    monkeypatch.setattr(ramp_up, "_session_state_root", lambda: state)
    monkeypatch.setattr(ramp_up, "session_archive_stores", lambda: [tmp_path / "none"])
    assert ramp_up._session_dir_by_id("live-1") == src
    assert ramp_up._session_dir_by_id("missing") is None
