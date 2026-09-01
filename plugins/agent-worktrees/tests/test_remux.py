"""Tests for the Linux/WSL re-mux (reparent a bare Copilot into a tmux pane).

The reptyr reparent itself is a live ptrace operation and is not exercised here;
these cover the guards, the tty decode/detection, the pane command, and the
orchestration control flow with the mux + subprocess boundary mocked.
"""

from __future__ import annotations

import types

from agent_worktrees import __main__ as cli
from agent_worktrees import remux


# ── tty_nr decode (pure) ────────────────────────────────────────────────────
class TestTtyDecode:
    def _stat(self, tty_nr, comm="copilot"):
        # pid (comm) state ppid pgrp session tty_nr tpgid ...
        return f"1234 ({comm}) S 1 1234 1234 {tty_nr} -1 0 0"

    def test_pts_low_minor(self):
        # /dev/pts/3 -> new_encode_dev(136,3) == 34819
        assert remux._tty_from_stat(self._stat(34819)) == "/dev/pts/3"

    def test_pts_high_minor(self):
        # /dev/pts/300 -> minor high bits ride bit 20, not 12
        assert remux._tty_from_stat(self._stat(1083436)) == "/dev/pts/300"

    def test_no_controlling_tty(self):
        assert remux._tty_from_stat(self._stat(0)) is None

    def test_non_pts_major_ignored(self):
        # major 4 (legacy tty), minor 1 -> encode (1)|(4<<8)=1025
        assert remux._tty_from_stat(self._stat(1025)) is None

    def test_comm_with_spaces_and_parens(self):
        s = "1234 (weird )( name) S 1 1234 1234 34819 -1 0"
        assert remux._tty_from_stat(s) == "/dev/pts/3"

    def test_garbage_is_none(self):
        assert remux._tty_from_stat("no paren here") is None


# ── is_in_mux_pane ──────────────────────────────────────────────────────────
class TestIsInMuxPane:
    def test_true_when_tty_in_panes(self, monkeypatch):
        monkeypatch.setattr(remux, "process_tty", lambda p: "/dev/pts/5")
        assert remux.is_in_mux_pane(42, pane_ttys={"/dev/pts/5", "/dev/pts/9"})

    def test_false_when_not_in_panes(self, monkeypatch):
        monkeypatch.setattr(remux, "process_tty", lambda p: "/dev/pts/2")
        assert not remux.is_in_mux_pane(42, pane_ttys={"/dev/pts/5"})

    def test_false_when_no_tty(self, monkeypatch):
        monkeypatch.setattr(remux, "process_tty", lambda p: None)
        assert not remux.is_in_mux_pane(42, pane_ttys={"/dev/pts/5"})


# ── reptyr pane command ─────────────────────────────────────────────────────
class TestReptyrPaneCmd:
    def test_no_sudo(self):
        assert remux._reptyr_pane_cmd(99, use_sudo=False) == ["reptyr", "99"]

    def test_sudo_uses_askpass(self):
        assert remux._reptyr_pane_cmd(99, use_sudo=True) == [
            "sudo", "-A", "reptyr", "99"]


# ── guards ──────────────────────────────────────────────────────────────────
class TestGuards:
    def test_windows_unsupported(self, monkeypatch):
        monkeypatch.setattr(remux, "is_windows", lambda: True)
        r = remux.remux_bare_copilot(worktree_id="wtA")
        assert r["ok"] is False and "Windows" in r["reason"]

    def test_reptyr_missing(self, monkeypatch):
        monkeypatch.setattr(remux, "is_windows", lambda: False)
        monkeypatch.setattr(remux, "reptyr_path", lambda: None)
        r = remux.remux_bare_copilot(worktree_id="wtA")
        assert r["ok"] is False and "reptyr" in r["reason"]


