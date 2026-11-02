from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).parents[1] / "skills" / "capturing-client-requests" / "scripts"
sys.path.insert(0, str(SCRIPTS))
from snapshot_inventory import collect_inventory, scanner_module

pytestmark = pytest.mark.guard


@pytest.fixture(autouse=True)
def isolated_custom_instruction_directories(monkeypatch):
    monkeypatch.delenv("COPILOT_CUSTOM_INSTRUCTIONS_DIRS", raising=False)


def write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_candidate_inventory_uses_selected_config_and_no_hooks(tmp_path, monkeypatch):
    home, root, config = tmp_path / "home", tmp_path / "repo", tmp_path / "custom-config"
    home.mkdir()
    root.mkdir()
    (root / ".git").write_text("synthetic", encoding="utf-8")
    write(root / "AGENTS.md", "Synthetic repository instructions.\n")
    write(root / ".github" / "skills" / "sample" / "SKILL.md",
          "---\nname: sample\ndescription: >\n  Synthetic skill description.\n---\nBody\n")
    write(root / ".github" / "agents" / "sample.agent.md",
          "---\nname: sample-agent\ndescription: Synthetic agent.\n---\nBody\n")
    write(config / "copilot-instructions.md", "Synthetic personal instructions.\n")
    write(config / "mcp-config.json", json.dumps({
        "mcpServers": {"example-server": {
            "command": "MUST_NOT_RUN", "tools": ["*", "lookup", "read_*"],
        }},
    }))
    write(config / "settings.json", json.dumps({
        "enabledPlugins": {"source-plugin@example": True},
        "extraKnownMarketplaces": {"example": {"source": {
            "source": "agent-worktrees-repo", "repo": "source-repo",
        }}},
    }))
    module = scanner_module("scan_plugin_sources")
    monkeypatch.setattr(module, "_agent_worktrees_repo_root",
                        lambda *_a: pytest.fail("no catalog command: must not resolve via PATH"))
    inventory = collect_inventory(root, home=home, config_root=config)
    assert {Path(row["path"]).name for row in inventory["instructions"]} >= {
        "AGENTS.md", "copilot-instructions.md",
    }
    assert inventory["skills"][0]["name"] == "sample"
    assert "Synthetic skill description." in inventory["skills"][0]["description"]
    assert inventory["agents"][0]["name"] == "sample-agent"
    assert inventory["mcp_servers"][0]["name"] == "example-server"
    assert inventory["mcp_servers"][0]["tools"] == ["lookup"]
    assert any(w["kind"] == "plugin_source_unavailable" for w in inventory["warnings"])
    assert inventory["coverage"]["hook_execution"] is False


def test_explicit_session_instruction_candidates_and_bounded_reads(tmp_path, monkeypatch):
    home, root, config = tmp_path / "home", tmp_path / "repo", tmp_path / "config"
    root.mkdir()
    session = "11111111-2222-4333-8444-555555555555"
    instruction = config / "session-state" / session / "instructions" / "example" / "guidance.instructions.md"
    write(instruction, "Session-scoped synthetic instructions.")
    inventory = collect_inventory(root, home=home, config_root=config, session_id=session)
    assert any(row["path"] == str(instruction) for row in inventory["instructions"])
    with pytest.raises(ValueError):
        collect_inventory(root, home=home, config_root=config, session_id="../not-a-session")
    import snapshot_inventory

    monkeypatch.setattr(snapshot_inventory, "MAX_FILE_BYTES", 8)
    limited = collect_inventory(root, home=home, config_root=config, session_id=session)
    assert not limited["instructions"]
    assert any(w["kind"] == "inventory_byte_limit" for w in limited["warnings"])


def test_bare_or_missing_catalog_commands_never_fall_back_to_path(tmp_path):
    with pytest.raises(ValueError, match="catalog_command"):
        collect_inventory(tmp_path, agent_worktrees_command="agent-worktrees")
    with pytest.raises(ValueError, match="catalog_command"):
        collect_inventory(tmp_path, agent_worktrees_command=str(tmp_path / "absent-command"))


def test_relative_custom_instruction_paths_use_the_selected_harness(tmp_path, monkeypatch):
    caller, harness = tmp_path / "caller", tmp_path / "harness"
    caller.mkdir()
    write(harness / ".git", "synthetic")
    write(harness / "rules" / "sample.instructions.md", "The selected harness instruction.")
    write(caller / "rules" / "sample.instructions.md", "The wrong caller instruction.")
    monkeypatch.chdir(caller)
    monkeypatch.setenv("COPILOT_CUSTOM_INSTRUCTIONS_DIRS", "rules")
    inventory = collect_inventory(
        harness, home=tmp_path / "home", config_root=tmp_path / "config",
    )
    paths = {row["path"] for row in inventory["instructions"]}
    assert str(harness / "rules" / "sample.instructions.md") in paths
    assert str(caller / "rules" / "sample.instructions.md") not in paths


