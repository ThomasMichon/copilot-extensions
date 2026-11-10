"""Manager-owned plugin contribution contract tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from agent_procutil import no_window_kwargs

from worktree_manager.plugin_contracts import (
    CONTRACT_VERSION,
    ContractError,
    discover_contracts,
    parse_manifest,
)
from worktree_manager.__main__ import main


@pytest.fixture(autouse=True)
def _isolate_plugin_root_overrides(monkeypatch):
    monkeypatch.delenv("WORKTREE_MANAGER_PLUGINS_DIR", raising=False)
    monkeypatch.delenv("AGENT_WORKTREES_PLUGINS_DIR", raising=False)
    monkeypatch.delenv("AGENT_WORKTREES_PIVOTS_DIR", raising=False)


def _settings(home: Path, enabled: dict[str, bool]) -> None:
    root = home / ".copilot"
    root.mkdir(parents=True, exist_ok=True)
    (root / "settings.json").write_text(
        json.dumps({"enabledPlugins": enabled}), encoding="utf-8")


def _manifest(root: Path, marketplace: str, plugin: str, name: str, data: dict) -> Path:
    path = root / marketplace / plugin / "pivots" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    (path.parents[1] / "plugin.json").write_text(
        json.dumps({"name": plugin}), encoding="utf-8")
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


def _directory_plugin(home: Path, *, project: bool) -> Path:
    checkout = home / "src" / "repo"
    marketplace = checkout / ".ai"
    plugin = marketplace / "payload"
    (marketplace / ".claude-plugin").mkdir(parents=True)
    (marketplace / ".claude-plugin" / "marketplace.json").write_text(json.dumps({
        "name": "local",
        "plugins": [{"name": "agent-local", "source": "./payload"}],
    }), encoding="utf-8")
    plugin.mkdir()
    (plugin / "plugin.json").write_text(
        json.dumps({"name": "agent-local"}), encoding="utf-8")
    settings = (
        checkout / ".github" / "copilot" if project else home / ".copilot"
    )
    settings.mkdir(parents=True, exist_ok=True)
    (settings / "settings.json").write_text(json.dumps({
        "extraKnownMarketplaces": {
            "local": {"source": {"source": "directory", "path": str(marketplace)}},
        },
        "enabledPlugins": {"agent-local@local": True},
    }), encoding="utf-8")
    if project:
        aw = home / ".agent-worktrees"
        aw.mkdir()
        (aw / "repos.yaml").write_text(
            "repos:\n  repo:\n    class: worktree\n"
            + "".join(
                f"    {platform}: {json.dumps(str(checkout))}\n"
                for platform in ("windows", "linux", "macos")
            ),
            encoding="utf-8",
        )
        (aw / "projects.yaml").write_text(
            "projects:\n  repo:\n    config_dir: \"~/.repo\"\n", encoding="utf-8")
    pivot = plugin / "pivots" / "local.json"
    pivot.parent.mkdir()
    pivot.write_text(json.dumps(_pivot(list=[sys.executable])), encoding="utf-8")
    return pivot


@pytest.mark.parametrize("project", [False, True])
def test_directory_contract_keeps_hidden_declaration_owned_and_read_only(
    tmp_path, project, monkeypatch,
):
    from plugin_activation import resolve_active_plugins
    from worktree_manager.production_picker.picker_tui import pivot_manifest, pivots

    home = tmp_path / "home"
    path = _directory_plugin(home, project=project)
    path.write_text(json.dumps(_pivot(
        list=[sys.executable], visible_when={"state_root_file": "notes/enabled.json"},
    )), encoding="utf-8")
    legacy = home / ".agent-worktrees" / "pivots" / path.name
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_bytes(path.read_bytes())
    before = {p: p.read_bytes() for p in home.rglob("*") if p.is_file()}

    report = discover_contracts(project="repo" if project else None, home_dir=home)

    assert [c.qualified_plugin for c in report.contributions] == ["agent-local@local"]
    assert report.contributions[0].source_path == str(path.resolve())
    assert not any(f.code.endswith("legacy-dropin") for f in report.findings)
    registry = tmp_path / "ui-pivots"
    registry.mkdir()
    (registry / path.name).write_text(json.dumps({
        "schema_version": pivot_manifest.MANAGED_SCHEMA_VERSION,
        "plugin": "agent-local@local",
        "plugin_root": str(path.parents[1].resolve()),
        "template": path.name,
    }), encoding="utf-8")
    monkeypatch.setattr(pivot_manifest, "_resolve_state_root_path", lambda: None)
    ui = pivots.scan_pivot_registry(
        registry, materialize=False,
        activation_report=resolve_active_plugins(
            home=home, include_projects=False,
            project_root=path.parents[3] if project else None,
        ),
    )
    assert len(ui.contributions) == 1
    assert ui.pivots == []
    assert {p: p.read_bytes() for p in home.rglob("*") if p.is_file()} == before


def test_directory_contract_supersedes_cached_copy_and_retains_stale_owner(tmp_path):
    home = tmp_path / "home"
    path = _directory_plugin(home, project=False)
    _manifest(home / ".copilot" / "installed-plugins", "local", "agent-local", "local",
              _pivot(label="Cached", list=[sys.executable]))
    legacy = home / ".agent-worktrees" / "pivots" / path.name
    legacy.parent.mkdir(parents=True)
    legacy.write_text("{}", encoding="utf-8")

    report = discover_contracts(home_dir=home)

    assert len(report.contributions) == 1
    assert report.contributions[0].pivot.label == "Tasks"
    assert not any(f.code == "orphan-legacy-dropin" for f in report.findings)
    stale = next(f for f in report.findings if f.code == "stale-legacy-dropin")
    assert (stale.marketplace, stale.plugin) == ("local", "agent-local")
    assert str(path.resolve()) in stale.detail


def test_directory_contract_respects_project_disable_and_scope(tmp_path):
    home = tmp_path / "home"
    path = _directory_plugin(home, project=True)
    assert discover_contracts(home_dir=home).contributions == ()
    _settings(home, {"agent-local@local": True})
    (path.parents[3] / ".github" / "copilot" / "settings.local.json").write_text(
        json.dumps({"enabledPlugins": {"agent-local@local": False}}), encoding="utf-8")

    assert discover_contracts(project="repo", home_dir=home).contributions == ()


@pytest.mark.parametrize("base,local", [(False, True), (None, True), (True, False)])
def test_contracts_use_effective_user_local_enablement(tmp_path, base, local):
    home = tmp_path / "home"
    path = _directory_plugin(home, project=False)
    settings = home / ".copilot" / "settings.json"
    data = json.loads(settings.read_text("utf-8"))
    data["enabledPlugins"] = {} if base is None else {"agent-local@local": base}
    settings.write_text(json.dumps(data), encoding="utf-8")
    (settings.parent / "settings.local.json").write_text(json.dumps({
        "enabledPlugins": {"agent-local@local": local},
    }), encoding="utf-8")
    cached = home / ".copilot" / "installed-plugins"
    _manifest(cached, "local", "agent-local", "local", _pivot())
    legacy = home / ".agent-worktrees" / "pivots" / path.name
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(path.read_bytes())

    report = discover_contracts(home_dir=home, plugins_root=cached)

    assert [c.qualified_plugin for c in report.contributions] == (
        ["agent-local@local"] if local else []
    )
    assert any(f.code == "disabled-contribution" for f in report.findings) is not local
    assert any(f.code == "orphan-legacy-dropin" for f in report.findings) is not local


@pytest.mark.parametrize("anchor_only", [False, True])
def test_contracts_handle_project_registration_without_repository_path(tmp_path, anchor_only):
    home = tmp_path / "home"
    path = _directory_plugin(home, project=True)
    aw = home / ".agent-worktrees"
    (aw / "repos.yaml").write_text(
        "repos: {}\n" if anchor_only else "repos:\n  repo:\n    class: worktree\n",
        encoding="utf-8",
    )
    if anchor_only:
        (aw / "projects.yaml").write_text(
            "projects:\n  repo:\n    config_dir: \"~/.repo\"\n"
            f"    anchor: {json.dumps(str(path.parents[3]))}\n", encoding="utf-8",
        )

    report = discover_contracts(project="repo", home_dir=home)

    assert [c.qualified_plugin for c in report.contributions] == (
        ["agent-local@local"] if anchor_only else []
    )


def test_directory_source_failure_retains_canonical_activation_diagnostics(tmp_path):
    home = tmp_path / "home"
    path = _directory_plugin(home, project=False)
    cached = _manifest(
        home / ".copilot" / "installed-plugins", "local", "agent-local", "local",
        _pivot(label="Cached", list=[sys.executable]),
    )
    (path.parents[1] / "plugin.json").write_text(
        json.dumps({"name": "wrong-owner"}), encoding="utf-8")

    report = discover_contracts(home_dir=home)

    assert [c.source_path for c in report.contributions] == [str(cached.resolve())]
    assert any(f.code.startswith("activation-") for f in report.findings)


def test_directory_contract_cli_reads_fresh_profile_without_materializing(tmp_path):
    home = tmp_path / "home"
    path = _directory_plugin(home, project=False)
    malformed = path.with_name("malformed.json")
    malformed.write_text("{", encoding="utf-8")
    legacy = home / ".agent-worktrees" / "pivots"
    legacy.mkdir(parents=True)
    (legacy / path.name).write_bytes(path.read_bytes())
    (legacy / "orphan.json").write_text("{}", encoding="utf-8")
    before = {p: p.read_bytes() for p in home.rglob("*") if p.is_file()}
    env = {**os.environ, "USERPROFILE": str(home), "HOME": str(home)}
    for key in ("WORKTREE_MANAGER_PLUGINS_DIR", "AGENT_WORKTREES_PLUGINS_DIR",
                "AGENT_WORKTREES_PIVOTS_DIR"):
        env.pop(key, None)

    result = subprocess.run(
        [sys.executable, "-m", "worktree_manager", "contracts", "--json"],
        env=env, capture_output=True, text=True, timeout=15, **no_window_kwargs(),
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert [c["plugin"] for c in report["contributions"]] == ["agent-local"]
    assert any(f["code"] == "invalid-json" for f in report["findings"])
    assert [f["source_path"] for f in report["findings"]
            if f["code"] == "orphan-legacy-dropin"] == [str(legacy / "orphan.json")]
    assert {p: p.read_bytes() for p in home.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("surface", [
    "list", "actions", "view_actions", "worktree_actions", "config_sections",
])
@pytest.mark.parametrize("invalid", [42, ["example", False], ["bad\x00command"]])
def test_discovery_isolates_malformed_argv_from_valid_peer(tmp_path, surface, invalid):
    home = tmp_path / "home"
    root = home / ".copilot/installed-plugins"
    _settings(home, {"agent-good@example": True, "agent-bad@example": True})
    _manifest(root, "example", "agent-good", "good", {
        "schema_version": 1, "label": "Good", "list": [sys.executable],
    })
    bad = {"schema_version": 1, "label": "Bad", "list": [sys.executable]}
    if surface == "list":
        bad["list"] = invalid
    else:
        bad[surface] = [{"label": "Malformed", "run": invalid}]
    _manifest(root, "example", "agent-bad", "bad", bad)
    report = discover_contracts(home_dir=home, plugins_root=root)
    assert [contribution.plugin for contribution in report.contributions] == ["agent-good"]
    assert any(
        finding.code == "invalid-contract" and finding.plugin == "agent-bad"
        for finding in report.findings
    )


@pytest.fixture(autouse=True)
def _fixture_owned_plugin_roots(monkeypatch):
    for key in (
        "WORKTREE_MANAGER_PLUGINS_DIR",
        "AGENT_WORKTREES_PLUGINS_DIR",
        "AGENT_WORKTREES_PIVOTS_DIR",
    ):
        monkeypatch.delenv(key, raising=False)


def test_configuration_form_contract_and_command_availability():
    from worktree_manager.plugin_contracts import _probe_commands

    form = {
        "label": "Controls", "confirm": True,
        "run": [sys.executable, "{field.pool}"],
        "fields": [{"name": "pool", "type": "choice",
                    "options_command": ["definitely-missing-config-options"]}],
    }
    contribution = parse_manifest(
        {"config_sections": [{"label": "Controls", "run": [sys.executable],
                              "configure_action": form}]},
        name="fixture", marketplace="example", plugin="fixture", source_path="fixture.json",
    )
    action = contribution.config_sections[0].configure_action
    assert action.confirm and not action.fields[0].get("allow_other")
    probed, missing = _probe_commands(contribution)
    assert not probed.config_sections[0].available
    assert missing == ["config section fixture0: definitely-missing-config-options"]
    form["fields"] = [{"name": "pool"}, {"name": "pool"}]
    with pytest.raises(ContractError, match="duplicates"):
        parse_manifest(
            {"config_sections": [{"label": "Controls", "run": [sys.executable],
                                  "configure_action": form}]},
            name="fixture", marketplace="example", plugin="fixture", source_path="fixture.json",
        )


def test_parses_pivot_actions_cards_forms_and_config_sections():
    contribution = parse_manifest(
        _pivot(
            entity="task",
            home=True,
            items_field="tasks",
            ready_status="{count} tasks",
            columns=[{"key": "state", "width": 8, "palette": "state"}],
            actions=[
                {
                    "key": "open",
                    "label": "Open",
                    "kind": "internal",
                    "verb": "open-cli",
                    "shortcut": "o",
                },
                {
                    "key": "steer",
                    "label": "Steer",
                    "kind": "form",
                    "fields_from": "card.request_input",
                    "run": ["agent-example", "steer", "{fields}"],
                },
                {"key": "card", "label": "Card", "kind": "card"},
            ],
            view_actions=[
                {"key": "new", "label": "New", "kind": "internal", "verb": "new"},
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
    assert contribution.pivot.entity == "task"
    assert contribution.pivot.items_field == "tasks"
    assert contribution.pivot.ready_status == "{count} tasks"
    assert contribution.pivot.home is True
    assert contribution.pivot.columns[0].palette == "state"
    assert [a.kind for a in contribution.pivot.actions] == ["internal", "form", "card"]
    assert contribution.pivot.actions[0].shortcut == "o"
    assert contribution.pivot.view_actions[0].internal == "new"
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


def test_home_requires_boolean():
    with pytest.raises(ContractError, match="`home` must be a boolean"):
        parse_manifest(
            _pivot(home="yes"),
            name="agent-example",
            marketplace="example",
            plugin="agent-example",
            source_path="/payload/pivots/agent-example.json",
        )


def test_entity_requires_non_empty_string():
    with pytest.raises(ContractError, match="`entity` must be a non-empty string"):
        parse_manifest(
            _pivot(entity=""),
            name="agent-example",
            marketplace="example",
            plugin="agent-example",
            source_path="/payload/pivots/agent-example.json",
        )


def test_items_field_requires_non_empty_string():
    with pytest.raises(ContractError, match="`items_field` must be a non-empty string"):
        parse_manifest(
            _pivot(items_field=""),
            name="agent-example",
            marketplace="example",
            plugin="agent-example",
            source_path="/payload/pivots/agent-example.json",
        )


def test_action_shortcut_requires_non_empty_string():
    with pytest.raises(ContractError, match=r"`actions\[0\]\.shortcut`"):
        parse_manifest(
            _pivot(actions=[
                {
                    "label": "Open",
                    "kind": "internal",
                    "verb": "open",
                    "shortcut": "",
                },
            ]),
            name="agent-example",
            marketplace="example",
            plugin="agent-example",
            source_path="/payload/pivots/agent-example.json",
        )


def test_view_action_errors_name_the_view_actions_field():
    with pytest.raises(ContractError, match=r"`view_actions\[0\]` must be an object"):
        parse_manifest(
            _pivot(view_actions=["invalid"]),
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


def test_duplicate_home_pivots_are_reported(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    plugins = home / ".copilot" / "installed-plugins"
    _settings(home, {
        "agent-a@example": True,
        "agent-b@example": True,
    })
    _manifest(plugins, "example", "agent-a", "a", _pivot(label="A", home=True))
    _manifest(plugins, "example", "agent-b", "b", _pivot(label="B", home=True))
    monkeypatch.setattr("worktree_manager.plugin_contracts.shutil.which", lambda _: "/bin/tool")

    report = discover_contracts(home_dir=home)

    assert len(report.contributions) == 2
    assert sum(f.code == "duplicate-home-pivot" for f in report.findings) == 2


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
    ], view_actions=[
        {"key": "new", "label": "New", "run": ["agent-missing", "new"]},
    ]))
    monkeypatch.setattr(
        "worktree_manager.plugin_contracts.shutil.which",
        lambda command: (
            None if command in ("agent-peer", "agent-missing") else f"/bin/{command}"
        ),
    )

    report = discover_contracts(home_dir=home)
    contribution = report.contributions[0]
    assert contribution.command_available is True
    assert contribution.pivot is not None
    assert [a.available for a in contribution.pivot.actions] == [True, False]
    assert contribution.pivot.view_actions[0].available is False
    assert any("action peer: agent-peer" in f.detail for f in report.findings)
    assert any("view action new: agent-missing" in f.detail for f in report.findings)


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
