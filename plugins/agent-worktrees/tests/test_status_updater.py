"""Tests for the `status-updater` background loop and the render helpers.

The updater moves the status-bar work off psmux's paint path: instead of the
bar polling ``#(agent-worktrees ...)`` (a process spawn per render, which
psmux runs synchronously), a detached loop renders in-process and pushes the
result into session options ``@aw_ctx`` (identity, once) and ``@aw_seg``
(disposition, on an interval).  These tests drive the loop with a fake mux
binary so no real psmux/tmux is required.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time

import pytest

from agent_worktrees import __main__ as m
from agent_worktrees import update_stage as us


@pytest.fixture(autouse=True)
def _disable_status_monitor(monkeypatch):
    """This file exercises the legacy per-session updater loop.

    The resident monitor has separate tests; leave it off here so default-on
    delegation does not short-circuit the mux writes these tests assert.
    """
    monkeypatch.setenv("AGENT_WORKTREES_STATUS_MONITOR", "0")


def _ns(**kw):
    base = {"session": "wt-test", "mux": "psmux", "path": "/w/x", "interval": 5}
    base.update(kw)
    return argparse.Namespace(**base)


def test_status_updater_registered():
    assert m.COMMAND_MAP["status-updater"] is m.cmd_status_updater
    assert m._WORKTREE_VERBS["status-updater"] == "status-updater"


def test_render_helpers_back_the_print_wrappers(monkeypatch, capsys):
    """The cmd_* wrappers must print exactly what the renderers return."""
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTXLINE")
    monkeypatch.setattr(m, "_render_status_segment", lambda *a, **k: "SEGLINE")

    assert m.cmd_status_context(argparse.Namespace(path=None, plain=False)) == 0
    assert capsys.readouterr().out.strip() == "CTXLINE"

    assert m.cmd_status_segment(
        argparse.Namespace(path=None, fetch=False, plain=False, no_title=False)
    ) == 0
    assert capsys.readouterr().out.strip() == "SEGLINE"


def test_render_wrapper_prints_nothing_when_empty(monkeypatch, capsys):
    monkeypatch.setattr(m, "_render_status_segment", lambda *a, **k: "")
    rc = m.cmd_status_segment(
        argparse.Namespace(path=None, fetch=False, plain=False, no_title=False)
    )
    assert rc == 0
    assert capsys.readouterr().out == ""


def _fake_mux(has_session_codes, calls, store=None):
    """Build a fake subprocess.run for the mux binary.

    ``has_session_codes`` is consumed one return-code per ``has-session``
    call; ``set-option`` invocations are recorded into ``calls`` as
    ``(option, value)`` tuples and mirrored into ``store``; ``display-message``
    reads ``store`` so the ``@aw_updater`` token round-trips.
    """
    codes = iter(has_session_codes)
    store = store if store is not None else {}

    def fake_run(argv, **_kw):
        verb = argv[1]
        if verb == "has-session":
            return subprocess.CompletedProcess(argv, next(codes, 1), "", "")
        if verb == "set-option":
            # argv == [bin, set-option, -t, <sess>, <opt>, <val>]
            store[argv[4]] = argv[5]
            calls.append((argv[4], argv[5]))
            return subprocess.CompletedProcess(argv, 0, "", "")
        if verb == "display-message":
            # argv == [bin, display-message, -t, <sess>, -p, "#{@opt}"]
            key = argv[5].strip("#{}")
            return subprocess.CompletedProcess(argv, 0, store.get(key, ""), "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    return fake_run


def _fake_mux_transient(has_session_plan, calls, store=None):
    """Like ``_fake_mux`` but ``has-session`` entries may be the sentinel
    ``"raise"`` -- modelling a *transient* mux failure (timeout/exception) that
    ``_mux`` collapses to ``None`` -> ``_session_state() == "unknown"``.  Used to
    prove the loop tolerates hiccups instead of retiring the bar (dotfiles
    #915)."""
    plan = iter(has_session_plan)
    store = store if store is not None else {}

    def fake_run(argv, **_kw):
        verb = argv[1]
        if verb == "has-session":
            nxt = next(plan, 1)
            if nxt == "raise":
                raise subprocess.TimeoutExpired(argv, 15)
            return subprocess.CompletedProcess(argv, nxt, "", "")
        if verb == "set-option":
            store[argv[4]] = argv[5]
            calls.append((argv[4], argv[5]))
            return subprocess.CompletedProcess(argv, 0, "", "")
        if verb == "display-message":
            key = argv[5].strip("#{}")
            return subprocess.CompletedProcess(argv, 0, store.get(key, ""), "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    return fake_run


def test_status_updater_sets_ctx_once_then_seg_until_gone(monkeypatch):
    calls: list[tuple[str, str]] = []
    # present (initial guard), present (loop iter 1), gone (loop iter 2).
    monkeypatch.setattr(subprocess, "run", _fake_mux([0, 0, 1], calls))
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(m, "_render_status_segment", lambda *a, **k: "SEG")
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)

    rc = m.cmd_status_updater(_ns())

    assert rc == 0
    # Ownership claimed first, then identity once, then disposition.
    assert calls[0][0] == "@aw_updater"
    assert [c for c in calls if c[0] == "@aw_ctx"] == [("@aw_ctx", "CTX")]
    assert ("@aw_seg", "SEG") in calls


def test_status_updater_retires_when_token_taken_over(monkeypatch):
    """A newer updater claiming @aw_updater makes the older one retire."""
    calls: list[tuple[str, str]] = []
    store: dict[str, str] = {}
    monkeypatch.setattr(subprocess, "run", _fake_mux([0, 0, 0, 0], calls, store))
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(m, "_render_status_segment", lambda *a, **k: "SEG")
    # Simulate a newer updater stealing the token after the first tick.
    monkeypatch.setattr(
        time, "sleep",
        lambda *_a, **_k: store.__setitem__("@aw_updater", "another-pid"),
    )

    rc = m.cmd_status_updater(_ns())

    assert rc == 0
    # Exactly one disposition write before retiring on the stolen token.
    assert [c for c in calls if c[0] == "@aw_seg"] == [("@aw_seg", "SEG")]


def test_status_updater_noop_when_session_absent(monkeypatch):
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(subprocess, "run", _fake_mux([1], calls))  # gone at start
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(m, "_render_status_segment", lambda *a, **k: "SEG")
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)

    rc = m.cmd_status_updater(_ns())

    assert rc == 0
    assert calls == []  # never set any option (or claim a token) for a dead session


