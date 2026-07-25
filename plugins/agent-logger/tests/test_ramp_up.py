"""Tests for the session ramp-up front end.

Covers the one primitive ramp-up adds over the segmenter engine it reuses:
worktree -> session discovery. Collation itself is exercised by the segmenter
tests and functional checks.
"""

from __future__ import annotations

import json
from pathlib import Path

import agent_logger.segmenter.ramp_up as ramp_up


def _make_session(
    state_root: Path,
    session_id: str,
    cwd: str,
    *,
    updated_at: str,
    quip: bool = False,
    events: bool = True,
    name: str = "Test Session",
) -> Path:
    """Create a minimal session-state dir with workspace.yaml (+ events.jsonl)."""
    d = state_root / session_id
    d.mkdir(parents=True)
    ws = [
        f"id: {session_id}",
        f"cwd: {cwd}",
        f"git_root: {cwd}",
        "branch: worktree/test",
        f"name: {name}",
        "created_at: 2026-01-01T00:00:00.000Z",
        f"updated_at: {updated_at}",
    ]
    (d / "workspace.yaml").write_text("\n".join(ws) + "\n", encoding="utf-8")
    if events:
        start = {
            "type": "session.start",
            "timestamp": "2026-01-01T00:00:00.000Z",
            "data": {"sessionId": session_id, "context": {"cwd": cwd}},
        }
        (d / "events.jsonl").write_text(
            json.dumps(start) + "\n", encoding="utf-8"
        )
    return d


def _point_home(monkeypatch, home: Path) -> Path:
    """Point find_copilot_dir at *home*/.copilot and return the state root."""
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    state_root = home / ".copilot" / "session-state"
    state_root.mkdir(parents=True)
    return state_root


def test_main_callable() -> None:
    assert callable(ramp_up.main)


def test_discovers_matching_worktree(monkeypatch, tmp_path: Path) -> None:
    state_root = _point_home(monkeypatch, tmp_path / "home")
    worktree = str(tmp_path / "wt")
    _make_session(state_root, "aaaa", worktree, updated_at="2026-01-02T00:00:00.000Z")

    found = ramp_up.discover_sessions(worktree)
    assert [s["id"] for s in found] == ["aaaa"]
    assert found[0]["name"] == "Test Session"


def test_ignores_other_worktrees(monkeypatch, tmp_path: Path) -> None:
    state_root = _point_home(monkeypatch, tmp_path / "home")
    _make_session(
        state_root, "aaaa", str(tmp_path / "other"), updated_at="2026-01-02T00:00:00.000Z"
    )

    found = ramp_up.discover_sessions(str(tmp_path / "wt"))
    assert found == []


def test_excludes_quip_sessions(monkeypatch, tmp_path: Path) -> None:
    state_root = _point_home(monkeypatch, tmp_path / "home")
    quip_cwd = str(tmp_path / "temp" / "quip-abc")
    _make_session(state_root, "qqqq", quip_cwd, updated_at="2026-01-02T00:00:00.000Z")

    found = ramp_up.discover_sessions(quip_cwd)
    assert found == []


def test_requires_events_jsonl(monkeypatch, tmp_path: Path) -> None:
    state_root = _point_home(monkeypatch, tmp_path / "home")
    worktree = str(tmp_path / "wt")
    _make_session(
        state_root, "noev", worktree, updated_at="2026-01-02T00:00:00.000Z", events=False
    )

    found = ramp_up.discover_sessions(worktree)
    assert found == []


def test_sorts_most_recent_first(monkeypatch, tmp_path: Path) -> None:
    state_root = _point_home(monkeypatch, tmp_path / "home")
    worktree = str(tmp_path / "wt")
    _make_session(state_root, "older", worktree, updated_at="2026-01-01T10:00:00.000Z")
    _make_session(state_root, "newer", worktree, updated_at="2026-01-05T10:00:00.000Z")

    found = ramp_up.discover_sessions(worktree)
    assert [s["id"] for s in found] == ["newer", "older"]


def test_matching_is_case_insensitive(monkeypatch, tmp_path: Path) -> None:
    import os

    if os.path.normcase("A") != "a":
        # normcase is identity on this platform (POSIX); case-folding N/A.
        return
    state_root = _point_home(monkeypatch, tmp_path / "home")
    worktree = str(tmp_path / "WT")
    _make_session(state_root, "aaaa", worktree, updated_at="2026-01-02T00:00:00.000Z")

    # Query with a differently-cased path resolves to the same session.
    found = ramp_up.discover_sessions(str(tmp_path / "wt"))
    assert [s["id"] for s in found] == ["aaaa"]


def test_default_output_dir_matches_read_digest_temp_fallback(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TEMP", str(tmp_path))
    out = ramp_up._default_output_dir("abcd")
    assert out == tmp_path / "session-digest" / "abcd"


def test_worktree_suffix() -> None:
    assert ramp_up._worktree_suffix("C:/x/lambda-core-win-20260724-120542-fbc5") == "fbc5"
    assert ramp_up._worktree_suffix("/home/u/borealis-win-20260101-000000-ABCD") == "abcd"
    assert ramp_up._worktree_suffix("") == ""


def test_discover_by_suffix(monkeypatch, tmp_path: Path) -> None:
    state_root = _point_home(monkeypatch, tmp_path / "home")
    wt = str(tmp_path / "lambda-core-win-20260724-120542-fbc5")
    _make_session(state_root, "aaaa", wt, updated_at="2026-01-02T00:00:00.000Z")

    found = ramp_up.discover_by_suffix("fbc5")
    assert [s["id"] for s in found] == ["aaaa"]
    # Case-insensitive, and a leading dash is tolerated.
    assert ramp_up.discover_by_suffix("-FBC5")[0]["id"] == "aaaa"
    # A non-matching suffix finds nothing.
    assert ramp_up.discover_by_suffix("9999") == []


def test_discover_by_suffix_machine_filter(monkeypatch, tmp_path: Path) -> None:
    state_root = _point_home(monkeypatch, tmp_path / "home")
    lc = str(tmp_path / "lambda-core-win-20260724-120542-dupe")
    bl = str(tmp_path / "borealis-win-20260724-120542-dupe")
    _make_session(state_root, "lc01", lc, updated_at="2026-01-02T00:00:00.000Z")
    _make_session(state_root, "bl01", bl, updated_at="2026-01-03T00:00:00.000Z")

    # Same suffix on two machines -> both without a filter, one with it.
    assert {s["id"] for s in ramp_up.discover_by_suffix("dupe")} == {"lc01", "bl01"}
    assert [s["id"] for s in ramp_up.discover_by_suffix("dupe", "lambda-core")] == ["lc01"]
    assert [s["id"] for s in ramp_up.discover_by_suffix("dupe", "borealis")] == ["bl01"]


def test_is_local_machine(monkeypatch) -> None:
    monkeypatch.setattr(ramp_up, "detect_machine", lambda: "lambda-core")
    assert ramp_up._is_local_machine(None) is True
    assert ramp_up._is_local_machine("lambda-core") is True
    assert ramp_up._is_local_machine("lambda-core-wsl") is True
    assert ramp_up._is_local_machine("borealis") is False