def test_project_mcp_bare_maps_and_native_precedence(tmp_path):
    root, config = tmp_path / "repo", tmp_path / "config"
    nested = root / "nested"
    write(root / ".git", "synthetic")
    write(config / "mcp-config.json", json.dumps({"mcpServers": {
        "shared": {"tools": ["user"]}, "global-only": {"tools": ["global"]},
    }}))
    write(root / ".github" / "mcp.json", json.dumps({"ignored-root": {}}))
    write(root / ".mcp.json", json.dumps({
        "shared": {"tools": ["root"]}, "root-only": {"tools": ["root"]},
        "enabledPlugins": {"command": "MUST_NOT_RUN", "tools": ["reserved-name"]},
    }))
    write(nested / ".github" / "mcp.json", json.dumps({"ignored-nested": {}}))
    write(nested / ".mcp.json", json.dumps({"mcpServers": {
        "shared": {"tools": ["closest"]}, "nested-only": {},
    }}))
    inventory = collect_inventory(nested, home=tmp_path / "home", config_root=config)
    servers = {row["name"]: row for row in inventory["mcp_servers"]}
    assert set(servers) == {"shared", "root-only", "global-only", "nested-only", "enabledPlugins"}
    assert servers["shared"]["tools"] == ["closest"]
    assert servers["shared"]["source"] == str(nested / ".mcp.json")
    assert inventory["coverage"]["mcp_complete"] is True


