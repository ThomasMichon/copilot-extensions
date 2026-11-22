"""Tests for ``_build_active_paths`` mux liveness (picker Active-section signal).

``_build_active_paths`` must derive the set of worktree paths with a live mux
session from a SINGLE batched ``list-sessions`` snapshot on the hot path (one
subprocess for the whole fleet) rather than a ``has-session`` probe per
worktree. It degrades to the per-worktree probe only when the batch list is
unavailable (mux missing/blocked). See __main__._build_active_paths.
"""

from __future__ import annotations
from agent_worktrees.sessions import SessionContext

import builtins
import json
from pathlib import Path
from typing import Any

from agent_worktrees import tracking


from unittest.mock import patch

import pytest

from agent_worktrees import __main__ as cli


def _rec(wt_id: str, *, path: str) -> tracking.WorktreeRecord:
    return tracking.WorktreeRecord(
        worktree_id=wt_id,
        branch=f"worktree/{wt_id}",
        worktree_path=path,
        repo="owner/repo",
        machine="m",
        platform="wsl",
        started_at="2026-06-01T10:00:00",
        last_resumed_at="2026-06-01T10:00:00",
        resume_count=0,
        title=None,
        status="active",
        completed_at=None,
        sessions=[],
        prs=[],
        kind="session",
    )


def _empty_ctx() -> SessionContext:
    return SessionContext()


def test_active_paths_use_single_batched_list() -> None:
    """One ``_list_mux_sessions`` call classifies the whole fleet; the
    per-worktree ``has_mux_session`` probe is NOT used on the batch path."""
    recs = [_rec("aaaa", path="/tmp/a"), _rec("bbbb", path="/tmp/b"),
            _rec("cccc", path="/tmp/c")]
    batch = {"wt-aaaa": 1, "wt-cccc": 0}  # b has no live session

    with patch("agent_worktrees.sessions._list_mux_sessions",
               return_value=batch) as m_list, \
         patch("agent_worktrees.sessions.has_mux_session") as m_has:
        assert cli._build_active_paths(recs, session_ctx=_empty_ctx()) == set()
        active = cli._build_active_paths(recs, session_ctx=_empty_ctx(), copilot_only=False)

    assert active == {"/tmp/a", "/tmp/c"}
    m_list.assert_called_once()
    m_has.assert_not_called()  # batch path must not fall back to per-worktree


def test_active_paths_fall_back_when_batch_unavailable() -> None:
    """When ``_list_mux_sessions`` returns None (mux missing/blocked), degrade
    to the per-worktree ``has_mux_session`` probe rather than lose liveness."""
    recs = [_rec("aaaa", path="/tmp/a"), _rec("bbbb", path="/tmp/b")]

    def _has(wt_id: str) -> bool:
        return wt_id == "aaaa"

    with patch("agent_worktrees.sessions.platform.system", return_value="Linux"), \
         patch("agent_worktrees.sessions._list_mux_sessions", return_value=None), \
         patch("agent_worktrees.sessions.has_mux_session", side_effect=_has):
        active = cli._build_active_paths(recs, session_ctx=_empty_ctx(), copilot_only=False)

    assert active == {"/tmp/a"}


@pytest.mark.parametrize("hint", [None, False, True])
def test_windows_passive_classification_does_not_fall_back_to_psmux(hint: bool | None) -> None:
    rec = _rec("aaaa", path="/tmp/a")
    rec.mux_live = hint
    rec.mux_live_at = "2020-01-01T00:00:00"
    with patch("agent_worktrees.sessions.platform.system", return_value="Windows"), \
         patch("agent_worktrees.sessions._list_mux_sessions", return_value=None), \
         patch("agent_worktrees.sessions.has_mux_session") as probe:
        active = cli._build_active_paths([rec], session_ctx=_empty_ctx())
    assert active == set()
    probe.assert_not_called()


def test_active_paths_union_lock_and_mux() -> None:
    """Lock-file sessions and mux sessions both contribute; the result is their
    union (a worktree live by either signal is active)."""
    recs = [_rec("aaaa", path="/tmp/a"), _rec("bbbb", path="/tmp/b")]
    ctx = SessionContext(active_sessions={"/tmp/b": ["sid-1"]})

    with patch("agent_worktrees.sessions._list_mux_sessions",
               return_value={"wt-aaaa": 0}), \
         patch("agent_worktrees.sessions.has_mux_session") as m_has:
        assert cli._build_active_paths(recs, session_ctx=ctx) == {"/tmp/b"}
        active = cli._build_active_paths(recs, session_ctx=ctx, copilot_only=False)

    assert active == {"/tmp/a", "/tmp/b"}
    m_has.assert_not_called()


def test_active_paths_include_hosted_session_bindings() -> None:
    rec = _rec("aaaa", path="/tmp/a")
    rec.execution_leg = tracking.ExecutionLegBinding(
        provider="ahp",
        state="active",
        binding_revision=1,
        blob={"session_id": "11111111-1111-1111-1111-111111111111"},
    )

    with patch("agent_worktrees.sessions._list_mux_sessions", return_value={}):
        assert cli._build_active_paths([rec], session_ctx=_empty_ctx()) == set()
        active = cli._build_active_paths([rec], session_ctx=_empty_ctx(), copilot_only=False)

    assert active == {"/tmp/a"}


