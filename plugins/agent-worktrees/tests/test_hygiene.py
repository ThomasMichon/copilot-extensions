"""Unit tests for the stale-global-editable-install hygiene sweep (#2726)."""

from __future__ import annotations

import json
from pathlib import Path

from agent_worktrees import __main__ as m
from agent_worktrees import hygiene


def _write_pth(dirpath: Path, dist: str, version: str, target: str) -> Path:
    dirpath.mkdir(parents=True, exist_ok=True)
    p = dirpath / f"__editable__.{dist}-{version}.pth"
    p.write_text(target + "\n", encoding="utf-8")
    return p


def test_finds_worktree_pointing_editable_install(tmp_path):
    site = tmp_path / "site-packages"
    target = str(
        tmp_path / "Src" / "copilot-extensions.worktrees" / "someone-20260101-000000-aaaa"
        / "plugins" / "agent-worktrees" / "src"
    )
    _write_pth(site, "agent_worktrees", "1.5.5.dev1", target)

    found = hygiene.find_stale_editable_installs([site])

    assert len(found) == 1
    assert found[0].dist_name == "agent_worktrees"
    assert found[0].version == "1.5.5.dev1"
    assert found[0].target_path == target
    assert found[0].pip_name == "agent-worktrees"


def test_ignores_editable_install_not_pointing_at_a_worktree(tmp_path):
    site = tmp_path / "site-packages"
    target = str(tmp_path / "Src" / "copilot-extensions" / "plugins" / "agent-worktrees" / "src")
    _write_pth(site, "agent_worktrees", "1.5.5.dev1", target)

    assert hygiene.find_stale_editable_installs([site]) == []


def test_ignores_non_editable_pth_and_other_dists(tmp_path):
    site = tmp_path / "site-packages"
    site.mkdir(parents=True)
    (site / "some_other_package.pth").write_text(
        str(tmp_path / "x.worktrees" / "y"), encoding="utf-8"
    )

    assert hygiene.find_stale_editable_installs([site]) == []


def test_remove_stale_editable_install_falls_back_to_pth_delete_when_pip_unavailable(tmp_path):
    site = tmp_path / "site-packages"
    target = str(tmp_path / "repo.worktrees" / "w1" / "plugins" / "agent-bridge" / "src")
    pth = _write_pth(site, "agent_bridge", "0.4.0.dev1", target)
    entry = hygiene.StaleEditableInstall(
        dist_name="agent_bridge", version="0.4.0.dev1", pth_path=pth, target_path=target
    )

    status = hygiene.remove_stale_editable_install(entry, python=str(tmp_path / "no-such-python"))

    assert status == "pth-removed"
    assert not pth.exists()


def test_scan_and_clean_report_mode_does_not_delete(tmp_path, monkeypatch):
    site = tmp_path / "site-packages"
    target = str(tmp_path / "repo.worktrees" / "w1" / "plugins" / "agent-bridge" / "src")
    pth = _write_pth(site, "agent_bridge", "0.4.0.dev1", target)
    monkeypatch.setattr(hygiene, "_site_packages_dirs", lambda: [site])

    report = hygiene.scan_and_clean(fix=False)

    assert report["action"] == "report"
    assert len(report["findings"]) == 1
    assert report["findings"][0]["status"] == "would-remove"
    assert pth.exists()


def test_hygiene_registered_as_no_project_command():
    assert m.COMMAND_MAP["hygiene"] is m.cmd_hygiene
    assert "hygiene" in m._NO_PROJECT_COMMANDS


def test_cmd_hygiene_json_report(tmp_path, monkeypatch, capsys):
    site = tmp_path / "site-packages"
    target = str(tmp_path / "repo.worktrees" / "w1" / "plugins" / "agent-bridge" / "src")
    _write_pth(site, "agent_bridge", "0.4.0.dev1", target)
    monkeypatch.setattr(hygiene, "_site_packages_dirs", lambda: [site])

    parser = m.build_parser()
    args = parser.parse_args(["hygiene", "--json"])
    code = m.cmd_hygiene(args)
    out = json.loads(capsys.readouterr().out)

    assert code == 0
    assert out["action"] == "report"
    assert out["findings"][0]["distribution"] == "agent_bridge"


def test_cmd_hygiene_no_findings(tmp_path, monkeypatch, capsys):
    site = tmp_path / "site-packages"
    site.mkdir(parents=True)
    monkeypatch.setattr(hygiene, "_site_packages_dirs", lambda: [site])

    parser = m.build_parser()
    args = parser.parse_args(["hygiene"])
    code = m.cmd_hygiene(args)

    assert code == 0
    assert "No stale" in capsys.readouterr().out
