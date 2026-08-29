"""Suite-wide contract tests for session-context contributor declarations."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
PLUGINS = ROOT / "plugins"
SCHEMA = "copilot-extensions.session-context-contributors"
EXPECTED = {
    "agent-bridge": {"command-catalog"},
    "agent-codespaces": {
        "runtime-readiness",
        "command-catalog",
        "codespace-map",
    },
    "agent-containers": {"command-catalog"},
    "agent-dispatch": {"focus-guidance", "command-catalog"},
    "agent-index": {"command-catalog", "scope-binding"},
    "agent-logger": {"command-catalog"},
    "agent-machines": {"command-catalog"},
    "agent-mcp": {"command-catalog"},
    "agent-ssh": {"command-catalog", "mesh-pointer"},
    "agent-vault": {"command-catalog"},
    "agent-worktrees": {"aggregate-context", "command-catalog"},
    "ai-attribution": {"publication-policy"},
    "context-handoff": {"continuity-guidance"},
    "copilot-extensions-harness": {"contribution-boundary"},
    "delegation-guidance": {"delegation-guidance"},
    "zz-context-injection": set(),
}
def _json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _session_start_entries(plugin: Path, manifest: dict[str, object]) -> list[object]:
    configured = manifest.get("hooks", "hooks.json")
    hook_paths = [configured] if isinstance(configured, str) else configured
    entries: list[object] = []
    for relative in hook_paths if isinstance(hook_paths, list) else []:
        if not isinstance(relative, str):
            continue
        path = plugin / relative
        if not path.is_file():
            continue
        hooks = _json(path).get("hooks")
        if isinstance(hooks, dict):
            value = hooks.get("sessionStart", hooks.get("SessionStart", []))
            if isinstance(value, list):
                entries.extend(value)
    return entries


def test_every_session_start_plugin_has_a_complete_declaration() -> None:
    discovered: set[str] = set()
    for plugin in sorted(path for path in PLUGINS.iterdir() if path.is_dir()):
        manifest_path = plugin / "plugin.json"
        if not manifest_path.is_file():
            continue
        manifest = _json(manifest_path)
        if not _session_start_entries(plugin, manifest):
            continue
        discovered.add(plugin.name)
        assert manifest.get("sessionContext") == "session-context.json"

        declaration = _json(plugin / "session-context.json")
        assert declaration["schema"] == SCHEMA
        assert declaration["version"] == 1
        assert declaration["complete"] is True
        contributors = declaration["contributors"]
        assert isinstance(contributors, list)
        assert {
            contributor["id"]
            for contributor in contributors
            if isinstance(contributor, dict)
        } == EXPECTED[plugin.name]

    assert discovered == set(EXPECTED)


def test_contributor_commands_are_bounded_payload_scripts() -> None:
    for plugin_name, expected_ids in EXPECTED.items():
        plugin = PLUGINS / plugin_name
        declaration = _json(plugin / "session-context.json")
        contributors = declaration["contributors"]
        assert isinstance(contributors, list)
        assert len(contributors) == len(expected_ids)

        seen: set[str] = set()
        for contributor in contributors:
            assert isinstance(contributor, dict)
            contributor_id = contributor["id"]
            assert isinstance(contributor_id, str)
            assert contributor_id not in seen
            seen.add(contributor_id)
            assert contributor["pure"] is True
            assert type(contributor["order"]) is int
            assert type(contributor["timeoutSeconds"]) is int
            assert 1 <= contributor["timeoutSeconds"] <= 10
            assert type(contributor["maxBytes"]) is int
            assert 1 <= contributor["maxBytes"] <= 64 * 1024

            for platform, suffix in (("bash", ".sh"), ("powershell", ".ps1")):
                command = contributor[platform]
                assert isinstance(command, list) and command
                assert all(isinstance(part, str) and part for part in command)
                relative = Path(command[0])
                assert not relative.is_absolute()
                assert relative.suffix == suffix
                resolved = (plugin / relative).resolve()
                assert resolved.is_relative_to(plugin.resolve())
                assert resolved.is_file()


def test_mixed_hooks_expose_only_read_only_companion_modes() -> None:
    worktrees = _json(PLUGINS / "agent-worktrees" / "session-context.json")
    commands = {
        contributor["id"]: contributor
        for contributor in worktrees["contributors"]
    }
    aggregate = commands["aggregate-context"]
    assert aggregate["bash"] == ["scripts/emit-session-context.sh"]
    assert aggregate["powershell"] == ["scripts/emit-session-context.ps1"]

    handoff = _json(PLUGINS / "context-handoff" / "session-context.json")
    contributor = handoff["contributors"][0]
    assert contributor["bash"][1:] == ["--aggregate"]
    assert contributor["powershell"][1:] == ["--aggregate"]
