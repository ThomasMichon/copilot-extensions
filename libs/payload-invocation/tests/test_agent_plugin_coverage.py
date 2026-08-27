"""Roster-wide bootstrap and session-command-glossary coverage."""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "generate.py"
REPO = SCRIPT.parents[2]
_spec = importlib.util.spec_from_file_location(
    "payload_invocation_coverage_generate", SCRIPT
)
if _spec is None or _spec.loader is None:
    raise RuntimeError("cannot load payload-invocation generator")
generator = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(generator)


def test_runtime_agent_plugins_bootstrap_and_emit_their_command_glossary() -> None:
    marketplace = json.loads(
        (REPO / ".github" / "plugin" / "marketplace.json").read_text(
            encoding="utf-8"
        )
    )
    failures: list[str] = []
    for entry in marketplace["plugins"]:
        name = entry["name"]
        if not name.startswith("agent-"):
            continue
        plugin = REPO / entry["source"]
        if not (plugin / "pyproject.toml").is_file():
            continue

        manifest = plugin / "payload-invocation.json"
        if not manifest.is_file():
            failures.append(f"{name}: missing payload-invocation.json")
            continue
        try:
            generated = generator.expected_files(manifest)
        except ValueError as error:
            failures.append(f"{name}: invalid payload manifest: {error}")
            continue
        for path, expected in generated.items():
            try:
                actual = path.read_text(encoding="utf-8")
            except OSError:
                actual = ""
            if actual != expected:
                failures.append(
                    f"{name}: missing or stale generated file "
                    f"{path.relative_to(REPO).as_posix()}"
                )

        data = generator.load_manifest(manifest)
        installer = str(data["installer"])
        for suffix in ("sh", "ps1"):
            for required in (
                plugin / "scripts" / f"{installer}.{suffix}",
                plugin / "scripts" / f"bootstrap-check.{suffix}",
                plugin / "scripts" / f"emit-command-catalog.{suffix}",
            ):
                if not required.is_file():
                    failures.append(
                        f"{name}: missing {required.relative_to(REPO).as_posix()}"
                    )

        plugin_manifest = json.loads(
            (plugin / "plugin.json").read_text(encoding="utf-8")
        )
        hooks_rel = plugin_manifest.get("hooks")
        if not isinstance(hooks_rel, str) or not hooks_rel:
            failures.append(f"{name}: plugin.json does not declare hooks")
            continue
        hooks_path = plugin / hooks_rel
        try:
            hooks_data = json.loads(hooks_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            hooks_data = {}
        session_start = hooks_data.get("hooks", {}).get("sessionStart", [])
        command_hooks = [
            hook
            for hook in session_start
            if isinstance(hook, dict) and hook.get("type") == "command"
        ]
        for shell_field, required_hooks in (
            ("bash", ("bootstrap-check.sh", "emit-command-catalog.sh")),
            (
                "powershell",
                ("bootstrap-check.ps1", "emit-command-catalog.ps1"),
            ),
        ):
            for required_hook in required_hooks:
                matching = [
                    str(hook.get(shell_field, ""))
                    for hook in command_hooks
                    if required_hook in str(hook.get(shell_field, ""))
                ]
                if not any(
                    "COPILOT_PLUGIN_ROOT" in command for command in matching
                ):
                    failures.append(
                        f"{name}: {hooks_rel} missing attributable "
                        f"{shell_field} {required_hook} command hook"
                    )

    assert not failures, "\n" + "\n".join(failures)


def test_agent_skills_do_not_hardcode_another_plugins_payload_bin() -> None:
    marketplace = json.loads(
        (REPO / ".github" / "plugin" / "marketplace.json").read_text(
            encoding="utf-8"
        )
    )
    agent_plugins = {
        entry["name"]: REPO / entry["source"]
        for entry in marketplace["plugins"]
        if entry["name"].startswith("agent-")
    }
    direct_bin = re.compile(
        r"(?:^|[\\/])(agent-[a-z0-9-]+)[\\/]bin[\\/]",
        re.IGNORECASE,
    )
    failures: list[str] = []
    for owner, plugin in agent_plugins.items():
        for skill in (plugin / "skills").glob("**/*.md"):
            text = skill.read_text(encoding="utf-8")
            for match in direct_bin.finditer(text):
                referenced = match.group(1).lower()
                if referenced in agent_plugins and referenced != owner:
                    failures.append(
                        f"{skill.relative_to(REPO).as_posix()}: hardcodes "
                        f"{referenced} payload bin path"
                    )
    assert not failures, "\n" + "\n".join(failures)
