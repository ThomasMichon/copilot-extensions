"""Tests for the precise session->process reclaimer (:mod:`reclaim`).

Cover the pure resolution/classification logic and the ``reclaim`` command's
control flow (dry-run-by-default, self-guard, filters) with the process table,
lock files, and termination boundary mocked -- no real process is killed.
"""

from __future__ import annotations

import argparse
import json

from agent_worktrees import __main__ as m
from agent_worktrees import reclaim


# ── homing_of / descendants_of (pure) ──────────────────────────────────────
class TestHoming:
    def test_mux_ancestor(self):
        table = {
            100: {"ppid": 50, "name": "copilot.exe"},
            50: {"ppid": 10, "name": "pwsh.exe"},
            10: {"ppid": 1, "name": "psmux.exe"},
            1: {"ppid": 0, "name": "init"},
        }
        assert reclaim.homing_of(100, table) == "mux"

    def test_bare_no_mux_ancestor(self):
        table = {
            100: {"ppid": 50, "name": "copilot.exe"},
            50: {"ppid": 10, "name": "pwsh.exe"},
            10: {"ppid": 1, "name": "windowsterminal.exe"},
            1: {"ppid": 0, "name": "explorer.exe"},
        }
        assert reclaim.homing_of(100, table) == "bare"

    def test_unknown_when_absent(self):
        assert reclaim.homing_of(999, {}) == "unknown"

    def test_tmux_counts_as_mux(self):
        table = {5: {"ppid": 4, "name": "copilot"}, 4: {"ppid": 1, "name": "tmux: server"}}
        assert reclaim.homing_of(5, table) == "mux"

    def test_cycle_is_survived(self):
        # pathological ppid cycle must not loop forever
        table = {2: {"ppid": 3, "name": "a"}, 3: {"ppid": 2, "name": "b"}}
        assert reclaim.homing_of(2, table) == "bare"

    def test_descendants(self):
        table = {
            1: {"ppid": 0, "name": "root"},
            2: {"ppid": 1, "name": "child"},
            3: {"ppid": 2, "name": "grandchild"},
            4: {"ppid": 1, "name": "sibling"},
        }
        assert reclaim.descendants_of(1, table) == {2, 3, 4}
        assert reclaim.descendants_of(2, table) == {3}
        assert reclaim.descendants_of(3, table) == set()


# ── _worktree_id_from_path (pure) ──────────────────────────────────────────
class TestWorktreeIdFromPath:
    def test_dotworktrees_container(self):
        p = r"C:\Data\Src\.worktrees\aperture-labs\lambda-core-win-20260101-x"
        assert reclaim._worktree_id_from_path(p) == "lambda-core-win-20260101-x"

    def test_suffix_worktrees_container(self):
        p = r"C:\Data\Src\copilot-extensions.worktrees\lambda-core-win-abc"
        assert reclaim._worktree_id_from_path(p) == "lambda-core-win-abc"

    def test_non_worktree_path_is_none(self):
        assert reclaim._worktree_id_from_path(r"C:\Users\me\project") is None


# ── resolve_bound_copilots (session dirs + lock files mocked) ───────────────
def _mk_session(tmp_path, sid, cwd, pids):
    d = tmp_path / sid
    d.mkdir()
    (d / "workspace.yaml").write_text(f"cwd: {cwd}\n", encoding="utf-8")
    for pid in pids:
        (d / f"inuse.{pid}.lock").write_text("x", encoding="utf-8")
    return d


