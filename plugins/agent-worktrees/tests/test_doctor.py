"""Tests for registry reconciliation (``repos doctor``)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agent_worktrees import doctor, repos


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    """Redirect ~ so both registries read/write under a tmp dir."""
    monkeypatch.setattr(repos.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(doctor.Path, "home", lambda: tmp_path)
    # Pin the platform so path keys are deterministic across CI hosts.
    monkeypatch.setattr(repos, "_current_platform", lambda: "linux")
    (tmp_path / ".agent-worktrees").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _write_repos(home: Path, text: str) -> None:
    (home / ".agent-worktrees" / "repos.yaml").write_text(text, encoding="utf-8")


def _write_projects(home: Path, projects: dict) -> None:
    (home / ".agent-worktrees" / "projects.yaml").write_text(
        yaml.safe_dump({"projects": projects}), encoding="utf-8"
    )


def _kinds(findings) -> set[str]:
    return {f.kind for f in findings}


def _by_repo(findings, repo: str):
    return [f for f in findings if f.repo == repo]


# ---------------------------------------------------------------------------
# Clean state
# ---------------------------------------------------------------------------

def test_no_drift_when_consistent(home: Path):
    repo_dir = home / "src" / "proj"
    repo_dir.mkdir(parents=True)
    _write_repos(home, f"""
repos:
  proj:
    class: worktree
    linux: {repo_dir}
""")
    _write_projects(home, {
        "proj": {"anchor": str(repo_dir), "expose_agent": True,
                 "default_branch": "main"},
    })
    findings = doctor.diagnose()
    # Only possible finding would be unadopted/stale; here it's adopted + exists.
    assert _kinds(findings) == set()


# ---------------------------------------------------------------------------
# missing_repo_entry
# ---------------------------------------------------------------------------

def test_missing_repo_entry_detected_and_fixed(home: Path):
    repo_dir = home / "src" / "newproj"
    repo_dir.mkdir(parents=True)
    _write_repos(home, "repos: {}\n")
    _write_projects(home, {
        "newproj": {"anchor": str(repo_dir), "expose_agent": False,
                    "default_branch": "main"},
    })

    findings = doctor.diagnose()
    assert "missing_repo_entry" in _kinds(findings)

    doctor.reconcile(fix=True)
    entry = repos.find_repo("newproj")
    assert entry is not None
    assert entry.repo_class == "worktree"
    assert entry.agent is False           # took expose_agent from the project
    assert entry.paths.get("linux") == str(repo_dir)


# ---------------------------------------------------------------------------
# wrong_class (adopted but reference)
# ---------------------------------------------------------------------------

def test_wrong_class_upgraded_to_worktree(home: Path):
    repo_dir = home / "src" / "harness"
    repo_dir.mkdir(parents=True)
    _write_repos(home, f"""
repos:
  harness:
    class: reference
    linux: {repo_dir}
""")
    _write_projects(home, {
        "harness": {"anchor": str(repo_dir), "expose_agent": True,
                    "default_branch": "main"},
    })

    findings = doctor.diagnose()
    assert "wrong_class" in _kinds(findings)
    assert any(f.severity == doctor.SEV_ERROR for f in findings)

    doctor.reconcile(fix=True)
    entry = repos.find_repo("harness")
    assert entry.repo_class == "worktree"
    assert entry.agent is True            # exposure taken from adoption on upgrade


# ---------------------------------------------------------------------------
# anchor_mismatch
# ---------------------------------------------------------------------------

def test_anchor_mismatch_aligns_to_live_anchor(home: Path):
    live = home / "src" / "moved"
    live.mkdir(parents=True)
    _write_repos(home, """
repos:
  moved:
    class: worktree
    linux: /old/stale/path
