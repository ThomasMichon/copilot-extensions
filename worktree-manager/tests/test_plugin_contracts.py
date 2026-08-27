"""Manager-owned plugin contribution contract tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from worktree_manager.plugin_contracts import (
    CONTRACT_VERSION,
    ContractError,
    discover_contracts,
    parse_manifest,
)
from worktree_manager.__main__ import main


def _settings(home: Path, enabled: dict[str, bool]) -> None:
    root = home / ".copilot"
    root.mkdir(parents=True, exist_ok=True)
    (root / "settings.json").write_text(
        json.dumps({"enabledPlugins": enabled}), encoding="utf-8")


def _manifest(root: Path, marketplace: str, plugin: str, name: str, data: dict) -> Path:
    path = root / marketplace / plugin / "pivots" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _pivot(**extra) -> dict:
    return {
        "schema_version": CONTRACT_VERSION,
        "label": "Tasks",
        "list": ["agent-example", "list", "--json"],
        "entry": {"id": "id", "title": "title"},
        **extra,
    }


def test_parses_pivot_actions_cards_forms_and_config_sections():
    contribution = parse_manifest(
        _pivot(
            columns=[{"key": "state", "width": 8, "palette": "state"}],
            actions=[
                {"key": "open", "label": "Open", "kind": "internal", "verb": "open-cli"},
                {
                    "key": "steer",
                    "label": "Steer",
                    "kind": "form",
                    "fields_from": "card.request_input",
                    "run": ["agent-example", "steer", "{fields}"],
                },
                {"key": "card", "label": "Card", "kind": "card"},
            ],
            worktree_actions=[
                {"key": "send", "label": "Send", "run": ["agent-example", "send", "{id}"]},
            ],
            config_sections=[
                {"key": "settings", "label": "Settings", "run": ["agent-example", "config"]},
            ],
        ),
        name="agent-example",
        marketplace="example",
        plugin="agent-example",
        source_path="/payload/pivots/agent-example.json",
    )
    assert contribution.schema_version == CONTRACT_VERSION
    assert contribution.pivot is not None
    assert contribution.pivot.columns[0].palette == "state"
    assert [a.kind for a in contribution.pivot.actions] == ["internal", "form", "card"]
    assert contribution.pivot.actions[1].form["fields_from"] == "card.request_input"
    assert contribution.worktree_actions[0].key == "send"
    assert contribution.config_sections[0].key == "settings"


@pytest.mark.parametrize("argv", [
    ["agent-example", 1],
    ["agent-example", ""],
])
def test_argv_rejects_non_string_or_empty_elements(argv):
    with pytest.raises(ContractError):
        parse_manifest(
            _pivot(list=argv),
            name="agent-example",
            marketplace="example",
            plugin="agent-example",
            source_path="/payload/pivots/agent-example.json",
        )


def test_discovers_only_effectively_enabled_payloads(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    plugins = home / ".copilot" / "installed-plugins"
    _settings(home, {
        "agent-enabled@example": True,
        "agent-disabled@example": False,
    })
    _manifest(plugins, "example", "agent-enabled", "enabled", _pivot())
    _manifest(plugins, "example", "agent-disabled", "disabled", _pivot(label="Disabled"))
    monkeypatch.setattr("worktree_manager.plugin_contracts.shutil.which", lambda _: "/bin/tool")

    report = discover_contracts(home_dir=home)
    assert [c.plugin for c in report.contributions] == ["agent-enabled"]
    assert any(
        f.code == "disabled-contribution" and f.plugin == "agent-disabled"
        for f in report.findings
    )


def test_project_enablement_adds_project_scoped_plugin(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    plugins = home / ".copilot" / "installed-plugins"
    _settings(home, {})
    checkout = home / "src" / "repo"
    settings = checkout / ".github" / "copilot"
    settings.mkdir(parents=True)
    (settings / "settings.json").write_text(json.dumps({
        "enabledPlugins": {"agent-project@example": True},
    }), encoding="utf-8")
    aw = home / ".agent-worktrees"
    aw.mkdir()
    (aw / "repos.yaml").write_text(
        "repos:\n"
        "  repo:\n"
        "    class: worktree\n"
        f"    windows: \"{str(checkout).replace(chr(92), chr(92) * 2)}\"\n",
        encoding="utf-8",
    )
    (aw / "projects.yaml").write_text(
        "projects:\n  repo:\n    config_dir: \"~/.repo\"\n", encoding="utf-8")
    _manifest(plugins, "example", "agent-project", "project", _pivot())
    monkeypatch.setattr("worktree_manager.plugin_contracts.shutil.which", lambda _: "/bin/tool")

    report = discover_contracts(project="repo", home_dir=home)
    assert [c.qualified_plugin for c in report.contributions] == [
        "agent-project@example"
    ]


def test_project_false_overrides_user_global_true(tmp_path: Path):
    home = tmp_path / "home"
    plugins = home / ".copilot" / "installed-plugins"
    _settings(home, {"agent-project@example": True})
    checkout = home / "src" / "repo"
    settings = checkout / ".github" / "copilot"
    settings.mkdir(parents=True)
    (settings / "settings.local.json").write_text(json.dumps({
        "enabledPlugins": {"agent-project@example": False},
    }), encoding="utf-8")
    aw = home / ".agent-worktrees"
    aw.mkdir()
    (aw / "repos.yaml").write_text(
        "repos:\n"
        "  repo:\n"
        "    class: worktree\n"
        f"    windows: \"{str(checkout).replace(chr(92), chr(92) * 2)}\"\n",
        encoding="utf-8",
    )
    (aw / "projects.yaml").write_text(
        "projects:\n  repo:\n    config_dir: \"~/.repo\"\n", encoding="utf-8")
    _manifest(plugins, "example", "agent-project", "project", _pivot())

    report = discover_contracts(project="repo", home_dir=home)
    assert report.contributions == ()
    assert any(f.code == "disabled-contribution" for f in report.findings)


def test_legacy_schema_is_accepted_with_finding(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    plugins = home / ".copilot" / "installed-plugins"
    _settings(home, {"agent-example@example": True})
    legacy = _pivot()
    legacy.pop("schema_version")
    _manifest(plugins, "example", "agent-example", "legacy", legacy)
    monkeypatch.setattr("worktree_manager.plugin_contracts.shutil.which", lambda _: "/bin/tool")

    report = discover_contracts(home_dir=home)
    assert report.contributions[0].legacy_schema is True
    assert any(f.code == "legacy-schema" for f in report.findings)


def test_invalid_and_duplicate_contributions_are_isolated(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    plugins = home / ".copilot" / "installed-plugins"
    _settings(home, {
        "agent-a@example": True,
        "agent-b@example": True,
        "agent-bad@example": True,
    })
    _manifest(plugins, "example", "agent-a", "same", _pivot())
    _manifest(plugins, "example", "agent-b", "same", _pivot())
    bad = _manifest(plugins, "example", "agent-bad", "bad", {})
    bad.write_text("{", encoding="utf-8")
    monkeypatch.setattr("worktree_manager.plugin_contracts.shutil.which", lambda _: "/bin/tool")

    report = discover_contracts(home_dir=home)
    assert len(report.contributions) == 2
    assert sum(f.code == "duplicate-pivot-label" for f in report.findings) == 2
    assert any(f.code == "invalid-json" for f in report.findings)


def test_non_utf8_manifest_isolated(tmp_path: Path):
    home = tmp_path / "home"
    plugins = home / ".copilot" / "installed-plugins"
    _settings(home, {
        "agent-good@example": True,
        "agent-bad@example": True,
    })
    _manifest(plugins, "example", "agent-good", "good", _pivot())
    bad = _manifest(plugins, "example", "agent-bad", "bad", _pivot())
    bad.write_bytes(b'{"label":"\xff"}')

    report = discover_contracts(home_dir=home)
    assert [c.plugin for c in report.contributions] == ["agent-good"]
    assert any(f.code == "invalid-json" and f.plugin == "agent-bad" for f in report.findings)


def test_legacy_registry_drift_is_reported(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    plugins = home / ".copilot" / "installed-plugins"
    _settings(home, {"agent-example@example": True})
    _manifest(plugins, "example", "agent-example", "example", _pivot())
    legacy = home / ".agent-worktrees" / "pivots"
    legacy.mkdir(parents=True)
    (legacy / "example.json").write_text("{}", encoding="utf-8")
    (legacy / "orphan.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr("worktree_manager.plugin_contracts.shutil.which", lambda _: "/bin/tool")

    report = discover_contracts(home_dir=home)
    assert any(f.code == "stale-legacy-dropin" for f in report.findings)
    assert any(f.code == "orphan-legacy-dropin" for f in report.findings)


def test_missing_optional_action_command_does_not_disable_pivot(
    tmp_path: Path, monkeypatch
):
    home = tmp_path / "home"
    plugins = home / ".copilot" / "installed-plugins"
    _settings(home, {"agent-example@example": True})
    _manifest(plugins, "example", "agent-example", "example", _pivot(actions=[
        {"key": "local", "label": "Local", "run": ["agent-example", "open"]},
        {"key": "peer", "label": "Peer", "run": ["agent-peer", "open"]},
    ]))
    monkeypatch.setattr(
        "worktree_manager.plugin_contracts.shutil.which",
        lambda command: None if command == "agent-peer" else f"/bin/{command}",
    )

    report = discover_contracts(home_dir=home)
    contribution = report.contributions[0]
    assert contribution.command_available is True
    assert contribution.pivot is not None
    assert [a.available for a in contribution.pivot.actions] == [True, False]
    assert any("action peer: agent-peer" in f.detail for f in report.findings)


def test_contracts_command_emits_machine_readable_report(
    tmp_path: Path, monkeypatch, capsys
):
    home = tmp_path / "home"
    plugins = home / ".copilot" / "installed-plugins"
    _settings(home, {"agent-example@example": True})
    _manifest(plugins, "example", "agent-example", "example", _pivot())
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("WORKTREE_MANAGER_PLUGINS_DIR", str(plugins))
    monkeypatch.setattr("worktree_manager.plugin_contracts.shutil.which", lambda _: "/bin/tool")

    assert main(["contracts", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["contract_version"] == CONTRACT_VERSION
    assert report["contributions"][0]["plugin"] == "agent-example"


def test_real_checkout_manifests_match_contract():
    root = Path(__file__).resolve().parents[2]
    paths = sorted((root / "plugins").glob("*/pivots/*.json"))
    assert paths
    for path in paths:
        data = json.loads(path.read_text("utf-8"))
        contribution = parse_manifest(
            data,
            name=path.stem,
            marketplace="copilot-extensions",
            plugin=path.parents[1].name,
            source_path=str(path),
        )
        assert contribution.schema_version == CONTRACT_VERSION
        assert contribution.legacy_schema is False
