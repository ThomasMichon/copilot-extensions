"""Unit tests for the postToolUse disposition-nudge hook (scripts/nudge_status.py).

The hook is a standalone script (runs under system python, no agent_worktrees
import), so we load it by path and exercise the pure ``decide()`` seam against a
fabricated ``home`` (tracking yaml + sidecar), never the real ~/.agent-worktrees.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "nudge_status.py"


def _load():
    spec = importlib.util.spec_from_file_location("nudge_status", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


nudge = _load()

_WID = "examplehost-20260101-000000-abcd"


def _make_home(tmp_path: Path, *, project="dotfiles", state="active",
               note_at="2026-01-01T00:00:00+00:00") -> tuple[Path, str]:
    """Create a fake home with one tracked worktree; return (home, cwd)."""
    tdir = tmp_path / f".{project}" / "worktrees"
    tdir.mkdir(parents=True)
    body = f"id: {_WID}\nstate: {state}\n"
    if note_at is not None:
        body += f"status_note_at: '{note_at}'\n"
    (tdir / f"{_WID}.yaml").write_text(body, encoding="utf-8")
    # A cwd whose ancestor basename is the worktree id (need not exist on disk).
    cwd = str(tmp_path / "src" / ".worktrees" / project / _WID / "sub")
    return tmp_path, cwd


def _payload(cwd):
    return {"toolName": "view", "cwd": cwd}


def test_no_nudge_before_threshold(tmp_path):
    home, cwd = _make_home(tmp_path)
    env = {"AGENT_WORKTREES_NUDGE_CALLS": "5", "AGENT_WORKTREES_NUDGE_MINUTES": "999"}
    for i in range(4):
        assert nudge.decide(_payload(cwd), env=env, home=home, now=1000.0 + i) is None


def test_nudge_at_call_threshold_then_cooldown(tmp_path):
    home, cwd = _make_home(tmp_path)
    env = {"AGENT_WORKTREES_NUDGE_CALLS": "3", "AGENT_WORKTREES_NUDGE_MINUTES": "999"}
    outs = [nudge.decide(_payload(cwd), env=env, home=home, now=1000.0 + i)
            for i in range(6)]
    fired = [i for i, o in enumerate(outs) if o]
    # Fires on the 3rd call (index 2), resets, needs another full window -> 6th (index 5).
    assert fired == [2, 5]
    assert "status --summary" in outs[2]
    assert "--title" in outs[2]  # summary_and_title scope


def test_time_threshold_triggers(tmp_path):
    home, cwd = _make_home(tmp_path)
    env = {"AGENT_WORKTREES_NUDGE_CALLS": "999", "AGENT_WORKTREES_NUDGE_MINUTES": "20"}
    assert nudge.decide(_payload(cwd), env=env, home=home, now=1000.0) is None
    # 21 minutes later -> due on elapsed time even though call count is tiny.
    assert nudge.decide(_payload(cwd), env=env, home=home, now=1000.0 + 21 * 60) is not None


def test_reset_when_disposition_written(tmp_path):
    home, cwd = _make_home(tmp_path, note_at="2026-01-01T00:00:00+00:00")
    env = {"AGENT_WORKTREES_NUDGE_CALLS": "3", "AGENT_WORKTREES_NUDGE_MINUTES": "999"}
    nudge.decide(_payload(cwd), env=env, home=home, now=1000.0)
    nudge.decide(_payload(cwd), env=env, home=home, now=1001.0)  # count now 2
    # Agent writes a disposition -> status_note_at advances.
    yml = home / ".dotfiles" / "worktrees" / f"{_WID}.yaml"
    yml.write_text(
        f"id: {_WID}\nstate: active\nstatus_note_at: '2026-01-01T00:05:00+00:00'\n",
        encoding="utf-8")
    # Next fire resets the window (count back to 1), so no nudge yet at count 1/2.
    assert nudge.decide(_payload(cwd), env=env, home=home, now=1002.0) is None
    assert nudge.decide(_payload(cwd), env=env, home=home, now=1003.0) is None
    # Third post-reset call hits the threshold again.
    assert nudge.decide(_payload(cwd), env=env, home=home, now=1004.0) is not None


def test_terminal_state_clears_sidecar_and_is_silent(tmp_path):
    home, cwd = _make_home(tmp_path, state="active")
    env = {"AGENT_WORKTREES_NUDGE_CALLS": "1", "AGENT_WORKTREES_NUDGE_MINUTES": "999"}
    assert nudge.decide(_payload(cwd), env=env, home=home, now=1000.0) is not None
    sidecar = home / ".agent-worktrees" / "nudge-state" / f"{_WID}.json"
    assert sidecar.is_file()
    # Worktree finalized -> terminal state: no nudge, sidecar dropped.
    yml = home / ".dotfiles" / "worktrees" / f"{_WID}.yaml"
    yml.write_text(f"id: {_WID}\nstate: finalized\n", encoding="utf-8")
    assert nudge.decide(_payload(cwd), env=env, home=home, now=1001.0) is None
    assert not sidecar.exists()


def test_untracked_cwd_is_silent(tmp_path):
    home, _ = _make_home(tmp_path)
    env = {"AGENT_WORKTREES_NUDGE_CALLS": "1"}
    outside = str(tmp_path / "not" / "a" / "worktree")
    assert nudge.decide({"cwd": outside}, env=env, home=home, now=1000.0) is None


def test_kill_switch(tmp_path):
    home, cwd = _make_home(tmp_path)
    for off in ("off", "0", "false", "no"):
        env = {"AGENT_WORKTREES_NUDGE": off, "AGENT_WORKTREES_NUDGE_CALLS": "1"}
        assert nudge.decide(_payload(cwd), env=env, home=home, now=1000.0) is None


@pytest.mark.parametrize("scope_key", ["summary", "title"])
def test_nudge_mentions_both_summary_and_title(tmp_path, scope_key):
    home, cwd = _make_home(tmp_path)
    env = {"AGENT_WORKTREES_NUDGE_CALLS": "1", "AGENT_WORKTREES_NUDGE_MINUTES": "999"}
    text = nudge.decide(_payload(cwd), env=env, home=home, now=1000.0)
    assert text is not None
    assert f"--{scope_key}" in text