def test_status_updater_requires_session():
    assert m.cmd_status_updater(_ns(session="")) == 2


def test_status_updater_loop_requests_title_persistence(monkeypatch):
    """The loop must render the segment with persist_title=True so the
    daemon lands the resolved title in rec.title (the Picker's slot)."""
    flags: list[object] = []
    monkeypatch.setattr(subprocess, "run", _fake_mux([0, 0, 1], []))
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTX")

    def _seg(_path, **kw):
        flags.append(kw.get("persist_title"))
        return "SEG"

    monkeypatch.setattr(m, "_render_status_segment", _seg)
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)

    rc = m.cmd_status_updater(_ns())

    assert rc == 0
    assert flags and all(f is True for f in flags)


def test_status_updater_survives_render_errors(monkeypatch):
    """A transient render exception must not kill the loop or leak out."""
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(subprocess, "run", _fake_mux([0, 0, 1], calls))

    def boom(*_a, **_k):
        raise RuntimeError("git hiccup")

    monkeypatch.setattr(m, "_render_status_context", boom)
    monkeypatch.setattr(m, "_render_status_segment", boom)
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)

    rc = m.cmd_status_updater(_ns())

    assert rc == 0
    # ctx render raised -> no @aw_ctx; seg render raised -> empty @aw_seg set.
    assert ("@aw_seg", "") in calls


def test_status_updater_activates_project_from_path(monkeypatch):
    """The loop must resolve project context from --path before rendering.

    ``status-updater`` is a no-project command, so ``main()`` never sets an
    active project for it; without this the status renderers can't find the
    worktree's tracking record and the bar loses its repo:id locus + title.
    """
    seen: list[str | None] = []
    monkeypatch.setattr(subprocess, "run", _fake_mux([0, 0, 1], []))
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(m, "_render_status_segment", lambda *a, **k: "SEG")
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(m, "_activate_project_for_path", lambda p: seen.append(p))

    rc = m.cmd_status_updater(_ns(path="/w/x"))

    assert rc == 0
    assert seen == ["/w/x"]


