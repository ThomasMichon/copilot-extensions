from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from agent_mcp import __main__ as cli
from agent_mcp import config, cutover
from agent_mcp.provider_selection import effective_enablement


@pytest.fixture
def providers(tmp_path, monkeypatch):
    home = tmp_path / "copilot"
    plugins = home / "installed-plugins"
    current = plugins / "current" / "example" / "agents" / "demo.mcp.yaml"
    disabled = plugins / "retained" / "example" / "agents" / "demo.mcp.yaml"
    for path in (current, disabled):
        path.parent.mkdir(parents=True)
        path.write_text(
            "server:\n  type: http\n  url: https://example.invalid/mcp\n",
            encoding="utf-8",
        )
    (home / "settings.json").write_text(json.dumps({
        "enabledPlugins": {"example@retained": False, "example@current": True},
    }), encoding="utf-8")
    monkeypatch.setenv("COPILOT_HOME", str(home))
    monkeypatch.setenv("AGENT_MCP_PLUGIN_ROOTS", str(plugins))
    monkeypatch.setenv("AGENT_MCP_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("AGENT_MCP_MARKETPLACE_ROOT", raising=False)
    monkeypatch.setattr(config, "BRIDGES_DIR", tmp_path / "state" / "bridges")
    monkeypatch.chdir(tmp_path)
    return home, current, disabled


def test_disabled_cache_does_not_block_either_readiness_consumer(providers, capsys):
    _home, current, disabled = providers

    assert len(config.discover_plugin_bridge_candidates()) == 2
    assert cutover._configured_readiness()["state"] == "ready"
    assert cli.main(["installer-readiness"]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "ready"
    assert config.resolve_config_path(str(disabled)) == disabled
    assert config.load_config(str(disabled)).source_path == disabled
    assert current.is_file() and disabled.is_file()


@pytest.mark.parametrize("selection", ({}, {"example@retained": True}))
def test_unknown_or_reenabled_cache_still_refuses_ambiguity(providers, selection):
    home, _current, _disabled = providers
    (home / "settings.json").write_text(json.dumps({"enabledPlugins": selection}), encoding="utf-8")

    result = cutover._configured_readiness()

    assert result["state"] == "failed"
    assert "ambiguous" in result["detail"]


def test_project_and_local_precedence_can_reenable_provider(providers):
    home, _current, _disabled = providers
    (home / "settings.local.json").write_text(
        '{"enabledPlugins":{"example@retained":true}}', encoding="utf-8",
    )
    assert effective_enablement()["example@retained"] is True
    settings = home.parent / ".github" / "copilot"
    settings.mkdir(parents=True)
    (settings / "settings.json").write_text(
        '{"enabledPlugins":{"example@retained":false}}', encoding="utf-8",
    )
    assert cutover._configured_readiness()["state"] == "ready"
    (settings / "settings.local.json").write_text(
        '{"enabledPlugins":{"example@retained":true}}', encoding="utf-8",
    )
    assert cutover._configured_readiness()["state"] == "failed"


@pytest.mark.parametrize("raw", (
    "{", "[]", '{"enabledPlugins":[]}',
    '{"enabledPlugins":{"example@retained":"false"}}',
    '{"enabledPlugins":{"example@retained":true,"example@retained":false}}',
))
def test_invalid_selection_fails_readiness_instead_of_hiding_cache(providers, capsys, raw):
    home, _current, _disabled = providers
    (home / "settings.json").write_text(raw, encoding="utf-8")

    assert cli.main(["installer-readiness"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "failed"
    assert "Invalid plugin selection" in result["detail"]
    assert cutover._configured_readiness()["state"] == "failed"


def test_unreadable_selection_does_not_hide_cached_provider(providers):
    home, _current, _disabled = providers
    (home / "settings.json").write_bytes(b"\xff")

    result = cutover._configured_readiness()

    assert result["state"] == "failed"
    assert "Cannot read plugin selection" in result["detail"]


def test_selected_invalid_content_and_user_collision_still_fail(providers):
    _home, current, _disabled = providers
    current.write_text("server:\n  type: http\n", encoding="utf-8")
    assert "invalid" in cutover._configured_readiness()["detail"]
    user = config.BRIDGES_DIR / "demo.yaml"
    user.parent.mkdir(parents=True)
    user.write_text(current.read_text(encoding="utf-8"), encoding="utf-8")
    assert "ambiguous" in cutover._configured_readiness()["detail"]


def test_explicit_marketplace_scope_is_not_replaced_by_cache_selection(providers, monkeypatch):
    _home, _current, disabled = providers
    monkeypatch.setenv("AGENT_MCP_MARKETPLACE_ROOT", str(disabled.parents[2]))
    disabled.write_text("server:\n  type: http\n", encoding="utf-8")

    result = cutover._configured_readiness()

    assert result["state"] == "failed"
    assert "invalid" in result["detail"]


def test_disabled_alias_cannot_hide_active_physical_provider(providers):
    home, current, _disabled = providers
    alias = home / "installed-plugins" / "aaa" / "example"
    alias.parent.mkdir()
    try:
        alias.symlink_to(current.parent.parent, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory symlinks unavailable: {error}")
    settings = home / "settings.json"
    document = json.loads(settings.read_text(encoding="utf-8"))
    document["enabledPlugins"]["example@aaa"] = False
    settings.write_text(json.dumps(document), encoding="utf-8")

    assert cutover._configured_readiness()["state"] == "ready"


def test_actual_readiness_command_uses_isolated_selection(providers):
    home, current, disabled = providers
    before = {path: path.read_bytes() for path in (home / "settings.json", current, disabled)}

    result = subprocess.run(
        [sys.executable, "-m", "agent_mcp", "installer-readiness"],
        cwd=home.parent, env=dict(os.environ), capture_output=True, text=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["state"] == "ready"
    assert all(path.read_bytes() == content for path, content in before.items())
    assert not (home.parent / "state").exists()
