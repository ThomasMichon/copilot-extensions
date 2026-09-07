"""Behavior and payload tests for context-handoff ambient guidance."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
MANIFEST = PLUGIN / "plugin.json"
HOOKS = PLUGIN / "hooks.json"
POWERSHELL_PRODUCER = PLUGIN / "scripts" / "emit-guidance.ps1"
BASH_PRODUCER = PLUGIN / "scripts" / "emit-guidance.sh"


def _hook_input() -> str:
    return json.dumps({
        "sessionId": "context-handoff-hook-test",
        "cwd": str(PLUGIN),
        "source": "test",
        "timestamp": 1,
    })


def _powershell() -> str | None:
    if os.name == "nt":
        return shutil.which("pwsh") or shutil.which("powershell.exe")
    return shutil.which("pwsh")


def _bash() -> str | None:
    executable = shutil.which("bash")
    if not executable:
        return None
    try:
        result = subprocess.run(
            [executable, "-lc", "exit 0"],
            capture_output=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return executable if result.returncode == 0 else None


def _bash_path(path: Path) -> str:
    text = str(path)
    if os.name == "nt" and len(text) >= 3 and text[1:3] == ":\\":
        return f"/{text[0].lower()}/{text[3:].replace(chr(92), '/')}"
    return text


def _run(
    command: list[str],
    *,
    plugin_root: Path | None = PLUGIN,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.pop("COPILOT_PLUGIN_ROOT", None)
    if plugin_root is not None:
        environment["COPILOT_PLUGIN_ROOT"] = str(plugin_root)
    return subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )


def _run_bash(
    *args: str,
    plugin_root: Path | None = PLUGIN,
) -> subprocess.CompletedProcess[str]:
    bash = _bash()
    if not bash:
        pytest.skip("a conformant Bash is unavailable")
    return _run([bash, str(BASH_PRODUCER), *args], plugin_root=plugin_root)


def _run_powershell(
    *args: str,
    plugin_root: Path | None = PLUGIN,
) -> subprocess.CompletedProcess[str]:
    powershell = _powershell()
    assert powershell
    return _run(
        [powershell, "-NoProfile", "-File", str(POWERSHELL_PRODUCER), *args],
        plugin_root=plugin_root,
    )


def _context(result: subprocess.CompletedProcess[str]) -> str:
    payload = json.loads(result.stdout)
    assert set(payload) == {"additionalContext"}
    return payload["additionalContext"]


def _catalog(context: str) -> dict[str, object]:
    raw = context.split("```json\n", 1)[1].split("\n```", 1)[0]
    return json.loads(raw)


def _contributor_hook_entry() -> dict[str, object]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    hook_path = (PLUGIN / manifest["hooks"]).resolve()
    assert hook_path.is_relative_to(PLUGIN.resolve())
    assert hook_path == HOOKS.resolve()
    hooks = json.loads(hook_path.read_text(encoding="utf-8"))
    entries = hooks["hooks"]["sessionStart"]
    matching = [
        entry
        for entry in entries
        if "invoke-context-contributor" in str(entry.get("bash", ""))
    ]
    assert len(matching) == 1
    return matching[0]


def test_manifest_registers_cross_platform_session_start_hook() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["hooks"] == "hooks.json"
    entry = _contributor_hook_entry()
    assert entry["type"] == "command"
    assert entry["timeoutSec"] == 30
    for shell in ("powershell", "bash"):
        command = str(entry[shell])
        assert "COPILOT_PLUGIN_ROOT" in command
        assert "PLUGIN_ROOT" in command
        assert "CLAUDE_PLUGIN_ROOT" in command
        assert "invoke-context-contributor." in command
        assert "emit-guidance." in command


def test_bash_emits_owned_bounded_continuity_guidance() -> None:
    version = json.loads(MANIFEST.read_text(encoding="utf-8"))["version"]
    context = _context(_run_bash())
    assert context.startswith(f"[owner: context-handoff@{version}]\n")
    assert "When you own the active objective, it can span multiple agent sessions" in context
    assert "do not narrow investigation, planning, implementation" in context
    assert "compose and store the baton safely" in context
    assert "trigger_handoff" in context
    assert "do not ask first" in context
    assert "ending the turn with proposed follow-ups" in context
    assert "only signals pickup and never performs process management" in context
    assert "Consuming or producing a handoff is setup or progress, never completion" in context
    assert "one slice of the larger effort" in context
    assert "Bounded delegates remain within their assigned scope" in context
    assert "The session owning the objective stops only" in context
    assert "Use the `context-handoff` skill" in context
    kernel = context.split("\n\n## agent-worktrees session command catalog", 1)[0]
    assert len(kernel.encode("utf-8")) < 2048
    assert len(context.encode("utf-8")) < 3072


def test_bash_preserves_adjacent_agent_worktrees_command_catalog() -> None:
    context = _context(_run_bash())
    command = (
        PLUGIN.parent / "agent-worktrees" / "bin" / "payload" / "agent-worktrees"
    ).resolve()
    assert "## agent-worktrees session command catalog" in context
    catalog = _catalog(context)
    assert catalog["payload"] == {"provenance": "adjacent-compatibility"}
    assert catalog["commands"][0]["argv"] == [str(command)]
    assert catalog["commands"][0]["availability"] == "ready"


def test_bash_own_only_mode_excludes_adjacent_catalog() -> None:
    context = _context(_run_bash("--own-only"))
    assert context.startswith("[owner: context-handoff@")
    assert "agent-worktrees session command catalog" not in context
    assert len(context.encode("utf-8")) < 2048

    if _powershell():
        assert _context(_run_powershell("--own-only")) == context


def test_aggregate_mode_is_owned_compact_and_cross_platform() -> None:
    context = _context(_run_bash("--aggregate"))
    assert context.startswith("[owner: context-handoff@")
    assert "handoff is progress, never completion" in context
    assert "trigger_handoff" in context
    assert "Near token pressure" in context
    assert "Only the turn-end follow-up path asks" in context
    assert "one slice" in context
    assert "Use the `context-handoff` skill" in context
    assert len(context.encode("utf-8")) <= 700

    if _powershell():
        assert _context(_run_powershell("--aggregate")) == context


def test_bash_reports_incomplete_adjacent_payload_as_unavailable(
    tmp_path: Path,
) -> None:
    payload = tmp_path / "context-handoff"
    shutil.copytree(PLUGIN, payload)
    agent_worktrees = tmp_path / "agent-worktrees"
    command_dir = agent_worktrees / "bin" / "payload"
    command_dir.mkdir(parents=True)
    (agent_worktrees / "plugin.json").write_text(
        json.dumps({"name": "agent-worktrees"}),
        encoding="utf-8",
    )
    command = command_dir / "agent-worktrees"
    command.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    command.chmod(0o755)

    catalog = _catalog(_context(_run_bash(plugin_root=payload)))
    assert catalog["commands"][0]["availability"] == "unavailable"


def test_bash_standalone_payload_emits_only_own_guidance(tmp_path: Path) -> None:
    payload = tmp_path / "context-handoff"
    shutil.copytree(PLUGIN, payload)
    context = _context(_run_bash(plugin_root=payload))
    assert context.startswith("[owner: context-handoff@")
    assert "agent-worktrees session command catalog" not in context


def test_oversized_adjacent_catalog_falls_back_to_own_guidance(
    tmp_path: Path,
) -> None:
    parent = tmp_path
    for index in range(7):
        parent /= f"{index}-" + ("x" * 190)
        parent.mkdir()
    payload = parent / "context-handoff"
    shutil.copytree(PLUGIN, payload)
    agent_worktrees = parent / "agent-worktrees"
    command_dir = agent_worktrees / "bin" / "payload"
    command_dir.mkdir(parents=True)
    (agent_worktrees / "plugin.json").write_text(
        json.dumps({"name": "agent-worktrees"}),
        encoding="utf-8",
    )
    bash_command = command_dir / "agent-worktrees"
    bash_command.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    bash_command.chmod(0o755)
    (command_dir / "agent-worktrees.ps1").write_text("exit 0\n", encoding="utf-8")
    scripts_dir = agent_worktrees / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "install.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    (scripts_dir / "install.ps1").write_text("exit 0\n", encoding="utf-8")

    bash_context = _context(_run_bash(plugin_root=payload))
    assert bash_context.startswith("[owner: context-handoff@")
    assert "agent-worktrees session command catalog" not in bash_context
    assert len(bash_context.encode("utf-8")) < 2048

    if _powershell():
        assert _context(_run_powershell(plugin_root=payload)) == bash_context


def test_bash_falls_back_to_script_location() -> None:
    assert _context(_run_bash(plugin_root=None)).startswith("[owner: context-handoff@")


def test_hook_commands_use_plugin_root() -> None:
    entry = _contributor_hook_entry()
    bash = _bash()
    if not bash:
        pytest.skip("a conformant Bash is unavailable")
    environment = os.environ.copy()
    environment["COPILOT_PLUGIN_ROOT"] = _bash_path(PLUGIN)

    result = subprocess.run(
        [bash, "-c", str(entry["bash"])],
        input=_hook_input(),
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert _context(result) == _context(_run_bash("--aggregate"))

    powershell = _powershell()
    if powershell:
        powershell_environment = os.environ.copy()
        powershell_environment["COPILOT_PLUGIN_ROOT"] = str(PLUGIN)
        result = subprocess.run(
            [powershell, "-NoProfile", "-Command", str(entry["powershell"])],
            input=_hook_input(),
            check=True,
            capture_output=True,
            text=True,
            env=powershell_environment,
        )
        assert _context(result) == _context(_run_powershell("--aggregate"))


def test_hook_commands_accept_compatibility_root_aliases() -> None:
    entry = _contributor_hook_entry()
    bash = _bash()
    if not bash:
        pytest.skip("a conformant Bash is unavailable")
    for alias in ("PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT"):
        environment = os.environ.copy()
        for name in ("COPILOT_PLUGIN_ROOT", "PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT"):
            environment.pop(name, None)
        environment[alias] = _bash_path(PLUGIN)

        result = subprocess.run(
            [bash, "-c", str(entry["bash"])],
            input=_hook_input(),
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
        assert _context(result).startswith("[owner: context-handoff@")


@pytest.mark.skipif(_powershell() is None, reason="PowerShell is not installed")
def test_powershell_matches_bash_guidance() -> None:
    bash_context = _context(_run_bash())
    powershell_context = _context(_run_powershell())
    separator = "\n\n## agent-worktrees session command catalog"
    assert bash_context.split(separator, 1)[0] == powershell_context.split(separator, 1)[0]
    assert separator in bash_context
    assert separator in powershell_context
    for context in (bash_context, powershell_context):
        assert _catalog(context)["payload"] == {
            "provenance": "adjacent-compatibility"
        }
    powershell_command = _catalog(powershell_context)["commands"][0]["argv"][0]
    assert powershell_command.replace("\\", "/").endswith(
        "/bin/payload/agent-worktrees.ps1"
    )


@pytest.mark.parametrize("failure", ["missing-skill", "invalid-version"])
def test_producers_fail_open_for_incomplete_payload(
    tmp_path: Path,
    failure: str,
) -> None:
    payload = tmp_path / "context-handoff"
    shutil.copytree(PLUGIN, payload)
    if failure == "missing-skill":
        (payload / "skills" / "context-handoff" / "SKILL.md").unlink()
    else:
        manifest = json.loads((payload / "plugin.json").read_text(encoding="utf-8"))
        manifest["version"] = "0.1"
        (payload / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")

    assert _run_bash(plugin_root=payload).stdout == "{}"
    if _powershell():
        assert _run_powershell(plugin_root=payload).stdout == "{}"
