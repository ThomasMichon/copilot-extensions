"""Installation-context selection for config/projects/repos registry paths."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from agent_worktrees import config, config_migrations, doctor, installer, repos
from agent_worktrees.picker_tui import pivots

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
CONTEXT_HELPER = (
    PLUGIN_ROOT / "scripts" / "installation-context" / "installation_context.py"
)
REGISTRY_HELPER = PLUGIN_ROOT / "scripts" / "registry_root.py"


def _payload(tmp_path: Path, label: str, plugin: str = "agent-worktrees") -> Path:
    root = tmp_path / label
    helper = root / "scripts" / "installation-context" / "installation_context.py"
    helper.parent.mkdir(parents=True)
    shutil.copyfile(CONTEXT_HELPER, helper)
    shutil.copyfile(REGISTRY_HELPER, root / "scripts" / "registry_root.py")
    (root / "plugin.json").write_text(
        json.dumps({"name": plugin, "version": "0.0.1-dev1"}),
        encoding="utf-8",
    )
    return root


def _stamp(
    durable: Path,
    payload: Path,
    *,
    marketplace: str,
    repository: str,
    plugin: str = "agent-worktrees",
) -> tuple[Path, Path]:
    completed = subprocess.run(
        [
            sys.executable,
            str(payload / "scripts" / "installation-context" / "installation_context.py"),
            "stamp",
            "--source-json",
            json.dumps({"source": "github", "repo": repository}),
            "--marketplace-key",
            marketplace,
            "--plugin-id",
            plugin,
            "--payload-root",
            str(payload),
            "--payload-version",
            "0.0.1-dev1",
            "--payload-origin",
            "explicit",
            "--expected-namespace-generation",
            "0",
            "--expected-install-generation",
            "0",
            "--durable-home",
            str(durable),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    result = json.loads(completed.stdout)
    return Path(result["installReceipt"]), Path(result["pluginRoot"])


def _select(monkeypatch, context: Path, payload: Path) -> None:
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(context))
    monkeypatch.setenv("AGENT_WORKTREES_PAYLOAD_ROOT", str(payload))
    monkeypatch.delenv("COPILOT_PLUGIN_ROOT", raising=False)


def test_legacy_paths_remain_exact_and_ignore_runtime_root(monkeypatch, tmp_path):
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setenv("AGENT_RT_ROOT", str(tmp_path / "spoofed-runtime"))

    expected = Path.home() / ".agent-worktrees"
    assert config.global_config_path() == expected / "config.yaml"
    assert installer.projects_yaml_path() == expected / "projects.yaml"
    assert repos._repos_yaml_path() == expected / "repos.yaml"


def test_explicit_context_selects_all_three_registry_files(monkeypatch, tmp_path):
    payload = _payload(tmp_path, "payload")
    context, plugin_root = _stamp(
        tmp_path / "durable",
        payload,
        marketplace="example-primary",
        repository="Example-Org/Example-Marketplace",
    )
    _select(monkeypatch, context, payload)

    assert config.global_config_path() == plugin_root / "config.yaml"
    assert installer.projects_yaml_path() == plugin_root / "projects.yaml"
    assert repos._repos_yaml_path() == plugin_root / "repos.yaml"
    assert doctor._projects_path() == plugin_root / "projects.yaml"


def test_two_cells_do_not_share_registry_writes(monkeypatch, tmp_path):
    first_payload = _payload(tmp_path, "first-payload")
    second_payload = _payload(tmp_path, "second-payload")
    first_context, first_root = _stamp(
        tmp_path / "durable",
        first_payload,
        marketplace="first",
        repository="Example-Org/First-Marketplace",
    )
    second_context, second_root = _stamp(
        tmp_path / "durable",
        second_payload,
        marketplace="second",
        repository="Example-Org/Second-Marketplace",
    )

    _select(monkeypatch, first_context, first_payload)
    installer.write_projects_registry({"projects": {"first": {}}})
    repos.write_registry(
        repos.ReposRegistry(repos={"first": repos.RepoEntry(name="first")})
    )

    _select(monkeypatch, second_context, second_payload)
    installer.write_projects_registry({"projects": {"second": {}}})
    repos.write_registry(
        repos.ReposRegistry(repos={"second": repos.RepoEntry(name="second")})
    )

    first_projects = yaml.safe_load(
        (first_root / "projects.yaml").read_text(encoding="utf-8")
    )
    second_projects = yaml.safe_load(
        (second_root / "projects.yaml").read_text(encoding="utf-8")
    )
    assert set(first_projects["projects"]) == {"first"}
    assert set(second_projects["projects"]) == {"second"}
    first_repos = (first_root / "repos.yaml").read_text(encoding="utf-8")
    assert "first:" in first_repos
    assert "second:" not in first_repos


def test_invalid_context_refuses_legacy_fallback(monkeypatch, tmp_path):
    payload = _payload(tmp_path, "payload")
    invalid = tmp_path / "invalid" / "install.json"
    invalid.parent.mkdir()
    invalid.write_text("{", encoding="utf-8")
    legacy = Path.home() / ".agent-worktrees"
    (legacy / "projects.yaml").write_text(
        "projects:\n  legacy: {}\n", encoding="utf-8"
    )
    _select(monkeypatch, invalid, payload)

    with pytest.raises(ValueError):
        installer.read_projects_registry()


def test_context_rejects_foreign_plugin_and_payload(monkeypatch, tmp_path):
    aw_payload = _payload(tmp_path, "aw-payload")
    other_payload = _payload(tmp_path, "other-payload", plugin="agent-bridge")
    foreign_context, _ = _stamp(
        tmp_path / "foreign-durable",
        other_payload,
        marketplace="foreign",
        repository="Example-Org/Foreign-Marketplace",
        plugin="agent-bridge",
    )
    _select(monkeypatch, foreign_context, aw_payload)
    with pytest.raises(ValueError):
        repos.read_registry()

    real_context, _ = _stamp(
        tmp_path / "real-durable",
        aw_payload,
        marketplace="real",
        repository="Example-Org/Real-Marketplace",
    )
    spoof_payload = _payload(tmp_path, "spoof-payload")
    _select(monkeypatch, real_context, spoof_payload)
    with pytest.raises(ValueError):
        config.global_config_path()


def test_eager_migration_only_mutates_selected_registry_root(monkeypatch, tmp_path):
    payload = _payload(tmp_path, "payload")
    context, plugin_root = _stamp(
        tmp_path / "durable",
        payload,
        marketplace="migration",
        repository="Example-Org/Migration-Marketplace",
    )
    legacy = Path.home() / ".agent-worktrees" / "projects.yaml"
    legacy.write_text(
        "schema_version: 1\nprojects:\n  legacy:\n    anchor: /legacy\n",
        encoding="utf-8",
    )
    selected = plugin_root / "projects.yaml"
    selected.write_text(
        "schema_version: 1\nprojects:\n  selected:\n    anchor: /selected\n",
        encoding="utf-8",
    )
    _select(monkeypatch, context, payload)

    config_migrations.run_migrations()

    assert yaml.safe_load(selected.read_text(encoding="utf-8"))["schema_version"] == 2
    assert yaml.safe_load(legacy.read_text(encoding="utf-8"))["schema_version"] == 1


def test_namespaced_pivot_activation_stands_down_without_legacy_scan(
    monkeypatch, tmp_path
):
    payload = _payload(tmp_path, "payload")
    context, _ = _stamp(
        tmp_path / "durable",
        payload,
        marketplace="pivots",
        repository="Example-Org/Pivot-Marketplace",
    )
    _select(monkeypatch, context, payload)
    monkeypatch.setattr(
        pivots,
        "resolve_active_plugins",
        lambda: pytest.fail("legacy plugin activation must not run"),
    )

    report = pivots._resolve_activation()

    assert not report.active