def test_activate_project_for_path_sets_from_anchor(monkeypatch):
    """Resolve the project git-like from the path anchor and thread it in."""
    from pathlib import Path

    set_to: list[str | None] = []
    monkeypatch.setattr(m.cfg, "active_project", lambda: None)
    monkeypatch.setattr(m.cfg, "set_active_project", lambda n: set_to.append(n))
    monkeypatch.setattr(m, "_git_toplevel", lambda p: Path("/anchor/proj"))
    monkeypatch.setattr(m, "_reverse_lookup_project", lambda a: "proj")

    m._activate_project_for_path("/w/x")

    assert set_to == ["proj"]


def test_activate_project_for_path_noop_when_already_active(monkeypatch):
    """When a project is already active, don't touch resolution."""
    called: list[bool] = []
    monkeypatch.setattr(m.cfg, "active_project", lambda: "already")
    monkeypatch.setattr(
        m, "_git_toplevel", lambda p: called.append(True) or None
    )
    monkeypatch.setattr(
        m.cfg, "set_active_project",
        lambda n: (_ for _ in ()).throw(AssertionError("should not set")),
    )

    m._activate_project_for_path("/w/x")

    assert called == []


def test_activate_project_for_path_noop_outside_repo(monkeypatch):
    """No anchor (path not in a repo) -> leave the active project unset."""
    monkeypatch.setattr(m.cfg, "active_project", lambda: None)
    monkeypatch.setattr(m, "_git_toplevel", lambda p: None)
    monkeypatch.setattr(
        m.cfg, "set_active_project",
        lambda n: (_ for _ in ()).throw(AssertionError("should not set")),
    )

    m._activate_project_for_path("/not/a/repo")  # must not raise


# --- version-supersede self-reap (dotfiles #911) -------------------------

def test_slot_superseded_pure_comparison():
    root = "/home/u/.agent-worktrees/versions"
    # A different slot than active, both under versions/ -> superseded.
    assert m._slot_superseded(f"{root}/dev5", f"{root}/dev4", root) is True
    # Same slot -> not superseded.
    assert m._slot_superseded(f"{root}/dev5", f"{root}/dev5", root) is False


def test_slot_superseded_non_slot_interpreter_keeps_serving():
    """A dev/source or system-python interpreter (not under versions/) is never
    judged superseded -- degrade-safe."""
    root = "/home/u/.agent-worktrees/versions"
    assert m._slot_superseded(f"{root}/dev5", "/usr/lib/python3.11", root) is False
    assert m._slot_superseded(f"{root}/dev5", "/src/checkout/.venv", root) is False


def test_slot_superseded_no_partial_prefix_match():
    """``versions-old`` must not count as under ``versions`` (path-segment safe)."""
    root = "/home/u/.agent-worktrees/versions"
    mine = "/home/u/.agent-worktrees/versions-x/dev5"
    assert m._slot_superseded(f"{root}/dev5", mine, root) is False


def test_runtime_superseded_true_when_active_slot_differs(tmp_path):
    versions = tmp_path / "versions"
    (versions / "dev4").mkdir(parents=True)
    (versions / "dev5").mkdir(parents=True)
    # The current-version marker names the active slot (dev5); this process
    # pretends to run from dev4 -- an older, superseded slot. Resolution is
    # marker-based (junction-free, #1106), so no symlink privilege is needed.
    (tmp_path / "current-version").write_text("dev5", encoding="utf-8")
    assert m._runtime_superseded(
        prefix=str(versions / "dev4"), install_root=tmp_path
    ) is True
    assert m._runtime_superseded(
        prefix=str(versions / "dev5"), install_root=tmp_path
    ) is False


def test_runtime_superseded_degrades_safe_on_missing_root(tmp_path):
    """No versions/ layout at all -> never superseded (keep serving)."""
    assert m._runtime_superseded(
        prefix=str(tmp_path / "whatever"), install_root=tmp_path / "nope"
    ) is False


