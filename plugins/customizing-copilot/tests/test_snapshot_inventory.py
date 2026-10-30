from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).parents[1] / "skills" / "capturing-client-requests" / "scripts"
sys.path.insert(0, str(SCRIPTS))
from snapshot_inventory import collect_inventory, scanner_module

pytestmark = pytest.mark.guard


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
