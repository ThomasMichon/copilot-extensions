"""Tests for Phase 3e Step 5's deploy mechanism (``deploy_fragment``).

Exercises :func:`worktree_manager.terminal_fragment.deploy_fragment` against
a synthetic ``USERPROFILE``/``LOCALAPPDATA`` home (matching
``test_terminal_fragment_cli.py``'s fixture style): proves the dry-run path
(``apply=False``, the default) never touches disk, and the live path
(``apply=True``) writes the fragment file and reconciles a fake Windows
Terminal ``state.json``/``settings.json`` the same way ``install.ps1``'s
``Sync-TerminalState``/``Clean-TerminalSettingsJson`` did.
"""
from __future__ import annotations

import json

from worktree_manager import terminal_fragment as tf

STALE_GUID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def _make_home(tmp_path, *, machine="book2"):
    awt = tmp_path / ".agent-worktrees"
    awt.mkdir(parents=True)
    anchor = tmp_path / "checkout"
    (anchor / ".agent-worktrees").mkdir(parents=True)
    (anchor / ".agent-worktrees" / "machines.yaml").write_text(
        "machines:\n"
        f"  {machine}:\n"
        f"    display_name: {machine.title()}\n",
        encoding="utf-8",
    )
    (awt / "projects.yaml").write_text(
        "schema_version: 2\n"
        "projects:\n"
        "  myproj:\n"
        f"    anchor: \"{str(anchor).replace(chr(92), chr(92) * 2)}\"\n",
        encoding="utf-8",
    )
    (awt / "repos.yaml").write_text("schema_version: 1\nrepos: {}\n", encoding="utf-8")
    proj_cfg = tmp_path / ".myproj"
    proj_cfg.mkdir()
    (proj_cfg / "config.yaml").write_text(f"machine: {machine}\n", encoding="utf-8")
    return tmp_path


def _set_home(monkeypatch, tmp_path):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))


def test_deploy_fragment_dry_run_never_touches_disk(monkeypatch, tmp_path):
    _make_home(tmp_path)
    _set_home(monkeypatch, tmp_path)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))

    plan = tf.deploy_fragment("book2", current_project="myproj", apply=False)

    assert plan.applied is False
    assert plan.new_guids  # the myproj/book2 fragment emits at least one profile
    assert plan.fragment_path is not None
    assert not plan.fragment_path.exists()
    assert any("unavailable" in note for note in plan.notes)


def test_deploy_fragment_apply_without_localappdata_reports_not_applied(
    monkeypatch, tmp_path
):
    """``apply=True`` with no resolvable Fragments dir (no LOCALAPPDATA, e.g.
    non-Windows) must not claim success -- nothing was actually written.
    Regression test: ``deploy_fragment`` used to set ``plan.applied = True``
    unconditionally whenever ``apply=True`` was passed, even when
    ``fragment_path`` was ``None`` and ``_apply_deploy_plan`` wrote nothing."""
    _make_home(tmp_path)
    _set_home(monkeypatch, tmp_path)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    plan = tf.deploy_fragment("book2", current_project="myproj", apply=True)

    assert plan.fragment_path is None
    assert plan.applied is False


def test_deploy_fragment_apply_writes_and_reconciles(monkeypatch, tmp_path):
    _make_home(tmp_path)
    _set_home(monkeypatch, tmp_path)
    local = tmp_path / "localappdata"
    monkeypatch.setenv("LOCALAPPDATA", str(local))

    wt_local_state = (
        local / "Packages" / "Microsoft.WindowsTerminal_8wekyb3d8bbwe" / "LocalState"
    )
    wt_local_state.mkdir(parents=True)
    frag_dir = local / "Microsoft" / "Windows Terminal" / "Fragments" / "AgentWorktrees"
    frag_dir.mkdir(parents=True)
    fragment_dst = frag_dir / "agent-worktrees.json"

    # Discover the real fragment this project/machine would emit (dry run),
    # then seed disk with an "old" fragment carrying one extra stale profile.
    preview = tf.deploy_fragment("book2", current_project="myproj", apply=False)
    real_profiles = preview.fragment["profiles"]
    assert real_profiles

    old_fragment = {
        "profiles": real_profiles + [{
            "guid": STALE_GUID, "name": "Stale", "commandline": "echo stale",
            "icon": "", "startingDirectory": "%USERPROFILE%", "hidden": False,
        }],
        "schemes": [],
    }
    fragment_dst.write_text(json.dumps(old_fragment), encoding="utf-8")

    settings = {"profiles": {"list": [
        {"guid": real_profiles[0]["guid"], "name": real_profiles[0]["name"],
         "source": "AgentWorktrees"},
        {"guid": STALE_GUID, "name": "Stale", "source": "AgentWorktrees"},
    ]}}
    (wt_local_state / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    (wt_local_state / "state.json").write_text(
        json.dumps({"generatedProfiles": [STALE_GUID]}), encoding="utf-8"
    )

    plan = tf.deploy_fragment("book2", current_project="myproj", apply=True)

    assert plan.applied is True
    assert STALE_GUID.lower() in [g.lower() for g in plan.stale_guids]
    assert plan.generated_plan is not None
    assert STALE_GUID.lower() in [g.lower() for g in plan.generated_plan.remove]

    new_state = json.loads((wt_local_state / "state.json").read_text())
    assert STALE_GUID.lower() not in [g.lower() for g in new_state["generatedProfiles"]]

    new_settings = json.loads((wt_local_state / "settings.json").read_text())
    remaining = {p["guid"].lower() for p in new_settings["profiles"]["list"]}
    assert STALE_GUID.lower() not in remaining
    assert real_profiles[0]["guid"].lower() in remaining

    written = json.loads(fragment_dst.read_text())
    written_guids = {p["guid"].lower() for p in written["profiles"]}
    assert STALE_GUID.lower() not in written_guids
    assert real_profiles[0]["guid"].lower() in written_guids


def test_deploy_fragment_apply_false_after_apply_true_leaves_state_unchanged(
    monkeypatch, tmp_path
):
    """A second dry-run after a real deploy reports a converged, no-op plan."""
    _make_home(tmp_path)
    _set_home(monkeypatch, tmp_path)
    local = tmp_path / "localappdata"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    wt_local_state = (
        local / "Packages" / "Microsoft.WindowsTerminal_8wekyb3d8bbwe" / "LocalState"
    )
    wt_local_state.mkdir(parents=True)
    (local / "Microsoft" / "Windows Terminal" / "Fragments" / "AgentWorktrees").mkdir(
        parents=True
    )

    first = tf.deploy_fragment("book2", current_project="myproj", apply=True)
    assert first.applied is True

    second = tf.deploy_fragment("book2", current_project="myproj", apply=False)
    assert second.applied is False
    assert second.stale_guids == []
    assert second.changed_guids == []
    assert second.new_guids == first.new_guids