def test_status_updater_retires_when_runtime_superseded(monkeypatch):
    """A superseded updater (newer runtime active) retires at once, before
    claiming a token or writing any bar option (dotfiles #911)."""
    calls: list[tuple[str, str]] = []
    # Session is present, but the runtime is superseded -> early return.
    monkeypatch.setattr(subprocess, "run", _fake_mux([0, 0, 0], calls))
    monkeypatch.setattr(m, "_runtime_superseded", lambda *a, **k: True)
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(m, "_render_status_segment", lambda *a, **k: "SEG")
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)

    rc = m.cmd_status_updater(_ns())

    assert rc == 0
    assert calls == []  # no token claim, no @aw_ctx/@aw_seg writes


def test_status_updater_loop_exits_when_runtime_becomes_superseded(monkeypatch):
    """An updater running fine retires on the tick after a newer runtime lands."""
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(subprocess, "run", _fake_mux([0, 0, 0, 0], calls))
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(m, "_render_status_segment", lambda *a, **k: "SEG")
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)
    # Not superseded at startup; becomes superseded after the first tick.
    seq = iter([False, False, True])
    monkeypatch.setattr(m, "_runtime_superseded", lambda *a, **k: next(seq, True))

    rc = m.cmd_status_updater(_ns())

    assert rc == 0
    # Exactly one disposition write before the supersede retires the loop.
    assert [c for c in calls if c[0] == "@aw_seg"] == [("@aw_seg", "SEG")]


# --- transient mux-failure tolerance (dotfiles #915) ---------------------

def test_status_updater_tolerates_transient_then_recovers(monkeypatch):
    """A transient mux failure mid-loop must NOT retire the bar: the tick is
    skipped and the loop keeps serving once the mux answers again."""
    calls: list[tuple[str, str]] = []
    # startup alive, iter1 alive (SEG), iter2 transient (skip), iter3 alive
    # (SEG), iter4 gone.
    monkeypatch.setattr(
        subprocess, "run",
        _fake_mux_transient([0, 0, "raise", 0, 1], calls),
    )
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(m, "_render_status_segment", lambda *a, **k: "SEG")
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)

    rc = m.cmd_status_updater(_ns())

    assert rc == 0
    # Two disposition writes: the transient tick was skipped, not fatal.
    assert [c for c in calls if c[0] == "@aw_seg"] == [
        ("@aw_seg", "SEG"), ("@aw_seg", "SEG"),
    ]


def test_status_updater_startup_tolerates_transient(monkeypatch):
    """A transient hiccup on the *startup* liveness probe must not abort the
    updater before it ever paints -- only a definitive ``gone`` bails."""
    calls: list[tuple[str, str]] = []
    # startup transient (must proceed), then loop sees gone and exits.
    monkeypatch.setattr(
        subprocess, "run", _fake_mux_transient(["raise", 1], calls),
    )
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(m, "_render_status_segment", lambda *a, **k: "SEG")
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)

    rc = m.cmd_status_updater(_ns())

    assert rc == 0
    # It proceeded past startup: claimed the token and rendered identity once.
    assert ("@aw_ctx", "CTX") in calls
    assert any(c[0] == "@aw_updater" for c in calls)


def test_status_updater_exits_after_sustained_transient_failure(monkeypatch):
    """A genuinely wedged mux (unbroken run of transient failures) eventually
    retires the updater instead of spinning forever."""
    calls: list[tuple[str, str]] = []
    # startup alive, then an unbroken run of transient failures.
    monkeypatch.setattr(
        subprocess, "run",
        _fake_mux_transient([0] + ["raise"] * 40, calls),
    )
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(m, "_render_status_segment", lambda *a, **k: "SEG")
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)

    rc = m.cmd_status_updater(_ns())

    assert rc == 0
    # No disposition ever landed (every liveness probe failed), and the loop
    # terminated rather than hanging.
    assert [c for c in calls if c[0] == "@aw_seg"] == []


# --- sessionStart reseed spawn helper (dotfiles #915) --------------------

def test_spawn_status_updater_noop_on_empty_id():
    assert m._spawn_status_updater("", "/w/x") is False


def test_spawn_status_updater_noop_without_mux(monkeypatch):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _x: None)
    popped: list = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: popped.append(a))

    assert m._spawn_status_updater("x", "/w/x") is False
    assert popped == []