""")
    _write_projects(home, {
        "moved": {"anchor": str(live), "expose_agent": True},
    })

    assert "anchor_mismatch" in _kinds(doctor.diagnose())
    doctor.reconcile(fix=True)
    assert repos.find_repo("moved").paths["linux"] == str(live)


# ---------------------------------------------------------------------------
# agent_mismatch (repos.yaml wins)
# ---------------------------------------------------------------------------

def test_agent_mismatch_repos_wins_and_aligns_project(home: Path):
    repo_dir = home / "src" / "noagent"
    repo_dir.mkdir(parents=True)
    _write_repos(home, f"""
repos:
  noagent:
    class: worktree
    agent: false
    linux: {repo_dir}
""")
    # Project omits expose_agent -> effective True -> disagrees with agent:false.
    _write_projects(home, {"noagent": {"anchor": str(repo_dir)}})

    assert "agent_mismatch" in _kinds(doctor.diagnose())
    doctor.reconcile(fix=True)

    projects = doctor._read_projects()
    assert projects["noagent"]["expose_agent"] is False


# ---------------------------------------------------------------------------
# name_collision (SPO / SPO.Core)
# ---------------------------------------------------------------------------

def test_name_collision_renames_to_project_name(home: Path):
    shared = home / "git" / "SPO"
    shared.mkdir(parents=True)
    _write_repos(home, f"""
repos:
  SPO:
    class: reference
    remote: "https://example/_git/SPO.Core"
    linux: {shared}
""")
    _write_projects(home, {
        "SPO.Core": {"anchor": str(shared), "base_repo": True,
                     "expose_agent": True},
    })

    findings = doctor.diagnose()
    assert "name_collision" in _kinds(findings)

    doctor.reconcile(fix=True)
    assert repos.find_repo("SPO") is None
    canon = repos.find_repo("SPO.Core")
    assert canon is not None
    assert canon.repo_class == "worktree"          # upgraded from reference
    assert canon.agent is True                     # took expose_agent on upgrade
    assert canon.remote == "https://example/_git/SPO.Core"   # preserved
    # Single-pass convergence: no residual auto-fixable drift (e.g. agent_mismatch).
    assert not [f for f in doctor.diagnose() if f.fixable]


# ---------------------------------------------------------------------------
# report-only findings
# ---------------------------------------------------------------------------

def test_unadopted_worktree_reported_not_fixed(home: Path):
    repo_dir = home / "src" / "lonely"
    repo_dir.mkdir(parents=True)
    _write_repos(home, f"""
repos:
  lonely:
    class: worktree
    linux: {repo_dir}
""")
    _write_projects(home, {})  # not adopted

    findings = doctor.diagnose()
    f = _by_repo(findings, "lonely")
    assert any(x.kind == "unadopted_worktree" and not x.fixable for x in f)

    # --fix must NOT adopt it (side effects); finding stays unfixed.
    after = doctor.reconcile(fix=True)
    assert any(x.kind == "unadopted_worktree" and not x.fixed
               for x in _by_repo(after, "lonely"))


def test_stale_path_reported(home: Path):
    _write_repos(home, """
repos:
  ghost:
    class: reference
    linux: /does/not/exist/ghost
""")
    _write_projects(home, {})
    assert "stale_path" in _kinds(doctor.diagnose())


# ---------------------------------------------------------------------------
# idempotence
# ---------------------------------------------------------------------------

def test_fix_is_idempotent(home: Path):
    repo_dir = home / "src" / "proj"
    repo_dir.mkdir(parents=True)
    _write_repos(home, "repos: {}\n")
    _write_projects(home, {
        "proj": {"anchor": str(repo_dir), "expose_agent": True,
                 "default_branch": "main"},
    })
    doctor.reconcile(fix=True)
    first = doctor.diagnose()
    doctor.reconcile(fix=True)
    second = doctor.diagnose()
    # After a fix, the auto-fixable findings are gone and stay gone.
    assert not [f for f in first if f.fixable]
    assert not [f for f in second if f.fixable]


# ---------------------------------------------------------------------------
# wsl.state promotion (Windows-only)
# ---------------------------------------------------------------------------

def _adopted_wsl_project(home: Path, state: str = "bootstrap") -> Path:
    """A consistent adopted project carrying a wsl.state marker + repos entry."""
    repo_dir = home / "src" / "proj"
    repo_dir.mkdir(parents=True, exist_ok=True)
    _write_repos(home, f"""
