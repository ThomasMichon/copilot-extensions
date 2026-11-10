"""Roster-wide bootstrap and session-command-glossary coverage."""
from __future__ import annotations
from typing import Any, cast

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
def _runtime_agent_plugins() -> list[tuple[str, Path, dict[str, Any]]]:
    marketplace = json.loads(
        (REPO / ".github" / "plugin" / "marketplace.json").read_text(
            encoding="utf-8"
        )
    )
    plugins: list[tuple[str, Path, dict[str, Any]]] = []
    for entry in marketplace["plugins"]:
        name = entry["name"]
        plugin = REPO / entry["source"]
        manifest = plugin / "payload-invocation.json"
        if (
            name.startswith("agent-")
            and (manifest.exists() or manifest.is_symlink())
        ):
            plugins.append(
                (
                    name,
                    plugin,
                    generator.load_manifest(manifest),
                )
            )
    return plugins


def test_runtime_agent_plugins_bootstrap_and_write_their_command_glossary() -> None:
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
        manifest = plugin / "payload-invocation.json"
        if not manifest.exists() and not manifest.is_symlink():
            continue
        try:
            generated = generator.expected_files(manifest)
            canonical_errors = generator.source_check(manifest)
            if canonical_errors:
                failures.extend(f"{name}: {error}" for error in canonical_errors)
                continue
            data = generator.load_manifest(manifest)
        except (OSError, ValueError) as error:
            failures.append(f"{name}: invalid payload manifest: {error}")
            continue

        # Promotion owns materialized equality; Dev validates canonical plans.
        installer = str(data["installer"])
        for suffix in ("sh", "ps1"):
            catalog = plugin / "scripts" / f"emit-command-catalog.{suffix}"
            if catalog not in generated:
                failures.append(
                    f"{name}: missing planned generated catalog "
                    f"{catalog.relative_to(REPO).as_posix()}"
                )
            required_scripts = [
                plugin / "scripts" / f"{installer}.{suffix}",
                plugin / "scripts" / f"bootstrap-check.{suffix}",
            ]
            if name == "agent-pull-requests":
                required_scripts.append(
                    plugin / "scripts" / f"bootstrap-check.admission.{suffix}"
                )
            if name != "agent-worktrees":
                required_scripts.append(
                    plugin / "scripts" / f"write-session-guidance.{suffix}"
                )
            for required in required_scripts:
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
        required_hook_names = [
            "hook_client.py" if name == "agent-worktrees" else "write-session-guidance"
        ]
        if data["sessionStartBootstrap"]:
            required_hook_names.insert(
                0, "bootstrap-check.admission" if name == "agent-pull-requests" else "bootstrap-check",
            )
        for shell_field, required_hooks in (
            (
                "bash",
                tuple(
                    name if name.endswith(".py") else f"{name}.sh"
                    for name in required_hook_names
                ),
            ),
            (
                "powershell",
                tuple(
                    name if name.endswith(".py") else f"{name}.ps1"
                    for name in required_hook_names
                ),
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


@pytest.fixture
def canonical_agent_roster(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, dict[Path, str | bytes]]:
    plugin = tmp_path / "plugins" / "agent-example"
    scripts = plugin / "scripts"
    scripts.mkdir(parents=True)
    marketplace = tmp_path / ".github" / "plugin" / "marketplace.json"
    marketplace.parent.mkdir(parents=True)
    marketplace.write_text(json.dumps({
        "plugins": [{"name": "agent-example", "source": "plugins/agent-example"}],
    }), encoding="utf-8")
    (plugin / "pyproject.toml").write_text("", encoding="utf-8")
    manifest = plugin / "payload-invocation.json"
    manifest.write_text(json.dumps({
        "schema": "copilot-extensions.payload-invocation", "version": 1,
        "command": "agent-example", "module": "agent_example",
        "runtimeRoot": ".agent-example",
        "noSelfProvisionEnv": "AGENT_EXAMPLE_NO_SELFPROVISION",
        "purpose": "Exercise an example runtime",
    }), encoding="utf-8")
    for stem in ("install", "bootstrap-check", "write-session-guidance"):
        for suffix in ("sh", "ps1"):
            (scripts / f"{stem}.{suffix}").write_text("", encoding="utf-8")
    (plugin / "plugin.json").write_text(
        json.dumps({"hooks": "hooks.json"}), encoding="utf-8",
    )
    (plugin / "hooks.json").write_text(json.dumps({
        "hooks": {"sessionStart": [
            {"type": "command",
             "bash": f"$COPILOT_PLUGIN_ROOT/scripts/{stem}.sh",
             "powershell": f"$COPILOT_PLUGIN_ROOT/scripts/{stem}.ps1"}
            for stem in ("bootstrap-check", "write-session-guidance")
        ]},
    }), encoding="utf-8")
    outputs: dict[Path, str | bytes] = {
        scripts / "emit-command-catalog.sh": "#!/bin/sh\n",
        scripts / "emit-command-catalog.ps1": "# canonical catalog\n",
        scripts / "canonical.py": "VALUE = 1\n",
        scripts / "toolkit.pyz": b"\xff\x00canonical-binary",
    }

    def expected_files(
        path: Path, *, canonical_root: Path | None = None,
    ) -> dict[Path, str | bytes]:
        assert path == manifest
        return outputs

    monkeypatch.setitem(globals(), "REPO", tmp_path)
    monkeypatch.setattr(generator, "expected_files", expected_files)
    return plugin, outputs


@pytest.mark.parametrize("stale", [False, True])
def test_runtime_agent_roster_uses_plans_without_reading_generated_mirrors(
    canonical_agent_roster: tuple[Path, dict[Path, str | bytes]],
    monkeypatch: pytest.MonkeyPatch, stale: bool,
) -> None:
    _plugin, outputs = canonical_agent_roster
    if stale:
        for path in outputs:
            path.write_bytes(b"\xffstale")
    read_text = Path.read_text

    def read_authored(
        path: Path, encoding: str | None = None, errors: str | None = None,
    ) -> str:
        assert path not in outputs, "generated mirrors are not canonical inputs"
        return read_text(path, encoding=encoding, errors=errors)

    monkeypatch.setattr(Path, "read_text", read_authored)
    test_runtime_agent_plugins_bootstrap_and_write_their_command_glossary()


@pytest.mark.parametrize("bad_plan", ["python", "escape", "catalog"])
def test_runtime_agent_roster_refuses_invalid_canonical_plans(
    canonical_agent_roster: tuple[Path, dict[Path, str | bytes]],
    bad_plan: str,
) -> None:
    plugin, outputs = canonical_agent_roster
    if bad_plan == "python":
        outputs[plugin / "scripts" / "canonical.py"] = "def broken(\n"
        diagnostic = "invalid canonical generated Python"
    elif bad_plan == "escape":
        outputs[plugin.parent / "escape.py"] = "VALUE = 1\n"
        diagnostic = "generated output escapes its payload"
    else:
        del outputs[plugin / "scripts" / "emit-command-catalog.ps1"]
        diagnostic = "missing planned generated catalog"
    with pytest.raises(AssertionError, match=diagnostic):
        test_runtime_agent_plugins_bootstrap_and_write_their_command_glossary()


@pytest.mark.parametrize("missing", [
    "scripts/install.ps1", "scripts/bootstrap-check.sh",
    "scripts/write-session-guidance.ps1", "hooks.json",
])
def test_runtime_agent_roster_preserves_authored_source_requirements(
    canonical_agent_roster: tuple[Path, dict[Path, str | bytes]],
    missing: str,
) -> None:
    plugin, _outputs = canonical_agent_roster
    (plugin / missing).unlink()
    with pytest.raises(AssertionError, match="missing"):
        test_runtime_agent_plugins_bootstrap_and_write_their_command_glossary()


@pytest.mark.parametrize("python_project", [False, True])
def test_runtime_agent_roster_allows_undeclared_applications(
    canonical_agent_roster: tuple[Path, dict[Path, str | bytes]],
    python_project: bool,
) -> None:
    plugin, _outputs = canonical_agent_roster
    (plugin / "payload-invocation.json").unlink()
    (plugin / "plugin.json").write_text(
        json.dumps({"name": "agent-example"}), encoding="utf-8",
    )
    (plugin / "hooks.json").unlink()
    if not python_project:
        (plugin / "pyproject.toml").unlink()
    assert _runtime_agent_plugins() == []
    test_runtime_agent_plugins_bootstrap_and_write_their_command_glossary()


@pytest.mark.parametrize("bad_plan", [False, True])
def test_runtime_agent_roster_validates_declared_thin_plugins(
    canonical_agent_roster: tuple[Path, dict[Path, str | bytes]],
    bad_plan: bool,
) -> None:
    plugin, outputs = canonical_agent_roster
    (plugin / "pyproject.toml").unlink()
    assert [(name, path) for name, path, _data in _runtime_agent_plugins()] == [
        ("agent-example", plugin),
    ]
    if bad_plan:
        outputs[plugin / "scripts" / "canonical.py"] = "def broken(\n"
        with pytest.raises(AssertionError, match="invalid canonical generated Python"):
            test_runtime_agent_plugins_bootstrap_and_write_their_command_glossary()
    else:
        test_runtime_agent_plugins_bootstrap_and_write_their_command_glossary()


@pytest.mark.parametrize("declaration", ["json", "schema", "directory", "dangling"])
def test_runtime_agent_roster_refuses_invalid_present_declarations(
    canonical_agent_roster: tuple[Path, dict[Path, str | bytes]],
    monkeypatch: pytest.MonkeyPatch, declaration: str,
) -> None:
    plugin, _outputs = canonical_agent_roster
    (plugin / "pyproject.toml").unlink()
    manifest = plugin / "payload-invocation.json"
    if declaration == "json":
        manifest.write_text("{", encoding="utf-8")
    elif declaration == "schema":
        manifest.write_text(json.dumps({"schema": "invalid"}), encoding="utf-8")
    else:
        manifest.unlink()
        if declaration == "directory":
            manifest.mkdir()
        else:
            is_symlink = Path.is_symlink

            def declared_link(path: Path) -> bool:
                return path == manifest or is_symlink(path)

            monkeypatch.setattr(Path, "is_symlink", declared_link)
    with pytest.raises((OSError, ValueError)):
        _runtime_agent_plugins()
    with pytest.raises(AssertionError, match="invalid payload manifest"):
        test_runtime_agent_plugins_bootstrap_and_write_their_command_glossary()


def test_runtime_agent_roster_declares_output_free_session_start() -> None:
    names = set()
    failures: list[str] = []
    for name, plugin, _manifest in _runtime_agent_plugins():
        names.add(name)
        plugin_manifest = json.loads(
            (plugin / "plugin.json").read_text(encoding="utf-8")
        )
        declaration_rel = plugin_manifest.get("sessionContext")
        if not isinstance(declaration_rel, str) or not declaration_rel:
            failures.append(f"{name}: plugin.json does not declare sessionContext")
            continue
        declaration_path = plugin / declaration_rel
        try:
            declaration = json.loads(declaration_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            failures.append(f"{name}: invalid {declaration_rel}")
            continue
        if declaration.get("contributors") != []:
            failures.append(f"{name}: session context still declares contributors")
        session_start = declaration.get("sessionStart")
        if not isinstance(session_start, dict):
            failures.append(f"{name}: missing sessionStart classification")
            continue
        if session_start.get("context") != "none":
            failures.append(f"{name}: sessionStart is not output-free")
        for retired in (
            "scripts/invoke-context-contributor.sh",
            "scripts/invoke-context-contributor.ps1",
            "scripts/resolve_context_authority.py",
        ):
            if (plugin / retired).exists():
                failures.append(f"{name}: retained {retired}")

    assert {"agent-index", "agent-logger"} <= names
    assert not failures, "\n" + "\n".join(failures)


@pytest.mark.parametrize(
    ("name", "plugin", "manifest"),
    _runtime_agent_plugins(),
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_default_legacy_stamp_publishes_every_payload_command(
    name: str,
    plugin: Path,
    manifest: dict[str, Any],
    tmp_path: Path,
) -> None:
    """Absent namespaced policy preserves the complete legacy PATH fallback."""
    if os.name != "nt":
        profile_home = Path(cast(Any, pwd).getpwuid(cast(Any, os).getuid()).pw_dir)
        if (profile_home / ".copilot-extensions" / "installation-mode.json").exists():
            pytest.skip("OS-profile installation-mode policy is present")
    installer = str(manifest["installer"])
    home = tmp_path / name
    home.mkdir(mode=0o700)
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "LOCALAPPDATA": str(home / "AppData" / "Local"),
            "COPILOT_PLUGIN_INSTALL_STAGED": "1",
            "AGENT_BRIDGE_CONFIG_DIR": str(home / ".agent-bridge"),
        }
    )
    env.pop("AGENT_BRIDGE_PROVIDERS_DIR", None)
    for key in tuple(env):
        if key.startswith(
            (
                "COPILOT_EXT_INSTALLATION_",
                "COPILOT_EXTENSIONS_INSTALLATION_",
            )
        ) or key in {
            "COPILOT_PLUGIN_ROOT",
            "PLUGIN_ROOT",
            "CLAUDE_PLUGIN_ROOT",
            "COPILOT_PLUGIN_DATA",
            "PLUGIN_DATA",
            "CLAUDE_PLUGIN_DATA",
        }:
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
