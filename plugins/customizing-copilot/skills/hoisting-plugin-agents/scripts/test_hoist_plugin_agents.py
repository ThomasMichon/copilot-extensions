"""Tests for hoist_plugin_agents.py -- the marketplace-agent delegation
workaround (hoisting-plugin-agents skill).

Stdlib + pytest only.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path, PurePosixPath

import pytest

_SCRIPT = Path(__file__).with_name("hoist_plugin_agents.py")
_spec = importlib.util.spec_from_file_location("hoist_plugin_agents", _SCRIPT)
hoist = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = hoist
_spec.loader.exec_module(hoist)


def _settings(repo: Path, enabled: dict, marketplaces: dict) -> None:
    path = repo / ".github" / "copilot" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"enabledPlugins": enabled, "extraKnownMarketplaces": marketplaces}
        ),
        encoding="utf-8",
    )


def _plugin_agent(
    repo: Path,
    marketplace_dir: str,
    plugin: str,
    agent_name: str,
    *,
    version: str = "0.1.0",
    body: str = "# An agent\n",
) -> Path:
    plugin_dir = repo / marketplace_dir / plugin
    (plugin_dir / "agents").mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps({"name": plugin, "version": version}), encoding="utf-8"
    )
    agent_path = plugin_dir / "agents" / f"{agent_name}.agent.md"
    agent_path.write_text(
        f"---\nname: {agent_name}\ndescription: test\n---\n{body}",
        encoding="utf-8",
    )
    return agent_path


def _basic_repo(tmp_path: Path) -> Path:
    _settings(
        tmp_path,
        {"widget@mkt": True},
        {"mkt": {"source": {"source": "directory", "path": "./.ai"}}},
    )
    _plugin_agent(tmp_path, ".ai", "widget", "widget")
    return tmp_path


def test_enabled_plugin_agent_dirs_resolves_directory_marketplace(tmp_path):
    repo = _basic_repo(tmp_path)
    resolved = hoist.enabled_plugin_agent_dirs(repo)
    assert resolved == [("mkt", "widget", (repo / ".ai" / "widget" / "agents").resolve())]


def test_disabled_plugin_is_skipped(tmp_path):
    _settings(
        tmp_path,
        {"widget@mkt": False},
        {"mkt": {"source": {"source": "directory", "path": "./.ai"}}},
    )
    _plugin_agent(tmp_path, ".ai", "widget", "widget")
    assert hoist.enabled_plugin_agent_dirs(tmp_path) == []


def test_non_directory_marketplace_is_skipped(tmp_path):
    _settings(
        tmp_path,
        {"widget@mkt": True},
        {"mkt": {"source": {"source": "github", "repo": "example/example"}}},
    )
    assert hoist.enabled_plugin_agent_dirs(tmp_path) == []


def test_sync_writes_expected_output_with_rewritten_links(tmp_path):
    repo = _basic_repo(tmp_path)
    skill_ref = repo / ".ai" / "widget" / "skills" / "SKILL.md"
    skill_ref.parent.mkdir(parents=True)
    skill_ref.write_text("# skill\n", encoding="utf-8")
    agent_path = repo / ".ai" / "widget" / "agents" / "widget.agent.md"
    agent_path.write_text(
        "---\nname: widget\ndescription: test\n---\n"
        "See [the skill](../skills/SKILL.md) and "
        "[an anchor](#section) and "
        "[a URL](https://example.com/x).\n",
        encoding="utf-8",
    )

    written, stale = hoist.sync(repo, PurePosixPath(".github/agents"))
    assert stale == []
    output_path = repo / ".github" / "agents" / "widget.agent.md"
    assert output_path in written
    content = output_path.read_text(encoding="utf-8")
    assert "[the skill](../../.ai/widget/skills/SKILL.md)" in content
    assert "[an anchor](#section)" in content
    assert "[a URL](https://example.com/x)" in content
    assert "GENERATED" in content

    # Idempotent: second sync reports nothing new to write.
    written_again, stale_again = hoist.sync(repo, PurePosixPath(".github/agents"))
    assert written_again == []
    assert stale_again == []


def test_scan_reports_drift_without_writing(tmp_path):
    repo = _basic_repo(tmp_path)
    mismatches, stale = hoist.scan(repo, PurePosixPath(".github/agents"))
    assert len(mismatches) == 1
    assert stale == []
    assert not (repo / ".github" / "agents" / "widget.agent.md").exists()


def test_scan_reports_stale_output(tmp_path):
    repo = _basic_repo(tmp_path)
    hoist.sync(repo, PurePosixPath(".github/agents"))
    _settings(repo, {}, {})  # disable everything
    mismatches, stale = hoist.scan(repo, PurePosixPath(".github/agents"))
    assert mismatches == []
    assert stale == [repo.resolve() / ".github" / "agents" / "widget.agent.md"]


def test_conflicting_agent_names_across_plugins_raise(tmp_path):
    _settings(
        tmp_path,
        {"widget@mkt": True, "gadget@mkt": True},
        {"mkt": {"source": {"source": "directory", "path": "./.ai"}}},
    )
    _plugin_agent(tmp_path, ".ai", "widget", "shared-name")
    _plugin_agent(tmp_path, ".ai", "gadget", "shared-name")
    with pytest.raises(hoist.HoistError):
        hoist.desired(tmp_path, PurePosixPath(".github/agents"))


def test_missing_settings_file_raises(tmp_path):
    with pytest.raises(hoist.HoistError):
        hoist.enabled_plugin_agent_dirs(tmp_path)


def test_relative_link_escaping_repo_root_raises(tmp_path):
    repo = _basic_repo(tmp_path)
    agent_path = repo / ".ai" / "widget" / "agents" / "widget.agent.md"
    agent_path.write_text(
        "---\nname: widget\ndescription: test\n---\n"
        "[escape](../../../../outside.md)\n",
        encoding="utf-8",
    )
    with pytest.raises(hoist.HoistError):
        hoist.desired(repo, PurePosixPath(".github/agents"))


def test_marketplace_path_escape_raises(tmp_path):
    _settings(
        tmp_path,
        {"widget@mkt": True},
        {"mkt": {"source": {"source": "directory", "path": "../outside"}}},
    )
    with pytest.raises(hoist.HoistError):
        hoist.enabled_plugin_agent_dirs(tmp_path)