def test_spawn_status_updater_noop_when_no_live_session(monkeypatch):
    import shutil

    monkeypatch.setattr(
        shutil, "which", lambda x: "/bin/psmux" if x == "psmux" else None)
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 1, "", ""))
    popped: list = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: popped.append(a))

    assert m._spawn_status_updater("x", "/w/x") is False
    assert popped == []  # never spawn a loop for a session that isn't under mux


def test_spawn_status_updater_spawns_detached_when_session_live(monkeypatch):
    import shutil

    monkeypatch.setattr(
        shutil, "which", lambda x: "/bin/psmux" if x == "psmux" else None)
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""))
    captured: dict = {}

    def fake_popen(argv, **kw):
        captured["argv"] = argv
        captured["kw"] = kw
        return object()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        m, "windowless_daemon_kwargs",
        lambda **_kw: {"windowless_daemon": True},
    )
    monkeypatch.setenv("GH_TOKEN", "secret")
    monkeypatch.setenv("GITHUB_TOKEN", "other-secret")
    monkeypatch.setenv("AGENT_WORKTREES_AHP_AUTH_TOKEN", "handoff-secret")
    monkeypatch.setenv("SAFE_VALUE", "kept")

    assert m._spawn_status_updater("x", "/w/x") is True
    argv = captured["argv"]
    assert argv[0] == m.sys.executable
    assert argv[1:4] == ["-m", "agent_worktrees", "status-updater"]
    assert argv[argv.index("--session") + 1] == "wt-x"
    assert argv[argv.index("--mux") + 1] == "psmux"
    assert argv[argv.index("--path") + 1] == "/w/x"
    # Detached: stdio is silenced so the loop never blocks on the hook's pipes.
    assert captured["kw"].get("stdin") is subprocess.DEVNULL
    assert captured["kw"]["windowless_daemon"] is True
    assert captured["kw"]["env"]["SAFE_VALUE"] == "kept"
    assert "GH_TOKEN" not in captured["kw"]["env"]
    assert "GITHUB_TOKEN" not in captured["kw"]["env"]
    assert "AGENT_WORKTREES_AHP_AUTH_TOKEN" not in captured["kw"]["env"]


def test_spawn_status_updater_omits_path_when_absent(monkeypatch):
    import shutil

    monkeypatch.setattr(
        shutil, "which", lambda x: "/bin/tmux" if x == "tmux" else None)
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""))
    captured: dict = {}
    monkeypatch.setattr(
        subprocess, "Popen",
        lambda argv, **kw: captured.update(argv=argv) or object())

    assert m._spawn_status_updater("x", None) is True
    assert "--path" not in captured["argv"]
    assert captured["argv"][captured["argv"].index("--mux") + 1] == "tmux"


def test_spawn_status_updater_roots_child_at_home_not_payload(monkeypatch):
    """The detached updater must NOT inherit the spawner's cwd (the plugin
    payload dir under the hook/launcher): a child holding the payload as its cwd
    blocks ``copilot plugin update`` on Windows (os error 32).  It is rooted at
    HOME instead."""
    import os as _os
    import shutil

    monkeypatch.setattr(
        shutil, "which", lambda x: "/bin/psmux" if x == "psmux" else None)
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""))
    captured: dict = {}
    monkeypatch.setattr(
        subprocess, "Popen",
        lambda argv, **kw: captured.update(kw=kw) or object())

    assert m._spawn_status_updater("x", "/w/x") is True
    assert captured["kw"].get("cwd") == _os.path.expanduser("~")


# --- spawn debounce: don't leak a pair of updaters (dotfiles #911) --------

