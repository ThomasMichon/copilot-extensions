"""Suite guards for output-free session-start declarations."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
PLUGINS = ROOT / "plugins"
SCHEMA = "copilot-extensions.session-context-contributors"
NO_SESSION_HOOK = {
    "copilot-extensions-harness",
    "delegation-guidance",
}
RETIRED_AGGREGATE_FILES = {
    "scripts/invoke-context-contributor.sh",
    "scripts/invoke-context-contributor.ps1",
    "scripts/resolve_context_authority.py",
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


def _marketplace_plugin_names() -> list[str]:
    marketplace = _json(ROOT / ".github" / "plugin" / "marketplace.json")
    return [
        entry["name"]
        for entry in marketplace["plugins"]
        if isinstance(entry, dict) and isinstance(entry.get("name"), str)
    ]


def test_every_output_free_declaration_is_complete() -> None:
    for plugin_name in _marketplace_plugin_names():
        plugin = PLUGINS / plugin_name
        manifest = _json(plugin / "plugin.json")
        configured = manifest.get("sessionContext")
        if not isinstance(configured, str):
            continue

        declaration = _json(plugin / configured)
        assert declaration == {
            "schema": SCHEMA,
            "version": 1,
            "complete": True,
            "contributors": [],
            "sessionStart": {
                "sideEffects": "restart-safe-idempotent",
                "context": "none",
            },
        }


def test_static_projection_plugins_register_no_session_start_hook() -> None:
    for plugin_name in NO_SESSION_HOOK:
        plugin = PLUGINS / plugin_name
        manifest = _json(plugin / "plugin.json")
        assert "hooks" not in manifest
        assert "sessionContext" not in manifest
        assert not (plugin / "hooks.json").exists()
        assert not (plugin / "session-context.json").exists()
        assert (plugin / "instruction-projections.json").is_file()


def test_owned_plugins_ship_no_custom_authority_adapters() -> None:
    for plugin_name in _marketplace_plugin_names():
        plugin = PLUGINS / plugin_name
        for relative in RETIRED_AGGREGATE_FILES:
            assert not (plugin / relative).exists()


def test_output_free_hooks_do_not_invoke_context_producers() -> None:
    for plugin_name in _marketplace_plugin_names():
        plugin = PLUGINS / plugin_name
        manifest = _json(plugin / "plugin.json")
        if not isinstance(manifest.get("sessionContext"), str):
            continue
        commands = "\n".join(
            str(entry.get(platform, ""))
            for entry in _session_start_entries(plugin, manifest)
            if isinstance(entry, dict)
            for platform in ("bash", "powershell")
        )
        assert "invoke-context-contributor" not in commands
        assert "--aggregate" not in commands


def test_dynamic_pointer_projections_have_exact_session_writers() -> None:
    for plugin_name in _marketplace_plugin_names():
        plugin = PLUGINS / plugin_name
        projections_path = plugin / "instruction-projections.json"
        if not projections_path.is_file():
            continue
        projections = _json(projections_path).get("projections", [])
        dynamic = False
        for projection in projections if isinstance(projections, list) else []:
            if not isinstance(projection, dict):
                continue
            template = projection.get("template")
            if not isinstance(template, str):
                continue
            template_path = plugin / template
            if not template_path.is_file():
                continue
            body = template_path.read_text(encoding="utf-8")
            if "session-state folder" in body and "instructions/" in body:
                dynamic = True
        if not dynamic:
            continue
        manifest = _json(plugin / "plugin.json")
        commands = "\n".join(
            str(entry.get(platform, ""))
            for entry in _session_start_entries(plugin, manifest)
            if isinstance(entry, dict)
            for platform in ("bash", "powershell")
        )
        assert (
            "write-session-guidance" in commands
            or "hook_client.py" in commands
        ), plugin_name
        assert isinstance(manifest.get("sessionContext"), str), plugin_name