repos:
  proj:
    class: worktree
    linux: {repo_dir}
    windows: {repo_dir}
""")
    _write_projects(home, {
        "proj": {
            "anchor": str(repo_dir), "expose_agent": True,
            "default_branch": "main",
            "wsl": {"state": state, "distro": "Ubuntu",
                    "path": "$HOME/src/proj"},
        },
    })
    return repo_dir


def test_wsl_state_promoted_when_wsl_install_present(home: Path, monkeypatch):
    _adopted_wsl_project(home)
    monkeypatch.setattr(doctor, "_wsl_install_present", lambda distro: True)

    findings = doctor.diagnose(plat="windows")
    stale = _by_repo(findings, "proj")
    assert any(f.kind == "wsl_state_stale" and f.fixable for f in stale)

    doctor.reconcile(fix=True, plat="windows")
    assert doctor._read_projects()["proj"]["wsl"]["state"] == "adopted"


def test_wsl_state_reported_when_install_absent(home: Path, monkeypatch):
    _adopted_wsl_project(home)
    monkeypatch.setattr(doctor, "_wsl_install_present", lambda distro: False)

    findings = doctor.reconcile(fix=True, plat="windows")
    proj = _by_repo(findings, "proj")
    assert any(f.kind == "wsl_unadopted" and not f.fixable for f in proj)
    # Report-only: the marker is NOT promoted.
    assert doctor._read_projects()["proj"]["wsl"]["state"] == "bootstrap"


def test_wsl_state_untouched_when_probe_inconclusive(home: Path, monkeypatch):
    _adopted_wsl_project(home)
    monkeypatch.setattr(doctor, "_wsl_install_present", lambda distro: None)

    findings = doctor.reconcile(fix=True, plat="windows")
    assert not [f for f in findings if f.kind.startswith("wsl_")]
    assert doctor._read_projects()["proj"]["wsl"]["state"] == "bootstrap"


def test_wsl_state_ignored_off_windows(home: Path, monkeypatch):
    _adopted_wsl_project(home)
    # The probe must never even run off Windows (the marker isn't ours there).
    called = {"n": 0}

    def _boom(distro):
        called["n"] += 1
        return True

    monkeypatch.setattr(doctor, "_wsl_install_present", _boom)
    findings = doctor.diagnose(plat="linux")
    assert not [f for f in findings if f.kind.startswith("wsl_")]
    assert called["n"] == 0


# ---------------------------------------------------------------------------
# Overlay reconciliation -- per-project ~/.<project>/config.yaml
# ---------------------------------------------------------------------------

def _write_global(home: Path, **kw) -> None:
    (home / ".agent-worktrees" / "config.yaml").write_text(
        yaml.safe_dump(kw), encoding="utf-8"
    )


def _overlay(home: Path, cfg_dir: Path, text: str) -> Path:
    cfg_dir.mkdir(parents=True, exist_ok=True)
    p = cfg_dir / "config.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def _adopted(home: Path, repo_dir: Path, cfg_dir: Path, **extra) -> None:
    _write_repos(home, f"""
repos:
  proj:
    class: worktree
    linux: {repo_dir}
    default_branch: main
""")
    proj = {"anchor": str(repo_dir), "config_dir": str(cfg_dir),
            "default_branch": "main", "expose_agent": True}
    proj.update(extra)
    _write_projects(home, {"proj": proj})


def test_overlay_redundant_keys_detected_and_stripped(home: Path):
    repo_dir = home / "src" / "proj"; repo_dir.mkdir(parents=True)
    cfg_dir = home / ".proj"
    _write_global(home, srcroot=str(home / "src"), machine="dev6",
                  platform="linux")
    _adopted(home, repo_dir, cfg_dir, base_repo=True)
    _overlay(home, cfg_dir, f"""# keep me
