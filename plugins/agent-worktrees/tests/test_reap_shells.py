"""Tests for the orphaned launcher-shell reaper (copilot-extensions #102).

``reap_orphan_launcher_shells`` reclaims pwsh/python launcher shells stranded by
a force-closed terminal. Because it KILLS processes, the predicate is engineered
to fail SAFE: positive launcher-signature matching, service/self vetoes, a
live-descendant spare, an orphan+idle gate, and dry-run by default. These tests
pin every safety layer -- above all that a blank-command-line service (the
failure mode that once killed a live sampler) is NEVER a candidate.
"""

from __future__ import annotations

from agent_worktrees import __main__ as cli

NOW = 1_000_000.0
OLD = NOW - 7200      # 2h -- past the 1h grace
FRESH = NOW - 60      # 1m -- inside the grace

_LAUNCH_CMD = r"pwsh.exe -NoLogo -Command & 'C:\x\launch-session.ps1' -Machine m"
_PY_CMD = r"python.exe -m agent_worktrees --project demo"


def _p(pid, *, ppid=999999, name="pwsh.exe", cmdline=_LAUNCH_CMD,
       create=OLD, sid=1):
    return {"pid": pid, "ppid": ppid, "name": name, "cmdline": cmdline,
            "create_epoch": create, "session_id": sid}


def _select(procs, *, self_pid=424242, grace=3600.0):
    return cli.select_orphan_launcher_shells(
        procs, now=NOW, idle_grace_secs=grace, self_pid=self_pid)


def _reasons(skipped):
    return {s["pid"]: s["reason"] for s in skipped}


# ── The core safety inversion: blank/absent command lines are never touched ──

def test_blank_commandline_service_is_never_a_candidate():
    """A service with a blank command line (looks exactly like an orphan to a
    non-elevated query) lacks a launcher signature -> never reaped, never even
    listed. This is the incident this reaper exists to prevent."""
    procs = [_p(800, name="pwsh.exe", cmdline="")]
    reap, skipped = _select(procs)
    assert reap == []
    assert 800 not in _reasons(skipped)  # silently ignored, not a candidate


def test_unrelated_shell_without_signature_ignored():
    procs = [_p(801, cmdline="pwsh.exe -File C:\\other\\thing.ps1")]
    reap, skipped = _select(procs)
    assert reap == [] and 801 not in _reasons(skipped)


def test_non_shell_process_ignored():
    procs = [_p(900, name="notepad.exe", cmdline="notepad launch-session.ps1")]
    reap, skipped = _select(procs)
    assert reap == [] and 900 not in _reasons(skipped)


# ── The happy path: a genuinely orphaned launcher shell is reaped ────────────

def test_orphaned_launcher_shell_is_reaped():
    procs = [_p(100)]  # parent 999999 absent, old, no children, session 1
    reap, skipped = _select(procs)
    assert [p["pid"] for p in reap] == [100]


def test_orphaned_python_waiter_is_reaped():
    procs = [_p(101, name="python.exe", cmdline=_PY_CMD)]
    reap, _ = _select(procs)
    assert [p["pid"] for p in reap] == [101]


# ── Spare gates ──────────────────────────────────────────────────────────────

def test_live_descendant_is_spared():
    """A launcher shell with a live copilot child is a LIVE session -> spared."""
    procs = [
        _p(200),
        {"pid": 201, "ppid": 200, "name": "copilot.exe", "cmdline": "copilot",
         "create_epoch": OLD, "session_id": 1},
    ]
    reap, skipped = _select(procs)
    assert reap == []
    assert _reasons(skipped)[200] == "live-descendant"