def test_plugin_mcp_inline_and_declared_relative_sources(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    write(root / ".git", "synthetic")
    inline, referenced = root / "inline-plugin", root / "referenced-plugin"
    write(inline / "plugin.json", json.dumps({
        "mcpServers": {"inline-server": {"command": "MUST_NOT_RUN", "tools": ["lookup"]}},
    }))
    write(inline / ".mcp.json", json.dumps({"default-must-not-load": {}}))
    write(referenced / ".claude-plugin" / "plugin.json", json.dumps({
        "mcpServers": ".github/custom-servers.json",
    }))
    write(referenced / ".github" / "custom-servers.json",
          '// synthetic MCP JSONC\n{"referenced-server": {"tools":["read"]}}')
    plugins = [SimpleNamespace(
        payload_root=path, skills_root=path / "skills", plugin_name=path.name,
        origin="example/" + path.name, error="",
    ) for path in (inline, referenced)]
    source_reader = scanner_module("scan_plugin_sources")
    monkeypatch.setattr(source_reader, "assemble_enabled_plugins", lambda *_a, **_k: plugins)
    inventory = collect_inventory(root, home=tmp_path / "home", config_root=tmp_path / "config")
    assert {row["name"] for row in inventory["mcp_servers"]} == {"inline-server", "referenced-server"}
    expected = [
        inline / "plugin.json", referenced / ".claude-plugin" / "plugin.json",
        referenced / ".github" / "custom-servers.json",
    ]
    assert inventory["coverage"]["files_examined"] == 3
    assert inventory["coverage"]["source_bytes_read"] == sum(path.stat().st_size for path in expected)
    assert inventory["coverage"]["mcp_complete"] is True


@pytest.mark.parametrize("limit_kind", ["files", "bytes"])
def test_mcp_configuration_shares_the_instruction_read_budget(tmp_path, monkeypatch, limit_kind):
    import snapshot_inventory

    root, config = tmp_path / "repo", tmp_path / "config"
    write(root / ".git", "synthetic")
    write(root / "AGENTS.md", "rules")
    write(config / "mcp-config.json", json.dumps({"mcpServers": {"must-not-read": {}}}))
    if limit_kind == "files":
        monkeypatch.setattr(snapshot_inventory, "MAX_FILES", 1)
    else:
        monkeypatch.setattr(snapshot_inventory, "MAX_TOTAL_BYTES", 6)
    inventory = collect_inventory(root, home=tmp_path / "home", config_root=config)
    assert inventory["mcp_servers"] == []
    assert not inventory["coverage"]["mcp_complete"]
    assert inventory["coverage"]["source_bytes_read"] == len(b"rules")
    assert any(w["kind"].startswith("mcp_") for w in inventory["warnings"])


def test_mcp_duplicate_json_and_name_limits_are_explicit(tmp_path, monkeypatch):
    import snapshot_mcp_inventory

    root, config = tmp_path / "repo", tmp_path / "config"
    write(root / ".git", "synthetic")
    write(root / ".mcp.json", '{"duplicate":{},"duplicate":{}}')
    inventory = collect_inventory(root, home=tmp_path / "home", config_root=config)
    assert not inventory["coverage"]["mcp_complete"]
    assert any(w["kind"] == "mcp_config_invalid_json" for w in inventory["warnings"])
    write(root / ".mcp.json", '{"long-server":{},"ok":{"tools":["long-tool"]}}')
    monkeypatch.setattr(snapshot_mcp_inventory, "MAX_MCP_NAME_CHARACTERS", 4)
    limited = collect_inventory(root, home=tmp_path / "home", config_root=config)
    assert [row["name"] for row in limited["mcp_servers"]] == ["ok"]
    assert limited["mcp_servers"][0]["tools"] == []
    assert not limited["coverage"]["mcp_complete"]


def test_global_unknown_wrapper_is_not_invented_as_a_bare_server(tmp_path):
    root, config = tmp_path / "repo", tmp_path / "config"
    write(root / ".git", "synthetic")
    write(config / "mcp-config.json", '{"servers":{"example":{"command":"MUST_NOT_RUN"}}}')
    inventory = collect_inventory(root, home=tmp_path / "home", config_root=config)
    assert inventory["mcp_servers"] == []
    assert not inventory["coverage"]["mcp_complete"]
    assert any(w["kind"] == "mcp_global_schema_unrecognized" for w in inventory["warnings"])


@pytest.mark.parametrize("quote", ['"', "'"])
def test_quoted_frontmatter_names_correlate_without_yaml_quotes(tmp_path, monkeypatch, quote):
    root, plugin = tmp_path / "repo", tmp_path / "plugin"
    write(root / ".git", "synthetic")
    write(plugin / "skills" / "sample" / "SKILL.md",
          f"---\nname: {quote}sample{quote}\ndescription: Synthetic skill.\n---\nBody\n")
    write(plugin / "agents" / "sample.agent.md",
          f"---\nname: {quote}sample-agent{quote}\ndescription: Synthetic agent.\n---\nBody\n")
    source = SimpleNamespace(
        payload_root=plugin, skills_root=plugin / "skills", plugin_name="example",
        origin="market/example", error="",
    )
    monkeypatch.setattr(scanner_module("scan_plugin_sources"), "assemble_enabled_plugins",
                        lambda *_a, **_k: [source])
    inventory = collect_inventory(root, home=tmp_path / "home", config_root=tmp_path / "config")
    assert inventory["skills"][0]["aliases"] == ["example:sample", "sample"]
    assert inventory["agents"][0]["aliases"] == ["example:sample-agent", "sample-agent"]


def test_missing_names_warn_for_skills_and_use_the_agent_filename(tmp_path):
    root = tmp_path / "repo"
    write(root / ".git", "synthetic")
    write(root / ".github" / "skills" / "unnamed" / "SKILL.md",
          "---\ndescription: Synthetic unnamed skill.\n---\nBody\n")
    write(root / ".github" / "agents" / "fallback.agent.md",
          "---\ndescription: Synthetic unnamed agent.\n---\nBody\n")
    inventory = collect_inventory(root, home=tmp_path / "home", config_root=tmp_path / "config")
    assert inventory["skills"] == []
    assert inventory["agents"][0]["name"] == "fallback"
    assert any(w["kind"] == "metadata_name_missing" for w in inventory["warnings"])


@pytest.mark.parametrize("oversized", [None, "catalog", "manifest"])
def test_discovery_reads_share_cached_inventory_budget(tmp_path, monkeypatch, oversized):
    import snapshot_inventory

    root, config = tmp_path / "repo", tmp_path / "config"
    catalog = root / "market" / "marketplace.json"
    manifest = root / "market" / "example" / "plugin.json"
    settings = root / ".github" / "copilot" / "settings.json"
    write(root / ".git", "synthetic")
    write(settings, json.dumps({
        "enabledPlugins": {"example@market": True},
        "extraKnownMarketplaces": {"market": {"source": {
            "source": "directory", "path": "market",
        }}},
    }))
    write(catalog, json.dumps({
        "name": "market", "plugins": [{"name": "example", "source": "example"}],
    }))
    write(manifest, json.dumps({
        "name": "example", "mcpServers": {"synthetic": {"tools": ["lookup"]}},
    }))
    if oversized:
        target = catalog if oversized == "catalog" else manifest
        write(target, target.read_text(encoding="utf-8") + " " * 2048)
    monkeypatch.setattr(snapshot_inventory, "MAX_FILE_BYTES", 1024)
    if oversized is None:
        monkeypatch.setattr(snapshot_inventory, "MAX_FILES", 3)
    original = Path.read_text

    def forbid_unbounded_discovery(path, *args, **kwargs):
        if path in {catalog, manifest}:
            pytest.fail("discovery must use the shared bounded reader")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", forbid_unbounded_discovery)
    inventory = collect_inventory(root, home=tmp_path / "home", config_root=config)
    expected = [settings]
    if oversized != "catalog":
        expected.append(catalog)
    if oversized is None:
        expected.append(manifest)
        assert inventory["mcp_servers"][0]["name"] == "synthetic"
        assert inventory["coverage"]["mcp_complete"]
        assert inventory["coverage"]["files_examined"] == 3
    else:
        assert not inventory["mcp_servers"]
        assert not inventory["coverage"]["mcp_complete"]
        assert any(w["kind"] == "inventory_byte_limit" for w in inventory["warnings"])
    assert inventory["coverage"]["source_bytes_read"] == sum(path.stat().st_size for path in expected)
