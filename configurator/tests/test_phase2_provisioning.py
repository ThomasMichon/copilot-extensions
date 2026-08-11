"""Phase 2 tests: prerequisite detection, provisioning plan, and core install.

Everything here stays read-only / dry-run — no test installs anything or mutates
the machine. Provisioning apply and the core install are exercised only in
dry-run, and detection is driven against fakes where needed.
"""

from __future__ import annotations

from pathlib import Path

from configurator.catalog import Prereq
from configurator.core_install import (
    core_status,
    install_command,
    install_core,
)
from configurator.prereqs import (
    PrereqStatus,
    _ge,
    detect_baseline,
    detect_prereq,
    missing,
)
from configurator.provision import apply, plan, recipe_for, restart_needed
from configurator.__main__ import main


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

def test_doctor_command_runs(capsys):
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