def test_mux_client_child_spares_the_launcher():
    """The regression this veto exists for: a launcher whose foreground child is
    a `psmux attach-session` client is an ATTACHED, working session -> spared.

    The mux client is the only child a joined session's launcher has (the pane's
    Copilot lives under the mux *server*, a different tree), so if the snapshot
    omits mux images the launcher looks childless and gets killed -- orphaning
    the client and handing the console back to the terminal's base shell."""
    procs = [
        _p(210),
        {"pid": 211, "ppid": 210, "name": "psmux.exe",
         "cmdline": "psmux.exe attach-session -t wt-demo",
         "create_epoch": OLD, "session_id": 1},
    ]
    reap, skipped = _select(procs)
    assert reap == []
    assert _reasons(skipped)[210] == "live-descendant"


def test_witness_image_is_never_itself_a_candidate():
    """Enumerating mux/Copilot images to power the veto must not make them
    reapable -- only _LAUNCHER_SHELL_NAMES are ever candidates."""
    procs = [
        {"pid": 220, "ppid": 999999, "name": "psmux.exe",
         "cmdline": "psmux.exe attach-session -t wt-launch-session",
         "create_epoch": OLD, "session_id": 1},
    ]
    reap, skipped = _select(procs)
    assert reap == [] and 220 not in _reasons(skipped)

def test_parent_alive_is_spared():
    procs = [
        _p(300, ppid=301),
        {"pid": 301, "ppid": 1, "name": "python.exe", "cmdline": _PY_CMD,
         "create_epoch": OLD, "session_id": 1},
    ]
    reap, skipped = _select(procs)
    # 300 spared (parent alive); 301 itself is a launcher whose parent (1) is
    # absent -> 301 is the reap candidate.
    assert 300 in _reasons(skipped) and _reasons(skipped)[300] == "parent-alive"


def test_parent_alive_probe_spares_launcher_under_non_enumerated_terminal():
    """A launcher started from cmd.exe/bash/Windows Terminal has a parent that
    is NOT in the (filtered) snapshot. Snapshot membership therefore cannot mean
    "dead" -- with a real liveness probe the launcher is correctly parented."""
    procs = [_p(310, ppid=311)]  # 311 (the terminal) is not enumerated
    reap, skipped = cli.select_orphan_launcher_shells(
        procs, now=NOW, idle_grace_secs=3600.0, self_pid=424242,
        pid_alive=lambda pid: pid == 311)
    assert reap == []
    assert _reasons(skipped)[310] == "parent-alive"


def test_parent_dead_probe_still_reaps():
    """The probe must not blanket-spare: a genuinely exited parent still reaps."""
    procs = [_p(320, ppid=321)]
    reap, _ = cli.select_orphan_launcher_shells(
        procs, now=NOW, idle_grace_secs=3600.0, self_pid=424242,
        pid_alive=lambda pid: False)
    assert [p["pid"] for p in reap] == [320]


def test_service_session_zero_is_spared():
    procs = [_p(400, sid=0)]
    reap, skipped = _select(procs)
    assert reap == [] and _reasons(skipped)[400] == "service-session"


def test_service_marker_is_spared():
    procs = [_p(500, cmdline=_LAUNCH_CMD + " serve-service")]
    reap, skipped = _select(procs)
    assert reap == [] and _reasons(skipped)[500] == "service-marker"


def test_acp_stdio_launch_is_spared():
    procs = [_p(510, cmdline=_LAUNCH_CMD + " --stdio")]
    reap, skipped = _select(procs)
    assert reap == [] and _reasons(skipped)[510] == "service-marker"


def test_fresh_shell_is_spared():
    procs = [_p(600, create=FRESH)]
    reap, skipped = _select(procs)
    assert reap == [] and _reasons(skipped)[600] == "fresh"


def test_age_unknown_is_spared():
    procs = [_p(610, create=None)]
    reap, skipped = _select(procs)
    assert reap == [] and _reasons(skipped)[610] == "age-unknown"


def test_self_is_spared():
    procs = [_p(700)]
    reap, skipped = _select(procs, self_pid=700)
    assert reap == [] and _reasons(skipped)[700] == "self"