class TestWindowsRestore:
    def _base(self, monkeypatch):
        monkeypatch.setattr(cli.platform, "system", lambda: "Windows")
        monkeypatch.setattr(cli.sessions, "has_mux_session", lambda wt: False)
        monkeypatch.setattr(cli.reclaim, "build_process_table", lambda: {})
        monkeypatch.setattr(
            cli.reclaim,
            "resolve_bound_copilots",
            lambda **kwargs: [_bound(42, wt="wtA", sid="sessA")],
        )
        monkeypatch.setattr(cli.reclaim, "resolve_bridge_bound", lambda *a, **k: [])
        monkeypatch.setattr(
            cli.reclaim, "filter_stop_unreachable", lambda found, **kwargs: found
        )
        monkeypatch.setattr(cli.reclaim, "descendants_of", lambda pid, table: set())

    def test_preview_does_not_reclaim(self, monkeypatch):
        self._base(monkeypatch)
        monkeypatch.setattr(
            cli,
            "reclaim_one",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")),
        )
        result = cli._perform_remux(
            worktree_id="wtA",
            session_id=None,
            worktree_path="C:\\worktrees\\wtA",
            force_sudo=None,
            apply_windows=False,
        )
        assert result["ok"] is True
        assert result["action"] == "preview"
        assert result["requires_resume"] is True
        assert result["session_id"] == "sessA"

    def test_apply_reclaims_exact_owner(self, monkeypatch):
        self._base(monkeypatch)
        calls = []
        monkeypatch.setattr(
            cli,
            "reclaim_one",
            lambda wt, **kwargs: (
                calls.append((wt, kwargs))
                or {"ok": True, "targets": 1, "reaped": [{"pid": 42}]}
            ),
        )
        result = cli._perform_remux(
            worktree_id="wtA",
            session_id=None,
            worktree_path="C:\\worktrees\\wtA",
            force_sudo=None,
            apply_windows=True,
        )
        assert result["ok"] is True
        assert result["action"] == "reclaimed"
        assert calls == [("wtA", {"bare_only": True, "target_pids": {42}})]

    def test_existing_mux_is_refused(self, monkeypatch):
        self._base(monkeypatch)
        monkeypatch.setattr(cli.sessions, "has_mux_session", lambda wt: True)
        result = cli._perform_remux(
            worktree_id="wtA",
            session_id=None,
            worktree_path="C:\\worktrees\\wtA",
            force_sudo=None,
            apply_windows=True,
        )
        assert result["ok"] is False
        assert "already has a live mux" in result["reason"]


def _bound(pid, homing="bare", wt="wtA", cwd="/w/wtA", sid="sessA"):
    return {"session_id": sid, "pid": pid, "cwd": cwd,
            "worktree_id": wt, "homing": homing}


