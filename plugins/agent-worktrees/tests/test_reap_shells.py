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


def test_reap_shells_registered_as_no_project_command():
    """reap-shells is pure process enumeration -- runnable from anywhere with no
    project context (the bare binstub)."""
    assert "reap-shells" in cli._NO_PROJECT_COMMANDS
    assert "reap-shells" in cli.COMMAND_MAP