class TestResolveBoundCopilots:
    def _patch(self, monkeypatch, tmp_path, *, alive, copilots, wt_map, table):
        monkeypatch.setattr(reclaim.sessions, "_session_state_dir", lambda: tmp_path)
        monkeypatch.setattr(reclaim.sessions, "_is_process_alive", lambda p: p in alive)
        monkeypatch.setattr(reclaim.sessions, "_is_copilot_process", lambda p: p in copilots)
        monkeypatch.setattr(reclaim.sessions, "_is_detached_session", lambda e: False)
        monkeypatch.setattr(reclaim, "_resolve_worktree_id_for_cwd",
                            lambda cwd: wt_map.get(cwd))
        monkeypatch.setattr(reclaim, "build_process_table", lambda: table)
        # Neutralize the POSIX tty-upgrade by default (no tmux panes) so these
        # tests stay deterministic on Linux runners; a specific test overrides it.
        from agent_worktrees import remux
        monkeypatch.setattr(remux, "tmux_pane_ttys", lambda mux_bin=None: set())

    def test_bare_by_ppid_upgraded_to_mux_when_tty_is_a_pane(
            self, monkeypatch, tmp_path):
        # A reptyr-adopted Copilot keeps a bare ppid ancestry but its controlling
        # tty is now a tmux pane -> homing must upgrade bare -> mux.
        _mk_session(tmp_path, "sess", "/w/wtA", [777])
        table = {777: {"ppid": 10, "name": "copilot"},
                 10: {"ppid": 1, "name": "bash"}}
        self._patch(monkeypatch, tmp_path, alive={777}, copilots={777},
                    wt_map={"/w/wtA": "wtA"}, table=table)
        monkeypatch.setattr(reclaim.platform, "system", lambda: "Linux")
        from agent_worktrees import remux
        monkeypatch.setattr(remux, "tmux_pane_ttys",
                            lambda mux_bin=None: {"/dev/pts/9"})
        monkeypatch.setattr(remux, "process_tty", lambda pid: "/dev/pts/9")
        rows = reclaim.resolve_bound_copilots()
        assert [r["homing"] for r in rows] == ["mux"]
        _mk_session(tmp_path, "sessA", "/w/wtA", [5668, 35156])
        table = {
            5668: {"ppid": 10, "name": "copilot.exe"},
            10: {"ppid": 1, "name": "windowsterminal.exe"},
            35156: {"ppid": 20, "name": "copilot.exe"},
            20: {"ppid": 2, "name": "psmux.exe"},
        }
        self._patch(monkeypatch, tmp_path, alive={5668, 35156},
                    copilots={5668, 35156}, wt_map={"/w/wtA": "wtA"}, table=table)
        rows = reclaim.resolve_bound_copilots()
        by_pid = {r["pid"]: r for r in rows}
        assert by_pid[5668]["homing"] == "bare"
        assert by_pid[35156]["homing"] == "mux"
        # bare-only filter would keep just the orphan
        bare = [r for r in rows if r["homing"] == "bare"]
        assert [r["pid"] for r in bare] == [5668]

    def test_dead_and_non_copilot_locks_skipped(self, monkeypatch, tmp_path):
        _mk_session(tmp_path, "sessB", "/w/wtB", [111, 222, 333])
        table = {111: {"ppid": 1, "name": "copilot"}}
        # 111 alive+copilot; 222 dead; 333 alive but not copilot (pid reuse)
        self._patch(monkeypatch, tmp_path, alive={111, 333},
                    copilots={111}, wt_map={"/w/wtB": "wtB"}, table=table)
        rows = reclaim.resolve_bound_copilots()
        assert [r["pid"] for r in rows] == [111]

    def test_session_id_prefix_filter(self, monkeypatch, tmp_path):
        _mk_session(tmp_path, "aaaa1111", "/w/a", [1])
        _mk_session(tmp_path, "bbbb2222", "/w/b", [2])
        table = {1: {"ppid": 0, "name": "copilot"}, 2: {"ppid": 0, "name": "copilot"}}
        self._patch(monkeypatch, tmp_path, alive={1, 2}, copilots={1, 2},
                    wt_map={"/w/a": "a", "/w/b": "b"}, table=table)
        rows = reclaim.resolve_bound_copilots(session_id="aaaa")
        assert [r["pid"] for r in rows] == [1]

    def test_worktree_id_filter(self, monkeypatch, tmp_path):
        _mk_session(tmp_path, "s1", "/w/a", [1])
        _mk_session(tmp_path, "s2", "/w/b", [2])
        table = {1: {"ppid": 0, "name": "copilot"}, 2: {"ppid": 0, "name": "copilot"}}
        self._patch(monkeypatch, tmp_path, alive={1, 2}, copilots={1, 2},
                    wt_map={"/w/a": "wtA", "/w/b": "wtB"}, table=table)
        rows = reclaim.resolve_bound_copilots(worktree_id="wtB")
        assert [r["pid"] for r in rows] == [2]

    def test_session_registry_binds_bare_resume_with_home_cwd(
            self, monkeypatch, tmp_path):
        _mk_session(tmp_path, "resumed-session", "/home/user", [7])
        table = {7: {"ppid": 1, "name": "copilot"}}
        self._patch(
            monkeypatch, tmp_path, alive={7}, copilots={7},
            wt_map={"/home/user": None}, table=table,
        )
        monkeypatch.setattr(
            reclaim.tracking, "find_worktree_id_by_session",
            lambda sid: "wtA" if sid == "resumed-session" else None,
        )

        rows = reclaim.resolve_bound_copilots(worktree_id="wtA")

        assert [(r["session_id"], r["worktree_id"]) for r in rows] == [
            ("resumed-session", "wtA")
        ]

    def test_detached_sessions_excluded(self, monkeypatch, tmp_path):
        _mk_session(tmp_path, "sdet", "/w/a", [1])
        table = {1: {"ppid": 0, "name": "copilot"}}
        monkeypatch.setattr(reclaim.sessions, "_session_state_dir", lambda: tmp_path)
        monkeypatch.setattr(reclaim.sessions, "_is_process_alive", lambda p: True)
        monkeypatch.setattr(reclaim.sessions, "_is_copilot_process", lambda p: True)
        monkeypatch.setattr(reclaim.sessions, "_is_detached_session", lambda e: True)
        monkeypatch.setattr(reclaim, "_resolve_worktree_id_for_cwd", lambda cwd: "wtA")
        monkeypatch.setattr(reclaim, "build_process_table", lambda: table)
        assert reclaim.resolve_bound_copilots() == []

    def test_no_lock_dir_skips_yaml_read(self, monkeypatch, tmp_path):
        # Hot-path guard: a historical session dir with no live lock must be
        # skipped WITHOUT the expensive workspace.yaml read + worktree lookup.
        d = tmp_path / "old-session"
        d.mkdir()
        (d / "workspace.yaml").write_text("cwd: /w/x\n", encoding="utf-8")
        # no inuse.*.lock files at all
        monkeypatch.setattr(reclaim.sessions, "_session_state_dir",
                            lambda: tmp_path)
        monkeypatch.setattr(reclaim.sessions, "_is_detached_session",
                            lambda e: False)
        monkeypatch.setattr(reclaim, "build_process_table", lambda: {})
        calls = {"cwd": 0}
        real = reclaim._session_cwd
        monkeypatch.setattr(
            reclaim, "_session_cwd",
            lambda e: (calls.__setitem__("cwd", calls["cwd"] + 1), real(e))[1])
        assert reclaim.resolve_bound_copilots() == []
        assert calls["cwd"] == 0

    def test_stale_dead_lock_skips_yaml_read(self, monkeypatch, tmp_path):
        # A crashed session leaves a lock whose pid is dead -> still skipped
        # before the yaml read (only *live* bound Copilots pay the full path).
        d = tmp_path / "crashed"
        d.mkdir()
        (d / "workspace.yaml").write_text("cwd: /w/x\n", encoding="utf-8")
        (d / "inuse.99999.lock").write_text("x", encoding="utf-8")
        monkeypatch.setattr(reclaim.sessions, "_session_state_dir",
                            lambda: tmp_path)
        monkeypatch.setattr(reclaim.sessions, "_is_process_alive",
                            lambda p: False)
        monkeypatch.setattr(reclaim.sessions, "_is_copilot_process",
                            lambda p: True)
        monkeypatch.setattr(reclaim.sessions, "_is_detached_session",
                            lambda e: False)
        monkeypatch.setattr(reclaim, "build_process_table", lambda: {})
        calls = {"cwd": 0}
        real = reclaim._session_cwd
        monkeypatch.setattr(
            reclaim, "_session_cwd",
            lambda e: (calls.__setitem__("cwd", calls["cwd"] + 1), real(e))[1])
        assert reclaim.resolve_bound_copilots() == []
        assert calls["cwd"] == 0