# ── orchestration ───────────────────────────────────────────────────────────
class TestRemuxOrchestration:
    def _base(self, monkeypatch, bound):
        monkeypatch.setattr(remux, "is_windows", lambda: False)
        monkeypatch.setattr(remux, "reptyr_path", lambda: "/usr/bin/reptyr")
        monkeypatch.setattr(remux, "_needs_sudo", lambda: False)
        monkeypatch.setattr(remux.reclaim, "build_process_table", lambda: {})
        monkeypatch.setattr(remux.reclaim, "resolve_bound_copilots",
                            lambda **k: list(bound))
        monkeypatch.setattr(remux.reclaim, "descendants_of", lambda pid, t: set())

    def test_no_bare_target(self, monkeypatch):
        self._base(monkeypatch, [_bound(10, homing="mux")])
        r = remux.remux_bare_copilot(worktree_id="wtA")
        assert r["ok"] is False and "no bare" in r["reason"]

    def test_multiple_bare_requires_narrowing(self, monkeypatch):
        self._base(monkeypatch, [_bound(10, sid="a"), _bound(11, sid="b")])
        r = remux.remux_bare_copilot(worktree_id="wtA")
        assert r["ok"] is False and "multiple" in r["reason"]

    def test_success_new_session_verified(self, monkeypatch):
        self._base(monkeypatch, [_bound(1234)])
        monkeypatch.setattr(remux.sessions, "has_mux_session", lambda wt: False)
        cap = {}
        monkeypatch.setattr(
            remux.sessions, "build_mux_new_session_argv",
            lambda wt, wd, cmd, env=None, **k: (cap.update(cmd=cmd, wt=wt, wd=wd),
                                                ["tmux", "new-session"])[1])
        monkeypatch.setattr(
            remux.subprocess, "run",
            lambda *a, **k: types.SimpleNamespace(
                returncode=0, stdout="%3\n", stderr=""))
        monkeypatch.setattr(remux.sessions, "_is_process_alive", lambda p: True)
        monkeypatch.setattr(remux, "is_in_mux_pane", lambda pid: True)
        r = remux.remux_bare_copilot(worktree_id="wtA")
        assert r["ok"] is True and r["verified"] is True
        assert r["pid"] == 1234 and r["session"] == "wt-wtA" and r["pane"] == "%3"
        assert cap["cmd"] == ["reptyr", "1234"] and cap["wd"] == "/w/wtA"

    def test_success_existing_session_uses_new_window(self, monkeypatch):
        self._base(monkeypatch, [_bound(1234)])
        monkeypatch.setattr(remux.sessions, "has_mux_session", lambda wt: True)
        used = {"window": False}
        monkeypatch.setattr(
            remux.sessions, "build_mux_new_window_argv",
            lambda wt, wd, cmd, env=None, **k: (used.update(window=True),
                                                ["tmux", "new-window"])[1])
        monkeypatch.setattr(
            remux.subprocess, "run",
            lambda *a, **k: types.SimpleNamespace(
                returncode=0, stdout="%7", stderr=""))
        monkeypatch.setattr(remux.sessions, "_is_process_alive", lambda p: True)
        monkeypatch.setattr(remux, "is_in_mux_pane", lambda pid: True)
        r = remux.remux_bare_copilot(worktree_id="wtA")
        assert r["ok"] is True and used["window"] is True

    def test_sudo_flows_into_pane_cmd(self, monkeypatch):
        self._base(monkeypatch, [_bound(1234)])
        monkeypatch.setattr(remux, "_needs_sudo", lambda: True)
        monkeypatch.setattr(remux.sessions, "has_mux_session", lambda wt: False)
        cap = {}
        monkeypatch.setattr(
            remux.sessions, "build_mux_new_session_argv",
            lambda wt, wd, cmd, env=None, **k: (cap.update(cmd=cmd), ["tmux"])[1])
        monkeypatch.setattr(
            remux.subprocess, "run",
            lambda *a, **k: types.SimpleNamespace(returncode=0, stdout="%1",
                                                  stderr=""))
        monkeypatch.setattr(remux.sessions, "_is_process_alive", lambda p: True)
        monkeypatch.setattr(remux, "is_in_mux_pane", lambda pid: True)
        r = remux.remux_bare_copilot(worktree_id="wtA")  # force_sudo None -> auto
        assert r["used_sudo"] is True
        assert cap["cmd"] == ["sudo", "-A", "reptyr", "1234"]

    def test_tmux_error_is_reported(self, monkeypatch):
        self._base(monkeypatch, [_bound(1234)])
        monkeypatch.setattr(remux.sessions, "has_mux_session", lambda wt: False)
        monkeypatch.setattr(remux.sessions, "build_mux_new_session_argv",
                            lambda *a, **k: ["tmux"])
        monkeypatch.setattr(
            remux.subprocess, "run",
            lambda *a, **k: types.SimpleNamespace(
                returncode=1, stdout="", stderr="no server running"))
        r = remux.remux_bare_copilot(worktree_id="wtA")
        assert r["ok"] is False and "no server running" in r["reason"]

    def test_unverified_when_handover_not_confirmed(self, monkeypatch):
        self._base(monkeypatch, [_bound(1234)])
        monkeypatch.setattr(remux.sessions, "has_mux_session", lambda wt: False)
        monkeypatch.setattr(remux.sessions, "build_mux_new_session_argv",
                            lambda *a, **k: ["tmux"])
        monkeypatch.setattr(
            remux.subprocess, "run",
            lambda *a, **k: types.SimpleNamespace(returncode=0, stdout="%1",
                                                  stderr=""))
        monkeypatch.setattr(remux.sessions, "_is_process_alive", lambda p: True)
        monkeypatch.setattr(remux, "is_in_mux_pane", lambda pid: False)
        r = remux.remux_bare_copilot(worktree_id="wtA", verify_timeout=0.1)
        assert r["ok"] is True and r["verified"] is False