def _fake_mux_store(has_session_codes, calls, store):
    """A fake mux whose display-message reads back a pre-seeded ``store`` so a
    debounce probe sees an existing owner token/prefix."""
    codes = iter(has_session_codes)

    def fake_run(argv, **_kw):
        verb = argv[1]
        if verb == "has-session":
            return subprocess.CompletedProcess(argv, next(codes, 1), "", "")
        if verb == "set-option":
            store[argv[4]] = argv[5]
            calls.append((argv[4], argv[5]))
            return subprocess.CompletedProcess(argv, 0, "", "")
        if verb == "display-message":
            key = argv[5].strip("#{}")
            return subprocess.CompletedProcess(argv, 0, store.get(key, ""), "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    return fake_run


def test_status_updater_debounces_live_current_owner(monkeypatch):
    """A second spawn retires immediately (no token claim, no bar writes) when a
    live updater already owns the session on the *current* runtime."""
    calls: list[tuple[str, str]] = []
    store = {"@aw_updater": "99999", "@aw_updater_prefix": "/slot/current"}
    monkeypatch.setattr(subprocess, "run", _fake_mux_store([0], calls, store))
    monkeypatch.setattr(us, "_pid_alive", lambda _pid: True)
    # Not superseded for either the no-arg self-check or the owner-prefix check.
    monkeypatch.setattr(m, "_runtime_superseded", lambda *a, **k: False)
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(m, "_render_status_segment", lambda *a, **k: "SEG")
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)

    rc = m.cmd_status_updater(_ns())

    assert rc == 0
    # Debounced before claiming: no @aw_updater token, no bar options written.
    assert calls == []


def test_status_updater_replaces_superseded_owner(monkeypatch):
    """A live but *superseded* owner (mid-deploy) is replaced, not deferred to --
    the reseed must keep the bar alive after a deploy (dotfiles #915)."""
    calls: list[tuple[str, str]] = []
    store = {"@aw_updater": "99999", "@aw_updater_prefix": "/slot/old"}
    monkeypatch.setattr(subprocess, "run", _fake_mux_store([0, 0, 1], calls, store))
    monkeypatch.setattr(us, "_pid_alive", lambda _pid: True)
    # No-arg self-check: not superseded (proceed); owner-prefix check: superseded.
    monkeypatch.setattr(
        m, "_runtime_superseded",
        lambda *a, prefix=None, **k: prefix is not None)
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(m, "_render_status_segment", lambda *a, **k: "SEG")
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)

    rc = m.cmd_status_updater(_ns())

    assert rc == 0
    # It proceeded: claimed the token and published its own runtime prefix.
    assert any(c[0] == "@aw_updater" for c in calls)
    assert any(c[0] == "@aw_updater_prefix" for c in calls)


def test_status_updater_replaces_dead_owner(monkeypatch):
    """A stale token pointing at a dead pid never blocks a spawn."""
    calls: list[tuple[str, str]] = []
    store = {"@aw_updater": "99999", "@aw_updater_prefix": "/slot/current"}
    monkeypatch.setattr(subprocess, "run", _fake_mux_store([0, 0, 1], calls, store))
    monkeypatch.setattr(us, "_pid_alive", lambda _pid: False)  # owner dead
    monkeypatch.setattr(m, "_runtime_superseded", lambda *a, **k: False)
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(m, "_render_status_segment", lambda *a, **k: "SEG")
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)

    rc = m.cmd_status_updater(_ns())

    assert rc == 0
    assert any(c[0] == "@aw_updater" for c in calls)  # proceeded to claim


def test_status_updater_replaces_owner_without_published_prefix(monkeypatch):
    """A pre-upgrade owner (live pid but no @aw_updater_prefix) is replaced, not
    deferred to -- so the one-time upgrade transition can't wedge a dark bar."""
    calls: list[tuple[str, str]] = []
    store = {"@aw_updater": "99999"}  # note: no @aw_updater_prefix
    monkeypatch.setattr(subprocess, "run", _fake_mux_store([0, 0, 1], calls, store))
    monkeypatch.setattr(us, "_pid_alive", lambda _pid: True)
    monkeypatch.setattr(m, "_runtime_superseded", lambda *a, **k: False)
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(m, "_render_status_segment", lambda *a, **k: "SEG")
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)

    rc = m.cmd_status_updater(_ns())

    assert rc == 0
    assert any(c[0] == "@aw_updater" for c in calls)  # proceeded to claim


def test_status_updater_publishes_runtime_prefix_on_claim(monkeypatch):
    """The updater publishes its own ``sys.prefix`` so a later spawn can tell a
    current owner (defer) from a superseded one (replace)."""
    import os as _os
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(subprocess, "run", _fake_mux([0, 0, 1], calls))
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(m, "_render_status_segment", lambda *a, **k: "SEG")
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)

    rc = m.cmd_status_updater(_ns())

    assert rc == 0
    assert ("@aw_updater_prefix", _os.path.realpath(sys.prefix)) in calls