# ── reap_bound_copilots (termination boundary mocked) ──────────────────────
class TestReapBoundCopilots:
    def test_kills_target_and_copilot_children(self, monkeypatch):
        table = {
            100: {"ppid": 1, "name": "copilot.exe"},
            101: {"ppid": 100, "name": "copilot.exe"},   # preload child
            102: {"ppid": 100, "name": "conhost.exe"},    # non-copilot child
        }
        killed: list[int] = []
        import agent_worktrees.procs as procs
        monkeypatch.setattr(procs, "terminate_pid",
                            lambda p: (killed.append(p), True)[1])
        monkeypatch.setattr(reclaim.sessions, "_is_copilot_process",
                            lambda p: p in (100, 101))
        out = reclaim.reap_bound_copilots(
            [{"session_id": "s", "pid": 100, "worktree_id": "wt", "homing": "bare"}],
            table=table,
        )
        assert out[0]["killed"] is True
        assert out[0]["children_killed"] == 1        # only the copilot child
        assert 101 in killed and 100 in killed and 102 not in killed
        # child reaped before parent
        assert killed.index(101) < killed.index(100)


# ── cmd_reclaim control flow ───────────────────────────────────────────────
def _ns(**kw):
    base = dict(session_id=None, worktree_id=None, all=False, bare_only=False,
                yes=False, json=True)
    base.update(kw)
    return argparse.Namespace(**base)