srcroot: {home / "src"}
machine: dev6
platform: linux
repo_name: proj
repos:
  proj:
    anchor: {repo_dir}
    default_branch: main
    base_repo: true
    env_script:
      linux: tools/prime.sh
""")
    kinds = _kinds(doctor.diagnose(plat="linux"))
    assert "overlay_redundant_toplevel" in kinds
    assert "overlay_redundant_anchor" in kinds
    assert "overlay_redundant_branch" in kinds
    assert "overlay_redundant_base_repo" in kinds

    doctor.reconcile(fix=True, plat="linux")
    remaining = (cfg_dir / "config.yaml").read_text(encoding="utf-8")
    assert "srcroot" not in remaining
    assert "anchor" not in remaining
    assert "default_branch" not in remaining
    assert "base_repo" not in remaining
    # Preserved: comment, repo_name, and the SPO-specific env_script.
    assert "# keep me" in remaining
    assert "env_script" in remaining and "tools/prime.sh" in remaining


def test_overlay_conflicting_anchor_is_report_only(home: Path):
    repo_dir = home / "src" / "proj"; repo_dir.mkdir(parents=True)
    cfg_dir = home / ".proj"
    _write_global(home, srcroot=str(home / "src"), machine="dev6",
                  platform="linux")
    _adopted(home, repo_dir, cfg_dir)
    _overlay(home, cfg_dir, f"""repos:
  proj:
    anchor: {home / "elsewhere" / "proj"}
""")
    findings = doctor.diagnose(plat="linux")
    conflict = [f for f in findings if f.kind == "overlay_conflicting_anchor"]
    assert conflict and not conflict[0].fixable
    # A report-only conflict must NOT be stripped by --fix.
    doctor.reconcile(fix=True, plat="linux")
    assert "anchor" in (cfg_dir / "config.yaml").read_text(encoding="utf-8")


def test_overlay_conflicting_srcroot_stripped(home: Path):
    repo_dir = home / "src" / "proj"; repo_dir.mkdir(parents=True)
    cfg_dir = home / ".proj"
    _write_global(home, srcroot=str(home / "src"), machine="dev6",
                  platform="linux")
    _adopted(home, repo_dir, cfg_dir)
    _overlay(home, cfg_dir, f"""srcroot: {home / "other"}
repos:
  proj: {{}}
""")
    findings = doctor.diagnose(plat="linux")
    conf = [f for f in findings if f.kind == "overlay_conflicting_srcroot"]
    assert conf and conf[0].fixable
    doctor.reconcile(fix=True, plat="linux")
    assert "srcroot" not in (cfg_dir / "config.yaml").read_text(encoding="utf-8")


def test_branch_drift_between_registries_fixed(home: Path):
    repo_dir = home / "src" / "proj"; repo_dir.mkdir(parents=True)
    _write_repos(home, f"""
repos:
  proj:
    class: worktree
    linux: {repo_dir}
    default_branch: main
""")
    _write_projects(home, {"proj": {
        "anchor": str(repo_dir), "expose_agent": True,
        "default_branch": "master",  # drifted from repos.yaml 'main'
    }})
    findings = doctor.diagnose(plat="linux")
    assert "branch_drift" in _kinds(findings)
    doctor.reconcile(fix=True, plat="linux")
    projs = doctor._read_projects()
    assert projs["proj"]["default_branch"] == "main"  # aligned to repos.yaml


def test_overlay_absent_is_noop(home: Path):
    repo_dir = home / "src" / "proj"; repo_dir.mkdir(parents=True)
    cfg_dir = home / ".proj"  # no config.yaml written
    _write_global(home, srcroot=str(home / "src"))
    _adopted(home, repo_dir, cfg_dir)
    kinds = _kinds(doctor.diagnose(plat="linux"))
    assert not any(k.startswith("overlay_") for k in kinds)
