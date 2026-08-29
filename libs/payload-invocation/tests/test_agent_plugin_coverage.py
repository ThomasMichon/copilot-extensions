"""Roster-wide bootstrap and session-command-glossary coverage."""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

if os.name != "nt":
    import pwd

SCRIPT = Path(__file__).resolve().parents[1] / "generate.py"
REPO = SCRIPT.parents[2]
_spec = importlib.util.spec_from_file_location(
    "payload_invocation_coverage_generate", SCRIPT
)
if _spec is None or _spec.loader is None:
    raise RuntimeError("cannot load payload-invocation generator")
generator = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(generator)


def _runtime_agent_plugins() -> list[tuple[str, Path, dict]]:
    marketplace = json.loads(
        (REPO / ".github" / "plugin" / "marketplace.json").read_text(
            encoding="utf-8"
        )
    )
    plugins: list[tuple[str, Path, dict]] = []
    for entry in marketplace["plugins"]:
        name = entry["name"]
        plugin = REPO / entry["source"]
        if (
            name.startswith("agent-")
            and (plugin / "pyproject.toml").is_file()
            and (plugin / "payload-invocation.json").is_file()
        ):
            plugins.append(
                (
                    name,
                    plugin,
                    generator.load_manifest(plugin / "payload-invocation.json"),
                )
            )
    return plugins


def test_runtime_catalog_discovery_covers_harness_inventory() -> None:
    discovered = {name for name, _plugin, _manifest in _runtime_agent_plugins()}
    required = {
        "agent-worktrees",
        "agent-machines",
        "agent-codespaces",
        "agent-dispatch",
        "agent-mcp",
        "agent-index",
        "agent-bridge",
        "agent-containers",
        "agent-vault",
        "agent-logger",
        "agent-ssh",
    }
    assert required <= discovered


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


@pytest.mark.parametrize(
    ("name", "plugin", "manifest"),
    _runtime_agent_plugins(),
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_default_legacy_stamp_publishes_every_payload_command(
    name: str,
    plugin: Path,
    manifest: dict,
    tmp_path: Path,
) -> None:
    """Absent namespaced policy preserves the complete legacy PATH fallback."""
    if os.name != "nt":
        profile_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
        if (profile_home / ".copilot-extensions" / "installation-mode.json").exists():
            pytest.skip("OS-profile installation-mode policy is present")
    installer = str(manifest["installer"])
    home = tmp_path / name
    home.mkdir()
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "LOCALAPPDATA": str(home / "AppData" / "Local"),
            "COPILOT_PLUGIN_INSTALL_STAGED": "1",
        }
    )
    for key in tuple(env):
        if key.startswith(
            (
                "COPILOT_EXT_INSTALLATION_",
                "COPILOT_EXTENSIONS_INSTALLATION_",
            )
        ):
            env.pop(key, None)
    if os.name == "nt":
        pwsh = shutil.which("pwsh") or shutil.which("powershell")
        if not pwsh:
            pytest.skip("PowerShell is unavailable")
        command = [
            pwsh,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(plugin / "scripts" / f"{installer}.ps1"),
            "stamp",
        ]
    else:
        command = [
            "bash",
            str(plugin / "scripts" / f"{installer}.sh"),
            "stamp",
        ]
    result = subprocess.run(
        command,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        cwd=home,
    )
    assert result.returncode == 0, (
        f"{name} stamp failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    local_bin = home / ".local" / "bin"
    failures: list[str] = []
    for command_spec in manifest["commands"]:
        command_name = str(command_spec["command"])
        if os.name == "nt":
            candidates = (
                local_bin / f"{command_name}.ps1",
                local_bin / f"{command_name}.cmd",
                local_bin / command_name,
            )
            if not any(path.is_file() for path in candidates):
                failures.append(
                    f"{name}: stamp did not publish a Windows binstub for "
                    f"{command_name}"
                )
        else:
            path = local_bin / command_name
            if not path.is_file() or not os.access(path, os.X_OK):
                failures.append(f"{name}: stamp did not publish {path.name}")
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