class TestCmdReclaim:
    def _stub_resolution(self, monkeypatch, rows, table=None):
        table = table or {r["pid"]: {"ppid": 1, "name": "copilot"} for r in rows}
        monkeypatch.setattr(m.reclaim, "build_process_table", lambda: table)
        monkeypatch.setattr(m.reclaim, "resolve_bound_copilots",
                            lambda **k: list(rows))
        monkeypatch.setattr(m.reclaim, "descendants_of", lambda pid, t: set())

    def test_dry_run_is_default_no_kill(self, monkeypatch, capfd):
        rows = [{"session_id": "s1", "pid": 200, "cwd": "/w", "worktree_id": "wt",
                 "homing": "bare"}]
        self._stub_resolution(monkeypatch, rows)
        reaped = {"called": False}
        monkeypatch.setattr(m.reclaim, "reap_bound_copilots",
                            lambda *a, **k: reaped.update(called=True) or [])
        rc = m.cmd_reclaim(_ns(session_id="s1"))
        assert rc == 0
        out = json.loads(capfd.readouterr().out)
        assert out["action"] == "dry-run"
        assert reaped["called"] is False
        assert [t["pid"] for t in out["targets"]] == [200]
        assert out["reaped"] == []

    def test_yes_triggers_reap(self, monkeypatch, capfd):
        rows = [{"session_id": "s1", "pid": 200, "cwd": "/w", "worktree_id": "wt",
                 "homing": "bare"}]
        self._stub_resolution(monkeypatch, rows)
        captured = {}
        monkeypatch.setattr(
            m.reclaim, "reap_bound_copilots",
            lambda targets, **k: captured.update(t=targets) or
            [{"session_id": "s1", "pid": 200, "worktree_id": "wt",
              "homing": "bare", "killed": True, "children_killed": 2}])
        rc = m.cmd_reclaim(_ns(session_id="s1", yes=True))
        assert rc == 0
        out = json.loads(capfd.readouterr().out)
        assert out["action"] == "reclaim"
        assert out["reaped"][0]["killed"] is True
        assert [t["pid"] for t in captured["t"]] == [200]

    def test_self_is_never_targeted(self, monkeypatch, capfd):
        import os
        me = os.getpid()
        rows = [{"session_id": "self", "pid": me, "cwd": "/w", "worktree_id": "wt",
                 "homing": "mux"}]
        self._stub_resolution(monkeypatch, rows)
        monkeypatch.setattr(m.reclaim, "reap_bound_copilots",
                            lambda *a, **k: (_ for _ in ()).throw(
                                AssertionError("must not reap self")))
        rc = m.cmd_reclaim(_ns(session_id="self", yes=True))
        assert rc == 0
        out = json.loads(capfd.readouterr().out)
        assert out["targets"] == []
        assert [s["pid"] for s in out["self_skipped"]] == [me]

    def test_bare_only_filter(self, monkeypatch, capfd):
        rows = [
            {"session_id": "s1", "pid": 200, "cwd": "/w", "worktree_id": "wt",
             "homing": "bare"},
            {"session_id": "s1", "pid": 201, "cwd": "/w", "worktree_id": "wt",
             "homing": "mux"},
        ]
        self._stub_resolution(monkeypatch, rows)
        monkeypatch.setattr(m.reclaim, "reap_bound_copilots", lambda *a, **k: [])
        rc = m.cmd_reclaim(_ns(worktree_id=None, session_id="s1", bare_only=True))
        assert rc == 0
        out = json.loads(capfd.readouterr().out)
        assert [t["pid"] for t in out["targets"]] == [200]

    def test_no_target_and_neutral_cwd_errors(self, monkeypatch, capfd):
        monkeypatch.setattr(m, "_infer_worktree_id_from_cwd", lambda: None)
        rc = m.cmd_reclaim(_ns())
        assert rc == 2
        assert "not a worktree" in capfd.readouterr().out


