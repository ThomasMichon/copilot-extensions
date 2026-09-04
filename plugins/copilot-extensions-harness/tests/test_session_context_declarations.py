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
    "context-injection": set(),
}
CONTEXT_ONLY = {
    "ai-attribution",
    "context-handoff",
    "copilot-extensions-harness",
    "delegation-guidance",
}
MIXED = set(EXPECTED) - CONTEXT_ONLY - {"context-injection"}
def _json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_repository_adoption_is_plugin_owned_configuration() -> None:
    settings = _json(ROOT / ".github" / "copilot" / "settings.json")
    assert settings["enabledPlugins"]["context-injection@copilot-extensions"] is True
    assert "sessionContextAggregation" not in settings
    assert (
        ROOT / ".context-injection" / "config.yaml"
    ).read_text(encoding="utf-8") == (
        "schema: copilot-extensions.context-injection\n"
        "version: 1\n"
        "authority: context-injection@copilot-extensions\n"
        "engine:\n"
        "  schema: copilot-extensions.context-injection-engine\n"
        "  version: 5\n"
    )


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
        behavior = declaration["sessionStart"]
        if plugin.name == "context-injection":
            assert behavior == {
                "sideEffects": "none",
                "context": "aggregate-authority",
            }
        else:
            assert behavior == {
                "sideEffects": (
                    "none"
                    if plugin.name in CONTEXT_ONLY
                    else "restart-safe-idempotent"
                ),
                "context": "authority-aware",
            }

    assert discovered == set(EXPECTED)
    assert len(MIXED) == 11
    assert len(CONTEXT_ONLY) == 4


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


def test_every_contributor_hook_uses_the_engine_v2_wrapper() -> None:
    for plugin_name, expected_ids in EXPECTED.items():
        if plugin_name == "context-injection":
            continue
        plugin = PLUGINS / plugin_name
        manifest = _json(plugin / "plugin.json")
        entries = _session_start_entries(plugin, manifest)
        declaration = _json(plugin / "session-context.json")
        contributors = {
            contributor["id"]: contributor
            for contributor in declaration["contributors"]
        }
        for contributor_id in expected_ids:
            source = f"{plugin_name}@copilot-extensions"
            matches = [
                entry
                for entry in entries
                if isinstance(entry, dict)
                and source in str(entry.get("bash", ""))
                and source in str(entry.get("powershell", ""))
                and contributor_id in str(entry.get("bash", ""))
                and contributor_id in str(entry.get("powershell", ""))
            ]
            assert len(matches) == 1
            entry = matches[0]
            assert entry["timeoutSec"] == 30
            assert "invoke-context-contributor.sh" in entry["bash"]
            assert "invoke-context-contributor.ps1" in entry["powershell"]

            for platform in ("bash", "powershell"):
                relative = contributors[contributor_id][platform][0]
                legacy = [
                    candidate
                    for candidate in entries
                    if isinstance(candidate, dict)
                    and relative in str(candidate.get(platform, ""))
                    and "invoke-context-contributor" not in str(
                        candidate.get(platform, "")
                    )
                ]
                assert legacy == []


def test_producer_wrappers_are_byte_identical_to_the_authority_copy() -> None:
    authority = PLUGINS / "context-injection" / "scripts"
    for plugin_name, contributors in EXPECTED.items():
        if not contributors:
            continue
        scripts = PLUGINS / plugin_name / "scripts"
        for filename in (
            "invoke-context-contributor.sh",
            "invoke-context-contributor.ps1",
        ):
            assert (scripts / filename).read_bytes() == (
                authority / filename
            ).read_bytes()


def test_agent_worktrees_lifecycle_side_effects_use_one_bounded_client() -> None:
    plugin = PLUGINS / "agent-worktrees"
    manifest = _json(plugin / "plugin.json")
    entries = _session_start_entries(plugin, manifest)
    commands = "\n".join(
        str(entry.get(platform, ""))
        for entry in entries
        if isinstance(entry, dict)
        for platform in ("bash", "powershell")
    )
    lifecycle_entries = [
        entry
        for entry in entries
        if isinstance(entry, dict)
        and all(
            "hook_client.py" in str(entry.get(platform, ""))
            for platform in ("bash", "powershell")
        )
    ]
    assert len(lifecycle_entries) == 1
    assert all(
        "sessionStart" in str(lifecycle_entries[0].get(platform, ""))
        for platform in ("bash", "powershell")
    )
    for stem in ("register-session", "register-nudge", "marketplace-overrides"):
        assert not [
            line
            for line in commands.splitlines()
            if stem in line and "invoke-context-contributor" not in line
        ]
    assert "session-conduct" not in commands
    assert "session-machine" not in commands
