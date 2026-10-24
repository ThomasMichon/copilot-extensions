"""Registration previews preserve registry bytes and all existing custody."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_worktrees import __main__ as core
from agent_worktrees import agent_policy, installer, pr_authority, registry_paths, repos, sessions


def _unexpected(*args: object, **kwargs: object) -> None:
    pytest.fail("read-only audit attempted a write, launch or project-resolution effect")


def _snapshot(root: Path) -> dict[str, bytes | None]:
    return {
        str(path.relative_to(root)): path.read_bytes() if path.is_file() else None
        for path in root.rglob("*")
    }


def _policy(root: Path, value: str) -> None:
    path = root / ".copilot-extensions/agent-worktrees/config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"expose_agent: {value}\n", encoding="utf-8")


@pytest.fixture
def registries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    projects = tmp_path / "projects.yaml"
    repositories = tmp_path / "repos.yaml"
    monkeypatch.setattr(installer, "projects_yaml_path", lambda: projects)
    monkeypatch.setattr(repos, "_repos_yaml_path", lambda: repositories)
    monkeypatch.setattr(registry_paths, "installation_context", lambda: None)
    monkeypatch.setattr(installer, "write_projects_registry", _unexpected)
    monkeypatch.setattr(repos, "write_registry", _unexpected)
    monkeypatch.setattr(pr_authority, "guard", _unexpected)
    monkeypatch.setattr(sessions, "has_mux_session", _unexpected)
    monkeypatch.setattr(sessions, "kill_tmux_session", _unexpected)
    monkeypatch.setattr(core, "_extract_project_flag", _unexpected)
    monkeypatch.setattr(core.cfg, "load_config", _unexpected)
    return projects, repositories


def _write_registries(
    paths: tuple[Path, Path], projects: dict, roots: dict[str, Path],
) -> None:
    paths[0].write_text(json.dumps({"schema_version": 2, "projects": projects}), encoding="utf-8")
    paths[1].write_text(json.dumps({
        "repos": {
            name: {"class": "worktree", **{platform: str(root) for platform in ("windows", "wsl", "linux")}}
            for name, root in roots.items()
        },
    }), encoding="utf-8")


def test_audit_reports_conflicts_invalid_and_unresolved_without_changing_state(
    tmp_path: Path, registries: tuple[Path, Path], capsys: pytest.CaptureFixture,
) -> None:
    roots = {name: tmp_path / name for name in ("denied", "allowed", "invalid")}
    for root in roots.values():
        root.mkdir()
    _policy(roots["denied"], "false")
    _policy(roots["allowed"], "true")
    _policy(roots["invalid"], "not-a-boolean")
    _write_registries(registries, {
        "denied": {"expose_agent": False, "config_dir": "retained-config"},
        "allowed": {}, "invalid": {}, "missing": {"expose_agent": True},
    }, roots)
    custody = tmp_path / "retained-config" / "worktrees"
    custody.mkdir(parents=True)
    (custody / "active.yaml").write_text(
        "sessions: [existing]\nclaims: [retained]\nhistory: [preserved]\n", encoding="utf-8",
    )
    before = _snapshot(tmp_path)
    assert core.main(["agent-policy", "audit", "--json"]) == 3
    report = json.loads(capsys.readouterr().out)
    assert report["version"] == 1 and report["read_only"] is True
    assert report["automatic_changes"] is False
    assert (report["conflicts"], report["invalid"], report["unresolved"]) == (1, 1, 1)
    rows = {row["project"]: row for row in report["projects"]}
    assert rows["denied"]["state"] == "conflict"
    assert rows["denied"]["registration_expose_agent"] is False
    assert rows["denied"]["policy"]["expose_agent"] is False
    assert rows["allowed"]["state"] == "eligible"
    assert rows["missing"]["state"] == "unresolved"
    assert "policy" not in rows["missing"]
    assert rows["invalid"]["code"] == "repository-agent-exposure-invalid"
    assert "Preserve existing registrations, sessions, tracking, history and claims" in report["guidance"]
    assert _snapshot(tmp_path) == before


def test_audit_uses_receiving_marketplace_and_registered_root_not_cwd(
    tmp_path: Path, registries: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    overlay = target / ".copilot-extensions/agent-worktrees/marketplaces/receiving/config.yaml"
    overlay.parent.mkdir(parents=True)
    overlay.write_text("expose_agent: false\n", encoding="utf-8")
    other = tmp_path / "other"
    other.mkdir()
    _policy(other, "true")
    monkeypatch.chdir(other)
    monkeypatch.setattr(
        registry_paths, "installation_context", lambda: {"marketplaceId": "receiving"},
    )
    _write_registries(registries, {"target": {"expose_agent": True}}, {"target": target})
    before = _snapshot(tmp_path)
    assert agent_policy.main(["audit", "--json"]) == 3
    report = json.loads(capsys.readouterr().out)
    row = report["projects"][0]
    assert report["marketplace_id"] == "receiving"
    assert row["state"] == "conflict"
    assert row["policy"]["repository"] == str(target.resolve())
    assert row["policy"]["denied_by"] == [str(overlay)]
    assert _snapshot(tmp_path) == before


def test_empty_audit_does_not_create_absent_registries(
    tmp_path: Path, registries: tuple[Path, Path], capsys: pytest.CaptureFixture,
) -> None:
    before = _snapshot(tmp_path)
    assert agent_policy.main(["audit", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["projects"] == [] and report["conflicts"] == 0
    assert report["read_only"] is True
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize("which", [0, 1])
def test_invalid_audit_registry_never_becomes_clean_empty_result(
    tmp_path: Path, registries: tuple[Path, Path],
    capsys: pytest.CaptureFixture, which: int,
) -> None:
    registries[which].write_text("not: [valid", encoding="utf-8")
    before = _snapshot(tmp_path)
    assert agent_policy.main(["audit", "--json"]) == 3
    report = json.loads(capsys.readouterr().out)
    assert report["version"] == 1 and report["code"] == "repository-agent-exposure-invalid"
    assert report["error"] and "projects" not in report
    assert _snapshot(tmp_path) == before


def test_invalid_context_refuses_before_registry_reads(
    registries: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    def invalid_context() -> dict:
        raise ValueError("receiving installation context is invalid")

    monkeypatch.setattr(registry_paths, "installation_context", invalid_context)
    monkeypatch.setattr(installer, "read_projects_registry", _unexpected)
    assert agent_policy.main(["audit", "--json"]) == 3
    report = json.loads(capsys.readouterr().out)
    assert report["code"] == "repository-agent-exposure-invalid"
    assert "receiving installation context is invalid" in report["error"]


def test_audit_reloads_policy_and_preserves_existing_project_registration(
    tmp_path: Path, registries: tuple[Path, Path], capsys: pytest.CaptureFixture,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    _write_registries(registries, {"target": {"expose_agent": True}}, {"target": target})
    registry_bytes = [path.read_bytes() for path in registries]
    assert agent_policy.main(["audit", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["projects"][0]["state"] == "eligible"
    _policy(target, "false")
    assert agent_policy.main(["audit", "--json"]) == 3
    assert json.loads(capsys.readouterr().out)["projects"][0]["state"] == "conflict"
    assert [path.read_bytes() for path in registries] == registry_bytes


def test_relative_registered_root_cannot_select_ambient_cwd(
    tmp_path: Path, registries: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    monkeypatch.chdir(tmp_path)
    _write_registries(registries, {"relative": {}}, {"relative": Path(".")})
    before = _snapshot(tmp_path)
    assert agent_policy.main(["audit", "--json"]) == 3
    row = json.loads(capsys.readouterr().out)["projects"][0]
    assert row["state"] == "invalid" and "policy" not in row
    assert "CWD-relative" in row["error"]
    assert _snapshot(tmp_path) == before