# ── find_bare_orphans (surfacing helper) ────────────────────────────────────
class TestFindBareOrphans:
    def test_returns_only_bare_excluding_self_subtree(self, monkeypatch):
        rows = [
            {"session_id": "bareA", "pid": 200, "cwd": "/w/a",
             "worktree_id": "wtA", "homing": "bare"},
            {"session_id": "muxB", "pid": 201, "cwd": "/w/b",
             "worktree_id": "wtB", "homing": "mux"},
            {"session_id": "self", "pid": 300, "cwd": "/w/c",
             "worktree_id": "wtC", "homing": "bare"},
        ]
        # 350 (this doctor command) is a child of the bare self-session 300, so
        # 300 must be excluded; 201 is mux; only the true orphan 200 remains.
        table = {
            200: {"ppid": 1, "name": "copilot"},
            201: {"ppid": 2, "name": "copilot"},
            300: {"ppid": 1, "name": "copilot"},
            350: {"ppid": 300, "name": "pwsh"},
        }
        monkeypatch.setattr(reclaim, "resolve_bound_copilots",
                            lambda **k: list(rows))
        out = reclaim.find_bare_orphans(table=table, self_pid=350)
        assert [o["pid"] for o in out] == [200]
        assert out[0] == {"session_id": "bareA", "pid": 200,
                          "worktree_id": "wtA", "cwd": "/w/a"}
        assert "homing" not in out[0]

    def test_empty_when_none_bound(self, monkeypatch):
        monkeypatch.setattr(reclaim, "resolve_bound_copilots", lambda **k: [])
        assert reclaim.find_bare_orphans(table={}, self_pid=1) == []

    def test_bare_orphan_worktree_ids_derives_deduped_set(self, monkeypatch):
        rows = [
            {"session_id": "a", "pid": 1, "cwd": "/w/a",
             "worktree_id": "wtA", "homing": "bare"},
            {"session_id": "b", "pid": 2, "cwd": "/w/b",
             "worktree_id": "wtB", "homing": "mux"},   # mux -> excluded
            {"session_id": "c", "pid": 3, "cwd": "/w/a2",
             "worktree_id": "wtA", "homing": "bare"},  # dupe wtA
            {"session_id": "d", "pid": 4, "cwd": "/w/d",
             "worktree_id": None, "homing": "bare"},    # no wt -> dropped
        ]
        table = {p: {"ppid": 0, "name": "copilot"} for p in (1, 2, 3, 4)}
        monkeypatch.setattr(reclaim, "resolve_bound_copilots",
                            lambda **k: list(rows))
        ids = reclaim.bare_orphan_worktree_ids(table=table, self_pid=999)
        assert ids == {"wtA"}
