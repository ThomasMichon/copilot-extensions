"""Phase 2 tests: prerequisite detection, provisioning plan, and core install.

No test installs production software or changes the machine. Apply controls
use isolated fake providers; the consumer control provisions only a synthetic
executable beneath the test's temporary directory.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from worktree_manager.catalog import Prereq
from worktree_manager.core_install import (
    core_status,
    install_command,
    install_core,
)
from worktree_manager.prereqs import (
    PrereqStatus,
    _ge,
    detect_baseline,
    detect_prereq,
    missing,
)
from worktree_manager.provision import apply, plan, recipe_for, restart_needed
from worktree_manager.__main__ import main


# ── detection ───────────────────────────────────────────────────────────────

def test_version_compare():
    assert _ge("2.43.0", "2.15")
    assert _ge("3.10.4", "3.10")
    assert not _ge("3.9.1", "3.10")
    assert _ge("anything", "")  # unparseable minimum never blocks


def test_detect_prereq_present_tool():
    # git is present in CI and locally; detection should find it with a version.
    st = detect_prereq(Prereq(name="git", min="2.15"))
    if st.present:
        assert st.path
        assert st.satisfied


def test_detect_prereq_absent_tool():
    st = detect_prereq(Prereq(name="definitely-not-a-real-tool-xyz"))
    assert st.present is False
    assert st.satisfied is False


def test_optional_absent_is_satisfied():
    st = PrereqStatus(name="psmux", present=False, optional=True)
    assert st.satisfied is True


def test_detect_baseline_covers_catalog():
    statuses = detect_baseline()
    names = {s.name for s in statuses}
    assert {"git", "python3", "uv"} <= names


def test_missing_filters_unsatisfied():
    statuses = [
        PrereqStatus(name="git", present=True, version="2.55", min_required="2.15"),
        PrereqStatus(name="uv", present=False),
        PrereqStatus(name="psmux", present=False, optional=True),
    ]
    names = {s.name for s in missing(statuses)}
    assert names == {"uv"}  # satisfied git out; optional-absent psmux out


# ── provisioning plan ───────────────────────────────────────────────────────

def test_recipe_shapes():
    uv = recipe_for("uv", "linux")
    assert uv is not None and uv.auto and uv.changes_path
    git = recipe_for("git", "windows")
    assert git is not None and git.manual and not git.auto
    py = recipe_for("python3", "macos")
    assert py is not None and py.requires == "uv"


def test_plan_orders_dependencies_first():
    gaps = [
        PrereqStatus(name="python3", present=False, min_required="3.10"),
        PrereqStatus(name="uv", present=False),
    ]
    actions = plan(gaps, os_="linux")
    names = [a.name for a in actions]
    assert names.index("uv") < names.index("python3")


def test_apply_dry_run_runs_nothing():
    gaps = [PrereqStatus(name="uv", present=False)]
    actions = plan(gaps, os_="linux")
    results = apply(actions, dry_run=True)
    assert all(not r.ran for r in results)
    assert all(r.skipped_reason == "dry-run" for r in results if r.action.auto)
    assert restart_needed(results) is False  # nothing actually ran


def test_manual_action_never_runs_even_when_applied():
    gaps = [PrereqStatus(name="git", present=False)]
    actions = plan(gaps, os_="linux")
    # dry_run=False, but a manual recipe must still not execute.
    results = apply(actions, dry_run=False)
    assert all(not r.ran for r in results)
    assert results[0].skipped_reason == "manual"


# ── core install driver ─────────────────────────────────────────────────────

def test_core_status_absent(tmp_path: Path):
    st = core_status(home=tmp_path)
    assert st.state == "absent"
    assert not st.installed


def test_core_status_partial(tmp_path: Path):
    (tmp_path / ".agent-worktrees" / ".venv").mkdir(parents=True)
    st = core_status(home=tmp_path)
    assert st.state == "partial"  # runtime+venv but no binstub


def test_core_status_installed(tmp_path: Path):
    (tmp_path / ".agent-worktrees" / ".venv").mkdir(parents=True)
    localbin = tmp_path / ".local" / "bin"
    localbin.mkdir(parents=True)
    (localbin / "agent-worktrees").write_text("#!stub\n")
    st = core_status(home=tmp_path)
    assert st.state == "installed"
    assert st.installed


def test_install_command_from_checkout():
    cmd = install_command()
    # Inside a checkout the real installer resolves; otherwise None (no checkout).
    if cmd is not None:
        assert "install" in cmd
        assert any("install.ps1" in c or "install.sh" in c for c in cmd)


def test_install_core_is_noop_when_installed(tmp_path: Path):
    (tmp_path / ".agent-worktrees" / ".venv").mkdir(parents=True)
    localbin = tmp_path / ".local" / "bin"
    localbin.mkdir(parents=True)
    (localbin / "agent-worktrees").write_text("#!stub\n")
    res = install_core(dry_run=True, home=tmp_path)
    assert res.ran is False
    assert res.reason == "already-installed"


def test_install_core_dry_run_plans_but_does_not_run(tmp_path: Path):
    res = install_core(dry_run=True, home=tmp_path)
    assert res.ran is False
    # Either a planned command (checkout present) or a clear no-installer reason.
    assert res.reason in ("dry-run",) or "no-installer" in (res.reason or "")


# ── command surface ─────────────────────────────────────────────────────────

def test_doctor_command_runs(capsys, monkeypatch, tmp_path):
    from worktree_manager import daemon_health

    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path))
    monkeypatch.setattr(
        daemon_health.mux_daemon_cutover, "_iter_mux_daemon_pids", lambda: set(),
    )
    rc = main(["doctor"])
    out = capsys.readouterr().out
    assert "doctor" in out.lower()
    assert "agent-worktrees core" in out
    assert rc in (0, 1)


def test_setup_command_dry_run_changes_nothing(capsys):
    rc = main(["setup"])
    out = capsys.readouterr().out
    assert "plan" in out.lower()
    assert "nothing was changed" in out.lower()
    assert rc in (0, 1)


def _setup_providers(monkeypatch, *, before, after, results, core_result=None):
    from worktree_manager import setup_cli as cli
    from worktree_manager.core_install import CoreInstallResult, CoreStatus

    probes = iter((before, after))
    monkeypatch.setattr(cli, "detect_baseline", lambda: next(probes))
    monkeypatch.setattr(cli, "provision_apply", lambda *a, **kw: results)
    status = CoreStatus("installed", True, True, "stub", "runtime")
    monkeypatch.setattr(cli, "core_status", lambda: status)
    monkeypatch.setattr(cli, "install_command", lambda: ["owned-installer"])
    calls = []

    def install(**kwargs):
        calls.append(kwargs)
        return core_result or CoreInstallResult(None, False, reason="already-installed")

    monkeypatch.setattr(cli, "install_core", install)
    return calls


def test_setup_apply_uses_post_provision_readiness(monkeypatch, capsys):
    from worktree_manager.provision import ProvisionResult

    gap = PrereqStatus("uv", False)
    action = plan([gap])[0]
    calls = _setup_providers(
        monkeypatch, before=[gap], after=[PrereqStatus("uv", True)],
        results=[ProvisionResult(action, True, returncode=0)],
    )
    assert main(["setup", "--apply"]) == 0
    assert len(calls) == 1
    assert "RESTART" not in capsys.readouterr().out


@pytest.mark.parametrize("returncode", [1, 3010, None])
def test_setup_failed_prerequisite_never_runs_core(monkeypatch, capsys, returncode):
    from worktree_manager.provision import ProvisionResult

    gap = PrereqStatus("uv", False)
    calls = _setup_providers(
        monkeypatch, before=[gap], after=[PrereqStatus("uv", True)],
        results=[ProvisionResult(plan([gap])[0], True, returncode=returncode)],
    )
    assert main(["setup", "--apply"]) == 1
    assert not calls
    assert "prerequisite uv failed" in capsys.readouterr().out


def test_setup_unstarted_prerequisite_reports_reason(monkeypatch, capsys):
    from worktree_manager.provision import ProvisionResult

    gap = PrereqStatus("uv", False)
    calls = _setup_providers(
        monkeypatch, before=[gap], after=[gap],
        results=[ProvisionResult(plan([gap])[0], False, skipped_reason="access denied")],
    )
    assert main(["setup", "--apply"]) == 1
    assert not calls
    assert "access denied" in capsys.readouterr().out


@pytest.mark.parametrize("name", ["uv", "git"])
def test_setup_unresolved_prerequisite_blocks_core(monkeypatch, capsys, name):
    from worktree_manager.provision import ProvisionResult

    gap = PrereqStatus(name, False)
    action = plan([gap])[0]
    result = (
        ProvisionResult(action, False, skipped_reason="manual") if action.manual
        else ProvisionResult(action, True, returncode=0)
    )
    calls = _setup_providers(
        monkeypatch, before=[gap], after=[gap], results=[result],
    )
    assert main(["setup", "--apply"]) == 1
    assert not calls
    out = capsys.readouterr().out
    assert f"prerequisites still needed: {name}" in out
    assert ("RESTART" in out) == result.ran


@pytest.mark.parametrize(
    "ran,returncode,reason",
    [(True, 7, None), (False, None, "no-installer"), (False, None, None)],
)
def test_setup_core_failure_is_not_hidden_by_installed_state(
    monkeypatch, capsys, ran, returncode, reason,
):
    from worktree_manager.core_install import CoreInstallResult

    calls = _setup_providers(
        monkeypatch, before=[], after=[], results=[],
        core_result=CoreInstallResult(["owned-installer"], ran, returncode, reason),
    )
    assert main(["setup", "--apply"]) == 1
    assert len(calls) == 1
    assert "core install failed" in capsys.readouterr().out


@pytest.mark.parametrize("installed", [True, False])
def test_setup_first_core_install_checks_postcondition(monkeypatch, installed):
    from worktree_manager import setup_cli as cli
    from worktree_manager.core_install import CoreInstallResult, CoreStatus

    calls = _setup_providers(
        monkeypatch, before=[], after=[], results=[],
        core_result=CoreInstallResult(["owned-installer"], True, 0),
    )
    absent = CoreStatus("absent", False, False, None, "runtime")
    ready = CoreStatus("installed", True, True, "stub", "runtime")
    states = iter((absent, ready if installed else absent))
    monkeypatch.setattr(cli, "core_status", lambda: next(states))
    assert main(["setup", "--apply"]) == (0 if installed else 1)
    assert len(calls) == 1


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("filesystem")
@pytest.mark.effect("process")
def test_setup_refreshes_actual_tool_after_synthetic_installer(
    monkeypatch, tmp_path, capsys,
):
    import os
    import shlex
    import subprocess
    import sys

    from worktree_manager import prereqs, setup_cli as cli
    from worktree_manager.core_install import CoreInstallResult, CoreStatus
    from worktree_manager.provision import ProvisionAction

    windows = os.name == "nt"
    tool = tmp_path / ("fixture-tool.cmd" if windows else "fixture-tool")
    content = "@echo 1.0.0\n" if windows else "#!/bin/sh\necho 1.0.0\n"
    script = (
        "from pathlib import Path;"
        f"p=Path({str(tool)!r});p.write_text({content!r});p.chmod(0o700)"
    )
    args = [sys.executable, "-c", script]
    command = subprocess.list2cmdline(args) if windows else shlex.join(args)
    action = ProvisionAction(
        "fixture-tool", "script", command, False, None, False, None,
    )
    monkeypatch.setitem(prereqs._EXE_ALIASES, "fixture-tool", (str(tool),))
    monkeypatch.setattr(
        cli, "detect_baseline",
        lambda: [detect_prereq(Prereq("fixture-tool", min="1.0"))],
    )
    monkeypatch.setattr(cli, "provision_plan", lambda gaps: [action] if gaps else [])
    monkeypatch.setattr(
        cli, "core_status",
        lambda: CoreStatus("installed", True, True, "stub", "runtime"),
    )
    monkeypatch.setattr(cli, "install_command", lambda: None)
    monkeypatch.setattr(
        cli, "install_core",
        lambda **kw: CoreInstallResult(None, False, reason="already-installed"),
    )
    assert not tool.exists()
    assert main(["setup", "--apply"]) == 0
    assert tool.is_file()
    assert prereqs.detect_prereq(Prereq("fixture-tool")).version == "1.0.0"
    assert "RESTART" not in capsys.readouterr().out
    assert main(["setup", "--apply"]) == 0