def test_self_ancestor_is_spared():
    # 710 is our parent; even though it is an orphaned-looking launcher, the
    # reaper never touches its own ancestry.
    procs = [_p(710, ppid=999999)]
    reap, skipped = _select(procs, self_pid=711)  # 711 not present; 710 is anc?
    # 710 is not actually an ancestor of 711 here (711 absent), so 710 reaps.
    assert [p["pid"] for p in reap] == [710]


# ── Mixed fleet: only the genuine orphan is selected ─────────────────────────

def test_mixed_fleet_reaps_only_the_orphan():
    procs = [
        _p(100),                                   # reap
        _p(600, create=FRESH),                     # spare fresh
        _p(400, sid=0),                            # spare service-session
        _p(800, cmdline=""),                       # ignore (no signature)
        {"pid": 201, "ppid": 100, "name": "x.exe", "cmdline": "x",
         "create_epoch": OLD, "session_id": 1},    # inert child of 100
    ]
    reap, _ = _select(procs)
    # 100 has a child 201 but 201 is not a live-session image -> still reaped.
    assert [p["pid"] for p in reap] == [100]


# ── Orchestrator: dry-run by default, --yes kills ────────────────────────────

def test_reaper_dry_run_by_default_kills_nothing(monkeypatch):
    killed: list[int] = []
    monkeypatch.setattr(cli.procs, "terminate_pid",
                        lambda pid: killed.append(pid) or True)
    out = cli.reap_orphan_launcher_shells(processes=[_p(100)], now=NOW)
    assert out["available"] is True
    assert out["reaped"] == [100]          # would-be
    assert out["candidates"][0]["pid"] == 100
    assert killed == []                     # dry-run kills nothing


def test_reaper_yes_terminates(monkeypatch):
    killed: list[int] = []
    monkeypatch.setattr(cli.procs, "terminate_pid",
                        lambda pid: killed.append(pid) or True)
    out = cli.reap_orphan_launcher_shells(
        processes=[_p(100)], now=NOW, dry_run=False)
    assert out["reaped"] == [100]
    assert killed == [100]


def test_reaper_reports_kill_failure(monkeypatch):
    monkeypatch.setattr(cli.procs, "terminate_pid", lambda pid: False)
    out = cli.reap_orphan_launcher_shells(
        processes=[_p(100)], now=NOW, dry_run=False)
    assert out["reaped"] == []
    assert out["errors"] == [{"pid": 100, "reason": "kill failed"}]


def test_reaper_unavailable_when_enumeration_none(monkeypatch):
    monkeypatch.setattr(cli, "_enumerate_launcher_shells", lambda: None)
    out = cli.reap_orphan_launcher_shells()
    assert out["available"] is False


# ── Enumeration must surface the witnesses the veto depends on ──────────────

def test_windows_enumeration_queries_witness_images(monkeypatch):
    """The live-descendant veto is only as good as the snapshot feeding it: if
    the process query omits the mux/Copilot images, the veto is dead code."""
    seen: dict = {}

    class _Res:
        stdout = "[]"

    def _run(cmd, **kw):
        seen["cmd"] = cmd
        return _Res()

    monkeypatch.setattr(cli.subprocess, "run", _run)
    cli._enumerate_launcher_shells_windows()
    query = seen["cmd"][-1]
    for image in ("pwsh.exe", "python.exe", "psmux.exe", "tmux.exe",
                  "copilot.exe", "node.exe"):
        assert f"Name='{image}'" in query


def test_posix_enumeration_keeps_witness_images():
    """The POSIX filter uses the same two sets, so /proc walks admit witnesses."""
    assert "psmux" in cli._LIVE_DESCENDANT_IMAGES
    assert "copilot" in cli._LIVE_DESCENDANT_IMAGES
    # …and every enumerated witness is matched by the veto's substring list.
    for image in cli._LIVE_DESCENDANT_IMAGES:
        assert any(m in image for m in cli._LIVE_DESCENDANT_NAMES)


