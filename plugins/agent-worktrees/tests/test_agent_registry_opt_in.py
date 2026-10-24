"""Registry agent declaration is opt-in; direct repository policy is opt-out."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from agent_worktrees import __main__ as core
from agent_worktrees import agent_policy, installer, registry_paths, related, repos, worktree_ops_cli
from test_worktree_creation_seed import _create_config, _stub_create_worktree_core_internals


@pytest.fixture
def registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "repos.yaml"
    monkeypatch.setattr(repos, "_repos_yaml_path", lambda: path)
    monkeypatch.setattr(installer, "projects_yaml_path", lambda: tmp_path / "projects.yaml")
    monkeypatch.setattr(registry_paths, "installation_context", lambda: None)
    monkeypatch.setattr(repos, "_best_effort_pin_credential", lambda *args: None)
    return path


@pytest.mark.parametrize("repo_class", ["reference", "singleton", "worktree", "knowledge"])
def test_registry_absence_does_not_declare_agent_or_deny_direct_repository_check(
    tmp_path: Path, registry: Path, capsys: pytest.CaptureFixture, repo_class: str,
) -> None:
    registry.write_text(json.dumps({
        "repos": {"source": {"class": repo_class, "linux": str(tmp_path)}},
    }), encoding="utf-8")
    assert repos.read_registry(strict=True).repos["source"].agent is False
    assert repos.RepoEntry(name="source", repo_class=repo_class).agent is False
    assert agent_policy.main(["check", "--path", str(tmp_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["allowed"] is True


@pytest.mark.parametrize("repo_class", ["reference", "singleton", "worktree", "knowledge"])
def test_explicit_registry_agent_opt_in_survives_serialization_and_class_changes(
    tmp_path: Path, registry: Path, repo_class: str,
) -> None:
    repos.add_repo("source", str(tmp_path), repo_class=repo_class, agent=True)
    assert "agent: true" in registry.read_text(encoding="utf-8")
    repos.add_repo("source", str(tmp_path), repo_class="worktree")
    assert repos.read_registry(strict=True).repos["source"].agent is True


@pytest.mark.parametrize("value", ["false", 1, "true", None])
def test_non_boolean_registry_agent_is_not_an_implicit_opt_in(
    tmp_path: Path, registry: Path, value: object,
) -> None:
    registry.write_text(json.dumps({
        "repos": {"source": {"class": "worktree", "agent": value, "linux": str(tmp_path)}},
    }), encoding="utf-8")
    assert repos.read_registry().repos["source"].agent is False
    with pytest.raises(ValueError, match="agent must be a Boolean"):
        repos.read_registry(strict=True)


def test_source_registry_entry_can_create_under_repository_veto_without_project_adoption(
    tmp_path: Path, registry: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture,
) -> None:
    config = _create_config(tmp_path)
    source = Path(config.default_repo.anchor)
    policy = source / ".copilot-extensions/agent-worktrees/config.yaml"
    policy.parent.mkdir(parents=True)
    policy.write_text("expose_agent: false\n", encoding="utf-8")
    repos.add_repo(config.repo_name, str(source), repo_class="worktree")
    assert repos.read_registry(strict=True).repos[config.repo_name].agent is False
    _stub_create_worktree_core_internals(monkeypatch, tmp_path, config)
    monkeypatch.setattr(core.cfg, "tracking_dir", lambda: tmp_path / "tracking")
    monkeypatch.setattr(worktree_ops_cli.project_state, "namespaced", lambda: False)
    capfd.readouterr()
    assert worktree_ops_cli.cmd_create(
        argparse.Namespace(json=True, no_owner=True, no_pair=True),
    ) == 0
    result = json.loads(capfd.readouterr().out)
    assert result["worktree"]["id"] and "launch" not in result
    assert not (tmp_path / "projects.yaml").exists()
    assert agent_policy.main(["check", "--path", str(source), "--json"]) == 3


@pytest.mark.parametrize("delegate", [None, "none", "agent-bridge"])
def test_related_location_and_editing_metadata_do_not_infer_agent_declaration(
    tmp_path: Path, delegate: str | None,
) -> None:
    path = tmp_path / ".copilot-extensions/agent-worktrees/related.yaml"
    path.parent.mkdir(parents=True)
    entry: dict[str, object] = {
        "role": "source", "locus": {"preferred": "machine:other", "machines": ["other"]},
    }
    if delegate is not None:
        entry["delegate"] = {"via": delegate}
    path.write_text(json.dumps({"related": {"source": entry}}), encoding="utf-8")
    loaded = related.read_related(tmp_path).related["source"]
    assert loaded.delegate == (delegate or "")
    result = related.build_resolution(
        loaded, current_machine="here", repo_class="worktree", repo_path=None, adopted=False,
    )
    assert result.delegate_via == (delegate or "")