@pytest.mark.parametrize("signal", ["session_bridge_live", "session_ahp_live", "execution_leg_live"])
def test_legacy_picker_keeps_independent_host_liveness_when_mux_is_unknown(signal: str) -> None:
    from agent_worktrees.picker_support import derive

    assert derive._sess({"mux_observation_unavailable": True, signal: True}) == "HOST(?)"


@pytest.mark.parametrize("metadata", ["missing", "malformed", "unreadable"])
@pytest.mark.parametrize("other_live", [False, True])
def test_unknown_registered_cwd_is_not_confirmed_inactive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, metadata: str, other_live: bool,
) -> None:
    from agent_worktrees import sessions
    from agent_worktrees.picker_support import data_local, derive

    record = _rec("aaaa", path=str(tmp_path / "worktree"))
    record.sessions = [tracking.SessionEntry(session_id="unknown", started_at="")]
    record.git_state = "wip"
    entry = tmp_path / "unknown"
    entry.mkdir()
    (entry / "inuse.123.lock").touch()
    workspace = entry / "workspace.yaml"
    if metadata != "missing":
        workspace.write_text("cwd: [" if metadata == "malformed" else json.dumps(
            {"cwd": record.worktree_path},
        ), encoding="utf-8")
    if metadata == "unreadable":
        original = builtins.open

        def open_file(file: Any, *args: Any, **kwargs: Any) -> Any:
            if file == workspace:
                raise PermissionError("fixture")
            return original(file, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", open_file)
    if other_live:
        peer = tmp_path / "live"
        peer.mkdir()
        (peer / "inuse.456.lock").touch()
        (peer / "workspace.yaml").write_text(json.dumps({"cwd": record.worktree_path}), encoding="utf-8")
        record.sessions.append(tracking.SessionEntry(session_id="live", started_at=""))
    monkeypatch.setattr(sessions, "_session_state_dir", lambda: tmp_path)
    monkeypatch.setattr(sessions, "_is_copilot_process", lambda _pid: True)
    monkeypatch.setattr(sessions, "_list_mux_sessions", lambda: {})
    ctx = sessions.scan_sessions_fast([record])
    norm = sessions._normalize_path(record.worktree_path)
    assert ctx.unavailable_paths == {norm}
    assert cli._build_active_paths([record], ctx) == ({norm} if other_live else set())
    assert cli._build_active_paths([record], ctx, copilot_only=False) == {norm}
    published = cli._worktree_to_dict(record, session_ctx=ctx)
    assert published["session_lock_live"] is (True if other_live else None)
    assert published["session_lock_observation_unavailable"] is True
    assert published["ff_eligible"] is False
    cached: dict[str, Any] = {"id": "aaaa", "session_bound_live": True, "ff_eligible": True}
    data_local._overlay_cached_state(cached, record)
    assert cached["session_lock_live"] is (True if other_live else None)
    assert derive._state(cached) == ("ACTIVE" if other_live else "UNKNOWN")
    if not other_live:
        assert derive._ff_from_raw(cached) is False


@pytest.mark.parametrize("observed", [None, False, True])
def test_native_process_probe_availability_is_preserved_in_scoped_activity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, observed: bool | None,
) -> None:
    from agent_worktrees import session_liveness, sessions
    from agent_worktrees.picker_support import data_local

    record = _rec("aaaa", path=str(tmp_path / "worktree"))
    record.sessions = [tracking.SessionEntry(session_id="registered", started_at="")]
    entry = tmp_path / "registered"
    entry.mkdir()
    (entry / "inuse.123.lock").touch()
    (entry / "workspace.yaml").write_text(json.dumps({"cwd": record.worktree_path}), encoding="utf-8")
    monkeypatch.setattr(sessions, "_session_state_dir", lambda: tmp_path)
    monkeypatch.setattr(sessions, "_is_copilot_process", lambda _pid: False)
    monkeypatch.setattr(session_liveness, "copilot_pid_state", lambda _pid: observed)
    ctx = sessions.scan_sessions_fast([record])
    norm = sessions._normalize_path(record.worktree_path)
    assert ctx.unavailable_paths == ({norm} if observed is None else set())
    assert ctx.active_sessions == ({norm: ["registered"]} if observed else {})
    cached: dict[str, Any] = {}
    data_local._overlay_cached_state(cached, record)
    assert cached["session_lock_live"] is observed


@pytest.mark.parametrize("session_id", ["", "..", "../other", r"..\other"])
def test_scoped_lock_observation_never_enumerates_a_root_or_foreign_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, session_id: str,
) -> None:
    from agent_worktrees import sessions
    from agent_worktrees.push_timeout import ObservationUnavailable

    with monkeypatch.context() as scoped:
        scoped.setattr("os.scandir", lambda *_a: pytest.fail("invalid identity reached directory enumeration"))
        with pytest.raises(ObservationUnavailable, match="Invalid registered session identity"):
            sessions._session_entry_lock_state(tmp_path, session_id, str(tmp_path), strict_cwd=True)