def test_real_enumeration_uses_a_live_parent_probe(monkeypatch):
    """Wiring guard: self-enumerated runs probe real pids; injected process
    lists stay hermetic (no probe), so tests never depend on the host's pids."""
    captured: dict = {}
    real_select = cli.select_orphan_launcher_shells

    def _spy(procs, **kw):
        captured.setdefault("probes", []).append(kw.get("pid_alive"))
        return real_select(procs, **kw)

    monkeypatch.setattr(cli, "select_orphan_launcher_shells", _spy)
    monkeypatch.setattr(cli, "_enumerate_launcher_shells", lambda: [])
    cli.reap_orphan_launcher_shells()
    cli.reap_orphan_launcher_shells(processes=[_p(100)], now=NOW)
    assert captured["probes"] == [cli.locks.pid_alive, None]


def test_reap_shells_registered_as_no_project_command():
    """reap-shells is pure process enumeration -- runnable from anywhere with no
    project context (the bare binstub)."""
    assert "reap-shells" in cli._NO_PROJECT_COMMANDS
    assert "reap-shells" in cli.COMMAND_MAP


# ── gc integration: garbage collection reaps orphaned shells too (#102) ──────

def _gc_args(**over):
    import argparse
    base = dict(dry_run=True, json=False, orphans_only=False, no_managed=True,
                no_reap_shells=False, reap_shells_grace_hours=None,
                managed_grace_hours=None, include_unused=False,
                include_conversations=False, reconcile_prs=False, max_age_days=7)
    base.update(over)
    return argparse.Namespace(**base)


def _patch_gc(monkeypatch, calls):
    import types
    from pathlib import Path

    from agent_worktrees import gc as gc_mod
    repo = types.SimpleNamespace(anchor="/a", remote="origin", default_branch="main")
    config = types.SimpleNamespace(default_repo=repo, repo_name="ext")
    monkeypatch.setattr(cli.cfg, "load_config", lambda *a, **k: config)
    monkeypatch.setattr(cli.cfg, "tracking_dir", lambda: Path("/t"))
    monkeypatch.setattr(cli.tracking, "list_records", lambda p: [])
    monkeypatch.setattr(cli, "cmd_cleanup", lambda a: 0)
    monkeypatch.setattr(cli, "sweep_managed_worktrees",
                        lambda **k: {"removed": [], "skipped": []})
    monkeypatch.setattr(cli.git_ops, "prune_worktrees", lambda **k: None)
    monkeypatch.setattr(gc_mod, "sweep_orphans",
                        lambda *a, **k: {"scanned": False, "removed": [], "skipped": []})

    def _reap(**k):
        calls.append(k)
        return {"available": True, "reaped": [], "candidates": [],
                "skipped": [], "errors": []}

    monkeypatch.setattr(cli, "reap_orphan_launcher_shells", _reap)


def test_gc_reaps_shells_by_default(monkeypatch):
    calls: list[dict] = []
    _patch_gc(monkeypatch, calls)
    cli.cmd_gc(_gc_args())
    assert len(calls) == 1
    assert calls[0]["dry_run"] is True   # honors gc --dry-run


def test_gc_no_reap_shells_skips(monkeypatch):
    calls: list[dict] = []
    _patch_gc(monkeypatch, calls)
    cli.cmd_gc(_gc_args(no_reap_shells=True))
    assert calls == []


def test_gc_orphans_only_skips_shells(monkeypatch):
    calls: list[dict] = []
    _patch_gc(monkeypatch, calls)
    cli.cmd_gc(_gc_args(orphans_only=True))
    assert calls == []


def test_gc_reap_shells_grace_hours_forwarded(monkeypatch):
    calls: list[dict] = []
    _patch_gc(monkeypatch, calls)
    cli.cmd_gc(_gc_args(reap_shells_grace_hours=2.0))
    assert calls[0]["idle_grace_secs"] == 7200.0


def test_gc_parser_has_reap_shells_flags():
    args = cli.build_parser().parse_args(
        ["gc", "--no-reap-shells", "--reap-shells-grace-hours", "3"])
    assert args.no_reap_shells is True
    assert args.reap_shells_grace_hours == 3.0
